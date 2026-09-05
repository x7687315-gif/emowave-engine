"""StateEvent + EventStream 单元测试。

重点验证：
  - 事件类型枚举完整覆盖 REFACTOR_PLAN.md §4.6 的数据流节点
  - EventStream 单调性防护（乱序追加被拒绝）
  - bulk_load 允许乱序（导入历史）
  - 按类型 / 时间窗口 / 因果链过滤
  - 环形缓冲容量上限
  - 序列化往返
"""

import time

import pytest

from emowave.core.domain.events import (
    StateEvent,
    StateEventType,
    EventStream,
)


def test_state_event_types_cover_data_flow():
    """事件类型应覆盖 REFACTOR_PLAN.md §4.6 的全部数据流节点。"""
    expected = {
        "observation",
        "emotion_estimate",
        "user_correction",
        "baseline_adjustment",
        "model_update",
        "trend_change",
        "threshold_cross",
        "crisis_alert",
        "system",
    }
    actual = {e.value for e in StateEventType}
    assert expected.issubset(actual)


def test_state_event_minimal_construction():
    evt = StateEvent(timestamp=1_700_000_000.0, event_type=StateEventType.OBSERVATION)
    assert evt.event_type == StateEventType.OBSERVATION
    assert evt.payload == {}
    assert evt.event_id.startswith("evt_")


def test_state_event_auto_generates_id():
    evt = StateEvent(timestamp=1_700_000_000.0, event_type=StateEventType.SYSTEM)
    assert "1700000000" in evt.event_id or evt.event_id.startswith("evt_")
    assert "system" in evt.event_id


def test_state_event_accepts_string_type():
    evt = StateEvent(timestamp=1.0, event_type="observation")
    assert evt.event_type == StateEventType.OBSERVATION


def test_state_event_rejects_nonpositive_timestamp():
    with pytest.raises(ValueError):
        StateEvent(timestamp=0.0, event_type=StateEventType.SYSTEM)
    with pytest.raises(ValueError):
        StateEvent(timestamp=-1.0, event_type=StateEventType.SYSTEM)


def test_state_event_is_frozen():
    evt = StateEvent(timestamp=1.0, event_type=StateEventType.SYSTEM)
    with pytest.raises(Exception):
        evt.payload = {"x": 1}  # type: ignore[misc]


def test_state_event_roundtrip():
    evt = StateEvent(
        timestamp=1_700_000_000.0,
        event_type=StateEventType.EMOTION_ESTIMATE,
        payload={"valence": 0.6, "arousal": 0.4, "confidence": 0.8},
        source="estimator",
        causality_id="causality_abc",
    )
    d = evt.to_dict()
    assert d["event_type"] == "emotion_estimate"
    restored = StateEvent.from_dict(d)
    assert restored.event_type == evt.event_type
    assert restored.payload == evt.payload
    assert restored.source == evt.source
    assert restored.causality_id == evt.causality_id


# ============================================================
# EventStream
# ============================================================


def test_event_stream_starts_empty():
    s = EventStream()
    assert len(s) == 0
    assert list(s) == []


def test_event_stream_append_and_len():
    s = EventStream()
    s.append(StateEvent(timestamp=1.0, event_type=StateEventType.OBSERVATION))
    s.append(StateEvent(timestamp=2.0, event_type=StateEventType.EMOTION_ESTIMATE))
    assert len(s) == 2


def test_event_stream_enforces_monotonic_timestamps():
    """默认强制时间戳单调不减（防御乱序 bug）。"""
    s = EventStream()
    s.append(StateEvent(timestamp=100.0, event_type=StateEventType.OBSERVATION))
    with pytest.raises(ValueError):
        s.append(StateEvent(timestamp=50.0, event_type=StateEventType.OBSERVATION))


def test_event_stream_allows_equal_timestamps():
    """相同时间戳允许（同一时刻可能产生多个事件）。"""
    s = EventStream()
    s.append(StateEvent(timestamp=100.0, event_type=StateEventType.OBSERVATION))
    s.append(StateEvent(timestamp=100.0, event_type=StateEventType.EMOTION_ESTIMATE))
    assert len(s) == 2


def test_event_stream_bulk_load_allows_out_of_order():
    """bulk_load 不强制单调性，用于导入历史数据。"""
    s = EventStream()
    events = [
        StateEvent(timestamp=100.0, event_type=StateEventType.OBSERVATION),
        StateEvent(timestamp=50.0, event_type=StateEventType.OBSERVATION),
        StateEvent(timestamp=75.0, event_type=StateEventType.EMOTION_ESTIMATE),
    ]
    n = s.bulk_load(events)
    assert n == 3
    assert len(s) == 3


def test_event_stream_maxlen_is_ring_buffer():
    """超过 maxlen 时最旧的事件被丢弃（环形缓冲）。"""
    s = EventStream(maxlen=3)
    for i in range(5):
        s.append(StateEvent(timestamp=float(i + 1), event_type=StateEventType.SYSTEM))
    assert len(s) == 3
    # 保留的应是最新的 3 条（timestamp 3, 4, 5）
    timestamps = [e.timestamp for e in s]
    assert timestamps == [3.0, 4.0, 5.0]


