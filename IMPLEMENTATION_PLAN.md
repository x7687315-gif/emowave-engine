# EmoWave 桌面应用 TDD 实现计划

> **目标:** 构建一个本地可运行的 PyQt5 桌面情绪追踪应用，完整集成 EmoWave 引擎，支持情绪记录、事件回顾、历史报告等全流程操作。

> **架构:** 三层架构 — UI 层 (PyQt5 窗口) ↔ 控制层 (session.py) ↔ 持久层 (db.py)。引擎模块 (models, kalman_filter, predictor, recommender, engine) 为已有稳定代码，通过 session 控制器桥接。

> **技术栈:** Python 3.10+, PyQt5 5.15, SQLite, pytest + pytest-qt

> **TDD 铁律:** 每个模块先写测试 → 看失败 → 写最小实现 → 看通过 → 重构。不例外。

---

## 文件结构

```
emowave-engine/
├── main_app.py                  # 入口 + MainWindow 集成 + 菜单栏
├── db.py                        # SQLite 持久化层
├── session.py                   # 会话控制器（UI ↔ Engine 桥接）
├── widgets.py                   # 共享自定义控件
├── windows/
│   ├── __init__.py              # (已存在)
│   ├── dashboard_window.py      # 今日仪表盘
│   ├── surfing_window.py        # 情绪冲浪记录
│   ├── event_summary_window.py  # 事件回顾
│   └── history_window.py        # 历史记录与报告
├── tests/
│   ├── __init__.py              # (已存在)
│   ├── conftest.py              # (已存在, 含 qapp fixture)
│   ├── test_db.py
│   ├── test_session.py
│   ├── test_widgets.py
│   ├── test_dashboard_window.py
│   ├── test_surfing_window.py
│   ├── test_event_summary_window.py
│   └── test_history_window.py
└── requirements.txt             # (已存在)
```

---

## Task 0: 清理未完成的代码

**文件:**
- 删除: `desktop_app/main_app.py`
- 删除: `desktop_app/` 目录（如果为空）
- 保留: 根目录 `main_app.py`（将作为入口扩展）

- [ ] **Step 1: 删除未测试的代码**

```bash
rm -rf /workspace/emowave-engine/desktop_app/
```

- [ ] **Step 2: 验证根目录 main_app.py 仍可运行**

```bash
cd /workspace/emowave-engine && python -c "import main_app; print('OK')"
```

预期: 输出 `OK`，无报错

---

## Task 1: db.py — SQLite 持久化层

**文件:**
- 创建: `db.py`
- 测试: `tests/test_db.py`

### 1.1 建表测试

- [ ] **Step 1: 写失败测试 — 建表**

```python
# tests/test_db.py
import os, tempfile, json
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
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /workspace/emowave-engine && python -m pytest tests/test_db.py::test_create_tables_no_error -v
```

预期: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: 写最小实现**

```python
# db.py
"""db.py — 心潮 EmoWave 本地 SQLite 持久化层"""
import os, sqlite3, json
from datetime import datetime

class DatabaseManager:
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

    def close(self):
        self.conn.close()
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/test_db.py::test_create_tables_no_error -v
```

预期: PASS

### 1.2 事件存储与查询

- [ ] **Step 5: 写失败测试 — 存储并查询事件**

```python
# tests/test_db.py (追加)
def test_save_and_get_event(tmp_path):
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
```

- [ ] **Step 6: 运行测试验证失败**

```bash
python -m pytest tests/test_db.py::test_save_and_get_event -v
```

预期: FAIL — `AttributeError: 'DatabaseManager' object has no attribute 'save_event'`

- [ ] **Step 7: 写最小实现**

```python
# db.py (在 DatabaseManager 类中追加方法)
    def save_event(self, event: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO emotion_events "
            "(event_id,start_time,end_time,peak_valence,peak_arousal,peak_intensity,"
            "sample_count,trigger_tags,coping_methods,coping_ratings,body_symptoms,"
            "user_peak_rating,raw_data,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
```

- [ ] **Step 8: 运行测试验证通过**

```bash
python -m pytest tests/test_db.py::test_save_and_get_event -v
```

预期: PASS

### 1.3 按日期查询 + 日期列表

- [ ] **Step 9: 写失败测试 — 按日期查询和获取所有日期**

```python
# tests/test_db.py (追加)
def test_get_events_by_date_and_all_dates(tmp_path):
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
```

- [ ] **Step 10: 运行验证失败，写实现，验证通过**

```python
# db.py (追加方法)
    def get_events_by_date(self, date_str):
        rows = self.conn.execute(
            "SELECT * FROM emotion_events WHERE date(created_at)=? ORDER BY created_at",
            (date_str,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_event_dates(self):
        rows = self.conn.execute(
            "SELECT DISTINCT date(created_at) as d FROM emotion_events").fetchall()
        return [r['d'] for r in rows]
```

### 1.4 每日摘要 + 应用状态

- [ ] **Step 11: 写失败测试 — 每日摘要和状态键值对**

```python
# tests/test_db.py (追加)
def test_daily_summary_and_app_state(tmp_path):
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
```

