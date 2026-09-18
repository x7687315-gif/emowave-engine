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

        self._target_w = width
        self._visible = False

        # ---- 为什么不能 setFixedWidth(width) ----
        # setFixedWidth 同时把 minimumWidth 也钉成 width，而收放动画只改
        # maximumWidth（0↔width）。Qt 里 minimumWidth 优先级高于 maximumWidth，
        # 于是抽屉永远缩不回 0、也永远关不上——点抽屉按钮毫无反应（“点不进去”）。
        # 正确做法：外壳 frame 的 minimumWidth 保持 0，靠 maximumWidth 动画收放；
        # 内容放进一个固定宽度的内层容器，滑动时不回流、只被 frame 裁切。
        self.setMinimumWidth(0)
        self.setMaximumWidth(0)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 内层：固定宽度，保证滑动过程中内容不被压扁/回流
        # 用 QFrame + #DrawerInner 规则置透明，避免全局 QWidget 的 paper 底
        # 盖掉抽屉应有的 paper_2 底色。
        inner = QFrame()
        inner.setObjectName("DrawerInner")
        inner.setFixedWidth(width)
        outer.addWidget(inner)

        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)

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
        il.addWidget(head)

        # 分隔
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
        il.addWidget(sep)

        # body
        self._body = QVBoxLayout()
        self._body.setContentsMargins(18, 16, 18, 16)
        self._body.setSpacing(14)
        self._body.addStretch(1)
        il.addLayout(self._body, 1)

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