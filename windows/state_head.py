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
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QFrame)
from PyQt5.QtGui import QFont, QFontMetrics

from theme import COLORS, app_font, app_font_num


class StateHead(QWidget):
    """一条状态头：状态名（含纠正入口）+ 四个读数。"""

    def __init__(self, on_correction_clicked, parent=None):
        super().__init__(parent)
        self._on_correction_clicked = on_correction_clicked

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 12, 8)
        outer.setSpacing(2)

        # ---- row1：状态名 + hint + 纠正入口 ----
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)

        self.state_label = QLabel("—")
        self.state_label.setFont(app_font(17, QFont.Medium))
        self.state_label.setStyleSheet(f"color: {COLORS['ink']};")
        # 状态名是全窗口最重要的一个词，绝不能被旁边元素挤掉。
        longest = max(("兴奋 · 活跃", "平静 · 偏积极",
                       "紧张 · 焦虑", "低落 · 疲惫"), key=len)
        self.state_label.setMinimumWidth(
            QFontMetrics(self.state_label.font()).horizontalAdvance(longest) + 4)
        row1.addWidget(self.state_label)

        self.state_hint = QLabel(" · 模型估计")
        self.state_hint.setFont(app_font(10))
        self.state_hint.setStyleSheet(f"color: {COLORS['muted']};")
        # 让提示语在极端窄/高缩放下先让位（可被压缩）
        self.state_hint.setMinimumWidth(0)
        row1.addWidget(self.state_hint, 1, Qt.AlignBottom | Qt.AlignLeft)

        self.corr_btn = QPushButton("· 情绪有误，点这里纠正")
        self.corr_btn.setCursor(Qt.PointingHandCursor)
        self.corr_btn.setFlat(True)
        self.corr_btn.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" border: 1px solid {COLORS['rule']};"
            f" color: {COLORS['ink_2']}; padding: 3px 8px; font-size: 10px;"
            f" border-radius: 0; }}"
            f"QPushButton:hover {{ border-color: {COLORS['accent']};"
            f" color: {COLORS['accent']}; background: {COLORS['accent_soft']}; }}"
        )
        self.corr_btn.clicked.connect(self._on_correction_clicked)
        row1.addWidget(self.corr_btn, 0, Qt.AlignRight | Qt.AlignBottom)
        outer.addLayout(row1)

        # ---- row1.5：一行说清「这个数是几点的」----
        # v5：模型估计与真实自评必须可区分。没有这一行，用户会把
        # 「两小时前自评、模型推演到现在」的数值当成「我刚说过」。
        self.meta_label = QLabel("")
        self.meta_label.setFont(app_font(9))
        self.meta_label.setStyleSheet(f"color: {COLORS['muted']};")
        self.meta_label.setMinimumWidth(0)
        outer.addWidget(self.meta_label)

        # ---- row2：四读数（效价/唤醒主，置信/区间次）----
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(16)

        self.v_lbl = self._make_cell("效价 VAL", 18, primary=True)
        self.a_lbl = self._make_cell("唤醒 ARO", 18, primary=True)
        self.c_lbl = self._make_cell("置信 CONF", 13, primary=False)
        self.ci_lbl = self._make_cell("区间 ±1σ", 13, primary=False)
        for lbl in (self.v_lbl, self.a_lbl, self.c_lbl, self.ci_lbl):
            row2.addWidget(lbl, 1)
        outer.addLayout(row2)

        self.setMinimumHeight(68)

    def _make_cell(self, key, num_size, primary):
        """一个读数单元。返回挂载了 .value_label 的容器。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        k = QLabel(key)
        k.setFont(app_font(8, QFont.DemiBold))
        k.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.2px;")
        big = QLabel("—")
        big.setFont(app_font_num(num_size,
                                 QFont.DemiBold if primary else QFont.Light))
        big.setStyleSheet(
            f"color: {COLORS['accent_ink'] if primary else COLORS['ink_2']};")
        big.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        v.addWidget(k)
        v.addWidget(big)
        w.value_label = big
        return w

    # ------------------------------------------------------------
    # 两个 v3 入口，签名不变
    # ------------------------------------------------------------
    def update_state(self, valence, arousal, params_text=None):
        """更新状态名。params_text 由「模型」Tab 的 ParamsExplainCard 消费。"""
        self.state_label.setText(_derive_state_name(valence, arousal))

    def update_values(self, valence, arousal, confidence, ci_text):
        self.v_lbl.value_label.setText(f"{int(valence * 100)}")
        self.a_lbl.value_label.setText(f"{int(arousal * 100)}")
        self.c_lbl.value_label.setText(f"{int(confidence * 100)}%")
        self.ci_lbl.value_label.setText(ci_text)

    def update_meta(self, valence, state_ts=0.0, last_obs_ts=0.0, baseline_v=None):
        """一行说清「这个数是几点的、离上次自评多久、比基线高多少」。

        没有这一行，用户会把「两小时前自评、模型推演到现在」的数值
        当成「我刚说过」——这是三种数据（真实观察 / 模型估计 / 预测）
        在界面上唯一的分界线。
        """
        if not state_ts:
            self.meta_label.setText("")
            return

        parts = []
        age = max(0.0, float(state_ts) - float(last_obs_ts)) if last_obs_ts else None
        if age is None:
            parts.append("还没有自评")
        elif age < 60:
            parts.append("刚刚自评")
        else:
            parts.append("距上次自评 " + _fmt_age(age))
            parts.append("模型估计 " + _fmt_clock(state_ts))

        if baseline_v is not None:
            parts.append(_fmt_delta(valence, baseline_v))

        self.meta_label.setText(" · ".join(parts))

    def reset_values(self):
        """全部回到占位符（停止推演 / 切换人群时）。"""
        for lbl in (self.v_lbl, self.a_lbl, self.c_lbl, self.ci_lbl):
            lbl.value_label.setText("—")
        self.state_label.setText("—")


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

        self._grid = QHBoxLayout()
        self._grid.setSpacing(0)
        lay.addLayout(self._grid)
        self._cells = []

        for i, key in enumerate(("效价 VALENCE", "唤醒 AROUSAL",
                                 "置信 CONFIDENCE", "置信区间 ±1σ")):
            cell = QFrame()
            cell.setStyleSheet("background: transparent; border: none;")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(0 if i == 0 else 18, 0, 0, 0)
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
            self._grid.addWidget(cell)
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
def _fmt_age(seconds: float) -> str:
    """把秒数说成人话：12 分钟 / 3 小时 / 2 天。"""
    seconds = max(0.0, float(seconds))
    if seconds < 3600:
        return "%d 分钟" % max(1, int(round(seconds / 60.0)))
    if seconds < 86400:
        return "%d 小时" % int(round(seconds / 3600.0))
    return "%d 天" % int(round(seconds / 86400.0))


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
