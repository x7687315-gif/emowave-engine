"""windows/baseline_tools.py — 抽屉首项：基线主权

实现 ui-draft-v1.html §02 「基线主权」分区。
三个控件：步进器（−/值/+）+ 分叉（新起点）+ 重置。

调用方传入 BaselineController 和 StateEstimator，按钮触发后写回控制器
并把 estimator.baseline 同步到当前控制器状态。
"""
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PyQt5.QtGui import QFont

from theme import COLORS, app_font, app_font_num
from emowave.core.domain.baseline import Baseline


_STEP = 0.02   # 一次微调的步长


class BaselineToolsCard(QFrame):
    def __init__(self, baseline_ctrl, estimator, parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        self._ctrl = baseline_ctrl
        self._est = estimator

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)

        title = QLabel("基线主权")
        title.setFont(app_font(9, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.8px;")
        lay.addWidget(title)

        desc = QLabel("基线是「你正常状态下的情绪位置」，所有曲线相对它偏离多少就是「今天不一样」的量。")
        desc.setFont(app_font(11))
        desc.setStyleSheet(f"color: {COLORS['ink_2']};")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # 当前基线显示（步进器中央那个值）
        self.current_label = QLabel(self._fmt_current())
        self.current_label.setFont(app_font_num(13, QFont.Light))
        self.current_label.setStyleSheet(f"color: {COLORS['ink']};")
        # 不在这里加单独的 .row，步进器内已有当前值

        # 步进器：− 当前值 +
        step_row = QHBoxLayout()
        step_row.setSpacing(10)
        k = QLabel("步进")
        k.setFont(app_font(10, QFont.DemiBold))
        k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.6px;")
        step_row.addWidget(k)
        delta = QLabel(f"±{_STEP:.2f}")
        delta.setFont(app_font_num(10, QFont.Light))
        delta.setStyleSheet(f"color: {COLORS['accent_ink']};")
        delta.setFixedWidth(34)
        step_row.addWidget(delta)

        # 步进器本体
        stepper = QFrame()
        stepper.setStyleSheet(
            f"QFrame {{ background: {COLORS['surface']};"
            f" border: 1px solid {COLORS['rule']}; }}"
            f"QFrame > QPushButton {{ background: transparent; border: none;"
            f" color: {COLORS['ink_2']}; }}"
            f"QFrame > QPushButton:hover {{ background: {COLORS['accent_soft']};"
            f" color: {COLORS['accent']}; }}"
            f"QFrame > QPushButton:pressed {{ background: {COLORS['accent']};"
            f" color: {COLORS['paper']}; }}"
        )
        stepper_lay = QHBoxLayout(stepper)
        stepper_lay.setContentsMargins(0, 0, 0, 0)
        stepper_lay.setSpacing(0)
        stepper.setFixedHeight(30)

        self.btn_minus = QPushButton("−")
        self.btn_minus.setFixedWidth(32)
        self.btn_minus.setCursor(Qt.PointingHandCursor)
        self.btn_minus.setFont(app_font(13, QFont.Medium))
        self.btn_minus.clicked.connect(lambda: self._nudge(-_STEP))

        self.value_lbl = QLabel(f"{self._ctrl.current.valence:.2f}")
        self.value_lbl.setAlignment(Qt.AlignCenter)
        self.value_lbl.setFont(app_font_num(11, QFont.Light))
        self.value_lbl.setStyleSheet(
            f"color: {COLORS['ink']}; background: {COLORS['paper_2']};"
            f" border-left: 1px solid {COLORS['rule']};"
            f" border-right: 1px solid {COLORS['rule']};"
        )
        self.value_lbl.setMinimumWidth(48)

        self.btn_plus = QPushButton("+")
        self.btn_plus.setFixedWidth(32)
        self.btn_plus.setCursor(Qt.PointingHandCursor)
        self.btn_plus.setFont(app_font(13, QFont.Medium))
        self.btn_plus.clicked.connect(lambda: self._nudge(+_STEP))

        stepper_lay.addWidget(self.btn_minus)
        stepper_lay.addWidget(self.value_lbl, 1)
        stepper_lay.addWidget(self.btn_plus)
        step_row.addWidget(stepper)
        lay.addLayout(step_row)

        # 分叉按钮
        self.btn_fork = QPushButton("◆  分叉（新起点）")
        self.btn_fork.setCursor(Qt.PointingHandCursor)
        self.btn_fork.setStyleSheet(self._neutral_btn_qss())
        self.btn_fork.clicked.connect(self._fork)
        lay.addWidget(self.btn_fork)

        # 重置按钮（danger）
        # 文案里的目标值取真实群体先验（Baseline() 默认 0.55/0.42），
        # 不能硬编码 0.50——那是设计稿写错的值。
        pop = Baseline()
        self.btn_reset = QPushButton(
            f"↺  重置  ·  回到群体先验 {pop.valence:.2f}")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.setProperty("danger", True)
        self.btn_reset.setStyleSheet(self._danger_btn_qss())
        self.btn_reset.clicked.connect(self._reset)
        lay.addWidget(self.btn_reset)

        self._refresh()

    # ================================================================
    # 样式
    # ================================================================
    def _neutral_btn_qss(self):
        return (
            f"QPushButton {{ background: transparent; border: 1px solid {COLORS['rule']};"
            f" color: {COLORS['ink_2']}; padding: 7px 14px; text-align: left;"
            f" font-size: 11.5px; }}"
            f"QPushButton:hover {{ border-color: {COLORS['accent']};"
            f" color: {COLORS['accent']}; background: {COLORS['accent_soft']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['accent_deep']};"
            f" color: {COLORS['paper']}; }}"
        )

    def _danger_btn_qss(self):
        return (
            f"QPushButton {{ background: transparent; border: 1px solid {COLORS['danger']};"
            f" color: {COLORS['danger']}; padding: 7px 14px; text-align: left;"
            f" font-size: 11.5px; }}"
            f"QPushButton:hover {{ background: {COLORS['danger']};"
            f" color: {COLORS['paper']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['accent_deep']}; }}"
        )

    # ================================================================
    # 行为
    # ================================================================
    def _refresh(self):
        v = self._ctrl.current.valence
        self.value_lbl.setText(f"{v:.2f}")

    def _fmt_current(self):
        return f"{self._ctrl.current.valence:.2f}"

    def _nudge(self, delta):
        bl = self._ctrl.current
        new_v = max(0.0, min(1.0, bl.valence + delta))
        try:
            self._ctrl.nudge(valence=new_v)
        except AttributeError:
            # 兼容旧 controller 接口（无 valence 入参）
            self._ctrl.nudge(new_v)
        self._est.baseline = self._ctrl.current
        self._refresh()

    def _fork(self):
        bl = self._ctrl.current
        self._ctrl.fork(time.time(), new_baseline=Baseline(
            valence=bl.valence, arousal=bl.arousal))
        self._est.baseline = self._ctrl.current
        self._refresh()

    def _reset(self):
        """L3 重置：回到群体先验（Baseline() 默认 0.55/0.42），并开新 regime。"""
        self._ctrl.reset()
        self._est.baseline = self._ctrl.current
        self._refresh()