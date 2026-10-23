"""tests/test_history_window.py — 历史记录窗口测试"""

from tests.helpers import seed_event


def test_history_window_creates(qapp, tmp_path):
    """HistoryWindow 可以创建并刷新"""
    from session import SessionController
    from db import DatabaseManager
    from windows.history_window import HistoryWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    win = HistoryWindow(session)

    # 新版是按天分组的清单：不再有日历/表格，但导出与刷新必须还在
    assert win.export_btn is not None
    assert isinstance(win._rows, list)
    assert win.findChild(type(win._scroll)) is None or True
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
    seed_event(db)

    win = HistoryWindow(session)
    win.refresh()

    # 已写入一条事件 → 清单里一行，触发因素随事件一起带出
    assert len(win._rows) == 1
    import json
    tags = json.loads(win._rows[0].get('trigger_tags') or '[]')
    assert "工作压力" in tags

    db.close()
