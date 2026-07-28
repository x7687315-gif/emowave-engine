"""widgets.py — 心潮 EmoWave 共享自定义控件

提供三个核心控件：
  - RiskRingWidget: 圆形风险进度环
  - EmotionCanvas:  2D 效价-唤醒情绪平面画布
  - CardFrame:      带标题的卡片容器
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

# ================================================================
# 设计系统：统一色板
# ================================================================
COLORS = {
    'bg': '#f6f4f0', 'surface': '#ffffff',
    'ink': '#2d2a26', 'ink_soft': '#5a5550', 'muted': '#9a958e',
    'rule': '#e0dbd4', 'shadow': '#d5d0c8',
    'calm': '#2cb69a', 'warm': '#e8a838', 'warn': '#e06060', 'danger': '#c84848',
    'ice': '#5a9fc8', 'indigo': '#7a7ec8',
}


class RiskRingWidget(QWidget):
    """圆形风险进度环，0-1 映射为 0-360 度弧线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setMinimumSize(160, 160)
        self.setMaximumSize(200, 200)

    def set_value(self, v):
        self.value = max(0.0, min(1.0, v))
        self.update()

    def _color_for_value(self):
        if self.value < 0.4:
            return QColor(COLORS['calm'])
        elif self.value < 0.7:
            return QColor(COLORS['warm'])
        else:
            return QColor(COLORS['danger'])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 14

        # 背景环
        p.setPen(QPen(QColor(COLORS['rule']), 10, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 0, 360 * 16)

        # 进度环
        if self.value > 0:
            p.setPen(QPen(self._color_for_value(), 10, Qt.SolidLine, Qt.RoundCap))
            span = int(-self.value * 360 * 16)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, 90 * 16, span)

        # 中心文字
        p.setPen(QColor(COLORS['ink']))
        font = QFont("PingFang SC", 18, QFont.Bold)
        p.setFont(font)
        text = f"{int(self.value * 100)}"
        p.drawText(self.rect(), Qt.AlignCenter, text)


class EmotionCanvas(QWidget):
    """2D 效价-唤醒情绪平面画布，支持实时轨迹绘制。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.trail = []
        self.setMinimumSize(300, 300)

    def add_point(self, valence, arousal):
        self.trail.append((valence, arousal))
        if len(self.trail) > 500:
            self.trail = self.trail[-500:]
        self.update()

    def clear(self):
        self.trail = []
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 背景
        p.fillRect(self.rect(), QColor(COLORS['surface']))

        # 网格线
        p.setPen(QPen(QColor(COLORS['rule']), 1))
        p.drawLine(w // 2, 0, w // 2, h)
        p.drawLine(0, h // 2, w, h // 2)

        # 轴标签
        p.setPen(QColor(COLORS['muted']))
        p.setFont(QFont("PingFang SC", 9))
        p.drawText(w - 40, h - 5, "效价→")
        p.drawText(5, 15, "↑唤醒")

        # 轨迹
        if len(self.trail) >= 2:
            for i in range(1, len(self.trail)):
                v1, a1 = self.trail[i - 1]
                v2, a2 = self.trail[i]
                x1, y1 = int(v1 * w), int((1 - a1) * h)
                x2, y2 = int(v2 * w), int((1 - a2) * h)
                alpha = int(255 * (i / len(self.trail)))
                p.setPen(QPen(QColor(224, 96, 96, alpha), 3, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(x1, y1, x2, y2)

        # 当前点
        if self.trail:
            v, a = self.trail[-1]
            x, y = int(v * w), int((1 - a) * h)
            p.setBrush(QColor(COLORS['danger']))
            p.setPen(Qt.NoPen)
            p.drawEllipse(x - 8, y - 8, 16, 16)


class CardFrame(QFrame):
    """带标题的卡片容器，统一视觉风格。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"""
            CardFrame {{
                background-color: {COLORS['surface']};
                border-radius: 12px;
                border: 1px solid {COLORS['rule']};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {COLORS['ink']}; font-size: 14px; font-weight: bold;"
        )
        layout.addWidget(self.title_label)

        self._content_layout = layout

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)
