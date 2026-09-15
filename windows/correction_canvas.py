"""windows/correction_canvas.py — 纠正上滑框的曲线画布

继承 EmotionCurveWidget（双序列 + small multiples）并叠加：
  - 拖点：鼠标按下抓住当前帧上离光标最近的点，移动时实时改 v
  - 选中点：橙色高亮 + 气泡显示 v/a
  - 辅助线：选中点的竖直虚线贯穿两图
  - 编辑点：原位置保留空心小圈，新位置用实心 accent 圆
"""
from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QPointF, QRectF
from PyQt5.QtGui import QPainter, QPen, QColor, QFont
from PyQt5.QtWidgets import QToolTip

from curve_widget import EmotionCurveWidget
from theme import COLORS, app_font


class CorrectionCanvas(EmotionCurveWidget):
    point_selected = pyqtSignal(int)
    point_dragged = pyqtSignal(int, float, float)

    def __init__(self, timestamps, model_v, model_a, baseline_v, parent=None):
        super().__init__(parent, series='both', compact=True)
        self.setMinimumHeight(220)
        self.set_data(timestamps, model_v, model_a,
                      var_v=[0.003] * len(model_v),
                      var_a=[0.003] * len(model_a),
                      baseline_v=baseline_v)
        self._selected = None       # int index
        self._drag_index = None     # int index currently being dragged
        self._edits = {}            # idx -> (v_new, a_new)
        self.setMouseTracking(True)
        self._tooltip_idx = None

    def set_selected(self, idx):
        self._selected = idx
        self.update()

    def set_edits(self, edits):
        self._edits = dict(edits)
        self.update()

    # ================================================================
    # 鼠标事件
    # ================================================================
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        idx = self._nearest_point(e.pos())
        if idx is None:
            return
        self._drag_index = idx
        self._selected = idx
        # 拖动起始 v/a：若已编辑用编辑值，否则用原值
        if idx in self._edits:
            v, a = self._edits[idx]
        else:
            v = self.model_v[idx] if idx < len(self.model_v) else 0.5
            a = self.model_a[idx] if idx < len(self.model_a) else 0.5
        self.point_selected.emit(idx)
        self.point_dragged.emit(idx, v, a)
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag_index is None:
            # 鼠标悬停提示
            idx = self._nearest_point(e.pos())
            if idx != self._tooltip_idx:
                self._tooltip_idx = idx
                self.update()
            return
        idx = self._drag_index
        if idx >= len(self.timestamps) or idx >= len(self.model_v):
            return
        # 改效价：只允许在效价子图里改 v
        # 改唤醒：只允许在唤醒子图里改 a
        # 简化：当前 pos 在哪个 panel 就改哪个，另一个保持原值
        x0, y0_v, pw_v, ph_v = self._plot_rect(0, 2)
        x0, y0_a, pw_a, ph_a = self._plot_rect(1, 2)
        new_v = self._edits.get(idx, (self.model_v[idx], self.model_a[idx]))[0]
        new_a = self._edits.get(idx, (self.model_v[idx], self.model_a[idx]))[1]
        if e.pos().y() < y0_v + ph_v + 6:    # 在效价图附近
            new_v = self.y_to_value(e.pos().y(), 0, 2)
        elif e.pos().y() > y0_a - 6:          # 在唤醒图附近
            new_a = self.y_to_value(e.pos().y(), 1, 2)
        else:
            return    # 中间空白区
        self._edits[idx] = (new_v, new_a)
        self.point_dragged.emit(idx, new_v, new_a)
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag_index = None

    def _nearest_point(self, pos):
        if not self.has_data():
            return None
        # 只在效价子图区域内寻找最近点（因为 x 坐标两子图一致）
        x0, _, pw, _ = self._plot_rect(0, 2)
        # 把 x 限制到绘图区
        if pos.x() < x0 or pos.x() > x0 + pw:
            return None
        # 找最近的 ts
        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0 if t1 > t0 else 1.0
        ts = t0 + (pos.x() - x0) / pw * span
        best, bd = 0, float('inf')
        for i, t in enumerate(self.timestamps):
            d = abs(t - ts)
            if d < bd:
                best, bd = i, d
        return best

    # ================================================================
    # 重绘：叠加选中 / 编辑标记
    # ================================================================
    def paintEvent(self, event):
        super().paintEvent(event)

        if not self.has_data():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0 if t1 > t0 else 1.0

        # 编辑点：在原位置画空心小圈，在新位置画实心 accent 圆
        if self._edits:
            pen_dash = QPen(QColor(COLORS['accent']), 1.5)
            p.setPen(pen_dash)
            p.setBrush(Qt.NoBrush)
            for idx, (nv, na) in self._edits.items():
                x0v, y0v, pwv, phv = self._plot_rect(0, 2)
                x0a, y0a, pwa, pha = self._plot_rect(1, 2)
                ox = self._x(self.timestamps[idx], t0, span, x0v, pwv)
                # 原位置（v 图）：空心小圈
                oy_v = self._y(self.model_v[idx], y0v, phv)
                p.drawEllipse(QPointF(ox, oy_v), 5, 5)
                # 新位置（v 图）：实心 + 实心 v + 同步 a
                ny_v = self._y(nv, y0v, phv)
                p.setBrush(QColor(COLORS['accent']))
                p.drawEllipse(QPointF(ox, ny_v), 4.5, 4.5)
                # a 图同步：原空心 + 新实心
                oy_a = self._y(self.model_a[idx], y0a, pha)
                ny_a = self._y(na, y0a, pha)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(ox, oy_a), 5, 5)
                p.setBrush(QColor(COLORS['accent']))
                p.drawEllipse(QPointF(ox, ny_a), 4.5, 4.5)

        # 选中点：高亮 + 气泡（v/a 数值）
        if self._selected is not None and self._selected < len(self.timestamps):
            x0, y0v, pw, phv = self._plot_rect(0, 2)
            x0, y0a, pw, pha = self._plot_rect(1, 2)
            ox = self._x(self.timestamps[self._selected], t0, span, x0, pw)
            # 竖直辅助线
            pen = QPen(QColor(COLORS['accent']), 1, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(ox, y0v), QPointF(ox, y0a + pha))
            # 当前 v/a
            if self._selected in self._edits:
                nv, na = self._edits[self._selected]
            else:
                nv = self.model_v[self._selected]
                na = self.model_a[self._selected]
            # 气泡（画在 v 图新点旁边）
            txt = f"V {nv:.2f}   A {na:.2f}"
            font = app_font(10)
            p.setFont(font)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(txt) + 12
            th = fm.height() + 6
            bx = min(ox + 8, self.width() - tw - 4)
            by = max(y0v + 4, self._y(nv, y0v, phv) - th - 8)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS['ink']))
            p.drawRect(QRectF(bx, by, tw, th))
            p.setPen(QColor(COLORS['paper']))
            p.drawText(QRectF(bx, by, tw, th), Qt.AlignCenter, txt)