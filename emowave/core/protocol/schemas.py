"""Protocol Schemas — EmoWave 对外协议与版本 schema。

REFACTOR_PLAN.md §27 版本与兼容性：

    {
      "schema_version": 1,
      "protocol_version": 1,
      "model_version": "0.1.0"
    }

三个版本号的职责：
  - schema_version  数据结构版本（字段增删、类型变化）。整数，单调递增。
  - protocol_version 通信协议版本（消息封装、能力协商）。整数，单调递增。
  - model_version   算法模型版本（Kalman 参数化、学习算法）。语义化字符串。

REFACTOR_PLAN.md §16 EmoWave → Amiya 输出协议：
    只输出"结论性状态"，不输出 Kalman 参数 / 数据库表结构 / 内部缓存。
    这样协议长期稳定，Core 可以自由演进。

REFACTOR_PLAN.md §15 Amiya Handshake / Capability Discovery：
    {
      "agent": "amiya",
      "version": "1.x",
      "capabilities": ["emotion_context", "conversation", "recommendation"]
    }

REFACTOR_PLAN.md §17 Amiya → EmoWave 输入协议：
    允许 user_feedback / conversation_context / explicit_emotion_statement /
    manual_intervention，其中 source=user 的显式输入优先级最高。

设计约束：
  - 零依赖：只用 dataclass + typing + enum
  - 前向兼容：Envelope 携带 schema_version，接收方据此决定解析策略
  - 后向兼容：from_dict 容忍未知字段，老代码读新数据不崩溃
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

# ============================================================
# 版本号（REFACTOR_PLAN.md §27）
# ============================================================

SCHEMA_VERSION: int = 1
"""数据结构版本。字段增删或类型变化时递增。"""

PROTOCOL_VERSION: int = 1
"""通信协议版本。消息封装、能力协商规则变化时递增。"""

MODEL_VERSION: str = "2.0.0-alpha.0"
"""算法模型版本。语义化：major.minor.patch[-prerelease]。

    major：不兼容的算法变化（如从 Kalman 切到 GP）
    minor：向后兼容的功能新增（如新增一个可学习超参数）
    patch：向后兼容的 bug 修复
