"""windows/dashboard_window.py — 今日情绪天气仪表盘窗口

聚合 SessionController.get_dashboard_data() 返回的数据，呈现：
  - 顶部标题
  - 风险环（peak_arousal）+ 今日概览卡片（基线数据）
  - 最近事件列表
  - 开始记录情绪按钮
"""
import json

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
)

from widgets import RiskRingWidget, CardFrame, COLORS


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
        self.setWindowTitle("今日情绪天气")
        self.setStyleSheet(f"background-color: {COLORS['bg']};")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # 标题
        title = QLabel("今日情绪天气")
        title.setStyleSheet(
            f"color: {COLORS['ink']}; font-size: 22px; font-weight: bold;"
        )
        root.addWidget(title)

        # 上半区：风险环 + 今日概览
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        self.risk_ring = RiskRingWidget()
        top_row.addWidget(self.risk_ring)

        overview_card = CardFrame(title="今日概览")
        self.info_label = QLabel("暂无数据")
        self.info_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 13px;"
        )
        self.info_label.setWordWrap(True)
        overview_card.add_widget(self.info_label)
        top_row.addWidget(overview_card, 1)

        root.addLayout(top_row)

        # 最近事件
        events_card = CardFrame(title="最近事件")
        self.recent_events_list = QListWidget()
        self.recent_events_list.setStyleSheet(
            f"QListWidget {{ background-color: {COLORS['surface']}; "
            f"border: none; color: {COLORS['ink']}; font-size: 13px; }}"
        )
        events_card.add_widget(self.recent_events_list)
        root.addWidget(events_card, 1)

        # 操作按钮
        self.btn_record = QPushButton("开始记录情绪")
        self.btn_record.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['calm']}; color: white; "
            f"border: none; border-radius: 8px; padding: 10px 16px; "
            f"font-size: 14px; font-weight: bold; }}"
        )
        root.addWidget(self.btn_record)

    # ================================================================
    # 数据刷新
    # ================================================================

    def refresh(self):
        """从 session 拉取仪表盘数据并刷新界面。

        - today_summary 含 peak_arousal 时同步到风险环，否则归零
        - info_label 显示基线数据（静息心率 / HRV / 睡眠评分 / 今日事件数）
        - 清空并重填最近事件列表
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

        # 更新今日概览信息
        self.info_label.setText(self._format_overview(data))

        # 刷新最近事件列表
        self.recent_events_list.clear()
        for evt in data.get('recent_events', []):
            self.recent_events_list.addItem(self._format_event(evt))

    # ================================================================
    # 格式化辅助
    # ================================================================

    @staticmethod
    def _format_overview(data):
        """根据基线 + 今日摘要生成概览文本。"""
        baseline = data.get('baseline') or {}
        today_summary = data.get('today_summary')

        lines = []
        hr = baseline.get('resting_hr')
        hrv = baseline.get('resting_hrv')
        sleep = baseline.get('sleep_score')

        if hr is not None:
            lines.append(f"静息心率: {hr:.1f} bpm")
        if hrv is not None:
            lines.append(f"静息 HRV: {hrv:.1f} ms")
        if sleep is not None:
            lines.append(f"睡眠评分: {sleep:.1f}")
        if today_summary:
            evt_count = today_summary.get('event_count', 0)
            lines.append(f"今日事件: {evt_count} 次")

        return "\n".join(lines) if lines else "暂无数据"

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