- [ ] **Step 12: 运行验证失败，写实现，验证通过**

```python
# db.py (追加方法)
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
```

---

## Task 2: session.py — 会话控制器

**文件:**
- 创建: `session.py`
- 测试: `tests/test_session.py`

### 2.1 初始化与引擎状态恢复

- [ ] **Step 1: 写失败测试 — SessionController 初始化**

```python
# tests/test_session.py
import pytest, json, time

def test_session_init_with_db(tmp_path):
    """SessionController 初始化时创建引擎和数据库"""
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)

    assert session.engine is not None
    assert session.db is db
    db.close()
```

- [ ] **Step 2: 运行验证失败**

```bash
python -m pytest tests/test_session.py::test_session_init_with_db -v
```

- [ ] **Step 3: 写最小实现**

```python
# session.py
"""session.py — 心潮 EmoWave 会话控制器，桥接 UI 与引擎"""
from engine import EmoCalibrationEngine
from models import EmotionEventRaw, TimeSeriesSample, DailySummary
from recommender import ContextualBandit, extract_context, DEFAULT_STRATEGIES
import json, time, uuid
from datetime import datetime

class SessionController:
    def __init__(self, db):
        self.db = db
        self.engine = EmoCalibrationEngine(user_id="local_user")
        self.bandit = ContextualBandit(DEFAULT_STRATEGIES)
        self._restore_state()

    def _restore_state(self):
        """从数据库恢复引擎状态"""
        state_json = self.db.get_state('engine_state', '')
        if state_json:
            try:
                self.engine = EmoCalibrationEngine.load(state_json)
            except Exception:
                pass  # 恢复失败时使用新引擎
```

- [ ] **Step 4: 运行验证通过**

### 2.2 处理情绪事件

- [ ] **Step 5: 写失败测试 — 完整事件处理流程**

```python
# tests/test_session.py (追加)
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
```

- [ ] **Step 6: 运行验证失败，写实现**

```python
# session.py (追加方法)
    def process_event(self, samples, trigger_tags=None, coping_methods=None,
                      coping_ratings=None, body_symptoms=None, user_peak_rating=None):
        """处理一次情绪事件"""
        trigger_tags = trigger_tags or []
        coping_methods = coping_methods or []
        coping_ratings = coping_ratings or {}
        body_symptoms = body_symptoms or []

        raw_event = EmotionEventRaw(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            samples=samples,
            user_peak_rating=user_peak_rating,
            trigger_tags=trigger_tags,
            coping_methods=coping_methods,
            coping_ratings=coping_ratings,
            body_symptoms=body_symptoms,
            calm_timestamp=samples[-1].timestamp if samples else time.time(),
        )

        profile, thresholds = self.engine.process_event(raw_event)

        # 存入数据库
        event_dict = {
            'event_id': profile.event_id,
            'start_time': profile.onset_time,
            'end_time': profile.calm_time,
            'peak_valence': profile.peak_valence,
            'peak_arousal': profile.peak_arousal,
            'peak_intensity': profile.subjective_peak or 0.0,
            'sample_count': profile.sample_count,
            'trigger_tags': trigger_tags,
            'coping_methods': coping_methods,
            'coping_ratings': coping_ratings,
            'body_symptoms': body_symptoms,
            'user_peak_rating': user_peak_rating,
        }
        self.db.save_event(event_dict)

        # 持久化引擎状态
        self._save_state()

        return {
            'event_id': profile.event_id,
            'peak_valence': profile.peak_valence,
            'peak_arousal': profile.peak_arousal,
            'peak_intensity': profile.subjective_peak or 0.0,
            'recovery_duration': profile.recovery_duration,
            'onset_time': profile.onset_time,
            'peak_time': profile.peak_time,
            'calm_time': profile.calm_time,
            'sample_count': profile.sample_count,
            'thresholds': {
                'high_risk_arousal': thresholds.high_risk_arousal,
                'warning_arousal': thresholds.warning_arousal,
            },
        }

    def _save_state(self):
        self.db.set_state('engine_state', self.engine.serialize_state())
```

- [ ] **Step 7: 运行验证通过**

### 2.3 获取推荐策略

- [ ] **Step 8: 写失败测试 — 获取推荐**

```python
# tests/test_session.py (追加)
def test_get_recommendation(tmp_path):
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
```

- [ ] **Step 9: 运行验证失败，写实现**

```python
# session.py (追加方法)
    def get_recommendation(self, valence, arousal, hour, weekday, sleep):
        """获取策略推荐"""
        ctx = extract_context(
            current_valence=valence,
            current_arousal=arousal,
            time_of_day=hour,
            weekday=weekday,
            last_sleep_score=sleep,
            trigger_category_code=0,
        )
        rec = self.bandit.recommend(ctx)
        return {
            'strategy_id': rec.strategy_id,
            'strategy_name': rec.strategy_name,
            'predicted_score': rec.predicted_score,
            'uncertainty': rec.uncertainty,
        }

    def record_feedback(self, strategy_id, reward):
        """记录策略效果反馈"""
        self.bandit.update(strategy_id, reward)
```

