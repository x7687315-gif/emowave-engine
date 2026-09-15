"""windows/drawer.py — 右侧滑出抽屉

slide-from-right 380px 宽，背景 paper_2。
通过 QPropertyAnimation 推动 maximumWidth 在 0 ↔ width 之间过渡（ease-out-quart）。
"""
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QWidget, QLabel, QHBoxLayout
from PyQt5.QtGui import QFont

from theme import COLORS, app_font, DUR_MID


class SideDrawer(QFrame):
    def __init__(self, width=380, parent=None):
        super().__init__(parent)
        self.setObjectName("Drawer")
        self.setFixedWidth(width)

        self._target_w = width
        self._visible = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 抽屉头
        head = QWidget()
        head.setFixedHeight(46)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(18, 0, 18, 0)
        t = QLabel("抽屉")
        t.setFont(app_font(10, QFont.DemiBold))
        t.setStyleSheet(f"color: {COLORS['ink']}; letter-spacing: 1.8px;")
        hl.addWidget(t)
        hl.addStretch(1)
        outer.addWidget(head)

        # 分隔
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
        outer.addWidget(sep)

        # body
        self._body = QVBoxLayout()
        self._body.setContentsMargins(18, 16, 18, 16)
        self._body.setSpacing(14)
        self._body.addStretch(1)
        outer.addLayout(self._body, 1)

        # 初始隐藏
        self.setMaximumWidth(0)

    def add_section(self, widget):
        """添加一个分区。"""
        self._body.insertWidget(self._body.count() - 1, widget)  # 插在 stretch 前

    def show_drawer(self):
        if self._visible:
            return
        self._visible = True
        self._anim = QPropertyAnimation(self, b"maximumWidth")
        self._anim.setDuration(DUR_MID)
        self._anim.setStartValue(self.maximumWidth())
        self._anim.setEndValue(self._target_w)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.OutQuart))
        self._anim.start()

    def hide_drawer(self):
        if not self._visible:
            return
        self._visible = False
        self._anim = QPropertyAnimation(self, b"maximumWidth")
        self._anim.setDuration(DUR_MID)
        self._anim.setStartValue(self.maximumWidth())
        self._anim.setEndValue(0)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.OutQuart))
        self._anim.start()

    def toggle(self):
        if self._visible:
            self.hide_drawer()
        else:
            self.show_drawer()

    def is_open(self):
        return self._visible