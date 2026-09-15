"""windows/correction_sheet.py — 底部上滑框（纠正）

=== 行为 ===
- 从底部滑出（slide-up）
- 内含效价 + 唤醒 两条曲线（small multiples，共用同一时间轴）
- 用户拖动数据点：实时显示效价 + 唤醒的气泡 + 竖直虚线贯穿两图
- 同时保留右侧手动输入面板（选中点时间 + 效价/唤醒输入 + 原值对照）
- 底部：取消 / 提交纠正
- 提交时把 (timestamp, v_new, a_new) 列表存进 self.applied

=== 数据 ===
直接接收 timestamps/model_v/model_a/baseline_v/current_state，
不再依赖 db —— 框架是 UI 而非数据持久化层。
"""
import time

from PyQt5.QtCore import Qt, QPoint, QPointF, QPropertyAnimation, QEasingCurve
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QDoubleSpinBox, QSizePolicy,
)
from PyQt5.QtGui import QFont, QPainter, QPen, QColor, QPainterPath, QPolygonF

from theme import COLORS, app_font, app_font_num, EASE_OUT, DUR_MID
from curve_widget import EmotionCurveWidget


class CorrectionSheet(QDialog):
    """底部上滑纠正框。"""

    def __init__(self, timestamps, model_v, model_a, baseline_v,
                 current_state, parent=None):
        # 用 QDialog 作容器，NoTitleBar / WindowStaysOnTopHint
        super().__init__(parent, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle("纠正")
        self.setModal(True)
        self.setMinimumSize(900, 520)

        self.applied = []   # 提交时填充 [(ts, v_new, a_new), ...]

        # 暂存用户修改（index -> (v, a)）
        self._edits = {}

        # 当前选中点
        self._selected_index = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 头部
        head = QFrame()
        head.setFixedHeight(56)
        head.setStyleSheet(
            f"QFrame {{ background: {COLORS['paper_2']};"
            f" border-bottom: 1px solid {COLORS['rule']}; }}"
        )
        hl = QHBoxLayout(head)
        hl.setContentsMargins(22, 0, 18, 0)
        t = QLabel("纠正  ·  拖动你觉得不对的点")
        t.setFont(app_font(11, QFont.DemiBold))
        t.setStyleSheet(f"color: {COLORS['ink']}; letter-spacing: 1.4px;")
        hl.addWidget(t)
        hl.addStretch(1)

        # 修改计数
        self.count_lbl = QLabel("已修改 0 个点")
        self.count_lbl.setFont(app_font(10))
        self.count_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        hl.addWidget(self.count_lbl)
        outer.addWidget(head)

        # 主体：左 2/3 曲线 + 右 1/3 手动输入
        body = QFrame()
        body.setStyleSheet(f"QFrame {{ background: {COLORS['paper']}; }}")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # ---- 左：双曲线（捕获鼠标拖点）----
        left = QFrame()
        left.setStyleSheet(f"QFrame {{ background: {COLORS['surface']};"
                           f" border-right: 1px solid {COLORS['rule']}; }}")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 8, 0, 0)

        # 拦截鼠标：用我们自己画的曲线 + 事件过滤
        from .correction_canvas import CorrectionCanvas
        self.canvas = CorrectionCanvas(
            timestamps=timestamps, model_v=model_v, model_a=model_a,
            baseline_v=baseline_v,
        )
        self.canvas.point_selected.connect(self._on_select)
        self.canvas.point_dragged.connect(self._on_drag)
        ll.addWidget(self.canvas, 1)
        bl.addWidget(left, 2)

        # ---- 右：手动输入面板 ----
        right = QFrame()
        right.setStyleSheet(f"QFrame {{ background: {COLORS['paper_2']}; }}")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(22, 18, 22, 18)
        rl.setSpacing(12)

        rt = QLabel("手动输入")
        rt.setFont(app_font(9, QFont.DemiBold))
        rt.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.6px;")
        rl.addWidget(rt)

        # 选中点时间
        self.tm_lbl = QLabel("—")
        self.tm_lbl.setFont(app_font_num(13, QFont.Light))
        self.tm_lbl.setStyleSheet(f"color: {COLORS['ink']};")
        self.tm_lbl_k = QLabel("时刻")
        self.tm_lbl_k.setFont(app_font(9, QFont.DemiBold))
        self.tm_lbl_k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.4px;")
        rl.addWidget(self.tm_lbl_k)
        rl.addWidget(self.tm_lbl)

        # 效价
        self._v_orig_lbl = QLabel("原 —")
        self._v_orig_lbl.setFont(app_font(10))
        self._v_orig_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        self.v_sb = QDoubleSpinBox()
        self.v_sb.setRange(0.0, 1.0)
        self.v_sb.setSingleStep(0.01)
        self.v_sb.setDecimals(2)
        self.v_sb.setFont(app_font(12))
        self.v_sb.valueChanged.connect(lambda v: self._on_manual_edit(0, v))
        v_lay = QVBoxLayout()
        v_k = QLabel("效价")
        v_k.setFont(app_font(9, QFont.DemiBold))
        v_k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.4px;")
        v_lay.addWidget(v_k)
        v_lay.addWidget(self.v_sb)
        v_lay.addWidget(self._v_orig_lbl)
        rl.addLayout(v_lay)

        # 唤醒
        self._a_orig_lbl = QLabel("原 —")
        self._a_orig_lbl.setFont(app_font(10))
        self._a_orig_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        self.a_sb = QDoubleSpinBox()
        self.a_sb.setRange(0.0, 1.0)
        self.a_sb.setSingleStep(0.01)
        self.a_sb.setDecimals(2)
        self.a_sb.setFont(app_font(12))
        self.a_sb.valueChanged.connect(lambda v: self._on_manual_edit(1, v))
        a_lay = QVBoxLayout()
        a_k = QLabel("唤醒")
        a_k.setFont(app_font(9, QFont.DemiBold))
        a_k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.4px;")
        a_lay.addWidget(a_k)
        a_lay.addWidget(self.a_sb)
        a_lay.addWidget(self._a_orig_lbl)
        rl.addLayout(a_lay)

        rl.addStretch(1)

        hint = QLabel("提示：双曲线共用同一时间轴，拖动效价点会同步映射唤醒到原值；\n"
                      "若需同时改唤醒，请用右侧手动输入或在双图同步调整。")
        hint.setFont(app_font(10))
        hint.setStyleSheet(f"color: {COLORS['muted']};")
        hint.setWordWrap(True)
        rl.addWidget(hint)

        bl.addWidget(right, 1)

        outer.addWidget(body, 1)

        # 底部按钮
        footer = QFrame()
        footer.setFixedHeight(64)
        footer.setStyleSheet(
            f"QFrame {{ background: {COLORS['paper_2']};"
            f" border-top: 1px solid {COLORS['rule']}; }}"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(22, 0, 18, 0)
        fl.setSpacing(10)
        fl.addStretch(1)

        btn_cancel = QPushButton("取消")
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet(self._cancel_qss())
        btn_cancel.clicked.connect(self.reject)
        fl.addWidget(btn_cancel)

        btn_apply = QPushButton("提交纠正")
        btn_apply.setCursor(Qt.PointingHandCursor)
        btn_apply.setProperty("primary", True)
        btn_apply.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; color: {COLORS['surface']};"
            f" border: none; padding: 10px 20px; font-size: 11.5px;"
            f" font-weight: 600; letter-spacing: 1.4px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['accent_deep']}; }}"
        )
        btn_apply.clicked.connect(self._apply)
        fl.addWidget(btn_apply)

        outer.addWidget(footer)

        # 入场动效：fade in
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(DUR_MID)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve(QEasingCurve.OutQuart))
        self._fade.start()

        # 数据（暴露给 canvas）
        self._timestamps = timestamps
        self._model_v = model_v
        self._model_a = model_a
        self._baseline_v = baseline_v

    def _cancel_qss(self):
        return (
            f"QPushButton {{ background: transparent; border: 1px solid {COLORS['rule']};"
            f" color: {COLORS['ink_2']}; padding: 10px 20px; font-size: 11.5px; }}"
            f"QPushButton:hover {{ border-color: {COLORS['accent']};"
            f" color: {COLORS['accent']}; background: {COLORS['accent_soft']}; }}"
        )

    # ================================================================
    # 选中 / 拖动 / 手动输入 三路汇聚到同一处
    # ================================================================
    def _on_select(self, idx):
        self._selected_index = idx
        ts = self._timestamps[idx]
        self.tm_lbl.setText(time.strftime("%H:%M:%S", time.localtime(ts)))
        self._v_orig_lbl.setText(f"原 {self._model_v[idx]:.2f}")
        self._a_orig_lbl.setText(f"原 {self._model_a[idx]:.2f}")
        # 若已有编辑，回填；否则回填原值
        if idx in self._edits:
            v, a = self._edits[idx]
        else:
            v, a = self._model_v[idx], self._model_a[idx]
        # 临时断开信号，避免 valueChanged 写回到 _edits
        self.v_sb.blockSignals(True)
        self.a_sb.blockSignals(True)
        self.v_sb.setValue(v)
        self.a_sb.setValue(a)
        self.v_sb.blockSignals(False)
        self.a_sb.blockSignals(False)
        self.canvas.set_selected(idx)

    def _on_drag(self, idx, new_v, new_a):
        """来自画布的拖动事件。"""
        self._edits[idx] = (new_v, new_a)
        self._selected_index = idx
        # 同步右侧输入框（仅在选中同一 idx 时）
        if idx == self._selected_index:
            self.v_sb.blockSignals(True)
            self.a_sb.blockSignals(True)
            self.v_sb.setValue(new_v)
            self.a_sb.setValue(new_a)
            self.v_sb.blockSignals(False)
            self.a_sb.blockSignals(False)
        self.canvas.set_edits(self._edits)
        self._refresh_count()

    def _on_manual_edit(self, dim, val):
        """右侧手动输入。"""
        if self._selected_index is None:
            return
        idx = self._selected_index
        if idx in self._edits:
            v, a = self._edits[idx]
        else:
            v, a = self._model_v[idx], self._model_a[idx]
        if dim == 0:
            v = float(val)
        else:
            a = float(val)
        self._edits[idx] = (v, a)
        self.canvas.set_edits(self._edits)
        self._refresh_count()

    def _refresh_count(self):
        n = len(self._edits)
        self.count_lbl.setText(f"已修改 {n} 个点" if n else "已修改 0 个点")

    def _apply(self):
        ts_list = self._timestamps
        self.applied = []
        for idx, (v, a) in self._edits.items():
            self.applied.append((ts_list[idx], v, a))
        self.accept()