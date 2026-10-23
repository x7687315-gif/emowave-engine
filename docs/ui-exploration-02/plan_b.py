"""plan_b.py — 方案 B「水位」

结构主张：**状态是一个位置，不是四个数字。**
  1. 左侧一条 0–100 的垂直刻度；当前效价画成一条横贯画面的"水位线"。
     你是"高出常态 12"，不是"效价 60"——围绕的是**离差**，不是绝对分。
  2. 曲线不再是独立图表，而是同一根刻度上的时间轨迹：填充轮廓 + 细实线。
     水位线与轨迹末端相接，所以"现在在哪"和"怎么走过来的"是同一眼看到的。
  3. 记录态把垂直轴变成输入：直接拖动水位线设定效价；唤醒用底部一条细滑杆。
  4. 底部是一条通栏控制带；记录时整条反相（黑底白字）——用**明度反转**表示
     "正在记录"，不引入任何颜色。

坐标（400×680）：
    52   页头（页签 + 字标）
    53   1px 线
    80   状态句 21px
   106   离差行 12px
   132   测量区（弹性，≈330px；左轴 30px + 轨迹）
   500   读数行 9.5px
   600   唤醒滑杆（仅记录态）
   664   通栏控制带 64px
"""
from PyQt5.QtCore import QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

import math

import curve_ink as ink
import gray_tokens as G


