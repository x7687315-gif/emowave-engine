"""Amiya Adapter（EmotionBridge）单元测试。

覆盖 REFACTOR_PLAN.md §14-§18 + §28 Phase 8 + part2 §3：
  - 连续 (v,a) → Amiya 4 状态映射（part2 §3.4）
  - detect 兼容签名 + 文本轻量估计
  - handshake / capability discovery（§15）
  - EmoWave→Amiya 输出协议（§16，不泄露内部）
  - Amiya→EmoWave 输入协议（§17，不覆盖状态）
  - 四级优雅降级（part2 §3.2）
  - §22 Degradation Test：Amiya 不存在/断开/版本不兼容时 EmoWave 正常
  - §22 Reverse Degradation Test：EmoWave 不存在时 Amiya 仍可用规则回退
  - §28 完成标准：EmoWave without Amiya / Amiya without EmoWave / 两者并存
"""

import time

import pytest

from emowave.adapters.agent.amiya import (
    EMOTION_KEYS,
    EmotionBridge,
    map_state_to_amiya_key,
    rule_based_fallback,
)
from emowave.core.domain.observation import Observation
from emowave.core.protocol.schemas import (
    AmiyaCapability,
    AmiyaInput,
    AmiyaInputType,
    EmotionStateOutput,
    SCHEMA_VERSION,
    PROTOCOL_VERSION,
)


# ============================================================
# map_state_to_amiya_key（part2 §3.4 映射）
# ============================================================


def test_map_low_arousal_is_calm():
    """低唤醒（a≤0.5）→ calm，无论效价。"""
    assert map_state_to_amiya_key(0.2, 0.3) == "calm"
    assert map_state_to_amiya_key(0.8, 0.4) == "calm"
    assert map_state_to_amiya_key(0.5, 0.5) == "calm"


def test_map_negative_high_arousal_is_worried():
    """负性 + 高唤醒 → worried（保留困扰信号）。"""
    assert map_state_to_amiya_key(0.1, 0.9) == "worried"   # 极度困扰
    assert map_state_to_amiya_key(0.3, 0.8) == "worried"
    assert map_state_to_amiya_key(0.2, 0.6) == "worried"   # 中唤醒负性


def test_map_positive_high_arousal_is_happy():
    """正性 + 高唤醒 → happy。"""
    assert map_state_to_amiya_key(0.9, 0.9) == "happy"
    assert map_state_to_amiya_key(0.8, 0.7) == "happy"
    assert map_state_to_amiya_key(0.7, 0.6) == "happy"     # 中唤醒正性


def test_map_neutral_very_high_arousal_is_thinking():
    """中性效价 + 很高唤醒（a>0.65）→ thinking（认知投入）。"""
    assert map_state_to_amiya_key(0.5, 0.9) == "thinking"
    assert map_state_to_amiya_key(0.45, 0.8) == "thinking"
    assert map_state_to_amiya_key(0.55, 0.75) == "thinking"


def test_map_always_returns_valid_key():
    """任何 (v,a) 输入都返回合法键。"""
    for v in [0.0, 0.25, 0.5, 0.75, 1.0]:
        for a in [0.0, 0.25, 0.5, 0.6, 0.7, 1.0]:
            assert map_state_to_amiya_key(v, a) in EMOTION_KEYS


def test_map_clips_out_of_range():
    assert map_state_to_amiya_key(1.5, -0.5) in EMOTION_KEYS
    # v=-1→0, a=2→1：a>0.65 且 v<0.4 → worried
    assert map_state_to_amiya_key(-1.0, 2.0) == "worried"
    # v=1.5→1, a=-0.5→0：a≤0.5 → calm
    assert map_state_to_amiya_key(1.5, -0.5) == "calm"


def test_map_preserves_distress_signal():
    """关键产品要求：高唤醒负性必须判 worried 而非 thinking（陪伴安全）。"""
    # v=0.1（很负面）, a=0.95（很高唤醒）→ 必须 worried，不能因 a>0.65 判 thinking
    assert map_state_to_amiya_key(0.1, 0.95) == "worried"


