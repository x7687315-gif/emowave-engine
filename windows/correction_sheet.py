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

from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QDoubleSpinBox,
)
from PyQt5.QtGui import QFont

from theme import COLORS, app_font, app_font_num, DUR_MID


class CorrectionSheet(QDialog):
    """底部上滑纠正框。"""

    def __init__(self, timestamps, model_v, model_a, baseline_v,
                 current_state, parent=None):
        # 用 QDialog 作容器，NoTitleBar / WindowStaysOnTopHint
        super().__init__(parent, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle("纠正")
        self.setModal(True)
        # 阶段 2 小窗化：v3 是 900×520 的左右 2:1 布局，400px 宽根本放不下
        # → 改成上曲线 + 下手动输入的上下布局，宽高对齐主窗
        self.setMinimumSize(400, 480)

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
        head.setFixedHeight(44)
        head.setStyleSheet(
            f"QFrame {{ background: {COLORS['paper_2']};"
            f" border-bottom: 1px solid {COLORS['rule']}; }}"
        )
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 0, 12, 0)
        t = QLabel("纠正  ·  拖动你觉得不对的点")
        t.setFont(app_font(10, QFont.DemiBold))
        t.setStyleSheet(f"color: {COLORS['ink']}; letter-spacing: 1.4px;")
        hl.addWidget(t)
        hl.addStretch(1)

        # 修改计数
        self.count_lbl = QLabel("已修改 0 个点")
        self.count_lbl.setFont(app_font(9))
        self.count_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        hl.addWidget(self.count_lbl)
        outer.addWidget(head)

        # 主体：上曲线 + 下手动输入（400×480 上下布局）
        body = QFrame()
        body.setStyleSheet(f"QFrame {{ background: {COLORS['paper']}; }}")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # ---- 上：双曲线（捕获鼠标拖点）----
        left = QFrame()
        left.setStyleSheet(f"QFrame {{ background: {COLORS['surface']};"
                           f" border-bottom: 1px solid {COLORS['rule']}; }}")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 6, 0, 6)

        # 拦截鼠标：用我们自己画的曲线 + 事件过滤
        from .correction_canvas import CorrectionCanvas
        self.canvas = CorrectionCanvas(
            timestamps=timestamps, model_v=model_v, model_a=model_a,
            baseline_v=baseline_v,
        )
        self.canvas.point_selected.connect(self._on_select)
        self.canvas.point_dragged.connect(self._on_drag)
        ll.addWidget(self.canvas, 1)
        bl.addWidget(left, 1)

        # ---- 下：手动输入一行（时刻 / 效价 / 唤醒）----
        right = QFrame()
        right.setStyleSheet(
            f"QFrame {{ background: {COLORS['paper_2']};"
            f" border-top: 1px solid {COLORS['rule']}; }}"
        )
        rl = QHBoxLayout(right)
        rl.setContentsMargins(14, 10, 14, 10)
        rl.setSpacing(10)

        def _field(key, widget, extra=None):
            """一格：极小标签在上，控件在下。"""
            g = QFrame()
            g.setStyleSheet("background: transparent; border: none;")
            gl = QVBoxLayout(g)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(3)
            k = QLabel(key)
            k.setFont(app_font(8, QFont.DemiBold))
            k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.2px;")
            gl.addWidget(k)
            gl.addWidget(widget)
            if extra is not None:
                extra.setFont(app_font(9))
                extra.setStyleSheet(f"color: {COLORS['muted']};")
                gl.addWidget(extra)
            return g

        # 选中点时间
        self.tm_lbl = QLabel("—")
        self.tm_lbl.setFont(app_font_num(13, QFont.Light))
        self.tm_lbl.setStyleSheet(f"color: {COLORS['ink']};")
        rl.addWidget(_field("时刻", self.tm_lbl), 1)

        # 效价
        self._v_orig_lbl = QLabel("原 —")
        self.v_sb = QDoubleSpinBox()
        self.v_sb.setRange(0.0, 1.0)
        self.v_sb.setSingleStep(0.01)
        self.v_sb.setDecimals(2)
        self.v_sb.setFont(app_font(11))
        self.v_sb.valueChanged.connect(lambda v: self._on_manual_edit(0, v))
        rl.addWidget(_field("效价", self.v_sb, self._v_orig_lbl), 2)

        # 唤醒
        self._a_orig_lbl = QLabel("原 —")
        self.a_sb = QDoubleSpinBox()
        self.a_sb.setRange(0.0, 1.0)
        self.a_sb.setSingleStep(0.01)
        self.a_sb.setDecimals(2)
        self.a_sb.setFont(app_font(11))
        self.a_sb.valueChanged.connect(lambda v: self._on_manual_edit(1, v))
        rl.addWidget(_field("唤醒", self.a_sb, self._a_orig_lbl), 2)

        bl.addWidget(right)

        outer.addWidget(body, 1)

        # 底部按钮
        footer = QFrame()
        footer.setFixedHeight(52)
        footer.setStyleSheet(
            f"QFrame {{ background: {COLORS['paper_2']};"
            f" border-top: 1px solid {COLORS['rule']}; }}"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14, 0, 12, 0)
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