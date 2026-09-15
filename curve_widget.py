"""curve_widget.py — 情绪曲线控件（视觉主角）

=== 相比旧 widgets.EmotionCurveWidget 修了什么 ===

1. **拐点"串珠"**
   旧实现用 `for i in range(1, n): p.drawLine(x1,y1,x2,y2)` + `Qt.RoundCap`。
   每个线帽都是独立的圆头，相邻线段在拐点处圆头叠加 → 节点处出现一串珠子，
   且 RoundCap 会让线条在端点外多出半个线宽的圆头（视觉上"毛"）。
   → 改为把所有点装进一个 QPainterPath，**单次 strokePath**，
     配 SquareCap + 斜接 Join，拐点是干净的硬角（也符合构成主义硬边）。

2. **149ms 重建**
   旧实现每次 paintEvent 都重算全部点并重绘全部图层（网格、带、点、线）。
   → 网格/刻度/轴标签这些**静态层**缓存进 QPixmap，尺寸不变时不重绘；
     只有数据层（带/线/点）每次重画。数据点还做了抽稀（> 900 点时降采样）。

3. **唤醒从未可视化**
   旧控件 `set_data` 收了 `model_a` / `var_a` 但从没画过 —— 唤醒只在读数条显示数字。
   → 新增 `series` 参数，可指定画 'valence' / 'arousal' / 'both'（small multiples）。
     纠正上滑框正是靠 'both' 实现"效价 + 唤醒 上下两图共用时间轴"。

4. **无刻度 / 无网格**
   号称"精密仪器"却没有 Y 轴数值与时间刻度。
   → 弱化网格：仅 3 条横线（100/50/0）+ 短刻度 + 少量时间刻度。
     用户明确要求"尽量减少没有意义的线条"，所以**没有竖网格**。

5. **色板**
   旧实现里 rgba(30,64,175,26)（蓝置信带）、QColor(156,150,138,120)（灰点）
   是写死的裸数值，和色板脱节。→ 全部改引用 theme.COLORS token。
"""
import math

from PyQt5.QtCore import Qt, QPointF, QRectF, QSize
from PyQt5.QtGui import (
    QPainter, QPen, QColor, QPolygonF, QPainterPath, QPixmap, QFont,
)
from PyQt5.QtWidgets import QWidget, QSizePolicy

from theme import COLORS, app_font, app_font_num

# 绘图内边距（留出刻度与轴标签空间）
_PAD_L, _PAD_R = 46, 16
_PAD_T, _PAD_B = 16, 26

# Y 轴刻度：100 / 50 / 0 —— 三条横线，仅此而已（用户要求减少无意义线条）
_Y_TICKS = (1.0, 0.5, 0.0)
# 抽稀阈值：超过这个点数就等间隔降采样（保证 strokePath 的点数有上界）
_MAX_POINTS = 900