# ============================================================
# rule_based_fallback（Amiya Emotion Fallback，§19）
# ============================================================


def test_rule_fallback_worried():
    assert rule_based_fallback("我好焦虑啊") == "worried"
    assert rule_based_fallback("压力好大") == "worried"


def test_rule_fallback_happy():
    assert rule_based_fallback("今天很开心") == "happy"


def test_rule_fallback_thinking():
    assert rule_based_fallback("我在想这个问题") == "thinking"


def test_rule_fallback_calm():
    assert rule_based_fallback("感觉很平静") == "calm"
    assert rule_based_fallback("") == "calm"
    assert rule_based_fallback("blah blah 无情绪词") == "calm"


def test_rule_fallback_worried_priority():
    """负面优先（陪伴场景安全考量）：同时含负面和正面词时判 worried。"""
    assert rule_based_fallback("有点开心但又有点担心") == "worried"


def test_rule_fallback_always_valid():
    for text in ["", "随便说点什么", "焦虑", "开心", "思考", "平静"]:
        assert rule_based_fallback(text) in EMOTION_KEYS


# ============================================================
# EmotionBridge.detect（Amiya 兼容签名）
# ============================================================


def test_bridge_disabled_uses_rule_fallback():
    """L1 配置期：enabled=False（默认）→ 根本不跑模型，直接规则回退。"""
    bridge = EmotionBridge(enabled=False)
    assert bridge.detect("我好焦虑") == "worried"  # 规则分类
    assert bridge.detect("很开心", valence=0.9, arousal=0.8) == "happy"  # 仍规则


def test_bridge_enabled_with_explicit_va():
    """启用 + 显式 (v,a) → 连续模型映射。"""
    bridge = EmotionBridge(enabled=True)
    # 负性高唤醒 → worried
    assert bridge.detect("x", valence=0.15, arousal=0.9, now=1000.0) == "worried"


def test_bridge_enabled_text_only():
    """启用 + 仅文本 → 轻量文本→(v,a) 估计 → 连续模型。"""
    bridge = EmotionBridge(enabled=True)
    result = bridge.detect("我好焦虑啊", now=1000.0)
    assert result in EMOTION_KEYS


def test_bridge_text_no_emotion_cue_falls_back():
    """文本无情绪线索 → 无法得到连续状态 → 规则回退。"""
    bridge = EmotionBridge(enabled=True)
    result = bridge.detect("the quick brown fox", now=1000.0)
    assert result in EMOTION_KEYS  # 回退到 calm


def test_bridge_detect_never_raises():
    """L2 运行期：任何异常都被捕获，绝不上抛（part2 §3.2 红线）。"""
    bridge = EmotionBridge(enabled=True)
    # 各种边界输入都不应抛异常
    for args in [
        ("", {}),
        ("text", {"valence": None, "arousal": None}),
        ("text", {"valence": float("nan"), "arousal": 0.5}),
        ("text", {"valence": 0.5, "arousal": float("inf")}),
    ]:
        text, kw = args
        result = bridge.detect(text, now=1000.0, **kw)
        assert result in EMOTION_KEYS


def test_bridge_detect_nan_falls_back_gracefully():
    """NaN 输入应优雅回退而非崩溃或返回非法键。"""
    bridge = EmotionBridge(enabled=True)
    result = bridge.detect("焦虑", valence=float("nan"), arousal=float("nan"), now=1000.0)
    assert result in EMOTION_KEYS


def test_bridge_sequential_detect_updates_state():
    """连续 detect 应更新内部状态（估计器持续工作）。"""
    bridge = EmotionBridge(enabled=True)
    bridge.detect("a", valence=0.5, arousal=0.5, now=1000.0)
    bridge.detect("b", valence=0.6, arousal=0.6, now=1001.0)
    assert bridge.last_state is not None
    assert bridge.call_count == 2


