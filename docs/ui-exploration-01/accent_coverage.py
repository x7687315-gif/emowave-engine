"""accent_coverage.py — 点缀色到底占了多少画面（可测，不靠观感）

用户约束里最容易滑走的一条是「点缀色不得主导页面」。这句话用肉眼看图是判不出来的——
金色和蓝色的"显眼程度"不同，人眼会系统性高估暖色。所以直接数像素：

    chroma = max(r,g,b) - min(r,g,b)
    彩色像素 = chroma 比**本页纸底的 chroma** 高出 12 以上

两套主题的**组件集合完全相同**（同一个 QSS、同一批控件），所以彩色占比的差异
只能来自色板本身：谁的饱和度更高、谁在更多元素上可见。这就是"谁更主导画面"的度量。

用法：
    python accent_coverage.py            # 扫 screenshots/ 下所有主窗图 + 生产基线
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

from PyQt5.QtGui import QImage                                # noqa: E402
from PyQt5.QtWidgets import QApplication                       # noqa: E402

#: 接近纯黑或纯白的像素不计入彩色（暗底的噪点、白面板的边缘高光都不算）
LUM_FLOOR, LUM_CEIL = 18, 244
#: 相对纸底的彩色增量：像素 chroma 必须比本页 paper 的 chroma 高出这么多才算"点缀色"。
#: 为什么必须相对：暗色模式的底本身带色偏（#0C1215 的 chroma=9），
#: 用绝对阈值会把整张背景数成彩色，量出 99% 这种荒谬结果。
CHROMA_MARGIN = 12


def chroma_of(color):
    from contrast_report import _rgb
    r, g, b = _rgb(color)[:3]
    return max(r, g, b) - min(r, g, b)


def coverage(path, bg_hex):
    """返回 (彩色像素占比, 平均 chroma 增量, 纸底自身 chroma)。"""
    img = QImage(path)
    if img.isNull():
        return None
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    stride = img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(stride * h)          # PyQt5 的 voidptr 不知道长度，必须显式给
    buf = bytes(ptr)

    base = chroma_of(bg_hex)
    limit = base + CHROMA_MARGIN
    chroma = 0
    delta_sum = 0.0
    for y in range(h):
        row = buf[y * stride:y * stride + w * 3]
        for x in range(w):
            o = x * 3
            r, g, b = row[o], row[o + 1], row[o + 2]
            mx = max(r, g, b)
            mn = min(r, g, b)
            if mx < LUM_FLOOR or mx > LUM_CEIL:
                continue
            if mx - mn > limit:
                chroma += 1
                delta_sum += (mx - mn) - base
    total = w * h
    return {
        'px': total,
        'chroma_pct': 100.0 * chroma / total,
        'mean_delta': (delta_sum / chroma) if chroma else 0.0,
        'paper_chroma': base,
    }


TARGETS = [
    # (标签, 图片, 纸底来源)
    ('生产 v5（暖纸陶土）',
     (os.path.join(REPO, 'docs', 'screenshots', 'v5-main-400x680.png'),
      'theme.COLORS')),
    ('A 月光微金 · 亮', (os.path.join(HERE, 'screenshots', 'a_light-main.png'),
                        'a_light')),
    ('B 云白深海 · 亮', (os.path.join(HERE, 'screenshots', 'b_light-main.png'),
                        'b_light')),
    ('A 月光微金 · 暗', (os.path.join(HERE, 'screenshots', 'a_dark-main.png'),
                        'a_dark')),
    ('B 云白深海 · 暗', (os.path.join(HERE, 'screenshots', 'b_dark-main.png'),
                        'b_dark')),
]


def paper_of(tag):
    """纸底色从 manifest 读，不在此重复硬编码——否则改了色板忘了改这里，量的是空气。"""
    if tag == 'theme.COLORS':
        import theme
        return theme.COLORS['paper']
    with open(os.path.join(HERE, 'screenshots', '%s.json' % tag),
              encoding='utf-8') as f:
        return json.load(f)['colors']['paper']


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])   # noqa: F841
    print('| 画面 | 纸底自身 chroma | 彩色像素占比 | 平均彩色强度 |')
    print('|:---|---:|---:|---:|')
    for label, (path, tag) in TARGETS:
        r = coverage(path, paper_of(tag))
        if r is None:
            print('| %s | 缺图 | — | — |' % label)
            continue
        print('| %s | %d | %.2f%% | %.1f |'
              % (label, r['paper_chroma'], r['chroma_pct'], r['mean_delta']))
    print('\n判读：')
    print('· 彩色占比越低 = 点缀色越克制。两套组件集合完全相同，差异只来自色板。')
    print('· 纸底自身 chroma 衡量"底色是不是彩色的"：旧方案的整张纸带 17 的色偏，'
          '这正是新方案要拿掉的暖纸感。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
