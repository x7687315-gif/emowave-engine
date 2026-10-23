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

与主窗共用同一个 `ConsolePresenter`，不复制任何模型状态。

=== 视觉语言 ===
暖纸底 + 柔和圆角卡片；左侧 2px accent 条标识"挂件"身份，关闭用 × 描线按钮。
"""
import logging

from PyQt5.QtCore import Qt, pyqtSignal, QRectF
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QSlider,
    QSizePolicy, QMenu,
)
from PyQt5.QtGui import QPainter, QPainterPath, QPen, QColor, QFont

from theme import COLORS, app_font, app_font_num, METRICS
from windows.state_head import _fmt_age
from curve_widget import EmotionCurveWidget

logger = logging.getLogger(__name__)

WIN_W, WIN_H = METRICS['mini_w'], METRICS['mini_h']
#: 曲线两侧留白 = 布局 10px 边距 ×2；min_width 必须由 WIN_W 派生，
#: 否则两处数字各自漂移后，超出部分会被 setFixedSize 静默裁掉。
CURVE_GUTTER = 20
#: 收起态高度（隐藏滑条那一条）。展开态仍是 WIN_H。
COLLAPSED_H = WIN_H - 46


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


class _Cell:
    """只暴露 `.value_label` 的小容器（沿用 v3 的访问方式）。"""

    def __init__(self, label):
        self.value_label = label


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
        # 顶层透明 + 自绘圆角：QSS 的 border-radius 改不了窗口形状
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(WIN_W)
        self.setFixedHeight(WIN_H)
        self._corner_radius = METRICS['radius_mini']
        # 展开/钉住状态（v5 Phase 4）
        self._expanded = True
        self.pinned = True              # 默认钉住：常驻桌面，不自动收起
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
            "QWidget#MiniWin { background: transparent; border: none; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        # ---- 标题行：字标 + 距上次自评 + 窗口按钮 ----
        head = QFrame()
        head.setFixedHeight(26)
        r_inner = self._corner_radius - 1
        head.setStyleSheet(
            f"QFrame {{ background: {COLORS['surface']};"
            f" border: none; border-bottom: 1px solid {COLORS['rule']};"
            f" border-top-left-radius: {r_inner}px;"
            f" border-top-right-radius: {r_inner}px; }}")
        self._head = head
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 0, 6, 0)
        hl.setSpacing(8)

        title = QLabel("心潮")
        title.setFont(app_font(9, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 2px;")
        hl.addWidget(title)

        self.age_label = QLabel("")
        self.age_label.setFont(app_font(8))
        self.age_label.setStyleSheet(f"color: {COLORS['muted']};")
        hl.addWidget(self.age_label)
        hl.addStretch(1)

        self.btn_main = self._head_button("↗", "打开主窗")
        self.btn_main.clicked.connect(self._open_main)
        hl.addWidget(self.btn_main)

        self.btn_pin = self._head_button("钉", "钉住：不自动收起")
        self.btn_pin.clicked.connect(self._toggle_pin)
        hl.addWidget(self.btn_pin)

        self.btn_expand = self._head_button("▾", "收起 / 展开")
        self.btn_expand.clicked.connect(lambda: self.set_expanded(not self._expanded))
        hl.addWidget(self.btn_expand)

        self.btn_close = MiniCloseButton()
        self.btn_close.clicked.connect(self._on_close_clicked)
        hl.addWidget(self.btn_close)
        outer.addWidget(head)

        # ---- 当下：状态句（左） + 效价（右） ----
        main = QWidget()
        main.setStyleSheet(f"background: {COLORS['surface']};")
        ml = QHBoxLayout(main)
        ml.setContentsMargins(12, 6, 12, 4)
        ml.setSpacing(10)

        self.state_label = QLabel("—")
        self.state_label.setFont(app_font(14, QFont.Medium))
        self.state_label.setStyleSheet(f"color: {COLORS['ink']};")
        ml.addWidget(self.state_label)
        ml.addStretch(1)

        v_lbl = QLabel("—")
        v_lbl.setFont(app_font_num(20, QFont.Light))
        v_lbl.setStyleSheet(f"color: {COLORS['accent_ink']};")
        v_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ml.addWidget(v_lbl)
        self.v_cell = _Cell(v_lbl)
        outer.addWidget(main)

        # ---- 迷你曲线：去掉图例/时间轴，保留水位线与常态 ----
        self.curve = EmotionCurveWidget(series='valence', compact=True,
                                        min_width=WIN_W - CURVE_GUTTER)
        self.curve.bg_token = 'surface'
        self.curve.fit_range = True     # 134px 里固定 0–100 会压成一条平线
        self.curve.setMinimumHeight(44)
        self.curve.setMaximumHeight(74)
        self.curve.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        wrap = QFrame()
        self._curve_wrap = wrap
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(10, 2, 10, 2)
        wl.setSpacing(0)
        wl.addWidget(self.curve, 1)
        outer.addWidget(wrap, 1)

        # ---- 底部：离差 + 唤醒/置信 + 记一笔 + 记录 ----
        foot = QWidget()
        foot.setStyleSheet(f"background: {COLORS['surface']};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(12, 4, 10, 6)
        fl.setSpacing(10)

        self.dev_label = QLabel("")
        self.dev_label.setFont(app_font(9))
        self.dev_label.setStyleSheet(f"color: {COLORS['muted']};")
        fl.addWidget(self.dev_label)
        fl.addStretch(1)

        a_lbl = QLabel("—")
        a_lbl.setFont(app_font_num(9, QFont.Light))
        a_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        self.a_cell = _Cell(a_lbl)
        c_lbl = QLabel("—")
        c_lbl.setFont(app_font_num(9, QFont.Light))
        c_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        self.c_cell = _Cell(c_lbl)
        # 收起时只留"离差 + 记录"；展开才显示唤醒/置信，避免 360 宽挤爆
        self._cells_box = QWidget()
        cb = QHBoxLayout(self._cells_box)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(5)
        for text, widget in (("唤醒", a_lbl), ("置信", c_lbl)):
            hint = QLabel(text)
            hint.setFont(app_font(8))
            hint.setStyleSheet(f"color: {COLORS['muted']};")
            cb.addWidget(hint)
            cb.addWidget(widget)
        self._cells_box.setVisible(False)
        fl.addWidget(self._cells_box)

        self.btn_note = QPushButton("＋")
        self.btn_note.setFixedSize(20, 20)
        self.btn_note.setCursor(Qt.PointingHandCursor)
        self.btn_note.setToolTip("记一笔（提交当前滑条值）")
        self.btn_note.setFlat(True)
        self.btn_note.setFocusPolicy(Qt.NoFocus)
        self.btn_note.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLORS['ink_2']};"
            f" border: none; padding: 0; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {COLORS['accent_ink']}; }}")
        self.btn_note.clicked.connect(self._commit_note)
        self.btn_note.setVisible(False)
        fl.addWidget(self.btn_note)

        self.btn_record = QPushButton("●  记录")
        self.btn_record.setFixedWidth(64)
        self.btn_record.setStyleSheet("text-align: right;")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_record.setFlat(True)
        self.btn_record.setFocusPolicy(Qt.NoFocus)
        self.btn_record.clicked.connect(self._toggle)
        fl.addWidget(self.btn_record)
        outer.addWidget(foot)

        # ---- 展开态：两条滑条（挂件也能直接输入）----
        bottom = QFrame()
        self._bottom = bottom
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(12, 2, 12, 8)
        bl.setSpacing(10)
        self.v_slider = self._make_slider("效价", 50)
        self.a_slider = self._make_slider("唤醒", 40)
        bl.addLayout(self.v_slider['layout'], 1)
        bl.addLayout(self.a_slider['layout'], 1)
        self.detail = bottom
        outer.addWidget(bottom)
        self._apply_corners()
        self._refresh_record_style()

    def _refresh_record_style(self):
        recording = bool(getattr(self.presenter, "recording", False))
        color = COLORS['accent_deep'] if recording else COLORS['accent_ink']
        self.btn_record.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {color};"
            f" border: none; padding: 0; font-size: 10px; font-weight: 600;"
            f" letter-spacing: 0.4px; }}"
            f"QPushButton:hover {{ color: {COLORS['accent']}; }}"
            f"QPushButton:disabled {{ color: {COLORS['muted']}; }}")

    def _head_button(self, glyph, tip):
        """头部 20×20 小按钮（与 × 同尺寸，视觉成组）。"""
        b = QPushButton(glyph)
        b.setFixedSize(20, 20)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" color: {COLORS['muted']}; font-size: 11px; padding: 0;"
            f" border-radius: {METRICS['radius_sm']}px; }}"
            f"QPushButton:hover {{ color: {COLORS['accent']}; }}"
        )
        return b

    # ------------------------------------------------------------
    # 圆角（顶层透明 + 各块负责自己那一侧）
    # ------------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            self._corner_radius, self._corner_radius)
        p.fillPath(path, QColor(COLORS['surface']))
        p.setPen(QPen(QColor(COLORS['rule']), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.end()

    def _apply_corners(self):
        """展开时底部条负责下圆角；收起时曲线区负责下圆角。"""
        r = self._corner_radius - 1
        bottom_r = 0 if self._expanded else r
        self._curve_wrap.setStyleSheet(
            f"QFrame {{ background: {COLORS['surface']};"
            f" border: none; border-top: 1px solid {COLORS['rule']};"
            f" border-bottom-left-radius: {bottom_r}px;"
            f" border-bottom-right-radius: {bottom_r}px; }}"
        )

    # ------------------------------------------------------------
    # 展开 / 钉住（v5 Phase 4）
    # ------------------------------------------------------------
    def set_expanded(self, expanded: bool):
        """收起 = 只留状态 + 曲线 + 记录按钮；展开 = 多出滑条。

        收起不是「暂停」：presenter 照常推进，定时刷新也不受影响。
        窗口尺寸变化只是视觉，不影响任何模型状态。
        """
        self._expanded = bool(expanded)
        self.detail.setVisible(self._expanded)
        self._cells_box.setVisible(self._expanded)
        self.setFixedHeight(WIN_H if self._expanded else COLLAPSED_H)
        self.btn_expand.setText("▾" if self._expanded else "▴")
        self._apply_corners()

    def _toggle_pin(self):
        self.pinned = not self.pinned
        if self.pinned:
            self.set_expanded(True)
        self.btn_pin.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" color: {COLORS['accent'] if self.pinned else COLORS['muted']};"
            f" font-size: 11px; padding: 0; }}"
            f"QPushButton:hover {{ color: {COLORS['accent']}; }}"
        )

    def _open_main(self):
        self.show_main_requested.emit()

    def enterEvent(self, event):
        """鼠标移入 → 展开（钉住时本来就是展开的）。"""
        super().enterEvent(event)
        if not self._expanded:
            self.set_expanded(True)

    def leaveEvent(self, event):
        """鼠标移出 → 收起，除非已钉住。

        收起只影响视觉；presenter 的推进与 5 分钟刷新都不受窗口形态影响。
        """
        super().leaveEvent(event)
        if not self.pinned and self._expanded:
            self.set_expanded(False)

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
        snap = p.sync_snapshot()
        self._on_mode(snap["mode"])
        self._on_count(snap["count"])
        if snap["readout"]:
            self._on_readouts(*snap["readout"])
        if snap["curve"]:
            self._on_curve(snap["curve"])

    def _on_input_changed(self, valence, arousal):
        """另一个视图改了输入 → 本窗滑杆镜像过来（避免显示与内核不一致）。"""
        for sl, val in ((self.v_slider['slider'], int(valence * 100)),
                        (self.a_slider['slider'], int(arousal * 100))):
            if sl.value() != val:
                sl.blockSignals(True)
                sl.setValue(val)
                sl.blockSignals(False)

    def _on_readouts(self, valence, arousal, confidence, ci_text, params_text,
                     state_ts=0.0, last_obs_ts=0.0):
        # v5：记下"这个数是几点的 / 离上次自评多久"，Phase 4 会显示出来
        self._state_ts = state_ts
        self._last_obs_ts = last_obs_ts
        self._last_v = float(valence)
        self._update_age()
        # round 与主窗保持一致，否则两个窗口会显示差 1 的数
        self.v_cell.value_label.setText("%d" % round(valence * 100))
        self.a_cell.value_label.setText("%d" % round(arousal * 100))
        self.c_cell.value_label.setText("%d%%" % round(confidence * 100))
        self.curve.set_waterline(valence)
        # 离差：与主屏同一句话，只是更短
        try:
            base = float(self.presenter.baseline_ctrl.current.valence)
            diff = int(round((float(valence) - base) * 100))
            self.dev_label.setText(
                "与常态持平" if abs(diff) <= 2 else
                ("比常态高 %d" % diff if diff > 0 else "比常态低 %d" % abs(diff)))
        except Exception:
            self.dev_label.setText("")
        from windows.state_head import derive_state_name
        self.state_label.setText(derive_state_name(valence, arousal))

    def _update_age(self):
        """把「距上次自评多久」说清楚——这是挂件最容易骗人的地方。"""
        if not self._state_ts or not self._last_obs_ts:
            self.age_label.setText("")
            return
        age = max(0.0, self._state_ts - self._last_obs_ts)
        if age < 60:
            self.age_label.setText("刚刚")
        else:
            self.age_label.setText(_fmt_age(age) + "前")

    def _on_reset(self):
        for c in (self.v_cell, self.a_cell, self.c_cell):
            c.value_label.setText("—")
        self.state_label.setText("—")
        self.dev_label.setText("")
        self.curve.set_waterline(None)

    def _on_curve(self, payload):
        self.curve.set_data(payload["ts"], payload["mv"], payload["ma"],
                            payload["vv"], payload["va"],
                            baseline_v=payload["baseline_v"], raw=payload["raw"],
                            predicted_from=payload.get("predicted_from"),
                            corrections=payload.get("corrections"))

    def _on_mode(self, mode):
        if mode == "recording":
            self.set_expanded(True)         # 开始记录 → 滑条必须可见
            self.btn_record.setText("■  停止")
            self.btn_record.setEnabled(True)
            self.btn_note.setVisible(True)
        elif mode == "decaying":
            self.btn_record.setText("◌  推演")
            self.btn_record.setEnabled(False)
            self.btn_note.setVisible(False)
        else:
            self.btn_record.setText("●  记录")
            self.btn_record.setEnabled(True)
            self.btn_note.setVisible(False)
        self._refresh_record_style()

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

    def _commit_note(self):
        """「记一笔」：把 presenter 当前的输入值立即结算成一条自评。"""
        self.presenter.commit_now()

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
