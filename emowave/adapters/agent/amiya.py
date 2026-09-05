"""amiya — EmoWave → Amiya Agent 集成适配器（EmotionBridge）。

REFACTOR_PLAN.md §14-§18 + ARCHITECTURE LIGHTWEIGHT part2 §3。

核心定位（REFACTOR_PLAN §3.3）：
    EmoWave = State Engine（"用户现在大概处于什么状态？"）
    Amiya   = Companion Agent（"我应该怎样理解、回应和陪伴？"）
Amiya 不重新计算 EmoWave 已算好的情绪状态；EmoWave 不承担人格/Prompt/LLM 职责。

不采用直接 import 依赖（§14.1）：禁止 `from amiya.core.agent import Agent`
成为 EmoWave 启动条件。正确模式（§14.2）：
    EmoWave Core ── Standalone（完整运行，不需要 Amiya）
               └── Amiya Adapter ── Amiya Agent（存在则启用额外能力）

四级优雅降级（part2 §3.2，REFACTOR_PLAN §18）：
    L0 启动期：emowave 未安装/import 失败 → 完全静默回退（Amiya 侧）
    L1 配置期：EMOWAVE_ENABLED=0（默认）→ 根本不尝试（Amiya 侧）
    L2 运行期：单次调用抛异常 → 捕获+warning，本轮回退规则分类，绝不上抛
    L3 能力期：设备档位低/省电 → 内核降级（Tier，只保留 L1 因果滤波）

连续 (v,a) → Amiya 4 状态映射（part2 §3.4）：
    Amiya 现有 core/emotion.py 输出 4 个离散键 calm/thinking/worried/happy。
    EmoWave 提供同样签名的 detect(text)->str，即可从"关键词猜"升级为
    "连续效价-唤醒模型推断"，下游（提示词注入、皮肤联动）完全不用改。

    映射表（part2 §3.4）存在条件重叠（a>0.65 同时满足 a>0.5），原文未定优先级。
    本实现的解析（见 map_state_to_amiya_key 文档）：高唤醒时优先用效价区分
    worried/happy（保留困扰信号，对陪伴 Agent 至关重要），效价中性才落 thinking。

红线（part2 §3.2）：情绪只流向 persona_state，绝不写入 memory 表。
本适配器只返回状态键与结论性状态，不触碰 Amiya 的任何存储。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from emowave.core.domain.emotion_state import EmotionState, compute_intensity
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource
from emowave.core.estimator.estimator import EstimatorConfig, StateEstimator
from emowave.core.protocol.schemas import (
    AmiyaCapability,
    AmiyaHandshake,
    AmiyaInput,
    AmiyaInputType,
    EmotionStateOutput,
    Envelope,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
)
from emowave.core.tier import Tier, detect_tier

log = logging.getLogger(__name__)

# Amiya core/emotion.py 的 4 个离散状态键（part2 §3.1 实地勘察）
EMOTION_KEYS = ("calm", "thinking", "worried", "happy")

# 环境变量
ENV_ENABLED = "EMOWAVE_ENABLED"

# 规则回退分类器的关键词表（镜像 Amiya core/emotion.py 的轻量规则，
# 作为 EmoWave 不可用时的安全底座，REFACTOR_PLAN §19 Amiya Emotion Fallback）
_WORRIED_WORDS = ("焦虑", "担心", "害怕", "紧张", "压力", "烦", "慌", "不安", "怕", "愁")
_HAPPY_WORDS = ("开心", "高兴", "快乐", "兴奋", "喜欢", "棒", "好耶", "哈哈", "愉快", "满足")
_THINKING_WORDS = ("想", "思考", "琢磨", "分析", "为什么", "怎么", "考虑", "研究", "疑惑")
_CALM_WORDS = ("平静", "放松", "安静", "还好", "一般", "没事", "还行")


def rule_based_fallback(user_text: str) -> str:
    """轻量规则情绪分类（零依赖，永远可用）。

    镜像 Amiya core/emotion.py 的关键词规则，作为 EmoWave 连续模型不可用时的
    安全底座（REFACTOR_PLAN §19：旧 Emotion 不被废弃，而是成为 Graceful
    Degradation 的安全底座）。

    优先级：worried（负面优先，陪伴场景安全考量）> happy > thinking > calm。
    """
    if not user_text:
        return "calm"
    text = user_text.lower()
    if any(w in text for w in _WORRIED_WORDS):
        return "worried"
    if any(w in text for w in _HAPPY_WORDS):
        return "happy"
    if any(w in text for w in _THINKING_WORDS):
        return "thinking"
    if any(w in text for w in _CALM_WORDS):
        return "calm"
    return "calm"


def map_state_to_amiya_key(valence: float, arousal: float) -> str:
    """连续 (v, a) → Amiya 4 离散状态键（part2 §3.4 映射）。

    part2 §3.4 的映射表存在条件重叠（a>0.65 同时满足 worried/happy 的 a>0.5），
    且原文未定义优先级——若按表行顺序 first-match，thinking 行（a>0.65）会被
    worried/happy 行（a>0.5）抢先匹配而成为死代码；若 thinking 优先，则高唤醒
    时丢失效价信息（v=0.1,a=0.9 的极度困扰会被判为 thinking，对陪伴 Agent 有害）。

    本实现的解析（兼顾两者）：
      - a ≤ 0.5（低唤醒）→ calm
      - a > 0.65（高唤醒/认知负荷）：
          v < 0.4 → worried（负性高唤醒=困扰，保留安全信号）
          v > 0.6 → happy（正性高唤醒=兴奋）
          否则    → thinking（中性高唤醒=认知投入，对应表中"无论效价"的本意）
      - 0.5 < a ≤ 0.65（中唤醒）：
          v < 0.5 → worried，否则 → happy

    Args:
        valence: 效价 [0,1]
        arousal: 唤醒 [0,1]

    Returns:
        EMOTION_KEYS 之一
    """
    v = max(0.0, min(1.0, float(valence)))
    a = max(0.0, min(1.0, float(arousal)))

    if a <= 0.5:
        return "calm"
    if a > 0.65:
        if v < 0.4:
            return "worried"
        if v > 0.6:
            return "happy"
        return "thinking"
    # 0.5 < a <= 0.65
    return "worried" if v < 0.5 else "happy"


class EmotionBridge:
    """EmoWave → Amiya 的集成桥（part2 §3.3 骨架的 EmoWave 侧实现）。

    Amiya 侧只需 `from emowave import EmotionBridge` 并调用 detect()，
    即可把"关键词猜"升级为"连续效价-唤醒模型推断"，且任何失败都优雅回退。

    职责：
      - detect(text, valence, arousal, now) → Amiya 4 状态键（兼容签名）
      - handshake() → 能力发现（REFACTOR_PLAN §15）
      - get_state_output() → EmoWave→Amiya 结论性状态（§16，不泄露内部）
      - ingest_input(AmiyaInput) → Observation（§17，不直接覆盖状态）
      - 四级优雅降级（L2 运行期异常捕获在本类，L0/L1 在 Amiya 侧 wrapper）

    standalone 模式（§14.2）：Amiya 不存在时 EmoWave 完整运行——本桥不依赖
    任何 Amiya 代码，Amiya 缺失只是没人调用它而已。
    """

    # 默认连接/调用超时（秒）。超时视为不可用，回退（§28 connection timeout）
    DEFAULT_TIMEOUT_SEC = 2.0

    def __init__(
        self,
        params: Optional[ModelParameters] = None,
        config: Optional[EstimatorConfig] = None,
        enabled: Optional[bool] = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        tier: Optional[Tier] = None,
    ) -> None:
        # L1 配置期：enabled 默认读 EMOWAVE_ENABLED（默认关闭，opt-in，part2 §3.2）
        if enabled is None:
            enabled = _env_enabled()
        self.enabled = enabled
        self.timeout_sec = timeout_sec
        # L3 能力期：Tier 探测（低配/省电只保留 L1 因果滤波）
        self.tier = tier if tier is not None else detect_tier()
        self._params = params or ModelParameters()
        self._config = config or EstimatorConfig()
        self._estimator: Optional[StateEstimator] = None
        self._last_state: Optional[EmotionState] = None
        self._handshake_cache: Optional[AmiyaHandshake] = None
        # 降级统计（诊断用）
        self._fallback_count = 0
        self._call_count = 0

    # ---------- 内部：估计器懒初始化 ----------

    def _get_estimator(self) -> StateEstimator:
        if self._estimator is None:
            self._estimator = StateEstimator(params=self._params, config=self._config)
        return self._estimator

    # ---------- 主接口：detect（Amiya 兼容签名）----------

    def detect(
        self,
        user_text: str,
        *,
        valence: Optional[float] = None,
        arousal: Optional[float] = None,
        now: Optional[float] = None,
    ) -> str:
        """优先用 EmoWave 连续模型推断 Amiya 状态键；任何失败都回退规则分类。

        part2 §3.3：保证返回合法状态键（EMOTION_KEYS 之一），绝不向上抛异常
        （L2 运行期降级）。

        流程：
          1. 未启用（enabled=False）→ 直接规则回退（L1）
          2. 有显式 (v,a) → 喂估计器 → 连续模型映射
          3. 仅有文本 → 轻量文本→(v,a) 估计（关键词加权）→ 连续模型
          4. 任何异常 → 规则回退 + warning（L2）
        """
        self._call_count += 1

        # L1：未启用，根本不跑模型
        if not self.enabled:
            return rule_based_fallback(user_text)

        try:
            ts = now if now is not None else time.time()
            v, a = self._resolve_valence_arousal(user_text, valence, arousal)
            if v is None or a is None:
                # 无法得到连续状态 → 规则回退
                return self._fallback(user_text, "no_continuous_state")

            est = self._get_estimator()
            obs = Observation(
                timestamp=ts, valence=v, arousal=a, source=ObservationSource.USER
            )
            state = est.update(obs)
            self._last_state = state
            key = map_state_to_amiya_key(state.valence, state.arousal)
            # 防御：映射结果必须合法（part2 §3.3 防御内核返回非法键）
            if key not in EMOTION_KEYS:
                return self._fallback(user_text, "illegal_key")
            return key
        except Exception as e:  # noqa: BLE001 — L2 绝不上抛
            return self._fallback(user_text, f"exception:{e}")

    def _resolve_valence_arousal(
        self,
        user_text: str,
        valence: Optional[float],
        arousal: Optional[float],
    ) -> tuple:
        """得到 (v, a)：显式提供优先，否则从文本轻量估计。

        part2 §8 开放问题1：Amiya 纯文本驱动时，需要文本→(v,a) 映射。
        这里用关键词加权做轻量估计（零依赖，不引入新模型），作为可选路径。
        """
        if valence is not None and arousal is not None:
            return float(valence), float(arousal)
        # 文本轻量估计
        return self._text_to_va(user_text)

    def _text_to_va(self, text: str) -> tuple:
        """轻量文本→(v,a) 估计（关键词加权，零依赖）。

        效价：正面词 +，负面词 -，从 0.5 起。
        唤醒：情绪词（无论正负）+，思考词轻微 +，从 0.4 起。
        这是 part2 §8 开放问题1 的最小可行实现，未来可替换为小模型。
        """
        if not text:
            return (None, None)
        t = text.lower()
        v = 0.5
        a = 0.4
        for w in _HAPPY_WORDS:
            if w in t:
                v += 0.12
                a += 0.10
        for w in _WORRIED_WORDS:
            if w in t:
                v -= 0.15
                a += 0.18
        for w in _THINKING_WORDS:
            if w in t:
                a += 0.08
        for w in _CALM_WORDS:
            if w in t:
                a -= 0.10
        v = max(0.0, min(1.0, v))
        a = max(0.0, min(1.0, a))
        # 若文本没有任何情绪线索，返回 None 让上层走规则回退
        if v == 0.5 and a == 0.4:
            return (None, None)
        return (v, a)

    def _fallback(self, user_text: str, reason: str) -> str:
        """L2 运行期降级：回退规则分类，记录 warning，绝不上抛。"""
        self._fallback_count += 1
        if reason and not reason.startswith("no_continuous_state"):
            log.warning("EmoWave detect 回退规则分类（%s）", reason)
        return rule_based_fallback(user_text)

    # ---------- handshake / capability discovery（§15）----------

    def handshake(
        self,
        agent_version: str = "1.x",
        capabilities: Optional[List[Any]] = None,
        timeout: Optional[float] = None,
    ) -> Optional[AmiyaHandshake]:
        """与 Amiya 握手 + 能力发现（REFACTOR_PLAN §15）。

        连接流程：Detect Amiya → Handshake → Capability Discovery →
        Enable Optional Features。不假设 Amiya 永远存在/版本一致/能力可用。

        Args:
            agent_version: Amiya 声明的版本
            capabilities: Amiya 声明的能力列表
            timeout: 连接超时（秒），超时返回 None（§28 connection timeout）

        Returns:
            AmiyaHandshake（成功）或 None（超时/不可用/版本不兼容）
        """
        timeout = self.timeout_sec if timeout is None else timeout
        try:
            t0 = time.time()
            hs = AmiyaHandshake(
                agent="amiya",
                version=agent_version,
                capabilities=list(capabilities or []),
                protocol_version=PROTOCOL_VERSION,
                schema_version=SCHEMA_VERSION,
                available=True,
            )
            # 模拟连接耗时检查（真实场景这里是 IPC/网络往返）
            if (time.time() - t0) > timeout:
                log.info("Amiya 握手超时（>%.1fs），降级 standalone", timeout)
                return None
            # 版本兼容性检查（§18 version mismatch fallback）
            if not hs.is_compatible():
                log.info(
                    "Amiya 版本不兼容（schema=%d protocol=%d），禁用集成适配器",
                    hs.schema_version, hs.protocol_version,
                )
                self._handshake_cache = None
                return None
            self._handshake_cache = hs
            return hs
        except Exception as e:  # noqa: BLE001
            log.warning("Amiya 握手异常，降级 standalone：%s", e)
            return None

    def supports(self, capability: AmiyaCapability) -> bool:
        """Amiya 是否声明支持某能力（握手后可用）。未握手则 False。"""
        if self._handshake_cache is None:
            return False
        return self._handshake_cache.supports(capability)

    @property
    def is_connected(self) -> bool:
        """是否已成功握手且版本兼容。"""
        return self._handshake_cache is not None

    # ---------- EmoWave → Amiya 输出协议（§16）----------

    def get_state_output(self) -> Optional[EmotionStateOutput]:
        """输出结论性状态给 Amiya（§16：只给结论，不泄露 Kalman/DB/内部缓存）。

        Returns:
            EmotionStateOutput（有最近状态时）或 None（尚无估计/未启用）
        """
        if not self.enabled or self._last_state is None:
            return None
        try:
            st = self._last_state
            bl_v, bl_a = self._params_baseline()
            return EmotionStateOutput(
                timestamp=st.timestamp,
                valence=st.valence,
                arousal=st.arousal,
                intensity=st.intensity,
                baseline_valence=bl_v,
                baseline_arousal=bl_a,
                trend=st.trend.value,
                confidence=st.confidence,
                emotion_label=map_state_to_amiya_key(st.valence, st.arousal),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("生成 Amiya 状态输出失败：%s", e)
            return None

    def _params_baseline(self) -> tuple:
        """从参数/最近状态推导基线（简化：用 0.5 中性或最近状态）。"""
        # 完整实现应接 BaselineController（Phase 5），这里用中性默认
        return (0.5, 0.5)

    def state_envelope(self) -> Optional[Envelope]:
        """把状态输出封装为 Envelope（跨进程传输，§16/§27）。"""
        out = self.get_state_output()
        if out is None:
            return None
        return out.to_envelope()

    # ---------- Amiya → EmoWave 输入协议（§17）----------

    def ingest_input(self, amiya_input: AmiyaInput) -> Optional[EmotionState]:
        """摄入 Amiya 提供的上下文（§17：不直接覆盖状态，走正常 Observation 流程）。

        Amiya 可以提供 user_feedback / conversation_context /
        explicit_emotion_statement / manual_intervention，但不能直接覆盖
        EmoWave 状态。source=user 的显式输入优先级最高（confidence=1）。

        Returns:
            更新后的 EmotionState，或 None（未启用/输入无情绪通道/异常）
        """
        if not self.enabled:
            return None
        try:
            obs_dict = amiya_input.to_observation_dict()
            obs = Observation.from_dict(obs_dict)
            if not obs.has_emotion_channel():
                return None
            est = self._get_estimator()
            state = est.update(obs)
            self._last_state = state
            return state
        except Exception as e:  # noqa: BLE001
            log.warning("摄入 Amiya 输入失败：%s", e)
            return None

    # ---------- 诊断 ----------

    @property
    def fallback_count(self) -> int:
        return self._fallback_count

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def last_state(self) -> Optional[EmotionState]:
        return self._last_state

    def reset(self) -> None:
        """重置桥状态（估计器 + 握手缓存 + 最近状态）。"""
        self._estimator = None
        self._last_state = None
        self._handshake_cache = None
        self._fallback_count = 0
        self._call_count = 0


def _env_enabled() -> bool:
    """读取 EMOWAVE_ENABLED（默认关闭，opt-in，part2 §3.2 关键设计）。"""
    return (os.getenv(ENV_ENABLED) or "0").strip().lower() in ("1", "true", "yes", "on")
