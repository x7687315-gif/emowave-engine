"""UserCorrection — 用户对模型估计的显式纠正。

Correction 是 EmoWave 2.0 相对于 1.x 最重要的新数据类型，是"个人在线学习"
的监督信号来源（REFACTOR_PLAN.md §9.2 Personal Calibration）。

三类 Correction（ARCHITECTURE part1 §4.1）：
  1. 即时采样（drag/slider）：可靠性最高，用户在事件进行中调节滑条
  2. 即时修正（manual）：可靠性高，"不对，我现在是 0.8 不是 0.5"
  3. 回顾编辑（retrospective）：可靠性随位置剧烈变化，用户拖动昨天的曲线

回顾编辑必须应用**峰终加权**（ARCHITECTURE part1 §4.2）：
  - Kahneman 峰终定律（1993，1523 引用）：回顾性评价由峰值与结尾主导
  - Fredrickson 过程忽视：中段被系统性遗忘
  - Faul & LaBar 心境一致性（2023，107 引用）：当前心境污染历史回忆

因此 reliability_weight 由下式给出（详见 compute_reliability_weight）：

    w = ω_recency · ω_salience · ω_current_mood

设计约束：
  - **append-only**：Correction 一旦写入永不修改，也永不覆盖 Observation
  - **Coactive Learning 语义**（ARCHITECTURE part1 §4.3）：Correction 表达
    "用户版比模型版更好"，不表达"用户版就是真值"。学习算法据此使用
    有界更新，避免单次随意拖动毁掉模型
  - **零依赖**：不 import numpy，只用 math 与 dataclass
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


class CorrectionDimension(str, Enum):
    """被纠正的维度。"""

    VALENCE = "valence"
    AROUSAL = "arousal"
    INTENSITY = "intensity"
    STABILITY = "stability"
    BASELINE = "baseline"     # 用户主动调整基线（Phase 5 的入口）
    THRESHOLD = "threshold"   # 用户主动调整阈值（Phase 5 的入口）


class CorrectionSource(str, Enum):
    """纠正来源。UI 交互方式决定 reliability 的先验。"""

    DRAG = "drag"                # 拖动曲线控制点（回顾编辑，最常见）
    SLIDER = "slider"            # 滑条实时采样（即时采样，最可靠）
    MANUAL = "manual"            # 手动输入数值（即时修正）
    VOICE = "voice"              # 语音输入（Amiya 转发的 explicit statement）
    IMPORT = "import"            # 从外部导入的历史修正
    AGENT = "agent"              # Agent 转发的用户反馈


# 时近性衰减时间常数：1 天（86400 秒）。
# 依据：ARCHITECTURE part1 §4.2 "τ_recall ≈ 1 天"，
# 与心境一致性记忆研究的经典时间尺度一致。
_TAU_RECALL_SEC = 86400.0

# 结尾提升时间常数：60 秒。事件结尾 60 秒内的编辑权重更高。
_TAU_END_SEC = 60.0

# 不同来源的先验可靠性系数（0-1）。SLIDER 最高（即时采样），
# DRAG 最低（回顾编辑易受记忆偏差污染）。
_SOURCE_PRIOR: Dict[CorrectionSource, float] = {
    CorrectionSource.SLIDER: 1.00,
    CorrectionSource.MANUAL: 0.95,
    CorrectionSource.VOICE: 0.85,
    CorrectionSource.AGENT: 0.70,
    CorrectionSource.IMPORT: 0.60,
    CorrectionSource.DRAG: 0.55,
}


def compute_reliability_weight(
    *,
    source: CorrectionSource,
    edit_latency_sec: float,
    salience: float = 0.5,
    current_mood_deviation: float = 0.0,
    seconds_before_event_end: Optional[float] = None,
) -> float:
    """按 ARCHITECTURE part1 §4.2 的峰终加权公式计算 Correction 可靠性。

    w = ω_source · ω_recency · ω_salience · ω_current_mood · ω_end

    Args:
        source: 纠正来源，决定先验系数。
        edit_latency_sec: 距被编辑时刻的时间间隔（秒）。0=实时，86400=一天前。
        salience: 被编辑点的显著性 [0, 1]，由 |μ(t)-baseline|·w_peak
            + |dμ/dt|·w_slope 计算得出。越高越可靠（峰终定律）。
        current_mood_deviation: 编辑时用户当前心境偏离中性的程度 [0, 1]。
            越大越不可靠（心境一致性偏差）。
        seconds_before_event_end: 被编辑点距事件结尾的秒数（若已知）。
            越接近结尾越可靠（峰终定律）。

    Returns:
        可靠性权重 [0, 1]。学习算法用此权重加权 Correction 的梯度贡献。
    """
    # 1. 来源先验
    w_source = _SOURCE_PRIOR.get(source, 0.5)

    # 2. 时近性：exp(-Δt / τ_recall)，τ=1 天
    latency = max(0.0, float(edit_latency_sec))
    w_recency = math.exp(-latency / _TAU_RECALL_SEC)

    # 3. 显著性：salience 越高越可靠。用 salience 本身作为权重，
    #    但下限 0.3（避免平淡段的编辑被完全忽略，仅"降权"而非"作废"）。
    s = _clip(float(salience))
    w_salience = 0.3 + 0.7 * s

    # 4. 心境一致性：当前心境越极端，历史编辑越不可信
    m = _clip(float(current_mood_deviation))
    w_current_mood = 1.0 - 0.5 * m

    # 5. 结尾提升（峰终定律的"终"）：距事件结尾越近越可靠
    if seconds_before_event_end is None:
        w_end = 1.0
    else:
        d = max(0.0, float(seconds_before_event_end))
        # 结尾 60 秒内 → 权重从 1.0 提升到 1.3；60 秒外衰减到 1.0
        w_end = 1.0 + 0.3 * math.exp(-d / _TAU_END_SEC)

    w = w_source * w_recency * w_salience * w_current_mood * w_end
    return _clip(w)


@dataclass(frozen=True)
class UserCorrection:
    """用户对模型估计的一次显式纠正。

    Attributes:
        timestamp: 被纠正的目标时刻（Unix 秒）。注意与 created_at 区分：
            timestamp 是"用户改的是哪一刻"，created_at 是"用户什么时候改的"。
        dimension: 被纠正的维度，见 CorrectionDimension。
        predicted_value: 模型原估计值。
        corrected_value: 用户认为的正确值。
        source: 纠正来源，见 CorrectionSource。
        reason: 用户可选的文字说明（"我其实没那么焦虑"）。
        edit_session_mood: 编辑时用户当前情绪强度 [0, 1]，用于心境一致性
            降权（ARCHITECTURE part1 §4.5）。
        edit_latency_sec: 距被编辑时刻的时间间隔（秒），用于时近性降权。
        drag_velocity: 拖动速度（单位/秒）。快速拖动 = 更随意 = 更降权。
        salience: 被编辑点的显著性 [0, 1]，越高越可靠（峰终定律）。
        seconds_before_event_end: 被编辑点距事件结尾的秒数（若已知）。
        reliability_weight: 由 compute_reliability_weight 计算的最终权重
            [0, 1]。若构造时未传入，__post_init__ 会自动计算。
        created_at: Correction 记录被创建的时间（Unix 秒），默认取当前时间。
        correction_id: 唯一标识，默认按 (timestamp, dimension, created_at) 生成。
        meta: 附加元信息。
    """

    timestamp: float
    dimension: CorrectionDimension
    predicted_value: float
    corrected_value: float
    source: CorrectionSource = CorrectionSource.DRAG
    reason: Optional[str] = None
    edit_session_mood: float = 0.0
    edit_latency_sec: float = 0.0
    drag_velocity: float = 0.0
    salience: float = 0.5
    seconds_before_event_end: Optional[float] = None
    reliability_weight: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    correction_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            raise ValueError(
                f"UserCorrection.timestamp 必须为正 Unix 时间戳，得到 {self.timestamp}"
            )
        if self.created_at <= 0:
            raise ValueError(
                f"UserCorrection.created_at 必须为正 Unix 时间戳，得到 {self.created_at}"
            )
        # created_at 不应早于 timestamp（不能"提前纠正未来的估计"）
        if self.created_at + 1e-6 < self.timestamp:
            raise ValueError(
                "UserCorrection.created_at 不能早于 timestamp："
                f"created_at={self.created_at}, timestamp={self.timestamp}"
            )

        # Enum 允许传字符串
        if not isinstance(self.dimension, CorrectionDimension):
            object.__setattr__(self, "dimension", CorrectionDimension(self.dimension))
        if not isinstance(self.source, CorrectionSource):
            object.__setattr__(self, "source", CorrectionSource(self.source))

        # edit_session_mood / salience 裁剪到 [0, 1]
        object.__setattr__(self, "edit_session_mood", _clip(self.edit_session_mood))
        object.__setattr__(self, "salience", _clip(self.salience))

        # edit_latency_sec / drag_velocity 非负
        if self.edit_latency_sec < 0:
            raise ValueError(
                f"edit_latency_sec 不能为负，得到 {self.edit_latency_sec}"
            )
        if self.drag_velocity < 0:
            raise ValueError(
                f"drag_velocity 不能为负，得到 {self.drag_velocity}"
            )

        # 若未提供 reliability_weight，自动计算
        if self.reliability_weight is None:
            w = compute_reliability_weight(
                source=self.source,
                edit_latency_sec=self.edit_latency_sec,
                salience=self.salience,
                current_mood_deviation=self.edit_session_mood,
                seconds_before_event_end=self.seconds_before_event_end,
            )
            # 快速拖动降权：drag_velocity 越大权重越低（有界，最多降到 0.5×）
            if self.drag_velocity > 0:
                # 用 sigmoid 型衰减：v=0 → 1.0，v=1 → 0.75，v→∞ → 0.5
                w *= 0.5 + 0.5 / (1.0 + self.drag_velocity)
            object.__setattr__(self, "reliability_weight", _clip(w))
        else:
            object.__setattr__(
                self, "reliability_weight", _clip(float(self.reliability_weight))
            )

        # 生成默认 correction_id
        if self.correction_id is None:
            cid = f"cor_{int(self.timestamp * 1000)}_{self.dimension.value}_{int(self.created_at * 1000)}"
            object.__setattr__(self, "correction_id", cid)

    @property
    def delta(self) -> float:
        """纠正幅度：corrected - predicted。正号=用户认为模型低估了。"""
        return self.corrected_value - self.predicted_value

    @property
    def abs_delta(self) -> float:
        """纠正幅度的绝对值。"""
        return abs(self.delta)

    def is_systematic_overestimate(self) -> bool:
        """模型是否系统性高估（用户拉低）。"""
        return self.delta < 0

    def is_systematic_underestimate(self) -> bool:
        """模型是否系统性低估（用户拉高）。"""
        return self.delta > 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["dimension"] = self.dimension.value
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UserCorrection":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
