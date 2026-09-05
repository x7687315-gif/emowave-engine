"""Protocol Schemas 单元测试。

覆盖 REFACTOR_PLAN.md §15-§18 与 §27：
  - 版本号常量
  - Envelope 通用封装 + 兼容性检查
  - AmiyaHandshake + Capability Discovery
  - EmotionStateOutput（EmoWave → Amiya）
  - AmiyaInput（Amiya → EmoWave）+ 转 Observation
  - Graceful Degradation：版本不兼容时拒绝，不崩溃
"""

import time

import pytest

from emowave.core.protocol.schemas import (
    SCHEMA_VERSION,
    PROTOCOL_VERSION,
    MODEL_VERSION,
    Envelope,
    AmiyaCapability,
    AmiyaHandshake,
    EmotionStateOutput,
    AmiyaInput,
    AmiyaInputType,
)


# ============================================================
# 版本号
# ============================================================


def test_version_constants_are_defined():
    """三个版本号必须定义（REFACTOR_PLAN.md §27）。"""
    assert isinstance(SCHEMA_VERSION, int)
    assert isinstance(PROTOCOL_VERSION, int)
    assert isinstance(MODEL_VERSION, str)
    assert SCHEMA_VERSION >= 1
    assert PROTOCOL_VERSION >= 1
    assert MODEL_VERSION  # 非空


def test_model_version_is_semver_like():
    """model_version 应是语义化版本（major.minor.patch[-prerelease]）。"""
    parts = MODEL_VERSION.split(".")
    assert len(parts) >= 3
    assert parts[0].isdigit()
    assert parts[1].isdigit()


# ============================================================
# Envelope
# ============================================================


def test_envelope_minimal_construction():
    env = Envelope(kind="test.message")
    assert env.kind == "test.message"
    assert env.schema_version == SCHEMA_VERSION
    assert env.protocol_version == PROTOCOL_VERSION
    assert env.model_version == MODEL_VERSION
    assert env.source == "emowave-core"
    assert env.message_id.startswith("msg_")


def test_envelope_rejects_empty_kind():
    with pytest.raises(ValueError):
        Envelope(kind="")


def test_envelope_rejects_nonpositive_timestamp():
    with pytest.raises(ValueError):
        Envelope(kind="test", timestamp=0.0)
    with pytest.raises(ValueError):
        Envelope(kind="test", timestamp=-1.0)


def test_envelope_is_frozen():
    env = Envelope(kind="test")
    with pytest.raises(Exception):
        env.kind = "other"  # type: ignore[misc]


def test_envelope_compatibility_same_schema():
    """schema_version 相等 + protocol_version 对端更低或相等 → 兼容。"""
    env = Envelope(kind="test")
    assert env.is_compatible(SCHEMA_VERSION, PROTOCOL_VERSION) is True
    # 对端 protocol 更低（我们向下兼容）
    if PROTOCOL_VERSION > 1:
        assert env.is_compatible(SCHEMA_VERSION, PROTOCOL_VERSION - 1) is True


def test_envelope_incompatible_schema_mismatch():
    """schema_version 不等 → 不兼容（数据结构变化直接拒绝）。"""
    env = Envelope(kind="test")
    assert env.is_compatible(SCHEMA_VERSION + 1, PROTOCOL_VERSION) is False
    assert env.is_compatible(SCHEMA_VERSION - 1, PROTOCOL_VERSION) is False


def test_envelope_incompatible_protocol_too_new():
    """对端 protocol_version 比我们高 → 不兼容（我们不认识新协议）。"""
    env = Envelope(kind="test")
    assert env.is_compatible(SCHEMA_VERSION, PROTOCOL_VERSION + 1) is False


def test_envelope_roundtrip():
    env = Envelope(
        kind="emowave.emotion_state",
        payload={"valence": 0.6, "arousal": 0.4},
        causality_id="causality_123",
        source="estimator",
    )
    d = env.to_dict()
    restored = Envelope.from_dict(d)
    assert restored.kind == env.kind
    assert restored.payload == env.payload
    assert restored.causality_id == env.causality_id
    assert restored.message_id == env.message_id


def test_envelope_from_dict_tolerates_unknown_fields():
    d = {"kind": "test", "future_field": "x"}
    env = Envelope.from_dict(d)
    assert env.kind == "test"


# ============================================================
# AmiyaHandshake（REFACTOR_PLAN.md §15）
# ============================================================


def test_amiya_handshake_minimal():
    hs = AmiyaHandshake()
    assert hs.agent == "amiya"
    assert hs.available is True
    assert hs.capabilities == []


def test_amiya_handshake_capabilities_from_strings():
    """capabilities 允许传字符串列表，自动转 Enum。"""
    hs = AmiyaHandshake(
        version="1.2.0",
        capabilities=["emotion_context", "conversation", "recommendation"],
    )
    assert AmiyaCapability.EMOTION_CONTEXT in hs.capabilities
    assert AmiyaCapability.CONVERSATION in hs.capabilities
    assert AmiyaCapability.RECOMMENDATION in hs.capabilities
    assert hs.supports(AmiyaCapability.EMOTION_CONTEXT) is True
    assert hs.supports(AmiyaCapability.VOICE) is False


