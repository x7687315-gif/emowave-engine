#!/usr/bin/env python3
"""心潮 EmoWave 桌面应用入口（v3 · 重做界面）

=== 与 v2 的差异 ===
1. 外壳极简：56px 图标列 + 一行页头，**删除菜单栏**
2. 主界面换为 windows/main_console.MainConsole（4 区块：状态 / 读数 / 曲线 / 输入）
3. 全局一次注入 theme.build_app_qss()（修 QSS 不继承 font/color 的老问题）
4. HighDPI 在 QApplication 创建**之前**打开（否则 Qt 忽略）

=== 为什么图标手绘而不用 SVG ===
QtSvg 不一定随 PyQt5 wheels 装好；构成主义硬边图形用 QPainter 直接描线更稳，
也更贴合"直角 / 无圆角 / 几何"的语言。
"""
import sys
import os
import json
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- HighDPI：必须在 QApplication 创建前设置 ----
from PyQt5.QtCore import Qt, QPointF
Qt.AA_EnableHighDpiScaling = True
Qt.AA_UseHighDpiPixmaps = True

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolButton, QLabel, QFrame, QFileDialog, QMessageBox, QSizePolicy,
)
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

from theme import (
    COLORS, METRICS, app_font, app_font_num, build_app_qss,
)
from windows.main_console import MainConsole
from windows.archetype_dialog import ArchetypeDialog
from emowave import ARCHETYPES, DEFAULT_ARCHETYPE_KEY, get_archetype

try:
    from db import DatabaseManager
except Exception:                                    # 无 DB 也能启动
    DatabaseManager = None

logger = logging.getLogger(__name__)


# ================================================================
# 手绘图标按钮（构成主义：直角、几何、无圆角）
# ================================================================
class IconButton(QToolButton):
    """56px 图标列 / 页头用的几何描线图标按钮。

    图标用 QPainter 在 16×16 逻辑框里画（坐标 2..14），不依赖字体字形覆盖。
    """

    #: kind -> 折线组（16×16 坐标系）
    _PATHS = {
        # 情绪曲线：一条折线（构成主义斜线）
        'curve': [((2, 11), (5, 5), (8, 9), (11, 3), (14, 7))],
        # 抽屉：三条不等长线 + 右侧竖框
        'drawer': [((2, 4), (9, 4)), ((2, 8), (9, 8)), ((2, 12), (6, 12)),
                   ((11, 3), (14, 3), (14, 13), (11, 13), (11, 3))],
        # 导出：下箭头 + 底线
        'export': [((8, 2), (8, 9)), ((5, 6), (8, 9), (11, 6)), ((3, 12), (13, 12))],
        # 设置：两条滑杆（杆 + 游标方块）
        'settings': [((2, 6), (14, 6)), ((2, 10), (14, 10))],
    }
    _KNOBS = {'settings': ((6, 6), (10, 10))}        # 游标方块中心

    # ---- 绘制几何常量（避免魔法值 P3C-STY-004）----
    _ICON_BOX = 16.0          # 逻辑坐标系边长
    _PEN_WIDTH = 1.6          # 描线粗细
    _ACTIVE_BAR_W = 2         # 当前视图指示条宽（px）
    _ACTIVE_BAR_TOP = 0.28    # 指示条顶端占高比
    _ACTIVE_BAR_H = 0.44      # 指示条高度占高比

    def __init__(self, kind: str, tooltip: str = "", size: int = 34, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._size = size
        self._active = False
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(False)
        self.setFixedSize(size, size)
        self.setStyleSheet("QToolButton { background: transparent; border: none; }")

    def set_active(self, on: bool):
        """当前视图高亮：active 时描线用 accent，并在左边缘画一条硬边指示条。"""
        if self._active != bool(on):
            self._active = bool(on)
            self.update()

    # ---- 绘制 ----
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        hovered = self.underMouse()
        emphasis = hovered or self._active
        color = QColor(COLORS['accent'] if emphasis else COLORS['ink_2'])
        if not self.isEnabled():
            color = QColor(COLORS['muted'])
        pen = QPen(color, self._PEN_WIDTH)
        pen.setCapStyle(Qt.SquareCap)
        pen.setJoinStyle(Qt.MiterJoin)
        p.setPen(pen)

        # 当前视图指示条：左边缘硬边竖线（构成主义，无圆角）
        if self._active:
            h = self.height()
            p.fillRect(0, int(h * self._ACTIVE_BAR_TOP), self._ACTIVE_BAR_W,
                       int(h * self._ACTIVE_BAR_H), QColor(COLORS['accent']))

        s = self._ICON_BOX
        ox = (self.width() - s) / 2.0
        oy = (self.height() - s) / 2.0
        for poly in self._PATHS.get(self.kind, []):
            pts = [QPointF(ox + x, oy + y) for (x, y) in poly]
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])
        for (kx, ky) in self._KNOBS.get(self.kind, ()):
            # 游标：实心方块（直角，非圆点）
            p.fillRect(int(ox + kx - 1.3), int(oy + ky - 2.6), 3, 5, color)
        p.end()


