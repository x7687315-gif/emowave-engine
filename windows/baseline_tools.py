"""windows/baseline_tools.py — 「模型」页首项：基线主权

实现 ui-draft-v1.html §02 「基线主权」分区。
三个控件：步进器（−/值/+）+ 分叉（新起点）+ 重置。

=== 阶段 6 起：本卡片是纯视图 ===
v3 时代它直接改内核（`ctrl.nudge()` + `est.baseline = ...`）。MVP 解耦后这些
副作用归 presenter，因此构造参数从「传 estimator / controller 对象」改为：

  · `baseline_provider`：callable，返回**当前** BaselineController
    —— 换精力人群后 presenter 会 replace 新 controller，卡片若持有旧对象
    就会把 nudge 写进一个已退役的 controller（阶段 5 的 bug：estimator 被污染
    成 0.60 而曲线基线仍画 0.50）。
  · `on_nudge / on_fork / on_reset`：回调，由视图接到 presenter，
    顺带拿回 provenance（reason="user_nudge"）与 baseline_changed 信号。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PyQt5.QtGui import QFont

from theme import COLORS, app_font, app_font_num
from emowave.core.domain.baseline import Baseline


_STEP = 0.02   # 一次微调的步长


class BaselineToolsCard(QFrame):
    """基线主权卡：步进器 + 分叉 + 重置（只显示、不直接改内核）。"""

    def __init__(self, baseline_provider, on_nudge, on_fork, on_reset,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        self._get_ctrl = baseline_provider
        self._on_nudge = on_nudge
        self._on_fork = on_fork
        self._on_reset = on_reset

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
        self.btn_minus.clicked.connect(lambda: self._on_nudge(-_STEP))

        self.value_lbl = QLabel(self._fmt_current())
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
        self.btn_plus.clicked.connect(lambda: self._on_nudge(+_STEP))

        stepper_lay.addWidget(self.btn_minus)
        stepper_lay.addWidget(self.value_lbl, 1)
        stepper_lay.addWidget(self.btn_plus)
        step_row.addWidget(stepper)
        lay.addLayout(step_row)

        # 分叉按钮
        self.btn_fork = QPushButton("◆  分叉（新起点）")
        self.btn_fork.setCursor(Qt.PointingHandCursor)
        self.btn_fork.setStyleSheet(self._neutral_btn_qss())
        self.btn_fork.clicked.connect(self._on_fork)
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
        self.btn_reset.clicked.connect(self._on_reset)
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
    # 对外接口
    # ================================================================
    def refresh(self):
        """从 provider 重新拉当前基线并刷新显示（换人群 / nudge 后由视图调）。

        换精力人群后 presenter 会 **replace** 新的 BaselineController，
        所以这里必须每次都从 provider 拿，不能缓存引用。
        """
        self._refresh()

    # ================================================================
    # 行为（纯显示：按钮一律走回调到 presenter，自身不改内核）
    # ================================================================
    def _refresh(self):
        self.value_lbl.setText(f"{self._get_ctrl().current.valence:.2f}")

    def _fmt_current(self):
        return f"{self._get_ctrl().current.valence:.2f}"