# ================================================================
# 测量区：一根刻度 + 轮廓 + 水位线
# ================================================================
class WaterlinePlot(QWidget):
    value_dragged = pyqtSignal(float)

    AXIS_X = G.PAD + 4       # 刻度线与页面文字左缘对齐（略进 4px，给数字让位）
    GUTTER = G.PAD + 30      # 刻度线到轨迹区左界的距离

    def __init__(self, parent=None):
        super().__init__(parent)
        self.payload = None
        self.current_v = 0.5
        self.recording = False
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._dragging = False

    def set_state(self, payload, current_v, recording):
        self.payload = payload
        self.current_v = float(current_v)
        self.recording = bool(recording)
        self.setCursor(Qt.SizeVerCursor if recording else Qt.ArrowCursor)
        self.update()

    # ------------------------------------------------------------
    def _geo(self):
        # 右端留 6px：轨迹出血到窗口边缘，但最后一个时间标签不能被裁
        return ink.Geo(self.payload, self.GUTTER, 14,
                       self.width() - 6, self.height() - 26)

    def paintEvent(self, _e):
        if not self.payload:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        geo = self._geo()

        self._draw_axis(p, geo)
        self._draw_silhouette(p, geo)
        # 不确定性用两条 ±1σ 虚线包络，而不是再叠一层灰面：
        # 这一屏已经有一块"偏差面积"，两层半透明灰叠在一起会互相吞掉，
        # 用户分不清"哪块是我偏离了、哪块是模型不确定"。
        self._draw_band_edges(p, geo)
        ink.draw_baseline(p, self.payload, geo, label='', right=True)
        ink.draw_estimate(p, self.payload, geo,
                          solid=G.INK, dashed=G.MUTED, width=1.2)
        ink.draw_observations(p, self.payload, geo, r=2.8, color=G.INK_2)
        ink.draw_corrections(p, self.payload, geo)
        ink.draw_time_labels(p, geo, count=3, dy=15)
        self._draw_waterline(p, geo)
        p.end()

    def _draw_band_edges(self, p, geo):
        ts = self.payload.get('ts') or []
        mv = self.payload.get('mv') or []
        vv = self.payload.get('vv') or []
        if len(mv) < 2:
            return
        pen = QPen(QColor(G.FAINT), 1)
        pen.setDashPattern([1, 3])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for sign in (1, -1):
            path = QPainterPath()
            for i, (t, m, var) in enumerate(zip(ts, mv, vv)):
                sd = min(0.45, math.sqrt(max(0.0, var)))
                pt = QPointF(geo.x(t), geo.y(m + sign * sd))
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            p.drawPath(path)

    def _draw_axis(self, p, geo):
        x = self.AXIS_X
        pen = QPen(QColor(G.RULE), 1)
        p.setPen(pen)
        p.drawLine(QPointF(x, geo.y0), QPointF(x, geo.y1))

        p.setFont(G.app_font(8))
        fm = p.fontMetrics()
        # 只标两端。中间那个位置留给"常态"——它是这根轴上唯一有个人意义的刻度。
        for value in (1.0, 0.0):
            y = geo.y(value)
            p.setPen(QPen(QColor(G.RULE), 1))
            p.drawLine(QPointF(x - 3, y), QPointF(x, y))
            p.setPen(QColor(G.MUTED))
            text = '%d' % round(value * 100)
            p.drawText(QPointF(x - 7 - fm.horizontalAdvance(text), y + 3), text)

        baseline = self.payload.get('baseline_v')
        if baseline is not None:
            yb = geo.y(baseline)
            p.setPen(QPen(QColor(G.MUTED), 1))
            p.drawLine(QPointF(x - 4, yb), QPointF(x + 3, yb))
            p.setPen(QColor(G.MUTED))
            text = '常态'
            p.drawText(QPointF(x - 7 - fm.horizontalAdvance(text), yb + 3), text)

    def _draw_silhouette(self, p, geo):
        """填充"与常态的偏差面积"——不是填到 0，而是填到基线。

        这样灰面的高度就等于"离你平时有多远"，而且是**有符号**的：
        曲线在基线之上是一块，在之下是另一块。底色面积也因此小得多，
        不会像填到 0 那样变成一大块灰色背景。
        """
        ts = self.payload.get('ts') or []
        mv = self.payload.get('mv') or []
        baseline = self.payload.get('baseline_v')
        if len(mv) < 2 or baseline is None:
            return
        yb = geo.y(baseline)
        path = QPainterPath()
        path.moveTo(QPointF(geo.x(ts[0]), yb))
        for t, m in zip(ts, mv):
            path.lineTo(QPointF(geo.x(t), geo.y(m)))
        path.lineTo(QPointF(geo.x(ts[-1]), yb))
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(G.SILHOUETTE)))
        p.drawPath(path)

    def _draw_waterline(self, p, geo):
        y = geo.y(self.current_v)
        # 水位线是主角：2px 实线，从刻度一直拉到右边
        pen = QPen(QColor(G.INK), 2)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawLine(QPointF(self.AXIS_X, y), QPointF(self.width() - 6, y))

        # 刻度上的位置点
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(G.INK))
        p.drawEllipse(QPointF(self.AXIS_X, y), 3.2, 3.2)

        # 数值贴在轴线右侧、水位线上方
        p.setFont(G.num_font(12, QFont.Light))
        p.setPen(QColor(G.INK))
        p.drawText(QPointF(self.GUTTER + 8, y - 7),
                   '%d' % round(self.current_v * 100))

    # ------------------------------------------------------------
    def mousePressEvent(self, e):
        if self.recording and e.button() == Qt.LeftButton:
            self._dragging = True
            self._emit_from(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._emit_from(e)

    def mouseReleaseEvent(self, _e):
        self._dragging = False

    def _emit_from(self, e):
        geo = self._geo()
        v = 1.0 - (e.y() - geo.y0) / max(1.0, (geo.y1 - geo.y0))
        self.value_dragged.emit(min(1.0, max(0.0, v)))


# ================================================================
# 页签：靠字重与位置区分，不用下划线
# ================================================================
class QuietTabs(QWidget):
    current = pyqtSignal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)
        self._btns = []
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            b.setFocusPolicy(Qt.NoFocus)
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
            b.setFont(G.app_font(10, QFont.DemiBold if active else QFont.Normal))
            b.setStyleSheet(
                'QPushButton { border: none; background: transparent;'
                ' color: %s; padding: 0; }'
                % (G.INK if active else G.MUTED))


