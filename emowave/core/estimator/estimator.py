"""estimator — 实时情绪状态估计器（Matérn 状态空间 Kalman 滤波）。

ARCHITECTURE part1 §3.1 L1 感知层的实现：
  - 因果 Kalman 滤波，<10ms，无历史依赖，O(1) 内存
  - 用 Matérn ν=3/2 闭式解取代旧版手写 F 矩阵与 velocity_damping=0.85
  - **保留**旧版自适应观测噪声 compute_R_from_interaction() 的设计思想
    （滑条交互质量 → 噪声），part1 §3.1 明确"这是个好设计，不要丢"
  - **保留**生理控制输入机制（HR/HRV 作为唤醒变化率先验）
  - **新增**输出完整协方差（不只是 trace），供 L2 回顾层与置信带渲染使用

数据流（REFACTOR_PLAN.md §21.1 实时模式）：
    Observation → State Estimator → EmotionState（含 confidence/variance/trend）

与旧版 kalman_filter.py 的关键区别：
  1. 状态排序 [v, v̇, a, ȧ]（Matérn 标准，块对角）而非旧版 [v, a, v̇, ȧ]
  2. F 由 ℓ 闭式导出，velocity_damping 不再需要（阻尼已内含在 e^(-λΔt) 中）
  3. Q 由稳态协方差导出 Q=P∞-F·P∞·Fᵀ，而非经验值拼凑
  4. 输出新域 EmotionState（含 confidence/variance/trend/baseline_id）
  5. 零 numpy 依赖（用 core.linalg）

intensity 修正：旧版 sqrt(v²+a²)/sqrt(2) 把中性点当 (0,0)，新版用
EmotionState.intensity property（到 (0.5,0.5) 的距离），修正 part1 §1.3 bug。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from emowave.core import linalg
from emowave.core.domain.baseline import Baseline
from emowave.core.domain.emotion_state import EmotionState, Trend, compute_intensity
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation
from emowave.core.estimator.matern import (
    OBSERVATION_MATRIX,
    build_process_noise,
    build_stationary_covariance,
    build_transition,
)

# 状态维度：x = [v, v̇, a, ȧ]
_STATE_DIM = 4
# 位置维度索引（valence 在 0，arousal 在 2）
_IDX_V = 0
_IDX_DV = 1
_IDX_A = 2
_IDX_DA = 3


@dataclass
class EstimatorConfig:
    """估计器的可调配置（与 ModelParameters 分离：后者是可学习的，前者是工程旋钮）。

    Attributes:
        initial_valence: 冷启动初始效价。
        initial_arousal: 冷启动初始唤醒。
        initial_position_var: 初始位置方差（不确定性）。
        initial_velocity_var: 初始速度方差。
        r_base_std: 基础观测噪声标准差（滑条缓慢移动时）。
        r_fast_std: 快速拖动噪声（用户积极控制时噪声更低）。
        r_jump_std: 跳变噪声（静止后突然跳变，可能是误触/补录）。
        jump_velocity_threshold: 速度超过此值视为"快速移动"。
        stillness_threshold_sec: 停顿超过此秒数视为"静止跳变"。
        hrv_control_weight: HRV 控制输入权重（保留旧版生理先验机制）。
        hr_control_weight: HR 控制输入权重。
        confidence_scale: 置信度映射的参考方差（见 _compute_confidence）。
        stability_scale: 稳定度映射的速度参考尺度（1/秒）。
        trend_epsilon: 趋势判定的强度变化率死区（1/秒）。
        min_confidence: 置信度下限（避免冷启动时 confidence=0 让 UI 完全虚化）。
    """

    initial_valence: float = 0.5
    initial_arousal: float = 0.4
    initial_position_var: float = 0.05
    initial_velocity_var: float = 0.02
    r_base_std: float = 0.08
    r_fast_std: float = 0.04
    r_jump_std: float = 0.25
    jump_velocity_threshold: float = 0.3
    stillness_threshold_sec: float = 3.0
    hrv_control_weight: float = 0.3
    hr_control_weight: float = 0.2
    confidence_scale: float = 0.05
    stability_scale: float = 0.05
    trend_epsilon: float = 1e-4
    min_confidence: float = 0.05


def compute_observation_noise(
    obs: Observation, config: EstimatorConfig, params: ModelParameters
) -> float:
    """根据观察来源与交互质量动态计算观测噪声标准差 σ_R。

    保留旧版 compute_R_from_interaction() 的设计思想（part1 §3.1）：
      - 快速移动中（touch_velocity 高）→ 噪声低（用户在积极控制）
      - 长时间静止后突然跳变 → 噪声高（可能误触或回顾性补录）
      - 正常交互 → 基础噪声

    设计要点：交互质量作为**相对因子**乘以学习到的 sigma_noise（个人化基线），
    而不是与之取 max。这样：
      - sigma_noise 是可学习的个人参数（Phase 4 校准会更新它），作为噪声基线
      - r_fast_std / r_base_std / r_jump_std 定义交互质量的相对权重
        （归一化到 r_base_std），保留旧版"交互行为分类"的语义

    交互质量信息从 obs.meta 读取（可选键）：
      - touch_velocity: 滑条移动速度（单位/秒）
      - seconds_since_last_touch: 距上次触摸间隔（秒）

    Args:
        obs: 观察
        config: 工程配置
        params: 模型参数（提供 sigma_noise 个人化基线）

    Returns:
        观测噪声标准差 σ_R（> 0）
    """
    meta: Dict = obs.meta or {}

    # 0. 用户编辑伪观察：精度提升（part1 §4.2 峰终加权 + §3.2 实时变形的工程落地）
    #    两个目标的平衡：
    #      - UX（§3.2）：用户拖动后曲线必须"实时变形"、可见，否则交互像坏了。
    #        因此即使 reliability_weight=0 的编辑也要比原始观察明显更精确（≥8×）。
    #      - 学习（§4.2）：权重越高采纳越强（峰值/结尾/近期 → 100×），
    #        权重越低采纳越弱（平淡中段/久远 → 8×），保留"温和 vs 强烈"的单调区分。
    #    reliability_weight 同时被完整保存在 CurveEdit 中，供 Phase 4 个人模型
    #    学习按权重加权——显示层的可见性与学习层的影响度是两个独立的关注点。
    #    edit_factor → 精度比（方差）：0.10→100×，0.225→~20×，0.35→~8×
    if meta.get("is_user_edit"):
        weight = max(0.0, min(1.0, obs.confidence))
        edit_factor = 0.35 - 0.25 * weight
        sigma_edit = params.sigma_noise * edit_factor
        return max(sigma_edit, 1e-4)

    touch_velocity = float(meta.get("touch_velocity", 0.0))
    stillness = float(meta.get("seconds_since_last_touch", 0.0))

    # 1. 交互行为分类 → 相对因子（围绕 1.0，归一化到 r_base_std）
    base = max(config.r_base_std, 1e-6)
    if touch_velocity > config.jump_velocity_threshold:
        # 快速移动：用户在积极控制，噪声降低
        factor = config.r_fast_std / base
    elif stillness > config.stillness_threshold_sec:
        # 长时间静止后跳变：噪声增大，跳变越久惩罚越大（上限 2×）
        jump_penalty = min(2.0, stillness / config.stillness_threshold_sec)
        factor = (config.r_jump_std / base) * jump_penalty
    else:
        # 正常交互
        factor = 1.0

    # 2. 个人化基线噪声（可学习）× 交互因子
    sigma = params.sigma_noise * factor

    # 3. 观察 confidence 调节：confidence 越低，噪声越大
    #    confidence=1 → ×1.0，confidence=0.5 → ×1.5，confidence→0 → ×2.0（有界）
    c = max(0.0, min(1.0, obs.confidence))
    sigma *= (2.0 - c)

    # 下限防护，避免 σ=0 导致除零
    return max(sigma, 1e-4)


class StateEstimator:
    """实时情绪状态估计器（因果 Kalman 滤波，Matérn ν=3/2 状态空间）。

    使用方式：
        params = ModelParameters()           # 或从个人学习加载
        est = StateEstimator(params)
        est.initialize(timestamp=t0)
        for obs in observation_stream:
            state = est.update(obs)          # → EmotionState
        # 可选：短期外推用于预警
        future = est.extrapolate(horizon_sec=60.0)

    内部状态：
        _x: 状态向量 [v, v̇, a, ȧ]
        _P: 4×4 状态协方差矩阵
        _last_timestamp: 上一次更新的时刻（用于计算 Δt）
    """

    def __init__(
        self,
        params: Optional[ModelParameters] = None,
        config: Optional[EstimatorConfig] = None,
        baseline: Optional[Baseline] = None,
    ) -> None:
        self.params = params or ModelParameters()
        self.config = config or EstimatorConfig()
        self.baseline = baseline
        self._x: List[float] = [0.0] * _STATE_DIM
        self._P: List[List[float]] = linalg.identity(_STATE_DIM)
        self._last_timestamp: Optional[float] = None
        self._initialized = False
        # 诊断计数
        self._update_count = 0

    # ============================================================
    # 初始化
    # ============================================================

    def initialize(
        self,
        timestamp: float,
        valence: Optional[float] = None,
        arousal: Optional[float] = None,
    ) -> EmotionState:
        """用初始状态初始化滤波器。

        Args:
            timestamp: 起始 Unix 时间戳
            valence: 初始效价（默认取 config 或 baseline）
            arousal: 初始唤醒（默认取 config 或 baseline）
        """
        if timestamp <= 0:
            raise ValueError(f"initialize: timestamp 必须为正，得到 {timestamp}")

        v = valence if valence is not None else self._default_initial_valence()
        a = arousal if arousal is not None else self._default_initial_arousal()
        v = _clip(v)
        a = _clip(a)

        self._x = [v, 0.0, a, 0.0]
        # 初始协方差：对角阵，位置/速度方差由 config 给定
        self._P = linalg.diag(
            [
                self.config.initial_position_var,
                self.config.initial_velocity_var,
                self.config.initial_position_var,
                self.config.initial_velocity_var,
            ]
        )
        self._last_timestamp = timestamp
        self._initialized = True
        self._update_count = 0
        return self._to_state(timestamp)

    def _default_initial_valence(self) -> float:
        if self.baseline is not None:
            return self.baseline.valence
        return self.config.initial_valence

    def _default_initial_arousal(self) -> float:
        if self.baseline is not None:
            return self.baseline.arousal
        return self.config.initial_arousal

    # ============================================================
    # 预测步骤（时间更新）
    # ============================================================

    def _predict(self, dt: float, control_arousal: float = 0.0) -> None:
        """Kalman 预测步骤：x ← F·x + B·u，P ← F·P·Fᵀ + Q。

        F 与 Q 由 Matérn 闭式解按 Δt 导出（不再用手写 F 与 velocity_damping）。

        Args:
            dt: 时间步长（秒），必须 ≥ 0
            control_arousal: 生理控制输入对唤醒速度的先验贡献
        """
        if dt < 0:
            raise ValueError(f"_predict: dt 不能为负，得到 {dt}")

        lam_v = self.params.lambda_valence()
        lam_a = self.params.lambda_arousal()

        f = build_transition(lam_v, lam_a, dt)
        q = build_process_noise(
            self.params.sigma_valence,
            self.params.sigma_arousal,
            lam_v,
            lam_a,
            dt,
        )

        # 状态预测：x ← F·x
        self._x = linalg.mul_vec(f, self._x)

        # 控制输入：生理信号作为唤醒速度（索引 3）的先验
        # B·u 只在 ȧ 维度注入，量纲为"速度增量"，乘以 dt 得到本步贡献
        if control_arousal != 0.0:
            self._x[_IDX_DA] += control_arousal * dt

        # 协方差预测：P ← F·P·Fᵀ + Q
        f_p = linalg.mul(f, self._P)
        self._P = linalg.add(linalg.mul(f_p, linalg.transpose(f)), q)

        # 约束位置到 [0, 1]
        self._x[_IDX_V] = _clip(self._x[_IDX_V])
        self._x[_IDX_A] = _clip(self._x[_IDX_A])

    # ============================================================
    # 更新步骤（观测更新）
    # ============================================================

    def update(self, obs: Observation) -> EmotionState:
        """融入一条 Observation，更新状态估计，输出 EmotionState。

        观测模型：z = H·x + ε，ε ~ N(0, R)，H 观测 v 和 a 的位置。

        若观察不含情绪通道（纯生理观察），只走预测步 + 控制输入，
        不做观测更新（没有 z 可用）。

        Args:
            obs: 观察

        Returns:
            更新后的 EmotionState
        """
        if not self._initialized:
            # 懒初始化：首条观察的时刻作为起点
            self.initialize(obs.timestamp)
            # 若首条观察就带情绪通道，下面会正常更新；否则直接返回初值
            if not obs.has_emotion_channel():
                return self._to_state(obs.timestamp)

        # 计算 Δt
        dt = obs.timestamp - (self._last_timestamp or obs.timestamp)
        if dt < 0:
            # 乱序观察：拒绝（防御性），返回当前状态不更新
            raise ValueError(
                f"update: 观察时间戳乱序 obs={obs.timestamp} < last={self._last_timestamp}"
            )
        # Δt 上限防护：长时间无观察后，外推会发散，钳到一个合理上限
        dt = min(dt, 600.0)

        # 生理控制输入（保留旧版机制）
        control_arousal = self._compute_control_arousal(obs)

        # 预测步
        self._predict(dt, control_arousal)
        self._last_timestamp = obs.timestamp

        # 观测更新（仅当有情绪通道时）
        if obs.has_emotion_channel():
            self._observation_update(obs)

        self._update_count += 1
        return self._to_state(obs.timestamp)

    def _compute_control_arousal(self, obs: Observation) -> float:
        """从生理信号计算唤醒速度的控制输入（保留旧版 update_with_control 机制）。

        control_arousal = w_hrv · hrv_drop_ratio + w_hr · hr_change/100

        hrv_drop_ratio / hr_change 从 obs.meta 读取（相对基线的变化），
        若 meta 未提供则从 hr/hrv 与 baseline 粗略推导。
        """
        meta: Dict = obs.meta or {}
        w_hrv = self.config.hrv_control_weight
        w_hr = self.config.hr_control_weight

        hrv_drop = meta.get("hrv_drop_ratio")
        hr_change = meta.get("hr_change")

        # 若 meta 未显式提供，尝试从原始 hr/hrv 与 baseline 推导
        if hrv_drop is None and obs.hrv is not None and self.baseline is not None:
            base_hrv = self.baseline.resting_hrv_mean
            if base_hrv > 0:
                # HRV 下降比例：(基线 - 当前)/基线，正值=下降
                hrv_drop = (base_hrv - obs.hrv) / base_hrv
        if hr_change is None and obs.hr is not None and self.baseline is not None:
            hr_change = obs.hr - self.baseline.resting_hr

        hrv_drop = float(hrv_drop or 0.0)
        hr_change = float(hr_change or 0.0)

        # 信号质量门控（meta.signal_quality，默认 1.0）
        quality = float(meta.get("signal_quality", 1.0))
        if quality < 0.3:
            return 0.0

        return w_hrv * hrv_drop + w_hr * (hr_change / 100.0)

    def _observation_update(self, obs: Observation) -> None:
        """Kalman 观测更新：用 z=[v,a] 修正状态。

        支持部分观察：若只有 valence 或只有 arousal，构造对应的 1×4 H 与 1×1 R。
        """
        h_full = OBSERVATION_MATRIX
        sigma_r = compute_observation_noise(obs, self.config, self.params)
        r_scalar = sigma_r * sigma_r

        # 构造实际可用的观测维度
        rows: List[List[float]] = []
        z: List[float] = []
        r_diag: List[float] = []
        if obs.valence is not None:
            rows.append(h_full[0])
            z.append(obs.valence)
            r_diag.append(r_scalar)
        if obs.arousal is not None:
            rows.append(h_full[1])
            z.append(obs.arousal)
            r_diag.append(r_scalar)

        if not rows:
            return

        h = rows
        m = len(rows)
        r = linalg.diag(r_diag)

        # 新息协方差 S = H·P·Hᵀ + R （m×m）
        p_ht = linalg.mul(self._P, linalg.transpose(h))
        s = linalg.add(linalg.mul(h, p_ht), r)

        # Kalman 增益 K = P·Hᵀ·S⁻¹ （4×m）
        s_inv = linalg.inv(s)
        k = linalg.mul(p_ht, s_inv)

        # 新息 y = z - H·x
        hx = linalg.mul_vec(h, self._x)
        y = linalg.vec_sub(z, hx)

        # 状态更新 x ← x + K·y
        ky = linalg.mul_vec(k, y)
        self._x = linalg.vec_add(self._x, ky)

        # 协方差更新（Joseph 形式，数值更稳定）：
        # P ← (I-KH)·P·(I-KH)ᵀ + K·R·Kᵀ
        kh = linalg.mul(k, h)
        i_kh = linalg.sub(linalg.identity(_STATE_DIM), kh)
        term1 = linalg.mul(linalg.mul(i_kh, self._P), linalg.transpose(i_kh))
        term2 = linalg.mul(linalg.mul(k, r), linalg.transpose(k))
        self._P = linalg.add(term1, term2)

        # 约束位置到 [0, 1]
        self._x[_IDX_V] = _clip(self._x[_IDX_V])
        self._x[_IDX_A] = _clip(self._x[_IDX_A])

    # ============================================================
    # 外推（用于预警，不做观测更新）
    # ============================================================

    def extrapolate(self, horizon_sec: float, dt: float = 1.0) -> List[EmotionState]:
        """从当前状态短期外推（仅状态转移，无观测更新）。

        用于预警引擎判断是否会进入危险区。外推不改变滤波器内部状态
        （先保存再恢复），因此可在任意时刻安全调用。

        Args:
            horizon_sec: 外推时长（秒）
            dt: 外推步长（秒）

        Returns:
            外推轨迹上的 EmotionState 列表
        """
        if not self._initialized:
            return []
        if horizon_sec <= 0 or dt <= 0:
            return []

        x_save = list(self._x)
        p_save = [row[:] for row in self._P]
        ts_save = self._last_timestamp

        trajectory: List[EmotionState] = []
        steps = int(horizon_sec / dt)
        base_ts = self._last_timestamp or 0.0
        for i in range(steps):
            self._predict(dt, control_arousal=0.0)
            trajectory.append(self._to_state(base_ts + (i + 1) * dt))

        # 恢复内部状态（外推不应污染滤波器）
        self._x = x_save
        self._P = p_save
        self._last_timestamp = ts_save
        return trajectory

    # ============================================================
    # 状态访问
    # ============================================================

    @property
    def state_vector(self) -> List[float]:
        """当前状态向量 [v, v̇, a, ȧ]（拷贝）。"""
        return list(self._x)

    @property
    def covariance(self) -> List[List[float]]:
        """当前 4×4 状态协方差（拷贝）。L2 回顾层与置信带渲染需要完整协方差。"""
        return [row[:] for row in self._P]

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def update_count(self) -> int:
        return self._update_count

    def reset(self) -> None:
        """重置滤波器到未初始化状态。"""
        self._x = [0.0] * _STATE_DIM
        self._P = linalg.identity(_STATE_DIM)
        self._last_timestamp = None
        self._initialized = False
        self._update_count = 0

    # ============================================================
    # 内部：状态 → EmotionState
    # ============================================================

    def _to_state(self, timestamp: float) -> EmotionState:
        """把内部状态向量转换为新域 EmotionState。

        派生量：
          - intensity: 由 (v,a) 经 Russell 环状模型计算（property，修正旧 bug）
          - variance_valence/arousal: 取自协方差对角元 P[0][0], P[2][2]
          - confidence: 后验位置方差相对平稳方差的缩减程度
          - stability: 速度幅值的指数衰减（速度越大越不稳定）
          - trend: 强度变化率的符号（rising/falling/stable）
        """
        v = self._x[_IDX_V]
        dv = self._x[_IDX_DV]
        a = self._x[_IDX_A]
        da = self._x[_IDX_DA]

        var_v = max(0.0, self._P[_IDX_V][_IDX_V])
        var_a = max(0.0, self._P[_IDX_A][_IDX_A])

        confidence = self._compute_confidence(var_v, var_a)
        stability = self._compute_stability(dv, da)
        trend = self._compute_trend(v, a, dv, da)

        baseline_id = self.baseline.baseline_id if self.baseline is not None else None

        return EmotionState(
            timestamp=timestamp,
            valence=v,
            arousal=a,
            stability=stability,
            confidence=confidence,
            variance_valence=var_v,
            variance_arousal=var_a,
            trend=trend,
            baseline_id=baseline_id,
            model_version=self.params.extra.get("model_version", "")
            if isinstance(self.params.extra, dict)
            else "",
        )

    def _compute_confidence(self, var_v: float, var_a: float) -> float:
        """置信度 = 后验位置方差相对平稳方差的缩减程度。

        原理：Kalman 后验方差总是 ≤ 先验（平稳）方差。定义
            confidence = 1 - avg_posterior_var / stationary_var
        未观测时 posterior≈stationary → confidence≈0；
        多次观测后 posterior 收缩 → confidence→1。

        用指数映射保证平滑且有界：
            confidence = 1 - exp(-reduction / confidence_scale)
        其中 reduction = 1 - avg_var/stationary_var ∈ [0, 1]。
        """
        lam_v = self.params.lambda_valence()
        lam_a = self.params.lambda_arousal()
        # 平稳位置方差 = σ²（见 matern_stationary_covariance）
        stat_v = self.params.sigma_valence ** 2
        stat_a = self.params.sigma_arousal ** 2
        # 避免除零
        stat_avg = max((stat_v + stat_a) / 2.0, 1e-9)
        post_avg = (var_v + var_a) / 2.0
        ratio = post_avg / stat_avg  # ≤1 通常；>1 表示比平稳还不确定
        reduction = max(0.0, 1.0 - ratio)
        conf = 1.0 - math.exp(-reduction / max(self.config.confidence_scale, 1e-6))
        # 叠加下限，避免冷启动 confidence=0
        conf = max(conf, self.config.min_confidence)
        return _clip(conf)

    def _compute_stability(self, dv: float, da: float) -> float:
        """稳定度 = 速度幅值的指数衰减。速度越大（变化越剧烈）越不稳定。

            stability = exp(-speed / stability_scale)
        speed=0 → 1.0（完全稳定）；speed=stability_scale → ≈0.37。
        """
        speed = math.sqrt(dv * dv + da * da)
        scale = max(self.config.stability_scale, 1e-6)
        return _clip(math.exp(-speed / scale))

    def _compute_trend(self, v: float, a: float, dv: float, da: float) -> Trend:
        """趋势 = 强度变化率的符号。

        intensity = dist((v,a), (0.5,0.5)) / sqrt(0.5)
        d(intensity)/dt = ((v-0.5)·dv + (a-0.5)·da) / (dist·sqrt(2))

        intensity_dot > eps → RISING（情绪被激活/极端化）
        intensity_dot < -eps → FALLING（情绪在平复）
        否则 → STABLE
        """
        dev_v = v - 0.5
        dev_a = a - 0.5
        dist = math.sqrt(dev_v * dev_v + dev_a * dev_a)
        if dist < 1e-6:
            # 恰好在中性点，强度变化率无定义，用速度幅值判断
            speed = math.sqrt(dv * dv + da * da)
            if speed < self.config.trend_epsilon:
                return Trend.STABLE
            # 中性点附近无法判断方向，保守返回 UNKNOWN
            return Trend.UNKNOWN
        # intensity = dist/sqrt(0.5)，d(intensity)/dt = (dev·d)/ (sqrt(0.5)·dist)
        intensity_dot = (dev_v * dv + dev_a * da) / (math.sqrt(0.5) * dist)
        eps = self.config.trend_epsilon
        if intensity_dot > eps:
            return Trend.RISING
        if intensity_dot < -eps:
            return Trend.FALLING
        return Trend.STABLE


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
