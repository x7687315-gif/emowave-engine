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
import bisect
import math

from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import (QPainter, QPen, QColor, QPolygonF, QPainterPath,
                         QPixmap, QFontMetrics)
from PyQt5.QtWidgets import QWidget, QSizePolicy

from theme import COLORS, CURVE_STYLE, METRICS, app_font

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

    def __init__(self, parent=None, series="valence",
                 compact=False, min_width=None, show_hover=True):
        super().__init__(parent)
        self.series = series
        self.compact = compact          # 紧凑模式：隐藏图例与轴标签（上滑框小图用）
        # 悬停读数只在主曲线开启：挂件/纠正画布太小，浮层会盖住曲线本身
        self.show_hover = bool(show_hover and not compact)

        self.timestamps = []
        self.model_v = []
        self.model_a = []
        self.var_v = []
        self.var_a = []
        self.baseline_v = 0.5
        self.raw = []                   # [(ts, value)]
        self.corrections = []           # [{ts, channel, value}] 用户修正点
        self.predicted_from = None      # 该时刻之后的点属于「预测段」（虚线）
        self._hover = None              # 悬停读数（自绘，不用 QToolTip）
        self._corner_radius = 0 if compact else METRICS['radius']
        self.bg_token = 'paper'         # 主屏画在纸底上；挂件由外层给白底
        self.waterline = None           # 当前效价（蓝色横线）；None = 不画
        self.fit_range = False          # 紧凑窗：纵轴按当段自适应（只读形状）
        self._lo, self._hi = 0.0, 1.0

        # 静态层缓存（网格/刻度/标签）—— 尺寸不变就不重画
        self._static_cache = None
        self._static_key = None

        # 320 是原写死的下限（留 Y 轴刻度 38px + 左右内边距 62px）。
        # 小窗化后新增 min_width 参数：迷你置顶窗（阶段 4）只有 360px 宽，
        # 需要把左内边距一并收窄，否则控件比窗还宽、根本摆不进去。
        self._min_w = 320 if min_width is None else min_width
        self.setMinimumSize(self._min_w, 180 if not compact else 96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(self.show_hover)

    # ================================================================
    # 数据
    # ================================================================
    def set_data(self, timestamps, model_v, model_a=None, var_v=None, var_a=None,
                 baseline_v=0.5, raw=None, predicted_from=None, corrections=None):
        self.timestamps = list(timestamps)
        self.model_v = list(model_v)
        self.model_a = list(model_a or [])
        self.var_v = list(var_v or [])
        self.var_a = list(var_a or [])
        self.baseline_v = baseline_v
        self.raw = list(raw or [])
        self.predicted_from = predicted_from
        # 用户修正点：[{ts, channel, value}]，与真实观察点画法不同
        self.corrections = list(corrections or [])
        self._hover = None
        self.update()

    def set_waterline(self, value):
        """设置"水位线"：当前效价在轴上的位置。这是新版的主视觉。"""
        self.waterline = None if value is None else float(value)
        self.update()

    def clear(self):
        self.timestamps = []
        self.model_v = []
        self.model_a = []
        self.var_v = []
        self.var_a = []
        self.raw = []
        self.predicted_from = None
        self.corrections = []
        self._hover = None
        self.update()

    def has_data(self):
        return len(self.timestamps) >= 2 and len(self.model_v) >= 2

    def prediction_split(self):
        """返回预测段的起始索引（该索引及之后画虚线）；无预测段返回 None。

        真实观察 + 平滑历史是实线，越过最后一次自评的外推部分是虚线——
        用户必须能一眼看出"哪些是测出来的、哪些是猜的"。
        """
        if self.predicted_from is None or not self.timestamps:
            return None
        for i, ts in enumerate(self.timestamps):
            if ts > self.predicted_from:
                return max(1, i)
        return None

    # ================================================================
    # 坐标映射
    # ================================================================
    def _plot_rect(self, index=0, count=1):
        """返回第 index 个子图（共 count 个）的绘图矩形。

        双曲线模式：两个子图各占可用高度的 50%，
        上图底部给"效价"标题让出空间，下图顶部给"唤醒"标题让出空间。
        """
        w, h = self.width(), self.height()
        # 迷你挂件是 sparkline：高度很小时去掉刻度留白，别让 100/50/0 叠在一起
        if self.compact and h < 100:
            left, top, right, bottom = 10, 6, w - 8, h - 6
            return left, top, right - left, bottom - top
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

    def _update_range(self):
        if not self.fit_range or not self.model_v:
            self._lo, self._hi = 0.0, 1.0
            return
        vals = list(self.model_v) + [self.baseline_v]
        lo, hi = min(vals) - 0.06, max(vals) + 0.06
        if hi - lo < 0.3:
            mid = (hi + lo) / 2.0
            lo, hi = mid - 0.15, mid + 0.15
        self._lo, self._hi = max(0.0, lo), min(1.0, hi)

    def _y(self, value, y0, ph):
        if value is None:
            v = 0.0
        else:
            v = (float(value) - self._lo) / max(1e-6, self._hi - self._lo)
            v = max(0.0, min(1.0, v))
        return y0 + (1.0 - v) * ph

    # ================================================================
    # 绘制
    # ================================================================
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        # 新版没有卡片：曲线直接画在页面底上（挂件由外层给 surface）
        p.fillRect(self.rect(), QColor(COLORS[self.bg_token]))

        if not self.has_data():
            p.setPen(QColor(COLORS['muted']))
            p.setFont(app_font(11))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "开始记录后，这里会出现你的情绪曲线")
            self._static_key = None
            self._static_cache = None
            return

        panels = self._panels()
        n = len(panels)
        self._update_range()

        # 静态层（网格 / 刻度 / 轴标签）走 pixmap 缓存：尺寸、面板、时间范围不变就不重画
        p.drawPixmap(0, 0, self._ensure_static(panels))

        for i, kind in enumerate(panels):
            x0, y0, pw, ph = self._plot_rect(i, n)
            self._paint_series(p, x0, y0, pw, ph, kind)

        if self._hover is not None:
            self._paint_hover(p, panels, self._hover)

    # ================================================================
    # 悬停读数（自绘，不用 QToolTip：避免闪烁与坐标系不同步）
    # ================================================================
    def mouseMoveEvent(self, event):
        if not self.show_hover:
            return super().mouseMoveEvent(event)
        info = self.hover_at(event.pos().x(), event.pos().y())
        if info != self._hover:
            self._hover = info
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def hover_at(self, x, y=None):
        """把鼠标坐标映射成一条读数；无数据/在绘图区外返回 None。

        返回 {panel, index, ts, value, kind, raw, correction}
        kind ∈ 观察 / 修正 / 预测 / 估计 —— 与曲线画法同一套语义。
        """
        if not self.has_data():
            return None
        panels = self._panels()
        count = len(panels)

        if count == 1:
            panel_index = 0
            panel = panels[0]
        else:
            panel_index = None
            if y is None:
                panel_index = 0
            else:
                for i in range(count):
                    _, py, _, ph = self._plot_rect(i, count)
                    if py <= y <= py + ph:
                        panel_index = i
                        break
            if panel_index is None:
                return None
            panel = panels[panel_index]

        x0, y0, pw, ph = self._plot_rect(panel_index, count)
        if pw <= 0 or x < x0 or x > x0 + pw:
            return None

        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0 if t1 > t0 else 1.0
        target = t0 + (x - x0) / pw * span
        i = bisect.bisect_left(self.timestamps, target)
        if i <= 0:
            i = 0
        elif i >= len(self.timestamps):
            i = len(self.timestamps) - 1
        elif abs(self.timestamps[i] - target) >= abs(self.timestamps[i - 1] - target):
            i -= 1

        model = self.model_v if panel == 'valence' else self.model_a
        if i >= len(model):
            return None
        ts = self.timestamps[i]
        value = model[i]

        raw_value = None
        raw_dist = None
        if panel == 'valence' and self.raw:
            nearest = min(self.raw, key=lambda p: abs(p[0] - ts))
            raw_dist = abs(nearest[0] - ts)
            if raw_dist <= 2.0:
                raw_value = nearest[1]

        correction_value = None
        corr_dist = None
        marks = [c for c in self.corrections if c.get("channel") == panel]
        if marks:
            nearest = min(marks, key=lambda c: abs(c["ts"] - ts))
            corr_dist = abs(nearest["ts"] - ts)
            if corr_dist <= 2.0:
                correction_value = nearest["value"]

        if self.predicted_from is not None and ts > self.predicted_from:
            kind = "预测"
        elif correction_value is not None and (
                raw_value is None or corr_dist <= raw_dist):
            kind = "修正"
        elif raw_value is not None:
            kind = "观察"
        else:
            kind = "估计"

        return {
            "panel": panel, "index": i, "ts": ts, "value": value,
            "kind": kind, "raw": raw_value, "correction": correction_value,
        }

    def _paint_hover(self, p, panels, info):
        panel_index = panels.index(info["panel"]) if info["panel"] in panels else 0
        count = len(panels)
        x0, y0, pw, ph = self._plot_rect(panel_index, count)
        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0 if t1 > t0 else 1.0
        x = self._x(info["ts"], t0, span, x0, pw)
        y = self._y(info["value"], y0, ph)

        # 竖直参考线贯穿所有子图（双图时用户能同时看到 V/A 的时间位置）
        p.setPen(QPen(QColor(COLORS['muted']), 1, Qt.DashLine))
        for i in range(count):
            _, py, _, ph_i = self._plot_rect(i, count)
            p.drawLine(QPointF(x, py), QPointF(x, py + ph_i))

        # 当前估计点
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS['accent']))
        p.drawEllipse(QPointF(x, y), 4.0, 4.0)

        parts = [_fmt_time(info["ts"], span),
                 "%s %d" % (info["kind"], int(info["value"] * 100))]
        if info.get("raw") is not None and abs(info["raw"] - info["value"]) > 0.005:
            parts.append("观察 %d" % int(info["raw"] * 100))
        if info.get("correction") is not None:
            parts.append("修正 %d" % int(info["correction"] * 100))
        text = " · ".join(parts)

        fm = QFontMetrics(app_font(9))
        box_w = min(fm.horizontalAdvance(text) + 12, max(80, self.width() - 8))
        box_h = fm.height() + 6
        text = fm.elidedText(text, Qt.ElideRight, int(box_w - 12))
        lx = x + 8
        if lx + box_w > self.width() - 4:
            lx = x - box_w - 8
        lx = max(4, lx)
        ly = y - box_h - 8
        if ly < 4:
            ly = y + 8
        ly = min(max(4, ly), max(4, self.height() - box_h - 4))

        box = QPainterPath()
        box.addRoundedRect(QRectF(lx, ly, box_w, box_h), 4, 4)
        p.setPen(Qt.NoPen)
        p.fillPath(box, QColor(COLORS['surface']))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(COLORS['rule']), 1))
        p.drawPath(box)
        p.setPen(QColor(COLORS['ink_2']))
        p.setFont(app_font(9))
        p.drawText(QRectF(lx + 6, ly + 3, box_w - 12, box_h - 6),
                   Qt.AlignLeft | Qt.AlignVCenter, text)

    # ---------- 静态层缓存 ----------
    def _panels(self):
        return (['valence', 'arousal'] if self.series == 'both'
                and len(self.model_a) >= 2 else [self.series])

    def _static_key_now(self, panels):
        """静态层只依赖：尺寸、面板、时间范围、是否紧凑。

        注意**不含数据值**——数据变了只需要重画数据层，网格和刻度不用动。
        """
        t0 = self.timestamps[0] if self.timestamps else 0.0
        t1 = self.timestamps[-1] if self.timestamps else 0.0
        return (self.width(), self.height(), tuple(panels), self.compact,
                round(t0, 3), round(t1 - t0, 3))

    def _ensure_static(self, panels):
        key = self._static_key_now(panels)
        if key == self._static_key and self._static_cache is not None:
            return self._static_cache

        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(COLORS[self.bg_token]))

        pp = QPainter(pm)
        pp.setRenderHint(QPainter.Antialiasing, True)
        pp.setRenderHint(QPainter.TextAntialiasing, True)
        n = len(panels)
        for i, kind in enumerate(panels):
            x0, y0, pw, ph = self._plot_rect(i, n)
            self._paint_static(pp, x0, y0, pw, ph, kind)
        pp.end()

        self._static_key = key
        self._static_cache = pm
        return pm

    # ---------- 静态层：网格 + 刻度 + 轴标签 ----------
    def _paint_static(self, p, x0, y0, pw, ph, kind):
        # 3 条横线（100 / 50 / 0）
        p.setPen(QPen(QColor(COLORS['grid']), 1))
        p.setFont(app_font(8))
        for tick in _Y_TICKS:
            y = self._y(tick, y0, ph)
            p.drawLine(QPointF(x0, y), QPointF(x0 + pw, y))
            if not (self.compact and self.height() < 100):
                # 短刻度 + 数值（用户要求"刻度不要太多"）
                p.setPen(QColor(COLORS['muted']))
                p.drawText(QRectF(x0 - 38, y - 6, 32, 12),
                           Qt.AlignRight | Qt.AlignVCenter, str(int(tick * 100)))
                p.setPen(QPen(QColor(COLORS['grid']), 1))

        if self.compact:
            return

        # 时间轴：按面板宽度放若干刻度（真实时钟时间，短跨度显示到秒）
        t0, t1 = self.timestamps[0], self.timestamps[-1]
        span = t1 - t0
        p.setPen(QColor(COLORS['muted']))
        p.setFont(app_font(8))
        nt = max(3, min(6, int(pw / 110) + 1))
        wide = 78
        for i in range(nt):
            frac = i / (nt - 1)
            ts = t0 + span * frac
            x = self._x(ts, t0, span, x0, pw)
            label = _fmt_time(ts, span)
            if i == 0:
                rx, al = x0, Qt.AlignLeft
            elif i == nt - 1:
                rx, al = x0 + pw - wide, Qt.AlignRight
            else:
                rx, al = x - wide / 2, Qt.AlignHCenter
            p.drawText(QRectF(rx, y0 + ph + 6, wide, 12), al | Qt.AlignVCenter, label)

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
                poly = QPolygonF(_densify(upper) + list(reversed(_densify(lower))))
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
            baseline = CURVE_STYLE['baseline']
            p.setPen(QPen(QColor(COLORS[baseline['color']]),
                          baseline['width'], Qt.DashLine))
            yb = self._y(self.baseline_v, y0, ph)
            p.drawLine(QPointF(x0, yb), QPointF(x0 + pw, yb))
            if not self.compact:
                p.setFont(app_font(8))
                text = "常态 %d" % round(self.baseline_v * 100)
                w = p.fontMetrics().horizontalAdvance(text)
                # 纸色小块垫底：标签可能压在置信带/曲线上，不垫会糊
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(COLORS[self.bg_token]))
                p.drawRect(QRectF(x0 + pw - w - 8, yb - 13, w + 10, 13))
                p.setBrush(Qt.NoBrush)
                p.setPen(QColor(COLORS['muted']))
                p.drawText(QPointF(x0 + pw - w - 4, yb - 3), text)

        # ③ 原始观察点（弱）
        if self.raw:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*_RAW_RGBA))
            for ts, v in self.raw:
                x = self._x(ts, t0, span, x0, pw)
                y = self._y(v, y0, ph)
                p.drawEllipse(QPointF(x, y), 2.2, 2.2)

        # ③.5 用户修正点（空心方框）—— 与真实观察的实心圆区分开：
        #     "我改过的" 和 "我说过的" 不是一回事，用户必须一眼能分辨。
        marks = [c for c in self.corrections if c.get("channel") == kind]
        if marks:
            correction = CURVE_STYLE['correction']
            p.setPen(QPen(QColor(COLORS[correction['color']]),
                          correction['width']))
            p.setBrush(Qt.NoBrush)
            half = correction['size'] / 2.0
            for mark in marks:
                x = self._x(mark["ts"], t0, span, x0, pw)
                y = self._y(mark["value"], y0, ph)
                p.drawRect(QRectF(x - half, y - half,
                                  correction['size'], correction['size']))

        # ④ 模型线 —— 单次 strokePath（修串珠的关键）
        #    越过最后一次自评的部分是**预测**，用虚线画：用户要能看出哪些是猜的。
        if len(model) >= 2:
            split = self.prediction_split()
            solid = idx if split is None else [i for i in idx if i < split]
            dashed = [] if split is None else [i for i in idx if i >= split]
            if dashed and solid:
                dashed = [solid[-1]] + dashed      # 预测段与实线接上，避免断口

            def _stroke(indices, line_style, color_token):
                path = _smooth_path([
                    QPointF(self._x(self.timestamps[i], t0, span, x0, pw),
                            self._y(model[i], y0, ph)) for i in indices])
                pen = QPen(QColor(COLORS[color_token]),
                           CURVE_STYLE['model']['width'], line_style)
                # 圆头圆角 = 柔和；折线被单调插值抹平后，圆角才不会露出接缝
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)

            if len(solid) >= 2:
                _stroke(solid, Qt.SolidLine, CURVE_STYLE['model']['color'])
            if len(dashed) >= 2:
                _stroke(dashed, Qt.DashLine, CURVE_STYLE['forecast']['color'])

            # ⑤ 当前点
            cx = self._x(self.timestamps[idx[-1]], t0, span, x0, pw)
            cy = self._y(model[idx[-1]], y0, ph)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS[CURVE_STYLE['model']['color']]))
            p.drawEllipse(QPointF(cx, cy), 4.0, 4.0)

        # ⑥ 水位线：当前效价的位置。全图唯一的高饱和元素（画在最上层）
        if kind == 'valence' and self.waterline is not None:
            yw = self._y(self.waterline, y0, ph)
            pen = QPen(QColor(COLORS['accent']), 2.0)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x0, yw), QPointF(x0 + pw, yw))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS['accent']))
            p.drawEllipse(QPointF(x0, yw), 3.2, 3.2)
            p.setBrush(QColor(COLORS['surface']))
            p.drawEllipse(QPointF(x0, yw), 1.3, 1.3)

    def y_to_value(self, y, panel_index=0, count=1):
        _, y0, _, ph = self._plot_rect(panel_index, count)
        if ph <= 0:
            return 0.0
        frac = max(0.0, min(1.0, 1.0 - (y - y0) / ph))
        return self._lo + frac * (self._hi - self._lo)


