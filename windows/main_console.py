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
import json
import math
import uuid
import logging

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
    get_archetype, DEFAULT_ARCHETYPE_KEY,
)
from emowave.core.domain.baseline import Baseline
from emowave.core.estimator.estimator import StateEstimator
from emowave.core.curve.smoother import RTSSmoother
from emowave.core.calibration.baseline_control import BaselineController
from emowave.core.calibration.calibrator import Calibrator

# 子模块（抽屉、首项、上滑框）
from .drawer import SideDrawer
from .baseline_tools import BaselineToolsCard
from .model_card import ModelCard
from .legacy_embed import LegacyEmbed
from .correction_sheet import CorrectionSheet

logger = logging.getLogger(__name__)

# ---- 情绪波生命周期常量（记录→衰减回基线→消失→待下次记录）----
SETTLE_EPS = 0.05        # 距基线小于此值视为"回归基线"
SETTLE_MIN_SEC = 3.0     # 至少衰减这么久才允许判定结束（避免刚停就消失）
MAX_DECAY_SEC = 600.0    # 衰减外推的硬上限（防止无限外推）
DECAY_DT = 1.0           # 每个定时器 tick 向前推演的秒数（实时、无需后台进程）


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
        self.btn_record.clicked.connect(lambda: self._on_toggle())
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
        elif mode == "decaying":
            btn.setText("◌  推演中…")
            btn.setEnabled(False)
            btn.setStyleSheet(self._BTN_DECAY)
        else:  # idle
            btn.setText("●  开始记录")
            btn.setEnabled(True)
            btn.setStyleSheet(self._BTN_PRIMARY)

    def set_recording(self, running: bool):
        """兼容旧接口：True→recording，False→idle。"""
        self.set_mode("recording" if running else "idle")

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

    def __init__(self, parent=None, db=None, archetype_key=None):
        super().__init__(parent)

        # 数据库（事件表数据源）：抽屉「事件回顾/历史记录」与停止记录落库都靠它
        self.db = db

        # 精力人群先验：开局选一类 → 用它 seed 默认曲线（ModelParameters）+ 中心（Baseline）
        self.archetype_key = archetype_key or DEFAULT_ARCHETYPE_KEY
        self.archetype = get_archetype(self.archetype_key)

        # ---- 2.0 内核会话 ----
        self.params = self.archetype.to_params()
        self.baseline_ctrl = BaselineController(initial_baseline=self.archetype.to_baseline())
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.calibrator = Calibrator(population=self.archetype.to_params())
        self.observations = []
        self.states = []
        self.recording = False
        self._segment = []          # 本次「开始→停止记录」采集到的观察，停止时聚合成一条 event
        self._edits = []            # 用户对曲线点的编辑（伪观察），驱动显示曲线的 RTS 重拟合

        # 情绪波生命周期：idle → recording → decaying(外推回基线) → 回归基线后消失 → idle
        self._wave_active = False
        self._last_obs_ts = 0.0
        self._decay_now = 0.0

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

        self._build()
        self._refresh_readouts()
        self._resume_active_wave()   # 上次未走完的波：开机后"慢慢推演"

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
        self.legacy = LegacyEmbed(db=self.db)
        self.drawer.add_section(self.baseline_card)
        self.drawer.add_section(model_card)
        self.drawer.add_section(self.legacy)

    # ================================================================
    # 抽屉开合
    # ================================================================
    def open_drawer(self):
        self.drawer.show_drawer()
        self.legacy.refresh()

    def close_drawer(self):
        self.drawer.hide_drawer()

    def toggle_drawer(self):
        # 必须用 drawer.is_open()（内部 _visible 标志），不能用 isVisible()：
        # 抽屉靠 maximumWidth 0↔380 收放，Qt 始终认为它 visible，
        # 用 isVisible() 判断会导致第二次 toggle 关不掉。
        self.drawer.toggle()
        if self.drawer.is_open():
            self.legacy.refresh()

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
                uc = UserCorrection(
                    timestamp=ts,
                    dimension=dim,
                    predicted_value=pv,
                    corrected_value=cv,
                    source=source,
                    created_at=time.time(),
                )
                self.calibrator.ingest_correction(uc)
                # 同时作为伪观察喂给显示曲线 → 改点即时重拟合整条曲线
                self._edits.append({
                    "timestamp": ts,
                    "channel": dim.value,
                    "value": cv,
                    "reliability_weight": uc.reliability_weight,
                })
                n += 1
        if n:
            self._refresh_curve()
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
        """开始/停止记录。停止后不清空，而是进入"外推衰减回基线"，回归后曲线消失。"""
        if want_running is None:
            want_running = not self.recording
        if want_running:
            if self._wave_active and not self.recording:
                self._end_wave()                 # 上一波还在衰减 → 先收尾
            self.recording = True
            self._wave_active = True
            self._segment = []
            self.input_panel.set_mode("recording")
            self.timer.start(1000)
        else:
            if not self.recording:
                return
            self.recording = False
            self._persist_segment_event()        # 落库 + 自动学习（保留 states/observations）
            if not self.states:                  # 没采到点 → 直接回 idle
                self._end_wave()
                return
            self._last_obs_ts = self.states[-1][0]
            self._decay_now = self._last_obs_ts
            self.input_panel.set_mode("decaying")
            self._store_active_wave()            # 存快照：关掉重开也能"慢慢推演"
            self.timer.start(1000)               # 继续跑 → _decay_step 衰减回基线

    def _tick(self):
        """1Hz 心跳：记录中采样；衰减中向前推演。"""
        if self.recording:
            self._sample()
        elif self._wave_active:
            self._decay_step()

    def _sample(self):
        ts = time.time()
        v, a = self.input_panel.get_values()
        obs = Observation(timestamp=ts, valence=v, arousal=a,
                          source=ObservationSource.USER)
        self.observations.append(obs)
        self._segment.append(obs)
        self._last_obs_ts = ts
        if not self.estimator.is_initialized:
            self.estimator.initialize(timestamp=ts, valence=v, arousal=a)
        state = self.estimator.update(obs)
        self.states.append((ts, state))
        self.input_panel.set_count(len(self.observations))
        self._refresh_readouts()
        self._refresh_curve()

    def _persist_segment_event(self):
        """把本次「开始→停止」采集到的观察聚合成一条 emotion_events 落库。

        v3 的连续记录此前从不写事件表（只有旧 SessionController.process_event 写），
        导致抽屉「事件回顾/历史记录」永远空。这里补上落库，面板才有真实数据。
        """
        if self.db is None or not self._segment:
            return
        seg = self._segment
        peak_obs = max(seg, key=lambda o: o.arousal)
        # Russell 环状模型：中性点 (0.5, 0.5)，强度=到中性点距离 / 最大距离
        def _intensity(o):
            return math.sqrt((o.valence - 0.5) ** 2 + (o.arousal - 0.5) ** 2) / math.sqrt(0.5)
        event = {
            "event_id": uuid.uuid4().hex,
            "start_time": seg[0].timestamp,
            "end_time": seg[-1].timestamp,
            "peak_valence": peak_obs.valence,
            "peak_arousal": peak_obs.arousal,
            "peak_intensity": max(_intensity(o) for o in seg),
            "sample_count": len(seg),
            "trigger_tags": [],
            "coping_methods": [],
            "coping_ratings": {},
            "body_symptoms": [],
            "user_peak_rating": None,
            "raw_data": json.dumps([[o.timestamp, o.valence, o.arousal] for o in seg]),
        }
        try:
            self.db.save_event(event)
        except Exception as exc:                       # 落库失败不打断 UI，仅记日志
            logger.warning("情绪事件落库失败（%d 个采样）：%s", len(seg), exc)
        finally:
            self._segment = []                         # 无论成败都消费掉，避免重复落库
        # 停止即自动个性化一步（用本次记录 + 已有纠正），不再只靠手点「学习一步」
        try:
            self._learn()
            self._refresh_curve()
        except Exception as exc:
            logger.warning("停止记录后自动学习失败：%s", exc)
        self.legacy.refresh()                          # 新事件即时出现在抽屉里

    # ================================================================
    # 情绪波衰减：记录停止后外推回基线，回归即消失（无需后台进程）
    # ================================================================
    def _decay_step(self):
        if not self.states:
            self._end_wave()
            return
        self._decay_now += DECAY_DT
        horizon = self._decay_now - self._last_obs_ts
        if horizon <= 0:
            return
        pts = self._decay_points(horizon)          # 向基线（GP 均值函数）衰减，而非向 0
        self._refresh_curve_wave(pts)
        bv = self.baseline_ctrl.current.valence
        ba = self.baseline_ctrl.current.arousal
        if pts:
            last_v, last_a = pts[-1][1], pts[-1][2]
        else:
            last_v, last_a = self.states[-1][1].valence, self.states[-1][1].arousal
        settled = (abs(last_v - bv) < SETTLE_EPS and abs(last_a - ba) < SETTLE_EPS
                   and horizon >= SETTLE_MIN_SEC)
        if settled or horizon >= MAX_DECAY_SEC:
            self._end_wave()                       # 回归基线 → 曲线消失

    def _decay_points(self, horizon: float):
        """从末态按 Matérn 位置衰减因子 d(Δt)=e^(-λΔt)(1+λΔt) 外推回基线。

        返回 [(ts, valence, arousal, var_v, var_a)]，与 predict_forward 的
        "偏离空间回复到基线"同一套数学（estimator.extrapolate 是向 0 收，不适用）。
        """
        bv = self.baseline_ctrl.current.valence
        ba = self.baseline_ctrl.current.arousal
        last = self.states[-1][1]
        lv, la = last.valence, last.arousal
        lv_var, la_var = last.variance_valence, last.variance_arousal
        lam_v = self.params.lambda_valence()
        lam_a = self.params.lambda_arousal()
        pts = []
        n = int(horizon // DECAY_DT)
        for i in range(1, n + 1):
            dt = i * DECAY_DT
            dv = math.exp(-lam_v * dt) * (1 + lam_v * dt)
            da = math.exp(-lam_a * dt) * (1 + lam_a * dt)
            v = max(0.0, min(1.0, bv + (lv - bv) * dv))
            a = max(0.0, min(1.0, ba + (la - ba) * da))
            pts.append((self._last_obs_ts + dt, v, a, lv_var, la_var))
        return pts

    def _refresh_curve_wave(self, pts):
        """显示 = 已记录的真实状态 + 外推衰减尾巴（一条会自己往基线收的波）。"""
        ts_list = [t for t, _ in self.states] + [p[0] for p in pts]
        mv = [s.valence for _, s in self.states] + [p[1] for p in pts]
        ma = [s.arousal for _, s in self.states] + [p[2] for p in pts]
        vv = [s.variance_valence for _, s in self.states] + [p[3] for p in pts]
        va = [s.variance_arousal for _, s in self.states] + [p[4] for p in pts]
        raw = [(o.timestamp, o.valence) for o in self.observations]
        self.curve.set_data(ts_list, mv, ma, vv, va,
                            baseline_v=self.baseline_ctrl.current.valence, raw=raw)

    def _end_wave(self):
        """一波结束：曲线消失、读数复位、估计器重置回基线，回到待记录(idle)。"""
        self.timer.stop()
        self.recording = False
        self._wave_active = False
        self.curve.clear()
        self._reset_readouts()
        self.states = []
        self.observations = []
        self._edits = []
        self._segment = []
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.input_panel.set_mode("idle")
        self.input_panel.set_count(0)
        self._clear_active_wave()

    def _reset_readouts(self):
        for lbl in (self.readout.v_lbl, self.readout.a_lbl,
                    self.readout.c_lbl, self.readout.ci_lbl):
            lbl.value_label.setText("—")
        self.status.state_label.setText("—")

    def _store_active_wave(self):
        if self.db is None:
            return
        try:
            snap = {"end_ts": self._last_obs_ts,
                    "samples": [[o.timestamp, o.valence, o.arousal] for o in self.observations]}
            self.db.set_state("active_wave", json.dumps(snap))
        except Exception as exc:
            logger.warning("保存活动情绪波快照失败：%s", exc)

    def _clear_active_wave(self):
        if self.db is None:
            return
        try:
            self.db.set_state("active_wave", "")
        except Exception as exc:
            logger.debug("清除 active_wave 快照失败：%s", exc)

    def _resume_active_wave(self):
        """开机若有未走完的波：重放观察、重建估计器，进入衰减"慢慢推演"。"""
        if self.db is None:
            return
        try:
            raw = self.db.get_state("active_wave", "") or ""
        except Exception as exc:
            logger.debug("读取 active_wave 快照失败：%s", exc)
            return
        if not raw:
            return
        try:
            data = json.loads(raw)
            samples = data.get("samples", [])
            end_ts = float(data.get("end_ts", 0.0))
        except Exception as exc:
            logger.warning("active_wave 快照损坏，已清除：%s", exc)
            self._clear_active_wave()
            return
        if not samples:
            self._clear_active_wave()
            return
        for ts, v, a in samples:
            obs = Observation(timestamp=ts, valence=v, arousal=a,
                              source=ObservationSource.USER)
            self.observations.append(obs)
            if not self.estimator.is_initialized:
                self.estimator.initialize(timestamp=ts, valence=v, arousal=a)
            state = self.estimator.update(obs)
            self.states.append((ts, state))
        self._last_obs_ts = end_ts or self.states[-1][0]
        self._decay_now = self._last_obs_ts
        self._wave_active = True
        self.recording = False
        self.input_panel.set_mode("decaying")
        self.input_panel.set_count(len(self.observations))
        self.timer.start(1000)                         # 前台心跳推演（无后台进程）

    # ================================================================
    # 学习（个人模型）
    # ================================================================
    def _learn(self):
        self.params = self.calibrator.learn(self.params)
        self.estimator.params = self.params

    def set_archetype(self, key: str):
        """切换精力人群先验：用新类的默认曲线 + 基线重新 seed，清空当前会话。

        换人群 = 换起点先验，历史曲线随之作废（从零重新按新先验演化）。
        """
        self.archetype_key = key
        self.archetype = get_archetype(key)
        self.params = self.archetype.to_params()
        self.baseline_ctrl = BaselineController(initial_baseline=self.archetype.to_baseline())
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.calibrator = Calibrator(population=self.archetype.to_params())
        self.observations = []
        self.states = []
        self._segment = []
        self._edits = []
        self.recording = False
        self._wave_active = False
        self._last_obs_ts = 0.0
        self._decay_now = 0.0
        self.timer.stop()
        self.input_panel.set_mode("idle")
        self.input_panel.set_count(0)
        self._reset_readouts()
        self.curve.clear()
        self._clear_active_wave()

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
        if len(self.observations) < 2:
            return
        # 显示曲线 = RTS 非因果平滑（原始观察 + 用户编辑伪观察共同参与）：
        # 用户改任一点，整条曲线按核函数光滑地重新收敛（"拉函数图像"的手感）。
        traj = RTSSmoother(self.params).smooth(self.observations, self._edits)
        raw = [(o.timestamp, o.valence) for o in self.observations]
        self.curve.set_data(traj.timestamps, traj.mean_valence, traj.mean_arousal,
                            traj.var_valence, traj.var_arousal,
                            baseline_v=self.baseline_ctrl.current.valence,
                            raw=raw)