"""widgets.py — 遗留窗口共享的小控件

=== 这个文件现在还剩什么 ===
v1「精密仪器」时代的控件里，只有 `CardFrame` 还在用（事件回顾 / 历史记录两个
遗留窗口经 `LegacyEmbed` 进「历史」Tab）。其余已经没有任何生产代码引用：

  · RiskRingWidget   —— 状态环，v2 重做界面后弃用
  · EmotionCanvas    —— 效价-唤醒平面，同上
  · EmotionCurveWidget —— 时间序列曲线，已被 `curve_widget.py` 取代
  · StatBlock        —— 仪器读数块，读数条合并进 `state_head` 后弃用
  · hline()          —— 发丝分隔线，各窗口自己画
  · COLORS_COLD      —— v1 冷色板留档，零引用

2026-10-19 清理：上述 4 个类 + hline + 冷色板一并删除（约 280 行）。
新增控件请写到 `curve_widget.py` / `theme.py`，不要再往这里加。

`COLORS` 在这里保留一次 re-export：两个遗留窗口按旧路径 `from widgets import COLORS`
导入，改它们属于另一件事，先保持兼容。
"""
from PyQt5.QtWidgets import QVBoxLayout, QLabel, QFrame
from PyQt5.QtGui import QFont

from theme import COLORS                      # noqa: F401  （兼容遗留窗口的旧导入路径）


def app_font(size: int = 10, weight: int = QFont.Normal) -> QFont:
    """界面字体：系统无衬线（Segoe UI / 微软雅黑回退）。"""
    f = QFont("Segoe UI", size, weight)
    f.setStyleHint(QFont.SansSerif)
    return f


class CardFrame(QFrame):
    """极细边分组容器：白面 + 1px 极细边 + 极小大写标签。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"CardFrame {{ background-color: {COLORS['surface']};"
            f" border-radius: 0px; border: 1px solid {COLORS['rule']}; }}"
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
