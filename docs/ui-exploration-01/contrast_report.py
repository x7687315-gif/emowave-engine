"""contrast_report.py — 把"这套色板能不能上线"变成可算的数字

不靠肉眼判断。对每个真实出现的**前景/背景组合**算 WCAG 2.1 对比度，
再算曲线四语义之间的**灰度分离度**（把画面转成灰阶后还分不分得开）。

用法：
    python docs/ui-exploration-01/contrast_report.py          # 四组 + 生产基线
    python docs/ui-exploration-01/contrast_report.py --strict # 有 FAIL 则退出码 1
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import palette_tokens as P                                        # noqa: E402


# ================================================================
# WCAG 2.1 相对亮度与对比度
# ================================================================
def _srgb_to_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(color):
    if isinstance(color, (tuple, list)):
        return tuple(int(round(float(c))) for c in color[:3])
    s = color.strip()
    if s.startswith('rgba(') or s.startswith('rgb('):
        nums = s[s.find('(') + 1:s.find(')')].split(',')
        return tuple(int(float(x)) for x in nums[:3])
    if s.startswith('#'):
        h = s[1:]
        if len(h) == 3:
            h = ''.join(ch * 2 for ch in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    raise ValueError('无法解析颜色：%r' % (color,))


def luminance(color):
    r, g, b = (_srgb_to_lin(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    l1, l2 = luminance(fg), luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def _alpha(color):
    """CSS `rgba()` 的第 4 位是 0–1，QColor 元组的第 4 位是 0–255。两种都要认。"""
    if isinstance(color, str) and color.strip().startswith('rgba('):
        nums = color[color.find('(') + 1:color.find(')')].split(',')
        return float(nums[3]) if len(nums) > 3 else 1.0
    if isinstance(color, (tuple, list)) and len(color) > 3:
        return float(color[3]) / 255.0
    return 1.0


def composite(fg, bg_hex):
    """把半透明前景按 src-over 合成到背景上，得到**真正落进像素的颜色**。

    为什么必须合成：观察点和置信带都是半透明。直接拿原始值算对比度会**高估**
    它们与纸面的分离度——屏幕上看到的其实是合成后的淡色。
    """
    r, g, b = _rgb(fg)[:3]
    a = _alpha(fg)
    br, bg_, bb = _rgb(bg_hex)
    return '#%02X%02X%02X' % (round(r * a + br * (1 - a)),
                              round(g * a + bg_ * (1 - a)),
                              round(b * a + bb * (1 - a)))


# ================================================================
# 需要验收的前景/背景组合（取自真实 QSS 与绘制代码）
# ================================================================
# (检查名, 前景 token, 背景 token, 阈值, 用途)
CHECKS = [
    ('主文字/纸底',     'ink',    'paper',   4.5, '页头标题、区块标题'),
    ('主文字/曲线纸面', 'ink',    'surface', 4.5, '#CurvePanel 上的轴标签'),
    ('次级文字/纸底',   'ink_2',  'paper',   4.5, '滑块标签、输入台'),
    ('次级文字/面板',   'ink_2',  'surface', 4.5, '读数、状态说明'),
    ('次级文字/次级面', 'ink_2',  'paper_2', 4.5, '悬浮窗底、抽屉'),
    ('极小标签/纸底',   'muted',  'paper',   4.5, '9.5px 级别标签（最严）'),
    ('极小标签/面板',   'muted',  'surface', 4.5, '读数下的解释文字'),
    ('极小标签/次级面', 'muted',  'paper_2', 4.5, '纠正框的字段名'),
    ('选中 Tab',        'accent_ink', 'paper', 4.5, '底部指示条旁的文字'),
    ('列表选中',        'accent_ink', 'accent_soft', 4.5, '历史列表选中项'),
    ('flat 按钮悬停',   'accent', 'accent_soft', 4.5, '页头图标 hover（生产用 accent）'),
    ('主按钮',          'surface', 'accent', 4.5, '「开始记录」'),
    ('主按钮悬停',      'surface', 'accent_ink', 4.5, 'hover'),
    ('主按钮按下',      'surface', 'accent_deep', 4.5, 'pressed'),
    ('危险按钮文字',    'danger', 'paper', 4.5, '「重置全部」'),
    ('模型线/纸面',     'accent', 'surface', 3.0, '曲线：模型估计（图形）'),
    ('修正框/纸面',     'accent_ink', 'surface', 3.0, '曲线：空心方框（图形）'),
    ('基线/纸面',       'cyan', 'surface', 3.0, '曲线：基线参考（仅参考）'),
]

# 曲线四语义的灰度分离：转成灰阶后还分得开，才算"靠形状不靠颜色"
# 阈值不是"越大越好"：线要够深才看得见（1.4），但置信带本来就该是**若隐若现**的
# 柔和底，做到 1.1 就够——再高会盖过网格，反而降低整张图的可读性。
SEPARATIONS = [
    ('模型线 vs 观察点', 'accent', 'observation', 1.4),
    ('模型线 vs 基线',   'accent', 'cyan', 1.4),
    ('基线 vs 观察点',   'cyan', 'observation', 1.4),
    ('模型线 vs 网格',   'accent', 'grid', 2.0),
    ('置信带 vs 纸面',   'wash', 'surface', 1.1),
]


def run_one(colors, mode, obs):
    """返回 (rows, sep_rows, fails, 合成后观察点色)。

    rows = (检查名, 对比度, 阈值, PASS/FAIL, 用途)；obs = 该主题观察点 RGBA，
    曲线语义的灰度分离必须按**合成到纸面之后**的实际像素算。
    """
    rows, fails = [], 0
    for name, fg_k, bg_k, need, use in CHECKS:
        ratio = contrast(colors[fg_k], colors[bg_k])
        ok = ratio >= need
        if not ok:
            fails += 1
        rows.append((name, ratio, need, 'PASS' if ok else 'FAIL', use))

    obs_px = composite(obs, colors['surface'])

    def resolve(spec):
        if spec == 'observation':
            return obs_px
        if spec == 'wash':
            return composite(colors['wash'], colors['surface'])
        return colors[spec]

    sep_rows = []
    for label, a_k, b_k, need in SEPARATIONS:
        sep_rows.append((label, contrast(resolve(a_k), resolve(b_k)), need))
    return rows, sep_rows, fails, obs_px


def main(argv):
    strict = '--strict' in argv
    import theme
    prod_obs = theme.CURVE_STYLE['observation']['color']
    sections = []

    # 生产色板当基线一起跑：新方案必须证明它比现状更好，不是只是"不一样"
    combos = [('prod', 'light', dict(theme.COLORS),
               '生产 · 暖纸陶土（基线）', prod_obs)]
    for key in ('a', 'b'):
        for mode in ('light', 'dark'):
            combos.append((key, mode, P.PALETTES[(key, mode)],
                           P.palette_key(key, mode), P.OBSERVATION[key][mode]))

    total_fail = 0
    for pal_key, mode, colors, title, obs in combos:
        rows, sep_rows, fails, obs_px = run_one(colors, mode, obs)
        sep_fails = sum(1 for _l, r, n in sep_rows if r < n)
        total_fail += fails + sep_fails

        lines = ['### %s' % title, '',
                 '| 检查 | 对比度 | 阈值 | 结果 | 出现位置 |',
                 '|:---|---:|---:|:---:|:---|']
        for name, ratio, need, verdict, use in rows:
            lines.append('| %s | %.2f:1 | %.1f | %s | %s |'
                         % (name, ratio, need, verdict, use))
        lines.append('')
        lines.append('**灰度分离度**（观察点已按 alpha 合成到纸面 = `%s`；'
                     '转灰阶后仍 ≥1.4:1 才算不依赖颜色）：' % obs_px)
        lines.append('')
        lines.append('| 语义对 | 灰度对比 | 阈值 | 结果 |')
        lines.append('|:---|---:|---:|:---:|')
        for label, ratio, need in sep_rows:
            lines.append('| %s | %.2f:1 | %.1f | %s |'
                         % (label, ratio, need,
                            'PASS' if ratio >= need else '偏弱'))
        lines.append('')
        lines.append('小计：文字/图形对比度 **%d** 项 FAIL / %d 项；'
                     '灰度分离 **%d** 项偏弱 / %d 项'
                     % (fails, len(rows), sep_fails, len(sep_rows)))
        lines.append('')
        sections.append('\n'.join(lines))

    out = '\n'.join(sections)
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'contrast-report.md')
    with open(dst, 'w', encoding='utf-8') as f:
        f.write('# 对比度与灰度分离实测\n\n'
                '> 由 `contrast_report.py` 生成，勿手改。改色板后重跑。\n\n'
                + out)
    print(out)
    print('[written] %s' % dst)
    if strict and total_fail:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
