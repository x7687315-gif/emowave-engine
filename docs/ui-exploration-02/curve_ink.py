"""curve_ink.py — 把真实 curve_payload 画到任意几何里（两套布局共用）

只做坐标映射与描边，不重算任何数据：
  · 平滑曲线来自内核 RTS，不在这里做二次平滑
  · 置信带 = mv ± sqrt(vv)，与生产一致
  · predicted_from 之后是外推 → 虚线
  · 观察点 / 修正方框 / 基线 各自独立，形状语义与生产完全一致

两套布局只是把同一批点放进不同的矩形，所以"曲线看起来不同"只可能来自
尺度和构图，不可能来自数据。
"""
import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QPainterPath, QPen

import gray_tokens as G


# ================================================================
# 几何
# ================================================================
class Geo:
    """时间 → x，效价 → y。y 轴固定 0–1，不做自动缩放（缩放会夸大波动）。"""

    def __init__(self, payload, x0, y0, x1, y1):
        ts = list(payload.get('ts') or [])
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.t0 = ts[0] if ts else 0.0
        self.t1 = ts[-1] if ts else 1.0
        span = max(1e-6, self.t1 - self.t0)
        # 两端各留 1.5% 余量：最后一个观察点不贴边
        self.t0 -= span * 0.015
        self.t1 += span * 0.015
        self._span = max(1e-6, self.t1 - self.t0)

    def x(self, ts):
        return self.x0 + (ts - self.t0) / self._span * (self.x1 - self.x0)

    def y(self, value):
        v = min(1.0, max(0.0, float(value)))
        return self.y1 - v * (self.y1 - self.y0)


