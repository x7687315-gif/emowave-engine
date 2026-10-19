"""presenters/engine.py — 内核深路径导入的聚合层

`emowave/__init__.py` 是**有意的"Core 硬边界"**：只导出领域模型与协议 schema，
`StateEstimator / RTSSmoother / Calibrator / BaselineController / predict_forward`
这些实现类都必须从 `emowave.core.*.*` 深路径导入。UI/presenter 层如果到处写
`from emowave.core.inference.dynamics import predict_forward, predict_step, predict_step`，将来内核一挪目录
就要改十几处。

因此本文件做一次聚合：presenter 只 `from .engine import ...`，内核重命名/移动
时只改这一处。
"""
from emowave import (
    Observation, ObservationSource, ModelParameters, UserCorrection,
    CorrectionDimension, CorrectionSource,
    get_archetype, DEFAULT_ARCHETYPE_KEY,
)
from emowave.core.domain.baseline import Baseline, BaselineSource
from emowave.core.estimator.estimator import StateEstimator, EstimatorConfig
from emowave.core.curve.smoother import RTSSmoother
from emowave.core.calibration.baseline_control import BaselineController
from emowave.core.calibration.calibrator import Calibrator
from emowave.core.inference.dynamics import predict_forward, predict_step, predict_step
from emowave.core.inference.predictor import TrendPredictor

__all__ = [
    "Observation", "ObservationSource", "ModelParameters", "UserCorrection",
    "CorrectionDimension", "CorrectionSource",
    "get_archetype", "DEFAULT_ARCHETYPE_KEY",
    "Baseline", "BaselineSource",
    "StateEstimator", "EstimatorConfig",
    "RTSSmoother", "BaselineController", "Calibrator",
    "predict_forward", "predict_step", "TrendPredictor",
]
