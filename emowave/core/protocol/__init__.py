"""Core Protocol — EmoWave 对外协议与版本 schema。"""

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

__all__ = [
    "SCHEMA_VERSION",
    "PROTOCOL_VERSION",
    "MODEL_VERSION",
    "Envelope",
    "AmiyaCapability",
    "AmiyaHandshake",
    "EmotionStateOutput",
    "AmiyaInput",
    "AmiyaInputType",
]
