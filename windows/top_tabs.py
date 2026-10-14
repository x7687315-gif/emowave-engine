"""windows/top_tabs.py — 顶部 Tab 栏（小窗化导航）

=== 为什么是它 ===
v3 用左侧 56px 图标列承载「情绪曲线 / 抽屉」两个动作。改成手机比例小窗
（400×680）后，56px 占掉 14% 宽度，且抽屉 380px 比整个窗口还宽 —— 两者都
不再成立。因此：

  · 横向导航改成**顶部 Tab 栏**（40px），与小窗宽度无关（百分比均分）
  · 抽屉的三段内容（基线主权 / 个人模型 / 回顾历史）拆成两个**整页**
    Tab：模型、历史 —— 这正是手机 App 的标准信息架构

=== 硬边语言 ===
构成主义：圆角 0、无渐变、当前项用**底部 2px accent 硬边条**标识
（与 IconButton 左侧指示条同一语汇），不靠填充底色。

=== 对外接口 ===
    tabs = TopTabs(["曲线", "模型", "历史"])
    tabs.currentChanged.connect(self.stack.setCurrentIndex)
    tabs.set_current(1)        # 编程式切换（不会重复 emit）
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QPushButton, QButtonGroup
from PyQt5.QtGui import QFont

from theme import METRICS, app_font


class TopTabs(QFrame):
    """顶部分段 Tab。items 为标签文本列表，索引即页索引。"""

    #: int —— 当前页索引变化时发射
    currentChanged = pyqtSignal(int)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setObjectName("TopTabs")
        self.setFixedHeight(METRICS['tabbar_h'])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(18)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for i, text in enumerate(items):
            btn = QPushButton(text)
            btn.setObjectName("TabBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            # 按钮必须撑满栏高，底部 2px 指示条才会贴在栏的底边上
            btn.setFixedHeight(METRICS['tabbar_h'])
            btn.setFont(app_font(11, QFont.Medium))
            btn.clicked.connect(lambda _=False, idx=i: self.set_current(idx))
            self._group.addButton(btn, i)
            lay.addWidget(btn)

        lay.addStretch(1)

        # 默认第一页（曲线）
        self._group.button(0).setChecked(True)
        self._current = 0

    # ------------------------------------------------------------
    def button(self, index: int):
        """取第 index 个 Tab 按钮（测试/外壳用）。"""
        return self._group.button(index)

    def current_index(self) -> int:
        """当前页索引。

        注意**不能**用 `QButtonGroup.checkedId()`：可检查按钮的 click()
        会先把按钮翻成 checked 再发 clicked，那时 checkedId 已经是新页了，
        拿它做去重会让"点当前页/点新页"都误判成没有变化 → 信号不发、
        页面不切换（正是 v3 那个"死按钮"的同类回归）。
        """
        return self._current

    def set_current(self, index: int):
        """切换页。点当前页不重复发射（避免无谓刷新），但会把按钮显式状态补正。"""
        if index < 0 or index >= len(self._group.buttons()):
            return
        if index == self._current:
            # click() 可能把已是当前页的按钮翻成未选中，这里补回选中态
            self._group.button(index).setChecked(True)
            return
        self._current = index
        self._group.button(index).setChecked(True)
        self.currentChanged.emit(index)