### 2.4 获取仪表盘数据

- [ ] **Step 10: 写失败测试 — 仪表盘数据**

```python
# tests/test_session.py (追加)
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
```

- [ ] **Step 11: 运行验证失败，写实现**

```python
# session.py (追加方法)
    def get_dashboard_data(self):
        """获取仪表盘展示数据"""
        recent = self.db.get_recent_events(limit=5)
        today = datetime.now().strftime('%Y-%m-%d')
        today_summary = self.db.get_daily_summary(today)
        baseline = self.engine.get_baseline()

        return {
            'recent_events': recent,
            'today_summary': today_summary,
            'baseline': {
                'resting_hr': baseline.resting_hr,
                'resting_hrv': baseline.resting_hrv_mean,
                'sleep_score': baseline.sleep_score,
            },
            'thresholds': self._get_thresholds_dict(),
        }

    def _get_thresholds_dict(self):
        t = self.engine.get_thresholds()
        return {
            'warning_arousal': t.warning_arousal,
            'high_risk_arousal': t.high_risk_arousal,
            'model_confidence': getattr(t, 'model_confidence', 0.0),
        }
```

---

## Task 3: widgets.py — 共享自定义控件

**文件:**
- 创建: `widgets.py`
- 测试: `tests/test_widgets.py`

### 3.1 RiskRingWidget 风险环形指示器

- [ ] **Step 1: 写失败测试 — 环形指示器**

```python
# tests/test_widgets.py
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
```

- [ ] **Step 2: 运行验证失败**

```bash
python -m pytest tests/test_widgets.py::test_risk_ring_widget_creates -v
```

- [ ] **Step 3: 写最小实现**

```python
# widgets.py
"""widgets.py — 心潮 EmoWave 共享自定义控件"""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QColor, QRadialGradient, QFont

COLORS = {
    'bg': '#f6f4f0', 'surface': '#ffffff',
    'ink': '#2d2a26', 'muted': '#9a958e', 'rule': '#e0dbd4',
    'calm': '#2cb69a', 'warm': '#e8a838', 'warn': '#e06060', 'danger': '#c84848',
}

class RiskRingWidget(QWidget):
    """圆形风险进度环"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setMinimumSize(160, 160)
        self.setMaximumSize(200, 200)

    def set_value(self, v):
        self.value = max(0.0, min(1.0, v))
        self.update()

    def _color_for_value(self):
        if self.value < 0.4:
            return QColor(COLORS['calm'])
        elif self.value < 0.7:
            return QColor(COLORS['warm'])
        else:
            return QColor(COLORS['danger'])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 14

        # 背景环
        p.setPen(QPen(QColor(COLORS['rule']), 10, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 0, 360 * 16)

        # 进度环
        if self.value > 0:
            p.setPen(QPen(self._color_for_value(), 10, Qt.SolidLine, Qt.RoundCap))
            span = int(-self.value * 360 * 16)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, 90 * 16, span)

        # 中心文字
        p.setPen(QColor(COLORS['ink']))
        font = QFont("PingFang SC", 18, QFont.Bold)
        p.setFont(font)
        text = f"{int(self.value * 100)}"
        p.drawText(self.rect(), Qt.AlignCenter, text)
```

- [ ] **Step 4: 运行验证通过**

### 3.2 EmotionCanvas 2D 情绪平面

- [ ] **Step 5: 写失败测试 — 情绪画布**

```python
# tests/test_widgets.py (追加)
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
```

- [ ] **Step 6: 运行验证失败，写实现**

```python
# widgets.py (追加)
class EmotionCanvas(QWidget):
    """2D 效价-唤醒情绪平面画布"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.trail = []
        self.setMinimumSize(300, 300)

    def add_point(self, valence, arousal):
        self.trail.append((valence, arousal))
        if len(self.trail) > 500:
            self.trail = self.trail[-500:]
        self.update()

    def clear(self):
        self.trail = []
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 背景
        p.fillRect(self.rect(), QColor(COLORS['surface']))

        # 网格线
        p.setPen(QPen(QColor(COLORS['rule']), 1))
        p.drawLine(w // 2, 0, w // 2, h)
        p.drawLine(0, h // 2, w, h // 2)

        # 轴标签
        p.setPen(QColor(COLORS['muted']))
        p.setFont(QFont("PingFang SC", 9))
        p.drawText(w - 40, h - 5, "效价→")
        p.drawText(5, 15, "↑唤醒")

        # 轨迹
        if len(self.trail) >= 2:
            for i in range(1, len(self.trail)):
                v1, a1 = self.trail[i - 1]
                v2, a2 = self.trail[i]
                x1, y1 = int(v1 * w), int((1 - a1) * h)
                x2, y2 = int(v2 * w), int((1 - a2) * h)
                alpha = int(255 * (i / len(self.trail)))
                p.setPen(QPen(QColor(224, 96, 96, alpha), 3, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(x1, y1, x2, y2)

        # 当前点
        if self.trail:
            v, a = self.trail[-1]
            x, y = int(v * w), int((1 - a) * h)
            p.setBrush(QColor(COLORS['danger']))
            p.setPen(Qt.NoPen)
            p.drawEllipse(x - 8, y - 8, 16, 16)
```

