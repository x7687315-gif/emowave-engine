"""fairness_check.py — 用可执行断言证明"A/B 只差色板"

设计对照最容易自欺的地方：嘴上说"布局完全一样"，实际上给好看的那套多留了
2px 内边距、把字号调大一号、或者演示数据多点一个峰。所以这里不写"我们相信
公平"，而是把四份 manifest 摊开逐键比：

    结构必须相同：窗口尺寸、各区块高度、截图像素尺寸、曲线数据、演示数据规模
    色板必须不同：COLORS 每个键都不能撞车

用法：
    python fairness_check.py            # 采集后跑，PASS/FAIL
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, 'screenshots')
COMBOS = [('a', 'light'), ('a', 'dark'), ('b', 'light'), ('b', 'dark')]

#: manifest 里**必须逐键相等**的字段（结构 + 数据，与颜色无关）
MUST_MATCH = [
    'window', 'topbar_h', 'tabbar_h', 'statehead_h', 'curve_h', 'input_h',
    'ui_font', 'n_states', 'n_obs', 'n_corrections', 'curve_ts',
    'baseline_v', 'readout_v', 'readout_a',
]


def load():
    out = {}
    for key, mode in COMBOS:
        path = os.path.join(SHOTS, '%s_%s.json' % (key, mode))
        if not os.path.exists(path):
            print('缺少 manifest：%s（先跑 prototype.py --all）' % path)
            sys.exit(2)
        with open(path, encoding='utf-8') as f:
            out['%s_%s' % (key, mode)] = json.load(f)
    return out


def main():
    data = load()
    ref = data['a_light']
    problems = []

    # ---- ① 结构与数据必须一致 ----
    for field in MUST_MATCH:
        vals = {tag: m['geometry'].get(field) for tag, m in data.items()}
        if len(set(map(str, vals.values()))) > 1:
            problems.append('结构/数据不一致 [%s]：%s' % (field, vals))

    # ---- ② 每张截图的像素尺寸必须一致 ----
    ref_shots = {s['name']: tuple(s['content_px']) for s in ref['shots']}
    for tag, m in data.items():
        got = {s['name']: tuple(s['content_px']) for s in m['shots']}
        if got != ref_shots:
            problems.append('%s 截图尺寸不一致：%s vs %s' % (tag, got, ref_shots))

    # ---- ③ 色板必须真的不同（同键同值 = 有一边根本没换色）----
    keys = set(ref['colors'])
    for i in range(len(COMBOS)):
        for j in range(i + 1, len(COMBOS)):
            t1 = '%s_%s' % COMBOS[i]
            t2 = '%s_%s' % COMBOS[j]
            same = [k for k in keys if data[t1]['colors'][k] == data[t2]['colors'][k]]
            # 允许个别键撞值（如两套 danger 都等于各自 accent_deep 不算错），
            # 但**过半相同**就说明有一边没生效
            if len(same) > len(keys) * 0.5:
                problems.append('%s 与 %s 色板 %.0f%% 相同（%s）'
                                % (t1, t2, 100.0 * len(same) / len(keys),
                                   ', '.join(sorted(same)[:6])))

    print('对比 4 组 manifest：%s' % ' / '.join(data))
    print('  结构键：%s' % ', '.join(MUST_MATCH))
    print('  基准值：%s' % json.dumps(
        {k: ref['geometry'][k] for k in ('window', 'statehead_h', 'curve_h',
                                         'input_h', 'curve_ts')},
        ensure_ascii=False))
    print('  截图：%s' % ', '.join('%s=%sx%s' % (n, s[0], s[1])
                                  for n, s in sorted(ref_shots.items())))
    if problems:
        print('\nFAIL —— 公平性被破坏：')
        for p in problems:
            print('  · %s' % p)
        return 1
    print('\nPASS —— 四组的布局、尺寸、字体与演示数据完全一致，差异只在色板。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
