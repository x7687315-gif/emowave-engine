"""softcurve.py — 柔和曲线 + 特色基线（数据不变，只改绘制）

=== "更柔和"是怎么做到的（并且没有篡改数据）===
相邻两个模型点之间用**单调三次 Hermite 插值**（Fritsch–Carlson）连接：

  · 它穿过每一个模型点，**不改变任何模型值**；
  · 它保证每段内不产生新的极值 —— 也就是**不可能凭空造出一个峰或谷**；
  · 斜率被限制在 3 倍割线以内，避免过冲（overshoot）。

为什么可以这样做：内核给出的是 Matérn 3/2 状态空间后验的均值，它本身就是光滑函数；
而 `curve_payload` 只在观察时刻上采样（11 个点）。把采样点之间如实连接起来是
**绘制分辨率**问题，不是模型问题。方差带同理，上下界各自插值。

绝对不做的事：对原始观察点做平滑、改预测、改方差、为了让曲线好看而移动任何点。

=== 基线（"常态带"）===
`_diff_from_baseline` 里已有产品规则：|差| ≤ 2 视为"持平"。
所以轴上的"常态"不是一个点，而是一条 **±2 的带**——这是既有的产品定义，
不是新造的统计量。画法：轴上一个小括号圈出这个区间 + "常态 55" 两行字，
plot 内一条点线。它同时回答了"我的正常值是多少"和"多接近才算没变"。
"""
import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QPainterPath, QPen

import tokens as T

#: 与 console_presenter._diff_from_baseline 的"持平"阈值保持一致
BASELINE_TOLERANCE = 0.02


# ================================================================
# 几何
# ================================================================
class Geo:
    """时间 → x；效价 → y。y 轴固定 0–1，不自动缩放（缩放会夸大波动）。"""

    def __init__(self, payload, x0, y0, x1, y1, y_range=None):
        ts = list(payload.get('ts') or [])
        self.lo, self.hi = y_range if y_range else (0.0, 1.0)
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        t0 = ts[0] if ts else 0.0
        t1 = ts[-1] if ts else 1.0
        span = max(1e-6, t1 - t0)
        self.t0 = t0 - span * 0.012
        self.t1 = t1 + span * 0.012
        self._span = max(1e-6, self.t1 - self.t0)

    def x(self, ts):
        return self.x0 + (ts - self.t0) / self._span * (self.x1 - self.x0)

    def y(self, value):
        v = (float(value) - self.lo) / max(1e-6, self.hi - self.lo)
        v = min(1.0, max(0.0, v))
        return self.y1 - v * (self.y1 - self.y0)


# ================================================================
# 单调三次插值
# ================================================================
def _dedupe(points):
    """同像素列只留最后一个点（5 分钟尾巴在 4px 里挤了 300 个点）。"""
    out = []
    for pt in points:
        if out and abs(pt.x() - out[-1].x()) < 0.4:
            out[-1] = pt
        else:
            out.append(pt)
    return out


def _slopes(xs, ys):
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
        s = a * a + b * b
        if s > 9.0:
            t = 3.0 / math.sqrt(s)
            m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    return m


def _hermite(xs, ys, ms, i, t, h):
    t2, t3 = t * t, t * t * t
    h00 = 2 * t3 - 3 * t2 + 1
    h10 = t3 - 2 * t2 + t
    h01 = -2 * t3 + 3 * t2
    h11 = t3 - t2
    return h00 * ys[i] + h10 * h * ms[i] + h01 * ys[i + 1] + h11 * h * ms[i + 1]


def smooth_path(points):
    """直接返回贝塞尔路径（线用）。"""
    pts = _dedupe(points)
    path = QPainterPath()
    if len(pts) < 2:
        return path
    xs = [p.x() for p in pts]
    ys = [p.y() for p in pts]
    ms = _slopes(xs, ys)
    path.moveTo(pts[0])
    for i in range(len(pts) - 1):
        h = xs[i + 1] - xs[i]
        path.cubicTo(QPointF(xs[i] + h / 3, ys[i] + ms[i] * h / 3),
                     QPointF(xs[i + 1] - h / 3, ys[i + 1] - ms[i + 1] * h / 3),
                     pts[i + 1])
    return path


def densify(points, steps=6):
    """按同一套插值采样成折线（面用；QPainterPath 的反向拼接不好做）。"""
    pts = _dedupe(points)
    if len(pts) < 2:
        return list(pts)
    xs = [p.x() for p in pts]
    ys = [p.y() for p in pts]
    ms = _slopes(xs, ys)
    out = [pts[0]]
    for i in range(len(pts) - 1):
        h = xs[i + 1] - xs[i]
        for k in range(1, steps + 1):
            t = k / float(steps)
            out.append(QPointF(xs[i] + h * t, _hermite(xs, ys, ms, i, t, h)))
    return out


# ================================================================
# 绘制
# ================================================================
def _forecast_index(payload):
    ts = payload.get('ts') or []
    cut = payload.get('predicted_from')
    if not ts or cut is None:
        return len(ts)
    for i, t in enumerate(ts):
        if t >= cut:
            return i
    return len(ts)


def _series(payload, key='mv'):
    ts = payload.get('ts') or []
    vals = payload.get(key) or []
    return ts, vals


