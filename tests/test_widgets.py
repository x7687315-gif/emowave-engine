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
