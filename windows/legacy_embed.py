"""windows/legacy_embed.py — 抽屉：事件回顾 + 历史记录（旧窗口嵌入）

保留所有旧 EventSummaryWindow / HistoryWindow 的功能；
只换主题色（暖色板），不复用旧 COLORS 的冷色蓝。
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QFrame, QLabel
from PyQt5.QtGui import QFont

from theme import COLORS, app_font

# 直接复用旧窗口 —— 它们自带数据驱动，无须改业务
from .event_summary_window import EventSummaryWindow
from .history_window import HistoryWindow


class LegacyEmbed(QFrame):
    """把事件回顾 + 历史记录两层叠在抽屉里。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Section")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)

        title = QLabel("事件回顾 & 历史")
        title.setFont(app_font(9, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.8px;")
        lay.addWidget(title)

        # 旧窗口（需要 session，这里从父级获取；不通过参数注入是为了简单）
        # 由于旧窗口在旧控制台里被嵌入时接收 session，
        # 我们让用户在使用抽屉时通过 SessionController 走，session 暂设为 None。
        # 真实使用时由 main_app.py 注入 session（见 TODO）。
        self.summary = EventSummaryWindow(session=None, parent=self)
        self.history = HistoryWindow(session=None, parent=self)

        # 标题小卡
        sub1 = QLabel("事件回顾")
        sub1.setFont(app_font(10, QFont.DemiBold))
        sub1.setStyleSheet(f"color: {COLORS['ink_2']}; letter-spacing: 1.4px;")
        lay.addWidget(sub1)
        lay.addWidget(self.summary)

        sub2 = QLabel("历史记录")
        sub2.setFont(app_font(10, QFont.DemiBold))
        sub2.setStyleSheet(f"color: {COLORS['ink_2']}; letter-spacing: 1.4px;")
        lay.addWidget(sub2)
        lay.addWidget(self.history)