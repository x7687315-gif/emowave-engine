"""tests/test_event_summary_window.py — 事件回顾窗口测试"""
import time

import pytest

from models import TimeSeriesSample


def _seed_event(session):
    """向数据库写入一条情绪事件，返回其 event_id。"""
    now = time.time()
    samples = [
        TimeSeriesSample(timestamp=now + i, valence=0.6 - i * 0.01,
                         arousal=0.3 + i * 0.05)
        for i in range(30)
    ]
    result = session.process_event(
        samples=samples,
        trigger_tags=['工作压力', 'deadline'],
        coping_methods=['深呼吸'],
        body_symptoms=['肩膀紧绷', '胸口压抑'],
        user_peak_rating=7.0,
    )
    return result['event_id']


def test_event_summary_window_creates(qapp, tmp_path):
    """EventSummaryWindow 可以创建并安全显示不存在的事件"""
    from session import SessionController
    from db import DatabaseManager
    from windows.event_summary_window import EventSummaryWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    win = EventSummaryWindow(session)

    assert win.profile_label is not None
    assert win.curve_canvas is not None
    assert win.body_label is not None

    # 显示不存在的事件不应崩溃
    win.show_event("nonexistent_id")
    assert "nonexistent_id" in win.profile_label.text()

    db.close()


def test_event_summary_window_shows_real_event(qapp, tmp_path):
    """显示真实事件时应填充峰值唤醒度/效价/采样点/触发因素/自评/躯体症状"""
    from session import SessionController
    from db import DatabaseManager
    from windows.event_summary_window import EventSummaryWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)
    event_id = _seed_event(session)

    win = EventSummaryWindow(session)
    win.show_event(event_id)

    text = win.profile_label.text()
    assert "峰值唤醒度" in text
    assert "峰值效价" in text
    assert "采样点" in text
    assert "触发因素" in text
    assert "自评" in text

    # 躯体症状应包含已写入的标签
    body_text = win.body_label.text()
    assert "肩膀紧绷" in body_text

    # 曲线画布应已收到数据点
    assert len(win.curve_canvas.points) > 0

    db.close()