def test_event_stream_rejects_nonpositive_maxlen():
    with pytest.raises(ValueError):
        EventStream(maxlen=0)
    with pytest.raises(ValueError):
        EventStream(maxlen=-5)


def test_event_stream_recent():
    s = EventStream()
    for i in range(10):
        s.append(StateEvent(timestamp=float(i + 1), event_type=StateEventType.SYSTEM))
    last3 = s.recent(3)
    assert len(last3) == 3
    assert [e.timestamp for e in last3] == [8.0, 9.0, 10.0]


def test_event_stream_recent_zero_returns_empty():
    s = EventStream()
    s.append(StateEvent(timestamp=1.0, event_type=StateEventType.SYSTEM))
    assert s.recent(0) == []


def test_event_stream_filter_by_type():
    s = EventStream()
    s.append(StateEvent(timestamp=1.0, event_type=StateEventType.OBSERVATION))
    s.append(StateEvent(timestamp=2.0, event_type=StateEventType.EMOTION_ESTIMATE))
    s.append(StateEvent(timestamp=3.0, event_type=StateEventType.OBSERVATION))
    s.append(StateEvent(timestamp=4.0, event_type=StateEventType.USER_CORRECTION))

    obs = s.filter_by_type(StateEventType.OBSERVATION)
    assert len(obs) == 2
    assert [e.timestamp for e in obs] == [1.0, 3.0]

    est = s.filter_by_type(StateEventType.EMOTION_ESTIMATE)
    assert len(est) == 1


def test_event_stream_filter_by_type_with_limit():
    s = EventStream()
    for i in range(10):
        s.append(StateEvent(timestamp=float(i + 1), event_type=StateEventType.OBSERVATION))
    last3 = s.filter_by_type(StateEventType.OBSERVATION, limit=3)
    assert len(last3) == 3
    assert [e.timestamp for e in last3] == [8.0, 9.0, 10.0]


def test_event_stream_filter_by_window():
    s = EventStream()
    for i in range(10):
        s.append(StateEvent(timestamp=float(i + 1), event_type=StateEventType.SYSTEM))
    window = s.filter_by_window(3.0, 6.0)
    assert [e.timestamp for e in window] == [3.0, 4.0, 5.0, 6.0]


def test_event_stream_filter_by_window_rejects_inverted():
    s = EventStream()
    with pytest.raises(ValueError):
        s.filter_by_window(10.0, 5.0)


def test_event_stream_filter_by_causality():
    """按因果链 id 过滤，用于回放"用户拖动 → Correction → ModelUpdate"。"""
    s = EventStream()
    s.append(StateEvent(timestamp=1.0, event_type=StateEventType.OBSERVATION, causality_id="c1"))
    s.append(StateEvent(timestamp=2.0, event_type=StateEventType.USER_CORRECTION, causality_id="c1"))
    s.append(StateEvent(timestamp=3.0, event_type=StateEventType.MODEL_UPDATE, causality_id="c1"))
    s.append(StateEvent(timestamp=4.0, event_type=StateEventType.OBSERVATION, causality_id="c2"))

    chain = s.filter_by_causality("c1")
    assert len(chain) == 3
    assert [e.event_type for e in chain] == [
        StateEventType.OBSERVATION,
        StateEventType.USER_CORRECTION,
        StateEventType.MODEL_UPDATE,
    ]


def test_event_stream_clear():
    s = EventStream()
    s.append(StateEvent(timestamp=1.0, event_type=StateEventType.SYSTEM))
    s.clear()
    assert len(s) == 0
    # clear 后应能重新追加（_last_ts 被重置）
    s.append(StateEvent(timestamp=0.5, event_type=StateEventType.SYSTEM))
    assert len(s) == 1


def test_event_stream_to_list_from_list_roundtrip():
    s = EventStream()
    s.append(StateEvent(timestamp=1.0, event_type=StateEventType.OBSERVATION, payload={"v": 0.5}))
    s.append(StateEvent(timestamp=2.0, event_type=StateEventType.EMOTION_ESTIMATE, payload={"v": 0.6}))

    data = s.to_list()
    assert len(data) == 2
    assert data[0]["event_type"] == "observation"

    restored = EventStream.from_list(data)
    assert len(restored) == 2
    assert restored.recent(1)[0].event_type == StateEventType.EMOTION_ESTIMATE


def test_event_stream_from_list_sorts_by_timestamp():
    """from_list 应按时间排序后加载，保证 UI 显示顺序正确。"""
    data = [
        {"timestamp": 3.0, "event_type": "system"},
        {"timestamp": 1.0, "event_type": "observation"},
        {"timestamp": 2.0, "event_type": "emotion_estimate"},
    ]
    s = EventStream.from_list(data)
    timestamps = [e.timestamp for e in s]
    assert timestamps == [1.0, 2.0, 3.0]


def test_event_stream_iteration_preserves_order():
    s = EventStream()
    for i in range(5):
        s.append(StateEvent(timestamp=float(i + 1), event_type=StateEventType.SYSTEM))
    timestamps = [e.timestamp for e in s]
    assert timestamps == [1.0, 2.0, 3.0, 4.0, 5.0]
