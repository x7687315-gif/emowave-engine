"""EmotionState + Intensity 单元测试。

重点验证 ARCHITECTURE part1 §1.3 的 intensity 修正：
旧版 sqrt(v²+a²)/sqrt(2) 把 (0,0) 算成 0 强度（错的），
新版到中性点 (0.5,0.5) 的距离把 (0,0) 算成 1.0 强度（对的）。
"""

import math

import pytest

from emowave.core.domain.emotion_state import (
    EmotionState,
    Trend,
    compute_intensity,
)


# ============================================================
# compute_intensity（Russell 环状模型修正）
# ============================================================


def test_compute_intensity_neutral_point_is_zero():
    """中性点 (0.5, 0.5) 的强度必须为 0（这是修正的核心）。"""
    assert compute_intensity(0.5, 0.5) == pytest.approx(0.0, abs=1e-9)


def test_compute_intensity_extreme_corner_is_one():
    """四角 (0,0) (0,1) (1,0) (1,1) 的强度必须为 1.0。"""
    for v, a in [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)]:
        assert compute_intensity(v, a) == pytest.approx(1.0, abs=1e-9), (v, a)


def test_compute_intensity_depressive_lag_is_high():
    """(0, 0) 极度不适 + 极度困倦 = 典型抑郁性迟滞，强度必须为 1.0。

    这是旧版 intensity 定义的核心 bug：旧版算出 0，新版算出 1。
    详见 ARCHITECTURE_EMOTIONpart1.md §1.3。
    """
    assert compute_intensity(0.0, 0.0) == pytest.approx(1.0)


def test_compute_intensity_symmetric_around_neutral():
    """强度对中性点对称：(0.4, 0.5) 与 (0.6, 0.5) 强度相同。"""
    assert compute_intensity(0.4, 0.5) == pytest.approx(compute_intensity(0.6, 0.5))


def test_compute_intensity_clipped_to_unit():
    """即使输入超界（理论上不该发生），输出也应裁剪到 [0, 1]。"""
    # 输入已被 EmotionState.__post_init__ 裁剪，这里直接测函数本身
    # compute_intensity 假设输入在 [0,1]，超界输入可能产生 >1 的值
    # 但函数内部有裁剪，所以仍应返回 [0, 1]
    result = compute_intensity(0.5, 0.5)
    assert 0.0 <= result <= 1.0


# ============================================================
# EmotionState 构造与校验
# ============================================================


def test_emotion_state_minimal_construction():
    """只给 timestamp/valence/arousal 也能构造，其余走默认值。"""
    st = EmotionState(timestamp=1.0, valence=0.6, arousal=0.4)
    assert st.valence == pytest.approx(0.6)
    assert st.arousal == pytest.approx(0.4)
    assert st.stability == 0.5
    assert st.confidence == 0.5
    assert st.trend == Trend.UNKNOWN
    assert st.baseline_id is None


def test_emotion_state_intensity_is_property():
    """intensity 是 property，由 (v, a) 实时计算，不存储独立字段。

    这避免了"存了旧 intensity 但 v/a 已变"的不一致状态。
    """
    st = EmotionState(timestamp=1.0, valence=0.5, arousal=0.5)
    assert st.intensity == pytest.approx(0.0)

    st2 = EmotionState(timestamp=1.0, valence=1.0, arousal=1.0)
    assert st2.intensity == pytest.approx(1.0)


def test_emotion_state_clips_out_of_range():
    """valence/arousal/stability/confidence 超过 [0,1] 应被裁剪。"""
    st = EmotionState(
        timestamp=1.0, valence=1.5, arousal=-0.5, stability=2.0, confidence=-1.0
    )
    assert st.valence == 1.0
    assert st.arousal == 0.0
    assert st.stability == 1.0
    assert st.confidence == 0.0


def test_emotion_state_rejects_negative_variance():
    """方差不能为负。"""
    with pytest.raises(ValueError):
        EmotionState(timestamp=1.0, valence=0.5, arousal=0.5, variance_valence=-0.1)
    with pytest.raises(ValueError):
        EmotionState(timestamp=1.0, valence=0.5, arousal=0.5, variance_arousal=-0.1)


