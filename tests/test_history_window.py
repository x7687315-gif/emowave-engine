"""tests/test_history_window.py — 历史记录窗口测试"""
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
        body_symptoms=['肩膀紧绷'],
        user_peak_rating=7.0,
    )
    return result['event_id']


def test_history_window_creates(qapp, tmp_path):
    """HistoryWindow 可以创建并刷新"""
    from session import SessionController
    from db import DatabaseManager
    from windows.history_window import HistoryWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    win = HistoryWindow(session)

    assert win.calendar is not None
    assert win.events_table is not None
    assert win.export_btn is not None

    # 表格应有 4 列：时间 / 峰值唤醒 / 触发因素 / 自评
    assert win.events_table.columnCount() == 4

    # 刷新不应崩溃
    win.refresh()

    db.close()


def test_history_window_lists_events_by_date(qapp, tmp_path):
    """点击今天日期时，事件列表应填充已写入的事件"""
    from session import SessionController
    from db import DatabaseManager
    from windows.history_window import HistoryWindow
    from PyQt5.QtCore import QDate

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)
    _seed_event(session)

    win = HistoryWindow(session)

    today = QDate.currentDate()
    win._on_date_clicked(today)

    # 已写入一条事件，表格应有一行
    assert win.events_table.rowCount() == 1
    # 触发因素列应包含标签
    trigger_item = win.events_table.item(0, 2)
    assert trigger_item is not None
    assert "工作压力" in trigger_item.text()

    db.close()
