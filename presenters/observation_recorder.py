"""presenters/observation_recorder.py — 决定"什么算一次真实自评"

=== 它解决的问题 ===
v4 之前，记录期间 1Hz 定时器每秒读一次滑条并 append 一条 Observation。
用户只把滑条拖到 0.30、之后十分钟没碰，系统就生成 600 条"独立观察"，
取值完全一样。模型会把它当成 600 次独立证据，观测噪声、sample_count、
RTS 权重全部被污染（Phase 0 审计 §3-A 实测：10 次心跳 = 10 条同值记录）。

=== 规则 ===
1. 滑条连续拖动 → 防抖（默认 350ms），**只提交一次**最终值；
2. 值与上次已提交的相同 → 不提交（没有新信息）；
3. 用户显式「记一笔」→ 强制提交（即使值没变，那也是一次明确确认）；
4. 不在记录状态 → 一律不提交。

=== 为什么用"一个可重启的 QTimer"而不是每次 singleShot ===
每次事件都 `QTimer.singleShot` 会堆积大量定时器（检索台账 #2 的反例）。
这里只持有一个单发定时器，每次 `submit` 只是 `start()` 重启它。
"""

import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

#: 拖动停止后多久算"这次输入结束了"。300–500ms 是人手拖动的自然停顿区间。
DEBOUNCE_MS = 350


class ObservationRecorder(QObject):
    """把连续的滑条输入收敛成离散的"真实自评"。"""

    #: 一次自评被确认：(valence, arousal)
    committed = pyqtSignal(float, float)

    def __init__(self, clock=None, debounce_ms: int = DEBOUNCE_MS, parent=None):
        super().__init__(parent)
        self._clock = clock or time.time
        self._debounce_ms = int(debounce_ms)
        self._enabled = False
        self._pending = None            # (v, a) 待提交
        self._last_committed = None     # (v, a) 上次已提交

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    # ------------------------------------------------------------
    # 开关（跟随 recording 状态）
    # ------------------------------------------------------------
    def set_enabled(self, enabled: bool):
        """进入/退出记录状态。退出时丢弃未提交的输入。"""
        self._enabled = bool(enabled)
        if not self._enabled:
            self._timer.stop()
            self._pending = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    # ------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------
    def submit(self, valence: float, arousal: float):
        """收到一次滑条变化：记为待提交，并重启防抖计时。"""
        if not self._enabled:
            return
        self._pending = (float(valence), float(arousal))
        self._timer.start(self._debounce_ms)

    def flush(self, force: bool = False):
        """立即结算待提交值。

        Args:
            force: True 表示用户显式「记一笔」，即使与上次相同也提交。
        """
        self._timer.stop()
        pending, self._pending = self._pending, None
        if pending is None or not self._enabled:
            return
        if not force and self._last_committed == pending:
            return
        self._last_committed = pending
        self.committed.emit(pending[0], pending[1])

    def _on_timeout(self):
        self.flush()

    def reset(self):
        """清空历史（换人群/结束一波时调用，避免"新会话沿用旧值去重"）。"""
        self._timer.stop()
        self._pending = None
        self._last_committed = None
