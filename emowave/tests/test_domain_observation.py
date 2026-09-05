"""Observation 领域模型单元测试。

覆盖：
  - 构造与字段默认值
  - frozen 不可变性（原始数据不可变原则）
  - 输入裁剪与非法值防护
  - source 枚举与字符串互转
  - to_dict / from_dict 序列化往返
  - has_emotion_channel / has_physio_channel 辅助方法
"""

import pytest

from emowave.core.domain.observation import Observation, ObservationSource


def test_observation_minimal_construction():
    """只给 timestamp 也能构造：所有情绪字段可选。"""
    obs = Observation(timestamp=1_700_000_000.0)
    assert obs.timestamp == 1_700_000_000.0
    assert obs.valence is None
    assert obs.arousal is None
    assert obs.hr is None
    assert obs.hrv is None
    assert obs.source == ObservationSource.USER
    assert obs.confidence == 1.0


def test_observation_full_construction():
    """全字段构造，验证各字段被正确保存。"""
    obs = Observation(
        timestamp=1_700_000_000.0,
        valence=0.32,
        arousal=0.77,
        hr=88.0,
        hrv=32.0,
        sleep=6.5,
        activity=0.4,
        source=ObservationSource.SENSOR,
        confidence=0.85,
        meta={"device": "watch_v2"},
    )
    assert obs.valence == pytest.approx(0.32)
    assert obs.arousal == pytest.approx(0.77)
    assert obs.hr == 88.0
    assert obs.hrv == 32.0
    assert obs.sleep == 6.5
    assert obs.activity == 0.4
    assert obs.source == ObservationSource.SENSOR
    assert obs.confidence == pytest.approx(0.85)
    assert obs.meta == {"device": "watch_v2"}


def test_observation_is_frozen():
    """frozen=True 保证 Observation 不可变（原始数据不可变原则的代码级支撑）。"""
    obs = Observation(timestamp=1_700_000_000.0, valence=0.5)
    with pytest.raises(Exception):  # FrozenInstanceError
        obs.valence = 0.9  # type: ignore[misc]


def test_observation_clips_valence_out_of_range():
    """valence/arousal/activity 超过 [0,1] 应被裁剪，不抛异常（防御性设计）。"""
    obs_high = Observation(timestamp=1.0, valence=1.5, arousal=2.0, activity=3.0)
    assert obs_high.valence == 1.0
    assert obs_high.arousal == 1.0
    assert obs_high.activity == 1.0

    obs_low = Observation(timestamp=1.0, valence=-0.5, arousal=-1.0, activity=-2.0)
    assert obs_low.valence == 0.0
    assert obs_low.arousal == 0.0
    assert obs_low.activity == 0.0


def test_observation_clips_confidence():
    """confidence 超过 [0,1] 应被裁剪。"""
    obs = Observation(timestamp=1.0, confidence=1.5)
    assert obs.confidence == 1.0
    obs2 = Observation(timestamp=1.0, confidence=-0.3)
    assert obs2.confidence == 0.0


def test_observation_rejects_negative_timestamp():
    """timestamp 必须为正 Unix 时间戳。"""
    with pytest.raises(ValueError):
        Observation(timestamp=0.0)
    with pytest.raises(ValueError):
        Observation(timestamp=-1.0)


def test_observation_rejects_negative_physio():
    """hr / hrv 不能为负。"""
    with pytest.raises(ValueError):
        Observation(timestamp=1.0, hr=-60.0)
    with pytest.raises(ValueError):
        Observation(timestamp=1.0, hrv=-5.0)


def test_observation_rejects_sleep_out_of_range():
    """sleep 必须在 [0, 10]。"""
    with pytest.raises(ValueError):
        Observation(timestamp=1.0, sleep=11.0)
    with pytest.raises(ValueError):
        Observation(timestamp=1.0, sleep=-1.0)


def test_observation_source_accepts_string():
    """source 允许传字符串，自动转 Enum（便于 JSON 反序列化）。"""
    obs = Observation(timestamp=1.0, source="sensor")
    assert obs.source == ObservationSource.SENSOR
    assert isinstance(obs.source, ObservationSource)


