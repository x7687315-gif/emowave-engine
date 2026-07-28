"""tests/test_surfing_window.py — 情绪冲浪记录窗口测试"""
import pytest


def test_surfing_window_creates(qapp, tmp_path):
    """SurfingWindow 可以创建，且关键控件均存在"""
    from session import SessionController
    from db import DatabaseManager
    from windows.surfing_window import SurfingWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    try:
        win = SurfingWindow(session)
        assert win.valence_slider is not None
        assert win.arousal_slider is not None
        assert win.canvas is not None
        assert win.start_btn is not None
    finally:
        db.close()
