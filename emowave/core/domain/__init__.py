"""Core Domain — EmoWave 的核心领域模型。

本模块回答"这个系统观察什么、估计什么、用户能纠正什么、基线是什么、
模型参数长什么样、发生过什么事件"这六个基本问题。

数据流（详见 REFACTOR_PLAN.md §33 最终架构）：

    Real World
      ↓
    Observation         观察到的事实（不可变）
      ↓
    State Estimator     基于观测 + 参数生成状态估计
      ↓
    EmotionState        模型此刻对用户状态的估计（含置信度）
      ↓
    User Feedback       用户查看曲线
      ↓
    UserCorrection      用户对模型的显式纠正（append-only）
      ↓
    Personal Calibration  基于纠正学习个人参数
      ↓
    ModelParameters     越来越像这个用户的参数集
      ↓
    Baseline            用户对"新正常"的定义
      ↓
    StateEvent          所有状态变化以事件流形式记录

设计原则（详见 REFACTOR_PLAN.md §13 数据存储原则）：
  - 原始数据不可变：Observation append-only
  - 模型结果可重算：EmotionState 可由 (Observation + ModelParameters) 重建
  - 用户修正永久保留：Correction 是训练个人模型的监督信号，不能丢
  - Schema 版本化：所有对象可 to_dict / from_dict，带 schema_version
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

__all__ = [
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
]
