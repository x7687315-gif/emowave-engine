"""presenters/forecast_logger.py — 因果预测快照日志（v5 Phase 6）

=== 为什么需要它 ===
“模型准不准”不能靠看曲线手感回答。要评估实时预测，必须在**观察发生之前**
把当时的预测记下来，之后再用真实自评对照；不能用 RTS 平滑后的曲线反推
“当时应该预测成什么”（那是未来信息泄漏）。

=== 记录什么 ===
每次记录一个 base_ts（模型当前时刻）和一组 horizon（5m/15m/30m/1h/4h/24h）
的预测值/方差。评估工具按 horizon 分组算 MAE/RMSE。
只写入 forecast_log；**绝不进入 observations，也不回灌模型**。
"""

import logging

from .engine import predict_forward

logger = logging.getLogger(__name__)

#: 评估用的预测视界（秒）：5m / 15m / 30m / 1h / 4h / 24h
DEFAULT_HORIZONS = (300.0, 900.0, 1800.0, 3600.0, 14400.0, 86400.0)
#: 轨迹采样步长；评估不需要秒级颗粒度，300s 足够 5m/15m/30m 视界，
#: 又避免每次观察都做 1440 步（实测会让一次 sample 从 ~10ms 涨到 ~40ms）
FORECAST_DT = 300.0
#: 衰减/刷新路径的最小写库间隔；新观察（force）不受此限
MIN_INTERVAL_SEC = 60.0


class ForecastLogger:
    """把因果预测写入 db.forecast_log；db 为 None 时全部 no-op。"""

    def __init__(self, db, horizons=None, min_interval_sec=MIN_INTERVAL_SEC):
        self.db = db
        self.horizons = tuple(sorted(float(h) for h in (horizons or DEFAULT_HORIZONS)))
        self.min_interval_sec = float(min_interval_sec)
        self._last_base_ts = None

    def log(self, estimator, params, baseline, source="refresh", force=False):
        """记录一组预测；返回写入行数（0 = 跳过/失败）。

        Args:
            estimator: 已初始化的 StateEstimator（提供 state_vector/covariance）
            params: 当前 ModelParameters
            baseline: 当前 Baseline（GP 均值函数）
            source: sample / decay / refresh / resume / restore
            force: True 时忽略节流（新观察必须留一份）
        """
        if (self.db is None or estimator is None
                or not getattr(estimator, "is_initialized", False)):
            return 0
        base = float(getattr(estimator, "last_timestamp", 0.0) or 0.0)
        if base <= 0:
            return 0
        if self._last_base_ts is not None:
            if base == self._last_base_ts:
                return 0
            if not force and base - self._last_base_ts < self.min_interval_sec:
                return 0

        dt = min(FORECAST_DT, self.horizons[0])
        try:
            traj = predict_forward(
                state_vector=estimator.state_vector,
                covariance=estimator.covariance,
                params=params,
                horizon_sec=max(self.horizons),
                dt=dt,
                mean=(float(baseline.valence), float(baseline.arousal)),
            )
        except Exception as exc:
            logger.warning("预测日志计算失败：%s", exc)
            return 0
        if not traj:
            return 0

        rows = []
        for horizon in self.horizons:
            idx = min(len(traj) - 1, max(0, int(round(horizon / dt)) - 1))
            point = traj[idx]
            rows.append((
                base, base + horizon, horizon,
                point["valence"], point["arousal"],
                point["var_valence"], point["var_arousal"], source,
            ))
        try:
            written = self.db.save_forecasts(rows)
        except Exception as exc:
            logger.warning("预测日志写入失败：%s", exc)
            return 0
        self._last_base_ts = base
        return written
