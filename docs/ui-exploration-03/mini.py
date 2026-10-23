"""mini.py — 悬浮窗：主屏那套语言在 360px 上的浓缩版

不是主屏缩小版：
  · 只留三样东西——状态句、当前水位、曲线。读数与滑杆按需出现。
  · 常态在窗内不写字，改用**同色的点线**（蓝色、虚线）与水位线（蓝色、实线）配对：
    一眼就能看出"我现在在哪、我的常态在哪"。
  · 收起 360×134 / 展开 360×180（与生产同尺寸），展开才显示置信/区间/时间刻度。
"""
from PyQt5.QtCore import QPointF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

import softcurve as S
import tokens as T
from views import flat_button, label

WIN_W = 360
H_COLLAPSED = 134
H_EXPANDED = 180


class MiniPlot(QWidget):
    AXIS_X = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.payload = None
        self.value = 0.5
        self.compact = True
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_state(self, payload, value, compact=True):
        self.payload = payload
        self.value = float(value)
        self.compact = compact
        self.update()

    def paintEvent(self, _e):
        if not self.payload:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bottom = 6 if self.compact else 20
        # 悬浮窗没有 0–100 标签，纵轴按当段自适应，只用来读"形状"。
        # 绝对位置由右侧数字与主屏的固定 0–100 轴提供，这里不做任何数据修改。
        mv = self.payload.get('mv') or []
        base = self.payload.get('baseline_v')
        vals = list(mv) + ([base] if base is not None else []) + [self.value]
        if vals:
            lo, hi = min(vals) - 0.06, max(vals) + 0.06
            if hi - lo < 0.28:
                mid = (hi + lo) / 2
                lo, hi = mid - 0.14, mid + 0.14
            geo = S.Geo(self.payload, self.AXIS_X, 4,
                        self.width() - 2, self.height() - bottom,
                        y_range=(max(0.0, lo), min(1.0, hi)))
        else:
            geo = S.Geo(self.payload, self.AXIS_X, 4,
                        self.width() - 2, self.height() - bottom)

        S.draw_band(p, self.payload, geo)
        # 常态：同色点线、不写字——与水位线配对，靠虚实区分
        S.draw_baseline(p, self.payload, geo, self.AXIS_X,
                        color=QColor(58, 106, 136, 120), label=False)
        S.draw_estimate(p, self.payload, geo)
        S.draw_observations(p, self.payload, geo)
        S.draw_corrections(p, self.payload, geo)
        if not self.compact:
            S.draw_time_labels(p, geo, count=2)
        S.draw_waterline(p, self.payload, geo, self.value, self.AXIS_X)
        p.end()


class MiniWindow(QWidget):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self.expanded = False
        self.setFixedSize(WIN_W, H_COLLAPSED)
        self.setObjectName('Mini')

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(0)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        self.state = QLabel('—')
        self.state.setFont(T.app_font(14, QFont.Medium))
        self.state.setStyleSheet(f'color: {T.INK};')
        hl.addWidget(self.state)
        hl.addStretch(1)
        self.value = QLabel('—')
        self.value.setFont(T.num_font(18, QFont.Light))
        self.value.setStyleSheet(f'color: {T.BLUE_INK};')
        hl.addWidget(self.value)
        root.addWidget(head)
        root.addSpacing(2)

        self.plot = MiniPlot()
        root.addWidget(self.plot, 1)

        self.readouts = QLabel('')
        self.readouts.setFont(T.app_font(9))
        self.readouts.setStyleSheet(f'color: {T.MUTED};')
        self.readouts.setVisible(False)
        root.addWidget(self.readouts)

        root.addSpacing(4)
        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(10)
        self.dev = QLabel('')
        self.dev.setFont(T.app_font(9))
        self.dev.setStyleSheet(f'color: {T.MUTED};')
        fl.addWidget(self.dev)
        fl.addStretch(1)
        self.act = flat_button('●　记录', color=T.BLUE_INK, size=9.5,
                               weight=QFont.DemiBold)
        self.act.clicked.connect(self.source.toggle_recording)
        fl.addWidget(self.act)
        root.addWidget(foot)

        self.setStyleSheet(
            f'QWidget#Mini {{ background: {T.SURFACE};'
            f' border: 1px solid {T.RULE}; }}')
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._timer = QTimer(self)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self.sync)
        self._timer.start()
        self.sync()

    # ------------------------------------------------------------
    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        self.readouts.setVisible(self.expanded)
        self.setFixedSize(WIN_W, H_EXPANDED if self.expanded else H_COLLAPSED)
        self.sync()

    def sync(self):
        s = self.source.snapshot()
        self.state.setText(s['state_name'])
        self.value.setText('%d' % round(s['v'] * 100))

        d = s['deviation']
        if abs(d) <= 2:
            dev = '与常态持平'
        elif d > 0:
            dev = '比常态高 %d' % d
        else:
            dev = '比常态低 %d' % abs(d)
        if s['recording']:
            self.dev.setText('正在记录 · 已记 %d 次' % s['count'])
            self.act.setText('■　停止')
        else:
            self.dev.setText(dev)
            self.act.setText('●　记录')
        self.readouts.setText(
            '唤醒 %d　置信 %d%%　±1σ %s　估 %s' % (
                round(s['a'] * 100), round(s['conf'] * 100), s['ci'],
                _clock(s['state_ts'])))
        self.plot.set_state(s['curve'], s['v'], compact=not self.expanded)


def _clock(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%H:%M')