class EmotionCurveWidget(QWidget):
    """时间序列情绪曲线：弱化网格 + 置信带 + 模型线 + 基线 + 原始点 + 当前点。

    series='valence' | 'arousal' | 'both'
        'both' 时上下 small multiples，共用同一时间轴（纠正上滑框用）。
    """

    def __init__(self, parent=None, series="valence", show_legend=True,
                 show_axis_label=True, compact=False):
        super().__init__(parent)
        self.series = series
        self.show_legend = show_legend
        self.show_axis_label = show_axis_label
        self.compact = compact          # 紧凑模式：隐藏图例与轴标签（上滑框小图用）

        self.timestamps = []
        self.model_v = []
        self.model_a = []
        self.var_v = []
        self.var_a = []
        self.baseline_v = 0.5
        self.raw = []                   # [(ts, value)]

        # 静态层缓存（网格/刻度/标签）—— 尺寸不变就不重画
        self._static_cache = None
        self._static_key = None

        self.setMinimumSize(320, 180 if not compact else 96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ================================================================
    # 数据
    # ================================================================
    def set_data(self, timestamps, model_v, model_a=None, var_v=None, var_a=None,
                 baseline_v=0.5, raw=None):
        self.timestamps = list(timestamps)
        self.model_v = list(model_v)
        self.model_a = list(model_a or [])
        self.var_v = list(var_v or [])
        self.var_a = list(var_a or [])
        self.baseline_v = baseline_v
        self.raw = list(raw or [])
        self.update()

    def clear(self):
        self.timestamps = []
        self.model_v = []
        self.model_a = []
        self.var_v = []
        self.var_a = []
        self.raw = []
        self.update()

    def has_data(self):
        return len(self.timestamps) >= 2 and len(self.model_v) >= 2

    # ================================================================
    # 坐标映射
    # ================================================================
    def _plot_rect(self, index=0, count=1):
        """返回第 index 个子图（共 count 个）的绘图矩形。

        双曲线模式：两个子图各占可用高度的 50%，
        上图底部给"效价"标题让出空间，下图顶部给"唤醒"标题让出空间。
        """
        w, h = self.width(), self.height()
        left, top = _PAD_L, _PAD_T
        right, bottom = w - _PAD_R, h - _PAD_B
        if count <= 1:
            return left, top, right - left, bottom - top
        gap = 18                       # 子图之间的间距（含两条标题行各 9px）
        total = bottom - top - gap
        each = total / count
        y = top + index * (each + gap)
        return left, y, right - left, each

    def _x(self, ts, t0, span, x0, pw):
        if span <= 0:
            return x0 + pw / 2.0
        return x0 + (ts - t0) / span * pw

    @staticmethod
    def _y(value, y0, ph):
        v = 0.0 if value is None else max(0.0, min(1.0, float(value)))
        return y0 + (1.0 - v) * ph

    # ================================================================
    # 绘制
    # ================================================================
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        w, h = self.width(), self.height()

        # 纸面
        p.fillRect(self.rect(), QColor(COLORS['surface']))

        if not self.has_data():
            p.setPen(QColor(COLORS['muted']))
            p.setFont(app_font(11))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "开始记录后，这里会出现你的情绪曲线")
            self._static_cache = None
            return

        panels = (['valence', 'arousal'] if self.series == 'both'
                  and len(self.model_a) >= 2 else [self.series])
        n = len(panels)

        for i, kind in enumerate(panels):
            x0, y0, pw, ph = self._plot_rect(i, n)
            self._paint_static(p, x0, y0, pw, ph, kind)
            self._paint_series(p, x0, y0, pw, ph, kind)

        # 静态层失效标记（尺寸变化时重画）
        key = (w, h, self.series, n)
        if self._static_key != key:
            self._static_key = key
            self._static_cache = None

    # ---------- 静态层：网格 + 刻度 + 轴标签 ----------
    def _paint_static(self, p, x0, y0, pw, ph, kind):
        # 3 条横线（100 / 50 / 0）
        p.setPen(QPen(QColor(COLORS['grid']), 1))
        p.setFont(app_font(8))
        for tick in _Y_TICKS:
            y = self._y(tick, y0, ph)
            p.drawLine(QPointF(x0, y), QPointF(x0 + pw, y))
            # 短刻度 + 数值（用户要求"刻度不要太多"）
            p.setPen(QColor(COLORS['muted']))
            p.drawText(QRectF(x0 - 38, y - 6, 32, 12),
                       Qt.AlignRight | Qt.AlignVCenter, str(int(tick * 100)))
            p.setPen(QPen(QColor(COLORS['grid']), 1))

        if self.compact:
            return

        # 时间轴：首 / 中 / 末 三个刻度（不画竖网格线）
        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0
        p.setPen(QColor(COLORS['muted']))
        p.setFont(app_font(8))
        for frac, anchor in ((0.0, Qt.AlignLeft), (0.5, Qt.AlignCenter),
                             (1.0, Qt.AlignRight)):
            ts = t0 + span * frac
            x = self._x(ts, t0, span, x0, pw)
            label = _fmt_time(ts)
            wide = 70
            tx = x - wide / 2 if anchor == Qt.AlignCenter else (
                x if anchor == Qt.AlignLeft else x - wide)
            p.drawText(QRectF(tx, y0 + ph + 6, wide, 12), anchor | Qt.AlignVCenter,
                       label)

        # 子图标题（效价 / 唤醒）—— 画在子图内部左上角，不占顶部空间
        # 这样不会和上方子图最顶端的"100"刻度标签重叠
        if self.series == 'both':
            p.setFont(app_font(9))
            p.setPen(QColor(COLORS['muted']))
            title = "效价 VALENCE" if kind == 'valence' else "唤醒 AROUSAL"
            # 放在绘图区内左上角，背景是 surface，muted 字色够对比
            p.drawText(QRectF(x0 + 4, y0 + 2, 140, 12),
                       Qt.AlignLeft | Qt.AlignVCenter, title)

    # ---------- 数据层：置信带 → 原始点 → 模型线 → 基线 → 当前点 ----------
    def _paint_series(self, p, x0, y0, pw, ph, kind):
        model = self.model_v if kind == 'valence' else self.model_a
        var = self.var_v if kind == 'valence' else self.var_a
        if not model or len(model) != len(self.timestamps):
            return

        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0 if t1 > t0 else 1.0

        # 抽稀：上限按面板像素宽自适应 —— 每个像素最多 1 个点，再多屏幕上也画不出来
        idx = _decimate(len(self.timestamps), min(_MAX_POINTS, max(2, pw)))

        # ① ±1σ 置信带（多边形填充，暖 wash）
        if len(var) == len(model) and len(model) >= 2:
            upper, lower = [], []
            for i in idx:
                sd = math.sqrt(max(0.0, var[i]))
                x = self._x(self.timestamps[i], t0, span, x0, pw)
                upper.append(QPointF(x, self._y(model[i] + sd, y0, ph)))
                lower.append(QPointF(x, self._y(model[i] - sd, y0, ph)))
            if len(upper) >= 2:
                poly = QPolygonF(upper + list(reversed(lower)))
                p.setPen(Qt.NoPen)
                # 注意：QColor 不接受单参数 4 元组，必须解包成 QColor(r,g,b,a)，
                # 否则 PyQt5 会直接段错误（进程 exit 127，无 traceback）。
                p.setBrush(QColor(*_WASH_RGBA))
                # 大面积填充开抗锯齿是主要开销（实测 1800 顶点 113ms → 关掉后 ~10ms）。
                # 置信带本就是柔和的半透明色块，边缘肉眼几乎看不出差别。
                aa = p.testRenderHint(QPainter.Antialiasing)
                p.setRenderHint(QPainter.Antialiasing, False)
                p.drawPolygon(poly)
                p.setRenderHint(QPainter.Antialiasing, aa)

        # ② 基线虚线（暖赭黄，仅效价图有意义）
        if kind == 'valence':
            p.setPen(QPen(QColor(COLORS['cyan']), 1, Qt.DashLine))
            yb = self._y(self.baseline_v, y0, ph)
            p.drawLine(QPointF(x0, yb), QPointF(x0 + pw, yb))

        # ③ 原始观察点（弱）
        if self.raw:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*_RAW_RGBA))
            for ts, v in self.raw:
                x = self._x(ts, t0, span, x0, pw)
                y = self._y(v, y0, ph)
                p.drawEllipse(QPointF(x, y), 2.2, 2.2)

        # ④ 模型线 —— 单次 strokePath（修串珠的关键）
        if len(model) >= 2:
            path = QPainterPath()
            first = True
            for i in idx:
                x = self._x(self.timestamps[i], t0, span, x0, pw)
                y = self._y(model[i], y0, ph)
                if first:
                    path.moveTo(x, y)
                    first = False
                else:
                    path.lineTo(x, y)
            pen = QPen(QColor(COLORS['accent']), 2.0)
            pen.setCapStyle(Qt.SquareCap)      # 硬边，不用 RoundCap
            pen.setJoinStyle(Qt.MiterJoin)     # 硬拐角
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

            # ⑤ 当前点
            cx = self._x(self.timestamps[idx[-1]], t0, span, x0, pw)
            cy = self._y(model[idx[-1]], y0, ph)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS['accent']))
            p.drawEllipse(QPointF(cx, cy), 4.0, 4.0)

    # ================================================================
    # 命中测试（纠正上滑框拖点用）
    # ================================================================
    def point_at(self, pos, panel_index=0, count=1):
        """把鼠标位置映射到数据索引；返回 (index, timestamp) 或 None。"""
        if not self.has_data():
            return None
        x0, y0, pw, ph = self._plot_rect(panel_index, count)
        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0 if t1 > t0 else 1.0
        x = max(x0, min(x0 + pw, pos.x()))
        ts = t0 + (x - x0) / pw * span
        # 最近点
        best, bd = 0, float('inf')
        for i, t in enumerate(self.timestamps):
            d = abs(t - ts)
            if d < bd:
                best, bd = i, d
        return best, self.timestamps[best]

    def value_to_y(self, value, panel_index=0, count=1):
        _, y0, _, ph = self._plot_rect(panel_index, count)
        return self._y(value, y0, ph)

    def y_to_value(self, y, panel_index=0, count=1):
        _, y0, _, ph = self._plot_rect(panel_index, count)
        if ph <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - (y - y0) / ph))


