"""widgets.py — 心潮 EmoWave 共享自定义控件（精密仪器版）

设计语言：EmoWave = 精密情绪科学仪器（Linear × Raycast × macOS × Vercel × Arc）。
暖白底 / 白面 / 近黑字 / 中性灰次级 / 极细灰边 / 克制蓝主色 / 淡青次色。
颜色只用于传达状态或交互，不用于装饰。曲线是视觉中心。
不做：心理健康 App 审美、AI Dashboard 卡片堆、渐变/玻璃/大圆角/emoji/彩虹色。

提供控件：
  - RiskRingWidget: 状态环（细环 · 状态色：蓝/琥珀/砖红）
  - EmotionCanvas:  2D 效价-唤醒平面（蓝轨迹 · 蓝当前点）
  - EmotionCurveWidget: 时间序列曲线（模型线+置信带+基线+原始点）
  - CardFrame:      极细边分组容器
  - StatBlock:      仪器数据块（极小大写标签 + 等宽大数值）
  - hline():        发丝水平分隔线
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

# ================================================================
# 设计系统：精密仪器色板（Linear × Raycast × macOS × Vercel × Arc）
#   颜色只用于传达状态或交互，不用于装饰。
# ================================================================
COLORS = {
    'bg':        '#FAFAF9',   # 暖白 / 极浅灰（页面底）
    'surface':   '#FFFFFF',   # 白（卡面 / 画布）
    'ink':       '#171716',   # 近黑（主文字 / 数值）
    'ink_soft':  '#555552',   # 次级文字
    'muted':     '#8A8A86',   # 中性灰（弱文字 / 原始点）
    'rule':      '#E6E6E3',   # 极细灰边（发丝线）
    'track':     '#EFEFEC',   # 滑轨 / 环轨道
    'accent':    '#1E40AF',   # 克制蓝（主强调 / 模型线 / 当前点）
    'accent_soft': '#E8EDF8', # 蓝浅底（选中 / 悬停 / 置信带）
    'accent_hover': '#17337F', # 蓝 hover（深一档）
    'accent_press': '#142A66', # 蓝 pressed（再深一档，按压反馈）
    'cyan':      '#7FA6C4',   # 淡青（次强调 / 基线参考）
    'warn':      '#B45309',   # 状态色：警示（琥珀深）
    'danger':    '#B4231F',   # 状态色：危险（砖红深）
    'ok':        '#3F6212',   # 状态色：平稳（橄榄深，极克制）
}


def app_font(size: int = 10, weight: int = QFont.Normal) -> QFont:
    """界面字体：系统无衬线（Segoe UI / 微软雅黑回退）。"""
    f = QFont("Segoe UI", size, weight)
    f.setStyleHint(QFont.SansSerif)
    return f


def app_font_num(size: int = 18, weight: int = QFont.Light) -> QFont:
    """仪器数值字体：等宽技术面（Cascadia Code → Consolas 回退），tabular 感。"""
    f = QFont("Cascadia Code", size, weight)
    f.setStyleHint(QFont.Monospace)
    return f


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
            return QColor(COLORS['accent'])
        elif self.value < 0.7:
            return QColor(COLORS['warn'])
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

        # 中心数值（等宽仪器字体）
        p.setPen(QColor(COLORS['ink']))
        p.setFont(app_font_num(20, QFont.Light))
        p.drawText(self.rect(), Qt.AlignCenter, str(int(self.value * 100)))


class EmotionCanvas(QWidget):
    """2D 效价-唤醒情绪平面：发丝轴 + 蓝轨迹 + 蓝当前点。"""

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

        # 白面
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

        # 轨迹：克制蓝，越新越实
        if len(self.trail) >= 2:
            for i in range(1, len(self.trail)):
                v1, a1 = self.trail[i - 1]
                v2, a2 = self.trail[i]
                x1, y1 = int(v1 * w), int((1 - a1) * h)
                x2, y2 = int(v2 * w), int((1 - a2) * h)
                alpha = int(90 + 150 * (i / len(self.trail)))
                p.setPen(QPen(QColor(30, 64, 175, alpha), 2,
                              Qt.SolidLine, Qt.RoundCap))
                p.drawLine(x1, y1, x2, y2)

        # 当前点：克制蓝
        if self.trail:
            v, a = self.trail[-1]
            x, y = int(v * w), int((1 - a) * h)
            p.setBrush(QColor(COLORS['accent']))
            p.setPen(Qt.NoPen)
            p.drawEllipse(x - 5, y - 5, 10, 10)


class CardFrame(QFrame):
    """极细边分组容器：白面 + 1px 极细灰边 + 极小大写标签。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"CardFrame {{ background-color: {COLORS['surface']};"
            f" border-radius: 6px; border: 1px solid {COLORS['rule']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 10px;"
            " letter-spacing: 2px; text-transform: uppercase;"
        )
        layout.addWidget(self.title_label)

        self._content_layout = layout

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)


