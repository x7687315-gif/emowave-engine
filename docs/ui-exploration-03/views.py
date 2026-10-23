"""views.py — 三个页面，同一套设计逻辑

共同规则（三页一致，不允许某页自己发明）：
  · 只有一种分隔手段：1px 的 RULE 线；没有任何卡片/圆角/阴影
  · 文字只有三级：INK / INK_2 / MUTED（FAINT 只画线）
  · 数字一律右对齐、等宽；标题一律 9px 宽字距的小标签
  · 蓝色只表示"位置"（当下水位线 / 常态带 / 主操作）
  · 每页都是"一条竖轴 + 一条水平基准"的思路的变体
"""
from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

import softcurve as S
import tokens as T


# ================================================================
# 共用零件
# ================================================================
def rule():
    ln = QFrame()
    ln.setFixedHeight(T.HAIRLINE)
    ln.setStyleSheet(f'background: {T.RULE}; border: none;')
    return ln


def label(text, size=9, color=None, spacing=1.4, weight=QFont.DemiBold):
    w = QLabel(text)
    w.setFont(T.app_font(size, weight))
    w.setStyleSheet(f'color: {color or T.MUTED}; letter-spacing: {spacing}px;')
    return w


def flat_button(text, color=None, size=11, weight=QFont.Normal, hover=None):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFlat(True)
    b.setFocusPolicy(Qt.NoFocus)
    b.setFont(T.app_font(size, weight))
    b.setStyleSheet(
        f'QPushButton {{ border: none; background: transparent;'
        f' color: {color or T.INK}; padding: 0; }}'
        f'QPushButton:hover {{ color: {hover or T.INK}; }}')
    return b


class QuietTabs(QWidget):
    current = pyqtSignal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)
        self._btns = []
        for i, text in enumerate(labels):
            b = flat_button(text, size=10)
            b.clicked.connect(lambda _=False, idx=i: self.set_current(idx))
            lay.addWidget(b)
            self._btns.append(b)
        lay.addStretch(1)
        self._idx = 0
        self._restyle()

    def set_current(self, idx):
        self._idx = int(idx)
        self._restyle()
        self.current.emit(self._idx)

    def _restyle(self):
        for i, b in enumerate(self._btns):
            active = (i == self._idx)
            b.setFont(T.app_font(10, QFont.DemiBold if active else QFont.Normal))
            b.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {T.INK if active else T.MUTED}; padding: 0; }}')


def scrolled(inner):
    """页面级滚动：360×600 下模型页/历史页必然超长，不能硬裁。"""
    from PyQt5.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget'
                       ' { background: transparent; border: none; }')
    area.setWidget(inner)
    return area


def header(tabs):
    head = QWidget()
    head.setFixedHeight(52)
    hl = QHBoxLayout(head)
    hl.setContentsMargins(T.PAD, 0, T.PAD, 0)
    hl.setSpacing(0)
    hl.addWidget(tabs, 1)
    mark = label('心潮', size=9, color=T.MUTED, spacing=2.5)
    hl.addWidget(mark)
    return head


# ================================================================
# 主屏：曲线 + 当下
# ================================================================
class SofaPlot(QWidget):
    """竖轴（含常态括号）+ 柔和曲线 + 蓝色水位线。"""

    AXIS_X = 46

    def __init__(self, parent=None):
        super().__init__(parent)
        self.payload = None
        self.value = 0.5
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_state(self, payload, value):
        self.payload = payload
        self.value = float(value)
        self.update()

    def _geo(self):
        return S.Geo(self.payload, self.AXIS_X + 12, 16,
                     self.width() - 6, self.height() - 26)

    def paintEvent(self, _e):
        if not self.payload:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        geo = self._geo()

        self._draw_scale(p, geo)
        S.draw_band(p, self.payload, geo)
        S.draw_baseline(p, self.payload, geo, self.AXIS_X)
        S.draw_estimate(p, self.payload, geo)
        S.draw_observations(p, self.payload, geo)
        S.draw_corrections(p, self.payload, geo)
        S.draw_time_labels(p, geo, count=3)
        S.draw_waterline(p, self.payload, geo, self.value, self.AXIS_X)
        p.end()

    def _draw_scale(self, p, geo):
        x = self.AXIS_X
        p.setPen(QPen(QColor(T.RULE), 1))
        p.drawLine(QPointF(x, geo.y0), QPointF(x, geo.y1))
        p.setFont(T.app_font(8))
        p.setPen(QColor(T.MUTED))
        fm = p.fontMetrics()
        for value in (1.0, 0.0):
            y = geo.y(value)
            p.setPen(QPen(QColor(T.RULE), 1))
            p.drawLine(QPointF(x - 3, y), QPointF(x, y))
            p.setPen(QColor(T.MUTED))
            text = '%d' % round(value * 100)
            p.drawText(QPointF(x - 8 - fm.horizontalAdvance(text), y + 3), text)


