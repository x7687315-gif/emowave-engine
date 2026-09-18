"""windows/legacy_embed.py — 抽屉：事件回顾 + 历史记录（旧窗口嵌入）

保留所有旧 EventSummaryWindow / HistoryWindow 的功能；
只换主题色（暖色板），不复用旧 COLORS 的冷色蓝。
"""
import logging

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QFrame, QLabel
from PyQt5.QtGui import QFont

from theme import COLORS, app_font

# 直接复用旧窗口 —— 它们自带数据驱动，无须改业务
from .event_summary_window import EventSummaryWindow
from .history_window import HistoryWindow

logger = logging.getLogger(__name__)


class _DbSession:
    """最小 session 适配器：旧窗口只用到 session.db，这里把 db 包一层喂给它。"""
    __slots__ = ("db",)

    def __init__(self, db):
        self.db = db


class LegacyEmbed(QFrame):
    """把事件回顾 + 历史记录两层叠在抽屉里。"""

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        self._db = db

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)

        title = QLabel("事件回顾 & 历史")
        title.setFont(app_font(9, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.8px;")
        lay.addWidget(title)

        # 旧窗口按 session.db 读事件表；这里注入真实 db（此前写死 session=None，
        # 是 v3 重写遗留的 TODO，导致两个面板永远是空壳）。
        session = _DbSession(db)
        self.summary = EventSummaryWindow(session=session, parent=self)
        self.history = HistoryWindow(session=session, parent=self)

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

    def refresh(self):
        """刷新两个面板：日历/事件表按日期重载，事件回顾显示最近一条。

        无 db 时安全跳过（主界面仍可启动）；异常只记日志，不打断 UI。
        """
        if self._db is None:
            return
        try:
            self.history.refresh()
            recent = self._db.get_recent_events(limit=1)
            if recent:
                self.summary.show_event(recent[0].get("event_id"))
        except Exception as exc:                       # 刷新失败不应冒泡打断抽屉
            logger.warning("抽屉「事件回顾/历史记录」刷新失败：%s", exc)