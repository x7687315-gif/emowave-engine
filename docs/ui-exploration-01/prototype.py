"""prototype.py — UI Exploration 01 原型入口（A/B 双主题，零生产改动）

=== 它是怎么做到"不改生产代码"的 ===
生产 UI 的颜色全部读 `theme.COLORS` / `theme.CURVE_STYLE`。本脚本在
**导入任何界面模块之前**把那两张字典整体替换成候选色板，然后照常
`import main_app` 并构造真正的 `MainWindow` / `MiniWindow` / `CorrectionSheet`。
于是截图里看到的是**真实组件、真实排版、真实曲线渲染**，只有颜色是候选值。

代价（诚实记录）：`theme.py` 本身、`main_app.py`、`windows/*` 一个字节都没动，
但 A 与 B **不能共存于同一进程**——因为 4 个地方在 import 期就把颜色固化了
（详见 00 审计报告 §5）。所以每个主题各起一个子进程，互不影响。

=== 用法 ===
    python prototype.py --all                 # 4 个子进程：A/B × 亮/暗，出图 + manifest + compare.html
    python prototype.py --capture a light     # 只跑一组（内部用）
    python prototype.py a light --live        # 真窗口开在桌面上，可悬停/拖点，亲自试手感
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

#: 悬浮窗截图的中性背景：两套主题用同一块，避免"背景帮谁说话"
BACKDROP = '#5A5D61'

# ================================================================
# 演示数据 —— A/B 共用同一份，逐字节相同
# ================================================================
# 一个真实的工作日节奏：上午平稳上扬 → 午后会议压力（唤醒高、效价掉）
# → 傍晚回落 → 收工前慢慢回升。共 11 次自评，跨约 8 小时。
# 数值只描述"这个人今天怎么过的"，与配色无关，因此严禁两边不一样。
DEMO = [
    (45 * 60, 0.52, 0.40),
    (35 * 60, 0.58, 0.45),
    (60 * 60, 0.62, 0.55),
    (25 * 60, 0.44, 0.62),
    (40 * 60, 0.30, 0.70),
    (55 * 60, 0.24, 0.66),
    (30 * 60, 0.34, 0.52),
    (65 * 60, 0.46, 0.44),
    (50 * 60, 0.60, 0.48),
    (40 * 60, 0.66, 0.52),
    (35 * 60, 0.70, 0.58),
]

#: 纠正两个点（拖点纠正 = Coactive 改进信号，不是真值）
CORRECTIONS = [(3, 0.55, 0.50), (6, 0.42, 0.45)]

#: 悬浮窗截图要选一个"有意义"的点做选中态
SELECT_POINT = 5


class FakeClock:
    """可控时钟：截图必须在**确定的时间**上拍，否则 A/B 拍到的不是同一条曲线。"""

    def __init__(self, start):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += float(seconds)
        return self.t


def morning_base():
    """以"今天 09:00"为起点：同一天内 A/B 拍到的时间戳完全一致，
    而截图上的时间又像真实生活（不是 1970 或 2023-11-14）。"""
    return datetime.now().replace(hour=9, minute=0, second=0,
                                  microsecond=0).timestamp()


# ================================================================
# 调色注入：必须在 import 界面模块**之前**
# ================================================================
def apply_palette(key: str, mode: str):
    import palette_tokens as P
    import theme

    pal = P.PALETTES[(key, mode)]
    curve = P.curve_style_for(key, mode)

    # 结构必须一致——这条断言就是"公平比较"的可执行形式
    assert theme.METRICS == P.STRUCTURE_METRICS, (
        '结构令牌不一致，A/B 比较失效：%s vs %s' % (theme.METRICS, P.STRUCTURE_METRICS))

    theme.COLORS.clear()
    theme.COLORS.update(pal)
    theme.CURVE_STYLE.clear()
    theme.CURVE_STYLE.update(curve)
    return pal, curve


# ================================================================
# 构建 + 喂数据
# ================================================================
def build_app():
    """QApplication：与 main_app.main() 完全相同的三步注入（字体 / QSS / HighDPI）。"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    import theme

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setFont(theme.app_font(10))
    app.setStyleSheet(theme.build_app_qss())
    return app


