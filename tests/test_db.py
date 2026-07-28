"""tests/test_db.py — DatabaseManager 持久化层测试"""
import os, json
from datetime import datetime
import pytest


def test_create_tables_no_error(tmp_path):
    """DatabaseManager 初始化时自动创建所有表"""
    from db import DatabaseManager
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)

    tables = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = [t['name'] for t in tables]

    assert 'emotion_events' in table_names
    assert 'daily_summaries' in table_names
    assert 'app_state' in table_names
    db.close()


def test_save_and_get_event(tmp_path):
    """存储并查询情绪事件"""
    from db import DatabaseManager
    db = DatabaseManager(str(tmp_path / "test.db"))

    event = {
        'event_id': 'evt_001',
        'start_time': 1700000000.0,
        'end_time': 1700000300.0,
        'peak_valence': 0.15,
        'peak_arousal': 0.88,
        'peak_intensity': 8.5,
        'sample_count': 300,
        'trigger_tags': ['工作会议', 'deadline'],
        'coping_methods': ['深呼吸'],
        'coping_ratings': {'深呼吸': 4},
        'body_symptoms': ['胸口压抑'],
        'user_peak_rating': 8.0,
    }
    db.save_event(event)

    rows = db.get_recent_events(limit=5)
    assert len(rows) == 1
    assert rows[0]['event_id'] == 'evt_001'
    assert rows[0]['peak_arousal'] == 0.88
    db.close()


def test_get_events_by_date_and_all_dates(tmp_path):
    """按日期查询事件并获取所有事件日期"""
    from db import DatabaseManager
    db = DatabaseManager(str(tmp_path / "test.db"))

    event = {
        'event_id': 'evt_002', 'start_time': 1700000000.0,
        'end_time': 1700000300.0, 'peak_valence': 0.2,
        'peak_arousal': 0.75, 'peak_intensity': 7.0, 'sample_count': 100,
    }
    db.save_event(event)

    today = datetime.now().strftime('%Y-%m-%d')
    rows = db.get_events_by_date(today)
    assert len(rows) == 1

    dates = db.get_all_event_dates()
    assert today in dates
    db.close()


def test_daily_summary_and_app_state(tmp_path):
    """每日摘要存储与状态键值对"""
    from db import DatabaseManager
    db = DatabaseManager(str(tmp_path / "test.db"))

    summary = {
        'date': '2024-01-15', 'avg_valence': 0.45, 'avg_arousal': 0.6,
        'avg_intensity': 5.5, 'event_count': 3, 'peak_arousal': 0.9,
    }
    db.save_daily_summary(summary)

    result = db.get_daily_summary('2024-01-15')
    assert result is not None
    assert result['event_count'] == 3

    last = db.get_last_daily_summary()
    assert last['date'] == '2024-01-15'

    db.set_state('engine_state', '{"test": true}')
    assert db.get_state('engine_state') == '{"test": true}'
    assert db.get_state('nonexistent', 'default') == 'default'
    db.close()
