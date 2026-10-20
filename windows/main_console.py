"""windows/main_console.py — 心潮 EmoWave 2.0 新主界面

=== 信息架构（v2 重做）===
主界面只 4 区块，从上到下：
  ① 当前情绪状况（状态名 + 参数说明▾ + 「情绪有误，点这里纠正」入口）
  ② 读数条（效价 / 唤醒 / 置信 / 置信区间）
  ③ 情绪曲线（视觉主角）
  ④ 输入台（效价 + 唤醒双滑块 + 开始/停止记录）

次要区删除：基线主权 / 个人模型 / 事件回顾 / 历史记录 → 收进右侧抽屉。

=== 与原 console_window.py 的差异 ===
1. 整体暖色板（来自 theme.COLORS）；不依赖 widgets.py 旧 COLORS 的冷色蓝
2. 纠正改成「点入口 → 底部上滑框」（不在主界面占位）
3. 状态名按效价×唤醒象限派生（不主观评判）
4. 曲线支持双序列（效价 + 唤醒上下 small multiples 共用时间轴）
5. 主界面不再有菜单栏；用页头图标按钮替代
"""
import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QPushButton,
    QFrame,
    QScrollArea,
    QStackedWidget,
)
from PyQt5.QtGui import QFont

from theme import COLORS, app_font, app_font_num
from curve_widget import EmotionCurveWidget


# 子模块（抽屉、首项、上滑框）
from .baseline_tools import BaselineToolsCard
from .model_card import ModelCard
from .legacy_embed import LegacyEmbed
from .state_head import StateHead, ParamsExplainCard
# 注意用绝对导入：windows 是顶层包，`..presenters` 会越过顶层包报错。
from presenters.console_presenter import ConsolePresenter
from .correction_sheet import CorrectionSheet

logger = logging.getLogger(__name__)


