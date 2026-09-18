"""windows/archetype_dialog.py — 开局选"精力人群"的对话框。

三类（高/中/低精力）各对应一条默认曲线先验；用户选一类 → 主界面用它 seed。
构成主义风格：直角、硬边、无圆角、无 emoji；选中项用 accent 左竖条 + 描边。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PyQt5.QtGui import QFont

from theme import COLORS, app_font
from emowave import ARCHETYPE_ORDER, DEFAULT_ARCHETYPE_KEY


class _ArchetypeCard(QFrame):
    """一行可选的人群卡片，点击发 clicked(key)。"""

    clicked = pyqtSignal(str)

    def __init__(self, archetype, parent=None):
        super().__init__(parent)
        self.archetype = archetype
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(64)
        self._checked = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)
        name = QLabel(archetype.label)
        name.setFont(app_font(14, QFont.DemiBold))
        name.setStyleSheet(f"color: {COLORS['ink']}; background: transparent;")
        desc = QLabel(archetype.desc)
        desc.setFont(app_font(11))
        desc.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        lay.addWidget(name)
        lay.addWidget(desc)
        self._paint()

    def set_checked(self, on: bool):
        self._checked = bool(on)
        self._paint()

    def _paint(self):
        edge = COLORS['accent'] if self._checked else COLORS['rule']
        bg = COLORS['accent_soft'] if self._checked else COLORS['surface']
        self.setStyleSheet(
            f"_ArchetypeCard, QFrame {{ background: {bg};"
            f" border: 1px solid {edge}; border-left: 3px solid {edge}; }}"
        )

    def mousePressEvent(self, _e):
        self.clicked.emit(self.archetype.key)


class ArchetypeDialog(QDialog):
    """让用户在高/中/低精力里选一个起点先验。"""

    def __init__(self, current_key: str = DEFAULT_ARCHETYPE_KEY, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择你的精力类型")
        self.selected_key = current_key or DEFAULT_ARCHETYPE_KEY

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 18)
        root.setSpacing(12)

        title = QLabel("一开始，用哪条曲线作为你的默认？")
        title.setFont(app_font(16, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['ink']}; background: transparent;")
        root.addWidget(title)

        hint = QLabel("选一类最接近你平时的精力状态；之后会随你的记录与修改逐步个性化。")
        hint.setWordWrap(True)
        hint.setFont(app_font(11))
        hint.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        root.addWidget(hint)

        self._cards = {}
        for a in ARCHETYPE_ORDER:
            card = _ArchetypeCard(a)
            card.clicked.connect(self._pick)
            root.addWidget(card)
            self._cards[a.key] = card
            if a.key == self.selected_key:
                card.set_checked(True)

        root.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("开始")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; color: {COLORS['surface']};"
            f" border: none; padding: 10px 26px; font-weight: 600; letter-spacing: 1.5px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
        )
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        root.addLayout(row)

    def _pick(self, key: str):
        self.selected_key = key
        for k, card in self._cards.items():
            card.set_checked(k == key)

    def selected(self) -> str:
        return self.selected_key