def test_amiya_handshake_ignores_unknown_capabilities():
    """未知能力应被忽略（前向兼容：Amiya 新增能力，老 EmoWave 不崩溃）。"""
    hs = AmiyaHandshake(capabilities=["emotion_context", "future_capability_xyz"])
    assert len(hs.capabilities) == 1
    assert hs.capabilities[0] == AmiyaCapability.EMOTION_CONTEXT


def test_amiya_handshake_is_compatible():
    hs = AmiyaHandshake(
        schema_version=SCHEMA_VERSION, protocol_version=PROTOCOL_VERSION
    )
    assert hs.is_compatible() is True


def test_amiya_handshake_incompatible_schema():
    hs = AmiyaHandshake(schema_version=SCHEMA_VERSION + 1)
    assert hs.is_compatible() is False


def test_amiya_handshake_incompatible_protocol_too_new():
    hs = AmiyaHandshake(protocol_version=PROTOCOL_VERSION + 1)
    assert hs.is_compatible() is False


def test_amiya_handshake_roundtrip():
    hs = AmiyaHandshake(
        version="1.2.0",
        capabilities=[AmiyaCapability.EMOTION_CONTEXT, AmiyaCapability.VOICE],
        available=True,
    )
    d = hs.to_dict()
    assert d["capabilities"] == ["emotion_context", "voice"]
    restored = AmiyaHandshake.from_dict(d)
    assert restored.version == hs.version
    assert set(restored.capabilities) == set(hs.capabilities)


# ============================================================
# EmotionStateOutput（REFACTOR_PLAN.md §16）
# ============================================================


def test_emotion_state_output_minimal():
    out = EmotionStateOutput(
        timestamp=1_700_000_000.0, valence=0.32, arousal=0.77, intensity=0.81
    )
    assert out.valence == pytest.approx(0.32)
    assert out.arousal == pytest.approx(0.77)
    assert out.intensity == pytest.approx(0.81)
    assert out.trend == "unknown"
    assert out.confidence == 0.5


def test_emotion_state_output_clips_unit_fields():
    out = EmotionStateOutput(
        timestamp=1.0, valence=1.5, arousal=-0.5, intensity=2.0, confidence=-1.0
    )
    assert out.valence == 1.0
    assert out.arousal == 0.0
    assert out.intensity == 1.0
    assert out.confidence == 0.0


def test_emotion_state_output_normalizes_unknown_trend():
    out = EmotionStateOutput(
        timestamp=1.0, valence=0.5, arousal=0.5, intensity=0.0, trend="weird"
    )
    assert out.trend == "unknown"


def test_emotion_state_output_rejects_nonpositive_timestamp():
    with pytest.raises(ValueError):
        EmotionStateOutput(timestamp=0.0, valence=0.5, arousal=0.5, intensity=0.0)


def test_emotion_state_output_to_envelope():
    """to_envelope 应产生 kind=emowave.emotion_state 的 Envelope。"""
    out = EmotionStateOutput(
        timestamp=1_700_000_000.0,
        valence=0.32,
        arousal=0.77,
        intensity=0.81,
        trend="rising",
        confidence=0.84,
    )
    env = out.to_envelope()
    assert env.kind == "emowave.emotion_state"
    assert env.payload["valence"] == pytest.approx(0.32)
    assert env.payload["trend"] == "rising"
    assert env.schema_version == SCHEMA_VERSION


def test_emotion_state_output_does_not_leak_internals():
    """输出协议不应包含 Kalman 参数 / 数据库字段 / 内部缓存（REFACTOR_PLAN.md §16）。"""
    out = EmotionStateOutput(
        timestamp=1.0, valence=0.5, arousal=0.5, intensity=0.0
    )
    d = out.to_dict()
    forbidden_keys = {
        "covariance",
        "kalman",
        "P_matrix",
        "state_vector",
        "db_path",
        "sqlite",
        "internal_cache",
    }
    assert forbidden_keys.isdisjoint(d.keys())


def test_emotion_state_output_roundtrip():
    out = EmotionStateOutput(
        timestamp=1_700_000_000.0,
        valence=0.32,
        arousal=0.77,
        intensity=0.81,
        baseline_valence=0.56,
        baseline_arousal=0.42,
        trend="rising",
        confidence=0.84,
        emotion_label="worried",
    )
    d = out.to_dict()
    restored = EmotionStateOutput.from_dict(d)
    assert restored.valence == out.valence
    assert restored.trend == out.trend
    assert restored.emotion_label == out.emotion_label


# ============================================================
# AmiyaInput（REFACTOR_PLAN.md §17）
# ============================================================


