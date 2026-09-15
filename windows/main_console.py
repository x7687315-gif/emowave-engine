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
import time

from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, QFrame,
    QSizePolicy, QGraphicsOpacityEffect, QToolButton, QButtonGroup, QScrollArea,
)
from PyQt5.QtGui import QFont

from theme import COLORS, app_font, app_font_num, METRICS, EASE_OUT, DUR_MID
from curve_widget import EmotionCurveWidget

from emowave import (
    Observation, ObservationSource, ModelParameters, UserCorrection,
    CorrectionDimension, CorrectionSource,
)
from emowave.core.domain.baseline import Baseline
from emowave.core.estimator.estimator import StateEstimator
from emowave.core.calibration.baseline_control import BaselineController
from emowave.core.calibration.calibrator import Calibrator

# 子模块（抽屉、首项、上滑框）
from .drawer import SideDrawer
from .baseline_tools import BaselineToolsCard
from .model_card import ModelCard
from .legacy_embed import LegacyEmbed
from .correction_sheet import CorrectionSheet


# ================================================================
# 状态名派生（按 V×A 象限；不作诊断/评判）
# ================================================================
def derive_state_name(valence: float, arousal: float) -> str:
    """由 0–1 的 V/A 返回中文状态名（不含主观评价）。"""
    v = valence >= 0.5
    a = arousal >= 0.5
    if v and a:
        return "兴奋 · 活跃"
    if v and not a:
        return "平静 · 偏积极"
    if not v and a:
        return "紧张 · 焦虑"
    # V<0.5 & A<0.5
    return "低落 · 疲惫"


