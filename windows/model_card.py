"""windows/model_card.py — 模型页第二段：个人模型

新版把四列等宽读数改成**逐行记录**：标签在左、数值在右、行间一条细线。
四列并排在 400px 下会挤成"仪表盘"，逐行才能读清"模型学到了什么"。

参数仍是 provider（callable）：`learn()` 会 replace 新 params，卡片每次
refresh 都从源头拉，否则"学习一步"点下去数字不动（v3 的 bug）。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from theme import COLORS, app_font, app_font_num


class _Value:
    """只暴露 `.value_label` 的小容器（沿用旧卡片的访问方式）。"""

    def __init__(self, label):
        self.value_label = label


class ModelCard(QFrame):
    def __init__(self, params, calibrator, on_learn, parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        self._params_ref = params
        self._calibrator_ref = calibrator
        self._on_learn = on_learn

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 14, 0, 6)
        lay.setSpacing(0)

        title = QLabel("个人模型")
        title.setFont(app_font(9, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.8px;")
        lay.addWidget(title)
        lay.addSpacing(2)

        self._cells = {}
        for key, name in (("n", "已学习"), ("ell", "惯性 ℓ (s)"),
                          ("stage", "阶段"), ("shrink", "个人化")):
            lay.addWidget(self._row(key, name))

        lay.addSpacing(10)
        learn = QPushButton("学习一步")
        learn.setCursor(Qt.PointingHandCursor)
        learn.setFlat(True)
        learn.setFocusPolicy(Qt.NoFocus)
        learn.setFont(app_font(11, QFont.DemiBold))
        learn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent;"
            f" color: {COLORS['accent_ink']}; padding: 0; }}"
            f"QPushButton:hover {{ color: {COLORS['accent']}; }}")
        learn.clicked.connect(self._learn)
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(learn)
        self._hint = QLabel("积累纠正后点击学习")
        self._hint.setFont(app_font(9.5))
        self._hint.setStyleSheet(f"color: {COLORS['muted']};")
        row.addWidget(self._hint, 1)
        lay.addLayout(row)
        lay.addSpacing(10)

        self.refresh()

    # ------------------------------------------------------------
    def _row(self, key, name):
        box = QFrame()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        line = QHBoxLayout()
        line.setSpacing(10)
        k = QLabel(name)
        k.setFont(app_font(11))
        k.setStyleSheet(f"color: {COLORS['ink_2']};")
        line.addWidget(k)
        line.addStretch(1)
        v = QLabel("—")
        v.setFont(app_font(13) if key == "stage" else app_font_num(14, QFont.Light))
        v.setStyleSheet(f"color: {COLORS['ink']};")
        line.addWidget(v, 0, Qt.AlignVCenter)
        holder = _Value(v)
        self._cells[key] = holder
        # 沿用阶段 5 的属性名：测试与外部按 _lbl_ell.value_label 读值
        setattr(self, "_lbl_" + key, holder)

        bl.addSpacing(9)
        bl.addLayout(line)
        bl.addSpacing(9)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
        bl.addWidget(rule)
        return box

    @staticmethod
    def _get(ref):
        return ref() if callable(ref) else ref

    def refresh(self):
        p = self._get(self._params_ref)
        if p is None:
            return
        self._cells["n"].value_label.setText(
            "%d 次" % getattr(p, 'n_events_fitted', 0))
        self._cells["ell"].value_label.setText(
            "%d" % round(getattr(p, 'ell_valence', 0)))
        stage = getattr(p, 'stage', None)
        self._cells["stage"].value_label.setText(getattr(stage, 'value', '—'))
        self._cells["shrink"].value_label.setText(
            "%d%%" % int(getattr(p, 'shrinkage_alpha', 0) * 100))

    def _learn(self):
        self._on_learn()
        self.refresh()