class MainPage(QWidget):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        text = QWidget()
        tl = QVBoxLayout(text)
        tl.setContentsMargins(T.PAD, 24, T.PAD, 0)
        tl.setSpacing(0)

        # 第一行：状态句（左）+ 当下数值（右）
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)
        self.state = QLabel('—')
        self.state.setFont(T.app_font(26, QFont.Medium))
        self.state.setStyleSheet(f'color: {T.INK};')
        rl.addWidget(self.state)
        rl.addStretch(1)

        now_box = QWidget()
        nl = QVBoxLayout(now_box)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(0)
        cap = label('效价', size=9, color=T.MUTED)
        cap.setAlignment(Qt.AlignRight)
        nl.addWidget(cap)
        self.now = QLabel('—')
        self.now.setFont(T.num_font(26, QFont.Light))
        self.now.setStyleSheet(f'color: {T.BLUE_INK};')
        self.now.setAlignment(Qt.AlignRight)
        nl.addWidget(self.now)
        rl.addWidget(now_box, 0, Qt.AlignBottom)
        tl.addWidget(row)

        tl.addSpacing(6)

        # 第二行：离差 + 预测 + 纠正
        dev_row = QWidget()
        dl = QHBoxLayout(dev_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)
        self.dev = QLabel('')
        self.dev.setFont(T.app_font(11))
        self.dev.setStyleSheet(f'color: {T.INK_2};')
        dl.addWidget(self.dev)
        dl.addStretch(1)
        self.corr = flat_button('纠正', color=T.MUTED, size=9)
        self.corr.setStyleSheet(
            f'QPushButton {{ border: none; background: transparent;'
            f' color: {T.MUTED}; text-decoration: underline; padding: 0;'
            f' font-size: 9px; }}')
        dl.addWidget(self.corr)
        tl.addWidget(dev_row)

        tl.addSpacing(10)
        lay.addWidget(text)

        self.plot = SofaPlot()
        lay.addWidget(self.plot, 1)

        meta = QWidget()
        ml = QHBoxLayout(meta)
        ml.setContentsMargins(T.PAD, 8, T.PAD, 10)
        ml.setSpacing(10)
        self.readouts = QLabel('')
        self.readouts.setFont(T.app_font(9.5))
        self.readouts.setStyleSheet(f'color: {T.MUTED};')
        ml.addWidget(self.readouts, 1)
        self.stamp = QLabel('')
        self.stamp.setFont(T.app_font(9))
        self.stamp.setStyleSheet(f'color: {T.MUTED};')
        ml.addWidget(self.stamp)
        lay.addWidget(meta)

    def sync(self, s):
        self.state.setText(s['state_name'])
        self.now.setText('%d' % round(s['v'] * 100))
        d = s['deviation']
        if abs(d) <= 2:
            dev = '与你的常态持平'
        elif d > 0:
            dev = '比你的常态高 %d' % d
        else:
            dev = '比你的常态低 %d' % abs(d)
        if s['recording']:
            dev = '正在记录 · ' + dev
        else:
            minutes = S.forecast_minutes(s['curve'] or {})
            if minutes:
                dev += '　·　预测 %d 分钟' % minutes
        self.dev.setText(dev)
        self.readouts.setText(
            '唤醒 %d　置信 %d%%　±1σ %s' % (
                round(s['a'] * 100), round(s['conf'] * 100), s['ci']))
        self.stamp.setText('估 %s' % _clock(s['state_ts']))
        self.plot.set_state(s['curve'], s['v'])