# ================================================================
# ① 状态头（合并自 v3 的 StatusCard + ReadoutBar，见 state_head.py）
# ================================================================
# ================================================================
# ④ 输入台（双滑块 + 记录按钮）
# ================================================================
class InputPanel(QWidget):
    """双滑块 + 开始/停止记录 + 采样数。"""

    def __init__(self, on_toggle, on_note=None, parent=None):
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._on_note = on_note

        outer = QVBoxLayout(self)
        # 阶段 2：边距 22→14、间距 14→8，把输入台压到 ~104px
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        # 行 1：效价滑块
        self.v_row = self._make_slider_row("效价", 50, "50/100")
        outer.addLayout(self.v_row['layout'])
        # 行 2：唤醒滑块
        self.a_row = self._make_slider_row("唤醒", 40, "40/100")
        outer.addLayout(self.a_row['layout'])

        # 行 3：操作
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)
        self.btn_record = QPushButton("●  开始记录")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_record.setProperty("primary", True)
        self.btn_record.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; color: {COLORS['surface']};"
            f" border: none; padding: 10px 16px; font-size: 11.5px;"
            f" font-weight: 600; letter-spacing: 1.4px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['accent_deep']}; }}"
        )
        self.btn_record.clicked.connect(lambda: self._on_toggle())
        ctrl.addWidget(self.btn_record)

        # v4：v4 起"记录"不再每秒自动产生观察，用户需要能主动确认一次感受。
        # 「记一笔」只在记录中可用，不占新行（与记录按钮同一行）。
        self.btn_note = QPushButton("记一笔")
        self.btn_note.setCursor(Qt.PointingHandCursor)
        self.btn_note.setEnabled(False)
        self.btn_note.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLORS['ink_2']};"
            f" border: 1px solid {COLORS['rule']}; padding: 9px 10px;"
            f" font-size: 10.5px; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ color: {COLORS['accent']};"
            f" border-color: {COLORS['accent']}; }}"
            f"QPushButton:disabled {{ color: {COLORS['rule']};"
            f" border-color: {COLORS['rule']}; }}"
        )
        if self._on_note is not None:
            self.btn_note.clicked.connect(lambda: self._on_note())
        ctrl.addWidget(self.btn_note)

        # 阶段 2：计数与提示合并成一个标签。v3 是两个标签并列，400px 宽下
        # 计数被挤成"55 个观"（右侧裁掉）——合成一个后两个信息都在。
        # v4：文案从"个观察 · 1 Hz"改为"次自评"——观察不再按秒产生。
        self.count_label = QLabel("0 次自评")
        self.count_label.setFont(app_font(9))
        self.count_label.setStyleSheet(f"color: {COLORS['muted']};")
        ctrl.addWidget(self.count_label)
        ctrl.addStretch(1)
        outer.addLayout(ctrl)

        self.v_row['slider'].valueChanged.connect(
            lambda v: self.v_row['num'].setText(f"{v}/100"))
        self.a_row['slider'].valueChanged.connect(
            lambda v: self.a_row['num'].setText(f"{v}/100"))

    def _make_slider_row(self, key, default, num_text):
        layout = QHBoxLayout()
        layout.setSpacing(10)
        k = QLabel(key)
        k.setFont(app_font(10))
        k.setStyleSheet(f"color: {COLORS['ink_2']};")
        k.setFixedWidth(40)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(default)
        num = QLabel(num_text)
        num.setFont(app_font_num(12, QFont.Light))
        num.setStyleSheet(f"color: {COLORS['muted']};")
        num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        num.setFixedWidth(46)
        layout.addWidget(k)
        layout.addWidget(slider, 1)
        layout.addWidget(num)
        return {'layout': layout, 'slider': slider, 'num': num, 'key': key}

    _BTN_PRIMARY = (
        f"QPushButton {{ background: {COLORS['accent']}; color: {COLORS['surface']};"
        f" border: none; padding: 11px 22px; font-size: 11.5px;"
        f" font-weight: 600; letter-spacing: 1.6px; }}"
        f"QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
        f"QPushButton:pressed {{ background: {COLORS['accent_deep']}; }}"
    )
    _BTN_STOP = (
        f"QPushButton {{ background: {COLORS['accent_deep']};"
        f" color: {COLORS['surface']}; border: none; padding: 11px 22px;"
        f" font-size: 11.5px; font-weight: 600; letter-spacing: 1.6px; }}"
    )
    _BTN_DECAY = (
        f"QPushButton {{ background: {COLORS['rule']};"
        f" color: {COLORS['muted']}; border: none; padding: 11px 22px;"
        f" font-size: 11.5px; font-weight: 600; letter-spacing: 1.6px; }}"
    )

    def set_mode(self, mode: str):
        """按钮三态：idle=开始记录 / recording=停止记录 / decaying=推演中(禁用)。"""
        btn = self.btn_record
        if mode == "recording":
            btn.setText("■  停止记录")
            btn.setEnabled(True)
            btn.setStyleSheet(self._BTN_STOP)
            self.btn_note.setEnabled(True)
        elif mode == "decaying":
            btn.setText("◌  推演中…")
            btn.setEnabled(False)
            btn.setStyleSheet(self._BTN_DECAY)
            self.btn_note.setEnabled(False)
        else:  # idle
            btn.setText("●  开始记录")
            btn.setEnabled(True)
            btn.setStyleSheet(self._BTN_PRIMARY)
            self.btn_note.setEnabled(False)

    def get_values(self):
        return (self.v_row['slider'].value() / 100.0,
                self.a_row['slider'].value() / 100.0)

    def set_count(self, n):
        self.count_label.setText(f"{n} 次自评" if n else "还没有自评")