def test_bridge_default_enabled_from_env(monkeypatch):
    """enabled 默认读 EMOWAVE_ENABLED（默认关闭，opt-in，part2 §3.2）。"""
    monkeypatch.delenv("EMOWAVE_ENABLED", raising=False)
    bridge = EmotionBridge()
    assert bridge.enabled is False
    monkeypatch.setenv("EMOWAVE_ENABLED", "1")
    bridge2 = EmotionBridge()
    assert bridge2.enabled is True


# ============================================================
# handshake / capability discovery（§15）
# ============================================================


def test_handshake_success():
    bridge = EmotionBridge(enabled=True)
    hs = bridge.handshake(agent_version="1.2.0",
                          capabilities=["emotion_context", "conversation"])
    assert hs is not None
    assert hs.agent == "amiya"
    assert bridge.is_connected is True


def test_handshake_capability_discovery():
    bridge = EmotionBridge(enabled=True)
    bridge.handshake(capabilities=["emotion_context", "recommendation"])
    assert bridge.supports(AmiyaCapability.EMOTION_CONTEXT) is True
    assert bridge.supports(AmiyaCapability.RECOMMENDATION) is True
    assert bridge.supports(AmiyaCapability.VOICE) is False


def test_supports_without_handshake_is_false():
    bridge = EmotionBridge(enabled=True)
    assert bridge.supports(AmiyaCapability.EMOTION_CONTEXT) is False
    assert bridge.is_connected is False


def test_handshake_version_mismatch_returns_none():
    """§18 version mismatch fallback：schema 不兼容 → 禁用集成，返回 None。"""
    bridge = EmotionBridge(enabled=True)
    # 构造一个 schema 不兼容的握手（通过直接传高 protocol 版本模拟对端过新）
    from emowave.core.protocol.schemas import AmiyaHandshake
    hs = AmiyaHandshake(schema_version=SCHEMA_VERSION + 1)
    assert hs.is_compatible() is False
    # handshake() 内部用当前 SCHEMA_VERSION，总是兼容；这里验证不兼容检测逻辑
    # 通过 monkeypatch 模拟对端声明不兼容版本
    original = AmiyaHandshake.is_compatible
    AmiyaHandshake.is_compatible = lambda self: False
    try:
        result = bridge.handshake(agent_version="999.0")
        assert result is None
        assert bridge.is_connected is False
    finally:
        AmiyaHandshake.is_compatible = original


def test_handshake_does_not_raise():
    bridge = EmotionBridge(enabled=True)
    # 异常输入不应崩溃
    hs = bridge.handshake(agent_version="", capabilities=None)
    assert hs is None or hs.agent == "amiya"


# ============================================================
# EmoWave → Amiya 输出协议（§16）
# ============================================================


def test_state_output_none_before_any_detect():
    bridge = EmotionBridge(enabled=True)
    assert bridge.get_state_output() is None


def test_state_output_after_detect():
    bridge = EmotionBridge(enabled=True)
    bridge.detect("x", valence=0.2, arousal=0.85, now=1000.0)
    out = bridge.get_state_output()
    assert isinstance(out, EmotionStateOutput)
    assert out.emotion_label == "worried"  # v=0.2<0.4, a=0.85>0.65
    assert 0.0 <= out.valence <= 1.0


def test_state_output_disabled_is_none():
    bridge = EmotionBridge(enabled=False)
    bridge.detect("x", valence=0.2, arousal=0.85, now=1000.0)
    assert bridge.get_state_output() is None


def test_state_output_does_not_leak_internals():
    """§16：输出只含结论性状态，不泄露 Kalman 参数/DB/内部缓存。"""
    bridge = EmotionBridge(enabled=True)
    bridge.detect("x", valence=0.5, arousal=0.6, now=1000.0)
    out = bridge.get_state_output()
    d = out.to_dict()
    forbidden = {"covariance", "kalman", "P_matrix", "state_vector", "db_path", "sqlite"}
    assert forbidden.isdisjoint(d.keys())
    # 应含协议字段
    assert "valence" in d and "arousal" in d and "trend" in d and "confidence" in d