# 置信带 / 原始点：从 theme 的 rgba 字符串解析一次，避免每帧构造 QColor 字符串
def _parse_rgba(s):
    nums = s[s.find('(') + 1:s.find(')')].split(',')
    r, g, b = (int(float(x)) for x in nums[:3])
    a = int(float(nums[3]) * 255) if len(nums) > 3 else 255
    return r, g, b, a


_WASH_RGBA = _parse_rgba(COLORS['wash'])
#: 四种曲线元素的画法统一来自 theme.CURVE_STYLE（语义不随页面变）
_RAW_RGBA = CURVE_STYLE['observation']['color']


def _monotone_slopes(xs, ys):
    """Fritsch–Carlson：保证每段内不产生新极值（不造峰、不造谷）。"""
    n = len(xs)
    if n < 2:
        return [0.0] * n
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2.0
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        sq = a * a + b * b
        if sq > 9.0:
            t = 3.0 / math.sqrt(sq)
            m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    return m


def _dedupe(points):
    out = []
    for pt in points:
        if out and abs(pt.x() - out[-1].x()) < 0.4:
            out[-1] = pt
        else:
            out.append(pt)
    return out


def _smooth_path(points):
    """把折线变成单调三次贝塞尔：穿过每一点，不改变数值。"""
    pts = _dedupe(points)
    path = QPainterPath()
    if len(pts) < 2:
        return path
    xs = [p.x() for p in pts]
    ys = [p.y() for p in pts]
    ms = _monotone_slopes(xs, ys)
    path.moveTo(pts[0])
    for i in range(len(pts) - 1):
        h = xs[i + 1] - xs[i]
        path.cubicTo(QPointF(xs[i] + h / 3, ys[i] + ms[i] * h / 3),
                     QPointF(xs[i + 1] - h / 3, ys[i + 1] - ms[i + 1] * h / 3),
                     pts[i + 1])
    return path