# ================================================================
# ① 当前情绪状况（状态名 + 参数说明 + 纠正入口）
# ================================================================
class StatusCard(QWidget):
    """顶读数条上方的状态卡：状态名 + 右侧纠正入口 + 参数说明下拉。"""

    def __init__(self, on_correction_clicked, parent=None):
        super().__init__(parent)
        self._on_correction_clicked = on_correction_clicked

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 第一行：状态名 + 纠正入口
        row1 = QHBoxLayout()
        row1.setContentsMargins(22, 18, 22, 0)
        row1.setSpacing(0)

        self.state_label = QLabel("—")
        f = app_font(22, QFont.Medium)
        self.state_label.setFont(f)
        self.state_label.setStyleSheet(f"color: {COLORS['ink']};")
        row1.addWidget(self.state_label)

        self.state_hint = QLabel(" · 模型估计（不替你定义）")
        self.state_hint.setFont(app_font(11))
        self.state_hint.setStyleSheet(f"color: {COLORS['muted']};")
        row1.addWidget(self.state_hint, 1, Qt.AlignBottom | Qt.AlignLeft)

        # 纠正入口（"· 情绪有误，点这里纠正"）
        self.corr_btn = QPushButton("· 情绪有误，点这里纠正")
        self.corr_btn.setCursor(Qt.PointingHandCursor)
        self.corr_btn.setFlat(True)
        self.corr_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {COLORS['rule']};"
            f" color: {COLORS['ink_2']}; padding: 4px 10px; font-size: 11px;"
            f" border-radius: 0; }}"
            f"QPushButton:hover {{ border-color: {COLORS['accent']};"
            f" color: {COLORS['accent']}; background: {COLORS['accent_soft']}; }}"
        )
        self.corr_btn.clicked.connect(self._on_correction_clicked)
        row1.addWidget(self.corr_btn, 0, Qt.AlignRight | Qt.AlignBottom)
        outer.addLayout(row1)

        # 第二行：参数说明下拉
        row2 = QHBoxLayout()
        row2.setContentsMargins(22, 2, 22, 0)
        row2.setSpacing(0)

        self.params_btn = QToolButton()
        self.params_btn.setText("▾  参数说明")
        self.params_btn.setCursor(Qt.PointingHandCursor)
        self.params_btn.setCheckable(True)
        self.params_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none;"
            f" color: {COLORS['ink_2']}; font-size: 10.5px; padding: 0;"
            f" letter-spacing: 0.14em; text-transform: uppercase;"
            f" font-weight: 600; }}"
            f"QToolButton:hover {{ color: {COLORS['accent']}; }}"
        )
        self.params_btn.toggled.connect(self._toggle_params)
        row2.addWidget(self.params_btn)
        row2.addStretch(1)
        outer.addLayout(row2)

        # 参数说明折叠面板
        self.params_panel = QFrame()
        self.params_panel.setStyleSheet(
            f"QFrame {{ background: {COLORS['surface']};"
            f" border-top: 1px solid {COLORS['rule']};"
            f" border-bottom: 1px solid {COLORS['rule']}; }}"
        )
        pl = QHBoxLayout(self.params_panel)
        pl.setContentsMargins(22, 14, 22, 14)
        pl.setSpacing(0)
        self._params_grid = QHBoxLayout()
        self._params_grid.setSpacing(0)
        pl.addLayout(self._params_grid)
        self.params_panel.setMaximumHeight(0)
        self.params_panel.setMinimumHeight(0)
        outer.addWidget(self.params_panel)

        self._params_expanded = False

    def _toggle_params(self, checked):
        self._params_expanded = checked
        self.params_btn.setText("▴  参数说明" if checked else "▾  参数说明")
        target = 220 if checked else 0
        self._anim = QPropertyAnimation(self.params_panel, b"maximumHeight")
        self._anim.setDuration(DUR_MID)
        self._anim.setStartValue(self.params_panel.maximumHeight())
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.OutQuart))
        self._anim.start()
        # minimumHeight 也跟随（否则布局可能压扁内容）
        self._anim2 = QPropertyAnimation(self.params_panel, b"minimumHeight")
        self._anim2.setDuration(DUR_MID)
        self._anim2.setStartValue(self.params_panel.minimumHeight())
        self._anim2.setEndValue(target)
        self._anim2.setEasingCurve(QEasingCurve(QEasingCurve.OutQuart))
        self._anim2.start()

    def update_state(self, valence, arousal, params_text):
        """更新状态名 + 参数说明当前值。"""
        self.state_label.setText(derive_state_name(valence, arousal))
        if self._params_expanded:
            self._render_params_grid(valence, arousal, params_text)

    def _render_params_grid(self, valence, arousal, params_text):
        # 清除旧内容
        while self._params_grid.count():
            item = self._params_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        cells = [
            ("效价 VALENCE", f"{int(valence*100)}", "正负方向", f"={int(valence*100)}/100"),
            ("唤醒 AROUSAL", f"{int(arousal*100)}", "强度 · 能量", f"={int(arousal*100)}/100"),
            ("置信 CONFIDENCE", params_text.get('confidence', '—'), "0–1 把握度",
             params_text.get('confidence_desc', '')),
            ("置信区间 ±1σ", params_text.get('ci', '—'), "模型估计范围",
             params_text.get('ci_desc', '')),
        ]
        for i, (k, v, d, cur) in enumerate(cells):
            cell = QFrame()
            cell.setStyleSheet("background: transparent; border: none;")
            lay = QVBoxLayout(cell)
            lay.setContentsMargins(0 if i == 0 else 18, 0, 0, 0)
            lay.setSpacing(3)
            k_lbl = QLabel(k)
            k_lbl.setFont(app_font(9, QFont.DemiBold))
            k_lbl.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.4px;")
            v_lbl = QLabel(v)
            v_lbl.setFont(app_font_num(20, QFont.Light))
            v_lbl.setStyleSheet(f"color: {COLORS['ink']};")
            d_lbl = QLabel(d)
            d_lbl.setFont(app_font(10))
            d_lbl.setStyleSheet(f"color: {COLORS['muted']};")
            c_lbl = QLabel(cur)
            c_lbl.setFont(app_font(10))
            c_lbl.setStyleSheet(f"color: {COLORS['accent_ink']};")
            lay.addWidget(k_lbl)
            lay.addWidget(v_lbl)
            lay.addWidget(d_lbl)
            lay.addWidget(c_lbl)
            self._params_grid.addWidget(cell)
        self._params_grid.addStretch(1)


