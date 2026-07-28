"""tests/test_widgets.py — 共享自定义控件测试"""
import pytest


def test_risk_ring_widget_creates(qapp):
    """RiskRingWidget 可以创建并设置值"""
    from widgets import RiskRingWidget
    widget = RiskRingWidget()
    widget.set_value(0.65)
    assert widget.value == 0.65

    # 边界裁剪
    widget.set_value(1.5)
    assert widget.value == 1.0
    widget.set_value(-0.3)
    assert widget.value == 0.0


def test_emotion_canvas_creates(qapp):
    """EmotionCanvas 可以创建并添加轨迹点"""
    from widgets import EmotionCanvas
    canvas = EmotionCanvas()
    canvas.add_point(0.3, 0.7)
    canvas.add_point(0.25, 0.85)
    assert len(canvas.trail) == 2
    assert canvas.trail[0] == (0.3, 0.7)

    canvas.clear()
    assert len(canvas.trail) == 0


def test_card_frame_creates(qapp):
    """CardFrame 可以创建并添加子控件"""
    from widgets import CardFrame
    from PyQt5.QtWidgets import QLabel
    card = CardFrame(title="今日概览")
    label = QLabel("测试内容")
    card.add_widget(label)
    assert card.title_label.text() == "今日概览"
