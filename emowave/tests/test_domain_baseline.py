"""Baseline + BaselineShiftEvent + select_active_baseline 单元测试。

重点验证：
  - 基线版本化（baseline_id / version / parent_id 形成链表）
  - regime 分段（Fork 产生新 regime_id）
  - effective_from/to 时间窗口
  - select_active_baseline 在历史中正确选出生效版本
  - with_updates 不可变更新语义
"""

import time

import pytest

from emowave.core.domain.baseline import (
    Baseline,
    BaselineShiftEvent,
    BaselineSource,
    select_active_baseline,
)


def test_baseline_defaults_are_population_prior():
    """默认值 = 群体先验（冷启动可用）。"""
    b = Baseline()
    assert b.valence == pytest.approx(0.55)
    assert b.arousal == pytest.approx(0.42)
    assert b.intensity == pytest.approx(0.35)
    assert b.stability == pytest.approx(0.70)
    assert b.resting_hr == 72.0
    assert b.resting_hrv_mean == 50.0
    assert b.sleep_score == 7.0
    assert b.source == BaselineSource.POPULATION
    assert b.confidence == pytest.approx(0.3)


def test_baseline_id_is_unique():
    """每次构造生成唯一 baseline_id。"""
    b1 = Baseline()
    b2 = Baseline()
    assert b1.baseline_id != b2.baseline_id
    assert b1.baseline_id.startswith("bl_")


def test_baseline_clips_unit_dimensions():
    b = Baseline(valence=1.5, arousal=-0.5, intensity=2.0, stability=-1.0, confidence=1.5)
    assert b.valence == 1.0
    assert b.arousal == 0.0
    assert b.intensity == 1.0
    assert b.stability == 0.0
    assert b.confidence == 1.0


def test_baseline_rejects_negative_physio():
    with pytest.raises(ValueError):
        Baseline(resting_hr=-60.0)
    with pytest.raises(ValueError):
        Baseline(resting_hrv_mean=-5.0)


def test_baseline_rejects_sleep_out_of_range():
    with pytest.raises(ValueError):
        Baseline(sleep_score=11.0)
    with pytest.raises(ValueError):
        Baseline(sleep_score=-1.0)


def test_baseline_rejects_effective_to_before_from():
    now = time.time()
    with pytest.raises(ValueError):
        Baseline(effective_from=now, effective_to=now - 100)


def test_baseline_is_active_at():
    """is_active_at 正确判断时间窗口。"""
    now = time.time()
    b = Baseline(effective_from=now - 100, effective_to=now + 100)
    assert b.is_active_at(now) is True
    assert b.is_active_at(now - 200) is False
    assert b.is_active_at(now + 200) is False


def test_baseline_is_active_at_open_ended():
    """effective_to=None 表示仍然生效。"""
    now = time.time()
    b = Baseline(effective_from=now - 100, effective_to=None)
    assert b.is_active_at(now) is True
    assert b.is_active_at(now + 10_000) is True
    assert b.is_active_at(now - 200) is False


def test_baseline_deviation_zero_at_baseline():
    """(v, a) 等于基线时 deviation = 0。"""
    b = Baseline(valence=0.55, arousal=0.42)
    assert b.deviation(0.55, 0.42) == pytest.approx(0.0, abs=1e-9)


def test_baseline_deviation_max_at_opposite_corner():
    """(v, a) 在基线对角时 deviation 最大（接近 1）。"""
    b = Baseline(valence=0.0, arousal=0.0)
    assert b.deviation(1.0, 1.0) == pytest.approx(1.0, abs=1e-9)


def test_baseline_with_updates_creates_new_version():
    """with_updates 返回新对象，version+1，parent_id 指向旧 id。"""
    b1 = Baseline(valence=0.55, arousal=0.42)
    b2 = b1.with_updates(valence=0.67, source=BaselineSource.USER_NUDGE)

    assert b2 is not b1
    assert b2.baseline_id != b1.baseline_id
    assert b2.version == b1.version + 1
    assert b2.parent_id == b1.baseline_id
    assert b2.valence == pytest.approx(0.67)
    assert b2.arousal == pytest.approx(0.42)  # 未变的字段保留
    assert b2.source == BaselineSource.USER_NUDGE
    # 旧对象不变
    assert b1.valence == pytest.approx(0.55)
    assert b1.version == 1