# ================================================================
# 主：MainConsole
# ================================================================
class MainConsole(QWidget):
    """小窗化主界面（三页）：曲线 / 模型 / 历史。

    === 阶段 3 之后本类只是"视图" ===
    内核状态与编排全部搬到 `presenters.console_presenter.ConsolePresenter`，
    本类只做三件事：
      1. 造控件、摆布局（`_build`）
      2. 把 presenter 的信号接到控件更新槽（`_wire_presenter`）
      3. 把用户的动作（滑杆/按钮）翻译成 presenter 的方法调用

    v3 时代它还直接持有 estimator/calibrator/db/QTimer 并手写 Matérn 衰减；
    现在这些一律通过 `self.presenter` 访问。为兼容既有调用/测试，
    常用属性（observations/states/params/estimator/…）以 property 转发。
    """

    def __init__(self, parent=None, db=None, archetype_key=None, clock=None):
        super().__init__(parent)
        # ---- 内核编排层（唯一持有 emowave 对象的地方）----
        # clock 只给测试注入假时钟用；生产环境走默认墙钟
        self.presenter = ConsolePresenter(db=db, archetype_key=archetype_key, clock=clock)

        self._build()
        self._wire_presenter()

        self._refresh_readouts()
        self.presenter.resume_active_wave()   # 上次未走完的波：按真实时间续算
        # 没有未走完的波时，用最近一条事件的观察重建模型
        # （只影响"现在是什么状态"，不把历史曲线画出来）
        if not self.presenter.estimator.is_initialized:
            self.presenter.restore_recent_context()

    # ------------------------------------------------------------
    # v3 兼容：把 presenter 的状态以 property 暴露出来
    # （tests/test_main_app.py 等 30 条回归按这些名字访问）
    # ------------------------------------------------------------
    @property
    def db(self):
        return self.presenter.db

    @property
    def archetype_key(self):
        return self.presenter.archetype_key

    @property
    def archetype(self):
        return self.presenter.archetype

    @property
    def params(self):
        return self.presenter.params

    @property
    def estimator(self):
        return self.presenter.estimator

    @property
    def calibrator(self):
        return self.presenter.calibrator

    @property
    def baseline_ctrl(self):
        return self.presenter.baseline_ctrl

    @property
    def observations(self):
        return self.presenter.observations

    @property
    def states(self):
        return self.presenter.states

    @property
    def recording(self):
        return self.presenter.recording

    @recording.setter
    def recording(self, value):
        self.presenter.recording = bool(value)

    @property
    def _segment(self):
        return self.presenter._segment

    @property
    def _edits(self):
        return self.presenter._edits

    @property
    def _wave_active(self):
        return self.presenter._wave_active

    @_wave_active.setter
    def _wave_active(self, value):
        self.presenter._wave_active = bool(value)

    @property
    def _last_obs_ts(self):
        return self.presenter._last_obs_ts

    @property
    def _decay_now(self):
        return self.presenter._decay_now

    @property
    def timer(self):
        return self.presenter.timer

    # ================================================================
    # 布局（小窗化 · 阶段 1）
    # ================================================================
    def _build(self):
        """三页结构：
        页 0 曲线（4 区块）/ 页 1 模型（基线主权 + 个人模型）/ 页 2 历史（回顾 + 记录）。

        为什么改：v3 的右侧抽屉宽 380px，比 400×680 的小窗本身还宽，
        物理上无法并排；且次要区与主区同屏时小窗根本放不下。
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        # ---- 页 0：曲线（状态头 + 曲线 + 输入台）----
        main = QWidget()
        main.setStyleSheet(f"background: {COLORS['paper']};")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        # ① 状态头（状态名 + 四读数合并，v3 的 StatusCard/ReadoutBar 合一）
        self.status = StateHead(on_correction_clicked=self._open_correction_sheet)
        # 读数条已并入状态头；保留 readout 别名以维持 v3 的公开属性契约
        self.readout = self.status
        ml.addWidget(self.status)

        # ② 曲线（视觉主角）
        curve_frame = QFrame()
        curve_frame.setObjectName("CurvePanel")
        curve_frame.setStyleSheet(
            f"QFrame#CurvePanel {{ background: {COLORS['surface']};"
            f" border: 1px solid {COLORS['rule']}; }}"
        )
        cf = QVBoxLayout(curve_frame)
        cf.setContentsMargins(0, 14, 0, 4)
        head = QHBoxLayout()
        head.setContentsMargins(18, 0, 18, 0)
        hk = QLabel("EMOTION CURVE · 模型估计")
        hk.setFont(app_font(10, QFont.DemiBold))
        hk.setStyleSheet(f"color: {COLORS['ink_2']}; letter-spacing: 1.8px;")
        head.addWidget(hk)
        head.addStretch(1)
        legend = QLabel("— 模型线    ░ ±1σ    ┄ 基线    · 原始")
        legend.setFont(app_font(9))
        legend.setStyleSheet(f"color: {COLORS['muted']};")
        head.addWidget(legend)
        cf.addLayout(head)
        self.curve = EmotionCurveWidget(series='valence')
        self.curve.setMinimumHeight(340)
        cf.addWidget(self.curve)
        ml.addWidget(curve_frame, 1)

        # ③ 输入台
        self.input_panel = InputPanel(on_toggle=self._toggle_recording,
                                      on_note=self._commit_note)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
        ml.addWidget(sep)
        ml.addWidget(self.input_panel)

        self.page_curve = main
        self.stack.addWidget(main)

        # ---- 页 1：模型（基线主权 + 个人模型 + 参数说明）----
        # 卡片是纯视图：provider 保证换人群后拿到**新** controller，
        # 三个回调一律走 presenter（顺带拿回 provenance 与 baseline_changed）
        self.baseline_card = BaselineToolsCard(
            baseline_provider=lambda: self.presenter.baseline_ctrl,
            on_nudge=self._nudge,
            on_fork=self._fork,
            on_reset=self._reset_baseline,
        )
        # provider 形式传入：换人群/学习一步后 params 会被 replace，
        # 卡片必须从源头拉最新值（v3 遗留 bug，持有对象会显示旧值）
        self.model_card = ModelCard(
            params=lambda: self.presenter.params,
            calibrator=lambda: self.presenter.calibrator,
            on_learn=self._learn,
        )
        body = QVBoxLayout()
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(14)
        body.addWidget(self.baseline_card)
        body.addWidget(self.model_card)
        # 参数说明：v3 是状态卡里 220px 折叠面板，小窗放不下 → 常驻模型页
        self.params_card = ParamsExplainCard()
        body.addWidget(self.params_card)
        body.addStretch(1)
        self.page_model = self._make_scroll_page(body)
        self.stack.addWidget(self.page_model)

        # ---- 页 2：历史（事件回顾 + 历史记录）----
        self.legacy = LegacyEmbed(db=self.db)
        body2 = QVBoxLayout()
        body2.setContentsMargins(16, 16, 16, 16)
        body2.setSpacing(14)
        body2.addWidget(self.legacy)
        body2.addStretch(1)
        self.page_history = self._make_scroll_page(body2)
        self.stack.addWidget(self.page_history)

    def _make_scroll_page(self, body_layout):
        """把若干区块包成一个可滚动的整页。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("PageBody")
        content.setLayout(body_layout)
        scroll.setWidget(content)
        lay.addWidget(scroll, 1)
        return page

    def show_page(self, index: int):
        """切换页（外壳 TopTabs.currentChanged 接这里）。"""
        self.stack.setCurrentIndex(index)
        if index == 2:
            self.legacy.refresh()      # 进「历史」页才刷新：落库后立刻可见

    # ================================================================
    # presenter → 视图 的信号接线（解耦后唯一的耦合点）
    # ================================================================
    def _wire_presenter(self):
        p = self.presenter
        p.mode_changed.connect(self.input_panel.set_mode)
        p.count_changed.connect(self.input_panel.set_count)
        p.readouts_changed.connect(self._on_readouts)
        p.readouts_reset.connect(self._reset_readouts)
        p.curve_changed.connect(self._on_curve_data)
        p.curve_cleared.connect(self.curve.clear)
        p.history_changed.connect(self._refresh_history)
        p.baseline_changed.connect(self.baseline_card.refresh)

        # 滑条 → presenter：记录中 1Hz 用"当前滑条值"采样（v3 同语义）
        self.input_panel.v_row['slider'].valueChanged.connect(
            lambda *_: self._on_slider("v"))
        self.input_panel.a_row['slider'].valueChanged.connect(
            lambda *_: self._on_slider("a"))
        self.presenter.input_changed.connect(self._on_input_changed)
        self._push_input()

    def _push_input(self, *_):
        """把当前滑条值整体告诉 presenter（初次接线 / 编程式设定用）。

        单个滑杆变化走 _on_slider（分通道），避免覆盖另一维。
        """
        self.presenter.set_input(*self.input_panel.get_values(), source="main")

    def _on_slider(self, channel):
        """分通道推送：只推变化的那一维。

        修的是多视图共享 presenter 的输入源争抢——若主窗每次都整体推，
        主窗碰一下效价就会把挂件刚调好的唤醒打回原值（且无任何提示）。
        """
        if channel == "v":
            self.presenter.submit_input(
                valence=self.input_panel.v_row['slider'].value() / 100.0,
                source="main")
        else:
            self.presenter.submit_input(
                arousal=self.input_panel.a_row['slider'].value() / 100.0,
                source="main")

    def _on_input_changed(self, valence, arousal):
        """另一个视图改了输入 → 主窗滑杆镜像过来。"""
        for row, val in ((self.input_panel.v_row, int(valence * 100)),
                         (self.input_panel.a_row, int(arousal * 100))):
            sl = row['slider']
            if sl.value() != val:
                sl.blockSignals(True)
                sl.setValue(val)
                sl.blockSignals(False)
            row['num'].setText(f"{val}/100")

    def _on_readouts(self, valence, arousal, confidence, ci_text, params_text,
                     state_ts=0.0, last_obs_ts=0.0):
        # v5：state_ts = 这个数是「几点」的估计；last_obs_ts = 最后一次真实自评。
        # 两者不同 → 中间是模型推演（Phase 3 会在界面上明说）。
        self._state_ts = state_ts
        self._last_obs_ts_shown = last_obs_ts
        self.readout.update_values(valence, arousal, confidence, ci_text)
        self.status.update_state(valence, arousal, params_text)
        # 参数说明常驻「模型」Tab，同一份数据双处展示
        self.params_card.update_params(valence, arousal, params_text)

    def _on_curve_data(self, payload):
        self.curve.set_data(
            payload["ts"], payload["mv"], payload["ma"],
            payload["vv"], payload["va"],
            baseline_v=payload["baseline_v"], raw=payload["raw"],
            predicted_from=payload.get("predicted_from"),
        )

    def _refresh_history(self):
        try:
            self.legacy.refresh()
        except Exception as exc:            # 刷新失败不该打断记录流程
            logger.warning("历史面板刷新失败：%s", exc)

    # ================================================================
    # 视图 → presenter 的动作转发（方法名与 v3 完全一致）
    # ================================================================
    def _open_correction_sheet(self):
        if not self.states:
            return
        sheet = CorrectionSheet(
            timestamps=self.observations_ts_for_curve(),
            model_v=[s.valence for _, s in self.states],
            model_a=[s.arousal for _, s in self.states],
            baseline_v=self.baseline_ctrl.current.valence,
            current_state=self.states[-1][1],
            parent=self,
        )
        sheet.exec_()
        self.ingest_corrections(sheet.applied)

    def ingest_corrections(self, applied, source=None):
        return self.presenter.ingest_corrections(applied, source)

    def _toggle_recording(self, want_running=None):
        self.presenter.toggle_recording(want_running)
        # 开始记录时立刻把当前滑条值推过去，避免首拍用到初始值
        self._push_input()

    def _commit_note(self):
        """「记一笔」：把当前滑条值作为一次明确自评立即提交。"""
        self._push_input()
        self.presenter.commit_now()

    def _sample(self):
        v, a = self.input_panel.get_values()
        self.presenter.sample(v, a)

    def _decay_step(self):
        self.presenter.decay_step()

    def observations_ts_for_curve(self):
        return self.presenter.observations_ts_for_curve()

    def _nudge(self, delta: float):
        self.presenter.nudge(delta)

    def _fork(self):
        self.presenter.fork()

    def _reset_baseline(self):
        self.presenter.reset()

    def _learn(self):
        self.presenter.learn()
        self._refresh_readouts()
        self.model_card.refresh()      # learn() 返回新 params，卡片重新拉取

    def set_archetype(self, key: str):
        """切换精力人群先验：重 seed + 清空会话（委托 presenter）。"""
        self.presenter.set_archetype(key)
        # presenter 重建了 estimator/calibrator/params。
        # 两张卡都已是 provider 形式（每次 refresh 从源头拉最新值），
        # 所以这里只需触发刷新，不再需要手动换引用——v3/阶段5 的
        # "卡片持失效引用" 隐患到此彻底消除。
        self.baseline_card.refresh()
        self.model_card.refresh()
        self._push_input()

    # ================================================================
    # 刷新（现在都由 presenter 信号驱动；保留两个入口供外部主动调）
    # ================================================================
    def _refresh_readouts(self):
        if self.states:
            self._on_readouts(*self.presenter._readout_payload())

    def _reset_readouts(self):
        # 状态头内含四读数，一次复位（v3 是逐个标签 setText）
        self.status.reset_values()
        self.params_card.reset_params()
