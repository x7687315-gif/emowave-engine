"""windows/event_summary_window.py — 事件回顾窗口

展示单次情绪事件的概况、情绪曲线与躯体症状。
"""
import json

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

from widgets import CardFrame, COLORS


class EventSummaryWindow(QWidget):
    """事件回顾窗口：回顾单次情绪事件的关键信息。"""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._current_event = None
        self._setup_ui()

    # ----------------------------------------------------------------
    # UI 构建
    # ----------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("事件回顾")
        title.setStyleSheet(
            f"color: {COLORS['ink']}; font-size: 20px; font-weight: bold;"
        )
        layout.addWidget(title)

        # 事件概况卡片
        profile_card = CardFrame("事件概况")
        self.profile_label = QLabel("请选择一个事件查看概况")
        self.profile_label.setWordWrap(True)
        self.profile_label.setTextFormat(Qt.PlainText)
        self.profile_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 13px;"
        )
        profile_card.add_widget(self.profile_label)
        layout.addWidget(profile_card)

        # 情绪曲线卡片
        curve_card = CardFrame("情绪曲线")
        self.curve_canvas = self._CurveCanvas()
        curve_card.add_widget(self.curve_canvas)
        layout.addWidget(curve_card)

        # 躯体症状卡片
        body_card = CardFrame("躯体症状")
        self.body_label = QLabel("暂无躯体症状记录")
        self.body_label.setWordWrap(True)
        self.body_label.setTextFormat(Qt.PlainText)
        self.body_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 13px;"
        )
        body_card.add_widget(self.body_label)
        layout.addWidget(body_card)

        layout.addStretch(1)

    # ----------------------------------------------------------------
    # 业务逻辑
    # ----------------------------------------------------------------
    def show_event(self, event_id):
        """根据 event_id 从最近 50 条事件中查找并展示。

        若未找到，安全地给出提示而不抛异常。
        """
        events = self.session.db.get_recent_events(limit=50)
        event = None
        for e in events:
            if e.get('event_id') == event_id:
                event = e
                break

        if event is None:
            self._current_event = None
            self.profile_label.setText(f"未找到事件：{event_id}")
            self.body_label.setText("暂无躯体症状记录")
            self.curve_canvas.set_data([])
            return

        self._current_event = event
        self._render_event(event)

    def _render_event(self, event):
        # JSON 字段统一用 json.loads 解析
        trigger_tags = self._parse_json(event.get('trigger_tags'), [])
        body_symptoms = self._parse_json(event.get('body_symptoms'), [])

        profile_text = (
            f"事件 ID：{event.get('event_id', '-')}\n"
            f"峰值唤醒度：{self._fmt(event.get('peak_arousal'))}\n"
            f"峰值效价：{self._fmt(event.get('peak_valence'))}\n"
            f"采样点数：{event.get('sample_count', 0)}\n"
            f"触发因素：{('、'.join(trigger_tags) if trigger_tags else '未记录')}\n"
            f"自评峰值：{self._fmt(event.get('user_peak_rating'))}"
        )
        self.profile_label.setText(profile_text)

        self.body_label.setText(
            '、'.join(body_symptoms) if body_symptoms else '暂无躯体症状记录'
        )

        # 曲线使用简化数据：基于峰值唤醒度生成一段示意折线
        peak_arousal = self._to_float(event.get('peak_arousal'), 0.0)
        envelope = [0.25, 0.55, 0.85, 1.0, 0.85, 0.55, 0.25]
        self.curve_canvas.set_data(
            [max(0.0, min(1.0, peak_arousal * f)) for f in envelope]
        )

    # ----------------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------------
    @staticmethod
    def _parse_json(value, default):
        """安全解析 JSON 字段；已是 list/dict 时直接返回。"""
        if value is None:
            return default
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _to_float(value, default=0.0):
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def _fmt(cls, value):
        if value is None:
            return '-'
        f = cls._to_float(value, None)
        if f is None:
            return str(value)
        return f"{f:.2f}"

    # ================================================================
    # 内部类：情绪曲线画布
    # ================================================================
    class _CurveCanvas(QWidget):
        """根据采样点绘制折线的情绪曲线画布。"""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.points = []
            self.setMinimumHeight(180)

        def set_data(self, points):
            self.points = [float(p) for p in (points or [])]
            self.update()

        def paintEvent(self, event):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()

            # 背景
            p.fillRect(self.rect(), QColor(COLORS['surface']))

            # 中线网格
            p.setPen(QPen(QColor(COLORS['rule']), 1))
            p.drawLine(0, h // 2, w, h // 2)

            # 轴标签
            p.setPen(QColor(COLORS['muted']))
            p.setFont(QFont("PingFang SC", 9))
            p.drawText(4, 14, "↑唤醒度")

            n = len(self.points)
            if n < 2:
                # 单点时画一个圆点
                if n == 1:
                    x, y = w // 2, int((1 - self.points[0]) * h)
                    p.setBrush(QColor(COLORS['danger']))
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(x - 5, y - 5, 10, 10)
                return

            # 折线
            p.setPen(
                QPen(QColor(COLORS['danger']), 2, Qt.SolidLine, Qt.RoundCap)
            )
            for i in range(1, n):
                x1 = int((i - 1) / (n - 1) * w)
                y1 = int((1 - self.points[i - 1]) * h)
                x2 = int(i / (n - 1) * w)
                y2 = int((1 - self.points[i]) * h)
                p.drawLine(x1, y1, x2, y2)

            # 末端高亮点
            x_last = w - 1
            y_last = int((1 - self.points[-1]) * h)
            p.setBrush(QColor(COLORS['danger']))
            p.setPen(Qt.NoPen)
            p.drawEllipse(x_last - 5, y_last - 5, 10, 10)
