"""windows/legacy_embed.py — 历史页外壳：事件清单 + 按需展开的事件回顾

新版不再把「事件回顾」常驻在页面顶部（它是一张旧卡片，且大多数时候是空的）。
改为：清单优先；点某一行，才在顶部展开该事件的回顾，下方仍可继续浏览。

两个旧窗口都保留：
  · HistoryWindow 已按新语言重写（按天分组）
  · EventSummaryWindow 原样复用（它自带图表与补全入口）
"""
import logging

from PyQt5.QtWidgets import QFrame, QVBoxLayout

from session import SessionController
from .event_summary_window import EventSummaryWindow
from .history_window import HistoryWindow

logger = logging.getLogger(__name__)


class LegacyEmbed(QFrame):
    """历史页容器（名字沿用旧调用点，内容已重排）。"""

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        self._db = db

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        session = SessionController(db) if db is not None else None
        self._session = session

        self.summary = EventSummaryWindow(session=session, parent=self)
        self.summary.setVisible(False)      # 选中事件后才出现
        lay.addWidget(self.summary)

        self.history = HistoryWindow(session=session, parent=self)
        self.history.event_activated.connect(self._on_event)
        lay.addWidget(self.history, 1)

    # ------------------------------------------------------------
    def refresh(self):
        try:
            self.history.refresh()
        except Exception as exc:            # 刷新失败不该打断记录流程
            logger.warning("历史清单刷新失败：%s", exc)

    def _on_event(self, event_id):
        if not event_id or self._session is None:
            return
        try:
            self.summary.show_event(event_id)
            self.summary.setVisible(True)
        except Exception as exc:
            logger.warning("事件回顾打开失败：%s", exc)
