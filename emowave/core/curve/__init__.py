"""Curve — L2 回顾层：情绪曲线建模与人机协同编辑。"""

from emowave.core.curve.smoother import RTSSmoother, SmoothedTrajectory
from emowave.core.curve.curve import (
    EmotionCurve,
    CurveEdit,
    CurveChannel,
    CurveEditor,
    build_node_grid,
)

__all__ = [
    "RTSSmoother",
    "SmoothedTrajectory",
    "EmotionCurve",
    "CurveEdit",
    "CurveChannel",
    "CurveEditor",
    "build_node_grid",
]