def seed(win, clock):
    """把 DEMO 变成真实观察：走 presenter 的正门（sample），不碰视图内部。"""
    p = win.console.presenter
    p.toggle_recording(True)
    for dt, v, a in DEMO:
        clock.advance(dt)
        p.set_input(v, a)
        p.sample(v, a)
    ts_list = [t for t, _ in p.states]
    applied = [(ts_list[i], v, a) for i, v, a in CORRECTIONS if i < len(ts_list)]
    p.ingest_corrections(applied)
    return p


def geometry(win, p):
    """把布局与数据快照写进 manifest——A/B 是否真的公平，靠这份数据证明，不靠声明。

    注意 `console.readout is console.status`（v5 Phase 2 把状态头与读数合并成
    一个 StateHead），所以这里只记一份高度，不要假装它们是两块。
    """
    from PyQt5.QtWidgets import QApplication
    c = win.console
    payload = p.curve_payload()
    appfont = QApplication.instance().font()
    return {
        'window': [win.width(), win.height()],
        'topbar_h': _h(win, 'TopBar'),
        'tabbar_h': _h(win, 'TopTabs'),
        'statehead_h': c.status.height(),
        'curve_h': c.curve.height(),
        'input_h': c.input_panel.height(),
        'ui_font': '%s %spt w=%s' % (appfont.family(), appfont.pointSizeF(),
                                     appfont.weight()),
        'n_states': len(p.states),
        'n_obs': len(p.observations),
        'n_corrections': len(p.corrections),
        'curve_ts': len((payload or {}).get('ts', [])),
        'baseline_v': round(float(p.baseline_ctrl.current.valence), 4),
        'readout_v': round(float(p.latest_state.valence), 4) if p.latest_state else None,
        'readout_a': round(float(p.latest_state.arousal), 4) if p.latest_state else None,
    }


def _h(win, object_name):
    from PyQt5.QtWidgets import QWidget
    w = win.findChild(QWidget, object_name)
    return w.height() if w is not None else -1


# ================================================================
# 抓图
# ================================================================
def grab(widget, path, backdrop=None, pad=0):
    pm = widget.grab()
    if backdrop is None:
        out = pm
        ox = oy = 0
    else:
        from PyQt5.QtGui import QColor, QPainter, QPixmap
        out = QPixmap(pm.width() + pad * 2, pm.height() + pad * 2)
        out.fill(QColor(backdrop))
        p = QPainter(out)
        p.drawPixmap(pad, pad, pm)
        p.end()
        ox = oy = pad
    out.save(path, 'PNG')
    return {'px': [out.width(), out.height()],
            'content_px': [pm.width(), pm.height()]}


def open_correction_sheet(win, p, app):
    """纠正态：真实上滑框。入场是 windowOpacity 动画，截图前必须把不透明度推到 1，
    否则拍到的是全透明（这是**采集手法**，不是设计改动）。"""
    from windows.correction_sheet import CorrectionSheet

    ctx = p.correction_context()
    if not ctx['has_data']:
        return None
    kwargs = {k: ctx[k] for k in
              ('timestamps', 'model_v', 'model_a', 'baseline_v', 'current_state')}
    sheet = CorrectionSheet(parent=win, **kwargs)
    sheet.show()
    sheet.setWindowOpacity(1.0)
    app.processEvents()
    sheet.canvas.point_selected.emit(min(SELECT_POINT, len(ctx['timestamps']) - 1))
    app.processEvents()
    return sheet