- [ ] **Step 7: 运行验证通过**

### 3.3 CardFrame 卡片容器

- [ ] **Step 8: 写失败测试 — 卡片容器**

```python
# tests/test_widgets.py (追加)
def test_card_frame_creates(qapp):
    """CardFrame 可以创建并添加子控件"""
    from widgets import CardFrame
    from PyQt5.QtWidgets import QLabel
    card = CardFrame(title="今日概览")
    label = QLabel("测试内容")
    card.add_widget(label)
    assert card.title_label.text() == "今日概览"
```

- [ ] **Step 9: 运行验证失败，写实现**

```python
# widgets.py (追加)
from PyQt5.QtWidgets import QVBoxLayout, QLabel, QFrame

class CardFrame(QFrame):
    """带标题的卡片容器"""
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"""
            CardFrame {{
                background-color: {COLORS['surface']};
                border-radius: 12px;
                border: 1px solid {COLORS['rule']};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"color: {COLORS['ink']}; font-size: 14px; font-weight: bold;")
        layout.addWidget(self.title_label)

        self._content_layout = layout

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)
```

- [ ] **Step 10: 运行验证通过**

---

## Task 4: dashboard_window.py — 今日仪表盘

**文件:**
- 创建: `windows/dashboard_window.py`
- 测试: `tests/test_dashboard_window.py`

### 4.1 窗口创建与数据展示

- [ ] **Step 1: 写失败测试 — 仪表盘窗口创建**

```python
# tests/test_dashboard_window.py
import pytest

def test_dashboard_window_creates(qapp, tmp_path):
    """DashboardWindow 可以创建并显示数据"""
    from windows.dashboard_window import DashboardWindow
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)
    win = DashboardWindow(session)

    assert win.risk_ring is not None
    assert win.recent_events_list is not None

    # 空数据时不应崩溃
    win.refresh()
    db.close()
```

- [ ] **Step 2: 运行验证失败**

```bash
python -m pytest tests/test_dashboard_window.py::test_dashboard_window_creates -v
```

- [ ] **Step 3: 写最小实现**

```python
# windows/dashboard_window.py
"""windows/dashboard_window.py — 今日仪表盘窗口"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QScrollArea, QFrame, QPushButton, QSizePolicy,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from widgets import RiskRingWidget, CardFrame
from datetime import datetime

class DashboardWindow(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # 顶部标题
        title = QLabel("今日情绪天气")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2d2a26;")
        layout.addWidget(title)

        # 风险环 + 概要
        top_row = QHBoxLayout()
        self.risk_ring = RiskRingWidget()
        top_row.addWidget(self.risk_ring)

        info_card = CardFrame("今日概览")
        self.info_label = QLabel("加载中...")
        self.info_label.setStyleSheet("color: #5a5550; font-size: 13px;")
        info_card.add_widget(self.info_label)
        top_row.addWidget(info_card, stretch=1)
        layout.addLayout(top_row)

        # 最近事件列表
        events_card = CardFrame("最近事件")
        self.recent_events_list = QListWidget()
        self.recent_events_list.setMaximumHeight(200)
        events_card.add_widget(self.recent_events_list)
        layout.addWidget(events_card)

        # 快捷操作
        btn_row = QHBoxLayout()
        self.btn_record = QPushButton("开始记录情绪")
        self.btn_record.setStyleSheet("""
            QPushButton {
                background-color: #2cb69a; color: white;
                border-radius: 8px; padding: 10px 20px; font-size: 14px;
            }
        """)
        btn_row.addWidget(self.btn_record)
        layout.addLayout(btn_row)

        layout.addStretch()

    def refresh(self):
        """从 session 拉取最新数据并刷新界面"""
        data = self.session.get_dashboard_data()

        # 风险环：基于今日最高唤醒度
        summary = data.get('today_summary')
        if summary and summary.get('peak_arousal'):
            self.risk_ring.set_value(summary['peak_arousal'])
        else:
            self.risk_ring.set_value(0)

        # 概要文本
        baseline = data.get('baseline', {})
        self.info_label.setText(
            f"静息心率: {baseline.get('resting_hr', '--')} BPM\n"
            f"HRV: {baseline.get('resting_hrv', '--')} ms\n"
            f"睡眠评分: {baseline.get('sleep_score', '--')}/10"
        )

        # 事件列表
        self.recent_events_list.clear()
        for evt in data.get('recent_events', []):
            time_str = evt.get('created_at', '')[:16] or '--'
            arousal = evt.get('peak_arousal', 0)
            item_text = f"{time_str}  唤醒度: {arousal:.2f}"
            self.recent_events_list.addItem(QListWidgetItem(item_text))
```

- [ ] **Step 4: 运行验证通过**

---

## Task 5: surfing_window.py — 情绪冲浪记录

**文件:**
- 创建: `windows/surfing_window.py`
- 测试: `tests/test_surfing_window.py`

### 5.1 窗口创建与滑条交互

- [ ] **Step 1: 写失败测试 — 冲浪窗口创建**

