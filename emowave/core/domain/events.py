"""StateEvent + EventStream — 所有状态变化以事件流形式记录。

REFACTOR_PLAN.md §4.6 EventStream：

    10:21:02 Observation
    10:21:05 EmotionEstimate
    10:21:07 Observation
    10:21:10 UserCorrection
    10:21:10 BaselineAdjustment
    10:21:20 EmotionEstimate

这样可以回答："为什么模型现在认为我是 67？" —— 可解释性
（REFACTOR_PLAN.md §32 Explainability）的代码级支撑。

设计约束：
  - EventStream 是内存中的环形缓冲，不是持久化层（持久化由 adapters/storage 负责）
  - 事件按 timestamp 单调递增追加；乱序追加会被拒绝（防御性）
  - 支持按类型 / 时间窗口 / 维度过滤，供 UI 与诊断工具消费
  - 零依赖，纯 dataclass + deque
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Deque, Dict, Iterable, List, Optional


class StateEventType(str, Enum):
    """事件类型。覆盖 EmoWave 数据流中的所有关键节点。"""

    OBSERVATION = "observation"                 # 收到一条 Observation
    EMOTION_ESTIMATE = "emotion_estimate"       # 产生一个 EmotionState
    USER_CORRECTION = "user_correction"         # 用户提交一次 Correction
    BASELINE_ADJUSTMENT = "baseline_adjustment" # Baseline 发生 nudge/fork/reset
    MODEL_UPDATE = "model_update"               # ModelParameters 完成一次学习更新
    TREND_CHANGE = "trend_change"               # 趋势从 stable→rising 等
    THRESHOLD_CROSS = "threshold_cross"         # 越过警戒阈值（预警触发）
    CRISIS_ALERT = "crisis_alert"               # 危机协议触发（旧 crisis_protocol.py 的入口）
    SYSTEM = "system"                           # 启动 / 关闭 / 异常等系统事件


@dataclass(frozen=True)
class StateEvent:
    """事件流中的一条记录。

    Attributes:
        timestamp: 事件发生时间（Unix 秒）。
        event_type: 事件类型，见 StateEventType。
        payload: 事件负载（dict），结构由 event_type 决定：
            OBSERVATION       → {"observation_id": ..., "valence": ..., ...}
            EMOTION_ESTIMATE  → {"state": EmotionState.to_dict()}
            USER_CORRECTION   → {"correction": UserCorrection.to_dict()}
            BASELINE_ADJUSTMENT → {"shift": BaselineShiftEvent.to_dict()}
            MODEL_UPDATE      → {"params": ModelParameters.to_dict(), "delta_ll": ...}
        source: 事件来源模块名（如 "estimator", "calibrator", "ui"）。
        event_id: 唯一标识。
        causality_id: 因果链 id。同一次"用户拖动 → 产生 Correction →
            触发 ModelUpdate"的多个事件共享 causality_id，便于回放。
    """

    timestamp: float
    event_type: StateEventType
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    event_id: str = ""
    causality_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            raise ValueError(
                f"StateEvent.timestamp 必须为正 Unix 时间戳，得到 {self.timestamp}"
            )
        if not isinstance(self.event_type, StateEventType):
            object.__setattr__(self, "event_type", StateEventType(self.event_type))
        if not self.event_id:
            # 自动生成 event_id：时间戳（微秒）+ 类型 + 计数
            eid = f"evt_{int(self.timestamp * 1_000_000)}_{self.event_type.value}"
            object.__setattr__(self, "event_id", eid)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StateEvent":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


class EventStream:
    """内存中的事件流（环形缓冲）。

    用途：
      - UI 实时显示"模型为什么这么认为"（可解释性）
      - 诊断工具回放一段交互
      - Calibrator 从中提取 Correction 序列做在线学习

    **注意**：EventStream 不是持久化层。持久化由 adapters/storage/sqlite.py
    在 Phase 8 实现。EventStream 只保证内存中最近 N 条事件可查。

    Attributes:
        maxlen: 环形缓冲容量。默认 10000 条，约 1 MB 内存。
    """

    def __init__(self, maxlen: int = 10_000) -> None:
        if maxlen <= 0:
            raise ValueError(f"EventStream.maxlen 必须为正，得到 {maxlen}")
        self._buffer: Deque[StateEvent] = deque(maxlen=maxlen)
        self._last_ts: float = 0.0
        self._monotonic_enforced: bool = True

    def __len__(self) -> int:
        return len(self._buffer)

    def __iter__(self):
        return iter(self._buffer)

    @property
    def maxlen(self) -> int:
        return self._buffer.maxlen or 0

    def append(self, event: StateEvent) -> None:
        """追加一条事件。

        默认强制时间戳单调不减（防御乱序 bug）。若需允许乱序（如导入历史
        数据），构造时设 monotonic=False。
        """
        if self._monotonic_enforced and event.timestamp + 1e-6 < self._last_ts:
            raise ValueError(
                f"StateEvent 时间戳必须单调不减：新事件 {event.timestamp} < "
                f"上一条 {self._last_ts}。若需导入乱序历史，请用 "
                f"EventStream(monotonic=False) 或 bulk_load()。"
            )
        self._buffer.append(event)
        self._last_ts = max(self._last_ts, event.timestamp)

    def bulk_load(self, events: Iterable[StateEvent]) -> int:
        """批量加载事件，不强制单调性（用于导入历史）。

        Returns:
            实际加载的条数。
        """
        n = 0
        for e in events:
            self._buffer.append(e)
            self._last_ts = max(self._last_ts, e.timestamp)
            n += 1
        return n

    def recent(self, n: int = 10) -> List[StateEvent]:
        """返回最近 n 条事件（按时间正序）。"""
        if n <= 0:
            return []
        items = list(self._buffer)
        return items[-n:]

    def filter_by_type(
        self, event_type: StateEventType, limit: Optional[int] = None
    ) -> List[StateEvent]:
        """按类型过滤。limit=None 返回全部匹配。"""
        result = [e for e in self._buffer if e.event_type == event_type]
        if limit is not None and limit >= 0:
            result = result[-limit:]
        return result

    def filter_by_window(
        self, start_ts: float, end_ts: float
    ) -> List[StateEvent]:
        """按时间窗口 [start_ts, end_ts] 过滤。"""
        if end_ts < start_ts:
            raise ValueError(
                f"filter_by_window: end_ts ({end_ts}) < start_ts ({start_ts})"
            )
        return [e for e in self._buffer if start_ts <= e.timestamp <= end_ts]

    def filter_by_causality(self, causality_id: str) -> List[StateEvent]:
        """按因果链 id 过滤。用于回放"用户拖动 → Correction → ModelUpdate"。"""
        return [e for e in self._buffer if e.causality_id == causality_id]

    def clear(self) -> None:
        """清空事件流。"""
        self._buffer.clear()
        self._last_ts = 0.0

    def to_list(self) -> List[Dict[str, Any]]:
        """导出为 dict 列表（JSON 兼容）。"""
        return [e.to_dict() for e in self._buffer]

    @classmethod
    def from_list(
        cls, data: List[Dict[str, Any]], maxlen: int = 10_000
    ) -> "EventStream":
        """从 dict 列表重建 EventStream（不强制单调，便于加载历史）。"""
        stream = cls(maxlen=maxlen)
        stream._monotonic_enforced = False
        events = [StateEvent.from_dict(d) for d in data]
        # 按时间排序后加载，保证 UI 显示顺序正确
        events.sort(key=lambda e: e.timestamp)
        stream.bulk_load(events)
        stream._monotonic_enforced = True
        return stream
