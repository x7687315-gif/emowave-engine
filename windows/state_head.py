"""windows/state_head.py — 状态头（状态名 + 读数，合并自 StatusCard + ReadoutBar）

=== 为什么要合并 ===
v3 的「状态卡」(StatusCard) 与「读数条」(ReadoutBar) 是两个独立控件，各自
22px 大字号 + 四周 22px 边距，合计约 160px 高。在 400×680 的小窗里，光这两块
就吃掉 1/4 高度，曲线反而被挤成 220px。

合并后是一条 ~68px 的状态头：

    ┌───────────────────────────────────────────────┐
    │ 平静 · 偏积极   · 模型估计（不替你定义）  [· 情绪有误] │  ← row1
    │ 效价 55   唤醒 42    置信 99%   区间 48 – 50          │  ← row2
    └───────────────────────────────────────────────┘

=== 兼容契约（v3 属性名全部保留）===
MainConsole 与既有测试按这些名字访问，一个都不能少：
  · state_label / state_hint / corr_btn
  · v_lbl / a_lbl / c_lbl / ci_lbl —— 每个都是带 `.value_label` 的容器
  · update_state(valence, arousal, params_text)   ← 原 StatusCard 入口
  · update_values(valence, arousal, confidence, ci_text)  ← 原 ReadoutBar 入口

参数说明（原来的 220px 折叠面板）**移出小窗**，进「模型」Tab 的 ParamsExplainCard。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QPushButton, QFrame)
from PyQt5.QtGui import QFont, QFontMetrics

from theme import COLORS, METRICS, app_font, app_font_num


class _Value:
    """一个读数格：极小标签 + 数值。保留 v3 的 `.value_label` 访问方式。"""

    def __init__(self, caption, size=10, color=None):
        self.caption = QLabel(caption)
        self.caption.setFont(app_font(9))
        self.caption.setStyleSheet(f"color: {COLORS['muted']};")
        self.value_label = QLabel("—")
        self.value_label.setFont(app_font_num(size, QFont.Light))
        self.value_label.setStyleSheet(
            f"color: {color or COLORS['ink_2']};")


class StateHead(QWidget):
    """状态头：一句话 + 当下数值 + 离差 + 三个限定读数。

        平静 · 偏积极                          效价
                                               60
        比你的常态高 5 · 预测 5 分钟              纠正
        唤醒 47   置信 92%   ±1σ 46 – 73      估 17:05

    设计依据（Exploration 03）：最大的字必须是"你现在怎么样"，
    数字只做佐证；效价值提到标题层后，不再在读数行里重复。
    """

    def __init__(self, on_correction_clicked=None, parent=None):
        super().__init__(parent)
        self._on_corr = on_correction_clicked
        self._forecast_min = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 10)
        root.setSpacing(0)

        # ---- 第一行：状态句 + 当下效价 ----
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        self.state_label = QLabel("—")
        self.state_label.setFont(app_font(26, QFont.Medium))
        self.state_label.setStyleSheet(f"color: {COLORS['ink']};")
        row1.addWidget(self.state_label)
        row1.addStretch(1)

        now = QVBoxLayout()
        now.setSpacing(0)
        self.cap = QLabel("效价")
        self.cap.setFont(app_font(9))
        self.cap.setStyleSheet(f"color: {COLORS['muted']};")
        self.cap.setAlignment(Qt.AlignRight)
        now.addWidget(self.cap)
        self.v_lbl = _Value("效价", size=26, color=COLORS['accent_ink'])
        self.v_lbl.value_label.setAlignment(Qt.AlignRight)
        now.addWidget(self.v_lbl.value_label)
        row1.addLayout(now)
        root.addLayout(row1)

        root.addSpacing(5)

        # ---- 第二行：离差 + 纠正入口 ----
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.meta_label = QLabel("")
        self.meta_label.setFont(app_font(11))
        self.meta_label.setStyleSheet(f"color: {COLORS['ink_2']};")
        row2.addWidget(self.meta_label)
        row2.addStretch(1)
        self.corr_btn = QPushButton("纠正")
        self.corr_btn.setCursor(Qt.PointingHandCursor)
        self.corr_btn.setFlat(True)
        self.corr_btn.setFocusPolicy(Qt.NoFocus)
        self.corr_btn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent;"
            f" color: {COLORS['muted']}; font-size: 9px; padding: 0;"
            f" text-decoration: underline; }}"
            f"QPushButton:hover {{ color: {COLORS['accent_ink']}; }}")
        if on_correction_clicked is not None:
            self.corr_btn.clicked.connect(on_correction_clicked)
        row2.addWidget(self.corr_btn)
        root.addLayout(row2)

        root.addSpacing(9)

        # ---- 第三行：三个限定读数 + 估计时刻 ----
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self.a_lbl = _Value("唤醒")
        self.c_lbl = _Value("置信")
        self.ci_lbl = _Value("±1σ")
        for val in (self.a_lbl, self.c_lbl, self.ci_lbl):
            row3.addWidget(val.caption)
            row3.addWidget(val.value_label)
            row3.addSpacing(8)
        row3.addStretch(1)
        self.stamp = QLabel("")
        self.stamp.setFont(app_font(9))
        self.stamp.setStyleSheet(f"color: {COLORS['muted']};")
        row3.addWidget(self.stamp)
        root.addLayout(row3)

        self.reset_values()

    def resizeEvent(self, event):
        """360px：状态句降一档、去掉"效价"小标题，绝不裁字或藏功能。"""
        narrow = event.size().width() < 380
        self.state_label.setFont(app_font(21 if narrow else 26, QFont.Medium))
        self.v_lbl.value_label.setFont(
            app_font_num(22 if narrow else 26, QFont.Light))
        self.cap.setVisible(not narrow)
        pad = 18 if narrow else 24
        self.layout().setContentsMargins(pad, 22, pad, 10)
        super().resizeEvent(event)

    # ------------------------------------------------------------
    def set_forecast(self, minutes: int):
        """预测时长放进右下角的时间戳旁，不挤离差行。"""
        self._forecast_min = int(minutes or 0)
        if getattr(self, "_last_state_ts", 0.0):
            stamp = "估 %s" % _short_clock(self._last_state_ts)
            if self._forecast_min:
                stamp += "　·　预测 %d 分钟" % self._forecast_min
            self.stamp.setText(stamp)

    def update_state(self, valence, arousal, params_text=None):
        self.state_label.setText(derive_state_name(valence, arousal))

    def update_values(self, valence, arousal, confidence, ci_text):
        self.v_lbl.value_label.setText("%d" % round(float(valence) * 100))
        self.a_lbl.value_label.setText("%d" % round(float(arousal) * 100))
        self.c_lbl.value_label.setText("%d%%" % round(float(confidence) * 100))
        self.ci_lbl.value_label.setText(ci_text)

    def update_meta(self, valence, state_ts=0.0, last_obs_ts=0.0,
                    baseline_v=None):
        if not state_ts:
            # 没有估计时刻 = 还没有任何状态，别用"持平"假装有数据
            self._last_meta = EMPTY_HINT
            self.meta_label.setText(EMPTY_HINT)
            self.stamp.setText("")
            self.corr_btn.setVisible(False)
            return
        diff = int(round((float(valence) - float(baseline_v)) * 100))             if baseline_v is not None else 0
        if abs(diff) <= 2:
            parts = ["与你的常态持平"]
        elif diff > 0:
            parts = ["比你的常态高 %d" % diff]
        else:
            parts = ["比你的常态低 %d" % abs(diff)]
        if last_obs_ts:
            age = (float(state_ts) - float(last_obs_ts)) / 60.0
            parts.append("刚刚自评" if age < 1 else "自评 %d 分钟前" % round(age))
        text = "　·　".join(parts)
        self._last_meta = text
        self.meta_label.setText(text)
        stamp = "估 %s" % _short_clock(state_ts)
        if self._forecast_min:
            stamp += "　·　预测 %d 分钟" % self._forecast_min
        self.stamp.setText(stamp)
        self.corr_btn.setVisible(True)

    def reset_values(self):
        self.state_label.setText("—")
        self.v_lbl.value_label.setText("—")
        self.a_lbl.value_label.setText("—")
        self.c_lbl.value_label.setText("—")
        self.ci_lbl.value_label.setText("—")
        self.stamp.setText("")
        self._last_meta = EMPTY_HINT
        self.meta_label.setText(EMPTY_HINT)
        self.corr_btn.setVisible(False)


class ParamsExplainCard(QFrame):
    """参数说明卡（「模型」Tab）。

    v3 它是状态卡里一个 220px 的折叠面板，小窗放不下；整页化后有地方常驻展示，
    折叠动画与展开状态一并去掉（少一套动画 = 少一类 bug）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)

        title = QLabel("参数说明")
        title.setFont(app_font(10, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['ink']}; letter-spacing: 1.8px;")
        lay.addWidget(title)

        hint = QLabel("这些数字是模型对当前状态的估计，不是对你的定义。")
        hint.setFont(app_font(9))
        hint.setStyleSheet(f"color: {COLORS['muted']};")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(10)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        lay.addLayout(self._grid)
        self._cells = []

        for i, key in enumerate(("效价", "唤醒", "置信", "区间")):
            cell = QFrame()
            cell.setStyleSheet("background: transparent; border: none;")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(3)
            k = QLabel(key)
            k.setFont(app_font(8, QFont.DemiBold))
            k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.4px;")
            val = QLabel("—")
            val.setFont(app_font_num(18, QFont.Light))
            val.setStyleSheet(f"color: {COLORS['ink']};")
            desc = QLabel("—")
            desc.setFont(app_font(9))
            desc.setStyleSheet(f"color: {COLORS['muted']};")
            extra = QLabel("")
            extra.setFont(app_font(9))
            extra.setStyleSheet(f"color: {COLORS['accent_ink']};")
            cl.addWidget(k)
            cl.addWidget(val)
            cl.addWidget(desc)
            cl.addWidget(extra)
            self._grid.addWidget(cell, i // 2, i % 2)
            self._cells.append((val, desc, extra))

    def update_params(self, valence, arousal, params_text=None):
        """按 MainConsole._refresh_readouts 传入的 dict 刷新四格。"""
        p = params_text or {}
        vals = (
            (f"{int(valence * 100)}", "正负方向", f"={int(valence * 100)}/100"),
            (f"{int(arousal * 100)}", "强度 · 能量", f"={int(arousal * 100)}/100"),
            (p.get('confidence', '—'), "0–1 把握度", p.get('confidence_desc', '')),
            (p.get('ci', '—'), "模型估计范围", p.get('ci_desc', '')),
        )
        for (val, desc, extra), cell in zip(vals, self._cells):
            cell[0].setText(val)
            cell[1].setText(desc)
            cell[2].setText(extra)

    def reset_params(self):
        for cell in self._cells:
            cell[0].setText("—")
            cell[1].setText("—")
            cell[2].setText("")


# ================================================================
# 状态名派生（按 V×A 象限；不作诊断/评判）
# ================================================================
#: 空态引导（新用户第一眼看到的唯一一句操作提示）
EMPTY_HINT = "还没有自评 · 点「开始记录」，或按 Ctrl+R"


def _fmt_age(seconds: float) -> str:
    """把秒数说成人话：12 分钟 / 3 小时 / 2 天。"""
    seconds = max(0.0, float(seconds))
    if seconds < 3600:
        return "%d 分钟" % max(1, int(round(seconds / 60.0)))
    if seconds < 86400:
        return "%d 小时" % int(round(seconds / 3600.0))
    return "%d 天" % int(round(seconds / 86400.0))


def _short_clock(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M")


def _fmt_clock(ts: float) -> str:
    """时间戳 → HH:MM（当天）或 M月D日 HH:MM。"""
    import datetime as _dt
    moment = _dt.datetime.fromtimestamp(float(ts))
    now = _dt.datetime.now()
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    return moment.strftime("%m-%d %H:%M")


def _fmt_delta(valence: float, baseline_v: float) -> str:
    """与基线的差：比基线 +8 / 低于基线 8 / 与基线持平。"""
    diff = int(round((float(valence) - float(baseline_v)) * 100))
    if abs(diff) <= 2:
        return "与基线持平"
    if diff > 0:
        return "比基线 +%d" % diff
    return "低于基线 %d" % abs(diff)


def _derive_state_name(valence: float, arousal: float) -> str:
    """由 0–1 的 V/A 返回中文状态名（不含主观评价）。"""
    v = valence >= 0.5
    a = arousal >= 0.5
    if v and a:
        return "兴奋 · 活跃"
    if v and not a:
        return "平静 · 偏积极"
    if not v and a:
        return "紧张 · 焦虑"
    return "低落 · 疲惫"


#: 公开别名（v3 起该函数住在 main_console.py，为兼容既有 import 路径保留同名导出）
derive_state_name = _derive_state_name
