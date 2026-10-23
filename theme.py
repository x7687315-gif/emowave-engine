"""theme.py — 心潮 EmoWave 设计令牌与全局样式

=== 这一版的方向（Exploration 03 定稿）===
去卡片、去圆角面板、去多重描边：页面由**细线 + 留白 + 字号层级**组织。
中性三级灰承担全部结构，**低饱和深海蓝只表示"位置"**：
    当下（水位线）· 常态（基线）· 记录中的反相底 · 修正点

=== 为什么颜色仍然全部集中在这里 ===
Qt 的 QSS 不继承 font/color；旧代码逐控件 setStyleSheet 导致"每个区块不像一套东西"。
现在：app 级 QSS 一次注入，绘制代码只读令牌。

=== 对比度（WCAG 2.1，对 paper #F5F5F5 实测）===
  ink 15.96 · ink_2 7.75 · muted 4.68 · accent_ink 7.32
  accent 5.35（图形/填充按钮） · surface on accent_deep 12.41
  faint 1.99 **只许画线**，不得承载文字（见 docs/ui-exploration-03/00-设计说明.md）
"""
from PyQt5.QtGui import QFont

# ================================================================
# 色彩令牌
# ================================================================
COLORS = {
    # ---- 中性：R=G=B，画面里唯一被允许的色相是蓝 ----
    'paper':       '#F5F5F5',   # 页面底
    'paper_2':     '#EDEDED',   # 次级块面
    'surface':     '#FFFFFF',   # 输入底 / 需要"纸面"的地方

    'ink':         '#1A1A1A',   # 主文字、状态句、曲线    15.96:1
    'ink_2':       '#4D4D4D',   # 次级文字、观察点         7.75:1
    'muted':       '#6E6E6E',   # 极小标签（字号下限）     4.68:1
    'faint':       '#B0B0B0',   # 1.99:1：只许画线 / 禁用态，不得承载正文

    'rule':        '#D8D8D8',   # 唯一可见的分隔线
    'grid':        '#EBEBEB',   # 网格 / 静态层

    # ---- 语义蓝：只表示"位置"与"会改变位置的操作" ----
    'accent':      '#3A6A88',   # 水位线、常态、主按钮     5.35:1
    'accent_ink':  '#2A5570',   # 小字专用                7.32:1
    'accent_soft': '#E7EFF4',   # 选中底 / 常态带
    'accent_deep': '#1B374A',   # 记录中反相底 / 按下态（白字 12.41:1）

    'danger':      '#2A5570',   # 沿用同色相约定，不引入红色
    'wash':        'rgba(58, 106, 136, 0.10)',   # 置信带

    # ---- 兼容旧键名（生产代码仍在读）----
    'bg':          '#F5F5F5',
    'ink_soft':    '#4D4D4D',
    'cyan':        '#6E6E6E',   # 基线参考线（curve_widget 读这个键）
}

# ================================================================
# 字体
# ================================================================
FONT_FAMILY = "Bahnschrift"          # 几何 DIN 感（本机已装）
FONT_FAMILY_FALLBACK = "Segoe UI"
MONO_FAMILY = "Cascadia Code"
MONO_FAMILY_FALLBACK = "Consolas"


def app_font(size: int = 10, weight: int = QFont.Normal) -> QFont:
    f = QFont(FONT_FAMILY, int(round(size)), weight)
    f.setStyleHint(QFont.SansSerif)
    return f


def app_font_num(size: int = 18, weight: int = QFont.Light) -> QFont:
    f = QFont(MONO_FAMILY, int(round(size)), weight)
    f.setStyleHint(QFont.Monospace)
    return f


# ================================================================
# 尺寸与动效
# ================================================================
# 圆角只留给**控件**（按钮/输入框/滑条），面板一律直角 —— 新版没有卡片。
METRICS = {
    'tabbar_h':     40,
    'topbar_h':     44,
    'mini_w':       360,
    'mini_h':       180,
    'radius':       8,      # 列表等大面积控件
    'radius_sm':    4,      # 按钮 / 输入 / 滑条
    'radius_mini': 10,
}

# ================================================================
# 曲线语义 —— 四种元素，四种画法，**不随页面变化**
# ================================================================
# 观察 实心圆（我说的）· 估计 实线（模型算的）· 预测 虚线（猜的）
# 修正 空心方框（我改的）· 基线 点线（我的常态）
CURVE_STYLE = {
    'observation': {'color': (77, 77, 77, 150), 'radius': 2.6},
    'model':       {'color': 'ink', 'width': 1.3, 'dash': False},
    'forecast':    {'color': 'ink_2', 'width': 1.3, 'dash': True},
    'correction':  {'color': 'accent_ink', 'width': 1.0, 'size': 6.0},
    'band':        {'color': 'wash'},
    'baseline':    {'color': 'cyan', 'width': 1.0, 'dash': True},
}

EASE_OUT = "cubic-bezier(0.165, 0.84, 0.44, 1)"
DUR_FAST = 130
DUR_MID = 220
DUR_SLOW = 320