```python
# tests/test_surfing_window.py
import pytest, time
from PyQt5.QtWidgets import QSlider

def test_surfing_window_creates(qapp, tmp_path):
    """SurfingWindow 可以创建并交互"""
    from windows.surfing_window import SurfingWindow
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)
    win = SurfingWindow(session)

    assert win.valence_slider is not None
    assert win.arousal_slider is not None
    assert win.canvas is not None
    assert win.start_btn is not None
    db.close()
```

- [ ] **Step 2: 运行验证失败**

- [ ] **Step 3: 写最小实现**

```python
# windows/surfing_window.py
"""windows/surfing_window.py — 情绪冲浪记录窗口"""
import time
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QListWidget, QLineEdit, QCheckBox, QFrame,
)
from PyQt5.QtCore import Qt, QTimer
from widgets import EmotionCanvas, CardFrame
from models import TimeSeriesSample

TRIGGER_OPTIONS = ['工作压力', '人际冲突', '健康担忧', '财务问题', '回忆触发', '未知']
COPING_OPTIONS = ['深呼吸', '短暂散步', '听音乐', '情绪日记', '联系朋友', '冷水洗脸']
BODY_OPTIONS = ['胸口压抑', '肩膀紧绷', '头痛', '手心出汗', '胃部不适', '心跳加速']

class SurfingWindow(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.recording = False
        self.samples = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._sample)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("情绪冲浪")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2d2a26;")
        layout.addWidget(title)

        # 情绪画布
        self.canvas = EmotionCanvas()
        self.canvas.setMinimumHeight(280)
        layout.addWidget(self.canvas)

        # 滑条区域
        slider_card = CardFrame("实时调节")
        self.valence_slider = QSlider(Qt.Horizontal)
        self.valence_slider.setRange(0, 100)
        self.valence_slider.setValue(60)
        self.valence_label = QLabel("效价: 0.60")
        self.valence_slider.valueChanged.connect(
            lambda v: self.valence_label.setText(f"效价: {v/100:.2f}")
        )
        slider_card.add_widget(self.valence_label)
        slider_card.add_widget(self.valence_slider)

        self.arousal_slider = QSlider(Qt.Horizontal)
        self.arousal_slider.setRange(0, 100)
        self.arousal_slider.setValue(30)
        self.arousal_label = QLabel("唤醒: 0.30")
        self.arousal_slider.valueChanged.connect(
            lambda v: self.arousal_label.setText(f"唤醒: {v/100:.2f}")
        )
        slider_card.add_widget(self.arousal_label)
        slider_card.add_widget(self.arousal_slider)
        layout.addWidget(slider_card)

        # 标签选择
        tags_card = CardFrame("标签（可多选）")
        self.trigger_checks = []
        for opt in TRIGGER_OPTIONS:
            cb = QCheckBox(opt)
            self.trigger_checks.append(cb)
            tags_card.add_widget(cb)
        layout.addWidget(tags_card)

        # 控制按钮
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始记录")
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #2cb69a; color: white;
                border-radius: 8px; padding: 10px 24px; font-size: 14px;
            }
        """)
        self.start_btn.clicked.connect(self._toggle_recording)
        btn_row.addWidget(self.start_btn)

        self.finish_btn = QPushButton("已平静")
        self.finish_btn.setStyleSheet("""
            QPushButton {
                background-color: #e8a838; color: white;
                border-radius: 8px; padding: 10px 24px; font-size: 14px;
            }
        """)
        self.finish_btn.clicked.connect(self._finish_recording)
        self.finish_btn.setEnabled(False)
        btn_row.addWidget(self.finish_btn)
        layout.addLayout(btn_row)

    def _toggle_recording(self):
        if not self.recording:
            self.recording = True
            self.samples = []
            self.canvas.clear()
            self.start_btn.setText("暂停")
            self.finish_btn.setEnabled(True)
            self._timer.start(1000)  # 每秒采样
        else:
            self.recording = False
            self.start_btn.setText("继续")
            self._timer.stop()

    def _sample(self):
        v = self.valence_slider.value() / 100.0
        a = self.arousal_slider.value() / 100.0
        ts = time.time()
        self.samples.append(TimeSeriesSample(timestamp=ts, valence=v, arousal=a))
        self.canvas.add_point(v, a)

    def _finish_recording(self):
        self._timer.stop()
        self.recording = False
        self.start_btn.setText("开始记录")
        self.finish_btn.setEnabled(False)

        if len(self.samples) < 2:
            return

        triggers = [cb.text() for cb in self.trigger_checks if cb.isChecked()]
        result = self.session.process_event(
            samples=self.samples,
            trigger_tags=triggers,
        )
        self.samples = []
        # 通知主窗口切换到事件回顾
        if hasattr(self.parent(), 'show_event_summary'):
            self.parent().show_event_summary(result.get('event_id'))
```

- [ ] **Step 4: 运行验证通过**

---

## Task 6: event_summary_window.py — 事件回顾

**文件:**
- 创建: `windows/event_summary_window.py`
- 测试: `tests/test_event_summary_window.py`

### 6.1 窗口创建与事件展示

