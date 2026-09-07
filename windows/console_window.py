"""windows/console_window.py — 心潮 EmoWave 2.0 单页控制台

把「所有内容」集中在一个界面（用户要求①），并让设计跟着 2.0 新功能走（要求②）：
旧 UI 只有 1.x 的「事件追踪」四页；2.0 内核新增了实时状态估计、置信度、
趋势、可编辑曲线、基线主权、个人在线学习——这些旧设计完全没有体现。
本页把它们全部露出，自上而下：

  ① 实时状态   效价/唤醒/强度/稳定 + 置信度 + 趋势 + 置信区间
  ② 情绪曲线   时间序列 + ±1σ 置信带 + 基线虚线 + 原始点（视觉主角）
  ③ 实时调节   效价/唤醒滑条，1Hz 采样喂给 2.0 估计器
  ④ 基线主权   当前基线 + nudge / fork / reset（L4，用户最终解释权）
  ⑤ 个人模型   n_events / ℓ / 阶段 / 收缩度 + 学习一步（L3 在线学习）
  ⑥ 纠正       纠正当前状态（Correction First）+ 最近纠正日志
  ⑦ 事件回顾   （嵌入旧 EventSummaryWindow，保留全部旧内容）
  ⑧ 历史记录   （嵌入旧 HistoryWindow，保留全部旧内容）

侧边栏为「锚点导航」，点击滚动到对应分区；可折叠。
"""
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QScrollArea, QGridLayout, QListWidget, QFrame,
)

from widgets import (
    CardFrame, StatBlock, EmotionCurveWidget, COLORS, app_font, hline,
)
from models import TimeSeriesSample
from emowave import Observation, ObservationSource, ModelParameters, UserCorrection
from emowave.core.domain.correction import CorrectionDimension, CorrectionSource
from emowave.core.domain.baseline import Baseline
from emowave.core.estimator.estimator import StateEstimator
from emowave.core.calibration.baseline_control import BaselineController
from emowave.core.calibration.calibrator import Calibrator

from windows.event_summary_window import EventSummaryWindow
from windows.history_window import HistoryWindow

_SLIDER_QSS = (
    f"QSlider::groove:horizontal {{ height: 2px; background: {COLORS['rule']};"
    f" border-radius: 1px; }}"
    f"QSlider::handle:horizontal {{ width: 12px; margin: -5px 0;"
    f" border-radius: 6px; background: {COLORS['accent']}; }}"
    f"QSlider::handle:horizontal:pressed {{ background: {COLORS['accent_hover']}; }}"
    f"QSlider::sub-page:horizontal {{ background: {COLORS['accent']};"
    f" border-radius: 1px; }}"
)

_BTN_PRIMARY = (
    f"QPushButton {{ background-color: {COLORS['accent']}; color: #FFFFFF;"
    f" border: none; border-radius: 6px; padding: 7px 14px;"
    f" font-size: 12px; font-weight: 600; }}"
    f"QPushButton:hover {{ background-color: {COLORS['accent_hover']}; }}"
    f"QPushButton:pressed {{ background-color: {COLORS['accent_press']}; padding: 8px 14px 6px 14px; }}"
)
_BTN_OUTLINE = (
    f"QPushButton {{ background-color: transparent; color: {COLORS['ink_soft']};"
    f" border: 1px solid {COLORS['rule']}; border-radius: 6px;"
    f" padding: 7px 14px; font-size: 12px; }}"
    f"QPushButton:hover {{ border-color: {COLORS['accent']}; color: {COLORS['accent']}; }}"
    f"QPushButton:pressed {{ background-color: {COLORS['accent_soft']};"
    f" padding: 8px 14px 6px 14px; }}"
)


