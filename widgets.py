"""widgets.py — 心潮 EmoWave 共享自定义控件（禅意重构版）

设计语言：纸白底 + 灰绿/雾蓝 + 发丝线 + 朱红点睛。
克制、安静、留白有度——参考「山间呼吸 / 叶中一室 / 日式海报」的观感。

提供控件：
  - RiskRingWidget: 圆形风险进度环（细环 · 柔和色阶）
  - EmotionCanvas:  2D 效价-唤醒情绪平面画布（雾蓝轨迹 · 朱红当前点）
  - CardFrame:      发丝线卡片容器（去重装饰，只留必要层级）
  - StatBlock:      紧凑数据块（弱标签 + 轻数值）
  - hline():        发丝水平分隔线
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

# ================================================================
# 设计系统：色板（自然 muted，日式留白）
# ================================================================
COLORS = {
    'bg':       '#F5F3ED',   # 暖纸底
    'surface':  '#FBFAF6',   # 卡片近白
    'ink':      '#3A3730',   # 深炭（主文字）
    'ink_soft': '#6C675C',   # 次级文字
    'muted':    '#9C968A',   # 弱文字
    'rule':     '#E6E1D5',   # 发丝线
    'track':    '#ECE8DD',   # 环轨道
    'sage':     '#7D9070',   # 主色 · 灰绿（草坡）
    'sage_soft':'#EAF0E2',   # 灰绿浅底（选中/悬停）
    'mist':     '#8FA9BD',   # 雾蓝（山与天空，次级色）
    'mist_soft':'#E9EFF4',
    'amber':    '#D2A45F',   # 柔和琥珀（警示中间档）
    'danger':   '#C06B5C',   # 陶土红（高风险）
    'sun':      '#BC5548',   # 朱红一点（当前点/焦点，极小面积）
}


def app_font(size: int = 10, weight: int = QFont.Normal) -> QFont:
    """跨平台应用字体：Windows 用微软雅黑 UI。"""
    return QFont("Microsoft YaHei UI", size, weight)


def hline() -> QFrame:
    """1px 发丝水平分隔线。"""
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"background-color: {COLORS['rule']}; border: none;")
    line.setFixedHeight(1)
    return line


class RiskRingWidget(QWidget):
    """圆形风险进度环：细环 + 柔和三段色阶 + 轻数值。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setMinimumSize(112, 112)
        self.setMaximumSize(148, 148)

    def set_value(self, v):
        self.value = max(0.0, min(1.0, v))
        self.update()

    def _color_for_value(self):
        if self.value < 0.4:
            return QColor(COLORS['sage'])
        elif self.value < 0.7:
            return QColor(COLORS['amber'])
        return QColor(COLORS['danger'])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 9

        # 轨道细环
        p.setPen(QPen(QColor(COLORS['track']), 7, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 0, 360 * 16)

        # 进度弧
        if self.value > 0:
            p.setPen(QPen(self._color_for_value(), 7, Qt.SolidLine, Qt.RoundCap))
            span = int(-self.value * 360 * 16)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, 90 * 16, span)

        # 中心轻数值
        p.setPen(QColor(COLORS['ink']))
        font = app_font(20, QFont.Light)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignCenter, str(int(self.value * 100)))


class EmotionCanvas(QWidget):
    """2D 效价-唤醒情绪平面：发丝轴 + 雾蓝轨迹 + 朱红当前点。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.trail = []
        self.setMinimumSize(260, 210)

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

        # 纸面
        p.fillRect(self.rect(), QColor(COLORS['surface']))

        # 发丝中轴
        p.setPen(QPen(QColor(COLORS['rule']), 1))
        p.drawLine(w // 2, 0, w // 2, h)
        p.drawLine(0, h // 2, w, h // 2)

        # 轴标签
        p.setPen(QColor(COLORS['muted']))
        p.setFont(app_font(8))
        p.drawText(w - 42, h - 5, "效价 →")
        p.drawText(5, 14, "↑ 唤醒")

        # 轨迹：雾蓝，越新越实
        if len(self.trail) >= 2:
            for i in range(1, len(self.trail)):
                v1, a1 = self.trail[i - 1]
                v2, a2 = self.trail[i]
                x1, y1 = int(v1 * w), int((1 - a1) * h)
                x2, y2 = int(v2 * w), int((1 - a2) * h)
                alpha = int(90 + 150 * (i / len(self.trail)))
                p.setPen(QPen(QColor(143, 169, 189, alpha), 2,
                              Qt.SolidLine, Qt.RoundCap))
                p.drawLine(x1, y1, x2, y2)

        # 当前点：朱红（红日点睛）
        if self.trail:
            v, a = self.trail[-1]
            x, y = int(v * w), int((1 - a) * h)
            p.setBrush(QColor(COLORS['sun']))
            p.setPen(Qt.NoPen)
            p.drawEllipse(x - 5, y - 5, 10, 10)


class CardFrame(QFrame):
    """发丝线卡片：近白面 + 1px 细边 + 弱标题，紧凑内距。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"CardFrame {{ background-color: {COLORS['surface']};"
            f" border-radius: 8px; border: 1px solid {COLORS['rule']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 12px;"
            " letter-spacing: 1px;"
        )
        layout.addWidget(self.title_label)

        self._content_layout = layout

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)


class StatBlock(QWidget):
    """紧凑数据块：弱标签在上，轻数值在下（仪表盘概览用）。"""

    def __init__(self, label="", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.caption = QLabel(label)
        self.caption.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        self.caption.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.value_label = QLabel("—")
        self.value_label.setStyleSheet(
            f"color: {COLORS['ink']}; font-size: 18px; font-weight: 300;"
        )
        self.value_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        layout.addWidget(self.caption)
        layout.addWidget(self.value_label)

    def set_value(self, text: str):
        self.value_label.setText(text)
