"""personal_model — 个人超参数在线学习 + 边际似然。

ARCHITECTURE part1 §3.3 L3 学习层：
    边际似然最大化 → 个人超参数 θ_user
    层次先验收缩：θ_user ← 群体先验 + 个人证据
    Coactive Learning：把用户拖动当作"改进"而非"真值"

part1 §4.4 系统性拖动诊断表（本模块的参数调整依据）：
    总把峰值拉高/拉低 → σ 被低估/高估 → 调整 σ
    总把曲线拉得更陡  → ℓ 太大（过度平滑）→ 减小 ℓ
    总把曲线拉得更平  → ℓ 太小（过度跟随噪声）→ 增大 ℓ
    拖动幅度随机无模式 → σ_noise 偏小 → 增大 σ_noise

层次先验收缩（part1 §3.3，Taylor 2017 269 引用，Oravecz 2011 120 引用）：
    θ_user = argmax [ log p(y_user|θ) + log p(θ|μ_pop, Σ_pop) ]
    小样本下向群体先验收缩，防过拟合（part1 §9 风险表：前 20 事件强制强收缩）。
    实现：θ_final = w_pop·θ_pop + (1-w_pop)·θ_personal，
    w_pop = ModelParameters.population_shrinkage_weight()（随 n_events 单调下降）。

Coactive 有界更新（part1 §4.3，Shivaswamy & Joachims 2015）：
    单次大幅拖动只产生有界影响——每步参数相对变化裁剪到 ±max_relative_step，
    且按 reliability_weight 加权，随意拖动不会毁掉模型。

边际似然（part1 §3.3）：
    log p(y|θ) = -½ Σ_k [log|2πS_k| + y_kᵀ S_k⁻¹ y_k]
    状态空间形式下由 Kalman 滤波直接给出，O(N)。用于验证一组 θ 的拟合优度。

设计约束：零依赖（math + core.linalg + 域对象）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from emowave.core import linalg
from emowave.core.calibration.corrections import BiasStatistics, CorrectionDataset
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation
from emowave.core.estimator.estimator import EstimatorConfig, compute_observation_noise
from emowave.core.estimator.matern import (
    OBSERVATION_MATRIX,
    build_process_noise,
    build_transition,
)

_STATE_DIM = 4
_IDX_V = 0
_IDX_A = 2


@dataclass
class LearnerConfig:
    """学习器的工程旋钮。

    Attributes:
        max_relative_step: 每步参数相对变化上限（Coactive 有界化，part1 §4.3）。
            0.2 表示单步最多调整 ±20%。
        sigma_gain: σ 调整增益（peak_weighted_delta → σ 相对变化）。
        ell_gain: ℓ 调整增益（delta_autocorr → ℓ 相对变化）。
        noise_gain: σ_noise 调整增益（delta_scatter → σ_noise 相对变化）。
        min_personal_weight: 个人证据的最小权重（即使冷启动也允许微小个性化）。
    """

    max_relative_step: float = 0.2
    sigma_gain: float = 0.5
    ell_gain: float = 0.4
    noise_gain: float = 0.6
    min_personal_weight: float = 0.0


# ============================================================
# 边际似然（Kalman 前向遍）
# ============================================================


def kalman_log_likelihood(
    observations: Sequence[Observation],
    params: ModelParameters,
    config: Optional[EstimatorConfig] = None,
) -> float:
    """计算 log p(y|θ)：给定参数下观察序列的边际对数似然（O(N) Kalman 前向遍）。

    log p(y|θ) = Σ_k [ -½ log|2πS_k| - ½ y_kᵀ S_k⁻¹ y_k ]

    其中 S_k = H·P_pred_k·Hᵀ + R_k 是新息协方差，y_k = z_k - H·x_pred_k 是新息。
    这是状态空间 GP 的边际似然（part1 §3.3），用于超参数拟合优度评估。

    Args:
        observations: 观察序列（正序）
        params: 候选参数 θ
        config: 估计器配置

    Returns:
        对数似然（越大越好）。无有效观察时返回 0。
    """
    config = config or EstimatorConfig()
    obs = [o for o in observations if o.has_emotion_channel()]
    if not obs:
        return 0.0
    obs = sorted(obs, key=lambda o: o.timestamp)

    lam_v = params.lambda_valence()
    lam_a = params.lambda_arousal()

    x = [
        obs[0].valence if obs[0].valence is not None else config.initial_valence,
        0.0,
        obs[0].arousal if obs[0].arousal is not None else config.initial_arousal,
        0.0,
    ]
    p = linalg.diag(
        [
            config.initial_position_var,
            config.initial_velocity_var,
            config.initial_position_var,
            config.initial_velocity_var,
        ]
    )

    log_lik = 0.0
    prev_ts = obs[0].timestamp
    for k, o in enumerate(obs):
        if k > 0:
            dt = min(max(o.timestamp - prev_ts, 0.0), 600.0)
            f = build_transition(lam_v, lam_a, dt)
            q = build_process_noise(
                params.sigma_valence, params.sigma_arousal, lam_v, lam_a, dt
            )
            x = linalg.mul_vec(f, x)
            p = linalg.add(
                linalg.mul(linalg.mul(f, p), linalg.transpose(f)), q
            )
        prev_ts = o.timestamp

        # 构造观测维度
        rows: List[List[float]] = []
        z: List[float] = []
        r_diag: List[float] = []
        sigma_r = compute_observation_noise(o, config, params)
        r_scalar = sigma_r * sigma_r
        if o.valence is not None:
            rows.append(OBSERVATION_MATRIX[0])
            z.append(o.valence)
            r_diag.append(r_scalar)
        if o.arousal is not None:
            rows.append(OBSERVATION_MATRIX[1])
            z.append(o.arousal)
            r_diag.append(r_scalar)
        if not rows:
            continue

        h = rows
        r = linalg.diag(r_diag)
        p_ht = linalg.mul(p, linalg.transpose(h))
        s = linalg.add(linalg.mul(h, p_ht), r)  # 新息协方差 m×m
        m = len(rows)

        # 新息 y = z - H·x
        hx = linalg.mul_vec(h, x)
        y = linalg.vec_sub(z, hx)

        # -½ log|2πS|
        sign, logdet = _log_det(s)
        if sign <= 0:
            # S 应正定；非正定说明数值问题，跳过该步
            continue
        log_lik += -0.5 * (m * math.log(2 * math.pi) + logdet)

        # -½ yᵀ S⁻¹ y
        try:
            s_inv = linalg.inv(s)
        except ValueError:
            continue
        quad = linalg.dot(y, linalg.mul_vec(s_inv, y))
        log_lik += -0.5 * quad

        # Kalman 更新（推进状态）
        k_gain = linalg.mul(p_ht, s_inv)
        x = linalg.vec_add(x, linalg.mul_vec(k_gain, y))
        kh = linalg.mul(k_gain, h)
        i_kh = linalg.sub(linalg.identity(_STATE_DIM), kh)
        p = linalg.add(
            linalg.mul(linalg.mul(i_kh, p), linalg.transpose(i_kh)),
            linalg.mul(linalg.mul(k_gain, r), linalg.transpose(k_gain)),
        )

    return log_lik


def _log_det(m: List[List[float]]) -> tuple:
    """计算方阵的 (sign, log|det|)，高斯消元（部分主元）。

    对 1×1 / 2×2 新息协方差足够。返回 sign（+1/-1/0）与 log|det|，
    用对数行列式避免 det 下溢/上溢。
    """
    n = len(m)
    if n == 0:
        return (0, 0.0)
    # 拷贝为工作矩阵
    a = [row[:] for row in m]
    sign = 1
    log_abs_det = 0.0
    for col in range(n):
        # 部分主元
        pivot_row = col
        pivot_val = abs(a[col][col])
        for r in range(col + 1, n):
            v = abs(a[r][col])
            if v > pivot_val:
                pivot_val = v
                pivot_row = r
        if pivot_val < 1e-300:
            return (0, float("-inf"))  # 奇异
        if pivot_row != col:
            a[col], a[pivot_row] = a[pivot_row], a[col]
            sign = -sign
        pivot = a[col][col]
        log_abs_det += math.log(abs(pivot))
        # 消元
        inv_pivot = 1.0 / pivot
        for r in range(col + 1, n):
            factor = a[r][col] * inv_pivot
            if factor == 0.0:
                continue
            for c in range(col, n):
                a[r][c] -= factor * a[col][c]
    return (sign, log_abs_det)


# ============================================================
# 个人超参数在线学习
# ============================================================


class PersonalModelLearner:
    """从 correction dataset 在线学习个人超参数（ℓ/σ/σ_noise）。

    学习流程（每次 update）：
      1. 从 dataset 提取系统性偏差统计（part1 §4.4 诊断信号）
      2. 按诊断表把偏差映射为"个人目标参数"θ_personal
      3. 层次先验收缩：θ_final = w_pop·θ_pop + (1-w_pop)·θ_personal
      4. Coactive 有界化：θ_final 相对当前参数的变化裁剪到 ±max_relative_step
      5. n_events_fitted 递增，返回新版本 ModelParameters

    这不是"一步到位拟合"，而是**渐进式**个性化：每次事件结束调用一次，
    参数逐步向该用户收敛（REFACTOR_PLAN.md §9.2"修正越多越像这个用户"）。

    使用方式：
        learner = PersonalModelLearner()
        params = ModelParameters()           # 群体先验冷启动
        for event_corrections in stream:
            ds.add_...(event_corrections)
            params = learner.update(params, ds)   # 渐进个性化
    """

    def __init__(self, config: Optional[LearnerConfig] = None) -> None:
        self.config = config or LearnerConfig()

    def update(
        self,
        params: ModelParameters,
        dataset: CorrectionDataset,
        population: Optional[ModelParameters] = None,
    ) -> ModelParameters:
        """执行一步个人化学习，返回新版 ModelParameters。

        Args:
            params: 当前个人参数
            dataset: 累积的纠正数据集
            population: 群体先验（默认用 ModelParameters.population_prior()）

        Returns:
            更新后的 ModelParameters（version+1，n_events_fitted 递增）
        """
        pop = population or ModelParameters.population_prior()
        stats = dataset.bias_statistics()

        if stats.n_corrections == 0:
            # 无证据：仅递增计数，参数保持（向群体先验收缩到当前）
            return params.with_updates(
                n_events_fitted=params.n_events_fitted,
            )

        # ---------- 1. 按 part1 §4.4 诊断表计算个人目标参数 ----------
        theta_personal = self._diagnose_target(params, stats)

        # ---------- 2. 层次先验收缩 ----------
        # w_pop 由有效样本量决定（用 dataset 的加权 ESS 覆盖 n_events 推导，
        # 更直接反映"个人证据量"）
        w_pop = self._shrinkage_weight(params, stats)
        theta_final = self._shrink(theta_personal, pop, w_pop)

        # ---------- 3. Coactive 有界化：相对当前参数裁剪步长 ----------
        theta_bounded = self._bound_step(params, theta_final)

        # ---------- 4. 组装新参数 ----------
        n_new = params.n_events_fitted + 1
        new_params = params.with_updates(
            ell_valence=theta_bounded["ell_valence"],
            ell_arousal=theta_bounded["ell_arousal"],
            sigma_valence=theta_bounded["sigma_valence"],
            sigma_arousal=theta_bounded["sigma_arousal"],
            sigma_noise=theta_bounded["sigma_noise"],
            n_events_fitted=n_new,
            shrinkage_alpha=1.0 - w_pop,
        )
        return new_params

    def _diagnose_target(
        self, params: ModelParameters, stats: BiasStatistics
    ) -> Dict[str, float]:
        """part1 §4.4 诊断表：把偏差统计映射为个人目标参数。

        Returns:
            {"ell_valence","ell_arousal","sigma_valence","sigma_arousal","sigma_noise"}
        """
        cfg = self.config
        # 当前值作为起点
        ell_v = params.ell_valence
        ell_a = params.ell_arousal
        sig_v = params.sigma_valence
        sig_a = params.sigma_arousal
        sig_n = params.sigma_noise

        # --- σ（幅度）：峰值纠正方向 ---
        # peak_weighted_delta > 0：用户把峰值拉得更高/更远 → 模型幅度不够 → σ 增大
        # peak_weighted_delta < 0：用户把峰值拉平 → 模型幅度过大 → σ 减小
        peak = stats.peak_weighted_delta
        sigma_factor = 1.0 + cfg.sigma_gain * peak * stats.direction_consistency
        sigma_factor = self._clip_factor(sigma_factor)
        sig_v *= sigma_factor
        sig_a *= sigma_factor

        # --- ℓ（惯性）：纠正的时间持续性 ---
        # delta_autocorr > 0：纠正方向持续（用户连续同向拉）→ 模型缺少慢动态 → ℓ 增大
        # delta_autocorr < 0：纠正交替（用户来回拉）→ 模型过度平滑 → ℓ 减小
        ac = stats.delta_autocorr
        ell_factor = 1.0 + cfg.ell_gain * ac
        ell_factor = self._clip_factor(ell_factor)
        ell_v *= ell_factor
        ell_a *= ell_factor

        # --- σ_noise：随机拖动散度 ---
        # scatter 大且 direction_consistency 低（无系统模式）→ 观测噪声被低估 → σ_noise 增大
        randomness = stats.delta_scatter * (1.0 - stats.direction_consistency)
        noise_factor = 1.0 + cfg.noise_gain * randomness
        noise_factor = self._clip_factor(noise_factor)
        sig_n *= noise_factor

        return {
            "ell_valence": ell_v,
            "ell_arousal": ell_a,
            "sigma_valence": sig_v,
            "sigma_arousal": sig_a,
            "sigma_noise": sig_n,
        }

    def _clip_factor(self, factor: float) -> float:
        """把乘性因子裁剪到合理范围，防单次诊断暴走（Coactive 有界化的一部分）。"""
        lo = 1.0 - self.config.max_relative_step
        hi = 1.0 + self.config.max_relative_step
        return max(lo, min(hi, factor))

    def _shrinkage_weight(
        self, params: ModelParameters, stats: BiasStatistics
    ) -> float:
        """层次先验收缩权重 w_pop（向群体先验收缩的程度）。

        以 dataset 的累积有效样本量 ESS（Σ reliability_weight）为**直接证据度量**：
        dataset 是 append-only 的累积纠正集合，ESS 是"有多少个人证据"最直接的度量。

        为什么不用 max(w_by_events, w_by_ess)：population_shrinkage_weight() 按
        n_events_fitted 分段，新建 prior 的 n_events=0 会给 w=1.0，max 后会把大量
        纠正证据（ESS 高）错误地抵消掉，使个性化无法发生。ESS 才是真实证据。

        ESS 分段（与 part1 §9 风险表"前 20 事件强制强收缩"一致——现实中纠正按
        事件逐步累积，ESS 随之渐增，早期 ESS 小自然强收缩）：
            ESS < 5    → 1.0（纯群体，冷启动）
            5 ≤ ESS<20 → 0.8（强收缩）
            20 ≤ ESS<40→ 线性 0.8 → 0.2
            ESS ≥ 40   → 0.2（个人为主，保留 20% 群体锚点防漂移）

        注：ModelParameters.population_shrinkage_weight() 仍保留用于诊断/UI，
        学习器则以 dataset ESS 为准。
        """
        ess = stats.effective_sample_size
        if ess < 5:
            w_pop = 1.0
        elif ess < 20:
            w_pop = 0.8
        elif ess < 40:
            t = (ess - 20) / 20.0
            w_pop = 0.8 - 0.6 * t
        else:
            w_pop = 0.2
        # min_personal_weight 控制收缩上限（个人权重至少为该值）
        max_w = 1.0 - self.config.min_personal_weight
        return min(w_pop, max_w)

    def _shrink(
        self,
        theta_personal: Dict[str, float],
        pop: ModelParameters,
        w_pop: float,
    ) -> Dict[str, float]:
        """θ_final = w_pop·θ_pop + (1-w_pop)·θ_personal（层次先验收缩）。"""
        w_per = 1.0 - w_pop
        pop_vals = {
            "ell_valence": pop.ell_valence,
            "ell_arousal": pop.ell_arousal,
            "sigma_valence": pop.sigma_valence,
            "sigma_arousal": pop.sigma_arousal,
            "sigma_noise": pop.sigma_noise,
        }
        return {
            k: w_pop * pop_vals[k] + w_per * theta_personal[k]
            for k in pop_vals
        }

    def _bound_step(
        self, current: ModelParameters, target: Dict[str, float]
    ) -> Dict[str, float]:
        """Coactive 有界化：target 相对 current 的变化裁剪到 ±max_relative_step。

        part1 §4.3：单次大幅拖动只产生有界影响，不会毁掉模型。
        """
        cap = self.config.max_relative_step
        current_vals = {
            "ell_valence": current.ell_valence,
            "ell_arousal": current.ell_arousal,
            "sigma_valence": current.sigma_valence,
            "sigma_arousal": current.sigma_arousal,
            "sigma_noise": current.sigma_noise,
        }
        bounded: Dict[str, float] = {}
        for k, cur in current_vals.items():
            tgt = target[k]
            lo = cur * (1.0 - cap)
            hi = cur * (1.0 + cap)
            bounded[k] = max(lo, min(hi, tgt))
        return bounded
