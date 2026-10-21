"""theme.py — 心潮 EmoWave 设计令牌与全局样式（构成骨架 + 暖色板）

=== 为什么要有这个模块 ===
上一版 UI 的「廉价感」根因有两个，都在这里一次性解决：

1. **QSS 不继承 font/color**（Qt 官方限制）
   旧代码在循环里对每个控件 `setStyleSheet(...)`。Qt 的 QSS **不会**像 CSS 那样
   把 font/color 继承给子控件，所以每个没被显式设置过的子控件都回退到系统默认观感
   ——这就是"每个区块看起来不是一套东西"的原因。
   → 本模块输出**一份 app 级 QSS**，在 QApplication 上 setStyleSheet 一次。

2. **token 散落硬编码**
   旧 widgets.py 里 rgba(30,64,175,26)（置信带）、QColor(156,150,138,120)（原始点）
   这些裸数值写死在 paintEvent 里，改色板必须逐个翻源码。
   → 所有颜色集中在本文件 COLORS，绘制代码只引用 token。

=== 设计方向 ===
构成主义骨架（大块面 / 不对称 / 几何 / 无衬线）+ 暖色板 + **柔和圆角**。
不是纯构成主义的黑白红，也不是通用商业后台模板：
  - 骨架：块面靠**明度和面积**分层，而非描边和阴影
  - 圆角：容器/按钮/输入框/滑块用小幅圆角（4–10px），避免过度切割
  - 色板：全画面**单一色相**（陶土赭红），只靠明度分层级
  - 参考：Claude Code 暖色调（暖黄纸底、低饱和、柔和、色彩统一）

=== 对比度（WCAG，对 --surface #FCFAF6 实测）===
  ink 14.6 · ink2 7.8 · muted 5.1 · accent-ink 5.1 · danger 7.4   —— 全部 ≥ 4.5
  accent #BE5E38 4.16 → 仅用于**图形与大号数字**（图形阈值 3:1），小字一律用 accent-ink
"""
from PyQt5.QtGui import QFont

# ================================================================
# 色彩令牌
# ================================================================
COLORS = {
    # ---- 暖中性：靠明度分层，不靠描边 ----
    'paper':       '#F2ECE1',   # 暖黄纸底（页面底）
    'paper_2':     '#E9E1D3',   # 次级块面（图标列 / 抽屉底）
    'surface':     '#FCFAF6',   # 曲线纸面（比底亮，靠明度浮起）

    # ---- 文字（全部经 WCAG 实测）----
    'ink':         '#2B251C',   # 暖黑 · 主文字 / 大数值   14.6:1
    'ink_2':       '#574E40',   # 次级正文                  7.8:1
    'muted':       '#746A5A',   # 极小标签                  5.1:1
    # 注意：旧色板 muted 为 #9E9384，实测仅 2.89:1，
    #       用在 9.5px 极小标签上属严重违规，已弃用。

    # ---- 线条（非文字，不要求 4.5）----
    'rule':        '#DFD6C6',   # 暖边（唯一描边色）
    'grid':        '#E6DED0',   # 网格线（更淡）

    # ---- 强调：单一色相，只靠明度分层 ----
    'accent':      '#BE5E38',   # 陶土赭红 · 图形与大号数字（3:1 图形阈值达标）
    'accent_ink':  '#A95230',   # 陶土赭红 · 小号文字专用   5.1:1
    'accent_soft': '#F1E0D5',   # 强调浅底（hover / 选中）
    'accent_deep': '#8C3A1E',   # 同色相加深（危险 / pressed）7.4:1

    'danger':      '#8C3A1E',   # 危险操作 —— 同色相加深，不引入新色相
    'wash':        'rgba(190, 94, 56, 0.10)',   # 置信带填充

    # ---- 兼容旧 widgets.py 的键名（渐进迁移用，指向同一暖色板）----
    'bg':          '#F2ECE1',
    'ink_soft':    '#574E40',
    'cyan':        '#C9A227',   # 基线参考线：改暖赭黄，不引入冷色
}

# ================================================================
# 字体
# ================================================================
FONT_FAMILY = "Bahnschrift"          # 几何 DIN 感，构成主义标题 / 读数（本机已装）
FONT_FAMILY_FALLBACK = "Segoe UI"    # 回退
MONO_FAMILY = "Cascadia Code"        # 等宽数值（tabular 感）
MONO_FAMILY_FALLBACK = "Consolas"


def app_font(size: int = 10, weight: int = QFont.Normal) -> QFont:
    """界面字体：Bahnschrift（几何无衬线）→ Segoe UI 回退。"""
    f = QFont(FONT_FAMILY, size, weight)
    f.setStyleHint(QFont.SansSerif)
    return f


def app_font_num(size: int = 18, weight: int = QFont.Light) -> QFont:
    """仪器数值字体：等宽，tabular 感。"""
    f = QFont(MONO_FAMILY, size, weight)
    f.setStyleHint(QFont.Monospace)
    return f


