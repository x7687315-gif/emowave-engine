"""palette_tokens.py — UI Exploration 01：A/B 两套语义色板（共享同一套结构）

=== 这份文件为什么存在 ===
生产 UI 的颜色全部来自 `theme.COLORS` / `theme.CURVE_STYLE` / `theme.METRICS`。
本文件**不修改**它们，只是按同样的键结构给出四组候选值：

    A 月光微金  ×  {light, dark}
    B 云白深海  ×  {light, dark}

原型脚本在 `import theme` 之后、`import main_app` 之前，把选中的一组值
**在进程内**合并进 `theme.COLORS`。文件不动、生产默认值不动。

=== 公平比较契约（硬性）===
A 与 B 只允许在这些键上不同：

    paper / paper_2 / surface / ink / ink_2 / muted / rule / grid /
    accent / accent_ink / accent_soft / accent_deep / danger / wash /
    bg / ink_soft / cyan

禁止不同的：字体族与字号、`METRICS`（含 radius / tabbar_h / topbar_h /
mini_w / mini_h）、`CURVE_STYLE` 的线宽与点径、边距、组件集合、演示数据。
结构差异会污染比较——所以一处都不差。

=== 中性色为什么也允许不同 ===
用户约束是「黑 / 白 / 石墨灰 / 浅灰为主体系」。A 的中性走**纯中性石墨**
（色相 ≈ 0，无色偏），B 的中性走**冷调云白 / 深海黑**（同明度、带蓝相）。
两者明度层级一一对齐（下表 L* 对齐），差别只在色温。这属于"与色板相配的
细微明暗层次"，不是给 B 额外的好处。

    A-light paper #F2F2F3  ↔  B-light paper #F1F4F6
    A-dark  paper #121315  ↔  B-dark  paper #0C1215

=== 暗色模式的令牌极性反转（两套共用的设计规则）===
生产色板只有亮色。补暗色时两条规则必须同时适用于 A 与 B，否则比较不公平：

1. **高度（elevation）方向反转**：亮色下 `surface` 比 `paper` **亮**（纸面浮起）；
   暗色下 `surface` 比 `paper` **亮**但整体仍低于中灰——即"越靠前越亮"。
2. **accent_deep 方向反转**：亮色 pressed 用**加深**的同色相；暗色没有向下空间，
   pressed 改为**提亮**的同色相。否则按下时按钮像消失了。
"""

# ================================================================
# A · 月光微金
# ================================================================
# 概念：月光 = 冷中性的高分辨白光；微金 = 只在"语义发生"的地方出现一丝金。
# 中性轴严格无色偏（R=G=B±2），让金色成为画面里**唯一**的彩色。
A_LIGHT = {
    'paper':      '#F2F2F3',   # 浅灰底（无色偏）
    'paper_2':    '#E6E7E8',   # 次级块面
    'surface':    '#FFFFFF',   # 曲线纸面：靠明度浮起

    'ink':        '#1A1B1D',   # 主文字 / 大数值
    'ink_2':      '#45484C',   # 次级正文
    'muted':      '#64676B',   # 极小标签（实测 ≥4.5 于 paper/paper_2/surface）

    'rule':       '#DCDDDF',   # 唯一描边色
    'grid':       '#E9EAEB',   # 网格线

    # 亮色下金色必须**压深**：#9C7F3C 做曲线好看（3.4:1 图形达标），
    # 但作为「开始记录」的底色装不下白字（3.8:1 < 4.5）。压到 L≈0.18 后
    # 同一支色既做曲线（4.7:1）又做按钮底（4.7:1）——代价是金变成"暗金/香槟"，
    # 少了发亮的感觉。这是 A 方案最贵的一处妥协，评估表里单独记。
    'accent':      '#8A6D2E',  # 微金：图形与大号数字
    'accent_ink':  '#6B5524',  # 微金：小号文字专用
    'accent_soft': '#F0EADB',  # 强调浅底（hover / 选中）
    'accent_deep': '#4A3B15',  # 同色相加深（pressed / danger）

    'danger':     '#4A3B15',   # 沿用生产约定：不引入新色相
    # 置信带 alpha 与 B 严格相同（亮 0.10 / 暗 0.15）。实测两套的"带 vs 纸面"
    # 分离度几乎相等（1.14 vs 1.15），所以 A 暗色看起来更"闷"是**色相**造成的
    # 观感差异，不是对比度差异——这条写进评估表，不用调 alpha 掩盖。
    'wash':       'rgba(138, 109, 46, 0.10)',   # 置信带

    # 兼容键（生产代码仍在读这两个名字）
    'bg':         '#F2F2F3',
    'ink_soft':   '#45484C',
    'cyan':       '#8E9398',   # 基线参考线：中性灰，靠明度与金线分离
}

