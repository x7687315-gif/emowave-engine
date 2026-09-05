"""EmoWave / 心潮 — 个人情绪状态引擎。

EmoWave 2.0 的可移植内核。零第三方运行时依赖（只用 Python 标准库），
可被桌面 / 移动 / Linux / Amiya 插件等多平台复用。

包结构（详见 REFACTOR_PLAN.md §11 与 §29）：

    emowave/
      core/
        domain/       核心领域模型：Observation / EmotionState / UserCorrection /
                      Baseline / ModelParameters / StateEvent
        estimator/    状态估计器（Phase 2 起）
        calibration/  个人校准与在线学习（Phase 4 起）
        protocol/     协议与版本 schema
      adapters/       存储 / 传感器 / Agent 适配层（Phase 8 起）
      apps/           桌面 / Linux / 移动应用（Phase 9 起）

设计原则：
  - Core 硬边界：不知道 PyQt / Flet / SQLite / Amiya / LLM 的存在
  - 原始数据不可变：Observation append-only，用户修正独立记录
  - Schema 版本化：所有跨进程 / 跨语言传输的数据带版本号
  - 低配置优先：Kalman / EWMA / Online Regression，不用 GPU 也不用 ML 大框架
"""

from emowave.core.domain.observation import Observation, ObservationSource
from emowave.core.domain.emotion_state import EmotionState, Trend
from emowave.core.domain.correction import (
    UserCorrection,
    CorrectionSource,
    CorrectionDimension,
)
from emowave.core.domain.baseline import Baseline, BaselineShiftEvent
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.events import StateEvent, StateEventType, EventStream
from emowave.core.protocol.schemas import (
    SCHEMA_VERSION,
    PROTOCOL_VERSION,
    MODEL_VERSION,
    Envelope,
)

__version__ = "2.0.0-alpha.0"

__all__ = [
    # 版本
    "__version__",
    "SCHEMA_VERSION",
    "PROTOCOL_VERSION",
    "MODEL_VERSION",
    # 领域模型
    "Observation",
    "ObservationSource",
    "EmotionState",
    "Trend",
    "UserCorrection",
    "CorrectionSource",
    "CorrectionDimension",
    "Baseline",
    "BaselineShiftEvent",
    "ModelParameters",
    "StateEvent",
    "StateEventType",
    "EventStream",
    # 协议
    "Envelope",
]