# ================================================================
# ② 读数条
# ================================================================
class ReadoutBar(QWidget):
    """轻量读数条：4 个大数值等距，尾部置信区间说明。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 8, 22, 8)
        lay.setSpacing(0)
        self.v_lbl = self._make_cell("效价 VALENCE")
        self.a_lbl = self._make_cell("唤醒 AROUSAL")
        self.c_lbl = self._make_cell("置信 CONFIDENCE")
        self.ci_lbl = self._make_cell("置信区间 ±1σ")
        for lbl in (self.v_lbl, self.a_lbl, self.c_lbl, self.ci_lbl):
            lay.addWidget(lbl, 1)
        self.setFixedHeight(METRICS['readout_h'])

    def _make_cell(self, key):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 24, 4)
        v.setSpacing(4)
        k = QLabel(key)
        k.setFont(app_font(9, QFont.DemiBold))
        k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.6px;")
        big = QLabel("—")
        big.setFont(app_font_num(22, QFont.Light))
        big.setStyleSheet(f"color: {COLORS['ink']};")
        big.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        v.addWidget(k)
        v.addWidget(big)
        # 把大数字标签挂到 widget 上方便外部读
        w.value_label = big
        return w

    def update_values(self, valence, arousal, confidence, ci_text):
        self.v_lbl.value_label.setText(f"{int(valence*100)}")
        self.a_lbl.value_label.setText(f"{int(arousal*100)}")
        self.c_lbl.value_label.setText(f"{int(confidence*100)}%")
        self.ci_lbl.value_label.setText(ci_text)


# ================================================================
# ④ 输入台（双滑块 + 记录按钮）
# ================================================================
class InputPanel(QWidget):
    """双滑块 + 开始/停止记录 + 采样数。"""

    def __init__(self, on_toggle, parent=None):
        super().__init__(parent)
        self._on_toggle = on_toggle

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 16, 22, 16)
        outer.setSpacing(14)

        # 行 1：效价滑块
        self.v_row = self._make_slider_row("效价", 50, "50/100")
        outer.addLayout(self.v_row['layout'])
        # 行 2：唤醒滑块
        self.a_row = self._make_slider_row("唤醒", 40, "40/100")
        outer.addLayout(self.a_row['layout'])

        # 行 3：操作
        ctrl = QHBoxLayout()
        ctrl.setSpacing(14)
        self.btn_record = QPushButton("●  开始记录")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_record.setProperty("primary", True)
        self.btn_record.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; color: {COLORS['surface']};"
            f" border: none; padding: 11px 22px; font-size: 11.5px;"
            f" font-weight: 600; letter-spacing: 1.6px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['accent_deep']}; }}"
        )
        self.btn_record.clicked.connect(self._toggle_recording)
        ctrl.addWidget(self.btn_record)

        self.count_label = QLabel("0 个观察")
        self.count_label.setFont(app_font(11))
        self.count_label.setStyleSheet(f"color: {COLORS['muted']};")
        ctrl.addWidget(self.count_label)
        ctrl.addStretch(1)

        self.tip_label = QLabel("拖动滑块即时采样，1 Hz 喂给模型估计器")
        self.tip_label.setFont(app_font(10))
        self.tip_label.setStyleSheet(f"color: {COLORS['muted']};")
        ctrl.addWidget(self.tip_label)
        outer.addLayout(ctrl)

        self.v_row['slider'].valueChanged.connect(
            lambda v: self.v_row['num'].setText(f"{v}/100"))
        self.a_row['slider'].valueChanged.connect(
            lambda v: self.a_row['num'].setText(f"{v}/100"))

    def _make_slider_row(self, key, default, num_text):
        layout = QHBoxLayout()
        layout.setSpacing(14)
        k = QLabel(key)
        k.setFont(app_font(11))
        k.setStyleSheet(f"color: {COLORS['ink_2']};")
        k.setFixedWidth(56)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(default)
        num = QLabel(num_text)
        num.setFont(app_font_num(12, QFont.Light))
        num.setStyleSheet(f"color: {COLORS['muted']};")
        num.setFixedWidth(58)
        layout.addWidget(k)
        layout.addWidget(slider, 1)
        layout.addWidget(num)
        return {'layout': layout, 'slider': slider, 'num': num, 'key': key}

    def _toggle_recording(self):
        # running = 当前按钮显示的是「停止」→ 正在记录
        running = self.btn_record.text().startswith("■")
        self._on_toggle(not running)
        self.set_recording(not running)  # 用翻转后的新状态同步按钮文案

    def set_recording(self, running: bool):
        if running:
            self.btn_record.setText("■  停止记录")
            self.btn_record.setStyleSheet(
                f"QPushButton {{ background: {COLORS['accent_deep']};"
                f" color: {COLORS['surface']}; border: none; padding: 11px 22px;"
                f" font-size: 11.5px; font-weight: 600; letter-spacing: 1.6px; }}"
            )
        else:
            self.btn_record.setText("●  开始记录")
            self.btn_record.setStyleSheet(
                f"QPushButton {{ background: {COLORS['accent']};"
                f" color: {COLORS['surface']}; border: none; padding: 11px 22px;"
                f" font-size: 11.5px; font-weight: 600; letter-spacing: 1.6px; }}"
                f"QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
            )

    def get_values(self):
        return (self.v_row['slider'].value() / 100.0,
                self.a_row['slider'].value() / 100.0)

    def set_count(self, n):
        self.count_label.setText(f"{n} 个观察")


# ================================================================
# 主：MainConsole
# ================================================================
class MainConsole(QWidget):
    """新版主界面：4 区块 + 抽屉。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        # ---- 2.0 内核会话 ----
        self.params = ModelParameters()
        self.baseline_ctrl = BaselineController()
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.calibrator = Calibrator()
        self.observations = []
        self.states = []
        self.recording = False

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._sample)

        self._build()
        self._refresh_readouts()

    # ================================================================
    # 布局
    # ================================================================
    def _build(self):
        # 左：主界面（4 区块）；右：抽屉
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 主区 ----
        main = QWidget()
        main.setStyleSheet(f"background: {COLORS['paper']};")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        # ① 当前情绪状况
        self.status = StatusCard(on_correction_clicked=self._open_correction_sheet)
        ml.addWidget(self.status)

        # ② 读数条
        self.readout = ReadoutBar()
        ml.addWidget(self.readout)

        # ③ 曲线（视觉主角）
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
        self.curve.setMinimumHeight(280)
        cf.addWidget(self.curve)
        ml.addWidget(curve_frame, 1)

        # ④ 输入台
        self.input_panel = InputPanel(on_toggle=self._toggle_recording)
        # 输入台用一条顶部细边与曲线分隔
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
        ml.addWidget(sep)
        ml.addWidget(self.input_panel)

        outer.addWidget(main, 1)

        # ---- 抽屉（首期隐藏）----
        self.drawer = SideDrawer(width=METRICS['drawer_w'])
        outer.addWidget(self.drawer)

        # 抽屉内 4 项
        self.baseline_card = BaselineToolsCard(self.baseline_ctrl, self.estimator)
        model_card = ModelCard(self.params, self.calibrator,
                               on_learn=self._learn)
        legacy = LegacyEmbed()
        self.drawer.add_section(self.baseline_card)
        self.drawer.add_section(model_card)
        self.drawer.add_section(legacy)

    # ================================================================
    # 抽屉开合
    # ================================================================
    def open_drawer(self):
        self.drawer.show_drawer()

    def close_drawer(self):
        self.drawer.hide_drawer()

    def toggle_drawer(self):
        # 必须用 drawer.is_open()（内部 _visible 标志），不能用 isVisible()：
        # 抽屉靠 maximumWidth 0↔380 收放，Qt 始终认为它 visible，
        # 用 isVisible() 判断会导致第二次 toggle 关不掉。
        self.drawer.toggle()

    # ================================================================
    # 纠正上滑框
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
        """把「时刻 → (v, a)」的纠正列表写进 calibrator 数据集。

        单独抽出：上滑框是模态的，测试无法走 exec_()，直接喂本方法即可。

        注意 **UserCorrection 是单维度的**（dimension + predicted/corrected
        单个数值），没有 corrected_valence/corrected_arousal 双字段——
        所以一次拖点若同时改了效价和唤醒，会产出 2 条纠正记录。
        """
        if not applied:
            return 0
        if source is None:
            source = CorrectionSource.DRAG

        n = 0
        for ts, v, a in applied:
            pred = next((s for t, s in self.states if abs(t - ts) < 0.5), None)
            if pred is None:
                continue
            for dim, pv, cv in (
                (CorrectionDimension.VALENCE, pred.valence, v),
                (CorrectionDimension.AROUSAL, pred.arousal, a),
            ):
                if abs(cv - pv) < 1e-9:
                    continue        # 该维度没动，不产生纠正
                self.calibrator.ingest_correction(UserCorrection(
                    timestamp=ts,
                    dimension=dim,
                    predicted_value=pv,
                    corrected_value=cv,
                    source=source,
                    created_at=time.time(),
                ))
                n += 1
        return n

    # ================================================================
    # 基线主权（薄封装，委托给抽屉里的 BaselineToolsCard）
    # ================================================================
    def _nudge(self, delta: float):
        self.baseline_card._nudge(delta)

    def _fork(self):
        self.baseline_card._fork()

    def _reset_baseline(self):
        self.baseline_card._reset()

    def observations_ts_for_curve(self):
        return [t for t, _ in self.states]

    # ================================================================
    # 数据采集
    # ================================================================
    def _toggle_recording(self, want_running=None):
        """切换（或强制设置）记录状态。

        want_running=None → 按当前状态取反；否则按给定值设置。
        """
        if want_running is None:
            want_running = not self.recording
        self.recording = want_running
        self.input_panel.set_recording(want_running)
        if want_running:
            self.timer.start(1000)
        else:
            self.timer.stop()

    def _sample(self):
        ts = time.time()
        v, a = self.input_panel.get_values()
        obs = Observation(timestamp=ts, valence=v, arousal=a,
                          source=ObservationSource.USER)
        self.observations.append(obs)
        if not self.estimator.is_initialized:
            self.estimator.initialize(timestamp=ts, valence=v, arousal=a)
        state = self.estimator.update(obs)
        self.states.append((ts, state))
        self.input_panel.set_count(len(self.observations))
        self._refresh_readouts()
        self._refresh_curve()

    # ================================================================
    # 学习（个人模型）
    # ================================================================
    def _learn(self):
        self.params = self.calibrator.learn(self.params)
        self.estimator.params = self.params

    # ================================================================
    # 刷新
    # ================================================================
    def _refresh_readouts(self):
        if not self.states:
            return
        ts, st = self.states[-1]
        lo, hi = st.confidence_interval("valence", z=1.0)
        ci = f"{int(lo*100)} – {int(hi*100)}"
        self.readout.update_values(
            valence=st.valence,
            arousal=st.arousal,
            confidence=st.confidence,
            ci_text=ci,
        )
        self.status.update_state(
            valence=st.valence,
            arousal=st.arousal,
            params_text={
                'confidence': f"{int(st.confidence*100)}%",
                'confidence_desc': f"={int(st.confidence*100)}%",
                'ci': ci,
                'ci_desc': f"估 {int(st.valence*100)}±{int((hi-lo)*50)}",
            },
        )

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