# ================================================================
# 尺寸与动效令牌
# ================================================================
# v3 → 小窗化（阶段 1）的变更：
#   · rail_w 56      → 已删除，左侧图标列让位给顶部 Tab 栏 tabbar_h
#   · drawer_w 380   → 已删除，右侧抽屉改整页（模型 / 历史 Tab）
#   · topbar_h 46→44，新增 tabbar_h 40、statehead_h 56（阶段 2 合并状态头）
METRICS = {
    'tabbar_h':     40,      # 顶部 Tab 栏高（替代原来的 56px 图标列）
    'topbar_h':     44,      # 极简页头高
    'mini_w':       360,     # 迷你置顶窗宽（阶段 4）
    'mini_h':       180,     # 迷你置顶窗高（阶段 4）
    # v5 Phase 5：从“全站直角”改为“骨架几何 + 柔和圆角”。
    # 圆角只走令牌：卡片/面板 r，按钮/输入框/滑块 r_sm，悬浮窗 r_mini。
    'radius':       8,
    'radius_sm':    4,
    'radius_mini': 10,
}

# ================================================================
# 曲线语义 —— 四种元素，四种画法，**不随页面变化**
# ================================================================
# 为什么单独列出来：曲线是全应用信息密度最高的地方，用户要靠"形状"分辨
# 什么是自己说的、什么是模型算的、什么是猜的、什么是自己改的。
# 这四种语义一旦在某个页面被画混（例如预测用实线），整个产品的可信度就塌了。
#
# 约定：
#   observation 实心圆（弱）→ 我说的
#   model       实线        → 模型算的
#   forecast    虚线        → 猜的
#   correction  空心方框    → 我改的
CURVE_STYLE = {
    'observation': {'color': (156, 150, 138, 120), 'radius': 2.2},
    'model':       {'color': 'accent', 'width': 2.0, 'dash': False},
    'forecast':    {'color': 'accent', 'width': 2.0, 'dash': True},
    'correction':  {'color': 'accent_ink', 'width': 1.2, 'size': 6.0},
    'band':        {'color': 'wash'},
    'baseline':    {'color': 'cyan', 'width': 1.0, 'dash': True},
}

# 指数缓动（ease-out-quart）—— 绝不回弹
EASE_OUT = "cubic-bezier(0.165, 0.84, 0.44, 1)"
DUR_FAST = 130
DUR_MID = 220
DUR_SLOW = 320