def draw_band(p, payload, geo):
    """置信带：上下界各自单调插值，填充成柔和的蓝雾。"""
    ts, mv = _series(payload)
    vv = payload.get('vv') or []
    if len(mv) < 2:
        return
    upper, lower = [], []
    for t, m, var in zip(ts, mv, vv):
        sd = min(0.45, math.sqrt(max(0.0, var)))
        upper.append(QPointF(geo.x(t), geo.y(m + sd)))
        lower.append(QPointF(geo.x(t), geo.y(m - sd)))
    up = densify(upper)
    lo = densify(lower)
    path = QPainterPath()
    path.moveTo(up[0])
    for pt in up[1:]:
        path.lineTo(pt)
    for pt in reversed(lo):
        path.lineTo(pt)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(*T.BLUE_WASH)))
    p.drawPath(path)


def draw_estimate(p, payload, geo):
    """实线 = 模型估计；虚线 = 外推。两段都用同一套插值，接缝处共享端点。"""
    ts, mv = _series(payload)
    if len(mv) < 2:
        return
    cut = _forecast_index(payload)
    solid = [QPointF(geo.x(t), geo.y(v)) for t, v in zip(ts[:cut + 1], mv[:cut + 1])]
    if len(solid) >= 2:
        pen = QPen(QColor(T.INK), T.LINE_W)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(smooth_path(solid))
    if cut < len(ts) - 1:
        dashed = [QPointF(geo.x(t), geo.y(v))
                  for t, v in zip(ts[cut:], mv[cut:])]
        if len(dashed) >= 2:
            pen = QPen(QColor(T.INK_2), T.LINE_W)
            pen.setCapStyle(Qt.RoundCap)
            pen.setDashPattern([3, 3])
            p.setPen(pen)
            p.drawPath(smooth_path(dashed))


def draw_observations(p, payload, geo):
    """观察点：很小的实心圆。半径比模型线宽还小，不抢主线的形状。"""
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T.INK_2))
    for ts, v in payload.get('raw') or []:
        p.drawEllipse(QPointF(geo.x(ts), geo.y(v)), T.OBS_R, T.OBS_R)


def draw_corrections(p, payload, geo):
    pen = QPen(QColor(T.BLUE_INK), 1.0)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    half = T.CORR_SIZE / 2.0
    for c in payload.get('corrections') or []:
        if c.get('channel') != 'valence':
            continue
        pt = QPointF(geo.x(c['ts']), geo.y(c['value']))
        p.drawRect(QRectF(pt.x() - half, pt.y() - half,
                          T.CORR_SIZE, T.CORR_SIZE))


def draw_baseline(p, payload, geo, axis_x, color=None, label=True):
    """常态：plot 内一条点线 + 轴上圈住 ±2 的括号 + 两行标签。"""
    b = payload.get('baseline_v')
    if b is None:
        return
    y = geo.y(b)
    pen = QPen(QColor(color or T.FAINT), T.BASE_W)
    pen.setDashPattern([1, 3])
    p.setPen(pen)
    p.drawLine(QPointF(geo.x0, y), QPointF(geo.x1, y))
    if not label:
        return

    # 只留一条点线 + 两行标签。轴上那个圈出 ±2 的蓝色括号已按反馈删除：
    # 它更像"选中框"而不是刻度，反而让基线变重。±2 的规则改由
    # 模型页的标尺（浅蓝块）与历史页的偏差条承担。
    y_mid = y
    value = '%d' % round(b * 100)
    p.setFont(T.app_font(8))
    w_name = _w(p, '常态')
    p.setPen(QColor(T.MUTED))
    p.drawText(QPointF(axis_x - 8 - w_name, y_mid - 1), '常态')
    p.setFont(T.num_font(11, 0))
    w_val = _w(p, value)
    p.setPen(QColor(T.BLUE_INK))
    p.drawText(QPointF(axis_x - 8 - w_val, y_mid + 14), value)


def draw_waterline(p, payload, geo, value, axis_x):
    """当下：贯穿整幅的蓝线 + 轴上一个实心点。这是全屏唯一的高饱和元素。"""
    y = geo.y(value)
    pen = QPen(QColor(T.BLUE), T.WATER_W)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(axis_x, y), QPointF(geo.x1, y))

    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T.BLUE))
    p.drawEllipse(QPointF(axis_x, y), 3.4, 3.4)
    p.setBrush(QColor(T.PAPER))
    p.drawEllipse(QPointF(axis_x, y), 1.4, 1.4)


def draw_time_labels(p, geo, count=3):
    p.setFont(T.app_font(9))
    p.setPen(QColor(T.MUTED))
    fm = p.fontMetrics()
    for i in range(count):
        frac = i / float(count - 1) if count > 1 else 0.5
        ts = geo.t0 + (geo.t1 - geo.t0) * frac
        text = _clock(ts)
        w = fm.horizontalAdvance(text)
        x = (geo.x0 if i == 0 else
             geo.x1 - w if i == count - 1 else
             geo.x0 + (geo.x1 - geo.x0) * frac - w / 2.0)
        p.drawText(QPointF(x, geo.y1 + 15), text)


def _w(p, text):
    return p.fontMetrics().horizontalAdvance(text)


def _clock(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%H:%M')


def forecast_minutes(payload):
    ts = payload.get('ts') or []
    cut = payload.get('predicted_from')
    if not ts or cut is None or ts[-1] <= cut:
        return 0
    return int(round((ts[-1] - cut) / 60.0))
