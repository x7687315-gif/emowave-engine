"""predictor — 短期趋势预测器（回答 REFACTOR_PLAN.md §10 的四个问题）。

§10 Personal Dynamics Model 最终要能回答：
  1. 如果当前状态保持不变，情绪趋势会怎样？  → forecast()
  2. 什么因素最容易让这个用户发生明显变化？    → dynamics.signal_sensitivity.dominant_signals()
  3. 某种应对方式过去是否有效？                → 留给 Phase 8 recommender 集成
  4. 恢复速度是否正在发生变化？                → recovery_estimate() + 跨模型版本对比

本模块把 Phase 2 的状态估计、Phase 4 的个人参数、Phase 6 的动态模型
编排为面向"预测与解释"的接口，并对每个预测给出 uncertainty（§6.4：
允许模型诚实表达不确定性）。

设计约束：零依赖（math + 域对象 + dynamics）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

from emowave.core.domain.emotion_state import EmotionState, Trend
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.inference.dynamics import (
    PersonalDynamicsModel,
    predict_forward,
    recovery_half_time,
)


@dataclass(frozen=True)
class TrendForecast:
    """一次短期趋势预测的结果（含不确定性）。

    Attributes:
        timestamp: 预测起点时刻。
        horizon_sec: 预测时长。
        trend: 预测的趋势方向（rising/falling/stable/unknown）。
        predicted_valence / predicted_arousal: 预测终点的状态均值。
        predicted_intensity: 预测终点的强度。
        std_valence / std_arousal: 预测终点的标准差（不确定性）。
        ci_valence / ci_arousal: 预测终点的 ±1σ 置信区间 (low, high)。
        recovery_time_sec: 预计恢复到基线附近的时间（秒），None=无法估计。
        confidence: 预测置信度 [0,1]（随 horizon 增长而下降）。
        trajectory: 完整预测轨迹（每步含均值与方差），供 UI 渲染预测带。
    """

    timestamp: float
    horizon_sec: float
    trend: Trend = Trend.UNKNOWN
    predicted_valence: float = 0.5
    predicted_arousal: float = 0.5
    predicted_intensity: float = 0.0
    std_valence: float = 0.0
    std_arousal: float = 0.0
    ci_valence: tuple = (0.0, 1.0)
    ci_arousal: tuple = (0.0, 1.0)
    recovery_time_sec: Optional[float] = None
    confidence: float = 0.5
    trajectory: List[Dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["trend"] = self.trend.value
        return d


class TrendPredictor:
    """短期趋势预测器。

    使用方式：
        predictor = TrendPredictor(params, dynamics)
        forecast = predictor.forecast(current_state, horizon_sec=60)
        # forecast.trend / forecast.ci_valence / forecast.recovery_time_sec
    """

    def __init__(
        self,
        params: Optional[ModelParameters] = None,
        dynamics: Optional[PersonalDynamicsModel] = None,
        baseline_valence: float = 0.5,
        baseline_arousal: float = 0.5,
    ) -> None:
        self.params = params or ModelParameters()
        self.dynamics = dynamics
        self.baseline_valence = baseline_valence
        self.baseline_arousal = baseline_arousal

    def forecast(
        self,
        state: EmotionState,
        horizon_sec: float = 60.0,
        dt: float = 1.0,
    ) -> TrendForecast:
        """预测"如果当前状态保持不变（无新观测），情绪趋势会怎样"（§10 问题1）。

        从 state 的均值与协方差出发，用 Matérn 状态转移前向传播，
        不确定性随 horizon 增长。
        """
        if horizon_sec <= 0:
            return TrendForecast(
                timestamp=state.timestamp, horizon_sec=0.0, trend=Trend.UNKNOWN,
                predicted_valence=state.valence, predicted_arousal=state.arousal,
                predicted_intensity=state.intensity, confidence=state.confidence,
            )

        # 从 EmotionState 重建状态向量 [v, v̇, a, ȧ]
        # 速度未知时设为 0（保守：假设当前无趋势惯性，仅靠均值回复）
        # 若 state.meta 带速度则采用
        dv = float(state.meta.get("d_valence", 0.0)) if isinstance(state.meta, dict) else 0.0
        da = float(state.meta.get("d_arousal", 0.0)) if isinstance(state.meta, dict) else 0.0
        x = [state.valence, dv, state.arousal, da]
        # 协方差：用 state 的方差构造对角阵（速度方差用平稳值近似）
        p = [
            [state.variance_valence, 0.0, 0.0, 0.0],
            [0.0, self.params.sigma_valence ** 2 * self.params.lambda_valence() ** 2, 0.0, 0.0],
            [0.0, 0.0, state.variance_arousal, 0.0],
            [0.0, 0.0, 0.0, self.params.sigma_arousal ** 2 * self.params.lambda_arousal() ** 2],
        ]

        traj = predict_forward(
            x, p, self.params, horizon_sec, dt,
            mean=(self.baseline_valence, self.baseline_arousal),
        )
        if not traj:
            return TrendForecast(
                timestamp=state.timestamp, horizon_sec=horizon_sec, trend=Trend.UNKNOWN,
                predicted_valence=state.valence, predicted_arousal=state.arousal,
                predicted_intensity=state.intensity, confidence=state.confidence,
            )

        end = traj[-1]
        pred_v = end["valence"]
        pred_a = end["arousal"]
        std_v = end["std_valence"]
        std_a = end["std_arousal"]

        # 趋势：比较预测终点与起点的强度
        from emowave.core.domain.emotion_state import compute_intensity
        start_intensity = compute_intensity(state.valence, state.arousal)
        end_intensity = compute_intensity(pred_v, pred_a)
        eps = 0.01
        if end_intensity > start_intensity + eps:
            trend = Trend.RISING
        elif end_intensity < start_intensity - eps:
            trend = Trend.FALLING
        else:
            trend = Trend.STABLE

        # 置信度：随 horizon 增长、随预测方差增大而下降
        conf = self._forecast_confidence(state.confidence, std_v, std_a, horizon_sec)

        # 恢复时间估计
        rec = self.recovery_estimate(state)

        return TrendForecast(
            timestamp=state.timestamp,
            horizon_sec=horizon_sec,
            trend=trend,
            predicted_valence=pred_v,
            predicted_arousal=pred_a,
            predicted_intensity=end_intensity,
            std_valence=std_v,
            std_arousal=std_a,
            ci_valence=(_clip(pred_v - std_v), _clip(pred_v + std_v)),
            ci_arousal=(_clip(pred_a - std_a), _clip(pred_a + std_a)),
            recovery_time_sec=rec,
            confidence=conf,
            trajectory=traj,
        )

    def _forecast_confidence(
        self, base_conf: float, std_v: float, std_a: float, horizon: float
    ) -> float:
        """预测置信度：基础置信度 × 不确定性惩罚 × horizon 惩罚。

        预测越远、方差越大 → 置信度越低（诚实表达不确定性，§6.4）。
        """
        avg_std = (std_v + std_a) / 2.0
        # 不确定性惩罚：std 越大置信越低
        unc_penalty = math.exp(-avg_std / 0.2)
        # horizon 惩罚：预测越长置信越低（时间尺度 ~ ℓ）
        ell = max(self.params.ell_valence, 1e-6)
        horizon_penalty = math.exp(-horizon / (3.0 * ell))
        return _clip(base_conf * unc_penalty * horizon_penalty)

    def recovery_estimate(self, state: EmotionState) -> Optional[float]:
        """估计从当前状态恢复到基线附近的时间（§10 问题4：恢复速度）。

        基于 Matérn 恢复半衰期：偏离越大，恢复到"接近基线"需要的半衰期个数越多。
            t_recover ≈ t_half · log2(initial_deviation / target_deviation)
        target 取个人波动 σ（恢复到 1σ 内视为"回到基线附近"）。
        """
        dev_v = abs(state.valence - self.baseline_valence)
        dev_a = abs(state.arousal - self.baseline_arousal)
        dev = math.sqrt(dev_v ** 2 + dev_a ** 2)
        target = math.sqrt(
            self.params.sigma_valence ** 2 + self.params.sigma_arousal ** 2
        )
        if dev <= target or dev <= 1e-6:
            return 0.0  # 已在基线附近
        # 用效价恢复半衰期（也可按维度分别算，这里取综合）
        t_half = recovery_half_time(self.params.ell_valence)
        n_halves = math.log2(dev / target)
        return t_half * n_halves

    def recovery_speed_changing(
        self, prev_model: PersonalDynamicsModel, curr_model: PersonalDynamicsModel
    ) -> Dict[str, Any]:
        """对比两个时期的动态模型，判断恢复速度是否在变化（§10 问题4）。

        Returns:
            {"faster": bool, "ratio": float, "prev_half_time": .., "curr_half_time": ..}
            ratio = curr/prev，<1 表示恢复变快（半衰期缩短），>1 表示变慢。
        """
        prev_ht = prev_model.recovery_half_time_valence
        curr_ht = curr_model.recovery_half_time_valence
        if prev_ht <= 0:
            return {"faster": False, "ratio": 1.0, "prev_half_time": prev_ht, "curr_half_time": curr_ht}
        ratio = curr_ht / prev_ht
        return {
            "faster": ratio < 0.95,  # 半衰期缩短 5% 以上视为变快
            "slower": ratio > 1.05,
            "ratio": ratio,
            "prev_half_time": prev_ht,
            "curr_half_time": curr_ht,
        }

    def most_influential_signals(self, top_k: int = 3) -> List[tuple]:
        """什么因素最容易让这个用户发生明显变化（§10 问题2）。"""
        if self.dynamics is None:
            return []
        return self.dynamics.signal_sensitivity.dominant_signals(top_k)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
