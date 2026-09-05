"""Agent adapters — 与外部 Agent（Amiya 等）的集成。"""

from emowave.adapters.agent.amiya import (
    EMOTION_KEYS,
    EmotionBridge,
    map_state_to_amiya_key,
    rule_based_fallback,
)

__all__ = [
    "EMOTION_KEYS",
    "EmotionBridge",
    "map_state_to_amiya_key",
    "rule_based_fallback",
]
