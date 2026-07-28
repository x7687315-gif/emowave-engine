"""tests/test_session.py — SessionController 会话控制器测试"""
import pytest, json, time

from models import TimeSeriesSample


def test_session_init_with_db(tmp_path):
    """SessionController 初始化时创建引擎和数据库"""
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    assert session.engine is not None
    assert session.db is db
    db.close()


def test_process_emotion_event(tmp_path):
    """处理一次完整的情绪事件：采样 → 处理 → 存储"""
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    # 构造采样数据
    now = time.time()
    samples = [
        TimeSeriesSample(timestamp=now + i, valence=0.6 - i*0.01, arousal=0.3 + i*0.05)
        for i in range(30)
    ]

    result = session.process_event(
        samples=samples,
        trigger_tags=['工作压力'],
        coping_methods=['深呼吸'],
        body_symptoms=['肩膀紧绷'],
        user_peak_rating=7.0,
    )

    assert result is not None
    assert 'event_id' in result
    assert 'peak_arousal' in result

    # 验证已存入数据库
    events = db.get_recent_events(limit=5)
    assert len(events) == 1
    db.close()


def test_get_recommendation(tmp_path):
    """获取策略推荐"""
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    rec = session.get_recommendation(
        valence=0.2, arousal=0.85, hour=14, weekday=2, sleep=6.0
    )

    assert rec is not None
    assert 'strategy_name' in rec
    assert 'strategy_id' in rec
    db.close()


def test_get_dashboard_data_empty(tmp_path):
    """空数据库时返回默认仪表盘数据"""
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    data = session.get_dashboard_data()
    assert 'recent_events' in data
    assert 'today_summary' in data
    assert 'baseline' in data
    assert data['recent_events'] == []
    db.close()
