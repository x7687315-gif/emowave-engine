"""tests/helpers.py — 测试夹具的公共构造器

视图测试需要一个"已存在的情绪事件"。此前它们调用
`session.process_event(samples=...)` 造数据，等于让测试依赖 1.x 引擎
（`engine` / `kalman_filter` / `recommender`）。引擎退役后，测试夹具只依赖
持久化层本身——写一行数据就是写一行数据。
"""
import time
import uuid


def seed_event(db, **overrides):
    """直接向数据库写入一条情绪事件，返回 event_id。"""
    now = time.time()
    event = {
        "event_id": "evt_%s" % uuid.uuid4().hex[:12],
        "start_time": now - 120,
        "end_time": now,
        "peak_valence": 0.6,
        "peak_arousal": 0.45,
        "peak_intensity": 7.0,
        "sample_count": 30,
        "trigger_tags": ["工作压力", "deadline"],
        "coping_methods": ["深呼吸"],
        "coping_ratings": {},
        "body_symptoms": ["肩膀紧绷"],
        "user_peak_rating": 7.0,
    }
    event.update(overrides)
    db.save_event(event)
    return event["event_id"]


class FakeClock:
    """可控时钟：让"过了 30 分钟"变成一行代码，而不是真的 sleep。"""

    def __init__(self, start=1_700_000_000.0):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += float(seconds)
        return self.t
