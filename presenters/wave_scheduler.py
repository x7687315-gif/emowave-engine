"""presenters/wave_scheduler.py — 时间源与刷新节律

=== 为什么单独抽出来 ===
v4 之前，"时间"在三个地方各有一套：
  · `_decay_now += DECAY_DT`（假时间：按 tick 次数累加）
  · `_store_active_wave` 存 `end_ts`（真时间戳，但恢复时不读）
  · UI 文案里的 "1 Hz"（又是另一种时间口径）

结果是同一个"过去多久"，三个地方算出三个值。本类把"现在几点"收敛成**唯一入口**，
并允许注入 clock（测试里用假时钟，不必 sleep）。

=== 两种模式 ===
  · 普通查看：不排程，调用方按需 `now()` / `horizon()`
  · 悬浮窗常驻：Phase 4 会用 `is_due()` 驱动约 5 分钟的刷新

本阶段只提供时间源与节律判定，**不启动任何定时器**——定时器仍由 presenter 持有，
避免"调度逻辑"和"Qt 对象生命周期"再次混在一起。
"""

import time


class WaveScheduler:
    """时间源 + 刷新节律。纯 Python，可脱离 Qt 单测。"""

    #: 悬浮窗常驻时的刷新间隔（秒）。Phase 4 使用。
    REFRESH_INTERVAL_SEC = 300.0

    def __init__(self, clock=None):
        #: 可注入的时钟（测试用假时钟）；默认墙钟
        self._clock = clock or time.time

    def now(self) -> float:
        """当前 Unix 时间戳（秒）。**全应用唯一的"现在"。**"""
        return float(self._clock())

    def horizon(self, since_ts: float) -> float:
        """从 `since_ts` 到现在过了多久（秒，非负）。"""
        return max(0.0, self.now() - float(since_ts))

    def is_due(self, last_refresh_ts: float, interval_sec: float = None) -> bool:
        """距上次刷新是否已到间隔。"""
        interval = self.REFRESH_INTERVAL_SEC if interval_sec is None else interval_sec
        return self.horizon(last_refresh_ts) >= interval
