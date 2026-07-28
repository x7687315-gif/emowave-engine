"""tests/test_dashboard_window.py — 今日仪表盘窗口测试"""
import pytest


def test_dashboard_window_creates(qapp, tmp_path):
    """DashboardWindow 可以创建并刷新空数据不崩溃"""
    from session import SessionController
    from db import DatabaseManager
    from windows.dashboard_window import DashboardWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    win = DashboardWindow(session)
    # 核心控件存在
    assert win.risk_ring is not None
    assert win.recent_events_list is not None
    # 空数据刷新不崩溃
    win.refresh()

    db.close()


def test_dashboard_window_refresh_uses_today_summary(qapp, tmp_path):
    """refresh() 时若 today_summary 含 peak_arousal，则同步到风险环"""
    from session import SessionController
    from db import DatabaseManager
    from windows.dashboard_window import DashboardWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    # 注入一条今日摘要
    db.save_daily_summary({
        'date': __import__('datetime').datetime.now().strftime('%Y-%m-%d'),
        'avg_valence': 0.4, 'avg_arousal': 0.6, 'avg_intensity': 5.0,
        'event_count': 1, 'peak_arousal': 0.72,
    })

    win = DashboardWindow(session)
    win.refresh()

    assert win.risk_ring.value == pytest.approx(0.72, abs=1e-6)
    db.close()


def test_dashboard_window_refresh_empty_sets_zero(qapp, tmp_path):
    """空数据时风险环归零"""
    from session import SessionController
    from db import DatabaseManager
    from windows.dashboard_window import DashboardWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    win = DashboardWindow(session)
    win.risk_ring.set_value(0.9)  # 先设非零
    win.refresh()  # 空数据应归零
    assert win.risk_ring.value == 0.0

    db.close()


def test_dashboard_window_refresh_fills_recent_events(qapp, tmp_path):
    """refresh() 后最近事件列表应反映数据库内容"""
    from session import SessionController
    from db import DatabaseManager
    from windows.dashboard_window import DashboardWindow

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    # 写入一条事件
    db.save_event({
        'event_id': 'evt_test_001', 'start_time': 0.0, 'end_time': 1.0,
        'peak_valence': 0.3, 'peak_arousal': 0.8, 'peak_intensity': 7.0,
        'sample_count': 10, 'trigger_tags': ['工作压力'],
        'coping_methods': ['深呼吸'], 'coping_ratings': {},
        'body_symptoms': ['胸闷'], 'user_peak_rating': 7.0,
    })

    win = DashboardWindow(session)
    win.refresh()

    assert win.recent_events_list.count() == 1

    db.close()
