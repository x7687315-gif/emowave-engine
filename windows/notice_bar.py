"""windows/notice_bar.py — 主窗顶部的降级/错误提示条

设计约束：
  · 不打断记录流程：提示条可关闭，不弹模态框；
  · 只说三件事：哪份数据、什么影响、怎么办；
  · 不显示情绪数值，也不把异常堆栈丢给用户。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from theme import COLORS, METRICS, app_font


class NoticeBar(QFrame):
    """一条可关闭的提示；默认隐藏。"""

    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NoticeBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 8, 7)
        lay.setSpacing(8)

        self.text_label = QLabel("")
        self.text_label.setFont(app_font(9))
        self.text_label.setWordWrap(True)
        lay.addWidget(self.text_label, 1)

        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip("关闭提示")
        self.close_btn.clicked.connect(self.dismiss)
        lay.addWidget(self.close_btn, 0, Qt.AlignTop)

        self._apply_style()
        self.setVisible(False)

    def _apply_style(self):
        rs = METRICS['radius_sm']
        self.setStyleSheet(
            f"QFrame#NoticeBar {{ background: {COLORS['accent_soft']};"
            f" border: 1px solid {COLORS['accent_ink']};"
            f" border-radius: {rs}px; }}"
        )
        self.text_label.setStyleSheet(
            f"color: {COLORS['accent_ink']}; background: transparent;")
        self.close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" color: {COLORS['accent_ink']}; font-size: 12px; padding: 0;"
            f" border-radius: {rs}px; }}"
            f"QPushButton:hover {{ background: {COLORS['surface']}; }}"
        )

    def show_message(self, text, severity="warn"):
        """severity 预留给未来分级；当前统一用暖色警示，不引入新色相。"""
        self.text_label.setText(str(text))
        self.setVisible(True)

    def dismiss(self):
        self.setVisible(False)
        self.dismissed.emit()

    def current_text(self):
        return self.text_label.text()