def test_baseline_with_updates_chain():
    """多次 with_updates 形成版本链。"""
    b1 = Baseline(valence=0.55)
    b2 = b1.with_updates(valence=0.60)
    b3 = b2.with_updates(valence=0.65)
    assert b3.version == 3
    assert b3.parent_id == b2.baseline_id
    assert b2.parent_id == b1.baseline_id


def test_baseline_source_accepts_string():
    b = Baseline(source="user_fork")
    assert b.source == BaselineSource.USER_FORK


def test_baseline_is_frozen():
    b = Baseline()
    with pytest.raises(Exception):
        b.valence = 0.9  # type: ignore[misc]


def test_baseline_roundtrip():
    b = Baseline(
        valence=0.62,
        arousal=0.48,
        intensity=0.40,
        stability=0.75,
        resting_hr=68.0,
        resting_hrv_mean=55.0,
        sleep_score=8.0,
        source=BaselineSource.USER_NUDGE,
        regime_id="regime_abc",
        version=3,
        confidence=0.7,
    )
    d = b.to_dict()
    assert d["source"] == "user_nudge"
    restored = Baseline.from_dict(d)
    assert restored.valence == b.valence
    assert restored.source == b.source
    assert restored.regime_id == b.regime_id
    assert restored.version == b.version


# ============================================================
# BaselineShiftEvent
# ============================================================


def test_baseline_shift_event_construction():
    now = time.time()
    evt = BaselineShiftEvent(
        timestamp=now,
        old_baseline_id="bl_old",
        new_baseline_id="bl_new",
        shift_type=BaselineSource.USER_FORK,
        reason="失恋后我觉得我的正常状态变了",
        deltas={"valence": -0.15, "arousal": 0.10},
        user_confirmed=True,
        detector_confidence=0.0,
    )
    assert evt.shift_type == BaselineSource.USER_FORK
    assert evt.user_confirmed is True
    assert evt.deltas["valence"] == -0.15


def test_baseline_shift_event_roundtrip():
    now = time.time()
    evt = BaselineShiftEvent(
        timestamp=now,
        old_baseline_id="bl_old",
        new_baseline_id="bl_new",
        shift_type="user_nudge",
        reason="微调",
        deltas={"valence": 0.05},
    )
    d = evt.to_dict()
    assert d["shift_type"] == "user_nudge"
    restored = BaselineShiftEvent.from_dict(d)
    assert restored.shift_type == BaselineSource.USER_NUDGE
    assert restored.reason == "微调"


def test_baseline_shift_event_clips_detector_confidence():
    evt = BaselineShiftEvent(
        timestamp=time.time(),
        old_baseline_id=None,
        new_baseline_id="bl_new",
        shift_type=BaselineSource.EWMA_AUTO,
        detector_confidence=1.5,
    )
    assert evt.detector_confidence == 1.0


# ============================================================
# select_active_baseline
# ============================================================


def test_select_active_baseline_picks_correct_one():
    """在 Baseline 历史中选出在指定时刻生效的那一条。"""
    t0 = 1_000_000.0
    b1 = Baseline(effective_from=t0, effective_to=t0 + 100, valence=0.5)
    b2 = Baseline(effective_from=t0 + 100, effective_to=t0 + 200, valence=0.6)
    b3 = Baseline(effective_from=t0 + 200, effective_to=None, valence=0.7)

    assert select_active_baseline([b1, b2, b3], t0 + 50) is b1
    assert select_active_baseline([b1, b2, b3], t0 + 150) is b2
    assert select_active_baseline([b1, b2, b3], t0 + 250) is b3


def test_select_active_baseline_returns_none_before_all():
    """时间戳早于所有 Baseline 时返回 None。"""
    t0 = 1_000_000.0
    b1 = Baseline(effective_from=t0, effective_to=None)
    assert select_active_baseline([b1], t0 - 100) is None


def test_select_active_baseline_tie_break_by_version():
    """理论上不该有多条同时生效，但防御性处理：取 version 最大的。"""
    t0 = 1_000_000.0
    b1 = Baseline(effective_from=t0, effective_to=None, version=1, valence=0.5)
    b2 = Baseline(effective_from=t0, effective_to=None, version=2, valence=0.6)
    # 注意：with_updates 会生成新 id，这里直接构造两个独立 Baseline 模拟重叠
    result = select_active_baseline([b1, b2], t0 + 50)
    assert result is b2  # version 大的胜出


def test_select_active_baseline_empty_list():
    assert select_active_baseline([], time.time()) is None
