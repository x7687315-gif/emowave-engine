"""prototype3.py — UI Exploration 03 入口：定稿方向 B（蓝色 / 柔和曲线 / 三页打通）

    python prototype3.py --all          # 6 张实拍 + 一张系统对照图
    python prototype3.py --live         # 真窗口：可记录、拖水位线、切三页、开纠正

生产零改动。三页与纠正框复用同一套令牌；纠正框本身仍是生产组件原样。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

import tokens as T                                            # noqa: E402

#: 给复用的生产组件（纠正上滑框）看的色板映射
PROD_COLORS = {
    'paper': T.PAPER, 'paper_2': '#EDEDED', 'surface': T.SURFACE,
    'ink': T.INK, 'ink_2': T.INK_2, 'muted': T.MUTED,
    'rule': T.RULE, 'grid': T.GRID,
    'accent': T.BLUE, 'accent_ink': T.BLUE_INK,
    'accent_soft': T.BLUE_SOFT, 'accent_deep': T.BLUE_DEEP,
    'danger': T.BLUE_INK, 'wash': 'rgba(58, 106, 136, 0.10)',
    'bg': T.PAPER, 'ink_soft': T.INK_2, 'cyan': T.MUTED,
}


def apply_palette_before_import():
    import theme
    theme.COLORS.clear()
    theme.COLORS.update(PROD_COLORS)


def build_app():
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    import theme

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setFont(theme.app_font(10))
    app.setStyleSheet(theme.build_app_qss(PROD_COLORS) + T.build_qss())
    return app


def open_sheet(win, source, app):
    from windows.correction_sheet import CorrectionSheet
    ctx = source.correction_context()
    if not ctx.get('has_data'):
        return None
    sheet = CorrectionSheet(
        parent=win, timestamps=ctx['timestamps'], model_v=ctx['model_v'],
        model_a=ctx['model_a'], baseline_v=ctx['baseline_v'],
        current_state=ctx['current_state'])
    sheet.show()
    sheet.setWindowOpacity(1.0)
    app.processEvents()
    sheet.canvas.point_selected.emit(5)
    app.processEvents()
    return sheet


def capture(outdir, live=False):
    apply_palette_before_import()
    app = build_app()

    from PyQt5.QtCore import Qt as _Qt
    from PyQt5.QtGui import QColor, QPainter, QPixmap
    from PyQt5.QtWidgets import QVBoxLayout, QWidget
    from datasource import DemoSource
    from mini import MiniWindow
    from views import App

    source = DemoSource()
    view = App(source)

    win = QWidget()
    win.setWindowTitle('EmoWave · UI03')
    win.resize(400, 680)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(view)

    view.correction_requested.connect(
        lambda: open_sheet(win, source, app))
    win.move(-32000, -32000)
    win.show()
    app.processEvents()

    mini = MiniWindow(source)
    mini.move(-32000, -32000)
    mini.show()
    app.processEvents()

    if live:
        win.move(140, 80)
        win.show()
        mini.move(140 + 412, 80)
        mini.show()
        print('实时预览：主窗可记录/切页/开纠正；右侧是悬浮窗（收起 360×134）',
              flush=True)
        return app.exec_()

    shots = []

    def shot_mini(name):
        app.processEvents()
        pm = mini.grab()
        # 悬浮窗有边框、没有背景板：贴到中性底上才看得出边界
        out = QPixmap(pm.width() + 28, pm.height() + 28)
        out.fill(QColor('#6B7074'))
        q = QPainter(out)
        q.drawPixmap(14, 14, pm)
        q.end()
        out.save(os.path.join(outdir, '%s.png' % name), 'PNG')
        shots.append({'name': name, 'px': [out.width(), out.height()]})

    def shot(name):
        app.processEvents()
        pm = win.grab()
        pm.save(os.path.join(outdir, '%s.png' % name), 'PNG')
        shots.append({'name': name, 'px': [pm.width(), pm.height()]})
        return pm

    # 1 主屏（空闲）
    shot('main')
    snap = source.snapshot()

    # 2 模型页
    view.tabs.set_current(1)
    view.sync()
    shot('model')

    # 3 历史页
    view.tabs.set_current(2)
    view.sync()
    shot('history')

    # 4 纠正框（复用生产组件）
    view.tabs.set_current(0)
    view.sync()
    sheet = open_sheet(win, source, app)
    if sheet is not None:
        pm = sheet.grab()
        pm.save(os.path.join(outdir, 'correction.png'), 'PNG')
        shots.append({'name': 'correction', 'px': [pm.width(), pm.height()]})
        sheet.close()
        app.processEvents()

    # 4.5 悬浮窗（空闲态：展开 / 收起）
    shot_mini('mini-collapsed')
    mini.set_expanded(True)
    app.processEvents()
    shot_mini('mini-expanded')

    # 5 记录中（开始新一波会清掉上一波曲线，所以放最后）
    source.p.toggle_recording(True)
    for v, a in ((0.62, 0.55), (0.58, 0.60), (0.50, 0.64)):
        source.clock.advance(120)
        source.p.set_input(v, a)
        source.p.sample(v, a)
    view.sync()
    mini.sync()
    mini.set_expanded(False)
    app.processEvents()
    shot('recording')
    shot_mini('mini-recording')
    mini.hide()

    # 6 最小尺寸
    source.p.toggle_recording(False)
    win.resize(360, 600)
    view.sync()
    shot('min-360x600')
    win.resize(400, 680)

    manifest = {
        'dpr': app.devicePixelRatio(),
        'shots': shots,
        'data': {
            'n_obs': len(snap['curve'].get('raw') or []) if snap['curve'] else 0,
            'curve_ts': len(snap['curve'].get('ts') or []) if snap['curve'] else 0,
            'baseline_v': round(snap['baseline_v'], 4),
            'state': snap['state_name'],
            'count': snap['count'],
        },
    }
    with open(os.path.join(outdir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    source.close()
    win.close()
    app.processEvents()
    return 0


# ================================================================
def compose(outdir):
    from PyQt5.QtCore import QRect, Qt
    from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap

    app = build_app()                                    # noqa: F841
    grid = [
        (680, [('main', '主屏 · 当下'), ('model', '模型页 · 常态与学习'),
               ('history', '历史页 · 按天')]),
        (680, [('recording', '记录中'), ('correction', '纠正（生产组件复用）'),
               ('min-360x600', '最小尺寸 360×600')]),
        (216, [('mini-collapsed', '悬浮窗 · 收起 360×134'),
               ('mini-expanded', '悬浮窗 · 展开 360×180'),
               ('mini-recording', '悬浮窗 · 记录中')]),
    ]
    cell_w = 400
    gap, top, cap_h = 26, 64, 26
    width = 3 * cell_w + 4 * gap
    height = top + sum(cap_h + h + 24 for h, _ in grid)
    canvas = QPixmap(width, height)
    canvas.fill(QColor('#202426'))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setFont(QFont('Segoe UI', 13, QFont.DemiBold))
    p.setPen(QColor('#EDF0F2'))
    p.drawText(QRect(gap, 0, width - gap, top), Qt.AlignLeft | Qt.AlignVCenter,
               'EmoWave UI Exploration 03 · 定稿方向：水位（低饱和深海蓝）')

    y = top
    for cell_h, row in grid:
        for col, (name, caption) in enumerate(row):
            path = os.path.join(outdir, '%s.png' % name)
            if not os.path.exists(path):
                continue
            x = gap + col * (cell_w + gap)
            p.setFont(QFont('Segoe UI', 9))
            p.setPen(QColor('#9DA6AC'))
            p.drawText(QRect(x, y, cell_w, cap_h - 6),
                       Qt.AlignLeft | Qt.AlignVCenter, caption)
            pm = QPixmap(path)
            if name == 'correction':
                pm = pm.scaled(cell_w, cell_h, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
            if name.startswith('mini'):
                p.drawPixmap(x + (cell_w - pm.width()) // 2,
                             y + cap_h - 6 + (cell_h - pm.height()) // 3, pm)
            else:
                p.drawPixmap(x, y + cap_h - 6, pm)
        y += cap_h + cell_h + 24
    p.end()
    path = os.path.join(outdir, 'system-sheet.png')
    canvas.save(path, 'PNG')
    print('系统对照图：%s' % path)
    return path


def run_all(outdir):
    os.makedirs(outdir, exist_ok=True)
    rc = subprocess.call([sys.executable, os.path.abspath(__file__),
                          '--capture', '--out', outdir], cwd=REPO)
    if rc:
        return rc
    compose(outdir)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='EmoWave UI Exploration 03')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--capture', action='store_true')
    ap.add_argument('--compose-only', action='store_true')
    ap.add_argument('--out', default=os.path.join(HERE, 'screenshots'))
    ap.add_argument('--live', action='store_true')
    ns = ap.parse_args(argv)

    os.makedirs(ns.out, exist_ok=True)
    if ns.compose_only:
        compose(ns.out)
        return 0
    if ns.capture:
        return capture(ns.out, live=ns.live)
    if ns.live:
        return capture(ns.out, live=True)
    if ns.all:
        return run_all(ns.out)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