- [ ] **Step 1: 写失败测试 — 事件回顾窗口**

```python
# tests/test_event_summary_window.py
import pytest

def test_event_summary_window_creates(qapp, tmp_path):
    """EventSummaryWindow 可以创建"""
    from windows.event_summary_window import EventSummaryWindow
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)
    win = EventSummaryWindow(session)

    assert win.profile_label is not None
    assert win.curve_canvas is not None

    # 空数据不崩溃
    win.show_event("nonexistent_id")
    db.close()
```

- [ ] **Step 2: 运行验证失败**

- [ ] **Step 3: 写最小实现**

```python
# windows/event_summary_window.py
"""windows/event_summary_window.py — 事件回顾窗口"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QColor, QFont
from widgets import CardFrame

class _CurveCanvas(QWidget):
    """简单的情绪曲线画布"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = []
        self.setMinimumHeight(180)

    def set_data(self, points):
        self.points = points
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor('#ffffff'))

        if len(self.points) < 2:
            p.setPen(QColor('#9a958e'))
            p.setFont(QFont("PingFang SC", 11))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            return

        # 画 arousal 曲线
        p.setPen(QPen(QColor('#e06060'), 2))
        max_val = max(self.points) if max(self.points) > 0 else 1.0
        for i in range(1, len(self.points)):
            x1 = int((i - 1) / (len(self.points) - 1) * w)
            y1 = int((1 - self.points[i - 1] / max_val) * (h - 20)) + 10
            x2 = int(i / (len(self.points) - 1) * w)
            y2 = int((1 - self.points[i] / max_val) * (h - 20)) + 10
            p.drawLine(x1, y1, x2, y2)


class EventSummaryWindow(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("事件回顾")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2d2a26;")
        layout.addWidget(title)

        # 事件概况
        profile_card = CardFrame("事件概况")
        self.profile_label = QLabel("请从历史记录中选择一个事件查看")
        self.profile_label.setStyleSheet("color: #5a5550; font-size: 13px;")
        profile_card.add_widget(self.profile_label)
        layout.addWidget(profile_card)

        # 情绪曲线
        curve_card = CardFrame("情绪曲线")
        self.curve_canvas = _CurveCanvas()
        curve_card.add_widget(self.curve_canvas)
        layout.addWidget(curve_card)

        # 躯体症状
        body_card = CardFrame("躯体症状")
        self.body_label = QLabel("--")
        self.body_label.setStyleSheet("color: #5a5550; font-size: 13px;")
        body_card.add_widget(self.body_label)
        layout.addWidget(body_card)

        layout.addStretch()

    def show_event(self, event_id):
        """显示指定事件的回顾数据"""
        events = self.session.db.get_recent_events(limit=50)
        evt = next((e for e in events if e.get('event_id') == event_id), None)

        if evt is None:
            self.profile_label.setText("未找到该事件")
            self.curve_canvas.set_data([])
            return

        # 概况文本
        import json
        triggers = json.loads(evt.get('trigger_tags', '[]'))
        triggers_str = '、'.join(triggers) if triggers else '未标记'

        self.profile_label.setText(
            f"峰值唤醒度: {evt.get('peak_arousal', 0):.2f}\n"
            f"峰值效价: {evt.get('peak_valence', 0):.2f}\n"
            f"采样点数: {evt.get('sample_count', 0)}\n"
            f"触发因素: {triggers_str}\n"
            f"用户自评: {evt.get('user_peak_rating', '--')}"
        )

        # 躯体症状
        symptoms = json.loads(evt.get('body_symptoms', '[]'))
        self.body_label.setText('、'.join(symptoms) if symptoms else '无记录')

        # 曲线（简化：用 raw_data 如果有）
        self.curve_canvas.set_data([
            evt.get('peak_arousal', 0) * 0.3,
            evt.get('peak_arousal', 0) * 0.6,
            evt.get('peak_arousal', 0),
            evt.get('peak_arousal', 0) * 0.7,
            evt.get('peak_arousal', 0) * 0.4,
        ])
```

- [ ] **Step 4: 运行验证通过**

---

## Task 7: history_window.py — 历史记录与报告

**文件:**
- 创建: `windows/history_window.py`
- 测试: `tests/test_history_window.py`

### 7.1 窗口创建与日历展示

- [ ] **Step 1: 写失败测试 — 历史窗口创建**

```python
# tests/test_history_window.py
import pytest

def test_history_window_creates(qapp, tmp_path):
    """HistoryWindow 可以创建"""
    from windows.history_window import HistoryWindow
    from session import SessionController
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    session = SessionController(db)
    win = HistoryWindow(session)

    assert win.calendar is not None
    assert win.events_table is not None

    # 空数据不崩溃
    win.refresh()
    db.close()
```

- [ ] **Step 2: 运行验证失败**

- [ ] **Step 3: 写最小实现**