def test_observation_source_rejects_unknown_string():
    """未知 source 字符串应抛 ValueError。"""
    with pytest.raises(ValueError):
        Observation(timestamp=1.0, source="unknown_source")


def test_observation_has_emotion_channel():
    """has_emotion_channel 只在 valence 或 arousal 至少一个非 None 时为 True。"""
    assert Observation(timestamp=1.0).has_emotion_channel() is False
    assert Observation(timestamp=1.0, valence=0.5).has_emotion_channel() is True
    assert Observation(timestamp=1.0, arousal=0.5).has_emotion_channel() is True
    assert Observation(timestamp=1.0, hr=80.0).has_emotion_channel() is False


def test_observation_has_physio_channel():
    """has_physio_channel 只在 hr 或 hrv 至少一个非 None 时为 True。"""
    assert Observation(timestamp=1.0).has_physio_channel() is False
    assert Observation(timestamp=1.0, hr=80.0).has_physio_channel() is True
    assert Observation(timestamp=1.0, hrv=45.0).has_physio_channel() is True
    assert Observation(timestamp=1.0, valence=0.5).has_physio_channel() is False


def test_observation_to_dict_from_dict_roundtrip():
    """to_dict → from_dict 应完全还原。"""
    obs = Observation(
        timestamp=1_700_000_000.0,
        valence=0.32,
        arousal=0.77,
        hr=88.0,
        hrv=32.0,
        source=ObservationSource.SENSOR,
        confidence=0.85,
        meta={"device": "watch"},
    )
    d = obs.to_dict()
    # Enum 应被转为字符串值（JSON 兼容）
    assert d["source"] == "sensor"
    restored = Observation.from_dict(d)
    assert restored == obs


def test_observation_from_dict_tolerates_unknown_fields():
    """from_dict 应忽略未知字段（前向兼容：新代码读老数据不崩）。"""
    d = {
        "timestamp": 1.0,
        "valence": 0.5,
        "future_field": "some_new_thing",
        "another_unknown": 42,
    }
    obs = Observation.from_dict(d)
    assert obs.timestamp == 1.0
    assert obs.valence == 0.5


def test_observation_from_dict_tolerates_missing_optional_fields():
    """from_dict 允许缺失可选字段。"""
    obs = Observation.from_dict({"timestamp": 1.0})
    assert obs.timestamp == 1.0
    assert obs.valence is None


# ============================================================
# NaN/Inf 输入硬化（代码审查 HIGH 项回归）
# ============================================================


def test_nan_valence_becomes_unobserved():
    """NaN 效价应被当作未观察（None），而非穿透污染下游状态。"""
    import math
    obs = Observation(timestamp=1.0, valence=float("nan"), arousal=0.5)
    assert obs.valence is None
    assert obs.arousal == 0.5  # 另一通道不受影响


def test_inf_valence_becomes_unobserved():
    import math
    obs = Observation(timestamp=1.0, valence=float("inf"), arousal=float("-inf"))
    assert obs.valence is None
    assert obs.arousal is None


def test_nan_activity_becomes_unobserved():
    obs = Observation(timestamp=1.0, activity=float("nan"))
    assert obs.activity is None


def test_nan_physio_becomes_unobserved():
    obs = Observation(timestamp=1.0, hr=float("nan"), hrv=float("inf"))
    assert obs.hr is None
    assert obs.hrv is None


def test_nan_sleep_becomes_unobserved():
    obs = Observation(timestamp=1.0, sleep=float("nan"))
    assert obs.sleep is None


def test_nan_confidence_falls_back_to_default():
    obs = Observation(timestamp=1.0, valence=0.5, confidence=float("nan"))
    assert obs.confidence == 1.0  # 安全默认，非 NaN


def test_nan_timestamp_rejected():
    with pytest.raises(ValueError):
        Observation(timestamp=float("nan"), valence=0.5)


def test_inf_timestamp_rejected():
    with pytest.raises(ValueError):
        Observation(timestamp=float("inf"), valence=0.5)
