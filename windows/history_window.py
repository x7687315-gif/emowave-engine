"""windows/history_window.py — 历史记录窗口

按日期浏览情绪事件：情绪日历 + 事件列表 + 导出按钮。
"""
import json
from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCalendarWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView,
)
from PyQt5.QtCore import QDate, Qt
from PyQt5.QtGui import QTextCharFormat, QColor, QFont

from widgets import CardFrame, COLORS


class HistoryWindow(QWidget):
    """历史记录窗口：按日期浏览历史情绪事件。"""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._event_dates = []
        self._setup_ui()

    # ----------------------------------------------------------------
    # UI 构建
    # ----------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("历史记录")
        title.setStyleSheet(
            f"color: {COLORS['ink']}; font-size: 20px; font-weight: bold;"
        )
        layout.addWidget(title)

        # 情绪日历卡片
        cal_card = CardFrame("情绪日历")
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setHorizontalHeaderFormat(
            QCalendarWidget.LongDayNames
        )
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.NoVerticalHeader
        )
        self.calendar.clicked.connect(self._on_date_clicked)
        cal_card.add_widget(self.calendar)
        layout.addWidget(cal_card)

        # 事件列表卡片
        list_card = CardFrame("事件列表")
        self.events_table = QTableWidget(0, 4)
        self.events_table.setHorizontalHeaderLabels(
            ["时间", "峰值唤醒", "触发因素", "自评"]
        )
        self.events_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.events_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.events_table.verticalHeader().setVisible(False)
        header = self.events_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        list_card.add_widget(self.events_table)

        # 导出按钮（右对齐）
        export_container = QWidget()
        export_layout = QHBoxLayout(export_container)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.addStretch(1)
        self.export_btn = QPushButton("导出")
        self.export_btn.setStyleSheet(
            f"background-color: {COLORS['calm']}; color: white;"
            f"padding: 6px 16px; border-radius: 6px;"
        )
        export_layout.addWidget(self.export_btn)
        list_card.add_widget(export_container)
        layout.addWidget(list_card)

        layout.addStretch(1)

    # ----------------------------------------------------------------
    # 业务逻辑
    # ----------------------------------------------------------------
    def refresh(self):
        """刷新窗口：读取所有事件日期并高亮，随后触发今天日期点击。"""
        self._event_dates = self.session.db.get_all_event_dates()
        self._highlight_dates(self._event_dates)

        today = QDate.currentDate()
        self.calendar.setSelectedDate(today)
        self._on_date_clicked(today)

    def _on_date_clicked(self, qdate):
        """点击日历日期时按日期查询事件并填充表格。"""
        date_str = qdate.toString('yyyy-MM-dd')
        events = self.session.db.get_events_by_date(date_str)

        self.events_table.setRowCount(0)
        for row_idx, event in enumerate(events):
            self.events_table.insertRow(row_idx)
            self.events_table.setItem(
                row_idx, 0, QTableWidgetItem(self._fmt_time(event)))
            self.events_table.setItem(
                row_idx, 1, QTableWidgetItem(self._fmt_number(
                    event.get('peak_arousal'))))
            self.events_table.setItem(
                row_idx, 2, QTableWidgetItem(self._fmt_triggers(event)))
            self.events_table.setItem(
                row_idx, 3, QTableWidgetItem(self._fmt_number(
                    event.get('user_peak_rating'), digits=1)))

    # ----------------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------------
    def _highlight_dates(self, date_strs):
        """在日历上高亮有事件记录的日期。"""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(COLORS['warm']))
        fmt.setForeground(QColor('#ffffff'))
        fmt.setFontWeight(QFont.Bold)
        for ds in date_strs:
            qd = QDate.fromString(ds, 'yyyy-MM-dd')
            if qd.isValid():
                self.calendar.setDateTextFormat(qd, fmt)

    @staticmethod
    def _parse_json(value, default):
        if value is None:
            return default
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _fmt_time(event):
        """优先使用 created_at 的时间部分，回退到 start_time 时间戳。"""
        created = event.get('created_at')
        if created:
            try:
                dt = datetime.fromisoformat(created)
                return dt.strftime('%H:%M:%S')
            except (ValueError, TypeError):
                return str(created)
        start = event.get('start_time')
        if start is not None:
            try:
                return datetime.fromtimestamp(float(start)).strftime('%H:%M:%S')
            except (ValueError, TypeError, OSError):
                pass
        return '-'

    @classmethod
    def _fmt_number(cls, value, digits=2):
        if value is None:
            return '-'
        try:
            return f"{float(value):.{digits}f}"
        except (ValueError, TypeError):
            return str(value)

    @classmethod
    def _fmt_triggers(cls, event):
        tags = cls._parse_json(event.get('trigger_tags'), [])
        if not tags:
            return '-'
        return '、'.join(str(t) for t in tags)
