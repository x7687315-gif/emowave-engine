"""datasource.py — 真实内核 + 多天演示数据（临时库，退出即删）

主屏曲线照旧由 `ConsolePresenter` 产出；历史页的事件也是**真的走了一遍
"开始记录 → 采样 → 停止"**，不是手写进数据库的假行。
唯一的人工处理：演示库里把 `created_at` 归一到 `start_time`，
否则所有事件都会落在"今天"，历史页看不出按天分组的结构。
"""
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta

from PyQt5.QtCore import QObject, pyqtSignal

#: 今天这一波：跨约 8 小时，午后低谷、傍晚回升
TODAY = [
    (45 * 60, 0.52, 0.40), (35 * 60, 0.58, 0.45), (60 * 60, 0.62, 0.55),
    (25 * 60, 0.44, 0.62), (40 * 60, 0.30, 0.70), (55 * 60, 0.24, 0.66),
    (30 * 60, 0.34, 0.52), (65 * 60, 0.46, 0.44), (50 * 60, 0.60, 0.48),
    (40 * 60, 0.66, 0.52), (35 * 60, 0.70, 0.58),
]
CORRECTIONS = [(3, 0.55, 0.50), (6, 0.42, 0.45)]

#: 前两天各一段短会话（用于历史页的分天结构）
PAST = [
    (2, [(0.44, 0.52), (0.58, 0.60), (0.50, 0.48), (0.66, 0.55)], 25 * 60),
    (1, [(0.62, 0.44), (0.40, 0.66), (0.36, 0.58)], 20 * 60),
]


class FakeClock:
    def __init__(self, start):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += float(seconds)
        return self.t


def demo_base():
    """演示日必须全在过去：`UserCorrection` 校验 created_at ≥ timestamp。"""
    today9 = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    span = sum(dt for dt, _v, _a in TODAY) + 600
    if today9.timestamp() + span < time.time():
        return today9.timestamp()
    return (today9 - timedelta(days=1)).timestamp()


