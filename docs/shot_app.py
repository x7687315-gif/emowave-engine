"""docs/shot_app.py — 从**真实 PyQt5 应用**生成 README 截图。

与 docs/shot.py 的分工：
  · docs/shot.py     截 HTML 设计稿（ui-draft-v1.html），用于设计阶段对稿
  · docs/shot_app.py 截跑起来的 Qt 应用，用于 README 的"实拍"

用法（必须用 Python 3.9：该环境 PyQt5 5.15.2 带 windows 平台插件）：
    python docs/shot_app.py [输出目录]      # 默认 docs/screenshots

为什么要这个脚本：
  截图是"用一次就过期"的产物。把它固化成脚本，设计改完一条命令就能重拍，
  不会出现 README 里的图和代码对不上的情况。

实现要点：
  · 喂 48 个采样，让曲线有真实形态（不是空面板）
  · 纠正框是模态的（exec_ 会阻塞），所以用 show() + 单独 grab
  · 用 QTimer 串步骤，等动画（抽屉 220ms）走完再拍
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import Qt, QTimer
Qt.AA_EnableHighDpiScaling = True
Qt.AA_UseHighDpiPixmaps = True

from PyQt5.QtWidgets import QApplication

from theme import app_font, build_app_qss
from main_app import MainWindow
from windows.correction_sheet import CorrectionSheet

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots"
os.makedirs(OUT, exist_ok=True)

N_SAMPLES = 48

app = QApplication(sys.argv)
app.setFont(app_font(10))
app.setStyleSheet(build_app_qss())
win = MainWindow()
win.resize(1180, 780)
win.show()

console = win.console
holder = {}


def shot(widget, name):
    pm = widget.grab()
    p = os.path.join(OUT, name)
    ok = pm.save(p)
    print(("  saved   " if ok else "  FAILED  ") + p +
          f"  ({pm.width()}x{pm.height()})")
    return ok


def feed(n=N_SAMPLES):
    """拨滑块采样，让曲线有内容（模拟一次真实记录）。"""
    ip = console.input_panel
    for i in range(n):
        ip.v_row['slider'].setValue(int(35 + 30 * i / max(1, n - 1)))
        ip.a_row['slider'].setValue(int(55 - 15 * i / max(1, n - 1)))
        console._sample()
    print(f"  fed {n} samples -> {len(console.states)} states")


def open_sheet():
    sheet = CorrectionSheet(
        timestamps=console.observations_ts_for_curve(),
        model_v=[s.valence for _, s in console.states],
        model_a=[s.arousal for _, s in console.states],
        baseline_v=console.baseline_ctrl.current.valence,
        current_state=console.states[-1][1],
        parent=console,
    )
    holder['sheet'] = sheet
    sheet.show()                 # 不用 exec_()：模态会阻塞整个流程
    app.processEvents()


def run(i=0):
    if i >= len(STEPS):
        print("DONE")
        app.quit()
        return
    fn, delay = STEPS[i]
    try:
        fn()
    except Exception:
        traceback.print_exc()
        print(f"FAILED at step {i}")
        app.quit()
        return
    QTimer.singleShot(delay, lambda: run(i + 1))


STEPS = [
    (lambda: (feed(), shot(win, "01-main.png")), 500),
    (lambda: (console.toggle_drawer(), shot(win, "02-drawer.png")), 700),
    (lambda: (console.toggle_drawer(), None), 400),
    (open_sheet, 700),
    (lambda: shot(holder['sheet'], "03-correct-sheet.png"), 300),
]

QTimer.singleShot(300, lambda: run(0))
sys.exit(app.exec_())
