"""prototype2.py — UI Exploration 02 入口：两套灰度布局原型

运行方式（详见 README）：
    python prototype2.py --all                # 两套各起一个进程，出图 + 并列对照图
    python prototype2.py a --live             # 真窗口体验方案 A（可点记录/拖水位线/开纠正）
    python prototype2.py b --live             # 方案 B

为什么要分进程：纠正上滑框是**直接复用生产组件**的，它读 `theme.COLORS`；
而那套颜色在 import 期就被几个模块固化。灰度注入必须在导入之前完成，
所以每个方案各起一个进程，互不污染。生产文件一个字节都没动。
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

import gray_tokens as G                                       # noqa: E402

#: 把生产色板整体映射到灰阶——**只为给复用组件（纠正框）看**，
#: 两套方案自己的界面完全不读 theme.COLORS。
GRAY_COLORS = {
    'paper': G.PAPER, 'paper_2': '#EDEDED', 'surface': G.SURFACE,
    'ink': G.INK, 'ink_2': G.INK_2, 'muted': G.MUTED,
    'rule': G.RULE, 'grid': G.GRID,
    'accent': G.INK, 'accent_ink': G.INK,
    'accent_soft': '#EDEDED', 'accent_deep': '#000000',
    'danger': G.INK_2, 'wash': 'rgba(0, 0, 0, 0.06)',
    'bg': G.PAPER, 'ink_soft': G.INK_2, 'cyan': G.FAINT,
}

PLANS = {'a': ('A · 地平线', 'plan_a', 'PlanA'),
         'b': ('B · 水位', 'plan_b', 'PlanB')}


# ================================================================
def apply_gray_before_import():
    import theme
    theme.COLORS.clear()
    theme.COLORS.update(GRAY_COLORS)


def build_app():
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    import theme

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setFont(theme.app_font(10))
    app.setStyleSheet(theme.build_app_qss(GRAY_COLORS) + G.build_qss())
    return app


# ================================================================
def open_sheet(win, source, app):
    """纠正框：直接复用生产组件（结构不动），只是把颜色换成灰阶。

    入场是 windowOpacity 动画，截图前必须把不透明度推到 1，否则拍到全透明。
    """
    from windows.correction_sheet import CorrectionSheet

    ctx = source.correction_context()
    if not ctx.get('has_data'):
        return None
    sheet = CorrectionSheet(
        parent=win,
        timestamps=ctx['timestamps'], model_v=ctx['model_v'],
        model_a=ctx['model_a'], baseline_v=ctx['baseline_v'],
        current_state=ctx['current_state'])
    sheet.show()
    sheet.setWindowOpacity(1.0)
    app.processEvents()
    sheet.canvas.point_selected.emit(5)
    app.processEvents()
    return sheet


def capture(plan_key, outdir, live=False):
    apply_gray_before_import()
    app = build_app()

    from PyQt5.QtWidgets import QVBoxLayout, QWidget
    from data_source import DemoSource

    module = __import__(PLANS[plan_key][1], fromlist=[PLANS[plan_key][2]])
    plan_cls = getattr(module, PLANS[plan_key][2])

    source = DemoSource()
    view = plan_cls(source)

    win = QWidget()
    win.setWindowTitle('EmoWave · UI02 %s' % PLANS[plan_key][0])
    win.resize(400, 680)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(view)

    sheet_holder = {'sheet': None}

    def _open():
        sheet_holder['sheet'] = open_sheet(win, source, app)

    view.correction_requested.connect(_open)

    win.move(-32000, -32000)
    win.show()
    app.processEvents()

    shots = []

    def shot(name):
        app.processEvents()
        pm = win.grab()
        path = os.path.join(outdir, '%s-%s.png' % (plan_key, name))
        pm.save(path, 'PNG')
        shots.append({'name': name, 'px': [pm.width(), pm.height()]})
        return pm

    if live:
        win.move(140, 90)
        win.show()
        print('实时预览：%s（关闭窗口即退出）' % PLANS[plan_key][0], flush=True)
        return app.exec_()

    shot('idle')
    snap_idle = source.snapshot()      # manifest 记录的是首屏那一份数据

    # 最小尺寸先拍：此时还是完整的一天数据
    win.resize(360, 600)
    app.processEvents()
    shot('min-360x600')
    win.resize(400, 680)
    app.processEvents()

    # 纠正框也依赖这一天数据，必须在"开始新一波"之前
    sheet = open_sheet(win, source, app)
    if sheet is not None:
        pm = sheet.grab()
        pm.save(os.path.join(outdir, '%s-correction.png' % plan_key), 'PNG')
        shots.append({'name': 'correction', 'px': [pm.width(), pm.height()]})
        sheet.close()
        app.processEvents()

    # 记录态放最后：开始新一波会结束上一波并清空曲线（生产语义，不是 bug）
    source.start_session()
    view.sync()
    shot('recording')

    manifest = {
        'plan': plan_key, 'label': PLANS[plan_key][0],
        'dpr': app.devicePixelRatio(),
        'shots': shots,
        'data': {
            'n_obs': len(snap_idle['curve'].get('raw') or [])
            if snap_idle['curve'] else 0,
            'curve_ts': len(snap_idle['curve'].get('ts') or [])
            if snap_idle['curve'] else 0,
            'baseline_v': round(snap_idle['baseline_v'], 4),
            'state': snap_idle['state_name'],
            'count': snap_idle['count'],
        },
    }
    with open(os.path.join(outdir, '%s.json' % plan_key), 'w',
              encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    source.close()
    win.close()
    app.processEvents()
    return 0


# ================================================================
# 并列对照图
# ================================================================
def compose(outdir):
    from PyQt5.QtCore import QRect, Qt
    from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap

    app = build_app()                                    # noqa: F841
    rows = [('idle', '首屏 · 空闲'), ('recording', '记录中'),
            ('min-360x600', '最小尺寸 360×600')]
    gap, cap_h, label_h = 26, 30, 34
    cell_w, cell_h = 400, 680

    total_w = 2 * cell_w + 3 * gap + 2 * 14
    total_h = label_h + len(rows) * (cap_h + cell_h) + (len(rows) - 1) * 18 + gap
    canvas = QPixmap(total_w, total_h)
    canvas.fill(QColor('#232323'))

    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing, True)

    title = QFont('Segoe UI', 13, QFont.DemiBold)
    p.setFont(title)
    p.setPen(QColor('#EDEDED'))
    p.drawText(QRect(gap, 0, total_w - gap, label_h),
               Qt.AlignLeft | Qt.AlignVCenter,
               'EmoWave UI Exploration 02 · 两套结构方案（灰度）')

    col_x = {0: gap, 1: 2 * gap + cell_w}
    for col, key in enumerate(('a', 'b')):
        x = col_x[col] + 14
        p.setFont(QFont('Segoe UI', 10, QFont.DemiBold))
        p.setPen(QColor('#F0F0F0'))
        p.drawText(QRect(x, label_h + 10, cell_w, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, PLANS[key][0])

    y = label_h + label_h
    for row_key, row_label in rows:
        for col, key in enumerate(('a', 'b')):
            path = os.path.join(outdir, '%s-%s.png' % (key, row_key))
            if not os.path.exists(path):
                continue
            pm = QPixmap(path)
            x = col_x[col] + 14
            p.setFont(QFont('Segoe UI', 9))
            p.setPen(QColor('#9A9A9A'))
            p.drawText(QRect(x, y, cell_w, cap_h - 8),
                       Qt.AlignLeft | Qt.AlignVCenter, row_label)
            p.drawPixmap(x, y + cap_h - 8, pm)
        y += cap_h + cell_h + 18

    p.end()
    path = os.path.join(outdir, 'compare-side-by-side.png')
    canvas.save(path, 'PNG')
    print('并列对照图：%s' % path)
    return path


def run_all(outdir):
    os.makedirs(outdir, exist_ok=True)
    for key in ('a', 'b'):
        print('== 采集 %s ==' % PLANS[key][0])
        rc = subprocess.call([sys.executable, os.path.abspath(__file__),
                              '--capture', key, '--out', outdir], cwd=REPO)
        if rc:
            print('  [失败] 退出码 %d' % rc)
            return rc
    compose(outdir)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='EmoWave UI Exploration 02')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--capture', choices=('a', 'b'))
    ap.add_argument('--out', default=os.path.join(HERE, 'screenshots'))
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--compose-only', action='store_true')
    ap.add_argument('plan', nargs='?', choices=('a', 'b'))
    ns = ap.parse_args(argv)

    outdir = ns.out
    os.makedirs(outdir, exist_ok=True)

    if ns.compose_only:
        compose(outdir)
        return 0
    if ns.plan:
        return capture(ns.plan, outdir, live=True)
    if ns.capture:
        return capture(ns.capture, outdir, live=ns.live)
    if ns.all:
        return run_all(outdir)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
