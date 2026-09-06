#!/usr/bin/env python3
"""心潮 EmoWave 桌面情绪追踪应用入口（禅意紧凑版）

单窗口集成四个功能页：今日仪表盘 / 情绪冲浪 / 事件回顾 / 历史记录。
- 侧边栏可折叠：展开 172px 文字导航 ↔ 收起 54px 日式竖排二字导航
- 统一页头：当前页名 + 日期，页面内不再重复大标题（紧凑）
- 纸白 + 灰绿/雾蓝 + 发丝线 + 朱红点睛
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QStackedWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QFileDialog, QMessageBox, QSizePolicy,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from db import DatabaseManager
from session import SessionController
from widgets import COLORS, app_font
from windows.dashboard_window import DashboardWindow
from windows.surfing_window import SurfingWindow
from windows.event_summary_window import EventSummaryWindow
from windows.history_window import HistoryWindow

# 侧边栏两态宽度
SIDEBAR_W = 168
SIDEBAR_W_COLLAPSED = 54

STYLE_SHEET = f"""
QMainWindow {{ background-color: {COLORS['bg']}; }}
QStackedWidget {{ background-color: transparent; }}
QListWidget {{ border: none; background: transparent; }}
QPushButton:hover {{ opacity: 0.88; }}
QToolTip {{
    background-color: {COLORS['surface']}; color: {COLORS['ink']};
    border: 1px solid {COLORS['rule']}; padding: 4px;
}}
"""

# (完整名, 收起态竖排二字, 页索引)
NAV_ITEMS = [
    ("今日仪表盘", "仪表", 0),
    ("情绪冲浪", "冲浪", 1),
    ("事件回顾", "回顾", 2),
    ("历史记录", "历史", 3),
]


class MainWindow(QMainWindow):
    """主窗口：可折叠侧边栏 + 统一页头 + 四页面栈 + 菜单栏"""

    def __init__(self, db=None):
        super().__init__()
        self.setWindowTitle("心潮 EmoWave · 情绪追踪")
        self.setMinimumSize(860, 560)
        self.resize(980, 640)
        self.setStyleSheet(STYLE_SHEET)
        self.sidebar_collapsed = False

        # 初始化数据库和会话
        if db is None:
            db = DatabaseManager()
        self.db = db
        self.session = SessionController(db)

        # 中心区域：侧边栏 | (页头 + 页面栈)
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = self._build_sidebar()
        main_layout.addWidget(self.sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        content_layout.addWidget(self._build_header())
        content_layout.addWidget(self._header_rule())

        # 页面栈
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, stretch=1)
        main_layout.addWidget(content, stretch=1)

        # 创建四个页面（传入 self 作为 parent，使 SurfingWindow 能回调）
        self.dashboard_page = DashboardWindow(self.session, parent=self)
        self.surfing_page = SurfingWindow(self.session, parent=self)
        self.summary_page = EventSummaryWindow(self.session, parent=self)
        self.history_page = HistoryWindow(self.session, parent=self)

        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.surfing_page)
        self.stack.addWidget(self.summary_page)
        self.stack.addWidget(self.history_page)

        self._setup_menu()
        self._switch_to(0)

    # ================================================================
    # 侧边栏（可折叠）
    # ================================================================

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_W)
        sidebar.setStyleSheet(
            f"QFrame#Sidebar {{ background-color: {COLORS['surface']};"
            f" border: none; border-right: 1px solid {COLORS['rule']}; }}"
        )
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(2)

        # 折叠开关
        self.btn_toggle = QPushButton("≡")
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.setFixedSize(38, 30)
        self.btn_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" color: {COLORS['ink_soft']}; font-size: 15px; border-radius: 6px; }}"
            f"QPushButton:hover {{ background-color: {COLORS['sage_soft']}; }}"
        )
        self.btn_toggle.clicked.connect(self.toggle_sidebar)
        layout.addWidget(self.btn_toggle, 0, Qt.AlignLeft)
        layout.addSpacing(10)

        # 导航按钮
        self.nav_buttons = []
        for label, short, idx in NAV_ITEMS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, i=idx: self._switch_to(i))
            self.nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        ver = QLabel("  v2.0")
        ver.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        layout.addWidget(ver)

        self._apply_sidebar_style(expanded=True)
        return sidebar

    def _apply_sidebar_style(self, expanded: bool):
        """按展开/收起两态套用按钮样式与对齐。"""
        for (label, short, idx), btn in zip(NAV_ITEMS, self.nav_buttons):
            btn.setText(label if expanded else "\n".join(short))
            if expanded:
                btn.setFixedHeight(34)
                btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                btn.setStyleSheet(
                    f"QPushButton {{ text-align: left; padding: 0 12px;"
                    f" border: none; border-radius: 6px; font-size: 13px;"
                    f" color: {COLORS['ink_soft']}; background: transparent; }}"
                    f"QPushButton:hover {{ background-color: {COLORS['rule']}; }}"
                    f"QPushButton:checked {{ background-color: {COLORS['sage_soft']};"
                    f" color: {COLORS['ink']}; font-weight: 600; }}"
                )
            else:
                btn.setFixedHeight(46)
                btn.setStyleSheet(
                    f"QPushButton {{ text-align: center; padding: 2px 0;"
                    f" border: none; border-radius: 6px; font-size: 12px;"
                    f" color: {COLORS['ink_soft']};"
                    f" background: transparent; }}"
                    f"QPushButton:hover {{ background-color: {COLORS['rule']}; }}"
                    f"QPushButton:checked {{ background-color: {COLORS['sage_soft']};"
                    f" color: {COLORS['ink']}; font-weight: 600; }}"
                )

    def toggle_sidebar(self):
        """折叠 / 展开侧边栏：文字导航 ↔ 日式竖排二字导航。"""
        self.sidebar_collapsed = not self.sidebar_collapsed
        expanded = not self.sidebar_collapsed
        self.sidebar.setFixedWidth(SIDEBAR_W if expanded else SIDEBAR_W_COLLAPSED)
        self._apply_sidebar_style(expanded=expanded)

    # ================================================================
    # 统一页头
    # ================================================================

    def _build_header(self):
        header = QWidget()
        header.setFixedHeight(40)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 0, 14, 0)

        self.page_title = QLabel("今日仪表盘")
        self.page_title.setStyleSheet(
            f"color: {COLORS['ink']}; font-size: 15px; font-weight: 600;"
        )
        layout.addWidget(self.page_title)
        layout.addStretch()

        today = datetime.now()
        weekdays = "一二三四五六日"
        date_text = today.strftime("%m月%d日") + " 周" + weekdays[today.weekday()]
        self.header_date = QLabel(date_text)
        self.header_date.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        layout.addWidget(self.header_date)

        return header

    @staticmethod
    def _header_rule():
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"background-color: {COLORS['rule']}; border: none;")
        line.setFixedHeight(1)
        return line

    # ================================================================
    # 导航
    # ================================================================

    def _switch_to(self, index):
        """切换到指定页面并刷新数据"""
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        self.page_title.setText(NAV_ITEMS[index][0])
        page = self.stack.widget(index)
        if hasattr(page, 'refresh'):
            page.refresh()

    def _setup_menu(self):
        bar = self.menuBar()

        file_menu = bar.addMenu("文件(&F)")
        file_menu.addAction("导出数据", self._export_data)
        file_menu.addAction("退出", self.close)

        view_menu = bar.addMenu("视图(&V)")
        view_menu.addAction("折叠侧边栏", self.toggle_sidebar)
        view_menu.addAction("今日仪表盘", lambda: self._switch_to(0))
        view_menu.addAction("情绪冲浪", lambda: self._switch_to(1))
        view_menu.addAction("事件回顾", lambda: self._switch_to(2))
        view_menu.addAction("历史记录", lambda: self._switch_to(3))

        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self._show_about)

    # ================================================================
    # SurfingWindow 回调：完成记录后跳转到事件回顾
    # ================================================================

    def on_surfing_finished(self, result):
        """情绪冲浪完成后的回调，跳转到事件回顾页面"""
        event_id = result.get('event_id') if isinstance(result, dict) else None
        if event_id:
            self.summary_page.show_event(event_id)
        self._switch_to(2)

    # ================================================================
    # 菜单动作
    # ================================================================

    def _export_data(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出数据", "", "JSON Files (*.json)"
        )
        if path:
            import json
            events = self.db.get_recent_events(limit=10000)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            QMessageBox.information(
                self, "导出成功", f"已导出 {len(events)} 条记录到:\n{path}"
            )

    def _show_about(self):
        QMessageBox.about(
            self, "关于心潮",
            "心潮 EmoWave v2.0\n\n"
            "个人情绪状态引擎 · 本地情绪追踪与校准\n"
            "所有数据仅存储在设备本地\n不上传任何个人信息"
        )

    def closeEvent(self, event):
        """关闭时清理数据库连接"""
        if hasattr(self, 'db') and self.db:
            self.db.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setFont(app_font(10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