```python
# windows/history_window.py
"""windows/history_window.py — 历史记录与报告窗口"""
import json
from datetime import datetime
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCalendarWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView,
)
from PyQt5.QtCore import Qt, QDate
from widgets import CardFrame

class HistoryWindow(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._event_dates = set()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("历史记录")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2d2a26;")
        layout.addWidget(title)

        # 日历
        cal_card = CardFrame("情绪日历")
        self.calendar = QCalendarWidget()
        self.calendar.setMaximumHeight(240)
        self.calendar.clicked.connect(self._on_date_clicked)
        cal_card.add_widget(self.calendar)
        layout.addWidget(cal_card)

        # 事件表格
        table_card = CardFrame("事件列表")
        self.events_table = QTableWidget()
        self.events_table.setColumnCount(4)
        self.events_table.setHorizontalHeaderLabels(["时间", "峰值唤醒", "触发因素", "自评"])
        self.events_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.events_table.setMaximumHeight(200)
        self.events_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table_card.add_widget(self.events_table)
        layout.addWidget(table_card)

        # 导出按钮
        self.export_btn = QPushButton("导出周报")
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #5a9fc8; color: white;
                border-radius: 8px; padding: 10px 24px; font-size: 14px;
            }
        """)
        layout.addWidget(self.export_btn)

        layout.addStretch()

    def refresh(self):
        """刷新日历标记"""
        dates = self.session.db.get_all_event_dates()
        self._event_dates = set(dates)
        today = QDate.currentDate()
        self._on_date_clicked(today)

    def _on_date_clicked(self, qdate):
        date_str = qdate.toString("yyyy-MM-dd")
        events = self.session.db.get_events_by_date(date_str)
        self.events_table.setRowCount(len(events))

        for i, evt in enumerate(events):
            time_str = evt.get('created_at', '')[:16] or '--'
            self.events_table.setItem(i, 0, QTableWidgetItem(time_str))
            self.events_table.setItem(i, 1, QTableWidgetItem(f"{evt.get('peak_arousal', 0):.2f}"))
            triggers = json.loads(evt.get('trigger_tags', '[]'))
            self.events_table.setItem(i, 2, QTableWidgetItem('、'.join(triggers)))
            rating = evt.get('user_peak_rating')
            self.events_table.setItem(i, 3, QTableWidgetItem(str(rating) if rating else '--"))
```

- [ ] **Step 4: 运行验证通过**

---

## Task 8: main_app.py — 主窗口集成

**文件:**
- 修改: `main_app.py`
- 测试: 手动验证（GUI 集成测试在无头环境中有限制）

### 8.1 集成所有窗口

- [ ] **Step 1: 写失败测试 — 主窗口集成**

```python
# tests/test_main_app.py
import pytest

def test_main_window_creates_with_all_pages(qapp, tmp_path):
    """MainWindow 集成了所有四个功能页面"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)
    
    assert win.stack.count() == 4  # 四个页面
    assert win.dashboard_page is not None
    assert win.surfing_page is not None
    assert win.summary_page is not None
    assert win.history_page is not None
    db.close()
```

- [ ] **Step 2: 运行验证失败**

```bash
python -m pytest tests/test_main_app.py::test_main_window_creates_with_all_pages -v
```

- [ ] **Step 3: 重写 main_app.py**