# ================================================================
# 全局 QSS —— 一次性设在 QApplication 上
# ================================================================
def build_app_qss(c=None) -> str:
    """返回 app 级样式表。

    关键：Qt 的 QSS **不继承** font/color 给子控件，所以这里对常用的
    QWidget/QLabel/QPushButton 等**类型选择器**统一声明字体与前景色。
    类型选择器特异性相同、后写覆盖先写 —— 因此全部集中在本函数内，
    不再散落到各控件的局部 setStyleSheet。
    """
    c = dict(COLORS if c is None else c)
    fam = FONT_FAMILY
    mono = MONO_FAMILY
    r = METRICS['radius']
    rs = METRICS['radius_sm']
    return f"""
/* ---------- 基础：字体与前景（QSS 不继承，故逐类型声明） ---------- */
QWidget {{
    background-color: {c['paper']};
    color: {c['ink']};
    /* 不在这里声明 font-family/font-size：QSS 类型选择器会覆盖
       代码里 setFont() 的层级字体（状态名 17、读数 18、等宽数字）。
       默认字体由 QApplication.setFont(app_font(10)) 提供。 */
}}
QLabel {{
    background: transparent;
    color: {c['ink']};
}}
QToolTip {{
    background-color: {c['surface']};
    color: {c['ink']};
    border: 1px solid {c['rule']};
    border-radius: {rs}px;
    padding: 4px 6px;
    font-size: 11px;
}}

/* ---------- 按钮：柔和圆角，无渐变 ---------- */
QPushButton {{
    background-color: transparent;
    color: {c['ink_2']};
    border: 1px solid {c['rule']};
    border-radius: {rs}px;
    padding: 6px 14px;
    font-size: 11.5px;
    font-family: "{fam}", "{FONT_FAMILY_FALLBACK}", sans-serif;
}}
QPushButton:hover {{
    border-color: {c['accent']};
    color: {c['accent']};
    background-color: {c['accent_soft']};
}}
QPushButton:pressed {{
    background-color: {c['accent_deep']};
    color: {c['surface']};
    border-color: {c['accent_deep']};
}}
QPushButton:disabled {{
    color: {c['muted']};
    border-color: {c['rule']};
    background-color: transparent;
}}
/* 键盘焦点可见（无障碍）—— QSS 无 :focus-visible，用 :focus + 强样式补足 */
QPushButton:focus, QSlider:focus, QLineEdit:focus, QComboBox:focus,
QListWidget:focus, QTextEdit:focus {{
    outline: 2px solid {c['accent_ink']};
    outline-offset: 1px;
}}

/* ---------- 主按钮（记录 / 提交）---------- */
QPushButton[primary="true"] {{
    background-color: {c['accent']};
    color: {c['surface']};
    border: none;
    border-radius: {rs}px;
    padding: 10px 20px;
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 1.4px;
}}
QPushButton[primary="true"]:hover {{
    background-color: {c['accent_ink']};
    color: {c['surface']};
}}
QPushButton[primary="true"]:pressed {{
    background-color: {c['accent_deep']};
}}

/* ---------- 危险按钮（重置）---------- */
QPushButton[danger="true"] {{
    color: {c['danger']};
    border-color: {c['danger']};
}}
QPushButton[danger="true"]:hover {{
    background-color: {c['danger']};
    color: {c['surface']};
}}

/* ---------- 无边框工具按钮（页头图标 / 图标列）---------- */
QPushButton[flat="true"] {{
    background-color: transparent;
    border: none;
    border-radius: {rs}px;
    padding: 0px;
}}
QPushButton[flat="true"]:hover {{
    background-color: {c['accent_soft']};
    border: none;
    color: {c['accent']};
}}
QPushButton[flat="true"]:checked {{
    background-color: {c['accent']};
}}

/* ---------- 滑条：细轨 + 圆形 thumb ---------- */
QSlider::groove:horizontal {{
    height: 2px;
    background: {c['rule']};
    border-radius: 1px;
}}
QSlider::sub-page:horizontal {{
    background: {c['accent']};
    height: 2px;
    border-radius: 1px;
}}
QSlider::add-page:horizontal {{
    background: {c['rule']};
    height: 2px;
    border-radius: 1px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -6px 0px;
    background: {c['accent']};
    border: none;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {c['accent_ink']};
    width: 16px;
    height: 16px;
    margin: -7px 0px;
    border-radius: 8px;
}}
QSlider::handle:horizontal:pressed {{
    background: {c['accent_deep']};
}}

/* ---------- 输入框 ---------- */
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
    background-color: {c['surface']};
    color: {c['ink']};
    border: 1px solid {c['rule']};
    border-radius: {rs}px;
    padding: 5px 8px;
    selection-background-color: {c['accent_soft']};
}}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {c['accent']};
}}

/* ---------- 列表 ---------- */
QListWidget {{
    background-color: {c['surface']};
    color: {c['ink']};
    border: 1px solid {c['rule']};
    border-radius: {r}px;
    font-size: 12px;
}}
QListWidget::item {{
    padding: 5px 8px;
    border: none;
}}
QListWidget::item:selected {{
    background-color: {c['accent_soft']};
    color: {c['accent_ink']};
}}
QListWidget::item:hover {{
    background-color: {c['paper_2']};
}}

/* ---------- 滚动区：细滚动条 ---------- */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    width: 8px;
    background: transparent;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {c['rule']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['muted']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    height: 8px;
    background: transparent;
}}
QScrollBar::handle:horizontal {{
    background: {c['rule']};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ---------- 分组框 ---------- */
QGroupBox {{
    background-color: {c['surface']};
    border: 1px solid {c['rule']};
    border-radius: {r}px;
    margin-top: 12px;
    padding: 12px 14px 10px 14px;
    font-size: 9.5px;
    letter-spacing: 1.6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0px 6px;
    color: {c['muted']};
    font-weight: 600;
}}

/* ---------- 顶部 Tab 栏（小窗化：替代 56px 图标列）---------- */
QFrame#TopTabs {{
    background-color: {c['paper']};
    border: none;
    border-bottom: 1px solid {c['rule']};
}}
QPushButton#TabBtn {{
    background: transparent;
    border: none;
    /* 底部指示条占位：未选中时透明，选中时 2px accent */
    border-bottom: 2px solid transparent;
    /* Tab 的底部硬边条是导航语义，不跟随容器圆角 */
    border-radius: 0px;
    color: {c['muted']};
    font-family: "{fam}", "{FONT_FAMILY_FALLBACK}", sans-serif;
    letter-spacing: 1.2px;
    padding: 0px;
}}
QPushButton#TabBtn:hover {{
    color: {c['ink']};
}}
QPushButton#TabBtn:checked {{
    color: {c['accent_ink']};
    border-bottom: 2px solid {c['accent']};
}}

/* ---------- 次要页滚动区（模型 / 历史 Tab）---------- */
QScrollArea#PageScroll, QScrollArea#PageScroll > QWidget {{
    background: transparent;
    border: none;
}}
QWidget#PageBody {{
    background: {c['paper']};
}}
QFrame#TopBar {{
    background-color: {c['paper']};
    border-bottom: 1px solid {c['rule']};
}}
QFrame#CurvePanel {{
    background-color: {c['surface']};
    border: 1px solid {c['rule']};
    border-radius: {r}px;
}}
QFrame#Section {{
    background-color: {c['surface']};
    border: 1px solid {c['rule']};
    border-radius: {r}px;
}}
"""
