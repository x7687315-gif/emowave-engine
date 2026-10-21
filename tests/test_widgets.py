"""tests/test_widgets.py — 遗留窗口共享控件（只测还在用的部分）

v1 的 RiskRingWidget / EmotionCanvas / StatBlock / EmotionCurveWidget 已于
2026-10-19 删除（零生产引用，且曲线已被 curve_widget.py 取代），
对应测试一并退役。
"""


def test_card_frame_creates(qapp):
    """CardFrame 可以创建并挂子控件（事件回顾/历史记录在用）。"""
    from widgets import CardFrame

    card = CardFrame("测试分组")
    assert card.title_label.text() == "测试分组"

    from PyQt5.QtWidgets import QLabel
    label = QLabel("内容")
    card.add_widget(label)
    assert label.parent() is card


def test_widgets_reexports_theme_colors():
    """遗留窗口按 `from widgets import COLORS` 导入，必须与 theme 同一对象。"""
    import widgets
    from theme import COLORS

    assert widgets.COLORS is COLORS


# ================================================================
# curve_widget：预测段与静态层缓存（v4 Phase 5）
# ================================================================

def _curve(qapp, n=10, predicted_from=None):
    from curve_widget import EmotionCurveWidget

    w = EmotionCurveWidget(series="valence")
    w.resize(400, 340)
    ts = [1000.0 + i for i in range(n)]
    mv = [0.5 + 0.01 * i for i in range(n)]
    w.set_data(ts, mv, mv, [0.01] * n, [0.01] * n,
               baseline_v=0.5, raw=[(t, v) for t, v in zip(ts, mv)],
               predicted_from=predicted_from)
    return w


def test_prediction_split_marks_forecast(qapp):
    """越过最后一次自评的点被标成预测段。"""
    w = _curve(qapp, n=10, predicted_from=1005.0)
    assert w.prediction_split() == 6          # ts > 1005 从索引 6 开始

    w_none = _curve(qapp, n=10, predicted_from=None)
    assert w_none.prediction_split() is None

    w_all = _curve(qapp, n=10, predicted_from=2000.0)
    assert w_all.prediction_split() is None    # 没有点越过该时刻


def test_static_layer_cache_reused_until_size_changes(qapp):
    """静态层（网格/刻度）缓存：尺寸与时间范围不变时复用同一个 pixmap。"""
    w = _curve(qapp, n=10)
    first = w._ensure_static(w._panels())
    second = w._ensure_static(w._panels())
    assert first is second

    w.resize(420, 340)
    third = w._ensure_static(w._panels())
    assert third is not first


def test_curve_renders_correction_marks(qapp):
    """带修正点的曲线能真正画出来（render 走完整 paintEvent）。"""
    from PyQt5.QtGui import QPixmap

    w = _curve(qapp, n=10, predicted_from=1005.0)
    w.set_data(w.timestamps, w.model_v, w.model_v, [0.01] * 10, [0.01] * 10,
               baseline_v=0.5, raw=[(t, v) for t, v in zip(w.timestamps, w.model_v)],
               predicted_from=1005.0,
               corrections=[{"ts": 1003.0, "channel": "valence", "value": 0.9}])
    assert w.corrections

    pm = QPixmap(w.size())
    pm.fill()
    w.render(pm)
    assert not pm.isNull()


# ================================================================
# v5 Phase 5：悬停读数与柔和圆角
# ================================================================

def test_curve_hover_reports_semantics(qapp):
    """悬停读数必须和曲线画法同一套语义：观察/估计/预测/修正。"""
    from curve_widget import EmotionCurveWidget

    w = _curve(qapp, n=10, predicted_from=1005.0)
    w.set_data(w.timestamps, w.model_v, w.model_v, [0.01] * 10, [0.01] * 10,
               baseline_v=0.5, raw=[(1000.0, 0.5), (1005.0, 0.5)],
               predicted_from=1005.0,
               corrections=[{"ts": 1003.0, "channel": "valence", "value": 0.9}])
    x0, y0, pw, ph = w._plot_rect(0, 1)

    def at(ts, widget=w):
        return widget.hover_at(widget._x(ts, 1000.0, 9.0, x0, pw), y0 + ph / 2)

    assert at(1003.0)["kind"] == "修正"
    assert at(1005.0)["kind"] == "观察"
    assert at(1007.0)["kind"] == "预测"
    assert at(999.0) is None                    # 绘图区外

    plain = EmotionCurveWidget(series="valence")
    plain.resize(400, 340)
    plain.set_data(list(range(1000, 1010)), [0.5] * 10, [0.5] * 10,
                   [0.01] * 10, [0.01] * 10, baseline_v=0.5, raw=[])
    x0, y0, pw, ph = plain._plot_rect(0, 1)
    assert at(1004.0, plain)["kind"] == "估计"


def test_compact_curve_disables_hover(qapp):
    from curve_widget import EmotionCurveWidget
    from theme import METRICS

    w = EmotionCurveWidget(compact=True)
    assert w.show_hover is False
    assert w._corner_radius == 0
    full = EmotionCurveWidget()
    assert full._corner_radius == METRICS['radius']


def test_compact_curve_uses_sparkline_padding(qapp):
    """迷你挂件是 sparkline：高度 <100 时去掉刻度留白，避免 100/50/0 叠在一起。"""
    from curve_widget import EmotionCurveWidget

    w = EmotionCurveWidget(compact=True)
    w.setMinimumHeight(58)
    w.resize(340, 58)
    x0, y0, pw, ph = w._plot_rect(0, 1)
    assert x0 == 10 and y0 == 6
    assert pw == 340 - 18 and ph == w.height() - 12


def test_curve_hover_renders(qapp):
    """带悬停覆盖层的 render 不崩（覆盖层是自绘的，不走 QToolTip）。"""
    from PyQt5.QtGui import QPixmap

    w = _curve(qapp, n=10, predicted_from=1005.0)
    x0, y0, pw, ph = w._plot_rect(0, 1)
    w._hover = w.hover_at(w._x(1007.0, 1000.0, 9.0, x0, pw), y0 + ph / 2)
    assert w._hover is not None
    pm = QPixmap(w.size())
    pm.fill()
    w.render(pm)
    assert not pm.isNull()