def _densify(points, steps=6):
    """按同一套插值采样成折线——面积填充用（反向拼接贝塞尔不划算）。"""
    pts = _dedupe(points)
    if len(pts) < 2:
        return list(pts)
    xs = [p.x() for p in pts]
    ys = [p.y() for p in pts]
    ms = _monotone_slopes(xs, ys)
    out = [pts[0]]
    for i in range(len(pts) - 1):
        h = xs[i + 1] - xs[i]
        for k in range(1, steps + 1):
            t = k / float(steps)
            t2, t3 = t * t, t * t * t
            y = ((2 * t3 - 3 * t2 + 1) * ys[i]
                 + (t3 - 2 * t2 + t) * h * ms[i]
                 + (-2 * t3 + 3 * t2) * ys[i + 1]
                 + (t3 - t2) * h * ms[i + 1])
            out.append(QPointF(xs[i] + h * t, y))
    return out


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


def _fmt_time(ts, span=None):
    """时间戳 → 可读时钟时间。跨度小就显示到秒，跨天就带上日期。

    短记录（几十秒）若只显示到分钟，三个刻度会同为"00:05"，看着像卡住；
    这里按可见跨度自适应精度，让时间轴真正"走得动"。
    """
    import time as _time
    lt = _time.localtime(ts)
    if span is not None and span >= 86400:      # 跨天
        return _time.strftime("%m-%d %H:%M", lt)
    if span is not None and span < 180:         # 短于 3 分钟：显示到秒
        return _time.strftime("%H:%M:%S", lt)
    return _time.strftime("%H:%M", lt)
