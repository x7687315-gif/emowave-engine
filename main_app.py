#!/usr/bin/env python3
"""心潮 EmoWave 桌面应用入口（2.0 单页控制台）

所有内容集中在一个界面（ConsoleWindow）：实时状态 / 情绪曲线 / 实时调节 /
基线主权 / 个人模型 / 纠正 / 事件回顾 / 历史记录，自上而下单页呈现。
侧边栏为锚点导航（点击滚动到分区），可折叠为日式竖排二字。

后续由用户决定哪些分区放入隐藏式、哪些保留主界面。
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QFileDialog, QMessageBox, QSizePolicy,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from db import DatabaseManager
from session import SessionController
from widgets import COLORS, app_font
from windows.console_window import ConsoleWindow

SIDEBAR_W = 150
SIDEBAR_W_COLLAPSED = 54

STYLE_SHEET = f"""
QMainWindow {{ background-color: {COLORS['bg']}; }}
QToolTip {{
    background-color: {COLORS['surface']}; color: {COLORS['ink']};
    border: 1px solid {COLORS['rule']}; padding: 4px;
}}
"""

# (完整名, 收起态竖排二字, 锚点 key) — 7 项（≤7 导航 guideline），顺序同页面布局
NAV_ITEMS = [
    ("实时状态", "状态", "state"),
    ("情绪曲线", "曲线", "curve"),
    ("实时调节", "调节", "adjust"),
    ("纠正", "纠正", "correction"),
    ("基线主权", "基线", "baseline"),
    ("个人模型", "学习", "model"),
    ("回顾与历史", "回顾", "legacy"),
]


class MainWindow(QMainWindow):
    """主窗口：可折叠锚点侧栏 + 单页控制台 + 菜单栏"""

    def __init__(self, db=None):
        super().__init__()
        self.setWindowTitle("心潮 EmoWave · 个人情绪状态引擎")
        self.setMinimumSize(880, 620)
        self.resize(1020, 720)
        self.setStyleSheet(STYLE_SHEET)
        self.sidebar_collapsed = False

        if db is None:
            db = DatabaseManager()
        self.db = db
        self.session = SessionController(db)

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

        # 单页控制台（全部内容集中于此）
        self.console = ConsoleWindow(self.session, parent=self)
        content_layout.addWidget(self.console, stretch=1)
        main_layout.addWidget(content, stretch=1)

        self._setup_menu()
        self._set_active(0)

    # ================================================================
    # 侧边栏（锚点导航，可折叠）
    # ================================================================

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_W)
        sidebar.setStyleSheet(
            f"QFrame#Sidebar {{ background-color: {COLORS['bg']};"
            f" border: none; border-right: 1px solid {COLORS['rule']}; }}"
        )
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(2)

        self.btn_toggle = QPushButton("≡")
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.setFixedSize(38, 30)
        self.btn_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" color: {COLORS['muted']}; font-size: 15px; border-radius: 6px; }}"
            f"QPushButton:hover {{ background-color: {COLORS['accent_soft']};"
            f" color: {COLORS['accent']}; }}"
        )
        self.btn_toggle.clicked.connect(self.toggle_sidebar)
        layout.addWidget(self.btn_toggle, 0, Qt.AlignLeft)
        layout.addSpacing(8)

        self.nav_buttons = []
        for idx, (label, short, key) in enumerate(NAV_ITEMS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, i=idx: self._jump(i))
            self.nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()
        ver = QLabel("  v2.0")
        ver.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 10px;")
        layout.addWidget(ver)

        self._apply_sidebar_style(expanded=True)
        return sidebar

    def _apply_sidebar_style(self, expanded: bool):
        for (label, short, key), btn in zip(NAV_ITEMS, self.nav_buttons):
            btn.setText(label if expanded else "\n".join(short))
            if expanded:
                btn.setFixedHeight(28)
                btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                btn.setStyleSheet(
                    f"QPushButton {{ text-align: left; padding: 0 10px;"
                    f" border: none; border-left: 2px solid transparent;"
                    f" border-radius: 4px; font-size: 11px;"
                    f" color: {COLORS['ink_soft']}; background: transparent; }}"
                    f"QPushButton:hover {{ background-color: {COLORS['accent_soft']};"
                    f" color: {COLORS['ink']}; }}"
                    f"QPushButton:checked {{ background-color: {COLORS['accent_soft']};"
                    f" border-left: 2px solid {COLORS['accent']};"
                    f" color: {COLORS['ink']}; font-weight: 600; }}"
                )
            else:
                btn.setFixedHeight(42)
                btn.setStyleSheet(
                    f"QPushButton {{ text-align: center; padding: 2px 0;"
                    f" border: none; border-radius: 6px; font-size: 11px;"
                    f" color: {COLORS['ink_soft']}; background: transparent; }}"
                    f"QPushButton:hover {{ background-color: {COLORS['rule']}; }}"
                    f"QPushButton:checked {{ background-color: {COLORS['accent_soft']};"
                    f" color: {COLORS['ink']}; font-weight: 600; }}"
                )

    def toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        expanded = not self.sidebar_collapsed
        self.sidebar.setFixedWidth(SIDEBAR_W if expanded else SIDEBAR_W_COLLAPSED)
        self._apply_sidebar_style(expanded=expanded)

    def _jump(self, index):
        """锚点导航：滚动单页到对应分区并高亮侧栏项。"""
        self._set_active(index)
        self.console.scroll_to(NAV_ITEMS[index][2])

    def _set_active(self, index):
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        self.page_title.setText(NAV_ITEMS[index][0])

    # ================================================================
    # 页头
    # ================================================================

    def _build_header(self):
        header = QWidget()
        header.setFixedHeight(40)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 0, 14, 0)

        self.page_title = QLabel("实时状态")
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
    # 菜单
    # ================================================================

    def _setup_menu(self):
        bar = self.menuBar()
        file_menu = bar.addMenu("文件(&F)")
        file_menu.addAction("导出数据", self._export_data)
        file_menu.addAction("退出", self.close)

        view_menu = bar.addMenu("视图(&V)")
        view_menu.addAction("折叠侧边栏", self.toggle_sidebar)
        for i, (label, short, key) in enumerate(NAV_ITEMS):
            view_menu.addAction(label, lambda i=i: self._jump(i))

        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self._show_about)

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