def _fmt_clock(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%H:%M')


# ================================================================
# 语义元素
# ================================================================
def draw_band(p, payload, geo, color=G.BAND, alpha=110):
    mv = payload.get('mv') or []
    vv = payload.get('vv') or []
    ts = payload.get('ts') or []
    if len(mv) < 2:
        return
    upper, lower = [], []
    for t, m, var in zip(ts, mv, vv):
        sd = min(0.45, math.sqrt(max(0.0, var)))
        upper.append(QPointF(geo.x(t), geo.y(m + sd)))
        lower.append(QPointF(geo.x(t), geo.y(m - sd)))
    path = QPainterPath()
    path.moveTo(upper[0])
    for pt in upper[1:]:
        path.lineTo(pt)
    for pt in reversed(lower):
        path.lineTo(pt)
    path.closeSubpath()
    c = QColor(color)
    c.setAlpha(alpha)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawPath(path)


def _forecast_index(payload):
    """第一个"属于外推"的下标；没有预测段就返回 len(ts)。"""
    ts = payload.get('ts') or []
    cut = payload.get('predicted_from')
    if not ts or cut is None:
        return len(ts)
    for i, t in enumerate(ts):
        if t >= cut:
            return i
    return len(ts)


def draw_estimate(p, payload, geo, solid=G.INK, dashed=G.MUTED,
                  width=G.LINE_W, dash_pattern=(4, 4)):
    ts = payload.get('ts') or []
    mv = payload.get('mv') or []
    if len(mv) < 2:
        return
    cut = _forecast_index(payload)

    def _poly(a, b):
        path = QPainterPath()
        first = True
        for i in range(a, b):
            pt = QPointF(geo.x(ts[i]), geo.y(mv[i]))
            if first:
                path.moveTo(pt)
                first = False
            else:
                path.lineTo(pt)
        return path

    # 实线段：覆盖到预测起点（含一个重叠点，避免接缝）
    pen = QPen(QColor(solid), width)
    pen.setCapStyle(Qt.FlatCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_poly(0, min(len(ts), max(2, cut + 1))))

    # 虚线段：从预测起点开始
    if cut < len(ts) - 1:
        pen = QPen(QColor(dashed), width)
        pen.setCapStyle(Qt.FlatCap)
        pen.setDashPattern(list(dash_pattern))
        p.setPen(pen)
        p.drawPath(_poly(cut, len(ts)))


def draw_baseline(p, payload, geo, color=G.FAINT,
                  label='常态', label_color=G.MUTED, right=False):
    b = payload.get('baseline_v')
    if b is None:
        return
    y = geo.y(b)
    pen = QPen(QColor(color), G.BASE_W)
    pen.setDashPattern([1, 3])          # 点线：比预测虚线更细碎，形状不混淆
    p.setPen(pen)
    p.drawLine(QPointF(geo.x0, y), QPointF(geo.x1, y))
    if label:
        f = G.app_font(8)
        p.setFont(f)
        p.setPen(QColor(label_color))
        text = '%s %d' % (label, int(round(float(b) * 100)))
        if right:
            w = p.fontMetrics().horizontalAdvance(text)
            p.drawText(QPointF(geo.x1 - w, y - 4), text)
        else:
            p.drawText(QPointF(geo.x0, y - 4), text)


def draw_observations(p, payload, geo, r=G.OBS_R, color=G.INK_2):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    for ts, v in payload.get('raw') or []:
        p.drawEllipse(QPointF(geo.x(ts), geo.y(v)), r, r)


def draw_corrections(p, payload, geo, size=G.CORR_SIZE, color=G.INK):
    pen = QPen(QColor(color), G.CORR_W)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    half = size / 2.0
    for c in payload.get('corrections') or []:
        if c.get('channel') != 'valence':
            continue
        pt = QPointF(geo.x(c['ts']), geo.y(c['value']))
        p.drawRect(QRectF(pt.x() - half, pt.y() - half, size, size))


def draw_time_labels(p, geo, count=3, color=G.MUTED, size=9, dy=14):
    p.setFont(G.app_font(size))
    p.setPen(QColor(color))
    fm = p.fontMetrics()
    for i in range(count):
        frac = i / float(count - 1) if count > 1 else 0.5
        ts = geo.t0 + (geo.t1 - geo.t0) * frac
        text = _fmt_clock(ts)
        w = fm.horizontalAdvance(text)
        if i == 0:
            x = geo.x0
        elif i == count - 1:
            x = geo.x1 - w
        else:
            x = geo.x0 + (geo.x1 - geo.x0) * frac - w / 2.0
        p.drawText(QPointF(x, geo.y1 + dy), text)


def draw_now_tick(p, geo, color=G.INK_2, label='现在'):
    pen = QPen(QColor(color), 1)
    p.setPen(pen)
    p.drawLine(QPointF(geo.x1, geo.y0), QPointF(geo.x1, geo.y1))
    p.setFont(G.app_font(8))
    p.setPen(QColor(G.MUTED))
    w = p.fontMetrics().horizontalAdvance(label)
    p.drawText(QPointF(geo.x1 - w, geo.y0 - 6), label)


def draw_forecast_label(p, payload, geo, text='预测', color=G.MUTED):
    """在虚线起点就地标注，省掉一整行图例。"""
    cut = _forecast_index(payload)
    ts = payload.get('ts') or []
    mv = payload.get('mv') or []
    if cut >= len(ts) or cut <= 0:
        return
    pt = QPointF(geo.x(ts[cut]), geo.y(mv[cut]))
    p.setFont(G.app_font(8))
    p.setPen(QColor(color))
    p.drawText(QPointF(pt.x() + 4, pt.y() - 6), text)


def forecast_minutes(payload):
    """预测段有多长（分钟）。界面上用文字交代它，而不是靠几个像素的虚线。"""
    ts = payload.get('ts') or []
    cut = payload.get('predicted_from')
    if not ts or cut is None or ts[-1] <= cut:
        return 0
    return int(round((ts[-1] - cut) / 60.0))


def draw_forecast_caption(p, payload, geo, text=None):
    """右端一行小字，交代"虚线是什么、有多长"。

    为什么需要它：首屏的时间窗是 7.5 小时，而外推上限只有 10 分钟
    （`MAX_DECAY_SEC`），虚线在图上只占 1–2% 宽度 = 几个像素。
    把时间轴拉长来"让虚线好看"会篡改时间含义，所以改由文字保证语义可辨识。
    """
    if text is None:
        cut = _forecast_index(payload)
        ts = payload.get('ts') or []
        if cut < len(ts) - 1:
            minutes = max(1, int(round((ts[-1] - ts[cut]) / 60.0)))
            text = '虚线 = 接下来 %d 分钟' % minutes
        else:
            return
    p.setFont(G.app_font(8))
    p.setPen(QColor(G.MUTED))
    w = p.fontMetrics().horizontalAdvance(text)
    p.drawText(QPointF(geo.x1 - w, geo.y1 - 4), text)