class CollapsibleSection(QWidget):
    """可折叠分区：▸/▾ 标题行 + 可隐藏内容区（默认收起）。

    用于把低频分区（基线主权 / 个人模型 / 回顾历史）放入隐藏式，
    主界面只保留核心闭环（状态 / 曲线 / 调节 / 纠正）。
    """

    def __init__(self, title, content, parent=None, expanded=False):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = QPushButton(("▾  " if expanded else "▸  ") + title)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setStyleSheet(
            f"QPushButton {{ text-align: left; background: transparent;"
            f" border: none; border-bottom: 1px solid {COLORS['rule']};"
            f" color: {COLORS['ink_soft']}; font-size: 12px;"
            f" padding: 8px 4px; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ color: {COLORS['accent']}; }}"
        )
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)

        self.content = content
        self.content.setVisible(expanded)
        layout.addWidget(content)

        self._title = title
        self._expanded = expanded

    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded):
        self._expanded = expanded
        self.content.setVisible(expanded)
        self.header.setText(("▾  " if expanded else "▸  ") + self._title)

    def is_expanded(self):
        return self._expanded


class ConsoleWindow(QWidget):
    """2.0 单页控制台：全部内容集中一页。"""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session

        # ---- 2.0 内核会话（内存态）----
        self.params = ModelParameters()
        self.baseline_ctrl = BaselineController()
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.calibrator = Calibrator()
        self.observations = []
        self.states = []          # [(ts, EmotionState)]
        self.recording = False

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._sample)

        self.sections = {}
        self._setup_ui()
        self._refresh_readouts()

    # ================================================================
    # UI
    # ================================================================

    def _setup_ui(self):
        self.setObjectName("ConsolePage")
        self.setStyleSheet(f"QWidget#ConsolePage {{ background-color: {COLORS['bg']}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QScrollBar:vertical {{ width: 8px; background: transparent; }}"
            f"QScrollBar::handle:vertical {{ background: {COLORS['rule']};"
            f" border-radius: 4px; min-height: 30px; }}"
        )
        body = QWidget()
        body.setObjectName("ConsoleBody")
        body.setStyleSheet("QWidget#ConsoleBody { background: transparent; }")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 16)
        layout.setSpacing(10)

        layout.addWidget(self._section_state())
        layout.addWidget(self._section_curve())
        layout.addWidget(self._section_adjust())
        layout.addWidget(self._section_correction())

        # 隐藏式分区（默认折叠，按需展开）
        self.collapsibles = {}
        for key, title, content in [
            ("baseline", "基线主权 · 这是我的『正常』", self._section_baseline()),
            ("model", "个人模型 · 越用越懂你", self._section_model()),
            ("legacy", "回顾与历史", self._section_legacy()),
        ]:
            sec = CollapsibleSection(title, content, expanded=False)
            self.collapsibles[key] = sec
            layout.addWidget(sec)
        layout.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll)
        self._scroll = scroll
        self._body = body

    # ---------- ① 实时状态（裸读数条，无重卡片） ----------
    def _section_state(self):
        strip = QWidget()
        strip.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(strip)
        lay.setContentsMargins(2, 2, 2, 8)
        lay.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(26)
        self.st_v = StatBlock("Valence 效价")
        self.st_a = StatBlock("Arousal 唤醒")
        self.st_i = StatBlock("Intensity 强度")
        self.st_s = StatBlock("Stability 稳定")
        self.st_c = StatBlock("Confidence 置信")
        self.st_t = StatBlock("Trend 趋势")
        for b in [self.st_v, self.st_a, self.st_i, self.st_s, self.st_c, self.st_t]:
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.ci_label = QLabel("置信区间 —")
        self.ci_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 10px; letter-spacing: 0.5px;"
        )
        lay.addWidget(self.ci_label)
        lay.addWidget(hline())

        self.sections['state'] = strip
        return strip

    # ---------- ② 情绪曲线（视觉中心） ----------
    def _section_curve(self):
        panel = QFrame()
        panel.setObjectName("CurvePanel")
        panel.setStyleSheet(
            f"QFrame#CurvePanel {{ background-color: {COLORS['surface']};"
            f" border: 1px solid {COLORS['rule']}; border-radius: 6px; }}"
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)

        cap = QHBoxLayout()
        left = QLabel("EMOTION CURVE · 模型估计")
        left.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 10px; letter-spacing: 2px;"
        )
        legend = QLabel("— 模型   ░ ±1σ 置信带   ┄ 基线   · 原始")
        legend.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 9px;")
        cap.addWidget(left)
        cap.addStretch(1)
        cap.addWidget(legend)
        lay.addLayout(cap)

        self.curve = EmotionCurveWidget()
        self.curve.setMinimumHeight(240)
        lay.addWidget(self.curve)

        self.sections['curve'] = panel
        return panel

    # ---------- ③ 实时调节 ----------
    def _section_adjust(self):
        card = CardFrame("实时调节 · 1Hz 采样")
        grid = QGridLayout()
        grid.setSpacing(8)

        self.v_label = QLabel("效价 60")
        self.v_label.setFixedWidth(56)
        self.v_label.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 12px;")
        self.v_slider = QSlider(Qt.Horizontal)
        self.v_slider.setRange(0, 100); self.v_slider.setValue(60)
        self.v_slider.setStyleSheet(_SLIDER_QSS)
        self.v_slider.valueChanged.connect(lambda v: self.v_label.setText(f"效价 {v}"))

        self.a_label = QLabel("唤醒 30")
        self.a_label.setFixedWidth(56)
        self.a_label.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 12px;")
        self.a_slider = QSlider(Qt.Horizontal)
        self.a_slider.setRange(0, 100); self.a_slider.setValue(30)
        self.a_slider.setStyleSheet(_SLIDER_QSS)
        self.a_slider.valueChanged.connect(lambda v: self.a_label.setText(f"唤醒 {v}"))

        grid.addWidget(self.v_label, 0, 0); grid.addWidget(self.v_slider, 0, 1)
        grid.addWidget(self.a_label, 1, 0); grid.addWidget(self.a_slider, 1, 1)
        card._content_layout.addLayout(grid)

        row = QHBoxLayout()
        self.btn_record = QPushButton("开始记录")
        self.btn_record.setStyleSheet(_BTN_PRIMARY)
        self.btn_record.clicked.connect(self._toggle_recording)
        row.addWidget(self.btn_record)
        self.sample_count = QLabel("0 个观察")
        self.sample_count.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        row.addWidget(self.sample_count)
        row.addStretch(1)
        card._content_layout.addLayout(row)

        self.sections['adjust'] = card
        return card

    # ---------- ④ 基线主权 ----------
    def _section_baseline(self):
        card = CardFrame("基线主权 · 这是我的『正常』")
        row = QHBoxLayout()
        row.setSpacing(14)
        self.bl_v = StatBlock("基线 效价")
        self.bl_a = StatBlock("基线 唤醒")
        self.bl_regime = StatBlock("Regime")
        for b in [self.bl_v, self.bl_a, self.bl_regime]:
            row.addWidget(b)
        row.addStretch(1)
        card._content_layout.addLayout(row)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.btn_nudge_up = QPushButton("微调 +")
        self.btn_nudge_up.setStyleSheet(_BTN_OUTLINE)
        self.btn_nudge_up.clicked.connect(lambda: self._nudge(+0.05))
        self.btn_nudge_dn = QPushButton("微调 −")
        self.btn_nudge_dn.setStyleSheet(_BTN_OUTLINE)
        self.btn_nudge_dn.clicked.connect(lambda: self._nudge(-0.05))
        self.btn_fork = QPushButton("分叉（新起点）")
        self.btn_fork.setStyleSheet(_BTN_OUTLINE)
        self.btn_fork.clicked.connect(self._fork)
        self.btn_reset = QPushButton("重置")
        self.btn_reset.setStyleSheet(_BTN_OUTLINE)
        self.btn_reset.clicked.connect(self._reset_baseline)
        for b in [self.btn_nudge_up, self.btn_nudge_dn, self.btn_fork, self.btn_reset]:
            btns.addWidget(b)
        btns.addStretch(1)
        card._content_layout.addLayout(btns)

        self.sections['baseline'] = card
        return card

    # ---------- ⑤ 个人模型 ----------
    def _section_model(self):
        card = CardFrame("个人模型 · 越用越懂你")
        row = QHBoxLayout()
        row.setSpacing(14)
        self.pm_n = StatBlock("已学习事件")
        self.pm_ell = StatBlock("惯性 ℓ(s)")
        self.pm_stage = StatBlock("阶段")
        self.pm_shrink = StatBlock("个人化程度")
        for b in [self.pm_n, self.pm_ell, self.pm_stage, self.pm_shrink]:
            row.addWidget(b)
        row.addStretch(1)
        card._content_layout.addLayout(row)

        btns = QHBoxLayout()
        self.btn_learn = QPushButton("学习一步")
        self.btn_learn.setStyleSheet(_BTN_PRIMARY)
        self.btn_learn.clicked.connect(self._learn)
        btns.addWidget(self.btn_learn)
        self.learn_note = QLabel("积累纠正后点击学习，模型会逐渐贴近你")
        self.learn_note.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        btns.addWidget(self.learn_note)
        btns.addStretch(1)
        card._content_layout.addLayout(btns)

        self.sections['model'] = card
        return card

    # ---------- ⑥ 纠正 ----------
    def _section_correction(self):
        card = CardFrame("纠正 · 你拥有最终解释权")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.cv_label = QLabel("我认为效价是")
        self.cv_label.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 12px;")
        self.cv_slider = QSlider(Qt.Horizontal)
        self.cv_slider.setRange(0, 100); self.cv_slider.setValue(50)
        self.cv_slider.setStyleSheet(_SLIDER_QSS)
        self.cv_val = QLabel("0.50")
        self.cv_val.setFixedWidth(40)
        self.cv_val.setStyleSheet(f"color: {COLORS['ink']}; font-size: 12px;")
        self.cv_slider.valueChanged.connect(
            lambda v: self.cv_val.setText(f"{v/100:.2f}"))
        self.btn_correct = QPushButton("提交纠正")
        self.btn_correct.setStyleSheet(_BTN_OUTLINE)
        self.btn_correct.clicked.connect(self._submit_correction)
        row.addWidget(self.cv_label)
        row.addWidget(self.cv_slider, 1)
        row.addWidget(self.cv_val)
        row.addWidget(self.btn_correct)
        card._content_layout.addLayout(row)

        self.correct_hint = QLabel("")
        self.correct_hint.setStyleSheet(
            f"color: {COLORS['warn']}; font-size: 11px;"
        )
        card._content_layout.addWidget(self.correct_hint)

        self.correction_list = QListWidget()
        self.correction_list.setFixedHeight(72)
        self.correction_list.setStyleSheet(
            f"QListWidget {{ background-color: {COLORS['surface']}; border: none;"
            f" color: {COLORS['ink']}; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 3px 2px; }}"
        )
        card._content_layout.addWidget(self.correction_list)
        self.correction_empty = QLabel("还没有纠正记录 —— 记录后若模型不准，在这里提交你的判断")
        self.correction_empty.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 11px; padding: 6px 2px;"
        )
        card._content_layout.addWidget(self.correction_empty)

        self.sections['correction'] = card
        return card

    # ---------- ⑦⑧ 旧内容（嵌入，保留全部） ----------
    def _section_legacy(self):
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.summary_embed = EventSummaryWindow(self.session, parent=self)
        layout.addWidget(self.summary_embed)
        self.sections['summary'] = self.summary_embed

        self.history_embed = HistoryWindow(self.session, parent=self)
        layout.addWidget(self.history_embed)
        self.sections['history'] = self.history_embed

        return wrap

    # ================================================================
    # 行为
    # ================================================================

    def scroll_to(self, name):
        # 若目标在折叠分区内，先展开
        key = {"summary": "legacy", "history": "legacy", "legacy": "legacy"}.get(name, name)
        sec = self.collapsibles.get(key)
        if sec is not None and not sec.is_expanded():
            sec.set_expanded(True)
        target = self.sections.get(name)
        if target is None and key == "legacy":
            target = self.sections.get("summary")
        if target is None:
            return
        self._scroll.ensureWidgetVisible(target, 20, 20)

    def _toggle_recording(self):
        self.recording = not self.recording
        if self.recording:
            self.btn_record.setText("停止记录")
            self.timer.start(1000)
        else:
            self.btn_record.setText("开始记录")
            self.timer.stop()

    def _sample(self):
        ts = time.time()
        v = self.v_slider.value() / 100.0
        a = self.a_slider.value() / 100.0
        obs = Observation(timestamp=ts, valence=v, arousal=a,
                          source=ObservationSource.USER)
        self.observations.append(obs)
        if not self.estimator.is_initialized:
            self.estimator.initialize(timestamp=ts, valence=v, arousal=a)
        state = self.estimator.update(obs)
        self.states.append((ts, state))
        self._refresh_readouts()
        self._refresh_curve()
        self.sample_count.setText(f"{len(self.observations)} 个观察")

    def _refresh_readouts(self):
        if not self.states:
            return
        ts, st = self.states[-1]
        self.st_v.set_value(f"{st.valence:.2f}")
        self.st_a.set_value(f"{st.arousal:.2f}")
        self.st_i.set_value(f"{st.intensity:.2f}")
        self.st_s.set_value(f"{st.stability:.2f}")
        self.st_c.set_value(f"{int(st.confidence*100)}%")
        self.st_t.set_value(st.trend.value)
        lo, hi = st.confidence_interval("valence", z=1.0)
        self.ci_label.setText(f"效价置信区间 ±1σ：{lo:.2f} ~ {hi:.2f}")

        bl = self.baseline_ctrl.current
        self.bl_v.set_value(f"{bl.valence:.2f}")
        self.bl_a.set_value(f"{bl.arousal:.2f}")
        self.bl_regime.set_value((bl.regime_id or "initial")[:8])

        self.pm_n.set_value(str(self.params.n_events_fitted))
        self.pm_ell.set_value(f"{self.params.ell_valence:.0f}")
        self.pm_stage.set_value(self.params.stage.value)
        self.pm_shrink.set_value(f"{int(self.params.shrinkage_alpha*100)}%")

    def _refresh_curve(self):
        if len(self.states) < 2:
            return
        ts_list = [t for t, _ in self.states]
        mv = [s.valence for _, s in self.states]
        ma = [s.arousal for _, s in self.states]
        vv = [s.variance_valence for _, s in self.states]
        va = [s.variance_arousal for _, s in self.states]
        raw = [(o.timestamp, o.valence) for o in self.observations]
        self.curve.set_data(ts_list, mv, ma, vv, va,
                            baseline_v=self.baseline_ctrl.current.valence,
                            raw=raw)

    # ---- 基线主权 ----
    def _nudge(self, delta):
        bl = self.baseline_ctrl.current
        self.baseline_ctrl.nudge(valence=bl.valence + delta)
        self.estimator.baseline = self.baseline_ctrl.current
        self._refresh_readouts()
        self._refresh_curve()

    def _fork(self):
        bl = self.baseline_ctrl.current
        self.baseline_ctrl.fork(time.time(), new_baseline=Baseline(
            valence=bl.valence, arousal=bl.arousal))
        self.estimator.baseline = self.baseline_ctrl.current
        self._refresh_readouts()

    def _reset_baseline(self):
        from PyQt5.QtWidgets import QMessageBox
        ok = QMessageBox.question(
            self, "重置基线",
            "重置会清空个人基线与 regime，回到群体先验。此操作不可撤销，继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self.baseline_ctrl.reset()
        self.estimator.baseline = self.baseline_ctrl.current
        self._refresh_readouts()

    # ---- 个人学习 ----
    def _learn(self):
        self.params = self.calibrator.learn(self.params)
        self.estimator.params = self.params
        self._refresh_readouts()

    # ---- 纠正 ----
    def _submit_correction(self):
        if not self.states:
            self.correct_hint.setText("先点「开始记录」采集数据，才能提交纠正。")
            return
        self.correct_hint.setText("")
        ts, st = self.states[-1]
        corrected_v = self.cv_slider.value() / 100.0
        corr = UserCorrection(
            timestamp=ts,
            dimension=CorrectionDimension.VALENCE,
            predicted_value=st.valence,
            corrected_value=corrected_v,
            source=CorrectionSource.MANUAL,
            created_at=time.time(),
        )
        self.calibrator.ingest_correction(corr)
        self.correction_list.insertItem(
            0, f"模型 {st.valence:.2f} → 你 {corrected_v:.2f}"
        )
        self.correction_empty.setVisible(False)
        self.correction_list.setVisible(True)
        if self.correction_list.count() > 20:
            self.correction_list.takeItem(self.correction_list.count() - 1)
