#!/usr/bin/env python3
"""心潮 EmoWave 桌面应用入口（v3.1 · 小窗化重做界面）

=== 与 v3 的差异 ===
1. 窗口从 1180×780 缩到 **400×680**（约 9:15.5 竖屏，为手机迁移打底）
2. **删除左侧 56px 图标列**，横向导航换成**顶部 Tab 栏**（40px，见 windows/top_tabs.py）
3. **删除右侧 380px 抽屉**，其内容拆成两个整页 Tab：模型 / 历史
   （抽屉比小窗本身还宽，物理上无法并排）
4. 次要内容页（模型 / 历史）包进可滚动整页，小窗下靠下的区块才够得着
5. HighDPI 从「给枚举对象赋值的空操作」改成 `QApplication.setAttribute(...)`
   —— 原写法 `Qt.AA_EnableHighDpiScaling = True` Qt 根本不读，必须在
   QCoreApplication 构造前调用静态 setAttribute

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

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolButton, QLabel, QFrame, QFileDialog, QMessageBox, QSizePolicy,
)
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

from theme import (
    COLORS, METRICS, app_font, app_font_num, build_app_qss,
)
from windows.top_tabs import TopTabs
from windows.mini_window import MiniWindow
from windows.main_console import MainConsole
from windows.archetype_dialog import ArchetypeDialog
from emowave import ARCHETYPES, DEFAULT_ARCHETYPE_KEY, get_archetype

try:
    from db import DatabaseManager
except Exception:                                    # 无 DB 也能启动
    DatabaseManager = None

logger = logging.getLogger(__name__)

# ---- 小窗化后的窗口尺寸（手机比例；可拉大但布局保持单列）----
WIN_W, WIN_H = 400, 680
WIN_MIN_W, WIN_MIN_H = 360, 600


# ================================================================
# 手绘图标按钮（构成主义：直角、几何、无圆角）
# ================================================================
class IconButton(QToolButton):
    """页头用的几何描线图标按钮。

    图标用 QPainter 在 16×16 逻辑框里画（坐标 2..14），不依赖字体字形覆盖。
    """

    #: kind -> 折线组（16×16 坐标系）
    _PATHS = {
        # 情绪曲线：一条折线（构成主义斜线）
        'curve': [((2, 11), (5, 5), (8, 9), (11, 3), (14, 7))],
        # 导出：下箭头 + 底线
        'export': [((8, 2), (8, 9)), ((5, 6), (8, 9), (11, 6)), ((3, 12), (13, 12))],
        # 设置：两条滑杆（杆 + 游标方块）
        'settings': [((2, 6), (14, 6)), ((2, 10), (14, 10))],
        # 迷你挂件：右下角小窗（一个实心方块 + 右下指向的角标）
        'mini': [((2, 4), (9, 4), (9, 11), (2, 11), (2, 4))],
    }
    _KNOBS = {'settings': ((6, 6), (10, 10))}        # 游标方块中心

    # ---- 绘制几何常量（避免魔法值 P3C-STY-004）----
    _ICON_BOX = 16.0          # 逻辑坐标系边长
    _PEN_WIDTH = 1.6          # 描线粗细

    def __init__(self, kind: str, tooltip: str = "", size: int = 28, parent=None):
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
        """高亮态：hover/激活时描线用 accent。"""
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


def _hrule():
    ln = QFrame()
    ln.setFixedHeight(1)
    ln.setStyleSheet(f"background: {COLORS['rule']}; border: none;")
    return ln


# ================================================================
# 主窗口
# ================================================================
class MainWindow(QMainWindow):
    """外壳：页头 + 顶部 Tab + 三页（曲线 / 模型 / 历史）。"""

    def __init__(self, db=None):
        super().__init__()
        self.setWindowTitle("心潮 EmoWave · 个人情绪状态引擎")
        self.setMinimumSize(WIN_MIN_W, WIN_MIN_H)
        self.resize(WIN_W, WIN_H)

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
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(_hrule())

        # ---- 顶部 Tab（替代原 56px 图标列 + 380px 抽屉）----
        self.tabs = TopTabs(["曲线", "模型", "历史"])
        root.addWidget(self.tabs)

        # ---- 主控制台（自带三页；外壳只负责导航）----
        self.console = MainConsole(parent=central, db=self.db,
                                   archetype_key=self._archetype_key)
        root.addWidget(self.console, 1)

        # ---- 迷你置顶窗（与主窗共用同一个 presenter；阶段 4）----
        # 必须持有引用，否则无父窗口对象会被 GC 回收，表现为"点开就闪退"
        self.mini = MiniWindow(self.console.presenter)

        self.tabs.currentChanged.connect(self.console.show_page)

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
    # 页头（44px，无菜单栏）
    # ------------------------------------------------------------
    def _build_header(self):
        head = QFrame()
        head.setObjectName("TopBar")
        head.setFixedHeight(METRICS['topbar_h'])
        lay = QHBoxLayout(head)
        lay.setContentsMargins(14, 0, 10, 0)
        lay.setSpacing(8)

        title = QLabel("情绪曲线")
        title.setFont(app_font(12, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['ink']}; background: transparent;")
        lay.addWidget(title)
        lay.addStretch(1)

        today = datetime.now()
        weekdays = "一二三四五六日"
        # ⚠️ 不要用 `today.strftime("%m月%d日")`：strftime 会把格式串里的
        # 中文字面量按**系统 locale 代码页**编码，非中文 ANSI 代码页的机器
        # （如 en-US/1252 的 CI runner）会直接 UnicodeEncodeError。
        # 格式化只让 strftime 做 %m/%d（纯 ASCII 指令），中文由 f-string 拼。
        date_label = QLabel(
            f"{today.strftime('%m')}月{today.strftime('%d')}日"
            f" 周{weekdays[today.weekday()]}")
        date_label.setFont(app_font(9))
        date_label.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        lay.addWidget(date_label)

        self.arch_btn = QToolButton()
        self.arch_btn.setText("精力 · " + get_archetype(self._archetype_key).label + " ▾")
        self.arch_btn.setCursor(Qt.PointingHandCursor)
        self.arch_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none;"
            f" color: {COLORS['ink_2']}; font-size: 10px; padding: 2px 4px; }}"
            f"QToolButton:hover {{ color: {COLORS['accent']}; }}"
        )
        self.arch_btn.clicked.connect(self._repick_archetype)
        lay.addWidget(self.arch_btn, 0, Qt.AlignVCenter)

        lay.addWidget(_vline())

        # 迷你挂件开关（置顶悬浮窗，与主窗共用同一内核状态）
        self.b_mini = IconButton('mini', "迷你挂件（置顶悬浮窗）", size=28)
        self.b_mini.set_active(True)
        self.b_mini.clicked.connect(self._toggle_mini)
        lay.addWidget(self.b_mini, 0, Qt.AlignVCenter)

        b_export = IconButton('export', "导出数据（JSON）", size=28)
        b_export.clicked.connect(self._export_data)
        b_about = IconButton('settings', "关于 / 参数", size=28)
        b_about.clicked.connect(self._show_about)
        for b in (b_export, b_about):
            lay.addWidget(b, 0, Qt.AlignVCenter)
        return head

    def _toggle_mini(self):
        """唤起/收起迷你挂件。挂件与主窗共享 presenter，两边状态实时一致。"""
        if self.mini.isVisible():
            self.mini.hide()
            self.b_mini.set_active(False)
        else:
            self.mini.show()
            self.b_mini.set_active(True)

    # ------------------------------------------------------------
    # 行为
    # ------------------------------------------------------------
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
            "心潮 EmoWave v3.1\n\n"
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
        # 主窗关闭时一起收掉挂件，避免残留一个置顶小窗
        if getattr(self, 'mini', None) is not None:
            try:
                self.mini.close()
            except Exception:
                pass
        super().closeEvent(event)


def main():
    # HighDPI：必须在 QApplication 构造**之前**用静态 setAttribute。
    # v3 曾写成 `Qt.AA_EnableHighDpiScaling = True` —— 那只是给枚举对象
    # 加了个同名属性，Qt 从不读取，属于空操作（125% 缩放下字体放大而
    # 区块高度不变，按钮文字会被裁）。
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setFont(app_font(10))
    app.setStyleSheet(build_app_qss())          # 全局一次注入（QSS 不继承）
    win = MainWindow()
    win.show()
    win.prompt_archetype()          # 首启选精力人群（有存档则跳过）
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