def test_amiya_input_minimal():
    inp = AmiyaInput(
        input_type=AmiyaInputType.USER_FEEDBACK, timestamp=1_700_000_000.0
    )
    assert inp.input_type == AmiyaInputType.USER_FEEDBACK
    assert inp.valence is None
    assert inp.arousal is None
    assert inp.source == "agent_inferred"
    assert inp.is_user_explicit is False


def test_amiya_input_user_explicit_has_highest_priority():
    """source=user 的显式输入优先级最高（REFACTOR_PLAN.md §17）。"""
    inp = AmiyaInput(
        input_type=AmiyaInputType.EXPLICIT_EMOTION_STATEMENT,
        timestamp=1.0,
        valence=0.18,
        arousal=0.82,
        confidence=1.0,
        source="user",
        text="我现在很焦虑",
    )
    assert inp.is_user_explicit is True


def test_amiya_input_clips_unit_fields():
    inp = AmiyaInput(
        input_type=AmiyaInputType.USER_FEEDBACK,
        timestamp=1.0,
        valence=1.5,
        arousal=-0.5,
        confidence=2.0,
    )
    assert inp.valence == 1.0
    assert inp.arousal == 0.0
    assert inp.confidence == 1.0


def test_amiya_input_normalizes_unknown_source():
    inp = AmiyaInput(
        input_type=AmiyaInputType.USER_FEEDBACK, timestamp=1.0, source="weird"
    )
    assert inp.source == "agent_inferred"


def test_amiya_input_accepts_string_type():
    inp = AmiyaInput(input_type="manual_intervention", timestamp=1.0)
    assert inp.input_type == AmiyaInputType.MANUAL_INTERVENTION


def test_amiya_input_rejects_nonpositive_timestamp():
    with pytest.raises(ValueError):
        AmiyaInput(input_type=AmiyaInputType.USER_FEEDBACK, timestamp=0.0)


def test_amiya_input_to_observation_dict():
    """AmiyaInput 应能转为 Observation 兼容 dict（source=agent）。

    REFACTOR_PLAN.md §17：Amiya 不能直接覆盖 EmoWave 状态，
    而是走正常 Observation 流程。
    """
    inp = AmiyaInput(
        input_type=AmiyaInputType.EXPLICIT_EMOTION_STATEMENT,
        timestamp=1_700_000_000.0,
        valence=0.18,
        arousal=0.82,
        confidence=0.9,
        source="agent_inferred",
        text="用户说很焦虑",
    )
    d = inp.to_observation_dict()
    assert d["source"] == "agent"
    assert d["valence"] == pytest.approx(0.18)
    assert d["arousal"] == pytest.approx(0.82)
    # agent_inferred 的 confidence 透传
    assert d["confidence"] == pytest.approx(0.9)
    assert d["meta"]["amiya_input_type"] == "explicit_emotion_statement"
    assert d["meta"]["text"] == "用户说很焦虑"


def test_amiya_input_user_explicit_observation_confidence_is_one():
    """source=user 的显式输入转 Observation 后 confidence 应为 1.0（最高优先级）。"""
    inp = AmiyaInput(
        input_type=AmiyaInputType.MANUAL_INTERVENTION,
        timestamp=1.0,
        valence=0.2,
        arousal=0.9,
        confidence=0.5,  # Amiya 自己的置信度
        source="user",
    )
    d = inp.to_observation_dict()
    # 但 source=user 时强制 confidence=1.0
    assert d["confidence"] == 1.0


def test_amiya_input_roundtrip():
    inp = AmiyaInput(
        input_type=AmiyaInputType.CONVERSATION_CONTEXT,
        timestamp=1_700_000_000.0,
        valence=0.4,
        arousal=0.6,
        confidence=0.7,
        source="agent_inferred",
        text="聊到工作压力",
        meta={"session_id": "sess_123"},
    )
    d = inp.to_dict()
    assert d["input_type"] == "conversation_context"
    restored = AmiyaInput.from_dict(d)
    assert restored.input_type == inp.input_type
    assert restored.text == inp.text
    assert restored.meta == inp.meta


def test_amiya_input_from_dict_tolerates_unknown_fields():
    d = {
        "input_type": "user_feedback",
        "timestamp": 1.0,
        "future_field": "x",
    }
    inp = AmiyaInput.from_dict(d)
    assert inp.input_type == AmiyaInputType.USER_FEEDBACK


# ============================================================
# Graceful Degradation（REFACTOR_PLAN.md §18）
# ============================================================


def test_graceful_degradation_schema_mismatch_does_not_crash():
    """schema 不兼容时 is_compatible 返回 False，但不抛异常（优雅降级）。"""
    hs = AmiyaHandshake(schema_version=999)
    # 不崩溃，只是返回 False
    assert hs.is_compatible() is False


def test_graceful_degradation_unknown_capability_ignored():
    """Amiya 声明未知能力时，EmoWave 忽略它而不是崩溃。"""
    hs = AmiyaHandshake(capabilities=["emotion_context", "brand_new_capability_2030"])
    assert len(hs.capabilities) == 1
    assert hs.supports(AmiyaCapability.EMOTION_CONTEXT) is True
