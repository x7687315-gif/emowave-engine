"""windows/mini_window.py — 迷你置顶窗（360×180 悬浮情绪挂件）

=== 它是什么 ===
一个无边框、始终置顶、不出现在任务栏的小窗，常驻桌面角落：

    ┌── 心潮 ──────────────── × ┐
    │ 平静 · 偏积极            │   ← 当前状态名
    │  V 0.55   A 0.42  99%    │   ← 效价 / 唤醒 / 置信
    │  ╭──────────────────╮    │
    │  │  迷你曲线(96px)   │    │   ← compact 模式：去图例/时间轴
    │  ╰──────────────────╯    │
    │  [ ● 开始记录 ]     12 个观察 │
    └──────────────────────────┘

可在标题区按住拖动（frameless 窗口必须自己实现移动），默认落在主屏右下角
工作区（避开任务栏）。

=== 为什么阶段 4 才做 ===
它依赖阶段 3 的解耦：与主窗**共用同一个 `ConsolePresenter`**，
只多接一份信号、多发一次 `set_input()`。若没有 presenter，
就得把 estimator/timer/db 整套复制一份——那正是我们花一个阶段要避免的事。

=== 硬边语言 ===
沿用构成主义：无圆角、左侧 2px accent 条标识"挂件"身份、关闭用 × 图标按钮
（复用 main_app.IconButton 的描线语言）。
"""
import logging

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QSlider,
    QSizePolicy, QMenu,
)
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

from theme import COLORS, app_font, app_font_num, METRICS
from curve_widget import EmotionCurveWidget

logger = logging.getLogger(__name__)

WIN_W, WIN_H = METRICS['mini_w'], METRICS['mini_h']
#: 曲线两侧留白 = 布局 10px 边距 ×2；min_width 必须由 WIN_W 派生，
#: 否则两处数字各自漂移后，超出部分会被 setFixedSize 静默裁掉。
CURVE_GUTTER = 20


