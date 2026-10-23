"""windows/baseline_tools.py — 模型页第一段：你的常态

新版不再用「卡片 + 输入框 + 按钮」那一套：
  · 一个步进器 `− 55 +`（值本身是最大的东西）
  · 一把横尺：浅蓝块标出常态带 ±2（与主屏那条点线同源）
  · 分叉 / 重置降级成两个文字动作

`baseline_provider` 仍是 callable：换精力人群后 presenter 会 replace 新 controller，
卡片每次 refresh 都必须从源头拉，不能持有旧引用（阶段 5 的教训）。
"""
from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from theme import COLORS, app_font, app_font_num

#: 与 console_presenter 里的「持平」判定阈值保持一致
_STEP = 0.02
TOLERANCE = 0.02


class _Ruler(QWidget):
    """横向常态尺：中线 = 常态，浅蓝块 = ±2 的"持平"区间。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.55
        self.setFixedHeight(38)

    def set_value(self, v):
        self.value = float(v)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        x0, x1 = 2, self.width() - 2
        y, span = 16, self.width() - 4

        def x(v):
            return x0 + max(0.0, min(1.0, v)) * span

        p.setPen(QPen(QColor(COLORS['rule']), 1))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))

        band = QRectF(x(self.value - TOLERANCE), y - 7,
                      max(5.0, x(self.value + TOLERANCE) - x(self.value - TOLERANCE)),
                      14)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS['accent_soft']))
        p.drawRect(band)
        pen = QPen(QColor(COLORS['accent']), 2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(band.left() - 1, y), QPointF(band.right() + 1, y))

        p.setFont(app_font(8))
        fm = p.fontMetrics()
        for v_, text, align in ((0.0, '0', 0), (0.5, '50', 1), (1.0, '100', 2)):
            xx = x(v_)
            p.setPen(QPen(QColor(COLORS['rule']), 1))
            p.drawLine(QPointF(xx, y + 4), QPointF(xx, y + 7))
            p.setPen(QColor(COLORS['muted']))
            dx = (0 if align == 0 else
                  -fm.horizontalAdvance(text) / 2 if align == 1 else
                  -fm.horizontalAdvance(text))
            p.drawText(QPointF(xx + dx, y + 18), text)


class BaselineToolsCard(QFrame):
    def __init__(self, baseline_provider, on_nudge, on_fork, on_reset,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        self._get_ctrl = baseline_provider

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 14, 0, 8)
        lay.setSpacing(0)

        title = QLabel("你的常态")
        title.setFont(app_font(9, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.8px;")
        lay.addWidget(title)
        lay.addSpacing(6)

        hint = QLabel("±2 之内算「与常态持平」，它决定主屏那条点线的位置。")
        hint.setFont(app_font(10))
        hint.setStyleSheet(f"color: {COLORS['muted']};")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addSpacing(8)

        step = QWidget()
        sh = QHBoxLayout(step)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(8)
        sh.addStretch(1)

        self.btn_minus = self._glyph("−", lambda: on_nudge(-_STEP))
        sh.addWidget(self.btn_minus)

        # value_lbl 沿用旧名字：主窗与测试都按它读当前基线
        self.value_lbl = QLabel("55")
        self.value_lbl.setFont(app_font_num(24, QFont.Light))
        self.value_lbl.setStyleSheet(f"color: {COLORS['accent_ink']};")
        self.value_lbl.setAlignment(Qt.AlignCenter)
        self.value_lbl.setFixedWidth(70)
        sh.addWidget(self.value_lbl)

        self.btn_plus = self._glyph("+", lambda: on_nudge(_STEP))
        sh.addWidget(self.btn_plus)
        sh.addStretch(1)
        lay.addWidget(step)

        self._ruler = _Ruler()
        lay.addWidget(self._ruler)

        ops = QHBoxLayout()
        ops.setContentsMargins(0, 10, 0, 0)
        ops.setSpacing(18)
        ops.addStretch(1)
        self.btn_fork = self._text("分叉新起点", on_fork)
        ops.addWidget(self.btn_fork)
        self.btn_reset = self._text("重置为群体先验", on_reset)
        ops.addWidget(self.btn_reset)
        lay.addLayout(ops)

        self.refresh()

    # ------------------------------------------------------------
    @staticmethod
    def _glyph(text, slot):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFlat(True)
        b.setFocusPolicy(Qt.NoFocus)
        b.setFont(app_font(15))
        b.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent;"
            f" color: {COLORS['accent_ink']}; padding: 0; }}"
            f"QPushButton:hover {{ color: {COLORS['accent']}; }}")
        b.clicked.connect(lambda: slot())
        return b

    @staticmethod
    def _text(text, slot):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFlat(True)
        b.setFocusPolicy(Qt.NoFocus)
        b.setFont(app_font(9))
        b.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent;"
            f" color: {COLORS['muted']}; padding: 0; }}"
            f"QPushButton:hover {{ color: {COLORS['accent_ink']}; }}")
        b.clicked.connect(lambda: slot())
        return b

    def refresh(self):
        """从 provider 重新拉：换人群后 controller 会被 replace，不能缓存。"""
        ctrl = self._get_ctrl() if callable(self._get_ctrl) else self._get_ctrl
        value = float(getattr(ctrl.current, 'valence', 0.5))
        self.value_lbl.setText("%d" % round(value * 100))
        self._ruler.set_value(value)