class StatBlock(QWidget):
    """仪器读数块：极小大写标签在上 + 等宽大数值在下（强字阶）。"""

    def __init__(self, label="", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.caption = QLabel(label.upper())
        self.caption.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 9px; letter-spacing: 1.5px;"
        )
        self.caption.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.value_label = QLabel("—")
        self.value_label.setFont(app_font_num(20, QFont.Light))
        self.value_label.setStyleSheet(f"color: {COLORS['ink']};")
        self.value_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        layout.addWidget(self.caption)
        layout.addWidget(self.value_label)

    def set_value(self, text: str):
        self.value_label.setText(text)


class EmotionCurveWidget(QWidget):
    """2.0 时间序列情绪曲线：置信带 + 模型线 + 基线虚线 + 原始点 + 当前点。

    视觉主角（REFACTOR_PLAN §20：视觉重点是一条情绪曲线）。
    set_data(timestamps, model_v, model_a, var_v, var_a, baseline_v, raw)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timestamps = []
        self.model_v = []
        self.model_a = []
        self.var_v = []
        self.var_a = []
        self.baseline_v = 0.5
        self.raw = []          # [(ts, v)] 原始观察点
        self.setMinimumSize(320, 180)

    def set_data(self, timestamps, model_v, model_a, var_v, var_a,
                 baseline_v=0.5, raw=None):
        self.timestamps = list(timestamps)
        self.model_v = list(model_v)
        self.model_a = list(model_a)
        self.var_v = list(var_v)
        self.var_a = list(var_a)
        self.baseline_v = baseline_v
        self.raw = list(raw or [])
        self.update()

    def clear(self):
        self.timestamps = []
        self.model_v = []
        self.model_a = []
        self.var_v = []
        self.var_a = []
        self.raw = []
        self.update()

    def _x(self, ts, t0, span, w):
        if span <= 0:
            return w // 2
        return int((ts - t0) / span * (w - 8)) + 4

    def _y(self, value, h):
        return int((1 - max(0.0, min(1.0, value))) * (h - 12)) + 6

    def paintEvent(self, event):
        import math
        from PyQt5.QtGui import QPolygonF
        from PyQt5.QtCore import QPointF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 纸面
        p.fillRect(self.rect(), QColor(COLORS['surface']))
        if not self.timestamps:
            p.setPen(QColor(COLORS['muted']))
            p.setFont(app_font(10))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "开始记录后，这里会出现你的情绪曲线")
            return

        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0

        # 基线虚线（雾蓝）
        p.setPen(QPen(QColor(COLORS['cyan']), 1, Qt.DashLine))
        yb = self._y(self.baseline_v, h)
        p.drawLine(4, yb, w - 4, yb)

        # ±1σ 置信带（灰绿半透明）
        if len(self.model_v) >= 2 and len(self.var_v) == len(self.model_v):
            upper, lower = [], []
            for i, ts in enumerate(self.timestamps):
                sd = math.sqrt(max(0.0, self.var_v[i]))
                x = self._x(ts, t0, span, w)
                upper.append(QPointF(x, self._y(min(1.0, self.model_v[i] + sd), h)))
                lower.append(QPointF(x, self._y(max(0.0, self.model_v[i] - sd), h)))
            poly = QPolygonF(upper + list(reversed(lower)))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(30, 64, 175, 26))
            p.drawPolygon(poly)

        # 原始观察点（弱）
        p.setPen(Qt.NoPen)
        for ts, v in self.raw:
            p.setBrush(QColor(156, 150, 138, 120))
            p.drawEllipse(self._x(ts, t0, span, w) - 2, self._y(v, h) - 2, 4, 4)

        # 模型估计线（灰绿）
        if len(self.model_v) >= 2:
            p.setPen(QPen(QColor(COLORS['accent']), 2, Qt.SolidLine, Qt.RoundCap))
            for i in range(1, len(self.model_v)):
                x1 = self._x(self.timestamps[i - 1], t0, span, w)
                y1 = self._y(self.model_v[i - 1], h)
                x2 = self._x(self.timestamps[i], t0, span, w)
                y2 = self._y(self.model_v[i], h)
                p.drawLine(x1, y1, x2, y2)

        # 当前点（朱红）
        if self.model_v:
            cx = self._x(self.timestamps[-1], t0, span, w)
            cy = self._y(self.model_v[-1], h)
            p.setBrush(QColor(COLORS['accent']))
            p.setPen(Qt.NoPen)
            p.drawEllipse(cx - 4, cy - 4, 8, 8)

        # 轴标签
        p.setPen(QColor(COLORS['muted']))
        p.setFont(app_font(8))
        p.drawText(4, 12, "效价")
        p.drawText(w - 46, h - 4, "时间 →")