class MiniCloseButton(QPushButton):
    """挂件右上角的 × （手绘，避免再引一套图标资源）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("关闭挂件")
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        hovered = self.underMouse()
        color = QColor(COLORS['accent'] if hovered else COLORS['muted'])
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.SquareCap)
        p.setPen(pen)
        m = 6
        p.drawLine(m, m, self.width() - m, self.height() - m)
        p.drawLine(self.width() - m, m, m, self.height() - m)
        p.end()


class MiniWindow(QWidget):
    """迷你置顶窗。外壳只负责创建与显隐，数据全部由 presenter 驱动。"""

    #: 右键菜单：请求显示主窗 / 退出程序
    show_main_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    #: 用户点了 ×（只是隐藏，不代表退出）
    closed_by_user = pyqtSignal()

    def __init__(self, presenter, parent=None):
        super().__init__(parent)
        self.presenter = presenter
        self._drag_pos = None

        self.setWindowFlags(Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint
                            | Qt.Tool)              # Tool：不进任务栏
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(WIN_W, WIN_H)
        self.setWindowTitle("心潮 · 挂件")

        self._build()
        self._wire_presenter()

        # 默认落点：主屏工作区右下角（避开任务栏）
        try:
            from PyQt5.QtWidgets import QApplication
            scr = QApplication.primaryScreen().availableGeometry()
            self.move(scr.right() - WIN_W - 20, scr.bottom() - WIN_H - 20)
        except Exception as exc:
            logger.debug("定位迷你窗失败（用默认位置）：%s", exc)

    # ------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------
    def _build(self):
        self.setObjectName("MiniWin")
        self.setStyleSheet(
            f"QWidget#MiniWin {{ background: {COLORS['surface']};"
            f" border: 1px solid {COLORS['rule']}; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 标题行（也是拖动手柄）----
        head = QFrame()
        head.setFixedHeight(28)
        head.setStyleSheet(
            f"QFrame {{ background: {COLORS['paper_2']};"
            f" border-bottom: 1px solid {COLORS['rule']}; }}"
        )
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 0, 6, 0)
        hl.setSpacing(6)

        bar = QFrame()                     # 左侧 2px accent 硬边条（挂件身份标记）
        bar.setFixedWidth(2)
        bar.setStyleSheet(f"background: {COLORS['accent']}; border: none;")
        hl.addWidget(bar)

        title = QLabel("心潮")
        title.setFont(app_font(9, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['ink_2']}; letter-spacing: 1.6px;")
        hl.addWidget(title)
        hl.addStretch(1)

        self.state_label = QLabel("—")
        self.state_label.setFont(app_font(10, QFont.Medium))
        self.state_label.setStyleSheet(f"color: {COLORS['ink']};")
        hl.addWidget(self.state_label, 1)
        self.state_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.btn_close = MiniCloseButton()
        self.btn_close.clicked.connect(self._on_close_clicked)
        hl.addWidget(self.btn_close)
        outer.addWidget(head)

        # ---- 读数行 ----
        readings = QHBoxLayout()
        readings.setContentsMargins(10, 6, 10, 0)
        readings.setSpacing(14)

        def _cell(key):
            c = QWidget()
            v = QVBoxLayout(c)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            k = QLabel(key)
            k.setFont(app_font(7, QFont.DemiBold))
            k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1px;")
            val = QLabel("—")
            val.setFont(app_font_num(14, QFont.Light))
            val.setStyleSheet(f"color: {COLORS['ink']};")
            v.addWidget(k)
            v.addWidget(val)
            c.value_label = val
            return c

        self.v_cell = _cell("效价")
        self.a_cell = _cell("唤醒")
        self.c_cell = _cell("置信")
        for c in (self.v_cell, self.a_cell, self.c_cell):
            readings.addWidget(c)
        readings.addStretch(1)

        self.btn_record = QPushButton("●  开始")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_record.setProperty("primary", True)
        self.btn_record.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']};"
            f" color: {COLORS['surface']}; border: none;"
            f" padding: 5px 10px; font-size: 10px; font-weight: 600;"
            f" letter-spacing: 1.2px; border-radius: 0; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['accent_deep']}; }}"
            f"QPushButton:disabled {{ background: {COLORS['rule']};"
            f" color: {COLORS['muted']}; }}"
        )
        self.btn_record.clicked.connect(self._toggle)
        readings.addWidget(self.btn_record, 0, Qt.AlignVCenter)
        outer.addLayout(readings)

        # ---- 迷你曲线（compact：去掉图例与时间轴，只留形状）----
        self.curve = EmotionCurveWidget(series='valence', compact=True,
                                        min_width=WIN_W - CURVE_GUTTER)
        self.curve.setMinimumHeight(58)
        self.curve.setMaximumHeight(74)
        self.curve.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        wrap = QFrame()
        wrap.setStyleSheet(
            f"QFrame {{ background: {COLORS['surface']};"
            f" border: none; border-top: 1px solid {COLORS['rule']}; }}"
        )
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(10, 4, 10, 4)
        wl.setSpacing(0)
        wl.addWidget(self.curve, 1)
        outer.addWidget(wrap, 1)

        # ---- 底部：滑条（挂件上也能直接输入）----
        bottom = QFrame()
        bottom.setStyleSheet(
            f"QFrame {{ background: {COLORS['paper_2']};"
            f" border: none; border-top: 1px solid {COLORS['rule']}; }}"
        )
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(10, 4, 10, 6)
        bl.setSpacing(10)
        self.v_slider = self._make_slider("效价", 50)
        self.a_slider = self._make_slider("唤醒", 40)
        bl.addLayout(self.v_slider['layout'], 1)
        bl.addLayout(self.a_slider['layout'], 1)
        outer.addWidget(bottom)

        # 记录中/推演中的按钮三态与主窗一致（文案前缀也一致，便于肌肉记忆）
        self._btn_qss = {
            "idle": (f"QPushButton {{ background: {COLORS['accent']};"
                     f" color: {COLORS['surface']}; border: none;"
                     f" padding: 5px 10px; font-size: 10px; font-weight: 600;"
                     f" letter-spacing: 1.2px; border-radius: 0; }}"),
            "recording": (f"QPushButton {{ background: {COLORS['accent_deep']};"
                          f" color: {COLORS['surface']}; border: none;"
                          f" padding: 5px 10px; font-size: 10px; font-weight: 600;"
                          f" letter-spacing: 1.2px; border-radius: 0; }}"),
        }

    def _make_slider(self, key, default):
        layout = QHBoxLayout()
        layout.setSpacing(6)
        k = QLabel(key)
        k.setFont(app_font(9))
        k.setStyleSheet(f"color: {COLORS['ink_2']};")
        s = QSlider(Qt.Horizontal)
        s.setRange(0, 100)
        s.setValue(default)
        s.valueChanged.connect(
            lambda *_: self._on_slider("v" if key == "效价" else "a"))
        layout.addWidget(k)
        layout.addWidget(s, 1)
        return {'layout': layout, 'slider': s, 'key': key}

    # ------------------------------------------------------------
    # presenter → 本视图
    # ------------------------------------------------------------
    def _wire_presenter(self):
        p = self.presenter
        p.readouts_changed.connect(self._on_readouts)
        p.readouts_reset.connect(self._on_reset)
        p.curve_changed.connect(self._on_curve)
        p.curve_cleared.connect(self.curve.clear)
        p.mode_changed.connect(self._on_mode)
        p.count_changed.connect(self._on_count)
        p.input_changed.connect(self._on_input_changed)
        self._push_input()
        self._sync_from_presenter()

    def _sync_from_presenter(self):
        """初始同步：把 presenter 的**现状**拉一遍。

        必要原因：`MainConsole.__init__` 会调 `resume_active_wave()`，
        那时本窗还不存在——上一波未走完的衰减只会通知到主窗。
        不同步的话：主窗显示"推演中/禁用"，挂件却显示"开始/可用"，
        用户会在挂件上点"开始"去打断一场正在进行的衰减。
        """
        p = self.presenter
        self._on_mode(p.current_mode())
        self._on_count(p.current_count())
        # v4：状态常驻——波结束后 states 被清空，但 latest_state 仍在
        if p.latest_state is not None or p.states:
            self._on_readouts(*p._readout_payload())
        payload = p.current_curve_payload()
        if payload:
            self._on_curve(payload)

    def _on_input_changed(self, valence, arousal):
        """另一个视图改了输入 → 本窗滑杆镜像过来（避免显示与内核不一致）。"""
        for sl, val in ((self.v_slider['slider'], int(valence * 100)),
                        (self.a_slider['slider'], int(arousal * 100))):
            if sl.value() != val:
                sl.blockSignals(True)
                sl.setValue(val)
                sl.blockSignals(False)

    def _on_readouts(self, valence, arousal, confidence, ci_text, params_text):
        self.v_cell.value_label.setText(f"{int(valence * 100)}")
        self.a_cell.value_label.setText(f"{int(arousal * 100)}")
        self.c_cell.value_label.setText(f"{int(confidence * 100)}%")
        from windows.state_head import derive_state_name
        self.state_label.setText(derive_state_name(valence, arousal))

    def _on_reset(self):
        for c in (self.v_cell, self.a_cell, self.c_cell):
            c.value_label.setText("—")
        self.state_label.setText("—")

    def _on_curve(self, payload):
        self.curve.set_data(payload["ts"], payload["mv"], payload["ma"],
                            payload["vv"], payload["va"],
                            baseline_v=payload["baseline_v"], raw=payload["raw"],
                            predicted_from=payload.get("predicted_from"))

    def _on_mode(self, mode):
        if mode == "recording":
            self.btn_record.setText("■  停止")
            self.btn_record.setEnabled(True)
            self.btn_record.setStyleSheet(self._btn_qss["recording"])
        elif mode == "decaying":
            self.btn_record.setText("◌  推演中")
            self.btn_record.setEnabled(False)
        else:
            self.btn_record.setText("●  开始")
            self.btn_record.setEnabled(True)
            self.btn_record.setStyleSheet(self._btn_qss["idle"])

    def _on_count(self, n):
        self.setToolTip(f"{n} 次自评")

    # ------------------------------------------------------------
    # 本视图 → presenter
    # ------------------------------------------------------------
    def _push_input(self, *_):
        """挂件自己的滑杆也可以作为输入源（与主窗滑杆是同一份 presenter 状态）。

        两个参数都传：本方法作为"整体设定"用（初次接线时）。
        单个滑杆变化走 _on_slider（分通道），不会覆盖另一个通道。
        """
        self.presenter.set_input(self.v_slider['slider'].value() / 100.0,
                                 self.a_slider['slider'].value() / 100.0,
                                 source="mini")

    def _on_slider(self, channel):
        """单通道更新：只推变化的那一维，另一维保持 presenter 里的现值。

        否则会出现"在挂件上调好唤醒 → 回主窗碰一下效价 → 唤醒被弹回原值"。
        """
        if channel == "v":
            self.presenter.submit_input(valence=self.v_slider['slider'].value() / 100.0,
                                        source="mini")
        else:
            self.presenter.submit_input(arousal=self.a_slider['slider'].value() / 100.0,
                                        source="mini")

    def _on_close_clicked(self):
        """× 只隐藏挂件；是否退出由主窗根据"还有没有可见窗口"决定。"""
        self.hide()
        self.closed_by_user.emit()

    def contextMenuEvent(self, event):
        """右键菜单：显式提供"显示主窗"与"退出程序"。

        v4 起关主窗不等于退出（挂件可能还在跑），所以必须有一个
        用户能看懂的、真正的退出入口。
        """
        menu = QMenu(self)
        act_show = menu.addAction("显示主窗")
        menu.addSeparator()
        act_quit = menu.addAction("退出程序")
        chosen = menu.exec_(event.globalPos())
        if chosen is act_show:
            self.show_main_requested.emit()
        elif chosen is act_quit:
            self.quit_requested.emit()

    def _toggle(self):
        self.presenter.toggle_recording()
        self._push_input()

    # ------------------------------------------------------------
    # 无边框拖动
    # ------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
