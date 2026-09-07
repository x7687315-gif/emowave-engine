"""windows/dashboard_window.py — 今日情绪天气仪表盘（禅意紧凑版）

布局：风险环 + 一行式概览数据块 → 最近事件（带空状态）→ 主行动按钮。
间距克制：margins 14 / spacing 10；列表限高，不再出现大篇幅空白。
"""
import json

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QGridLayout,
)
from PyQt5.QtCore import Qt

from widgets import RiskRingWidget, CardFrame, COLORS, StatBlock, app_font


class DashboardWindow(QWidget):
    """今日情绪天气仪表盘窗口。"""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._setup_ui()

    # ================================================================
    # UI 构建
    # ================================================================

    def _setup_ui(self):
        self.setObjectName('DashboardPage')
        self.setStyleSheet("QWidget#DashboardPage { background-color: {COLORS['bg']}; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 上半区：风险环 + 今日概览（一行式数据块）
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self.risk_ring = RiskRingWidget()
        top_row.addWidget(self.risk_ring, 0, Qt.AlignTop)

        overview_card = CardFrame(title="今日概览")
        grid = QGridLayout()
        grid.setContentsMargins(2, 0, 2, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)

        self.stat_hr = StatBlock("静息心率 bpm")
        self.stat_hrv = StatBlock("静息 HRV ms")
        self.stat_sleep = StatBlock("睡眠评分")
        self.stat_events = StatBlock("今日事件")
        for col, block in enumerate(
                [self.stat_hr, self.stat_hrv, self.stat_sleep, self.stat_events]):
            grid.addWidget(block, 0, col)

        overview_card._content_layout.addLayout(grid)
        top_row.addWidget(overview_card, 1)

        root.addLayout(top_row)

        # 最近事件（限高 + 空状态，不再大篇幅空白）
        events_card = CardFrame(title="最近事件")
        self.recent_events_list = QListWidget()
        self.recent_events_list.setStyleSheet(
            f"QListWidget {{ background-color: {COLORS['surface']}; border: none;"
            f" color: {COLORS['ink']}; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 4px 2px; }}"
            f"QListWidget::item:selected {{ background-color: {COLORS['accent_soft']}; }}"
        )
        self.recent_events_list.setFixedHeight(118)
        events_card.add_widget(self.recent_events_list)

        self.events_empty = QLabel("今天还没有记录 —— 需要的话，从下面的按钮开始")
        self.events_empty.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 12px; padding: 10px 2px;"
        )
        events_card.add_widget(self.events_empty)

        root.addWidget(events_card)

        # 主行动按钮（灰绿，克制）
        self.btn_record = QPushButton("开始记录情绪")
        self.btn_record.setCursor(self.cursor())
        self.btn_record.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['accent']}; color: #FFFFFF;"
            f" border: none; border-radius: 8px; padding: 9px 16px;"
            f" font-size: 13px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {COLORS['accent_hover']}; }}"
        )
        root.addWidget(self.btn_record)
        root.addStretch(1)

    # ================================================================
    # 数据刷新
    # ================================================================

    def refresh(self):
        """从 session 拉取仪表盘数据并刷新界面。

        - today_summary 含 peak_arousal 时同步到风险环，否则归零
        - 概览数据块显示基线 + 今日事件数
        - 最近事件列表重填；无记录时显示空状态文案
        """
        data = self.session.get_dashboard_data()

        today_summary = data.get('today_summary')
        peak_arousal = None
        if today_summary:
            peak_arousal = today_summary.get('peak_arousal')

        if peak_arousal is not None:
            self.risk_ring.set_value(peak_arousal)
        else:
            self.risk_ring.set_value(0.0)

        self._update_overview(data)

        # 最近事件
        events = data.get('recent_events', [])
        self.recent_events_list.clear()
        for evt in events:
            self.recent_events_list.addItem(self._format_event(evt))
        self.recent_events_list.setVisible(bool(events))
        self.events_empty.setVisible(not events)

    # ================================================================
    # 格式化辅助
    # ================================================================

    def _update_overview(self, data):
        """把基线 + 今日摘要写入四个概览数据块。"""
        baseline = data.get('baseline') or {}
        today_summary = data.get('today_summary')

        hr = baseline.get('resting_hr')
        hrv = baseline.get('resting_hrv')
        sleep = baseline.get('sleep_score')

        self.stat_hr.set_value(f"{hr:.0f}" if hr is not None else "—")
        self.stat_hrv.set_value(f"{hrv:.0f}" if hrv is not None else "—")
        self.stat_sleep.set_value(f"{sleep:.1f}" if sleep is not None else "—")

        evt_count = today_summary.get('event_count', 0) if today_summary else 0
        self.stat_events.set_value(str(evt_count))

    @staticmethod
    def _format_event(evt):
        """格式化单条事件为可读字符串。"""
        peak_arousal = evt.get('peak_arousal')
        peak_intensity = evt.get('peak_intensity')

        trigger_tags = evt.get('trigger_tags', '[]')
        if isinstance(trigger_tags, str):
            try:
                trigger_tags = json.loads(trigger_tags)
            except (ValueError, TypeError):
                trigger_tags = []
        tag_text = "、".join(trigger_tags) if trigger_tags else "未标注"

        parts = [tag_text]
        if peak_intensity is not None:
            parts.append(f"强度 {peak_intensity:.1f}")
        if peak_arousal is not None:
            parts.append(f"唤醒 {peak_arousal:.2f}")
        return " · ".join(parts)
