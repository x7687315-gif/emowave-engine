"""gray_tokens.py — 灰度原型的唯一色源（严格 R=G=B，没有任何色相）

为什么严格中性：这一轮要证明的是**不靠颜色能不能建立层级**。
只要掺一点点色偏，"看起来还行"就可能来自那点色偏，而不是来自排版与空间。
所以这里所有值都满足 R=G=B，任何一个像素的 chroma 都是 0。

层级只允许用这几个手段：
    明度（ink → ink_2 → muted → rule → grid → paper）
    字号（32 / 22 / 13 / 11 / 10 / 9 / 8）
    字重（Medium / Normal / Light）
    字距（小标签 1.2–2.0px）
    空间（大留白 vs 密集行的对比）
禁止：边框堆叠、阴影、渐变、圆角卡片、彩色。
"""
from PyQt5.QtGui import QFont

# ================================================================
# 油墨（纯中性）
# ================================================================
INK = '#1A1A1A'        # 主文字、当前状态、主操作
INK_2 = '#4D4D4D'      # 次级文字
MUTED = '#6E6E6E'      # 极小标签（实测 4.68:1 于 paper，达 AA）
FAINT = '#B0B0B0'      # **只许画线**（基线/刻度/禁用态）。实测 1.99:1，禁止承载文字
RULE = '#D6D6D6'       # 唯一可见的线
GRID = '#EAEAEA'       # 网格 / 置信带边界
PAPER = '#F5F5F5'      # 页面底
SURFACE = '#FFFFFF'    # 需要"纸面"的地方（纠正框）
BAND = '#E4E4E4'       # 置信带填充（不透明，避免叠加歧义）
SILHOUETTE = '#E9E9E9'  # 方案 B 的偏差面积：承载'离常态多远'，必须看得见

# ================================================================
# 尺寸
# ================================================================
WIN_W, WIN_H = 400, 680
WIN_MIN_W, WIN_MIN_H = 360, 600
PAD = 24               # 页面左右主留白
HAIRLINE = 1

# 曲线几何：两套方案**完全相同**，这样"语义可辨识"的差异只来自排版而非线型
OBS_R = 3.0            # 观察点半径
LINE_W = 1.6           # 模型估计
FORECAST_W = 1.6       # 预测（虚线）
CORR_SIZE = 6.0        # 修正方框
CORR_W = 1.0
BASE_W = 1.0           # 基线


def app_font(size: int = 10, weight: int = QFont.Normal) -> QFont:
    from theme import app_font as _f
    return _f(size, weight)


def num_font(size: int = 12, weight: int = QFont.Light) -> QFont:
    from theme import app_font_num as _f
    return _f(size, weight)


def label_qss(color=MUTED, size=9, spacing=1.4, weight=QFont.DemiBold):
    """极小标签的统一样式：小字号 + 宽字距 = 不需要靠加粗来强调。"""
    return (f"color: {color}; background: transparent;"
            f" font-size: {size}px; letter-spacing: {spacing}px;"
            f" font-weight: {weight};")


def build_qss():
    """proto 用的一张轻量 QSS：只管滚动条、工具提示与按钮的基态。"""
    return f"""
    QWidget {{ background: {PAPER}; color: {INK}; }}
    QLabel {{ background: transparent; }}
    QToolTip {{
        background: {SURFACE}; color: {INK};
        border: 1px solid {RULE}; padding: 4px 6px; font-size: 11px;
    }}
    QScrollBar:vertical {{ width: 6px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: {RULE}; border-radius: 3px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
    """