# ================================================================
# 方案 B
# ================================================================
class PlanB(QWidget):
    correction_requested = pyqtSignal()

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self._snap = source.snapshot()
        self._was_recording = False
        self._build()
        self._refresh()

        self._timer = QTimer(self)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 页头 ----
        head = QWidget()
        head.setFixedHeight(52)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(G.PAD, 0, G.PAD, 0)
        hl.setSpacing(0)
        self.tabs = QuietTabs(['曲线', '模型', '历史'])
        self.tabs.current.connect(self._on_tab)
        hl.addWidget(self.tabs, 1)
        mark = QLabel('心潮')
        mark.setFont(G.app_font(9, QFont.DemiBold))
        mark.setStyleSheet(f'color: {G.MUTED}; letter-spacing: 2.5px;')
        hl.addWidget(mark)
        root.addWidget(head)
        root.addWidget(self._rule())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_main())
        self.stack.addWidget(self._placeholder('模型'))
        self.stack.addWidget(self._placeholder('历史'))
        root.addWidget(self.stack, 1)

        # ---- 唤醒滑杆（仅记录态）----
        self.arousal_row = QWidget()
        self.arousal_row.setFixedHeight(44)
        al = QHBoxLayout(self.arousal_row)
        al.setContentsMargins(G.PAD, 0, G.PAD, 0)
        al.setSpacing(14)
        name = QLabel('唤醒')
        name.setFont(G.app_font(9))
        name.setStyleSheet(f'color: {G.MUTED};')
        al.addWidget(name)
        self.a_slider = QSlider(Qt.Horizontal)
        self.a_slider.setRange(0, 100)
        self.a_slider.setFixedHeight(18)
        self.a_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{ height: 2px; background: {G.RULE}; }}
            QSlider::sub-page:horizontal {{ background: {G.INK}; height: 2px; }}
            QSlider::add-page:horizontal {{ background: {G.RULE}; height: 2px; }}
            QSlider::handle:horizontal {{ width: 10px; height: 10px;
                margin: -4px 0; background: {G.INK}; border-radius: 5px; }}
        """)
        self.a_slider.valueChanged.connect(self._on_arousal)
        al.addWidget(self.a_slider, 1)
        self.a_value = QLabel('')
        self.a_value.setFont(G.num_font(10, QFont.Light))
        self.a_value.setStyleSheet(f'color: {G.INK_2};')
        # 按实际字体量 "100/100"：固定宽度在不同字体回退下会把首位数字裁掉
        from PyQt5.QtGui import QFontMetrics
        self.a_value.setFixedWidth(
            QFontMetrics(self.a_value.font()).horizontalAdvance('100/100') + 8)
        self.a_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        al.addWidget(self.a_value)
        self.arousal_row.setVisible(False)
        root.addWidget(self.arousal_row)

        # ---- 通栏控制带 ----
        self.strip = QWidget()
        self.strip.setObjectName('Strip')
        self.strip.setFixedHeight(64)
        sl = QHBoxLayout(self.strip)
        sl.setContentsMargins(G.PAD, 0, G.PAD, 0)
        sl.setSpacing(14)
        self.act = QPushButton('●　开始记录')
        self.act.setCursor(Qt.PointingHandCursor)
        self.act.setFlat(True)
        self.act.setFocusPolicy(Qt.NoFocus)
        self.act.clicked.connect(self._toggle)
        sl.addWidget(self.act)
        sl.addStretch(1)
        self.note = QPushButton('记一笔')
        self.note.setCursor(Qt.PointingHandCursor)
        self.note.setFlat(True)
        self.note.setFocusPolicy(Qt.NoFocus)
        self.note.clicked.connect(self.source.commit_note)
        sl.addWidget(self.note)
        self.count = QLabel('')
        self.count.setFont(G.app_font(9))
        sl.addWidget(self.count)
        root.addWidget(self.strip)

    def _placeholder(self, name):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(G.PAD, 70, G.PAD, 40)
        t = QLabel('%s 页沿用现有实现\n本轮只重塑首屏结构与层级' % name)
        t.setFont(G.app_font(10))
        t.setStyleSheet(f'color: {G.MUTED};')
        t.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(t)
        lay.addStretch(1)
        return w

    def _build_main(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 文字区（带左右留白）
        text = QWidget()
        tl = QVBoxLayout(text)
        tl.setContentsMargins(G.PAD, 26, G.PAD, 0)
        tl.setSpacing(0)

        self.state = QLabel('—')
        self.state.setFont(G.app_font(21, QFont.Medium))
        self.state.setStyleSheet(f'color: {G.INK};')
        tl.addWidget(self.state)

        tl.addSpacing(5)

        dev_row = QWidget()
        dl = QHBoxLayout(dev_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)
        self.dev = QLabel('')
        self.dev.setFont(G.app_font(11))
        self.dev.setStyleSheet(f'color: {G.INK_2};')
        dl.addWidget(self.dev)
        dl.addStretch(1)
        self.corr = QPushButton('纠正')
        self.corr.setCursor(Qt.PointingHandCursor)
        self.corr.setFlat(True)
        self.corr.setFocusPolicy(Qt.NoFocus)
        self.corr.setStyleSheet(
            f'QPushButton {{ border: none; background: transparent;'
            f' color: {G.MUTED}; text-decoration: underline;'
            f' font-size: 9px; padding: 0; }}'
            f'QPushButton:hover {{ color: {G.INK}; }}')
        self.corr.clicked.connect(self.correction_requested.emit)
        dl.addWidget(self.corr)
        tl.addWidget(dev_row)

        tl.addSpacing(12)
        lay.addWidget(text)

        # 测量区（刻度贴左，轨迹出血到右边）
        self.plot = WaterlinePlot()
        self.plot.value_dragged.connect(self._on_waterline)
        lay.addWidget(self.plot, 1)

        # 读数行
        meta = QWidget()
        ml = QHBoxLayout(meta)
        ml.setContentsMargins(G.PAD, 8, G.PAD, 10)
        ml.setSpacing(10)
        self.readouts = QLabel('')
        self.readouts.setWordWrap(True)
        self.readouts.setFont(G.app_font(9.5))
        self.readouts.setStyleSheet(f'color: {G.MUTED};')
        # 注意：wordWrap 的 QLabel 会返回一个很窄的 sizeHint，
        # 后面再接 addStretch 的话它永远只拿到最小宽度 → 无谓折行。
        # 所以让标签自己吃掉剩余宽度。
        ml.addWidget(self.readouts, 1)
        self.baseline_lbl = QLabel('')
        self.baseline_lbl.setFont(G.app_font(9))
        self.baseline_lbl.setStyleSheet(f'color: {G.MUTED};')
        ml.addWidget(self.baseline_lbl)
        lay.addWidget(meta)
        return page

    def _rule(self):
        ln = QFrame()
        ln.setFixedHeight(G.HAIRLINE)
        ln.setStyleSheet(f'background: {G.RULE}; border: none;')
        return ln

    # ------------------------------------------------------------
    def _on_tab(self, idx):
        self.stack.setCurrentIndex(idx)

    def _on_arousal(self, v):
        self.a_value.setText('%d/100' % v)
        if self._snap['recording']:
            self.source.submit_input(arousal=v / 100.0)

    def _on_waterline(self, value):
        self.plot.current_v = value
        self.plot.update()
        self.source.submit_input(valence=value)

    def _toggle(self):
        self.source.toggle_recording()
        self._refresh()

    def sync(self):
        self._refresh()

    # ------------------------------------------------------------
    def _refresh(self):
        s = self._snap = self.source.snapshot()
        self.state.setText(s['state_name'])

        d = s['deviation']
        if abs(d) <= 2:
            dev_text = '与你的常态持平'
        elif d > 0:
            dev_text = '比你的常态高 %d' % d
        else:
            dev_text = '比你的常态低 %d' % abs(d)
        if s['recording']:
            dev_text = '正在记录 · ' + dev_text
        else:
            dev_text += self._forecast_note(s['curve'])
        self.dev.setText(dev_text)
        self.baseline_lbl.setText('估 %s' % _clock(s['state_ts']))

        self.readouts.setText(
            '效价 %d · 唤醒 %d · 置信 %d%% · ±1σ %s' % (
                round(s['v'] * 100), round(s['a'] * 100),
                round(s['conf'] * 100), s['ci']))

        self.plot.set_state(s['curve'], s['v'], s['recording'])

        self.arousal_row.setVisible(s['recording'])
        if s['recording'] and not self._was_recording:
            self.a_slider.blockSignals(True)
            self.a_slider.setValue(round(s['a'] * 100))
            self.a_value.setText('%d/100' % round(s['a'] * 100))
            self.a_slider.blockSignals(False)
        self._was_recording = s['recording']

        self._style_strip(s)
        self.count.setText('%d 次' % s['count'])

    @staticmethod
    def _forecast_note(payload):
        minutes = ink.forecast_minutes(payload or {})
        return '　·　预测 %d 分钟' % minutes if minutes else ''

    def _style_strip(self, s):
        if s['recording']:
            self.strip.setStyleSheet(
                f'QWidget#Strip {{ background: {G.INK}; }}')
            self.act.setText('■　停止记录')
            self.act.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {G.PAPER}; font-size: 12px; font-weight: 600;'
                f' letter-spacing: 1px; padding: 0; }}')
            self.note.setVisible(True)
            self.note.setEnabled(True)
            self.note.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {G.PAPER}; font-size: 10.5px; padding: 0; }}')
            self.count.setStyleSheet(f'color: {G.RULE};')
        else:
            self.strip.setStyleSheet(
                f'QWidget#Strip {{ background: {G.PAPER};'
                f' border-top: 1px solid {G.RULE}; }}')
            self.act.setText('●　开始记录')
            self.act.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {G.INK}; font-size: 12px; font-weight: 600;'
                f' letter-spacing: 1px; padding: 0; }}')
            self.note.setVisible(False)
            self.count.setStyleSheet(f'color: {G.MUTED};')


def _clock(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%H:%M')