# ================================================================
# 模型页：常态编辑 + 模型状态
# ================================================================
class BaselineRuler(QWidget):
    """把主屏那条"常态"点线放大成一把横尺——同一套语言，不是新控件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.55
        self.setFixedHeight(38)

    def set_value(self, v):
        self.value = float(v)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        x0, x1 = 2, self.width() - 2
        y, span = 18, self.width() - 4

        def x(v):
            return x0 + max(0.0, min(1.0, v)) * span

        p.setPen(QPen(QColor('#C4C4C4'), 1))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))

        tol = S.BASELINE_TOLERANCE
        band = QRectF(x(self.value - tol), y - 8,
                      max(5.0, x(self.value + tol) - x(self.value - tol)), 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(58, 106, 136, 46))
        p.drawRect(band)
        pen = QPen(QColor(T.BLUE), 2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(band.left() - 1, y), QPointF(band.right() + 1, y))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.BLUE))
        p.drawEllipse(QPointF(x(self.value), y), 3.0, 3.0)

        p.setFont(T.app_font(8))
        p.setPen(QColor(T.MUTED))
        fm = p.fontMetrics()
        for v_, text, align in ((0.0, '0', 0), (0.5, '50', 1), (1.0, '100', 2)):
            xx = x(v_)
            p.setPen(QPen(QColor(T.RULE), 1))
            p.drawLine(QPointF(xx, y + 4), QPointF(xx, y + 7))
            p.setPen(QColor(T.MUTED))
            dx = 0 if align == 0 else (-fm.horizontalAdvance(text) / 2 if align == 1
                                       else -fm.horizontalAdvance(text))
            p.drawText(QPointF(xx + dx, y + 18), text)


class ModelPage(QWidget):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(T.PAD, 20, T.PAD, 0)
        lay.setSpacing(0)
        outer.addWidget(scrolled(inner))

        lay.addWidget(label('你的常态'))
        lay.addSpacing(6)
        hint = QLabel('±2 之内算「与常态持平」，它决定主屏那条点线的位置。')
        hint.setFont(T.app_font(10))
        hint.setStyleSheet(f'color: {T.MUTED};')
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addSpacing(6)

        # 步进器：− 55 +   （真控件，不是画出来的）
        step = QWidget()
        sh = QHBoxLayout(step)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(8)
        sh.addStretch(1)
        self.b_minus = flat_button('−', color=T.BLUE_INK, size=15)
        self.b_minus.clicked.connect(lambda: self.source.nudge(-0.02))
        sh.addWidget(self.b_minus)
        self.value = QLabel('55')
        self.value.setFont(T.num_font(24, QFont.Light))
        self.value.setStyleSheet(f'color: {T.BLUE_INK};')
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setFixedWidth(70)
        sh.addWidget(self.value)
        self.b_plus = flat_button('+', color=T.BLUE_INK, size=15)
        self.b_plus.clicked.connect(lambda: self.source.nudge(0.02))
        sh.addWidget(self.b_plus)
        sh.addStretch(1)
        lay.addWidget(step)

        self.ruler = BaselineRuler()
        lay.addWidget(self.ruler)

        ops = QWidget()
        ol = QHBoxLayout(ops)
        ol.setContentsMargins(0, 10, 0, 0)
        ol.setSpacing(18)
        ol.addStretch(1)
        self.b_fork = flat_button('分叉新起点', color=T.MUTED, size=9)
        self.b_fork.clicked.connect(self.source.fork)
        ol.addWidget(self.b_fork)
        self.b_reset = flat_button('重置为群体先验', color=T.MUTED, size=9)
        self.b_reset.clicked.connect(self.source.reset_baseline)
        ol.addWidget(self.b_reset)
        lay.addWidget(ops)

        lay.addSpacing(14)
        lay.addWidget(rule())
        lay.addSpacing(14)

        lay.addWidget(label('个人模型'))
        lay.addSpacing(2)
        self.rows = {}
        for key, name in (('n_events', '已学习'), ('ell', '惯性 ℓ'),
                          ('stage', '阶段'), ('shrink', '个人化')):
            lay.addWidget(self._row(key, name))

        lay.addSpacing(4)
        learn_row = QWidget()
        ll = QHBoxLayout(learn_row)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(14)
        self.b_learn = flat_button('学习一步', color=T.BLUE_INK, size=11,
                                   weight=QFont.DemiBold)
        self.b_learn.clicked.connect(self.source.learn)
        ll.addWidget(self.b_learn)
        self.learn_hint = QLabel('积累纠正后点击学习')
        self.learn_hint.setFont(T.app_font(9.5))
        self.learn_hint.setStyleSheet(f'color: {T.MUTED};')
        ll.addWidget(self.learn_hint, 1)
        lay.addWidget(learn_row)

        lay.addStretch(1)
        lay.addWidget(rule())
        lay.addSpacing(12)
        lay.addWidget(label('参数说明'))
        lay.addSpacing(4)
        note = QLabel('这些数字是模型对当前状态的估计，不是对你的定义。\n'
                      '惯性 ℓ 越大，情绪回落越慢。')
        note.setFont(T.app_font(9.5))
        note.setStyleSheet(f'color: {T.MUTED};')
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addSpacing(14)

    def _row(self, key, name):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        k = QLabel(name)
        k.setFont(T.app_font(11))
        k.setStyleSheet(f'color: {T.INK_2};')
        h.addWidget(k)
        h.addStretch(1)
        v = QLabel('—')
        v.setFont(T.num_font(14, QFont.Light) if key != 'stage'
                  else T.app_font(13))
        v.setStyleSheet(f'color: {T.INK};')
        h.addWidget(v, 0, Qt.AlignVCenter)
        self.rows[key] = v
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addSpacing(7)
        bl.addWidget(w)
        bl.addSpacing(7)
        bl.addWidget(rule())
        return box

    def sync(self, m):
        self.value.setText('%d' % round(m['baseline_v'] * 100))
        self.ruler.set_value(m['baseline_v'])
        self.rows['n_events'].setText('%d 次' % m['n_events'])
        self.rows['ell'].setText('%d s' % round(m['ell']))
        self.rows['stage'].setText(m['stage'])
        self.rows['shrink'].setText('%d%%' % m['shrink'])
        self.learn_hint.setText(
            '已积累 %d 条纠正' % m['corrections'] if m['corrections']
            else '积累纠正后点击学习')


class DeviationBar(QWidget):
    """水平偏差条：中线 = 你的常态，向右 = 高于常态，向左 = 低于。

    为什么横向：行高只有 40px，竖着画 0–100 的刻度时，25 分的偏差只有 8px，
    几乎看不见。横向 64px 装 ±50 分，同样的偏差有 16px，读得出来。
    这是**列表内的放大显示**，不用于跨行比较绝对值——峰值数字仍在右侧。
    """

    def __init__(self, peak, baseline, parent=None):
        super().__init__(parent)
        self.peak = peak
        self.baseline = baseline
        self.setFixedSize(68, 24)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        span = 26.0        # ±50 分 → ±26px

        p.setPen(QPen(QColor('#CFCFCF'), 1))
        p.drawLine(QPointF(6, cy), QPointF(self.width() - 6, cy))
        pen = QPen(QColor('#9A9A9A'), 1)
        pen.setDashPattern([1, 2])
        p.setPen(pen)
        p.drawLine(QPointF(cx, cy - 9), QPointF(cx, cy + 9))

        dev = max(-0.5, min(0.5, self.peak - self.baseline))
        x = cx + dev / 0.5 * span
        pen = QPen(QColor(T.BLUE), 2.2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx, cy), QPointF(x, cy))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.BLUE))
        p.drawEllipse(QPointF(x, cy), 2.6, 2.6)


class EventRow(QWidget):
    def __init__(self, ev, baseline, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(14)

        t = QLabel('%s–%s' % (ev['t0'], ev['t1']))
        t.setFont(T.num_font(10, QFont.Light))
        t.setStyleSheet(f'color: {T.MUTED};')
        t.setFixedWidth(
            QFontMetrics(t.font()).horizontalAdvance('00:00–00:00') + 10)
        lay.addWidget(t)

        lay.addWidget(DeviationBar(ev['peak'] / 100.0, baseline))

        num = QWidget()
        nl = QVBoxLayout(num)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(0)
        peak = QLabel('%d' % ev['peak'])
        peak.setFont(T.num_font(15, QFont.Light))
        peak.setStyleSheet(f'color: {T.INK};')
        nl.addWidget(peak)
        dev = int(round((ev['peak'] / 100.0 - baseline) * 100))
        dev_lbl = QLabel('持平' if abs(dev) <= 2 else
                         ('高 %d' % dev if dev > 0 else '低 %d' % abs(dev)))
        dev_lbl.setFont(T.app_font(8))
        dev_lbl.setStyleSheet(f'color: {T.MUTED};')
        nl.addWidget(dev_lbl)
        lay.addWidget(num)

        lay.addStretch(1)
        n = QLabel('%d 次自评' % ev['count'])
        n.setFont(T.app_font(9))
        n.setStyleSheet(f'color: {T.MUTED};')
        lay.addWidget(n)


class HistoryPage(QWidget):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._inner = QWidget()
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(T.PAD, 24, T.PAD, 0)
        self._lay.setSpacing(0)
        outer.addWidget(scrolled(self._inner))

    def sync(self, groups, baseline):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not groups:
            self._lay.addWidget(label('还没有记录', size=11, color=T.MUTED))
            self._lay.addStretch(1)
            return
        first = True
        for g in groups:
            if not first:
                self._lay.addSpacing(20)
            first = False
            head = QWidget()
            hl = QHBoxLayout(head)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            hl.addWidget(label(g['label'], size=10, color=T.INK_2, spacing=0.6))
            hl.addStretch(1)
            self._lay.addWidget(head)
            self._lay.addSpacing(4)
            self._lay.addWidget(rule())
            for ev in g['events']:
                self._lay.addWidget(EventRow(ev, baseline))
                self._lay.addWidget(rule())
        self._lay.addStretch(1)


# ================================================================
# 外壳：页签 + 三页 + 全局记录条
# ================================================================
class App(QWidget):
    correction_requested = pyqtSignal()

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self._was_recording = False
        self._history_built = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QuietTabs(['曲线', '模型', '历史'])
        self.tabs.current.connect(self._on_tab)
        root.addWidget(header(self.tabs))
        root.addWidget(rule())

        self.main_page = MainPage(source)
        self.model_page = ModelPage(source)
        self.history_page = HistoryPage(source)
        self.stack = QStackedWidget()
        for w in (self.main_page, self.model_page, self.history_page):
            self.stack.addWidget(w)
        root.addWidget(self.stack, 1)

        # 唤醒滑杆（仅记录态、仅主屏）
        self.arousal_row = QWidget()
        self.arousal_row.setFixedHeight(44)
        al = QHBoxLayout(self.arousal_row)
        al.setContentsMargins(T.PAD, 0, T.PAD, 0)
        al.setSpacing(14)
        al.addWidget(label('唤醒', size=9, color=T.MUTED))
        self.a_slider = QSlider(Qt.Horizontal)
        self.a_slider.setRange(0, 100)
        self.a_slider.setFixedHeight(18)
        self.a_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{ height: 2px; background: {T.RULE}; }}
            QSlider::sub-page:horizontal {{ background: {T.BLUE}; height: 2px; }}
            QSlider::add-page:horizontal {{ background: {T.RULE}; height: 2px; }}
            QSlider::handle:horizontal {{ width: 10px; height: 10px;
                margin: -4px 0; background: {T.BLUE}; border-radius: 5px; }}
        """)
        self.a_slider.valueChanged.connect(self._on_arousal)
        al.addWidget(self.a_slider, 1)
        self.a_value = QLabel('')
        self.a_value.setFont(T.num_font(10, QFont.Light))
        self.a_value.setStyleSheet(f'color: {T.INK_2};')
        self.a_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.a_value.setFixedWidth(
            QFontMetrics(self.a_value.font()).horizontalAdvance('100/100') + 8)
        al.addWidget(self.a_value)
        self.arousal_row.setVisible(False)
        root.addWidget(self.arousal_row)

        # 全局记录条
        self.strip = QWidget()
        self.strip.setObjectName('Strip')
        self.strip.setFixedHeight(64)
        sl = QHBoxLayout(self.strip)
        sl.setContentsMargins(T.PAD, 0, T.PAD, 0)
        sl.setSpacing(14)
        self.act = flat_button('●　开始记录', size=12, weight=QFont.DemiBold)
        self.act.clicked.connect(self._toggle)
        sl.addWidget(self.act)
        sl.addStretch(1)
        self.note = flat_button('记一笔', size=10.5)
        self.note.clicked.connect(self.source.commit_note)
        sl.addWidget(self.note)
        self.count = QLabel('')
        self.count.setFont(T.app_font(9))
        sl.addWidget(self.count)
        root.addWidget(self.strip)

        self.main_page.corr.clicked.connect(self.correction_requested.emit)

        self._timer = QTimer(self)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self.sync)
        self._timer.start()
        self.sync()

    # ------------------------------------------------------------
    def _on_tab(self, idx):
        self.stack.setCurrentIndex(idx)
        if idx == 2:
            self._history_built = False      # 进历史页才重建，避免每 600ms 重建
        self.sync()

    def _toggle(self):
        self.source.toggle_recording()
        self.sync()

    def _on_arousal(self, v):
        self.a_value.setText('%d/100' % v)
        if self.source.p.recording:
            self.source.submit_input(arousal=v / 100.0)

    def sync(self):
        s = self.source.snapshot()
        recording = s['recording']
        on_main = self.stack.currentIndex() == 0

        self.main_page.sync(s)

        if not self._history_built:
            self.history_page.sync(self.source.history(), s['baseline_v'])
            self._history_built = True
        self.model_page.sync(self.source.model_snapshot())

        self.arousal_row.setVisible(recording and on_main)
        if recording and not self._was_recording:
            self.a_slider.blockSignals(True)
            self.a_slider.setValue(round(s['a'] * 100))
            self.a_value.setText('%d/100' % round(s['a'] * 100))
            self.a_slider.blockSignals(False)
        self._was_recording = recording

        self._style_strip(recording, on_main)
        self.count.setText('%d 次' % s['count'])

    def _style_strip(self, recording, on_main):
        if recording:
            self.strip.setStyleSheet(
                f'QWidget#Strip {{ background: {T.BLUE_DEEP}; }}')
            self.act.setText('■　停止记录')
            self.act.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {T.SURFACE}; font-size: 12px; font-weight: 600;'
                f' padding: 0; }}')
            self.note.setVisible(True)
            self.note.setEnabled(True)
            self.note.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {T.SURFACE}; font-size: 10.5px; padding: 0; }}')
            self.count.setStyleSheet(f'color: {T.BLUE_SOFT};')
        else:
            self.strip.setStyleSheet(
                f'QWidget#Strip {{ background: {T.PAPER};'
                f' border-top: 1px solid {T.RULE}; }}')
            self.act.setText('●　开始记录')
            self.act.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {T.BLUE_INK}; font-size: 12px; font-weight: 600;'
                f' letter-spacing: 0.6px; padding: 0; }}')
            self.note.setVisible(False)
            self.count.setStyleSheet(f'color: {T.MUTED};')


def _clock(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%H:%M')