"""


# ============================================================
# Envelope — 通用消息封装
# ============================================================


@dataclass(frozen=True)
class Envelope:
    """通用消息封装。所有跨进程 / 跨语言传输的 EmoWave 消息都应包在 Envelope 里。

    Attributes:
        kind: 消息类型（如 "observation", "emotion_state", "correction",
            "baseline_shift", "amiya_handshake", "amiya_output"）。
        payload: 消息体（dict）。结构由 kind 决定。
        schema_version: 数据结构版本，接收方据此决定解析策略。
        protocol_version: 通信协议版本。
        model_version: 产生该消息的算法模型版本。
        timestamp: 消息生成时间（Unix 秒）。
        message_id: 唯一标识，用于去重与请求-响应配对。
        causality_id: 因果链 id，同一次交互的多个消息共享。
        source: 消息来源标识（如 "emowave-core", "amiya-agent", "ui"）。
    """

    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    protocol_version: int = PROTOCOL_VERSION
    model_version: str = MODEL_VERSION
    timestamp: float = field(default_factory=time.time)
    message_id: str = ""
    causality_id: Optional[str] = None
    source: str = "emowave-core"

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("Envelope.kind 不能为空")
        if self.timestamp <= 0:
            raise ValueError(
                f"Envelope.timestamp 必须为正，得到 {self.timestamp}"
            )
        if not self.message_id:
            mid = f"msg_{int(self.timestamp * 1_000_000)}_{self.kind}"
            object.__setattr__(self, "message_id", mid)

    def is_compatible(self, other_schema: int, other_protocol: int) -> bool:
        """检查与另一端的版本兼容性。

        规则（REFACTOR_PLAN.md §18 Graceful Degradation）：
          - schema_version 必须完全相等（数据结构不兼容直接拒绝）
          - protocol_version 允许对端更低（我们向下兼容）
        """
        if other_schema != self.schema_version:
            return False
        if other_protocol > self.protocol_version:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Envelope":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


# ============================================================
# Amiya Handshake / Capability Discovery
# （REFACTOR_PLAN.md §15）
# ============================================================


class AmiyaCapability(str, Enum):
    """Amiya Agent 可提供的能力。"""

    EMOTION_CONTEXT = "emotion_context"      # 接受 EmoWave 的状态作为对话上下文
    CONVERSATION = "conversation"            # 提供对话能力
    RECOMMENDATION = "recommendation"        # 提供应对策略推荐
    VOICE = "voice"                          # 语音合成 / 识别
    MEMORY = "memory"                        # 长期记忆读写
    KNOWLEDGE = "knowledge"                  # RAG 知识库


@dataclass(frozen=True)
class AmiyaHandshake:
    """Amiya Agent 握手消息。

    连接流程（REFACTOR_PLAN.md §15）：

        EmoWave Start
             ↓
        Detect Amiya
             ↓
        No → Standalone
        Yes
             ↓
        Handshake
             ↓
        Capability Discovery
             ↓
        Enable Optional Features

    Attributes:
        agent: Agent 名称，固定 "amiya"（未来可能扩展）。
        version: Agent 版本字符串（如 "1.2.0"）。
        capabilities: Agent 声明的能力列表。
        protocol_version: Agent 支持的 EmoWave 协议版本。
        schema_version: Agent 支持的 EmoWave schema 版本。
        available: Agent 是否可用（False 表示检测到但当前不可用）。
        detected_at: 检测到 Agent 的时间戳。
    """

    agent: str = "amiya"
    version: str = ""
    capabilities: List[AmiyaCapability] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION
    schema_version: int = SCHEMA_VERSION
    available: bool = True
    detected_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        # 字符串列表 → Enum 列表
        normalized: List[AmiyaCapability] = []
        for c in self.capabilities:
            if isinstance(c, AmiyaCapability):
                normalized.append(c)
            else:
                try:
                    normalized.append(AmiyaCapability(c))
                except ValueError:
                    # 未知能力：忽略（前向兼容，新能力老 EmoWave 不认识很正常）
                    continue
        object.__setattr__(self, "capabilities", normalized)

    def supports(self, cap: AmiyaCapability) -> bool:
        """Agent 是否声明支持某能力。"""
        return cap in self.capabilities

    def is_compatible(self) -> bool:
        """版本是否兼容（schema 必须相等，protocol 允许对端更低）。"""
        return (
            self.schema_version == SCHEMA_VERSION
            and self.protocol_version <= PROTOCOL_VERSION
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["capabilities"] = [c.value for c in self.capabilities]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AmiyaHandshake":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


# ============================================================
# EmoWave → Amiya 输出协议
# （REFACTOR_PLAN.md §16）
# ============================================================


@dataclass(frozen=True)
class EmotionStateOutput:
    """EmoWave 输出给 Amiya 的"结论性状态"。

    只输出结论，不输出内部实现细节。Amiya 不需要知道：
      - Kalman Filter 参数
      - 数据库表结构
      - Online Regression 实现
      - 内部缓存

    这样协议长期稳定，Core 可以自由演进。

    Attributes:
        timestamp: 状态时刻。
        valence: 效价 [0, 1]。
        arousal: 唤醒 [0, 1]。
        intensity: 强度 [0, 1]。
        baseline_valence: 当前基线效价。
        baseline_arousal: 当前基线唤醒。
        trend: "rising" | "falling" | "stable" | "unknown"。
        confidence: 置信度 [0, 1]。
        emotion_label: 可选的 4 状态离散标签（Amiya 现有 core/emotion.py 的
            EMOTION_KEYS 兼容：calm/thinking/worried/happy）。
            映射规则详见 ARCHITECTURE_LIGHTWEIGHTpart2.md §3.4。
    """

    timestamp: float
    valence: float
    arousal: float
    intensity: float
    baseline_valence: float = 0.55
    baseline_arousal: float = 0.42
    trend: str = "unknown"
    confidence: float = 0.5
    emotion_label: Optional[str] = None

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            raise ValueError(
                f"EmotionStateOutput.timestamp 必须为正，得到 {self.timestamp}"
            )
        for name in ("valence", "arousal", "intensity", "confidence"):
            v = _clip(getattr(self, name))
            object.__setattr__(self, name, v)
        if self.trend not in ("rising", "falling", "stable", "unknown"):
            object.__setattr__(self, "trend", "unknown")

    def to_envelope(self) -> Envelope:
        """封装为 Envelope，用于跨进程传输。"""
        return Envelope(kind="emowave.emotion_state", payload=asdict(self))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EmotionStateOutput":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


# ============================================================
# Amiya → EmoWave 输入协议
# （REFACTOR_PLAN.md §17）
# ============================================================


class AmiyaInputType(str, Enum):
    """Amiya 可提供的输入类型。"""

    USER_FEEDBACK = "user_feedback"                    # 用户对 Amiya 回答的反馈
    CONVERSATION_CONTEXT = "conversation_context"      # 对话上下文摘要
    EXPLICIT_EMOTION_STATEMENT = "explicit_emotion_statement"  # 用户显式情绪陈述
    MANUAL_INTERVENTION = "manual_intervention"        # 用户主动干预（如"我现在很焦虑"）


@dataclass(frozen=True)
class AmiyaInput:
    """Amiya 提供给 EmoWave 的输入。

    Amiya 可以提供上下文，但**不能直接覆盖** EmoWave 的状态
    （REFACTOR_PLAN.md §17）。EmoWave 把 AmiyaInput 视为一种 Observation
    来源（source=agent），走正常的状态估计流程。

    Attributes:
        input_type: 输入类型。
        timestamp: 输入对应的时间。
        valence: 推断的效价 [0, 1]（可选）。
        arousal: 推断的唤醒 [0, 1]（可选）。
        confidence: Amiya 对自身推断的置信度 [0, 1]。
        source: "user" | "agent_inferred"。source=user 的显式输入优先级最高。
        text: 原始文本（如用户说的话），可选。
        meta: 附加元信息。
    """

    input_type: AmiyaInputType
    timestamp: float
    valence: Optional[float] = None
    arousal: Optional[float] = None
    confidence: float = 0.5
    source: str = "agent_inferred"
    text: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            raise ValueError(
                f"AmiyaInput.timestamp 必须为正，得到 {self.timestamp}"
            )
        if not isinstance(self.input_type, AmiyaInputType):
            object.__setattr__(self, "input_type", AmiyaInputType(self.input_type))
        object.__setattr__(self, "valence", _clip_opt(self.valence))
        object.__setattr__(self, "arousal", _clip_opt(self.arousal))
        object.__setattr__(self, "confidence", _clip(self.confidence))
        if self.source not in ("user", "agent_inferred"):
            object.__setattr__(self, "source", "agent_inferred")

    @property
    def is_user_explicit(self) -> bool:
        """是否为 source=user 的显式输入（优先级最高）。"""
        return self.source == "user"

    def to_observation_dict(self) -> Dict[str, Any]:
        """转换为 Observation 兼容 dict（source=agent）。

        EmoWave 接收 AmiyaInput 后，走正常 Observation 流程，
        不直接覆盖状态（REFACTOR_PLAN.md §17）。
        """
        return {
            "timestamp": self.timestamp,
            "valence": self.valence,
            "arousal": self.arousal,
            "source": "agent",
            # Amiya 的 confidence 与 source=user 加成后作为 Observation.confidence
            "confidence": self.confidence if not self.is_user_explicit else 1.0,
            "meta": {
                "amiya_input_type": self.input_type.value,
                "amiya_source": self.source,
                "text": self.text,
                **self.meta,
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["input_type"] = self.input_type.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AmiyaInput":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


# ============================================================
# 辅助函数
# ============================================================


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _clip_opt(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    return _clip(x)