# ================================================================
# 单主题采集
# ================================================================
def capture(key, mode, outdir, live=False):
    pal, _curve = apply_palette(key, mode)
    app = build_app()

    from PyQt5.QtWidgets import QApplication
    import main_app
    from db import DatabaseManager

    tag = '%s_%s' % (key, mode)
    shots = []

    tmp = tempfile.mkdtemp(prefix='emowave-proto-')
    try:
        db = DatabaseManager(os.path.join(tmp, 'demo.db'))
        clock = FakeClock(morning_base())
        win = main_app.MainWindow(db, clock=clock)
        win.move(-32000, -32000)          # 不在用户桌面上闪
        win.resize(400, 680)
        win.show()
        app.processEvents()

        p = seed(win, clock)

        def shot(widget, name, backdrop=None, pad=0):
            app.processEvents()
            info = grab(widget, os.path.join(outdir, '%s-%s.png' % (tag, name)),
                        backdrop, pad)
            shots.append(dict(name=name, **info))
            return info

        # ---- ① 记录反馈态（按钮=■停止记录、记一笔可用、读数在动）----
        shot(win, 'recording')

        # ---- ② 纠正态（上滑框 + 拖点选中）----
        sheet = open_correction_sheet(win, p, app)
        if sheet is not None:
            shot(sheet, 'correction')
            sheet.close()

        # ---- ③ 正常态（停止记录 → 衰减/预测尾 + 曲线纠正标记）----
        # 5 分钟是刻意选的：`MAX_DECAY_SEC = 600`，超过 10 分钟外推自然结束，
        # 而 `_end_wave()` 会清空 observations → 曲线按设计消失（v4 D1 语义）。
        # 拍"正常态"必须留在衰减窗口内，否则截到的是一张空图。
        p.toggle_recording(False)
        clock.advance(5 * 60)
        p.decay_step()
        app.processEvents()
        shot(win, 'main')

        # ---- ④ 悬浮窗：展开 360×180 / 收起 360×134 ----
        mini = win.mini
        mini.move(-32000, -32000)
        mini.set_expanded(True)
        mini.show()
        app.processEvents()
        shot(mini, 'mini-expanded', backdrop=BACKDROP, pad=18)
        mini.set_expanded(False)
        app.processEvents()
        shot(mini, 'mini-collapsed', backdrop=BACKDROP, pad=18)
        mini.hide()

        # ---- ⑤ 最小尺寸 360×600 布局检查 ----
        win.resize(360, 600)
        app.processEvents()
        shot(win, 'min-360x600')

        win.resize(400, 680)
        app.processEvents()
        man = {'key': key, 'mode': mode, 'label': _label(key, mode),
               'dpr': QApplication.instance().devicePixelRatio(),
               'colors': pal,
               'geometry': geometry(win, p),
               'shots': shots}
        with open(os.path.join(outdir, '%s.json' % tag), 'w', encoding='utf-8') as f:
            json.dump(man, f, ensure_ascii=False, indent=2)

        if live:
            # 真人试用：窗口搬回桌面，事件循环交给用户
            win.move(120, 80)
            win.show()
            print('实时预览：%s（关闭本进程窗口即退出）' % _label(key, mode),
                  flush=True)
            return app.exec_()

        win.shutdown()
        app.processEvents()
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _label(key, mode):
    import palette_tokens as P
    return P.palette_key(key, mode)