def _vline():
    ln = QFrame()
    ln.setFrameShape(QFrame.VLine)
    ln.setFixedWidth(1)
    ln.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
    return ln


# ================================================================
# 主窗口
# ================================================================
class MainWindow(QMainWindow):
    """外壳：图标列 + 极简页头 + 主控制台。"""

    def __init__(self, db=None):
        super().__init__()
        self.setWindowTitle("心潮 EmoWave · 个人情绪状态引擎")
        self.setMinimumSize(900, 640)
        self.resize(1180, 780)

        if db is None and DatabaseManager is not None:
            try:
                db = DatabaseManager()
            except Exception:
                # L2 优雅降级：本地库打不开不该阻止主界面启动。
                # 仅影响「导出数据」一个动作，该动作会提示未连接数据库。
                db = None
        self.db = db

        # 先解析精力人群先验（页头要显示它，主界面要用它 seed），必须在建 UI 前
        self._archetype_key = self._load_archetype_key()

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_rail())

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_header())
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
        rl.addWidget(rule)

        # ---- 主界面（自带内核 + 抽屉）----
        self.console = MainConsole(parent=self, db=self.db, archetype_key=self._archetype_key)
        rl.addWidget(self.console, 1)
        root.addWidget(right, 1)

    # ------------------------------------------------------------
    # 精力人群先验：读取 / 开局选择 / 重选
    # ------------------------------------------------------------
    def _load_archetype_key(self) -> str:
        saved = ""
        if self.db is not None:
            try:
                saved = self.db.get_state("archetype", "") or ""
            except Exception:
                saved = ""
        return saved if saved in ARCHETYPES else DEFAULT_ARCHETYPE_KEY

    def has_saved_archetype(self) -> bool:
        if self.db is None:
            return False
        try:
            return (self.db.get_state("archetype", "") or "") in ARCHETYPES
        except Exception:
            return False

    def prompt_archetype(self):
        """首启（无存档）时弹框让用户选精力人群；选定后持久化并 seed 主界面。"""
        if self.has_saved_archetype():
            return
        dlg = ArchetypeDialog(self._archetype_key, parent=self)
        if dlg.exec_() == ArchetypeDialog.Accepted:
            self.apply_archetype(dlg.selected())

    def apply_archetype(self, key: str):
        """应用（或切换）精力人群先验：重 seed 主界面 + 持久化。"""
        if key not in ARCHETYPES:
            return
        self._archetype_key = key
        self.console.set_archetype(key)
        if self.db is not None:
            try:
                self.db.set_state("archetype", key)
            except Exception as exc:
                logger.debug("持久化精力人群选择失败：%s", exc)

    def _repick_archetype(self):
        """页头按钮：随时重选精力类型。"""
        dlg = ArchetypeDialog(self._archetype_key, parent=self)
        if dlg.exec_() == ArchetypeDialog.Accepted:
            self.apply_archetype(dlg.selected())
            self.arch_btn.setText(
                "精力 · " + get_archetype(self._archetype_key).label + " ▾")

    # ------------------------------------------------------------
    # 图标列（56px）
    # ------------------------------------------------------------
    def _build_rail(self):
        rail = QFrame()
        rail.setObjectName("Rail")
        rail.setFixedWidth(METRICS['rail_w'])
        rail.setStyleSheet(
            f"QFrame#Rail {{ background: {COLORS['paper_2']};"
            f" border: none; border-right: 1px solid {COLORS['rule']}; }}"
        )
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 字标：竖排「心潮」（构成主义：竖排 + 硬边）
        mark = QLabel("心\n潮")
        mark.setFont(app_font(13, QFont.DemiBold))
        mark.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        mark.setStyleSheet(
            f"color: {COLORS['ink']}; padding: 14px 0 10px 0;"
            f" letter-spacing: 2px; background: transparent;"
        )
        lay.addWidget(mark)

        self.btn_curve = IconButton('curve', "情绪曲线（主界面）")
        self.btn_drawer = IconButton('drawer', "抽屉：基线 / 个人模型 / 回顾与历史")
        self.btn_curve.clicked.connect(self._select_curve)
        self.btn_drawer.clicked.connect(self._toggle_drawer)
        self.btn_curve.set_active(True)          # 启动即主界面（情绪曲线）
        for b in (self.btn_curve, self.btn_drawer):
            lay.addWidget(b, 0, Qt.AlignHCenter)

        lay.addStretch(1)

        ver = QLabel("v3")
        ver.setFont(app_font(9))
        ver.setAlignment(Qt.AlignHCenter)
        ver.setStyleSheet(f"color: {COLORS['muted']}; padding-bottom: 10px;"
                          f" background: transparent;")
        lay.addWidget(ver)
        return rail

    # ------------------------------------------------------------
    # 页头（46px，无菜单栏）
    # ------------------------------------------------------------
    def _build_header(self):
        head = QFrame()
        head.setObjectName("TopBar")
        head.setFixedHeight(METRICS['topbar_h'])
        lay = QHBoxLayout(head)
        lay.setContentsMargins(20, 0, 16, 0)
        lay.setSpacing(10)

        title = QLabel("情绪曲线")
        title.setFont(app_font(13, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['ink']}; background: transparent;")
        lay.addWidget(title)
        lay.addStretch(1)

        today = datetime.now()
        weekdays = "一二三四五六日"
        date_label = QLabel(today.strftime("%m月%d日") + " 周" + weekdays[today.weekday()])
        date_label.setFont(app_font(10))
        date_label.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        lay.addWidget(date_label)

        self.arch_btn = QToolButton()
        self.arch_btn.setText("精力 · " + get_archetype(self._archetype_key).label + " ▾")
        self.arch_btn.setCursor(Qt.PointingHandCursor)
        self.arch_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none;"
            f" color: {COLORS['ink_2']}; font-size: 11px; padding: 2px 6px; }}"
            f"QToolButton:hover {{ color: {COLORS['accent']}; }}"
        )
        self.arch_btn.clicked.connect(self._repick_archetype)
        lay.addWidget(self.arch_btn, 0, Qt.AlignVCenter)

        lay.addWidget(_vline())

        b_export = IconButton('export', "导出数据（JSON）", size=28)
        b_export.clicked.connect(self._export_data)
        b_about = IconButton('settings', "关于 / 参数", size=28)
        b_about.clicked.connect(self._show_about)
        for b in (b_export, b_about):
            lay.addWidget(b, 0, Qt.AlignVCenter)
        return head

    # ------------------------------------------------------------
    # 行为
    # ------------------------------------------------------------
    def _toggle_drawer(self):
        self.console.toggle_drawer()
        self._sync_rail_active()

    def _select_curve(self):
        # 情绪曲线 = 主界面：回到主视图即收起右侧抽屉，让曲线重新占满焦点。
        self.console.close_drawer()
        self._sync_rail_active()

    def _sync_rail_active(self):
        open_ = self.console.drawer.is_open()
        self.btn_drawer.set_active(open_)
        self.btn_curve.set_active(not open_)

    def _export_data(self):
        if self.db is None:
            QMessageBox.information(self, "暂无数据", "当前会话未连接本地数据库。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出数据", "", "JSON (*.json)")
        if not path:
            return
        events = self.db.get_recent_events(limit=10000)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        QMessageBox.information(self, "导出成功",
                                f"已导出 {len(events)} 条记录到:\n{path}")

    @staticmethod
    def _show_about():
        QMessageBox.about(
            None, "关于心潮",
            "心潮 EmoWave v3\n\n"
            "个人情绪状态引擎 · 本地情绪追踪与校准\n"
            "所有数据仅存储在设备本地，不上传任何个人信息。",
        )

    def closeEvent(self, event):
        # 关库失败不能拦住退出，否则用户点 × 关不掉窗口。
        if getattr(self, 'db', None):
            try:
                self.db.close()
            except Exception:
                pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setFont(app_font(10))
    app.setStyleSheet(build_app_qss())          # 全局一次注入（QSS 不继承）
    win = MainWindow()
    win.show()
    win.prompt_archetype()          # 首启选精力人群（有存档则跳过）
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