def test_state_envelope():
    bridge = EmotionBridge(enabled=True)
    bridge.detect("x", valence=0.5, arousal=0.6, now=1000.0)
    env = bridge.state_envelope()
    assert env is not None
    assert env.kind == "emowave.emotion_state"
    assert env.schema_version == SCHEMA_VERSION


# ============================================================
# Amiya → EmoWave 输入协议（§17）
# ============================================================


def test_ingest_input_updates_state():
    """§17：Amiya 输入走正常 Observation 流程，更新状态（不直接覆盖）。"""
    bridge = EmotionBridge(enabled=True)
    ai = AmiyaInput(
        input_type=AmiyaInputType.EXPLICIT_EMOTION_STATEMENT,
        timestamp=1000.0,
        valence=0.18,
        arousal=0.82,
        confidence=1.0,
        source="user",
        text="我现在很焦虑",
    )
    state = bridge.ingest_input(ai)
    assert state is not None
    assert state.valence < 0.5  # 负性
    assert bridge.last_state is not None


def test_ingest_input_disabled_is_none():
    bridge = EmotionBridge(enabled=False)
    ai = AmiyaInput(input_type=AmiyaInputType.USER_FEEDBACK, timestamp=1000.0,
                    valence=0.5, arousal=0.5)
    assert bridge.ingest_input(ai) is None


def test_ingest_input_no_emotion_channel_is_none():
    """输入无情绪通道（纯文本上下文）→ 不更新状态，返回 None。"""
    bridge = EmotionBridge(enabled=True)
    ai = AmiyaInput(input_type=AmiyaInputType.CONVERSATION_CONTEXT, timestamp=1000.0,
                    text="聊到工作")  # 无 valence/arousal
    assert bridge.ingest_input(ai) is None


def test_ingest_input_does_not_raise():
    bridge = EmotionBridge(enabled=True)
    ai = AmiyaInput(input_type=AmiyaInputType.USER_FEEDBACK, timestamp=1000.0,
                    valence=float("nan"), arousal=0.5)
    # 不应崩溃（NaN 会被 Observation 裁剪或捕获）
    result = bridge.ingest_input(ai)
    assert result is None or hasattr(result, "valence")


# ============================================================
# §22 Degradation Test：Amiya 缺失/断开/不兼容时 EmoWave 正常
# ============================================================


def test_degradation_amiya_absent_emowave_still_works():
    """Amiya 不存在（从未握手）→ EmoWave 核心完整运行。"""
    bridge = EmotionBridge(enabled=True)
    # 不握手，直接用 EmoWave 核心
    assert bridge.is_connected is False
    # detect 仍工作（不依赖 Amiya）
    result = bridge.detect("x", valence=0.5, arousal=0.6, now=1000.0)
    assert result in EMOTION_KEYS
    # 核心估计器独立运行
    from emowave.core.estimator.estimator import StateEstimator
    est = StateEstimator()
    est.initialize(timestamp=1000.0)
    st = est.update(Observation(timestamp=1001.0, valence=0.6, arousal=0.4))
    assert st is not None  # EmoWave without Amiya ✅


def test_degradation_amiya_disconnect_emowave_continues():
    """Amiya 连接失败/断开 → EmoWave 不阻塞，继续运行。"""
    bridge = EmotionBridge(enabled=True)
    bridge.handshake(capabilities=["emotion_context"])
    assert bridge.is_connected is True
    # 模拟断开（重置握手）
    bridge.reset()
    assert bridge.is_connected is False
    # EmoWave 核心继续工作
    result = bridge.detect("x", valence=0.5, arousal=0.6, now=1000.0)
    assert result in EMOTION_KEYS


def test_degradation_version_mismatch_disables_adapter():
    """Amiya 版本不兼容 → 禁用集成适配器，Core 继续运行（§18）。"""
    bridge = EmotionBridge(enabled=True)
    from emowave.core.protocol.schemas import AmiyaHandshake
    original = AmiyaHandshake.is_compatible
    AmiyaHandshake.is_compatible = lambda self: False
    try:
        hs = bridge.handshake(agent_version="999.0")
        assert hs is None  # 集成被禁用
    finally:
        AmiyaHandshake.is_compatible = original
    # Core 不受影响
    result = bridge.detect("x", valence=0.5, arousal=0.6, now=1000.0)
    assert result in EMOTION_KEYS


