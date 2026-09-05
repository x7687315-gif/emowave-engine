"""UserCorrection + 峰终加权单元测试。

重点验证 ARCHITECTURE part1 §4.2 的 reliability_weight 公式：
    w = ω_source · ω_recency · ω_salience · ω_current_mood · ω_end

以及 §4.3 Coactive Learning 语义：Correction 表达"用户版比模型版更好"，
不表达"用户版就是真值"。
"""

import math
import time

import pytest

from emowave.core.domain.correction import (
    UserCorrection,
    CorrectionDimension,
    CorrectionSource,
    compute_reliability_weight,
)


# ============================================================
# compute_reliability_weight 公式验证
# ============================================================


def test_reliability_slider_realtime_is_highest():
    """SLIDER + 实时（latency=0）+ 高 salience + 中性心境 → 权重接近 1.0。"""
    w = compute_reliability_weight(
        source=CorrectionSource.SLIDER,
        edit_latency_sec=0.0,
        salience=1.0,
        current_mood_deviation=0.0,
    )
    assert w == pytest.approx(1.0, abs=0.01)


def test_reliability_drag_old_low_salience_is_lowest():
    """DRAG + 一天前 + 低 salience + 极端心境 → 权重很低（但不为 0）。"""
    w = compute_reliability_weight(
        source=CorrectionSource.DRAG,
        edit_latency_sec=86400.0,  # 一天前
        salience=0.0,
        current_mood_deviation=1.0,
    )
    # ω_source=0.55, ω_recency=exp(-1)≈0.368, ω_salience=0.3, ω_mood=0.5
    # → 0.55 × 0.368 × 0.3 × 0.5 ≈ 0.030
    assert w < 0.05
    assert w > 0.0  # 永不为 0：保留"降权"而非"作废"的语义


def test_reliability_recency_decays_exponentially():
    """时近性应按 exp(-Δt/τ_recall) 衰减，τ=1 天。"""
    w_now = compute_reliability_weight(
        source=CorrectionSource.MANUAL, edit_latency_sec=0.0, salience=0.5
    )
    w_1day = compute_reliability_weight(
        source=CorrectionSource.MANUAL, edit_latency_sec=86400.0, salience=0.5
    )
    w_2day = compute_reliability_weight(
        source=CorrectionSource.MANUAL, edit_latency_sec=2 * 86400.0, salience=0.5
    )
    # 1 天后应衰减到约 exp(-1) ≈ 0.368 倍
    assert w_1day == pytest.approx(w_now * math.exp(-1.0), rel=0.01)
    # 2 天后应衰减到约 exp(-2) ≈ 0.135 倍
    assert w_2day == pytest.approx(w_now * math.exp(-2.0), rel=0.01)


def test_reliability_salience_monotonically_increasing():
    """salience 越高，权重越大（峰终定律：峰值附近的编辑更可靠）。"""
    ws = [
        compute_reliability_weight(
            source=CorrectionSource.DRAG,
            edit_latency_sec=0.0,
            salience=s,
        )
        for s in [0.0, 0.25, 0.5, 0.75, 1.0]
    ]
    assert ws == sorted(ws)
    assert ws[0] < ws[-1]


def test_reliability_current_mood_monotonically_decreasing():
    """当前心境越极端，权重越低（心境一致性偏差，Faul & LaBar 2023）。"""
    ws = [
        compute_reliability_weight(
            source=CorrectionSource.DRAG,
            edit_latency_sec=0.0,
            salience=0.5,
            current_mood_deviation=m,
        )
        for m in [0.0, 0.25, 0.5, 0.75, 1.0]
    ]
    assert ws == sorted(ws, reverse=True)


def test_reliability_end_boost_near_event_end():
    """接近事件结尾（60 秒内）的编辑应有结尾提升（峰终定律的"终"）。"""
    w_at_end = compute_reliability_weight(
        source=CorrectionSource.DRAG,
        edit_latency_sec=0.0,
        salience=0.5,
        seconds_before_event_end=0.0,
    )
    w_far_from_end = compute_reliability_weight(
        source=CorrectionSource.DRAG,
        edit_latency_sec=0.0,
        salience=0.5,
        seconds_before_event_end=600.0,  # 10 分钟远离结尾
    )
    assert w_at_end > w_far_from_end


def test_reliability_source_prior_ordering():
    """来源先验：SLIDER > MANUAL > VOICE > AGENT > IMPORT > DRAG。"""
    def w(src):
        return compute_reliability_weight(
            source=src, edit_latency_sec=0.0, salience=0.5
        )

    assert w(CorrectionSource.SLIDER) > w(CorrectionSource.MANUAL)
    assert w(CorrectionSource.MANUAL) > w(CorrectionSource.VOICE)
    assert w(CorrectionSource.VOICE) > w(CorrectionSource.AGENT)
    assert w(CorrectionSource.AGENT) > w(CorrectionSource.IMPORT)
    assert w(CorrectionSource.IMPORT) > w(CorrectionSource.DRAG)


def test_reliability_always_in_unit_interval():
    """任何输入组合下，权重都应落在 [0, 1]。"""
    for src in CorrectionSource:
        for latency in [0.0, 3600.0, 86400.0, 7 * 86400.0]:
            for salience in [0.0, 0.5, 1.0]:
                for mood in [0.0, 0.5, 1.0]:
                    w = compute_reliability_weight(
                        source=src,
                        edit_latency_sec=latency,
                        salience=salience,
                        current_mood_deviation=mood,
                    )
                    assert 0.0 <= w <= 1.0


