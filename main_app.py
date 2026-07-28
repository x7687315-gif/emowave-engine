#!/usr/bin/env python3
"""心潮 EmoWave 桌面情绪追踪应用入口

集成四个功能窗口：今日仪表盘、情绪冲浪、事件回顾、历史记录。
通过侧边栏导航和菜单栏提供完整操作入口。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QStackedWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QFileDialog, QMessageBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from db import DatabaseManager
from session import SessionController
from windows.dashboard_window import DashboardWindow
from windows.surfing_window import SurfingWindow
from windows.event_summary_window import EventSummaryWindow
from windows.history_window import HistoryWindow

STYLE_SHEET = """
QMainWindow { background-color: #f6f4f0; }
QListWidget { border: none; background-color: transparent; }
QPushButton:hover { opacity: 0.85; }
"""


class MainWindow(QMainWindow):
    """主窗口：侧边栏导航 + 四页面栈 + 菜单栏"""

    def __init__(self, db=None):
        super().__init__()
        self.setWindowTitle("心潮 EmoWave · 情绪追踪")
        self.setMinimumSize(900, 650)
        self.setStyleSheet(STYLE_SHEET)

        # 初始化数据库和会话
        if db is None:
            db = DatabaseManager()
        self.db = db
        self.session = SessionController(db)

        # 中心区域
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 侧边栏
        sidebar = self._build_sidebar()
        main_layout.addWidget(sidebar)

        # 页面栈
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, stretch=1)

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

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("""
            QFrame { background-color: #2d2a26; border: none; }
            QLabel { color: #e8e4dc; font-size: 16px; font-weight: bold; }
            QPushButton {
                color: #c0bbb4; text-align: left; padding: 12px 16px;
                border: none; font-size: 14px; background: transparent;
            }
            QPushButton:checked { background-color: #3d3a36; color: #ffffff; }
        """)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(0)

        logo = QLabel("  心潮 EmoWave")
        logo.setFixedHeight(50)
        layout.addWidget(logo)
        layout.addSpacing(20)

        self.nav_buttons = []
        nav_items = [
            ("今日仪表盘", 0),
            ("情绪冲浪", 1),
            ("事件回顾", 2),
            ("历史记录", 3),
        ]
        for label, idx in nav_items:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, i=idx: self._switch_to(i))
            self.nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        ver = QLabel("  v0.1.0")
        ver.setStyleSheet("color: #6a655e; font-size: 11px;")
        layout.addWidget(ver)

        return sidebar

    def _switch_to(self, index):
        """切换到指定页面并刷新数据"""
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        page = self.stack.widget(index)
        if hasattr(page, 'refresh'):
            page.refresh()

    def _setup_menu(self):
        bar = self.menuBar()

        file_menu = bar.addMenu("文件(&F)")
        file_menu.addAction("导出数据", self._export_data)
        file_menu.addAction("退出", self.close)

        view_menu = bar.addMenu("视图(&V)")
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
            events = self.db.get_recent_events(limit=10000)
            import json
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            QMessageBox.information(
                self, "导出成功", f"已导出 {len(events)} 条记录到:\n{path}"
            )

    def _show_about(self):
        QMessageBox.about(
            self, "关于心潮",
            "心潮 EmoWave v0.1.0\n\n"
            "本地情绪追踪与校准引擎\n"
            "所有数据仅存储在设备本地\n"
            "不上传任何个人信息"
        )

    def closeEvent(self, event):
        """关闭时清理数据库连接"""
        if hasattr(self, 'db') and self.db:
            self.db.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("PingFang SC", 11))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