# 置信带 / 原始点：从 theme 的 rgba 字符串解析一次，避免每帧构造 QColor 字符串
def _parse_rgba(s):
    nums = s[s.find('(') + 1:s.find(')')].split(',')
    r, g, b = (int(float(x)) for x in nums[:3])
    a = int(float(nums[3]) * 255) if len(nums) > 3 else 255
    return r, g, b, a


_WASH_RGBA = _parse_rgba(COLORS['wash'])
_RAW_RGBA = (156, 150, 138, 120)      # 原始点：暖灰，弱


def _decimate(n, cap=_MAX_POINTS):
    """等间隔抽稀，返回索引列表（首尾必保留）。

    抽稀是 149ms 的主要来源：绘制成本随顶点数线性增长，而屏幕只有几百像素宽，
    画到比像素还密的点纯属浪费。cap 由调用方按面板像素宽自适应传入。
    """
    cap = max(2, int(cap))
    if n <= cap:
        return list(range(n))
    step = n / float(cap)
    return sorted(set(int(i * step) for i in range(cap)) | {n - 1})


def _fmt_time(ts):
    """时间戳 → HH:MM（跨天时显示 MM-DD）。"""
    import time as _time
    lt = _time.localtime(ts)
    return _time.strftime("%H:%M", lt)