def test_degradation_connection_timeout():
    """连接超时 → 返回 None，降级 standalone（§28 connection timeout）。"""
    bridge = EmotionBridge(enabled=True, timeout_sec=0.0)
    # timeout=0 且握手内有耗时检查 → 可能超时返回 None（取决于实现）
    hs = bridge.handshake(timeout=-1.0)  # 负超时必然"超时"
    # 负 timeout：elapsed(≈0) > -1 为真 → 超时 → None
    assert hs is None


# ============================================================
# §22 Reverse Degradation Test：EmoWave 缺失时 Amiya 仍可用
# ============================================================


def test_reverse_degradation_amiya_works_without_emowave():
    """EmoWave 不可用时，Amiya 用规则回退仍能分类情绪（§19 安全底座）。

    模拟：EmotionBridge 内部估计器抛异常 → detect 回退 rule_based_fallback。
    这对应 Amiya 侧 emotion_bridge.py 在 emowave import 失败时的行为。
    """
    bridge = EmotionBridge(enabled=True)
    # 注入故障：让估计器 update 抛异常
    class BrokenEstimator:
        def update(self, obs):
            raise RuntimeError("模拟 EmoWave 内核故障")
    bridge._estimator = BrokenEstimator()
    # detect 应捕获异常并回退规则分类，绝不上抛
    result = bridge.detect("我好焦虑啊", valence=0.2, arousal=0.9, now=1000.0)
    assert result == "worried"  # 规则回退命中"焦虑"
    assert bridge.fallback_count >= 1


def test_reverse_degradation_rule_fallback_is_standalone():
    """rule_based_fallback 零依赖，完全独立于 EmoWave 内核（Amiya 安全底座）。"""
    # 即使没有任何 EmoWave 状态，规则分类也能工作
    assert rule_based_fallback("开心") == "happy"
    assert rule_based_fallback("担心") == "worried"


# ============================================================
# §28 完成标准：三种组合都工作
# ============================================================


def test_completion_emowave_without_amiya():
    """EmoWave without Amiya ✅"""
    from emowave.core.estimator.estimator import StateEstimator
    est = StateEstimator()
    est.initialize(timestamp=1000.0)
    st = est.update(Observation(timestamp=1001.0, valence=0.6, arousal=0.4))
    assert st is not None


def test_completion_amiya_without_emowave():
    """Amiya without EmoWave ✅（规则回退）"""
    assert rule_based_fallback("焦虑") == "worried"


def test_completion_emowave_plus_amiya():
    """EmoWave + Amiya ✅（握手 + 连续模型 + 状态输出 + 输入摄入）"""
    bridge = EmotionBridge(enabled=True)
    hs = bridge.handshake(capabilities=["emotion_context"])
    assert hs is not None
    key = bridge.detect("x", valence=0.2, arousal=0.85, now=1000.0)
    assert key == "worried"
    out = bridge.get_state_output()
    assert out is not None
    ai = AmiyaInput(input_type=AmiyaInputType.USER_FEEDBACK, timestamp=1001.0,
                    valence=0.6, arousal=0.3, source="user")
    state = bridge.ingest_input(ai)
    assert state is not None


def test_bridge_reset():
    bridge = EmotionBridge(enabled=True)
    bridge.detect("x", valence=0.5, arousal=0.6, now=1000.0)
    bridge.handshake(capabilities=["emotion_context"])
    bridge.reset()
    assert bridge.last_state is None
    assert bridge.is_connected is False
    assert bridge.call_count == 0


def test_bridge_tier_aware():
    """L3 能力期：桥持有 Tier（低配可降级，part2 §3.2）。"""
    from emowave.core.tier import Tier
    bridge = EmotionBridge(enabled=True, tier=Tier.EMBED)
    assert bridge.tier == Tier.EMBED