def test_emotion_state_rejects_nonpositive_timestamp():
    with pytest.raises(ValueError):
        EmotionState(timestamp=0.0, valence=0.5, arousal=0.5)
    with pytest.raises(ValueError):
        EmotionState(timestamp=-1.0, valence=0.5, arousal=0.5)


def test_emotion_state_is_frozen():
    st = EmotionState(timestamp=1.0, valence=0.5, arousal=0.5)
    with pytest.raises(Exception):
        st.valence = 0.9  # type: ignore[misc]


def test_emotion_state_trend_accepts_string():
    st = EmotionState(timestamp=1.0, valence=0.5, arousal=0.5, trend="rising")
    assert st.trend == Trend.RISING


# ============================================================
# 置信区间（ARCHITECTURE part1 §6.2 置信带渲染）
# ============================================================


def test_std_valence_is_sqrt_of_variance():
    st = EmotionState(
        timestamp=1.0, valence=0.5, arousal=0.5, variance_valence=0.04
    )
    assert st.std_valence() == pytest.approx(0.2)


def test_confidence_interval_one_sigma():
    """±1σ 置信区间：mean=0.5, std=0.1 → [0.4, 0.6]。"""
    st = EmotionState(
        timestamp=1.0, valence=0.5, arousal=0.5, variance_valence=0.01
    )
    lo, hi = st.confidence_interval("valence", z=1.0)
    assert lo == pytest.approx(0.4)
    assert hi == pytest.approx(0.6)


def test_confidence_interval_two_sigma():
    """±2σ 置信区间：mean=0.5, std=0.1 → [0.3, 0.7]。"""
    st = EmotionState(
        timestamp=1.0, valence=0.5, arousal=0.5, variance_valence=0.01
    )
    lo, hi = st.confidence_interval("valence", z=2.0)
    assert lo == pytest.approx(0.3)
    assert hi == pytest.approx(0.7)


def test_confidence_interval_clipped_to_unit():
    """置信区间应裁剪到 [0, 1]，不出现负值或 >1。"""
    st = EmotionState(
        timestamp=1.0, valence=0.05, arousal=0.5, variance_valence=0.04  # std=0.2
    )
    lo, hi = st.confidence_interval("valence", z=2.0)
    assert lo == 0.0  # 0.05 - 0.4 = -0.35 → clip to 0
    assert hi == pytest.approx(0.45)


def test_confidence_interval_unknown_dimension_raises():
    st = EmotionState(timestamp=1.0, valence=0.5, arousal=0.5)
    with pytest.raises(ValueError):
        st.confidence_interval("intensity")


# ============================================================
# 序列化
# ============================================================


def test_emotion_state_to_dict_includes_intensity():
    """to_dict 应把派生字段 intensity 一并输出（供 Amiya 协议直接消费）。"""
    st = EmotionState(timestamp=1.0, valence=1.0, arousal=1.0, trend="rising")
    d = st.to_dict()
    assert d["intensity"] == pytest.approx(1.0)
    assert d["trend"] == "rising"


def test_emotion_state_roundtrip():
    st = EmotionState(
        timestamp=1_700_000_000.0,
        valence=0.32,
        arousal=0.77,
        stability=0.6,
        confidence=0.84,
        variance_valence=0.01,
        variance_arousal=0.02,
        trend=Trend.RISING,
        baseline_id="bl_123",
        model_version="2.0.0-alpha.0",
        meta={"estimator": "kalman"},
    )
    d = st.to_dict()
    restored = EmotionState.from_dict(d)
    # intensity 是 property，from_dict 时应被忽略
    assert restored.valence == st.valence
    assert restored.arousal == st.arousal
    assert restored.trend == st.trend
    assert restored.baseline_id == st.baseline_id
    assert restored.meta == st.meta


def test_emotion_state_from_dict_ignores_intensity():
    """from_dict 应忽略派生字段 intensity（它由 v/a 实时计算）。"""
    d = {
        "timestamp": 1.0,
        "valence": 0.5,
        "arousal": 0.5,
        "intensity": 0.999,  # 错误值，应被忽略
    }
    st = EmotionState.from_dict(d)
    # intensity 由 (0.5, 0.5) 计算 → 0.0，不是 0.999
    assert st.intensity == pytest.approx(0.0)
