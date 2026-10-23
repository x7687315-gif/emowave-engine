"""windows/history_window.py — 历史页：按天分组的事件清单

旧版是「日历 + 表格 + 卡片」。新版按同一套语言重排：
  · 一天一组：日期（今天/昨天/月日）+ 当天自评次数
  · 一行一段记录：时间区间 · 迷你峰值尺 · 峰值 · 偏离文字 · 自评次数
  · 点一行 → 上方展开该事件的回顾（复用 EventSummaryWindow 与详情弹窗）
  · 没有卡片、没有表格网格，分隔只有 1px 线

导出仍在：把最近事件写成 JSON。
"""
import json

from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PyQt5.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from theme import COLORS, app_font, app_font_num


def _rule():
    ln = QFrame()
    ln.setFixedHeight(1)
    ln.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
    return ln


def _fmt_clock(ts):
    import datetime as dt
    if not ts:
        return "—"
    return dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M")


class _PeakBar(QWidget):
    """迷你峰值尺：0–100 的一条细轨 + 峰值位置（不声称与常态的差）。"""

    def __init__(self, peak, parent=None):
        super().__init__(parent)
        self.peak = max(0.0, min(1.0, float(peak or 0.0)))
        self.setFixedSize(18, 36)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        x, y0, y1 = 6, 4, self.height() - 4
        p.setPen(QPen(QColor(COLORS['rule']), 1))
        p.drawLine(QPointF(x, y0), QPointF(x, y1))
        y = y1 - self.peak * (y1 - y0)
        pen = QPen(QColor(COLORS['accent']), 2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x, y1), QPointF(x, y))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS['accent']))
        p.drawEllipse(QPointF(x, y), 2.6, 2.6)


class _EventRow(QWidget):
    clicked = pyqtSignal(dict)

    def __init__(self, event, parent=None):
        super().__init__(parent)
        self.event = event
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(46)      # 不给下限，布局会把行压扁到看不清
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(12)

        t = QLabel("%s–%s" % (_fmt_clock(event.get('start_time')),
                              _fmt_clock(event.get('end_time'))))
        t.setFont(app_font_num(10, QFont.Light))
        t.setStyleSheet(f"color: {COLORS['muted']};")
        from PyQt5.QtGui import QFontMetrics
        t.setFixedWidth(
            QFontMetrics(t.font()).horizontalAdvance("00:00–00:00") + 12)
        lay.addWidget(t)

        peak = float(event.get('peak_valence') or 0.0)
        lay.addWidget(_PeakBar(peak))

        val = QLabel("%d" % round(peak * 100))
        val.setFont(app_font_num(15, QFont.Light))
        val.setStyleSheet(f"color: {COLORS['ink']};")
        lay.addWidget(val)

        arousal = QLabel("唤醒 %d" % round(float(event.get('peak_arousal') or 0) * 100))
        arousal.setFont(app_font(8))
        arousal.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(arousal)

        lay.addStretch(1)

        n = QLabel("%d 次" % int(event.get('sample_count') or 0))
        n.setFont(app_font(9))
        n.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(n)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.event)
        super().mouseReleaseEvent(event)


class HistoryWindow(QWidget):
    """按天分组的事件清单。`event_activated` 交给外层打开事件回顾。"""

    event_activated = pyqtSignal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._rows = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget"
            " { background: transparent; border: none; }")
        self._content = QWidget()
        self._lay = QVBoxLayout(self._content)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 8, 0, 0)
        foot.addStretch(1)
        self.export_btn = QPushButton("导出 JSON")
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.setFlat(True)
        self.export_btn.setFocusPolicy(Qt.NoFocus)
        self.export_btn.setFont(app_font(9))
        self.export_btn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent;"
            f" color: {COLORS['muted']}; padding: 0; }}"
            f"QPushButton:hover {{ color: {COLORS['accent_ink']}; }}")
        self.export_btn.clicked.connect(self._export)
        foot.addWidget(self.export_btn)
        outer.addLayout(foot)

        self.refresh()

    # ------------------------------------------------------------
    def refresh(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)      # 立刻消失；deleteLater 之前还会画一帧
                w.deleteLater()

        db = getattr(self.session, "db", None) if self.session else None
        events = db.get_recent_events(limit=50) if db is not None else []
        self._rows = list(events)

        if not events:
            empty = QLabel("还没有记录 · 点「开始记录」，或在主屏按 Ctrl+R")
            empty.setFont(app_font(10))
            empty.setStyleSheet(f"color: {COLORS['muted']};")
            self._lay.addWidget(empty)
            self._lay.addStretch(1)
            return

        groups, order = {}, []
        for ev in events:
            day = (ev.get('created_at') or '')[:10]
            if day not in groups:
                groups[day] = []
                order.append(day)
            groups[day].append(ev)

        for idx, day in enumerate(order):
            if idx:
                self._lay.addSpacing(18)
            head = QWidget()
            head.setFixedHeight(26)
            hl = QHBoxLayout(head)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            title = QLabel(self._day_label(day))
            title.setFont(app_font(10, QFont.Medium))
            title.setStyleSheet(f"color: {COLORS['ink_2']};")
            hl.addWidget(title)
            hl.addStretch(1)
            total = sum(int(e.get('sample_count') or 0) for e in groups[day])
            n = QLabel("%d 次自评" % total)
            n.setFont(app_font(9))
            n.setStyleSheet(f"color: {COLORS['muted']};")
            hl.addWidget(n)
            self._lay.addWidget(head)
            self._lay.addSpacing(4)
            self._lay.addWidget(_rule())

            for ev in groups[day]:
                row = _EventRow(ev)
                row.clicked.connect(self._on_event)
                self._lay.addWidget(row)
                self._lay.addWidget(_rule())

        self._lay.addStretch(1)

    # ------------------------------------------------------------
    @staticmethod
    def _day_label(day):
        import datetime as dt
        today = dt.date.today()
        try:
            d = dt.datetime.strptime(day, "%Y-%m-%d").date()
        except Exception:
            return day or "—"
        if d == today:
            return "今天"
        if d == today - dt.timedelta(days=1):
            return "昨天"
        return "%d 月 %d 日" % (d.month, d.day)

    def _on_event(self, event):
        event_id = event.get('event_id')
        if event_id:
            self.event_activated.emit(event_id)

    def _export(self):
        db = getattr(self.session, "db", None) if self.session else None
        if db is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出数据", "", "JSON (*.json)")
        if not path:
            return
        events = db.get_recent_events(limit=10000)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        QMessageBox.information(self, "导出成功",
                                "已导出 %d 条记录" % len(events))
