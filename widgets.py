"""widgets.py — 遗留窗口共享的小控件

v1 的大部分控件已退役（2026-10-19 清理，约 280 行），现在只剩 `CardFrame`
供事件回顾 / 历史记录两个遗留窗口使用。新增控件请写到 `curve_widget.py` /
`theme.py`，不要再往这里加。

`COLORS` / `app_font` 在这里保留 re-export：两个遗留窗口按旧路径
`from widgets import ...` 导入，字体与色板必须与 theme 同源。
"""
from PyQt5.QtWidgets import QVBoxLayout, QLabel, QFrame

from theme import COLORS, METRICS, app_font      # noqa: F401  （兼容遗留窗口的旧导入路径）


class CardFrame(QFrame):
    """极细边分组容器：白面 + 1px 极细边 + 柔和圆角 + 极小大写标签。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"CardFrame {{ background-color: {COLORS['surface']};"
            f" border-radius: {METRICS['radius']}px;"
            f" border: 1px solid {COLORS['rule']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 10px;"
            " letter-spacing: 2px; text-transform: uppercase;"
        )
        layout.addWidget(self.title_label)

        self._content_layout = layout

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)
