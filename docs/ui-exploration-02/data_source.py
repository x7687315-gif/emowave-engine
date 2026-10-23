"""data_source.py — 用**真实内核**造演示数据，两套布局共用同一份

为什么不去手搓一份假曲线：
两套方案要比较的是"同一份数据在不同结构里怎么被理解"。如果曲线是我画的示意线，
那比较的就是两张插画，不是两个界面。所以这里走生产同一条链路：

    ConsolePresenter（真 presenter）
      → Matérn ν=3/2 状态空间 + 因果 Kalman + RTS 平滑
      → curve_payload() 给出 ts / mv / vv / raw / baseline / predicted_from / corrections

布局只做两件事：读 payload、决定怎么排。不重算、不平滑、不改语义。

数据库落在临时目录，进程结束即删——**绝不碰 `~/.emowave/` 下的真实数据**。
"""
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta

from PyQt5.QtCore import QObject, pyqtSignal

#: 演示数据：一个工作日（09:00 起，跨约 8 小时）
DEMO = [
    (45 * 60, 0.52, 0.40),
    (35 * 60, 0.58, 0.45),
    (60 * 60, 0.62, 0.55),
    (25 * 60, 0.44, 0.62),
    (40 * 60, 0.30, 0.70),
    (55 * 60, 0.24, 0.66),
    (30 * 60, 0.34, 0.52),
    (65 * 60, 0.46, 0.44),
    (50 * 60, 0.60, 0.48),
    (40 * 60, 0.66, 0.52),
    (35 * 60, 0.70, 0.58),
]

CORRECTIONS = [(3, 0.55, 0.50), (6, 0.42, 0.45)]


class FakeClock:
    def __init__(self, start):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += float(seconds)
        return self.t


def demo_base():
    """演示日必须**完全在过去**。

    两个硬约束：
    1. `UserCorrection` 校验 created_at ≥ timestamp，而 created_at 取真实墙钟；
       演示数据一旦落在未来，纠正会直接抛 ValueError。
    2. A/B 各起一个进程采集，起点必须逐秒一致，否则两边的时间轴不是同一条。

    所以取"今天 09:00"，若它加上整个演示跨度后仍超过现在（例如凌晨运行），
    就退到昨天 09:00。
    """
    today9 = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    span = sum(dt for dt, _v, _a in DEMO) + 600
    if today9.timestamp() + span < time.time():
        return today9.timestamp()
    return (today9 - timedelta(days=1)).timestamp()


class DemoSource(QObject):
    """presenter 的门面：视图只跟它要"此刻的快照"，不碰私有字段。"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from db import DatabaseManager
        from emowave import DEFAULT_ARCHETYPE_KEY
        from presenters.console_presenter import ConsolePresenter

        self._tmp = tempfile.mkdtemp(prefix='emowave-ui02-')
        self.db = DatabaseManager(os.path.join(self._tmp, 'demo.db'))
        self.clock = FakeClock(demo_base())
        self.p = ConsolePresenter(db=self.db, archetype_key=DEFAULT_ARCHETYPE_KEY,
                                  clock=self.clock)
        # 曲线必须取**推送**的那一份，而不是 `curve_payload()`：
        # 衰减中的尾巴由 `_emit_curve(points=...)` 增量推出，
        # 而 `curve_payload()` 只按观察重新 RTS 平滑，拿不到尾巴。
        # 拿错的话，"现在"的读数会落在曲线末端之外（方案 B 的水位线会悬空）。
        self._curve = None
        self.p.curve_changed.connect(self._on_curve)
        self._seed()

    def _on_curve(self, payload):
        self._curve = payload

    # ------------------------------------------------------------
    def _seed(self):
        p = self.p
        p.toggle_recording(True)
        for dt, v, a in DEMO:
            self.clock.advance(dt)
            p.set_input(v, a)
            p.sample(v, a)
        ts_list = [t for t, _ in p.states]
        applied = [(ts_list[i], v, a) for i, v, a in CORRECTIONS
                   if i < len(ts_list)]
        p.ingest_corrections(applied)
        # 停止记录 → 进入衰减，曲线才有"截止 + 预测"的分界。
        # 5 分钟是实测出来的安全值：再长（8.5 分钟试过）会被 `settled`
        # 判定为"已回归基线"，`_end_wave()` 直接把整条曲线清掉——
        # 截图就只剩一张空图。上界由 SETTLE_EPS=0.05 与 ℓ 共同决定。
        p.toggle_recording(False)
        self.clock.advance(5 * 60)
        p.decay_step()

    # ------------------------------------------------------------
    def snapshot(self):
        from windows.state_head import derive_state_name

        p = self.p
        v, a, conf, ci, params, state_ts, last_obs = p.readout_payload()
        baseline = float(p.baseline_ctrl.current.valence)
        age_min = int(max(0, (self.clock() - last_obs) / 60.0)) if last_obs else 0
        return {
            'v': float(v), 'a': float(a), 'conf': float(conf), 'ci': ci,
            'state_ts': float(state_ts), 'last_obs_ts': float(last_obs),
            'state_name': derive_state_name(float(v), float(a)),
            'deviation': int(round((float(v) - baseline) * 100)),
            'baseline_v': baseline,
            'curve': self._curve or p.curve_payload(),
            'recording': bool(p.recording),
            'mode': p.current_mode(),
            'count': int(p.current_count() or len(p.observations)),
            'age_min': age_min,
        }

    # ---- 交互（真调 presenter，不是摆样子）----
    def toggle_recording(self):
        self.p.toggle_recording()
        self.changed.emit()

    def commit_note(self):
        self.p.commit_now()
        self.changed.emit()

    def set_input(self, valence=None, arousal=None):
        self.p.set_input(valence=valence, arousal=arousal, source='proto')
        self.changed.emit()

    def submit_input(self, valence=None, arousal=None):
        """记录态下拖动 = 真实自评（走 350ms 防抖，与生产一致）。"""
        self.p.submit_input(valence=valence, arousal=arousal, source='proto')
        self.changed.emit()

    def correction_context(self):
        return self.p.correction_context()

    def start_session(self, step=120, samples=((0.62, 0.55), (0.58, 0.60),
                                              (0.50, 0.64))):
        """开始一次**新的**记录。

        注意生产语义：开始新一波会先结束上一波，`_end_wave()` 清空 states /
        observations（曲线消失，状态保留）。所以截图流程必须把"记录中"排在
        最后拍——拍完这一张，前一天那条曲线就没了。
        """
        self.p.toggle_recording(True)
        for v, a in samples:
            self.clock.advance(step)
            self.p.set_input(v, a)
            self.p.sample(v, a)
        self.changed.emit()

    def close(self):
        try:
            self.p.stop_all_timers()
            self.db.close()
        finally:
            shutil.rmtree(self._tmp, ignore_errors=True)
