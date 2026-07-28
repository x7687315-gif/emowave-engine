"""db.py — 心潮 EmoWave 本地 SQLite 持久化层

管理所有本地数据的读写：情绪事件、每日摘要、应用状态。
所有数据仅存储在设备本地，不上传任何信息。
"""
import os
import sqlite3
import json
from datetime import datetime


class DatabaseManager:
    """管理本地 SQLite 数据库的所有读写操作。"""

    def __init__(self, path: str = None):
        if path is None:
            path = os.path.join(os.path.expanduser("~"), ".emowave", "emowave.db")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS emotion_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE, start_time REAL, end_time REAL,
                peak_valence REAL, peak_arousal REAL, peak_intensity REAL,
                sample_count INTEGER, trigger_tags TEXT, coping_methods TEXT,
                coping_ratings TEXT, body_symptoms TEXT, user_peak_rating REAL,
                raw_data TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE, avg_valence REAL, avg_arousal REAL,
                avg_intensity REAL, event_count INTEGER, peak_arousal REAL,
                sleep_score REAL, avg_hrv REAL, avg_hr REAL, raw_data TEXT
            );
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY, value TEXT
            );
        """)
        self.conn.commit()

    def save_event(self, event: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO emotion_events "
            "(event_id,start_time,end_time,peak_valence,peak_arousal,peak_intensity,"
            "sample_count,trigger_tags,coping_methods,coping_ratings,body_symptoms,"
            "user_peak_rating,raw_data,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event['event_id'], event['start_time'], event['end_time'],
             event['peak_valence'], event['peak_arousal'], event['peak_intensity'],
             event['sample_count'], json.dumps(event.get('trigger_tags', [])),
             json.dumps(event.get('coping_methods', [])),
             json.dumps(event.get('coping_ratings', {})),
             json.dumps(event.get('body_symptoms', [])),
             event.get('user_peak_rating'),
             event.get('raw_data', ''),
             datetime.now().isoformat()))
        self.conn.commit()

    def get_recent_events(self, limit=5):
        rows = self.conn.execute(
            "SELECT * FROM emotion_events ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_events_by_date(self, date_str):
        rows = self.conn.execute(
            "SELECT * FROM emotion_events WHERE date(created_at)=? ORDER BY created_at",
            (date_str,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_event_dates(self):
        rows = self.conn.execute(
            "SELECT DISTINCT date(created_at) as d FROM emotion_events").fetchall()
        return [r['d'] for r in rows]

    def save_daily_summary(self, data: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO daily_summaries "
            "(date,avg_valence,avg_arousal,avg_intensity,event_count,"
            "peak_arousal,sleep_score,avg_hrv,avg_hr,raw_data) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (data['date'], data['avg_valence'], data['avg_arousal'],
             data['avg_intensity'], data['event_count'], data['peak_arousal'],
             data.get('sleep_score', 0), data.get('avg_hrv', 0),
             data.get('avg_hr', 0), json.dumps(data)))
        self.conn.commit()

    def get_daily_summary(self, date_str):
        row = self.conn.execute(
            "SELECT * FROM daily_summaries WHERE date=?", (date_str,)).fetchone()
        return dict(row) if row else None

    def get_last_daily_summary(self):
        row = self.conn.execute(
            "SELECT * FROM daily_summaries ORDER BY date DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def set_state(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO app_state (key,value) VALUES (?,?)",
            (key, value))
        self.conn.commit()

    def get_state(self, key: str, default=""):
        row = self.conn.execute(
            "SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default

    def close(self):
        self.conn.close()
