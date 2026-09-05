"""Baseline — 用户"什么状态可以被认为是正常"的可编辑模型参数。

Baseline 不是永恒真理，而是**可被用户重新定义的模型参数**
（REFACTOR_PLAN.md §8 Baseline Control）。

架构定位（ARCHITECTURE part1 §5.1）：
    情绪(t) = m(t) + f(t)
              │       └─ 零均值 GP：情绪波动部分（由 Estimator 学习）
              └─ 均值函数：基线，缓慢漂移（由用户 + EWMA 共同决定）

三级用户主权（ARCHITECTURE part1 §5.2）：
    1. Nudge 微调   上下拖动基线虚线 → 立即生效，EWMA 自然衰减
    2. Fork  分叉   标记某时刻为"新基线起点" → 历史分段，此后只用分叉后数据
    3. Reset 重置   清空个人模型 → 回到群体先验

版本化：每个 Baseline 有唯一 baseline_id，EmotionState.baseline_id 引用它，
支持"用新基线重算历史状态"（REFACTOR_PLAN.md §13）。

设计约束：
  - 与旧版 BaselineVector（models.py）保持字段兼容，但独立成新类，
    以便未来引入 regime 分段而不污染旧代码
  - 零依赖，纯 dataclass
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class BaselineSource(str, Enum):
    """基线的来源。决定其可信任度与是否可被自动更新。"""

    POPULATION = "population"    # 冷启动，用群体先验
    EWMA_AUTO = "ewma_auto"      # 系统 EWMA 自动追踪
    USER_NUDGE = "user_nudge"    # 用户微调（L1）
    USER_FORK = "user_fork"      # 用户分叉（L2，硬变点）
    USER_RESET = "user_reset"    # 用户重置（L3，回到群体先验）


def _new_baseline_id() -> str:
    """生成唯一 baseline_id。用时间戳前缀便于按时间排序。"""
    return f"bl_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Baseline:
    """用户当前的基线（"正常状态"）。

    Attributes:
        valence: 基线效价 [0, 1]，默认 0.55（群体先验）。
        arousal: 基线唤醒 [0, 1]，默认 0.42（群体先验）。
        intensity: 基线强度 [0, 1]，默认 0.35。
        stability: 基线稳定度 [0, 1]，默认 0.70。
        resting_hr: 静息心率 BPM（与旧 BaselineVector 兼容）。
        resting_hrv_mean: 静息 HRV 均值 ms（与旧 BaselineVector 兼容）。
        sleep_score: 前夜睡眠评分 [0, 10]（与旧 BaselineVector 兼容）。
        baseline_id: 唯一标识，EmotionState.baseline_id 引用此值。
        source: 基线来源，见 BaselineSource。
        effective_from: 生效起始 Unix 时间戳。分叉后新 Baseline 的
            effective_from = 分叉时刻，历史 Baseline 的 effective_to = 分叉时刻。
        effective_to: 失效 Unix 时间戳。None=仍然生效。
        regime_id: 所属 regime（分叉段）ID。同一 regime 内所有 Baseline
            共享一个 regime_id，便于"只用分叉后数据学习"。
        version: 单调递增版本号，同一 regime 内 nudge 会递增。
        parent_id: 上一个 Baseline 的 id，形成链表便于审计。
        confidence: 基线自身的置信度 [0, 1]。冷启动低，长期数据积累后高。
        meta: 附加元信息。
    """

    valence: float = 0.55
    arousal: float = 0.42
    intensity: float = 0.35
    stability: float = 0.70
    resting_hr: float = 72.0
    resting_hrv_mean: float = 50.0
    sleep_score: float = 7.0
    baseline_id: str = field(default_factory=_new_baseline_id)
    source: BaselineSource = BaselineSource.POPULATION
    effective_from: float = field(default_factory=time.time)
    effective_to: Optional[float] = None
    regime_id: Optional[str] = None
    version: int = 1
    parent_id: Optional[str] = None
    confidence: float = 0.3
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 裁剪到 [0, 1] 的维度
        for name in ("valence", "arousal", "intensity", "stability", "confidence"):
            v = _clip(getattr(self, name))
            object.__setattr__(self, name, v)

        # 生理字段：仅类型与非负检查
        for name in ("resting_hr", "resting_hrv_mean"):
            v = float(getattr(self, name))
            if v < 0:
                raise ValueError(f"Baseline.{name} 不能为负，得到 {v}")
            object.__setattr__(self, name, v)

        s = float(self.sleep_score)
        if s < 0 or s > 10:
            raise ValueError(f"Baseline.sleep_score 必须在 [0, 10]，得到 {s}")
        object.__setattr__(self, "sleep_score", s)

        # effective_from/to 校验
        if self.effective_from <= 0:
            raise ValueError(
                f"Baseline.effective_from 必须为正 Unix 时间戳，得到 {self.effective_from}"
            )
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError(
                "Baseline.effective_to 不能早于 effective_from："
                f"{self.effective_to} < {self.effective_from}"
            )

        # Enum 允许传字符串
        if not isinstance(self.source, BaselineSource):
            object.__setattr__(self, "source", BaselineSource(self.source))

    def is_active_at(self, ts: float) -> bool:
        """该 Baseline 在时刻 ts 是否生效。"""
        if ts < self.effective_from:
            return False
        if self.effective_to is not None and ts >= self.effective_to:
            return False
        return True

    def deviation(self, valence: float, arousal: float) -> float:
        """给定 (v, a) 到基线的欧氏距离，用于计算 salience（ARCHITECTURE part1 §4.2）。

        Returns:
            [0, 1] 的距离，0=完全等于基线，1=最大可能偏离。
        """
        import math
        dv = float(valence) - self.valence
        da = float(arousal) - self.arousal
        d = math.sqrt(dv * dv + da * da) / math.sqrt(2.0)
        return _clip(d)

    def with_updates(self, **kwargs: Any) -> "Baseline":
        """返回一个应用了 kwargs 更新的**新** Baseline（frozen 不可原地改）。

        自动处理：
          - version += 1
          - parent_id = 当前 baseline_id
          - baseline_id = 新生成
          - effective_from = 当前时间（除非 kwargs 显式覆盖）
        """
        current = asdict(self)
        current.pop("baseline_id")
        current.pop("version")
        current.pop("parent_id")
        current.pop("effective_from")
        current.update(kwargs)
        current["baseline_id"] = _new_baseline_id()
        current["version"] = self.version + 1
        current["parent_id"] = self.baseline_id
        current.setdefault("effective_from", time.time())
        # source 若是 Enum 需保留（asdict 已保留 Enum 实例）
        return Baseline(**current)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Baseline":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


@dataclass(frozen=True)
class BaselineShiftEvent:
    """基线变更事件。每次 Baseline 变化都产生一条，append-only 写入事件流。

    Attributes:
        timestamp: 变更发生时间。
        old_baseline_id: 变更前 Baseline 的 id。
        new_baseline_id: 变更后 Baseline 的 id。
        shift_type: 变更类型，见 BaselineSource（nudge/fork/reset/ewma_auto）。
        reason: 人类可读的原因（用户填写或系统生成）。
        deltas: 各维度变化量 {"valence": +0.09, "arousal": +0.11, ...}。
        user_confirmed: 是否经用户显式确认（BOCPD 提议 + 用户裁决，
            ARCHITECTURE part1 §5.3）。
        detector_confidence: 若由 BOCPD 等检测器提议，其置信度。
    """

    timestamp: float
    old_baseline_id: Optional[str]
    new_baseline_id: str
    shift_type: BaselineSource
    reason: str = ""
    deltas: Dict[str, float] = field(default_factory=dict)
    user_confirmed: bool = False
    detector_confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            raise ValueError(
                f"BaselineShiftEvent.timestamp 必须为正，得到 {self.timestamp}"
            )
        if not isinstance(self.shift_type, BaselineSource):
            object.__setattr__(self, "shift_type", BaselineSource(self.shift_type))
        d = _clip(float(self.detector_confidence))
        object.__setattr__(self, "detector_confidence", d)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["shift_type"] = self.shift_type.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BaselineShiftEvent":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


def select_active_baseline(
    baselines: List[Baseline], timestamp: float
) -> Optional[Baseline]:
    """在 Baseline 历史中选出在 timestamp 时刻生效的那一条。

    规则：
      1. 若有多条同时生效（理论上不该发生，防御性处理），取 version 最大的
      2. 若无一生效（时间戳早于所有 Baseline），返回 None
    """
    active = [b for b in baselines if b.is_active_at(timestamp)]
    if not active:
        return None
    return max(active, key=lambda b: (b.version, b.effective_from))


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