# ================================================================
# 对比页（静态 HTML，双击即看，不需要服务器）
# ================================================================
def write_compare_html(outdir, combos):
    import palette_tokens as P
    rows = []
    groups = [
        ('main', '主窗口 · 正常态（400×680）'),
        ('recording', '记录操作反馈（■ 停止记录 / 记一笔可用）'),
        ('correction', '曲线纠正态（上滑框 + 拖点）'),
        ('mini-expanded', '悬浮窗 · 展开（360×180）'),
        ('mini-collapsed', '悬浮窗 · 收起（360×134）'),
        ('min-360x600', '最小尺寸检查（360×600）'),
    ]
    for gkey, glabel in groups:
        cells = []
        for key, mode in combos:
            fn = '%s_%s-%s.png' % (key, mode, gkey)
            if not os.path.exists(os.path.join(outdir, fn)):
                continue
            cells.append((P.palette_key(key, mode), fn))
        if not cells:
            continue
        half = (len(cells) + 1) // 2
        left, right = cells[:half], cells[half:]
        body = []
        for i in range(half):
            pair = []
            for cell in (left[i] if i < len(left) else None,
                         right[i] if i < len(right) else None):
                if cell is None:
                    pair.append('<td></td>')
                else:
                    label, fn = cell
                    pair.append('<td><figure><img src="%s" alt="%s · %s">'
                                '<figcaption>%s</figcaption></figure></td>'
                                % (fn, glabel, label, label))
            body.append('<tr>%s</tr>' % ''.join(pair))
        rows.append('<h2>%s</h2><table class="cmp">%s</table>' % (glabel, ''.join(body)))

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>EmoWave · UI Exploration 01 · A/B 对照</title>
<style>
:root { color-scheme: light dark; }
body { margin: 0; padding: 32px 28px 56px; background: #232629; color: #E8EAED;
       font: 14px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif; }
h1 { font-size: 20px; font-weight: 600; letter-spacing: .04em; margin: 0 0 6px; }
.sub { color: #9AA0A6; font-size: 12.5px; margin-bottom: 30px; }
h2 { font-size: 13px; font-weight: 600; letter-spacing: .12em; color: #9AA0A6;
     margin: 34px 0 12px; padding-bottom: 8px; border-bottom: 1px solid #33373B; }
table.cmp { border-collapse: separate; border-spacing: 18px 22px; }
td { vertical-align: top; }
figure { margin: 0; }
img { display: block; width: 400px; image-rendering: auto;
      border: 1px solid #3A3E42; border-radius: 6px; background: #fff; }
figcaption { font-size: 12px; color: #BDC1C6; margin-top: 8px; letter-spacing: .04em; }
</style></head><body>
<h1>UI Exploration 01 · 月光微金 × 云白深海</h1>
<div class="sub">同一套布局 / 字体层级 / 组件 / 演示数据 / 曲线形状，只有语义色板不同。
<b>同一行 = 同一模式，左 A（月光微金）右 B（云白深海）</b>；第一行亮色，第二行暗色。
图片为真实 PyQt5 组件抓取，非合成稿。</div>
%s
</body></html>
""" % '\n'.join(rows)
    path = os.path.join(outdir, 'compare.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


# ================================================================
# 入口
# ================================================================
def run_all(outdir):
    combos = [('a', 'light'), ('a', 'dark'), ('b', 'light'), ('b', 'dark')]
    for key, mode in combos:
        print('== 采集 %s ==' % _label(key, mode))
        rc = subprocess.call([sys.executable, os.path.abspath(__file__),
                              '--capture', key, mode, '--out', outdir],
                             cwd=REPO)
        if rc:
            print('  [失败] 退出码 %d' % rc)
            return rc
    path = write_compare_html(outdir, combos)
    print('\n对照页：%s' % path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='EmoWave UI Exploration 01 原型')
    ap.add_argument('--all', action='store_true', help='采集四组并生成对照页')
    ap.add_argument('--capture', nargs=2, metavar=('KEY', 'MODE'))
    ap.add_argument('--out', default=os.path.join(HERE, 'screenshots'))
    ap.add_argument('--live', action='store_true', help='开真窗口手动体验')
    ap.add_argument('pair', nargs='*', help='简写：A|B [light|dark]，实时预览')
    ns = ap.parse_args(argv)

    os.makedirs(ns.out, exist_ok=True)

    if ns.pair:
        key = ns.pair[0].lower()
        mode = ns.pair[1].lower() if len(ns.pair) > 1 else 'light'
        return capture(key, mode, ns.out, live=True)
    if ns.capture:
        return capture(ns.capture[0].lower(), ns.capture[1].lower(), ns.out)
    if ns.all:
        return run_all(ns.out)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
