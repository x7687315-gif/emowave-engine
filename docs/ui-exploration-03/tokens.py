"""tokens.py — UI Exploration 03：中性骨架 + 单一语义蓝

与 02 的差别只有两点：
  1. 加入一个低饱和深海蓝（只在"位置"与"主操作"上出现）
  2. 曲线与基线重画（见 softcurve.py），令牌里因此多了曲线专用的蓝

中性色仍然严格 R=G=B：蓝是画面里**唯一**的色相。
文字只有三级合法灰（实测对比度见 docs/ui-exploration-03/00）。
"""
from PyQt5.QtGui import QFont

# ---- 中性（R=G=B）----
INK = '#1A1A1A'        # 15.96:1 于 paper
INK_2 = '#4D4D4D'      # 7.75:1
MUTED = '#6E6E6E'      # 4.68:1 —— 最小字号的下限
FAINT = '#B0B0B0'      # 1.99:1：只许画线 / 禁用态
RULE = '#D8D8D8'
GRID = '#EBEBEB'
PAPER = '#F5F5F5'
SURFACE = '#FFFFFF'

# ---- 语义蓝（低饱和深海）----
BLUE = '#3A6A88'       # 对白 5.84:1（图形与填充按钮都够）
BLUE_INK = '#2A5570'   # 小字专用，对 paper 7.4:1
BLUE_SOFT = '#E7EFF4'  # 选中底 / 常态带
BLUE_DEEP = '#1B374A'  # 记录中的反相底 / 按下态
BLUE_WASH = (58, 106, 136, 26)   # 置信带填充（alpha 26 ≈ 10%）

# ---- 几何 ----
WIN_W, WIN_H = 400, 680
WIN_MIN_W, WIN_MIN_H = 360, 600
PAD = 24
HAIRLINE = 1

# 曲线线型（两套页面共用，语义不随页面变）
LINE_W = 1.3
OBS_R = 2.6
CORR_SIZE = 6.0
WATER_W = 2.0
BASE_W = 1.0


def app_font(size=10, weight=QFont.Normal):
    from theme import app_font as f
    return f(size, weight)


def num_font(size=12, weight=QFont.Light):
    from theme import app_font_num as f
    return f(size, weight)


def build_qss():
    return f"""
    QWidget {{ background: {PAPER}; color: {INK}; }}
    QLabel {{ background: transparent; }}
    QToolTip {{ background: {SURFACE}; color: {INK};
        border: 1px solid {RULE}; padding: 4px 6px; font-size: 11px; }}
    QScrollBar:vertical {{ width: 6px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: {RULE}; border-radius: 3px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
    """
