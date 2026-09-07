"""windows/history_window.py — 历史记录窗口（禅意紧凑版）

按日期浏览情绪事件：情绪日历 + 事件列表 + 导出按钮。
有记录的日期以灰绿浅底标注（替代原琥珀重色），观感更安静。
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

from widgets import CardFrame, COLORS, app_font


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
        self.setObjectName('HistoryPage')
        self.setStyleSheet("QWidget#HistoryPage { background-color: {COLORS['bg']}; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # 情绪日历卡片。注意：祖先一旦有 stylesheet，QCalendarWidget 内部日格
        # 会切到 QStyleSheetStyle，若无显式 background-color 基色会变黑，
        # 故这里必须给 widget 与日格视图都显式指定背景。
        cal_card = CardFrame("情绪日历")
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setMaximumHeight(210)
        self.calendar.setStyleSheet(
            f"QCalendarWidget {{ background-color: {COLORS['surface']}; }}"
            f"QCalendarWidget QWidget {{ background-color: {COLORS['surface']}; }}"
            f"QCalendarWidget QAbstractItemView {{ background-color: {COLORS['surface']};"
            f" color: {COLORS['ink']}; alternate-background-color: {COLORS['surface']};"
            f" selection-background-color: {COLORS['accent_soft']};"
            f" selection-color: {COLORS['ink']}; }}"
            f"QCalendarWidget QToolButton {{ background-color: {COLORS['surface']};"
            f" color: {COLORS['ink']}; border: none; padding: 2px 6px; }}"
            f"QCalendarWidget QSpinBox {{ background-color: {COLORS['surface']};"
            f" color: {COLORS['ink']}; }}"
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
        self.events_table.setStyleSheet(
            f"QTableWidget {{ background-color: {COLORS['surface']};"
            f" color: {COLORS['ink']}; font-size: 12px;"
            f" border: none; gridline-color: {COLORS['rule']}; }}"
            f"QTableWidget::item {{ padding: 3px 4px; }}"
            f"QTableWidget::item:selected"
            f" {{ background-color: {COLORS['accent_soft']}; color: {COLORS['ink']}; }}"
            f"QHeaderView::section {{ background-color: transparent;"
            f" color: {COLORS['muted']}; font-size: 11px; border: none;"
            f" border-bottom: 1px solid {COLORS['rule']}; padding: 3px 4px; }}"
        )
        header = self.events_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        self.events_table.setMaximumHeight(170)
        list_card.add_widget(self.events_table)

        # 导出按钮（描边式，右对齐）
        export_container = QWidget()
        export_layout = QHBoxLayout(export_container)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.addStretch(1)
        self.export_btn = QPushButton("导出")
        self.export_btn.setCursor(self.cursor())
        self.export_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent;"
            f" color: {COLORS['ink_soft']}; border: 1px solid {COLORS['rule']};"
            f" border-radius: 6px; padding: 5px 16px; font-size: 12px; }}"
            f"QPushButton:hover {{ border-color: {COLORS['accent']};"
            f" color: {COLORS['accent']}; }}"
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
        """在日历上以灰绿浅底标注有事件记录的日期。"""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor('#DFE7D8'))          # 灰绿浅底
        fmt.setForeground(QColor(COLORS['ink']))
        fmt.setFontWeight(QFont.DemiBold)
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
