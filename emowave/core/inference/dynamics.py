"""dynamics — 个人动态模型：学习"这个用户的情绪是怎么变化的"。

REFACTOR_PLAN.md §10 Personal Dynamics Model：
长期目标不是"预测用户情绪标签"，而是学习用户情绪如何变化：

    E_{t+1} = f(E_t, X_t, U_t, Δt)

  E_t：当前情绪状态    X_t：外部观察信号    U_t：用户主动修正    Δt：时间间隔

最终可以回答：
  - 如果当前状态保持不变，情绪趋势会怎样？（→ predict_forward）
  - 什么因素最容易让这个用户发生明显变化？（→ SignalSensitivity）
  - 某种应对方式过去是否有效？（→ 留给 Phase 8 recommender 集成）
  - 恢复速度是否正在发生变化？（→ recovery_half_time + DynamicsLearner 追踪）

与已有阶段的关系：
  - Phase 2 的 Matérn 状态空间已提供状态转移 F(Δt)（E_t→E_{t+1}）与 ℓ
  - Phase 4 已学习个人 ℓ/σ（temporal decay 与波动范围）
  - 本阶段在其上：① 把 ℓ 翻译为可解释的"恢复半衰期"；② 学习 signal
    sensitivity（哪些生理/情境信号对这个用户影响最大）；③ 提供带
    不确定性增长的前向预测（预测越远，置信带越宽）

设计约束：零依赖（math + core.linalg + 域对象）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

from emowave.core import linalg
from emowave.core.domain.emotion_state import EmotionState
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation
from emowave.core.estimator.matern import (
    build_process_noise,
    build_transition,
)

_STATE_DIM = 4
_IDX_V = 0
_IDX_DV = 1
_IDX_A = 2
_IDX_DA = 3


# ============================================================
# 恢复速度（temporal decay 的可解释翻译）
# ============================================================


def recovery_half_time(ell: float) -> float:
    """情绪偏离恢复到一半所需的时间（秒）——temporal decay 的可解释形式。

    Matérn ν=3/2 的位置偏离（零初速度）按 d(Δt)=e^(-λΔt)(1+λΔt)·d₀ 衰减，
    λ=√3/ℓ。求 d(Δt)/d₀=0.5 的 Δt：数值解得 λΔt≈1.678，即
        t_half ≈ 1.678/λ = 1.678·ℓ/√3 ≈ 0.969·ℓ

    所以 ℓ 本质上就是恢复时间尺度（REFACTOR_PLAN §10"恢复速度"）。
    情绪惯性越强（ℓ 越大），恢复越慢——与 Kuppens 情绪惯性研究一致。
    """
    if ell <= 0:
        raise ValueError(f"recovery_half_time: ℓ 必须为正，得到 {ell}")
    return 0.969 * ell


# ============================================================
# Signal Sensitivity（哪些信号对这个用户影响最大）
# ============================================================


@dataclass(frozen=True)
class SignalSensitivity:
    """个人信号敏感度：各外部信号对该用户情绪变化的影响权重。

    回答 §10"什么因素最容易让这个用户发生明显变化"。

    Attributes:
        w_hr_arousal: 心率（相对基线 z-score）→ 唤醒变化 的权重
        w_hrv_arousal: HRV 下降比例 → 唤醒变化 的权重
        w_sleep_valence: 睡眠评分（中心化）→ 效价变化 的权重
        w_activity_arousal: 活动强度 → 唤醒变化 的权重
        w_valence_persistence: 效价自身惯性（上一时刻效价的持续权重）
        w_arousal_persistence: 唤醒自身惯性
        n_samples: 学习所用样本数
        r2_arousal: 唤醒回归的拟合优度（诊断）
        r2_valence: 效价回归的拟合优度
    """

    w_hr_arousal: float = 0.2
    w_hrv_arousal: float = 0.3
    w_sleep_valence: float = 0.15
    w_activity_arousal: float = 0.1
    w_valence_persistence: float = 0.5
    w_arousal_persistence: float = 0.5
    n_samples: int = 0
    r2_arousal: float = 0.0
    r2_valence: float = 0.0

    def dominant_signals(self, top_k: int = 3) -> List[tuple]:
        """返回影响最大的前 k 个信号 (名称, |权重|)，按绝对权重降序。"""
        items = [
            ("hr_arousal", abs(self.w_hr_arousal)),
            ("hrv_arousal", abs(self.w_hrv_arousal)),
            ("sleep_valence", abs(self.w_sleep_valence)),
            ("activity_arousal", abs(self.w_activity_arousal)),
            ("valence_persistence", abs(self.w_valence_persistence)),
            ("arousal_persistence", abs(self.w_arousal_persistence)),
        ]
        items.sort(key=lambda x: x[1], reverse=True)
        return items[:top_k]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 多特征在线岭回归（学习 signal sensitivity）
# ============================================================


class OnlineFeatureRegression:
    """多特征在线岭回归：y ≈ θᵀ·φ(x)，φ 含截距。

    用于从 (信号特征 → 状态变化) 对学习个人 signal sensitivity。
    在线更新：A←A+w·φφᵀ，b←b+w·y·φ，θ=(A+λI)⁻¹b。
    同时追踪 R²（拟合优度）用于诊断"哪些信号真的有用 vs 只是噪声"
    （REFACTOR_PLAN §9.2"哪些信号只是噪声"）。
    """

    def __init__(self, n_features: int, reg_lambda: float = 1.0) -> None:
        if n_features < 1:
            raise ValueError(f"n_features 必须 ≥1，得到 {n_features}")
        if reg_lambda < 0:
            raise ValueError(f"reg_lambda 不能为负，得到 {reg_lambda}")
        self.n_features = n_features  # 含截距的总维度
        self.reg_lambda = reg_lambda
        self._A = linalg.zeros(n_features, n_features)
        self._b = [0.0] * n_features
        self._n = 0
        # R² 统计
        self._sum_y = 0.0
        self._sum_y2 = 0.0
        self._sum_resid2 = 0.0
        self._sum_w = 0.0
        self._theta: Optional[List[float]] = None

    @property
    def theta(self) -> List[float]:
        if self._theta is None:
            self._theta = self._solve()
        return self._theta

    @property
    def n_samples(self) -> int:
        return self._n

    def update(self, features: Sequence[float], y: float, weight: float = 1.0) -> None:
        """加入一条样本。features 长度应为 n_features-1（截距自动加）。"""
        w = max(0.0, min(1.0, float(weight)))
        if w <= 0.0:
            return
        phi = [1.0] + [float(f) for f in features]
        if len(phi) != self.n_features:
            raise ValueError(
                f"特征维度不匹配：期望 {self.n_features - 1} 个特征，得到 {len(features)}"
            )
        # 先记录残差（用更新前的 θ 预测，衡量在线拟合优度）
        if self._n > 0:
            pred = linalg.dot(self.theta, phi)
            self._sum_resid2 += w * (y - pred) ** 2
        for i in range(self.n_features):
            for j in range(self.n_features):
                self._A[i][j] += w * phi[i] * phi[j]
        for i in range(self.n_features):
            self._b[i] += w * y * phi[i]
        self._sum_y += w * y
        self._sum_y2 += w * y * y
        self._sum_w += w
        self._n += 1
        self._theta = None

    def predict(self, features: Sequence[float]) -> float:
        phi = [1.0] + [float(f) for f in features]
        return linalg.dot(self.theta, phi)

    def r_squared(self) -> float:
        """在线 R²：1 - 残差方差/总方差。低 R² 说明信号对状态变化解释力弱（噪声）。"""
        if self._n < 3 or self._sum_w <= 1e-12:
            return 0.0
        mean_y = self._sum_y / self._sum_w
        total_var = self._sum_y2 / self._sum_w - mean_y ** 2
        if total_var <= 1e-12:
            return 0.0
        resid_var = self._sum_resid2 / self._sum_w
        r2 = 1.0 - resid_var / total_var
        return max(-1.0, min(1.0, r2))

    def _solve(self) -> List[float]:
        if self._n == 0:
            return [0.0] * self.n_features
        reg = linalg.scale(linalg.identity(self.n_features), self.reg_lambda)
        try:
            a_inv = linalg.inv(linalg.add(self._A, reg))
        except ValueError:
            return [0.0] * self.n_features
        return linalg.mul_vec(a_inv, self._b)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_features": self.n_features,
            "reg_lambda": self.reg_lambda,
            "A": [r[:] for r in self._A],
            "b": list(self._b),
            "n": self._n,
            "theta": self.theta,
        }


# ============================================================
# 个人动态模型
# ============================================================


@dataclass(frozen=True)
class PersonalDynamicsModel:
    """个人动态模型：这个用户的情绪如何变化（§10 的产物）。

    Attributes:
        ell_valence / ell_arousal: 时间相关尺度（temporal decay）
        recovery_half_time_valence / arousal: 恢复半衰期（秒，可解释）
        volatility_valence / volatility_arousal: 个人波动范围（σ）
        signal_sensitivity: 各信号影响权重
        mean_valence / mean_arousal: 个人平均状态（吸引子中心）
        n_events_learned: 学习所用事件数
        confidence: 模型置信度 [0,1]
        params_version: 对应 ModelParameters 版本
    """

    ell_valence: float = 300.0
    ell_arousal: float = 240.0
    recovery_half_time_valence: float = 290.7
    recovery_half_time_arousal: float = 232.6
    volatility_valence: float = 0.15
    volatility_arousal: float = 0.20
    signal_sensitivity: SignalSensitivity = field(default_factory=SignalSensitivity)
    mean_valence: float = 0.5
    mean_arousal: float = 0.5
    n_events_learned: int = 0
    confidence: float = 0.3
    params_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["signal_sensitivity"] = self.signal_sensitivity.to_dict()
        return d


# ============================================================
# 动态学习器
# ============================================================


class DynamicsLearner:
    """从观察历史 + 个人参数学习 PersonalDynamicsModel。

    学习三类量：
      1. temporal decay / recovery speed：直接从 ℓ 翻译（recovery_half_time）
      2. volatility：个人 σ（Phase 4 已学）
      3. signal sensitivity：在线回归 (生理/情境信号 → 状态变化)
    """

    # 唤醒回归特征：[hr_z, hrv_drop, activity, prev_arousal_centered]
    # 效价回归特征：[sleep_centered, hr_z, hrv_drop, prev_valence_centered]
    N_FEATURES_AROUSAL = 5
    N_FEATURES_VALENCE = 5

    def __init__(self, reg_lambda: float = 1.0) -> None:
        self._arousal_reg = OnlineFeatureRegression(self.N_FEATURES_AROUSAL, reg_lambda)
        self._valence_reg = OnlineFeatureRegression(self.N_FEATURES_VALENCE, reg_lambda)
        self._reg_lambda = reg_lambda

    def ingest_transition(
        self,
        obs_prev: Observation,
        obs_next: Observation,
        baseline_valence: float = 0.5,
        baseline_arousal: float = 0.5,
        resting_hr: float = 72.0,
        resting_hrv: float = 50.0,
        weight: float = 1.0,
    ) -> None:
        """摄入一次状态转移 (obs_prev → obs_next)，学习信号敏感度。

        目标 y = 状态变化（next - prev）；特征 = prev 时刻的生理/情境信号
        （相对基线中心化）。这建立"什么信号导致这个用户状态变化"的映射。
        """
        if obs_prev.valence is None or obs_next.valence is None:
            return
        if obs_prev.arousal is None or obs_next.arousal is None:
            return

        dt = obs_next.timestamp - obs_prev.timestamp
        if dt <= 0:
            return

        # 信号特征（相对基线中心化 / z-score）
        hr = obs_prev.hr if obs_prev.hr is not None else resting_hr
        hrv = obs_prev.hrv if obs_prev.hrv is not None else resting_hrv
        hr_z = (hr - resting_hr) / max(resting_hr * 0.15, 1e-6)
        hrv_drop = (resting_hrv - hrv) / max(resting_hrv, 1e-6)  # 正值=下降
        activity = obs_prev.activity if obs_prev.activity is not None else 0.0
        sleep = obs_prev.sleep if obs_prev.sleep is not None else 7.0
        sleep_centered = (sleep - 7.0) / 2.0

        prev_v_centered = obs_prev.valence - baseline_valence
        prev_a_centered = obs_prev.arousal - baseline_arousal

        # 目标：每秒变化率（归一化到单位时间，便于跨 Δt 比较）
        d_arousal = (obs_next.arousal - obs_prev.arousal) / dt
        d_valence = (obs_next.valence - obs_prev.valence) / dt

        self._arousal_reg.update(
            [hr_z, hrv_drop, activity, prev_a_centered], d_arousal, weight
        )
        self._valence_reg.update(
            [sleep_centered, hr_z, hrv_drop, prev_v_centered], d_valence, weight
        )

    def ingest_series(
        self,
        observations: Sequence[Observation],
        baseline_valence: float = 0.5,
        baseline_arousal: float = 0.5,
        resting_hr: float = 72.0,
        resting_hrv: float = 50.0,
    ) -> int:
        """摄入一段观察序列，逐对转移学习。返回学习的转移数。"""
        obs = sorted(
            [o for o in observations if o.has_emotion_channel()],
            key=lambda o: o.timestamp,
        )
        n = 0
        for i in range(len(obs) - 1):
            self.ingest_transition(
                obs[i], obs[i + 1],
                baseline_valence, baseline_arousal, resting_hr, resting_hrv,
            )
            n += 1
        return n

    def build_model(
        self,
        params: ModelParameters,
        mean_valence: float = 0.5,
        mean_arousal: float = 0.5,
    ) -> PersonalDynamicsModel:
        """把个人参数 + 学到的信号敏感度组装为 PersonalDynamicsModel。"""
        theta_a = self._arousal_reg.theta
        theta_v = self._valence_reg.theta
        n = self._arousal_reg.n_samples

        # θ = [截距, hr_z, hrv_drop, activity/sleep, persistence]
        sens = SignalSensitivity(
            w_hr_arousal=theta_a[1] if len(theta_a) > 1 else 0.2,
            w_hrv_arousal=theta_a[2] if len(theta_a) > 2 else 0.3,
            w_activity_arousal=theta_a[3] if len(theta_a) > 3 else 0.1,
            w_arousal_persistence=theta_a[4] if len(theta_a) > 4 else 0.5,
            w_sleep_valence=theta_v[1] if len(theta_v) > 1 else 0.15,
            w_valence_persistence=theta_v[4] if len(theta_v) > 4 else 0.5,
            n_samples=n,
            r2_arousal=self._arousal_reg.r_squared(),
            r2_valence=self._valence_reg.r_squared(),
        )

        # 置信度：样本越多越高（饱和到 0.95）
        conf = min(0.95, 0.3 + 0.65 * (1 - math.exp(-n / 20.0))) if n > 0 else 0.3

        return PersonalDynamicsModel(
            ell_valence=params.ell_valence,
            ell_arousal=params.ell_arousal,
            recovery_half_time_valence=recovery_half_time(params.ell_valence),
            recovery_half_time_arousal=recovery_half_time(params.ell_arousal),
            volatility_valence=params.sigma_valence,
            volatility_arousal=params.sigma_arousal,
            signal_sensitivity=sens,
            mean_valence=mean_valence,
            mean_arousal=mean_arousal,
            n_events_learned=params.n_events_fitted,
            confidence=conf,
            params_version=params.version,
        )


# ============================================================
# 前向预测（带不确定性增长）
# ============================================================


def predict_forward(
    state_vector: Sequence[float],
    covariance: Sequence[Sequence[float]],
    params: ModelParameters,
    horizon_sec: float,
    dt: float = 1.0,
    mean: Sequence[float] = (0.5, 0.5),
) -> List[Dict[str, float]]:
    """从当前状态前向预测，不确定性随 horizon 增长（§10"趋势会怎样"+ uncertainty）。

    纯状态转移（无观测更新）：
        x_{k+1} = F(Δt)·x_k
        P_{k+1} = F·P_k·Fᵀ + Q(Δt)
    Q 随每步累积，P 单调增长趋近 P∞，因此预测置信带随时间变宽——
    模型诚实地表达"预测越远越不确定"（REFACTOR_PLAN §6.4）。

    **基线作为 GP 均值函数**（part1 §5.1）：情绪(t)=m(t)+f(t)，f 是零均值 GP。
    原始 Matérn 状态空间 F 把状态均值回复到 0，但情绪量表的"中性"是基线
    （默认 0.5），不是 0。因此在**偏离空间**传播：先减去均值 m，F 使偏离
    回复到 0，输出时再加回 m。这保证无观测时状态均值回复到基线而非 0。

    Args:
        state_vector: 当前状态 [v, v̇, a, ȧ]
        covariance: 当前 4×4 协方差
        params: 个人参数
        horizon_sec: 预测时长（秒）
        dt: 步长（秒）
        mean: 均值函数 (mean_valence, mean_arousal)，即基线（part1 §5.1）

    Returns:
        列表，每项 {"timestamp_offset","valence","arousal","var_valence",
        "var_arousal","std_valence","std_arousal"}
    """
    if horizon_sec <= 0 or dt <= 0:
        return []
    m_v = float(mean[0])
    m_a = float(mean[1])
    # 偏离空间：位置减去均值，速度不变（均值为常数，速度即偏离的速度）
    x = [
        float(state_vector[_IDX_V]) - m_v,
        float(state_vector[_IDX_DV]),
        float(state_vector[_IDX_A]) - m_a,
        float(state_vector[_IDX_DA]),
    ]
    p = [list(row) for row in covariance]
    lam_v = params.lambda_valence()
    lam_a = params.lambda_arousal()

    f = build_transition(lam_v, lam_a, dt)
    q = build_process_noise(
        params.sigma_valence, params.sigma_arousal, lam_v, lam_a, dt
    )

    steps = int(horizon_sec / dt)
    traj: List[Dict[str, float]] = []
    for i in range(steps):
        x = linalg.mul_vec(f, x)
        # 偏离裁剪：位置（偏离+均值）应在 [0,1]，即偏离在 [-m, 1-m]
        v_abs = _clip(x[_IDX_V] + m_v)
        a_abs = _clip(x[_IDX_A] + m_a)
        x[_IDX_V] = v_abs - m_v
        x[_IDX_A] = a_abs - m_a
        p = linalg.add(linalg.mul(linalg.mul(f, p), linalg.transpose(f)), q)
        var_v = max(0.0, p[_IDX_V][_IDX_V])
        var_a = max(0.0, p[_IDX_A][_IDX_A])
        traj.append(
            {
                "timestamp_offset": (i + 1) * dt,
                "valence": v_abs,
                "arousal": a_abs,
                "var_valence": var_v,
                "var_arousal": var_a,
                "std_valence": math.sqrt(var_v),
                "std_arousal": math.sqrt(var_a),
            }
        )
    return traj


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