A_DARK = {
    'paper':      '#121315',   # 月光下的石墨黑
    'paper_2':    '#1A1C1F',
    'surface':    '#202327',   # 暗色：靠前 = 更亮

    'ink':        '#ECEDEF',
    'ink_2':      '#B9BDC1',
    'muted':      '#90959A',

    'rule':       '#32363A',
    'grid':       '#292D31',

    'accent':      '#C7A55E',  # 暗底上金要提亮才够 3:1
    'accent_ink':  '#DCC08A',  # 小号文字用更亮的金（AA 4.5）
    'accent_soft': '#2B2617',  # hover 底色 = 暗金，不是亮金
    'accent_deep': '#E7D3A2',  # 极性反转：pressed 提亮

    'danger':     '#E7D3A2',
    'wash':       'rgba(199, 165, 94, 0.15)',

    'bg':         '#121315',
    'ink_soft':   '#B9BDC1',
    'cyan':       '#767D84',
}

# ================================================================
# B · 云白深海
# ================================================================
# 概念：云白 = 带一丝蓝相的白；深海 = 低饱和深蓝作为唯一的语义色。
# 中性轴统一带蓝相（B > R），整块画面像同一束冷光下的不同深度。
B_LIGHT = {
    'paper':      '#F1F4F6',
    'paper_2':    '#E5EAEE',
    'surface':    '#FFFFFF',

    'ink':        '#14181B',
    'ink_2':      '#3F474D',
    'muted':      '#606B72',

    'rule':       '#D8E0E6',
    'grid':       '#E7EDF1',

    'accent':      '#3A6A88',  # 深海蓝：低饱和
    'accent_ink':  '#2A5570',
    'accent_soft': '#DFEAF1',
    'accent_deep': '#1B374A',

    'danger':     '#1B374A',
    'wash':       'rgba(58, 106, 136, 0.10)',

    'bg':         '#F1F4F6',
    'ink_soft':   '#3F474D',
    'cyan':       '#8C969E',
}

B_DARK = {
    'paper':      '#0C1215',   # 深海：带蓝相的黑，不是纯黑
    'paper_2':    '#121A1F',
    'surface':    '#17222A',

    'ink':        '#E7EEF3',
    'ink_2':      '#B1BFC7',
    'muted':      '#8B9AA4',

    'rule':       '#233340',
    'grid':       '#1C2831',

    'accent':      '#6BA6C8',
    'accent_ink':  '#92C1DE',
    'accent_soft': '#162935',
    'accent_deep': '#B6DBEF',

    'danger':     '#B6DBEF',
    'wash':       'rgba(107, 166, 200, 0.15)',

    'bg':         '#0C1215',
    'ink_soft':   '#B1BFC7',
    'cyan':       '#61727D',
}

PALETTES = {
    ('a', 'light'): A_LIGHT,
    ('a', 'dark'): A_DARK,
    ('b', 'light'): B_LIGHT,
    ('b', 'dark'): B_DARK,
}

NAMES = {
    'a': 'A · 月光微金',
    'b': 'B · 云白深海',
}

# ================================================================
# 曲线语义的**非颜色**部分 —— A/B 必须逐键相同
# ================================================================
# 观察点的半透明灰：A/B 用同一 alpha 与同一明度，只换色偏。
# 这样"B 的点更显眼"这种伪优势不可能出现。
OBSERVATION = {
    'a': {'light': (150, 152, 155, 118),    # 纯中性石墨灰
          'dark': (168, 172, 177, 96)},
    'b': {'light': (150, 158, 165, 118),    # 同明度冷灰
          'dark': (168, 178, 186, 96)},
}

# 结构令牌：两套主题共用，原型脚本原样写入 theme.METRICS
STRUCTURE_METRICS = {
    'tabbar_h': 40,
    'topbar_h': 44,
    'mini_w': 360,
    'mini_h': 180,
    'radius': 8,
    'radius_sm': 4,
    'radius_mini': 10,
}

# 曲线几何（线宽 / 点径 / 方框尺寸）—— 颜色交给 COLORS 令牌解析
CURVE_GEOMETRY = {
    'observation_radius': 2.2,
    'model_width': 2.0,
    'forecast_width': 2.0,
    'correction_width': 1.2,
    'correction_size': 6.0,
    'baseline_width': 1.0,
}


def curve_style_for(key: str, mode: str) -> dict:
    """产出与 `theme.CURVE_STYLE` 同构的字典，颜色只写**令牌名**。

    `curve_widget` 绘制时做 `COLORS[style['color']]`，所以只要 COLORS 换掉，
    曲线四语义自动跟随；observation 例外（生产实现直接存 RGBA 元组）。
    """
    g = CURVE_GEOMETRY
    obs = OBSERVATION[key][mode]
    return {
        'observation': {'color': obs, 'radius': g['observation_radius']},
        'model':       {'color': 'accent', 'width': g['model_width'], 'dash': False},
        'forecast':    {'color': 'accent', 'width': g['forecast_width'], 'dash': True},
        'correction':  {'color': 'accent_ink', 'width': g['correction_width'],
                        'size': g['correction_size']},
        'band':        {'color': 'wash'},
        'baseline':    {'color': 'cyan', 'width': g['baseline_width'], 'dash': True},
    }


def palette_key(key: str, mode: str) -> str:
    return NAMES[key] + ' · ' + ('亮色' if mode == 'light' else '暗色')