# ============================================================
# UserCorrection 构造与校验
# ============================================================


def test_correction_minimal_construction():
    """最小构造：timestamp + dimension + predicted + corrected。"""
    now = time.time()
    c = UserCorrection(
        timestamp=now - 10,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.5,
        corrected_value=0.7,
        created_at=now,
    )
    assert c.dimension == CorrectionDimension.VALENCE
    assert c.source == CorrectionSource.DRAG  # 默认值
    assert c.delta == pytest.approx(0.2)
    assert c.abs_delta == pytest.approx(0.2)
    # reliability_weight 自动计算，应在 [0, 1]
    assert 0.0 <= c.reliability_weight <= 1.0
    # correction_id 自动生成
    assert c.correction_id is not None
    assert c.correction_id.startswith("cor_")


def test_correction_delta_sign_semantics():
    """delta = corrected - predicted。正号=模型低估，负号=模型高估。"""
    now = time.time()
    under = UserCorrection(
        timestamp=now - 1,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.3,
        corrected_value=0.7,
        created_at=now,
    )
    assert under.delta > 0
    assert under.is_systematic_underestimate() is True
    assert under.is_systematic_overestimate() is False

    over = UserCorrection(
        timestamp=now - 1,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.9,
        corrected_value=0.4,
        created_at=now,
    )
    assert over.delta < 0
    assert over.is_systematic_overestimate() is True
    assert over.is_systematic_underestimate() is False


def test_correction_is_frozen():
    now = time.time()
    c = UserCorrection(
        timestamp=now - 1,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.5,
        corrected_value=0.7,
        created_at=now,
    )
    with pytest.raises(Exception):
        c.corrected_value = 0.9  # type: ignore[misc]


def test_correction_rejects_created_at_before_timestamp():
    """created_at 不能早于 timestamp（不能"提前纠正未来的估计"）。"""
    with pytest.raises(ValueError):
        UserCorrection(
            timestamp=2_000_000_000.0,
            dimension=CorrectionDimension.VALENCE,
            predicted_value=0.5,
            corrected_value=0.7,
            created_at=1_000_000_000.0,  # 早于 timestamp
        )


def test_correction_rejects_negative_latency():
    with pytest.raises(ValueError):
        UserCorrection(
            timestamp=time.time() - 1,
            dimension=CorrectionDimension.VALENCE,
            predicted_value=0.5,
            corrected_value=0.7,
            edit_latency_sec=-1.0,
        )


def test_correction_dimension_accepts_string():
    now = time.time()
    c = UserCorrection(
        timestamp=now - 1,
        dimension="arousal",
        predicted_value=0.5,
        corrected_value=0.7,
        created_at=now,
    )
    assert c.dimension == CorrectionDimension.AROUSAL


def test_correction_drag_velocity_reduces_weight():
    """快速拖动（drag_velocity 大）应降低 reliability_weight。"""
    now = time.time()
    slow = UserCorrection(
        timestamp=now - 1,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.5,
        corrected_value=0.7,
        drag_velocity=0.0,
        created_at=now,
    )
    fast = UserCorrection(
        timestamp=now - 1,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.5,
        corrected_value=0.7,
        drag_velocity=5.0,
        created_at=now,
    )
    assert fast.reliability_weight < slow.reliability_weight


def test_correction_explicit_reliability_weight_is_respected():
    """显式传入 reliability_weight 时应被采用（仍裁剪到 [0,1]）。"""
    now = time.time()
    c = UserCorrection(
        timestamp=now - 1,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.5,
        corrected_value=0.7,
        reliability_weight=0.42,
        created_at=now,
    )
    assert c.reliability_weight == pytest.approx(0.42)

    c2 = UserCorrection(
        timestamp=now - 1,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.5,
        corrected_value=0.7,
        reliability_weight=1.5,  # 超界
        created_at=now,
    )
    assert c2.reliability_weight == 1.0


def test_correction_roundtrip():
    now = time.time()
    c = UserCorrection(
        timestamp=now - 60,
        dimension=CorrectionDimension.AROUSAL,
        predicted_value=0.4,
        corrected_value=0.8,
        source=CorrectionSource.SLIDER,
        reason="我其实更焦虑",
        edit_session_mood=0.3,
        edit_latency_sec=60.0,
        drag_velocity=0.5,
        salience=0.7,
        seconds_before_event_end=30.0,
        created_at=now,
        meta={"ui": "surfing_window"},
    )
    d = c.to_dict()
    assert d["dimension"] == "arousal"
    assert d["source"] == "slider"
    restored = UserCorrection.from_dict(d)
    assert restored.dimension == c.dimension
    assert restored.source == c.source
    assert restored.reason == c.reason
    assert restored.reliability_weight == pytest.approx(c.reliability_weight)
    assert restored.meta == c.meta


def test_correction_baseline_dimension_supported():
    """BASELINE / THRESHOLD 维度用于 Phase 5 基线主权的入口。"""
    now = time.time()
    c = UserCorrection(
        timestamp=now - 1,
        dimension=CorrectionDimension.BASELINE,
        predicted_value=0.55,
        corrected_value=0.67,
        source=CorrectionSource.MANUAL,
        created_at=now,
    )
    assert c.dimension == CorrectionDimension.BASELINE