# ================================================================
# 全局 QSS —— 一次性设在 QApplication 上
# ================================================================
def build_app_qss(c=None) -> str:
    """返回 app 级样式表。

    关键：Qt 的 QSS **不继承** font/color，所以这里对常用类型统一声明。
    但绝不声明 font-family/size —— 类型选择器会覆盖代码里 setFont() 的层级字体。
    默认字体由 QApplication.setFont(app_font(10)) 提供。
    """
    c = dict(COLORS if c is None else c)
    fam = FONT_FAMILY
    r = METRICS['radius']
    rs = METRICS['radius_sm']
    return f"""
/* ---------- 基础 ---------- */
QWidget {{
    background-color: {c['paper']};
    color: {c['ink']};
}}
QLabel {{ background: transparent; color: {c['ink']}; }}
QToolTip {{
    background-color: {c['surface']};
    color: {c['ink']};
    border: 1px solid {c['rule']};
    border-radius: {rs}px;
    padding: 4px 6px;
    font-size: 11px;
}}

/* ---------- 按钮：细边、小圆角，无渐变 ---------- */
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
    color: {c['accent_ink']};
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
QPushButton:focus, QSlider:focus, QLineEdit:focus, QComboBox:focus,
QListWidget:focus, QTextEdit:focus {{
    outline: 2px solid {c['accent_ink']};
    outline-offset: 1px;
}}

/* ---------- 主按钮 ---------- */
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
QPushButton[primary="true"]:pressed {{ background-color: {c['accent_deep']}; }}

/* ---------- 危险按钮 ---------- */
QPushButton[danger="true"] {{ color: {c['danger']}; border-color: {c['danger']}; }}
QPushButton[danger="true"]:hover {{
    background-color: {c['danger']};
    color: {c['surface']};
}}

/* ---------- 无边框按钮 ---------- */
QPushButton[flat="true"] {{
    background-color: transparent;
    border: none;
    border-radius: {rs}px;
    padding: 0px;
}}
QPushButton[flat="true"]:hover {{
    background-color: {c['accent_soft']};
    border: none;
    color: {c['accent_ink']};
}}
QPushButton[flat="true"]:checked {{ background-color: {c['accent']}; }}

/* ---------- 滑条：细轨 + 圆点 ---------- */
QSlider::groove:horizontal {{ height: 2px; background: {c['rule']};
    border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; height: 2px;
    border-radius: 1px; }}
QSlider::add-page:horizontal {{ background: {c['rule']}; height: 2px;
    border-radius: 1px; }}
QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -4px 0px;
    background: {c['accent']}; border: none; border-radius: 5px; }}
QSlider::handle:horizontal:hover {{ background: {c['accent_ink']}; }}
QSlider::handle:horizontal:pressed {{ background: {c['accent_deep']}; }}

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
QListWidget::item {{ padding: 5px 8px; border: none; }}
QListWidget::item:selected {{ background-color: {c['accent_soft']};
    color: {c['accent_ink']}; }}
QListWidget::item:hover {{ background-color: {c['paper_2']}; }}

/* ---------- 滚动区 ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0px; }}
QScrollBar::handle:vertical {{ background: {c['rule']}; border-radius: 4px;
    min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {c['rule']}; border-radius: 4px;
    min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

/* ---------- 分组框：直角、只用一条上边线 ---------- */
QGroupBox {{
    background-color: transparent;
    border: none;
    border-top: 1px solid {c['rule']};
    margin-top: 12px;
    padding: 12px 0px 8px 0px;
    font-size: 9.5px;
    letter-spacing: 1.6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 0px;
    padding: 0px 6px;
    color: {c['muted']};
    font-weight: 600;
}}

/* ---------- 顶部 Tab ---------- */
QFrame#TopTabs {{
    background-color: {c['paper']};
    border: none;
    border-bottom: 1px solid {c['rule']};
}}
QPushButton#TabBtn {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    /* Tab 的底部硬边条是导航语义，不跟随容器圆角 */
    border-radius: 0px;
    color: {c['muted']};
    font-family: "{fam}", "{FONT_FAMILY_FALLBACK}", sans-serif;
    letter-spacing: 1.2px;
    padding: 0px;
}}
QPushButton#TabBtn:hover {{ color: {c['ink']}; }}
QPushButton#TabBtn:checked {{
    color: {c['accent_ink']};
    border-bottom: 2px solid {c['accent']};
}}

/* ---------- 页面容器：新版没有卡片，Section 退化成一条上边线 ---------- */
QScrollArea#PageScroll, QScrollArea#PageScroll > QWidget {{
    background: transparent;
    border: none;
}}
QWidget#PageBody {{ background: {c['paper']}; }}
QFrame#TopBar {{
    background-color: {c['paper']};
    border-bottom: 1px solid {c['rule']};
}}
QFrame#CurvePanel {{ background-color: transparent; border: none; }}
QFrame#Section {{
    background-color: transparent;
    border: none;
    border-top: 1px solid {c['rule']};
}}
"""