class DemoSource(QObject):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from db import DatabaseManager
        from emowave import DEFAULT_ARCHETYPE_KEY
        from presenters.console_presenter import ConsolePresenter

        self._tmp = tempfile.mkdtemp(prefix='emowave-ui03-')
        self.db = DatabaseManager(os.path.join(self._tmp, 'demo.db'))
        self.clock = FakeClock(demo_base())
        self.p = ConsolePresenter(db=self.db, archetype_key=DEFAULT_ARCHETYPE_KEY,
                                  clock=self.clock)
        # 曲线取**推送**的那一份：衰减尾巴由 _emit_curve(points=...) 增量推来，
        # curve_payload() 只按观察重算，拿不到尾巴。
        self._curve = None
        self.p.curve_changed.connect(self._on_curve)
        self._seed_past()
        self._seed_today()

    def _on_curve(self, payload):
        self._curve = payload

    # ------------------------------------------------------------
    def _session(self, samples, step, lead=0):
        p = self.p
        if lead:
            self.clock.advance(lead)
        p.toggle_recording(True)
        for v, a in samples:
            self.clock.advance(step)
            p.set_input(v, a)
            p.sample(v, a)
        p.toggle_recording(False)
        self.clock.advance(60)

    def _seed_past(self):
        base = demo_base()
        for days, samples, lead in PAST:
            self.clock.t = base - days * 86400 + 10 * 3600
            self._session(samples, 20 * 60)
        # 演示归一：created_at 跟随 start_time，历史页才能按天分组
        self.db.conn.execute(
            "UPDATE emotion_events SET created_at = "
            "strftime('%Y-%m-%dT%H:%M:%S', start_time, 'unixepoch', 'localtime')")
        self.db.conn.commit()
        self.clock.t = base

    def _seed_today(self):
        p = self.p
        p.toggle_recording(True)
        for dt, v, a in TODAY:
            self.clock.advance(dt)
            p.set_input(v, a)
            p.sample(v, a)
        ts_list = [t for t, _ in p.states]
        p.ingest_corrections([(ts_list[i], v, a)
                              for i, v, a in CORRECTIONS if i < len(ts_list)])
        p.toggle_recording(False)
        # 5 分钟外推：再长会被 settled 判定结束并清空曲线（SETTLE_EPS=0.05）
        self.clock.advance(5 * 60)
        p.decay_step()
        self.db.conn.execute(
            "UPDATE emotion_events SET created_at = "
            "strftime('%Y-%m-%dT%H:%M:%S', start_time, 'unixepoch', 'localtime')")
        self.db.conn.commit()

    # ------------------------------------------------------------
    def snapshot(self):
        from windows.state_head import derive_state_name

        p = self.p
        v, a, conf, ci, _params, state_ts, last_obs = p.readout_payload()
        baseline = float(p.baseline_ctrl.current.valence)
        age = int(max(0, (self.clock() - last_obs) / 60.0)) if last_obs else 0
        return {
            'v': float(v), 'a': float(a), 'conf': float(conf), 'ci': ci,
            'state_ts': float(state_ts),
            'state_name': derive_state_name(float(v), float(a)),
            'deviation': int(round((float(v) - baseline) * 100)),
            'baseline_v': baseline,
            'curve': self._curve or p.curve_payload(),
            'recording': bool(p.recording),
            'count': int(p.current_count() or len(p.observations)),
            'age_min': age,
        }

    def model_snapshot(self):
        p = self.p
        params = p.params
        stage = getattr(params, 'stage', None)
        return {
            'baseline_v': float(p.baseline_ctrl.current.valence),
            'n_events': int(getattr(params, 'n_events_fitted', 0)),
            'ell': float(getattr(params, 'ell_valence', 0)),
            'stage': getattr(stage, 'value', '—'),
            'shrink': int(getattr(params, 'shrinkage_alpha', 0) * 100),
            'corrections': len(p.corrections),
        }

    def history(self):
        from datetime import date
        rows = self.db.get_recent_events(limit=12)
        groups, order = {}, []
        for r in rows:
            day = (r.get('created_at') or '')[:10]
            if day not in groups:
                groups[day] = []
                order.append(day)
            groups[day].append(r)
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        out = []
        for day in order:
            label = ('今天' if day == today else
                     '昨天' if day == yesterday else
                     _month_day(day))
            out.append({'date': day, 'label': label,
                        'events': [_event_row(r) for r in groups[day]]})
        return out

    # ---- 交互 ----
    def toggle_recording(self):
        self.p.toggle_recording()
        self.changed.emit()

    def commit_note(self):
        self.p.commit_now()
        self.changed.emit()

    def submit_input(self, valence=None, arousal=None):
        self.p.submit_input(valence=valence, arousal=arousal, source='proto')
        self.changed.emit()

    def nudge(self, delta):
        self.p.nudge(delta)
        self.changed.emit()

    def fork(self):
        self.p.fork()
        self.changed.emit()

    def reset_baseline(self):
        self.p.reset()
        self.changed.emit()

    def learn(self):
        self.p.learn()
        self.changed.emit()

    def correction_context(self):
        return self.p.correction_context()

    def close(self):
        try:
            self.p.stop_all_timers()
            self.db.close()
        finally:
            shutil.rmtree(self._tmp, ignore_errors=True)


def _event_row(r):
    return {
        't0': _clock(r.get('start_time')),
        't1': _clock(r.get('end_time')),
        'peak': int(round((r.get('peak_valence') or 0) * 100)),
        'arousal': int(round((r.get('peak_arousal') or 0) * 100)),
        'count': int(r.get('sample_count') or 0),
    }


def _clock(ts):
    if not ts:
        return '—'
    return datetime.fromtimestamp(float(ts)).strftime('%H:%M')


def _month_day(day):
    try:
        d = datetime.strptime(day, '%Y-%m-%d')
        return '%d 月 %d 日' % (d.month, d.day)
    except Exception:
        return day
