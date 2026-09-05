"""Estimator — 实时情绪状态估计器。"""

from emowave.core.estimator.matern import (
    matern_transition,
    matern_stationary_covariance,
    matern_process_noise,
    build_transition,
    build_stationary_covariance,
    build_process_noise,
    OBSERVATION_MATRIX,
)
from emowave.core.estimator.estimator import (
    StateEstimator,
    EstimatorConfig,
    compute_observation_noise,
)

__all__ = [
    "matern_transition",
    "matern_stationary_covariance",
    "matern_process_noise",
    "build_transition",
    "build_stationary_covariance",
    "build_process_noise",
    "OBSERVATION_MATRIX",
    "StateEstimator",
    "EstimatorConfig",
    "compute_observation_noise",
]
