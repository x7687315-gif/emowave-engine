"""EmotionState — 模型对用户当前情绪状态的估计。

EmotionState 不是"真相"，而是"模型此刻的最佳猜测"，永远伴随不确定性。
用户对 EmotionState 有最终解释权，可以通过 UserCorrection 修正。

维度定义（详见 REFACTOR_PLAN.md §5，情绪从单点数值升级为时间函数）：

    E(t) = [V(t), A(t), I(t), S(t)]

      V(t)  Valence    效价    [0, 1]，0=极不舒服，1=极舒服
      A(t)  Arousal    唤醒    [0, 1]，0=极困倦，1=极兴奋
      I(t)  Intensity  强度    [0, 1]，到中性点 (0.5, 0.5) 的距离
      S(t)  Stability  稳定度  [0, 1]，1=非常稳定，0=剧烈波动

新增字段（旧版 EventProfile 中没有）：
  - confidence：模型对自身估计的置信度 [0, 1]
  - uncertainty：各维度的方差（用于置信带渲染，ARCHITECTURE part1 §6.2）
  - trend：短期趋势（rising / falling / stable），用于 Amiya 输出协议
  - baseline_id：本次估计所参照的 Baseline 版本，支持"重算历史"

设计约束：
  - Intensity 定义修正：ARCHITECTURE part1 §1.3 指出旧版把 intensity
    定义为"到原点的距离"，导致 (v=0, a=0) 的抑郁性迟滞被算成 0 强度。
    新版采用"到中性点 (0.5, 0.5) 的距离"，与 Russell 环状模型一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional

# 中性点：Russell 环状模型中，valence/arousal 归一化到 [0,1] 后，
# 中性状态在 (0.5, 0.5)，不是 (0, 0)。
_NEUTRAL_V = 0.5
_NEUTRAL_A = 0.5
# 从 (0.5, 0.5) 到四角的最大距离 = sqrt(0.5² + 0.5²) = sqrt(0.5) ≈ 0.7071
_MAX_DIST_TO_NEUTRAL = math.sqrt(0.5)


class Trend(str, Enum):
    """短期情绪趋势。用于 Amiya 输出协议与 UI 曲线箭头。"""

    RISING = "rising"      # 唤醒上升 / 强度上升
    FALLING = "falling"    # 唤醒下降 / 恢复中
    STABLE = "stable"      # 变化不显著
    UNKNOWN = "unknown"    # 数据不足，无法判断


def compute_intensity(valence: float, arousal: float) -> float:
    """按 Russell 环状模型计算强度：到中性点 (0.5, 0.5) 的欧氏距离。

    修正旧版 `sqrt(v² + a²) / sqrt(2)` 的定义（该定义把中性点当成 (0,0)，
    导致"极度不适 + 极度困倦"这种典型抑郁性迟滞被算成 0 强度）。

    详见 ARCHITECTURE_EMOTIONpart1.md §1.3。
    """
    dv = valence - _NEUTRAL_V
    da = arousal - _NEUTRAL_A
    d = math.sqrt(dv * dv + da * da) / _MAX_DIST_TO_NEUTRAL
    # 数值防护：d 理论上 ∈ [0, 1]，浮点误差可能到 1.0000000001
    if d < 0.0:
        return 0.0
    if d > 1.0:
        return 1.0
    return d


@dataclass(frozen=True)
class EmotionState:
    """模型对用户当前情绪状态的估计。

    Attributes:
        timestamp: Unix 时间戳（秒）。
        valence: 效价估计 [0, 1]。
        arousal: 唤醒估计 [0, 1]。
        intensity: 强度 [0, 1]，默认按 compute_intensity(v, a) 计算。
        stability: 稳定度 [0, 1]，1=非常稳定，0=剧烈波动。
        confidence: 模型对自身估计的置信度 [0, 1]。低置信度应在 UI 上
            以虚化 / 宽置信带明确传达（ARCHITECTURE part1 §6.2）。
        variance_valence: valence 的后验方差（用于渲染 ±1σ / ±2σ 置信带）。
        variance_arousal: arousal 的后验方差。
        trend: 短期趋势。
        baseline_id: 参照的 Baseline 版本号，支持"用新基线重算历史"。
        model_version: 产生此估计的模型版本（详见 protocol/schemas.py）。
        meta: 附加元信息（如估计器内部诊断字段）。
    """

    timestamp: float
    valence: float
    arousal: float
    stability: float = 0.5
    confidence: float = 0.5
    variance_valence: float = 0.0
    variance_arousal: float = 0.0
    trend: Trend = Trend.UNKNOWN
    baseline_id: Optional[str] = None
    model_version: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            raise ValueError(
                f"EmotionState.timestamp 必须为正 Unix 时间戳，得到 {self.timestamp}"
            )

        # 强制 [0, 1] 裁剪
        object.__setattr__(self, "valence", _clip(self.valence))
        object.__setattr__(self, "arousal", _clip(self.arousal))
        object.__setattr__(self, "stability", _clip(self.stability))
        object.__setattr__(self, "confidence", _clip(self.confidence))

        # 方差必须非负
        for name in ("variance_valence", "variance_arousal"):
            v = float(getattr(self, name))
            if v < 0:
                raise ValueError(f"EmotionState.{name} 不能为负，得到 {v}")
            object.__setattr__(self, name, v)

        # trend 允许传字符串
        if not isinstance(self.trend, Trend):
            object.__setattr__(self, "trend", Trend(self.trend))

    @property
    def intensity(self) -> float:
        """按 Russell 环状模型计算的强度（到中性点的距离）。

        实现为 property 而非 field，避免"存了旧的 intensity 值但 v/a 已被
        外部重算"这种不一致状态。任何时刻 intensity 都由 (v, a) 唯一决定。
        """
        return compute_intensity(self.valence, self.arousal)

    def std_valence(self) -> float:
        """valence 的后验标准差。UI 用于渲染 ±1σ 置信带。"""
        return math.sqrt(self.variance_valence)

    def std_arousal(self) -> float:
        """arousal 的后验标准差。"""
        return math.sqrt(self.variance_arousal)

    def confidence_interval(
        self, dimension: str = "valence", z: float = 1.0
    ) -> tuple:
        """返回给定维度在 z 倍标准差下的置信区间 (low, high)。

        默认 z=1.0 对应 ±1σ 置信带。UI 通常同时渲染 ±1σ 深色带与 ±2σ
        浅色带（ARCHITECTURE part1 §6.2）。

        Args:
            dimension: "valence" 或 "arousal"
            z: 标准差倍数，1.0/2.0 常见

        Returns:
            (low, high)，已裁剪到 [0, 1]。
        """
        if dimension == "valence":
            mean, std = self.valence, self.std_valence()
        elif dimension == "arousal":
            mean, std = self.arousal, self.std_arousal()
        else:
            raise ValueError(f"未知维度: {dimension}")
        return (_clip(mean - z * std), _clip(mean + z * std))

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 JSON 兼容 dict。intensity 作为派生字段一并输出。"""
        d = asdict(self)
        d["trend"] = self.trend.value
        d["intensity"] = self.intensity
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EmotionState":
        """从 dict 反序列化。忽略派生字段 intensity。"""
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """把标量裁剪到 [lo, hi]。非有限值（NaN/Inf）→ lo（安全兜底）。"""
    x = float(x)
    if not math.isfinite(x):
        return lo
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
