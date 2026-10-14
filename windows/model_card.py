"""windows/model_card.py — 抽屉：个人模型

学习事件数 / 惯性 ℓ / 阶段 / 收缩度 + 「学习一步」按钮。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PyQt5.QtGui import QFont

from theme import COLORS, app_font, app_font_num


class ModelCard(QFrame):
    """个人模型卡：已学习 / 惯性 ℓ / 阶段 / 个人化 + 「学习一步」。

    === 为什么不直接持有 params/calibrator ===
    v3 在构造时抓住 `params` 对象，但 presenter 有两处会 replace 它：
      · `set_archetype()` 换人群 → 新 params
      · `Calibrator.learn()` 学习一步 → 返回新 params（version+1）
    两者都会让卡片显示**上一次的旧值**（"学习一步"点下去数字不动）。
    因此改为持有 **provider（可调用）**，每次 refresh 都从源头拉；
    传入普通对象也支持（`_get()` 兜底），不破坏既有调用方式。
    """

    def __init__(self, params, calibrator, on_learn, parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        self._params_ref = params          # 对象或 callable
        self._calibrator_ref = calibrator  # 同上（当前仅作状态来源预留）
        self._on_learn = on_learn

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)

        title = QLabel("个人模型")
        title.setFont(app_font(9, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.8px;")
        lay.addWidget(title)

        desc = QLabel("模型已学习你的纠正，逐步贴近你的真实状态。")
        desc.setFont(app_font(11))
        desc.setStyleSheet(f"color: {COLORS['ink_2']};")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # 4 个数值
        grid = QHBoxLayout()
        grid.setSpacing(0)
        self._lbl_n = self._cell("已学习")
        self._lbl_ell = self._cell("惯性 ℓ(s)")
        self._lbl_stage = self._cell("阶段")
        self._lbl_shrink = self._cell("个人化")
        for w in (self._lbl_n, self._lbl_ell, self._lbl_stage, self._lbl_shrink):
            grid.addWidget(w, 1)
        lay.addLayout(grid)

        # 学习按钮
        row = QHBoxLayout()
        row.setSpacing(10)
        btn = QPushButton("学习一步")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("primary", True)
        btn.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; color: {COLORS['surface']};"
            f" border: none; padding: 8px 16px; font-size: 11.5px;"
            f" font-weight: 600; letter-spacing: 1.4px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['accent_deep']}; }}"
        )
        btn.clicked.connect(self._learn)
        row.addWidget(btn)
        hint = QLabel("积累纠正后点击学习")
        hint.setFont(app_font(10))
        hint.setStyleSheet(f"color: {COLORS['muted']};")
        row.addWidget(hint, 1, Qt.AlignVCenter)
        lay.addLayout(row)

        self.refresh()

    def _cell(self, key):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 12, 4)
        v.setSpacing(2)
        k = QLabel(key)
        k.setFont(app_font(9, QFont.DemiBold))
        k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.4px;")
        big = QLabel("—")
        big.setFont(app_font_num(14, QFont.Light))
        big.setStyleSheet(f"color: {COLORS['ink']};")
        v.addWidget(k)
        v.addWidget(big)
        w.value_label = big
        return w

    @staticmethod
    def _get(ref):
        """支持传对象或传 callable（callable 用于永远从源头拉最新值）。"""
        return ref() if callable(ref) else ref

    def refresh(self):
        p = self._get(self._params_ref)
        self._lbl_n.value_label.setText(str(getattr(p, 'n_events_fitted', 0)))
        self._lbl_ell.value_label.setText(f"{getattr(p, 'ell_valence', 0):.0f}")
        self._lbl_stage.value_label.setText(getattr(p.stage, 'value', '—'))
        self._lbl_shrink.value_label.setText(
            f"{int(getattr(p, 'shrinkage_alpha', 0)*100)}%")

    def _learn(self):
        self._on_learn()
        self.refresh()          # 学习一步后立刻重新拉取（learn() 返回新 params）