```python
#!/usr/bin/env python3
"""心潮 EmoWave 桌面情绪追踪应用入口"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QStackedWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from db import DatabaseManager
from session import SessionController
from windows.dashboard_window import DashboardWindow
from windows.surfing_window import SurfingWindow
from windows.event_summary_window import EventSummaryWindow
from windows.history_window import HistoryWindow

STYLE_SHEET = """
QMainWindow { background-color: #f6f4f0; }
QListWidget { border: none; background-color: transparent; }
QPushButton:hover { opacity: 0.85; }
"""


class MainWindow(QMainWindow):
    def __init__(self, db=None):
        super().__init__()
        self.setWindowTitle("心潮 EmoWave · 情绪追踪")
        self.setMinimumSize(900, 650)
        self.setStyleSheet(STYLE_SHEET)

        # 初始化数据库和会话
        if db is None:
            db = DatabaseManager()
        self.db = db
        self.session = SessionController(db)

        # 中心区域
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 侧边栏
        sidebar = self._build_sidebar()
        main_layout.addWidget(sidebar)

        # 页面栈
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, stretch=1)

        # 创建四个页面
        self.dashboard_page = DashboardWindow(self.session)
        self.surfing_page = SurfingWindow(self.session)
        self.summary_page = EventSummaryWindow(self.session)
        self.history_page = HistoryWindow(self.session)

        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.surfing_page)
        self.stack.addWidget(self.summary_page)
        self.stack.addWidget(self.history_page)

        self._setup_menu()
        self._switch_to(0)

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("""
            QFrame { background-color: #2d2a26; border: none; }
            QLabel { color: #e8e4dc; font-size: 16px; font-weight: bold; }
            QPushButton {
                color: #c0bbb4; text-align: left; padding: 12px 16px;
                border: none; font-size: 14px; background: transparent;
            }
            QPushButton:checked { background-color: #3d3a36; color: #ffffff; }
        """)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(0)

        logo = QLabel("  心潮 EmoWave")
        logo.setFixedHeight(50)
        layout.addWidget(logo)
        layout.addSpacing(20)

        self.nav_buttons = []
        nav_items = [
            ("今日仪表盘", 0),
            ("情绪冲浪", 1),
            ("事件回顾", 2),
            ("历史记录", 3),
        ]
        for label, idx in nav_items:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, i=idx: self._switch_to(i))
            self.nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # 版本信息
        ver = QLabel("  v0.1.0")
        ver.setStyleSheet("color: #6a655e; font-size: 11px;")
        layout.addWidget(ver)

        return sidebar

    def _switch_to(self, index):
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        # 切换到页面时刷新数据
        page = self.stack.widget(index)
        if hasattr(page, 'refresh'):
            page.refresh()

    def _setup_menu(self):
        bar = self.menuBar()

        file_menu = bar.addMenu("文件(&F)")
        file_menu.addAction("导出数据", self._export_data)
        file_menu.addAction("退出", self.close)

        view_menu = bar.addMenu("视图(&V)")
        view_menu.addAction("今日仪表盘", lambda: self._switch_to(0))
        view_menu.addAction("情绪冲浪", lambda: self._switch_to(1))
        view_menu.addAction("事件回顾", lambda: self._switch_to(2))
        view_menu.addAction("历史记录", lambda: self._switch_to(3))

        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self._show_about)

    def show_event_summary(self, event_id):
        """从冲浪页面跳转到事件回顾"""
        self.summary_page.show_event(event_id)
        self._switch_to(2)

    def _export_data(self):
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getSaveFileName(self, "导出数据", "", "JSON Files (*.json)")
        if path:
            events = self.db.get_recent_events(limit=10000)
            import json
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出成功", f"已导出 {len(events)} 条记录到:\n{path}")

    def _show_about(self):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.about(self, "关于心潮",
            "心潮 EmoWave v0.1.0\n\n"
            "本地情绪追踪与校准引擎\n"
            "所有数据仅存储在设备本地\n"
            "不上传任何个人信息")


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("PingFang SC", 11))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行验证通过**

```bash
python -m pytest tests/test_main_app.py::test_main_window_creates_with_all_pages -v
```

### 8.2 冲浪页面跳转

- [ ] **Step 5: 写失败测试 — 完成记录后跳转"""

```python
# tests/test_main_app.py (追加)
def test_surfing_finish_navigates_to_summary(qapp, tmp_path):
    """完成情绪记录后自动跳转到事件回顾页面"""
    import main_app
    from db import DatabaseManager
    import time
    from models import TimeSeriesSample

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = MainWindow(db)

    # 切换到冲浪页面
    win._switch_to(1)
    surfing = win.surfing_page

    # 模拟开始记录
    surfing._toggle_recording()
    assert surfing.recording is True

    # 模拟采样
    now = time.time()
    for i in range(5):
        surfing.samples.append(
            TimeSeriesSample(timestamp=now + i, valence=0.5 - i*0.05, arousal=0.3 + i*0.1)
        )

    # 完成记录
    surfing._finish_recording()

    # 应该跳转到事件回顾页面
    assert win.stack.currentIndex() == 2
    db.close()
```

- [ ] **Step 6: 运行验证通过（如果 SurfingWindow 的 parent 引用正确）**

---

## Task 9: 全部测试通过 + 最终验证

- [ ] **Step 1: 运行所有测试**

```bash
cd /workspace/emowave-engine && python -m pytest tests/ -v
```

- [ ] **Step 2: 运行应用（在桌面环境中）**

```bash
cd /workspace/emowave-engine && python main_app.py
```

- [ ] **Step 3: 创建 README.md**

```markdown
# 心潮 EmoWave · 桌面情绪追踪应用

## 快速开始

```bash
pip install -r requirements.txt
python main_app.py
```

## 功能

- **今日仪表盘**: 情绪风险环、基线数据、最近事件
- **情绪冲浪**: 实时效价-唤醒滑条记录，2D 轨迹可视化
- **事件回顾**: 单次事件的曲线、标签、躯体症状
- **历史记录**: 日历视图、事件表格、数据导出

## 架构

三层架构: UI (PyQt5) → Controller (session.py) → Persistence (db.py)
引擎层: models, kalman_filter, predictor, recommender, engine

## 测试

```bash
python -m pytest tests/ -v
```
```

---

## 实现顺序总结

| 顺序 | 模块 | 测试文件 | TDD 循环数 |
|------|------|----------|-----------|
| 0 | 清理未完成代码 | -- | -- |
| 1 | db.py | test_db.py | 4 |
| 2 | session.py | test_session.py | 4 |
| 3 | widgets.py | test_widgets.py | 3 |
| 4 | dashboard_window.py | test_dashboard_window.py | 1 |
| 5 | surfing_window.py | test_surfing_window.py | 1 |
| 6 | event_summary_window.py | test_event_summary_window.py | 1 |
| 7 | history_window.py | test_history_window.py | 1 |
| 8 | main_app.py | test_main_app.py | 2 |
| 9 | 最终验证 + README | -- | -- |

总计约 17 个 TDD 循环，每个循环包含：写测试 → 看失败 → 写实现 → 看通过。
