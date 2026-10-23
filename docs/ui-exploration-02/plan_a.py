"""plan_a.py — 方案 A「地平线」

结构主张：**状态是一句话，曲线是地平线。**
  1. 首屏只有一个主角：31px 的状态句。四个读数全部降级成一行注脚。
  2. 曲线去掉卡片、图例、Y 轴、标题栏，只剩一条通栏的轨迹 + 一条"常态"地平线。
  3. 滑块不常驻。空闲时底部只有一行「开始记录」；进入记录态才就地展开两个滑杆。
  4. 分隔只用两条 1px 线（页头下、操作区上），其余靠留白。

坐标（400×680）：
    52   页头（字标 + 页签）
    53   1px 线
    87   状态句 31px
   126   说明行 10px
   156   读数注脚 10px
   196   曲线（弹性，≈300px，无框）
   600   1px 线
   664   操作行 64px
"""
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontMetrics, QPainter
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

import curve_ink as ink
import gray_tokens as G


# ================================================================
# 曲线：没有框的地平线
# ================================================================
class HorizonCurve(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.payload = None
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def set_payload(self, payload):
        self.payload = payload
        self.update()

    def paintEvent(self, _e):
        if not self.payload:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        geo = ink.Geo(self.payload, 0, 12, self.width() - 1, self.height() - 26)

        ink.draw_band(p, self.payload, geo, alpha=120)
        ink.draw_baseline(p, self.payload, geo, label='常态', right=True)
        ink.draw_estimate(p, self.payload, geo)
        ink.draw_observations(p, self.payload, geo, color=G.INK_2)
        ink.draw_corrections(p, self.payload, geo)
        ink.draw_time_labels(p, geo, count=3, dy=15)
        p.end()


# ================================================================
# 滑杆行（只在记录态出现）
# ================================================================
class ThinSlider(QWidget):
    changed = pyqtSignal(int)

    def __init__(self, key, value=50, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        name = QLabel(key)
        name.setFont(G.app_font(9))
        name.setStyleSheet(f'color: {G.MUTED};')
        name.setFixedWidth(28)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(value)
        self.slider.setFixedHeight(18)
        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{ height: 2px; background: {G.RULE};
                border-radius: 1px; }}
            QSlider::sub-page:horizontal {{ background: {G.INK}; height: 2px;
                border-radius: 1px; }}
            QSlider::add-page:horizontal {{ background: {G.RULE}; height: 2px;
                border-radius: 1px; }}
            QSlider::handle:horizontal {{ width: 10px; height: 10px;
                margin: -4px 0; background: {G.INK}; border-radius: 5px; }}
        """)

        self.value = QLabel('%d/100' % value)
        self.value.setFont(G.num_font(10, QFont.Light))
        self.value.setStyleSheet(f'color: {G.INK_2};')
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value.setFixedWidth(
            QFontMetrics(self.value.font()).horizontalAdvance('100/100') + 6)

        lay.addWidget(name)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value)

        self.slider.valueChanged.connect(self._on_change)

    def _on_change(self, v):
        self.value.setText('%d/100' % v)
        self.changed.emit(v)

    def set_value(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(v))
        self.value.setText('%d/100' % int(v))
        self.slider.blockSignals(False)


# ================================================================
# 页签
# ================================================================
class QuietTabs(QWidget):
    current = pyqtSignal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(20)
        lay.addStretch(1)
        self._btns = []
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setStyleSheet('QPushButton { border: none; background: transparent; }')
            b.clicked.connect(lambda _=False, idx=i: self.set_current(idx))
            lay.addWidget(b)
            self._btns.append(b)
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
                ' color: %s; padding: 0px; }'
                % (G.INK if active else G.MUTED))


# ================================================================
# 方案 A
# ================================================================
class PlanA(QWidget):
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
        mark = QLabel('心潮')
        mark.setFont(G.app_font(10, QFont.DemiBold))
        mark.setStyleSheet(f'color: {G.INK}; letter-spacing: 3px;')
        hl.addWidget(mark)
        self.tabs = QuietTabs(['曲线', '模型', '历史'])
        self.tabs.current.connect(self._on_tab)
        hl.addWidget(self.tabs, 1)
        root.addWidget(head)
        root.addWidget(self._rule())

        # ---- 三页 ----
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_main())
        self.stack.addWidget(self._placeholder('模型'))
        self.stack.addWidget(self._placeholder('历史'))
        root.addWidget(self.stack, 1)

        # ---- 底部操作区 ----
        self.bottom_rule = self._rule()
        root.addWidget(self.bottom_rule)

        self.bottom = QWidget()
        bl = QVBoxLayout(self.bottom)
        bl.setContentsMargins(G.PAD, 0, G.PAD, 0)
        bl.setSpacing(0)

        # 记录态才出现的两个滑杆
        self.sliders = QWidget()
        sl = QVBoxLayout(self.sliders)
        sl.setContentsMargins(0, 10, 0, 0)
        sl.setSpacing(4)
        self.v_slider = ThinSlider('效价')
        self.a_slider = ThinSlider('唤醒')
        self.v_slider.changed.connect(self._on_slider)
        self.a_slider.changed.connect(self._on_slider)
        sl.addWidget(self.v_slider)
        sl.addWidget(self.a_slider)
        self.sliders.setVisible(False)
        bl.addWidget(self.sliders)

        row = QWidget()
        row.setFixedHeight(64)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(16)

        self.act = QPushButton('●　开始记录')
        self.act.setCursor(Qt.PointingHandCursor)
        self.act.setFlat(True)
        self.act.setFocusPolicy(Qt.NoFocus)
        self.act.clicked.connect(self._toggle)
        rl.addWidget(self.act)

        self.note = QPushButton('记一笔')
        self.note.setCursor(Qt.PointingHandCursor)
        self.note.setFlat(True)
        self.note.setFocusPolicy(Qt.NoFocus)
        self.note.clicked.connect(self.source.commit_note)
        rl.addWidget(self.note)

        rl.addStretch(1)

        self.count = QLabel('')
        self.count.setFont(G.app_font(9))
        self.count.setStyleSheet(f'color: {G.MUTED};')
        rl.addWidget(self.count)

        bl.addWidget(row)
        root.addWidget(self.bottom)

    def _placeholder(self, name):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(G.PAD, 60, G.PAD, 60)
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
        lay.setContentsMargins(G.PAD, 0, G.PAD, 0)
        lay.setSpacing(0)

        lay.addSpacing(34)

        # ---- 主角：状态句 ----
        self.state = QLabel('—')
        self.state.setFont(G.app_font(30, QFont.Medium))
        self.state.setStyleSheet(f'color: {G.INK};')
        lay.addWidget(self.state)

        lay.addSpacing(8)

        # ---- 说明行：这数是几点的 + 纠正入口 ----
        meta_row = QWidget()
        ml = QHBoxLayout(meta_row)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(10)
        self.meta = QLabel('')
        self.meta.setFont(G.app_font(10))
        self.meta.setStyleSheet(f'color: {G.MUTED};')
        ml.addWidget(self.meta)
        ml.addStretch(1)
        self.corr = QPushButton('纠正')
        self.corr.setCursor(Qt.PointingHandCursor)
        self.corr.setFlat(True)
        self.corr.setFocusPolicy(Qt.NoFocus)
        self.corr.setStyleSheet(
            f'QPushButton {{ border: none; background: transparent;'
            f' color: {G.MUTED}; text-decoration: underline;'
            f' font-size: 9.5px; padding: 0; }}'
            f'QPushButton:hover {{ color: {G.INK}; }}')
        self.corr.clicked.connect(self.correction_requested.emit)
        ml.addWidget(self.corr)
        lay.addWidget(meta_row)

        lay.addSpacing(14)

        # ---- 读数注脚：一行，全部降级 ----
        self.readouts = QLabel('')
        self.readouts.setWordWrap(True)      # 360px 下允许折成两行，不裁字
        self.readouts.setFont(G.app_font(10))
        self.readouts.setStyleSheet(f'color: {G.INK_2};')
        lay.addWidget(self.readouts)

        lay.addSpacing(26)

        # ---- 地平线 ----
        self.curve = HorizonCurve()
        lay.addWidget(self.curve, 1)

        lay.addSpacing(8)
        return page

    def _rule(self):
        ln = QFrame()
        ln.setFixedHeight(G.HAIRLINE)
        ln.setStyleSheet(f'background: {G.RULE}; border: none;')
        return ln

    # ------------------------------------------------------------
    def _on_tab(self, idx):
        self.stack.setCurrentIndex(idx)

    def _on_slider(self, _v):
        """记录态下拖动 = 真实自评（走 presenter 的防抖结算），与生产一致。"""
        self.source.submit_input(
            valence=self.v_slider.slider.value() / 100.0,
            arousal=self.a_slider.slider.value() / 100.0)

    def _toggle(self):
        self.source.toggle_recording()
        self._refresh()

    def sync(self):
        """外部（截图脚本 / 快捷键）改变状态后，让视图立刻重读一次。"""
        self._refresh()

    # ------------------------------------------------------------
    def _refresh(self):
        s = self._snap = self.source.snapshot()
        self.state.setText(s['state_name'])

        if s['recording']:
            tail = '正在记录 · 已记 %d 次' % s['count']
        elif s['age_min'] <= 0:
            tail = '估计 %s · 刚刚自评' % _clock(s['state_ts'])
        else:
            tail = '估计 %s · 自评 %d 分钟前' % (
                _clock(s['state_ts']), s['age_min'])
        # 预测时长属于"这数是什么、有多可信"的语境，放在说明行；
        # 读数行只保留真的数字，避免 400px 下折成两行。
        self.meta.setText(tail + self._forecast_note(s['curve']))
        self.readouts.setText(
            '效价 %d　唤醒 %d　置信 %d%%　区间 %s' % (
                round(s['v'] * 100), round(s['a'] * 100),
                round(s['conf'] * 100), s['ci']))

        self.curve.set_payload(s['curve'])

        self.sliders.setVisible(s['recording'])
        self.note.setVisible(s['recording'])
        self._style_actions(s)
        self.count.setText('%d 次自评' % s['count'])

        # 只在"刚进入记录态"时把滑杆对齐到当前估计；之后不再回写，
        # 否则每 600ms 刷新会把用户正在拖的手柄弹回去。
        if s['recording'] and not self._was_recording:
            self.v_slider.set_value(round(s['v'] * 100))
            self.a_slider.set_value(round(s['a'] * 100))
        self._was_recording = s['recording']

    @staticmethod
    def _forecast_note(payload):
        """虚线到底代表多久——用文字说清，不指望 2% 宽度的那几个像素。"""
        minutes = ink.forecast_minutes(payload or {})
        return ' · 预测 %d 分钟' % minutes if minutes else ''

    def _style_actions(self, s):
        if s['recording']:
            self.act.setText('■　停止记录')
            self.act.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {G.INK}; font-size: 12px; font-weight: 600;'
                f' letter-spacing: 1px; padding: 0; }}')
            self.note.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {G.INK_2}; font-size: 11px; padding: 0; }}'
                f'QPushButton:hover {{ color: {G.INK}; }}')
            self.note.setEnabled(True)
        else:
            self.act.setText('●　开始记录')
            self.act.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {G.INK}; font-size: 12px; font-weight: 600;'
                f' letter-spacing: 1px; padding: 0; }}')
            self.note.setStyleSheet(
                f'QPushButton {{ border: none; background: transparent;'
                f' color: {G.FAINT}; font-size: 11px; padding: 0; }}')
            self.note.setEnabled(False)


def _clock(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%H:%M')
