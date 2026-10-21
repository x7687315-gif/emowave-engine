"""presenters/console_presenter.py — 主控制台的内核编排层

=== 职责边界 ===
本类**不 import 任何 PyQt5.QtWidgets**（只允许 QtCore 的 QObject/QTimer/pyqtSignal），
也就是 MVP 里"可被 mock、可在无 GUI 环境下单测"的那一层。它：

  · 持有全部内核对象（estimator / baseline_ctrl / calibrator / observations …）
  · 持有 1Hz 心跳 QTimer —— **心跳只回答"现在几点"，不回答"用户说了什么"**
  · 把结果用**信号**推给视图；输入（滑条当前值）由调用方以参数显式传入
    —— 这样第二个视图（迷你置顶窗）可以复用同一个 presenter 而不抢状态

=== v4 Phase 1：时间与观察语义 ===
1. 观察只由用户动作产生：滑条走 `submit_input()` → `ObservationRecorder`
   防抖 350ms 后提交一次；心跳不再 `sample()`（此前 10 次心跳 = 10 条同值观察）。
2. 模型时间跟墙钟：`horizon = scheduler.now() - last_obs_ts`，
   不再 `_decay_now += DECAY_DT`（此前真实过 1.2s、模型只走 1.0s）。
3. 状态与动画解耦：`latest_state` 常驻并落盘；`_end_wave()` 只结束动画。
4. 恢复按真实经过时间外推；超过 `MAX_DECAY_SEC` 直接结束动画（状态保留）。

具体职责分工见同目录的 observation_recorder / state_query / wave_scheduler /
snapshot_store 四个对象。

=== 与 v3 的差异（阶段 3 解耦 + 换内核 API）===
1. v3 的 `MainConsole._decay_points()` **手写了一份 Matérn 衰减外推**
   （e^(-λΔt)(1+λΔt)），与内核 `emowave.core.inference.dynamics.predict_forward`
   是同一套数学，但内核版多做两件正确的事：在**偏差空间**传播（相对基线而非
   相对 0）、每步叠过程噪声 Q（外推越远方差越大 → "越预测越不确定"）。
   → 本类直接调 `predict_forward`，删掉手写实现。
2. v3 的 UI 直接 `db.save_event()` / `estimator.update()`；现在统一走本类。
"""
import json
import logging
import math
import os
import time
import uuid

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from .engine import (
    Observation, ObservationSource, UserCorrection,
    CorrectionDimension, CorrectionSource,
    get_archetype, DEFAULT_ARCHETYPE_KEY,
    Baseline, BaselineSource,
    StateEstimator, BaselineController, Calibrator,
    predict_forward,
)
from .observation_recorder import ObservationRecorder
from .snapshot_store import SnapshotStore
from .state_query import StateQuery
from .wave_scheduler import WaveScheduler

logger = logging.getLogger(__name__)

#: 显示修正的权重（RTS 里的观测置信度，上限 1.0）。
#: **刻意与学习权重分开**：`UserCorrection.reliability_weight` 的 docstring 写明
#: 它是"学习算法加权梯度贡献"用的，按来源/时近/显著性算出（DRAG 约 0.36）。
#: 拿它当显示权重会让用户拖完发现"曲线几乎没动"——实测 0.513→0.313 才是
#: 用户能感知的响应；用 0.36 时只到 0.513，等于没改。
#: 即时纠正 = 让曲线反映我此刻的意图（本权重）；个性化学习 = 逐步改善预测
#: （仍用 reliability_weight）。两件事分开，才既跟手又不过拟合。
DISPLAY_CORRECTION_WEIGHT = 1.0

# ---- 情绪波生命周期常量（记录→衰减回基线→消失→待下次记录）----
# 从 v3 的 main_console.py 原样搬来，并在那里保留同名 re-export 以兼容旧引用。
SETTLE_EPS = 0.05        # 距基线小于此值视为"回归基线"
SETTLE_MIN_SEC = 3.0     # 至少衰减这么久才允许判定结束（避免刚停就消失）
MAX_DECAY_SEC = 600.0    # 衰减外推的硬上限（防止无限外推）
DECAY_DT = 1.0           # 外推的采样步长（秒）——只影响绘图点密度，不再是"时间来源"

class ConsolePresenter(QObject):
    """主控制台的内核编排。视图只接信号、只传参数。"""

    # ---- 推给视图的信号 ----
    # v5：尾部两个时间戳让 UI 能回答"这个数是几点的 / 离上次自评多久"
    readouts_changed = pyqtSignal(float, float, float, str, dict, float, float)
    readouts_reset = pyqtSignal()
    curve_changed = pyqtSignal(dict)      # {ts, mv, ma, vv, va, baseline_v, raw}
    curve_cleared = pyqtSignal()
    mode_changed = pyqtSignal(str)        # idle / recording / decaying
    count_changed = pyqtSignal(int)
    history_changed = pyqtSignal()        # 有新事件落库 → 历史页该刷新了
    input_changed = pyqtSignal(float, float)   # 输入源变更 → 各视图镜像滑杆
    baseline_changed = pyqtSignal(float, float)   # 基线变了 → 基线卡显示该刷新
    #: 用户可见的降级/损坏提示：(severity, text)。severity 目前只用 "warn"。
    notice_changed = pyqtSignal(str, str)

    def __init__(self, db=None, archetype_key=None, clock=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.notices = []          # 构造期发出的提示先攒着，晚挂载的视图也能看到

        # ---- 时间与查询（v4 Phase 1：全应用唯一的时间源）----
        self.scheduler = WaveScheduler(clock=clock)
        self.query = StateQuery(lambda: self.estimator, self.scheduler.now,
                                lambda: self.baseline_ctrl.current)
        self.recorder = ObservationRecorder(clock=self.scheduler.now)
        self.recorder.committed.connect(self._on_committed)
        self.store = SnapshotStore(db, warn=self._notice)
        #: 数据库写失败时的恢复文件目录（测试可覆盖）
        self._spill_dir = os.path.join(os.path.expanduser("~"), ".emowave", "pending")
        # 用户纠正**跨会话保留**：v4 只放内存，重启即失（Phase 1 审计 §3）
        self.corrections = self.store.load_corrections()

        # 精力人群先验：开局选一类 → 用它 seed 默认曲线（ModelParameters）+ 中心（Baseline）
        self.archetype_key = archetype_key or DEFAULT_ARCHETYPE_KEY
        self.archetype = get_archetype(self.archetype_key)

        self.params = self.archetype.to_params()
        self.baseline_ctrl = BaselineController(initial_baseline=self.archetype.to_baseline())
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.calibrator = Calibrator(population=self.archetype.to_params())
        self._restore_calibrator_corrections()

        self.observations = []
        self.states = []
        self.recording = False
        self._segment = []          # 本次「开始→停止记录」采集到的观察
        self._edits = []            # 用户对曲线点的编辑（伪观察），驱动 RTS 重拟合

        # 情绪波生命周期：idle → recording → decaying → 回归基线后消失 → idle
        self._wave_active = False
        self._last_obs_ts = 0.0
        self._decay_now = 0.0       # 仅作可读的诊断值（= 最近一次推演时刻）
        self.last_horizon = 0.0     # 最近一次"距上次自评多久"（秒），供 UI/测试

        # 当前状态估计：**与波生命周期解耦**。
        # 波消失只是动画结束，latest_state 仍然代表"模型此刻对你的估计"。
        self.latest_state = None
        self.latest_state_ts = 0.0
        self._load_latest_state()

        # 曲线缓存：数据/参数/基线/窗口都没变就不重算（Phase 2）
        self._curve_key = None
        self._curve_cache = None

        # 衰减尾巴：{horizon, pts, state}。只记足迹，推进交给 estimator（v5）
        self._tail = None

        # 记录中 1Hz 采样用的"当前输入值"（由视图滑杆推动；
        # 默认值与 InputPanel 的滑杆初值一致：效价 50 / 唤醒 40）
        self._input_v = 0.50
        self._input_a = 0.40

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_tick)

        # 悬浮窗常驻时的低频刷新（Phase 4）：只推进"现在"，不产生观察。
        # 与 1Hz 心跳是两条独立的节律：心跳服务"记录/衰减动画"，
        # 本定时器服务"没在记录时也想知道当前状态"。
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(int(WaveScheduler.REFRESH_INTERVAL_SEC * 1000))
        self.refresh_timer.timeout.connect(self._on_refresh)

    def _notice(self, message, severity="warn"):
        """收集并广播用户可见的提示（构造期先攒在 self.notices）。"""
        item = (severity, str(message))
        self.notices.append(item)
        self.notice_changed.emit(*item)

    def set_input(self, valence=None, arousal=None, source=None):
        """视图滑杆变化时调用。

        分通道更新（valence=None 表示该通道不动）：主窗只碰效价时，
        不会再把挂件刚调好的唤醒打回原值——这是多视图共享 presenter
        必然遇到的输入源争抢（阶段 4 引入、阶段 6 修）。
        变更后广播 input_changed，让另一个视图的滑杆镜像过来。
        """
        if valence is not None:
            self._input_v = float(valence)
        if arousal is not None:
            self._input_a = float(arousal)
        self.input_changed.emit(self._input_v, self._input_a)

    def submit_input(self, valence=None, arousal=None, source=None):
        """用户**主动调整**了滑条：更新输入，并交给 recorder 防抖结算。

        与 `set_input` 的区别：`set_input` 只是"把当前值同步进来"（初始化、
        视图镜像、程序化设定），**不应该**因此产生一条自评；
        只有真实的手指操作才走这里。
        """
        self.set_input(valence, arousal, source)
        if self.recording:
            self.recorder.submit(self._input_v, self._input_a)

    def commit_now(self):
        """「记一笔」：立即把当前输入结算成一条真实自评（即使值没变）。"""
        if not self.recording:
            return
        self.recorder.submit(self._input_v, self._input_a)
        self.recorder.flush(force=True)

    # ================================================================
    # 录制 / 采样
    # ================================================================
    def toggle_recording(self, want_running=None):
        """开始/停止记录。停止后不清空，进入"外推衰减回基线"，回归后曲线消失。"""
        if want_running is None:
            want_running = not self.recording
        if want_running:
            # 守卫：已在记录中再按"开始"，旧实现会跳过收尾并直接清空 _segment，
            # 把本次已采集的观察吃掉（落库 sample_count 与曲线长度不一致），
            # 且按钮没有视觉反馈。明确拒绝比静默清空好。
            if self.recording:
                return
            if self._wave_active:
                self._end_wave()                 # 上一波还在衰减 → 先收尾
            self.recording = True
            self._wave_active = True
            self._segment = []
            self.recorder.set_enabled(True)      # 只有记录态才结算自评
            self.mode_changed.emit("recording")
            self.timer.start(1000)
        else:
            if not self.recording:
                return
            self.recorder.flush()                # 停止前把还没结算的拖动提交掉
            self.recorder.set_enabled(False)
            self.recording = False
            self._persist_segment_event()        # 落库 + 自动学习（保留 states/observations）
            if not self.states:                  # 没采到点 → 直接回 idle
                self._end_wave()
                return
            self._last_obs_ts = self.states[-1][0]
            self._decay_now = self.scheduler.now()
            self.mode_changed.emit("decaying")
            self._store_active_wave()            # 存快照：关掉重开也能按真实时间续算
            self.timer.start(1000)               # 前台心跳：只推进显示，不造观察

    def sample(self, valence: float, arousal: float):
        """提交**一条真实自评**：喂给内核，并把新状态推给视图。

        v4 起不再由定时器调用（见 `_on_tick`）——只有用户动作才会走到这里。
        """
        now = self.scheduler.now()
        # 防竞态：心跳可能刚把模型时间推到 now 之后几毫秒，观测不能早于模型时间
        last = self.estimator.last_timestamp or 0.0
        ts = max(now, last)
        obs = Observation(timestamp=ts, valence=valence, arousal=arousal,
                          source=ObservationSource.USER)
        self.observations.append(obs)
        self._segment.append(obs)
        self._last_obs_ts = ts
        if not self.estimator.is_initialized:
            self.estimator.initialize(timestamp=ts, valence=valence, arousal=arousal)
        state = self.estimator.update(obs)
        self.states.append((ts, state))
        self.latest_state = state
        self.latest_state_ts = ts
        self._tail = None          # 时间基准变了，旧尾巴作废
        self.count_changed.emit(len(self.observations))
        self._emit_readouts()
        self._emit_curve(refit=True)

    def _on_committed(self, valence: float, arousal: float):
        """recorder 结算了一次自评 → 记为观察。"""
        if not self.recording:
            return
        self.sample(valence, arousal)

    def _refresh_now(self):
        """无新自评，但时间过去了：把模型推进到"现在"并刷新读数。

        只做一次预测步 + 读数推送，**不重拟合曲线、不产生观察**。
        """
        state = self.query.query_state()
        if state is None:
            return
        self.latest_state = state
        self.latest_state_ts = state.timestamp
        self._emit_readouts()

    def set_periodic_refresh(self, enabled: bool, interval_sec: float = None):
        """开启/停止低频刷新（悬浮窗可见时开启）。

        Args:
            enabled: True 开启
            interval_sec: 刷新间隔（秒），默认 300（5 分钟）
        """
        if interval_sec is not None:
            self.refresh_timer.setInterval(max(1, int(interval_sec * 1000)))
        if enabled:
            if not self.refresh_timer.isActive():
                self.refresh_timer.start()
        else:
            self.refresh_timer.stop()

    def _on_refresh(self):
        """低频刷新：把模型推进到"现在"并更新读数。

        **不产生观察、不重拟合历史曲线**——它回答的是"现在大概是什么状态"，
        不是"用户刚刚说了什么"。记录/衰减期间 1Hz 心跳已在推进，这里直接跳过。
        """
        if self.recording or self._wave_active:
            return
        self._refresh_now()
        self._persist_latest_state()

    def refresh_now(self):
        """外部请求把状态推到「现在」（窗口显示/挂件唤起时用）。"""
        self._refresh_now()

    def stop_all_timers(self):
        """退出前停掉所有节律，避免进程退出后还有回调。"""
        self.timer.stop()
        self.refresh_timer.stop()
        self.recorder.set_enabled(False)

    def _on_tick(self):
        """1Hz 心跳：记录中只推进"现在"；衰减中按墙钟外推。

        v4 的关键改动：心跳**不再** `sample(_input_v, _input_a)`。
        心跳只回答"现在几点、状态是什么"，不回答"用户刚刚说了什么"。
        """
        if self.recording:
            self._refresh_now()
        elif self._wave_active:
            self.decay_step()

    # ================================================================
    # 衰减外推（改调内核 predict_forward，删掉 v3 手写实现）
    # ================================================================
    def decay_step(self):
        if not self.states:
            self._end_wave()
            return
        # v4：horizon 来自墙钟，不再是 `tick 次数 × DECAY_DT`。
        # 定时器只决定"多久看一次"，不决定"过了多久"。
        now = self.scheduler.now()
        self._decay_now = now
        horizon = now - self._last_obs_ts
        self.last_horizon = max(0.0, horizon)
        if horizon <= 0:
            return
        pts = self._tail_points(horizon)           # 增量外推（只推新增的一段）
        # v5：读数必须跟着曲线走，否则数字冻结在最后一次自评（Phase 0 问题 B）
        self._sync_latest_from_tail()
        self._emit_curve(points=pts)
        self._emit_readouts()
        last_v, last_a = (pts[-1][1], pts[-1][2]) if pts else (
            self.states[-1][1].valence, self.states[-1][1].arousal)
        bv = self.baseline_ctrl.current.valence
        ba = self.baseline_ctrl.current.arousal
        settled = (abs(last_v - bv) < SETTLE_EPS and abs(last_a - ba) < SETTLE_EPS
                   and horizon >= SETTLE_MIN_SEC)
        if settled or horizon >= MAX_DECAY_SEC:
            self._end_wave()                       # 回归基线 → 曲线消失（状态保留）

    def _decay_points(self, horizon: float):
        """从「最后一次自评」一次性外推 horizon 秒（非破坏性）。

        v3 这里是手写的 Matérn 衰减（`d=e^(-λΔt)(1+λΔt)`），与本内核
        `predict_forward` 是同一套数学；内核版额外做对两件事：
          · 在**偏差空间**传播（相对基线 mean，而非相对 0）
          · 每步叠过程噪声 Q → 外推越远方差越大（"越预测越不确定"）

        v5 修：起点必须是 `last_obs_ts` 时刻的状态。v4 取 `estimator.state_vector`，
        而那个状态可能已被推进到"现在"——于是"一次性重算"和增量推进给出两个答案，
        两者互相印证时又都错（Phase 0 §1.5）。
        """
        est, _ = self._rebuild_estimator()
        if est is None:
            return []
        mean = (self.baseline_ctrl.current.valence,
                self.baseline_ctrl.current.arousal)
        traj = predict_forward(
            state_vector=est.state_vector,
            covariance=est.covariance,
            params=self.params,
            horizon_sec=horizon,
            dt=DECAY_DT,
            mean=mean,
        )
        base = self._last_obs_ts
        return [(base + p["timestamp_offset"], p["valence"], p["arousal"],
                 p["var_valence"], p["var_arousal"]) for p in traj]

    def _tail_points(self, horizon: float):
        """增量外推：由 estimator 自己推进（**唯一时间源**），尾巴只记足迹。

        v5 修：v4 的 `_tail` 另存一份 x/P 独立推进，与 estimator 并行且互不校对，
        于是长出两个 bug——
          · 恢复时模型被推进两次（`query_state` 一次 + 尾巴一次）；
          · 读数读 estimator、曲线读 `_tail`，衰减期两者各走各的。
        现在只剩一条时间线：`estimator.advance_to()` 负责推进，
        `_tail["pts"]` 是它的足迹，`_tail["state"]` 就是"现在"的状态。
        """
        if self._tail is None or horizon < self._tail["horizon"]:
            # 模型若已被推到 last_obs 之后（恢复、刷新），先退回再重建
            last = self.estimator.last_timestamp or 0.0
            if last > self._last_obs_ts + 1e-9:
                self._replay_from_observations()
            self._tail = {"horizon": 0.0, "pts": [], "state": None}

        tail = self._tail
        while tail["horizon"] + DECAY_DT <= horizon + 1e-9:
            target = self._last_obs_ts + tail["horizon"] + DECAY_DT
            tail["horizon"] += DECAY_DT
            state = self.estimator.advance_to(target)
            if state is None:
                break
            tail["pts"].append((
                state.timestamp, state.valence, state.arousal,
                state.variance_valence, state.variance_arousal,
            ))
            tail["state"] = state
        return tail["pts"]

    def _sync_latest_from_tail(self) -> bool:
        """读数与曲线共用一个状态：尾巴推到哪，读数就是哪。"""
        if not self._tail or self._tail.get("state") is None:
            return False
        state = self._tail["state"]
        self.latest_state = state
        self.latest_state_ts = state.timestamp
        return True

    def _rebuild_estimator(self):
        """从 observations 重放出一个「停在最后一次自评」的估计器（**不改 self**）。

        与 `sample()` 完全同构：第一条 initialize，其余 update——
        这样重放出来的状态与在线记录时逐位一致。

        Returns:
            (estimator, states)；无观察时返回 (None, [])
        """
        if not self.observations:
            return None, []
        est = StateEstimator(params=self.params,
                             baseline=self.baseline_ctrl.current)
        states = []
        for obs in self.observations:
            if not est.is_initialized:
                est.initialize(timestamp=obs.timestamp,
                               valence=obs.valence, arousal=obs.arousal)
            states.append((obs.timestamp, est.update(obs)))
        return est, states

    def _replay_from_observations(self):
        """把模型退回「最后一次自评」时刻（基线/参数变化后重建）。"""
        est, states = self._rebuild_estimator()
        if est is None:
            return
        self.estimator = est
        self.states = states
        self._last_obs_ts = self.observations[-1].timestamp
        self._tail = None

    def _end_wave(self):
        """一波结束：曲线消失、回到待记录(idle)。

        v4：**只结束动画，不清空"当前状态"**。`latest_state` 是模型对用户的
        持续估计，波消失不代表"没有状态"——否则悬浮窗大部分时间只能显示 "—"。
        """
        self.timer.stop()
        self.recording = False
        self.recorder.set_enabled(False)
        self._wave_active = False
        self.curve_cleared.emit()
        self.states = []
        self.observations = []
        self._edits = []
        self._segment = []
        # 注意：**不重置 estimator**。
        # 1) 波结束只是动画结束，模型仍应能回答"现在是什么状态"（D1 状态常驻）；
        #    低频刷新靠 advance_to(now) 让它继续向基线收敛。
        # 2) 下一次记录从当前估计继续，而不是每次从零开始——这才是"持续状态模型"。
        # 真正的重置只发生在换人群（set_archetype）。
        self.mode_changed.emit("idle")
        self.count_changed.emit(0)
        self._clear_active_wave()
        # 状态常驻：有最新估计就继续显示，没有才复位
        if self.latest_state is not None:
            self._emit_readouts()
            self._persist_latest_state()
        else:
            self.readouts_reset.emit()

    # ================================================================
    # 落库 / 快照
    # ================================================================
    def _persist_segment_event(self):
        """把本次「开始→停止」采集到的观察聚合成一条 emotion_events 落库。

        v3 的连续记录此前从不写事件表，导致「事件回顾/历史记录」永远空。
        """
        if self.db is None or not self._segment:
            return
        seg = self._segment
        peak_obs = max(seg, key=lambda o: o.arousal)
        # Russell 环状模型：中性点 (0.5, 0.5)，强度=到中性点距离 / 最大距离
        def _intensity(o):
            return math.sqrt((o.valence - 0.5) ** 2 + (o.arousal - 0.5) ** 2) / math.sqrt(0.5)
        event = {
            "event_id": uuid.uuid4().hex,
            "start_time": seg[0].timestamp,
            "end_time": seg[-1].timestamp,
            "peak_valence": peak_obs.valence,
            "peak_arousal": peak_obs.arousal,
            "peak_intensity": max(_intensity(o) for o in seg),
            "sample_count": len(seg),
            "trigger_tags": [],
            "coping_methods": [],
            "coping_ratings": {},
            "body_symptoms": [],
            "user_peak_rating": None,
            "raw_data": json.dumps([[o.timestamp, o.valence, o.arousal] for o in seg]),
        }
        try:
            self.db.save_event(event)
            self.history_changed.emit()
        except Exception as exc:                   # 落库失败不打断 UI，但必须让用户知道
            logger.warning("情绪事件落库失败（%d 个采样）：%s", len(seg), exc)
            spill = self._spill_event(event)
            if spill:
                self._notice("本次记录未能写入数据库，已保存恢复文件：%s。"
                             "请检查磁盘空间/权限后再试。" % spill)
            else:
                self._notice("本次记录未能写入数据库，也未能保存恢复文件；"
                             "请先检查磁盘空间/权限。")
        finally:
            self._segment = []                     # 无论成败都消费掉，避免重复落库
        # 停止即自动个性化一步（用本次记录 + 已有纠正）
        try:
            self.learn()
        except Exception as exc:
            logger.warning("停止记录后自动学习失败：%s", exc)
        self._persist_latest_state()

    def _spill_event(self, event):
        """数据库写失败时的最后一道保险：把事件原样写到本地恢复文件。"""
        try:
            os.makedirs(self._spill_dir, exist_ok=True)
            path = os.path.join(self._spill_dir, "%s.json" % event["event_id"])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(event, f, ensure_ascii=False, indent=2)
            return path
        except Exception as exc:
            logger.warning("保存情绪事件恢复文件失败：%s", exc)
            return None

    def _store_active_wave(self):
        """保存未走完的波（细节见 SnapshotStore）。"""
        self.store.save_wave(self._last_obs_ts, self.scheduler.now(),
                             self.observations)

    def _clear_active_wave(self):
        self.store.clear_wave()

    def _persist_latest_state(self):
        """把"当前状态"落盘：重启后即使没有未走完的波，挂件也有东西可显示。"""
        self.store.save_latest(self.latest_state)

    def _load_latest_state(self):
        state = self.store.load_latest()
        if state is not None:
            self.latest_state = state
            self.latest_state_ts = float(getattr(state, "timestamp", 0.0))

    def resume_active_wave(self):
        """开机若有未走完的波：重放观察、重建估计器，**按真实经过时间**续算。

        v5 修：v4 先用 `query_state()` 把模型推到 now，随后尾巴又从 now 起算、
        却把点标在 last_obs 之后——整条尾巴被平移一个 horizon（关机越久偏得越多）。
        现在推进只发生一次：`_tail_points()` 从 last_obs 一路走到 now。
        """
        # 幂等：已经在记录 / 已经恢复过一波，就不要再重放一次
        # （重复调用会把同一批观察插两遍，模型会把它当成两次独立证据）
        if self.observations or self._wave_active:
            return
        loaded = self.store.load_wave()
        if loaded is None:
            return
        samples, last_obs_ts = loaded

        self.observations = [
            Observation(timestamp=ts, valence=v, arousal=a,
                        source=ObservationSource.USER)
            for ts, v, a in samples
        ]
        self._replay_from_observations()
        if last_obs_ts:
            self._last_obs_ts = last_obs_ts
        self._decay_now = self.scheduler.now()
        self.count_changed.emit(len(self.observations))

        horizon = self.scheduler.horizon(self._last_obs_ts)
        self.last_horizon = horizon
        if horizon >= MAX_DECAY_SEC:
            # 关机太久：波已自然结束，不留动画，只留状态
            logger.debug("active_wave 已过期（%.0fs），只保留状态", horizon)
            state = self.estimator.advance_to(self.scheduler.now())
            if state is not None:
                self.latest_state = state
                self.latest_state_ts = state.timestamp
            self._end_wave()
            return

        self._tail_points(horizon)          # 推进到「现在」，留下足迹
        self._sync_latest_from_tail()
        self._emit_readouts()
        self._persist_latest_state()

        self._wave_active = True
        self.recording = False
        self.mode_changed.emit("decaying")
        self.timer.start(1000)                         # 前台心跳推演（无后台进程）

    def restore_recent_context(self, max_age_sec: float = 7 * 86400):
        """重启后让模型接着上次的估计，而不是从零开始。

        只重建 estimator（回答"现在是什么状态"），**不把历史观察放进
        `self.observations`**——否则一打开就会看到上次的曲线，用户会误以为是
        本次记录。曲线仍然只在真正开始记录后出现。

        数据源是最近一条事件的 `raw_data`（停止记录时已落库），所以不需要新表。

        Args:
            max_age_sec: 超过这个时长的旧事件不再用于恢复（默认 7 天）

        Returns:
            bool: 是否恢复成功
        """
        if self.db is None or self.estimator.is_initialized:
            return False
        try:
            events = self.db.get_recent_events(limit=1)
        except Exception as exc:
            logger.debug("读取最近事件失败：%s", exc)
            self._notice("读取最近事件失败，本次未能恢复上次状态；历史页仍可打开。")
            return False
        if not events:
            return False
        try:
            raw = json.loads(events[0].get("raw_data") or "[]")
        except Exception as exc:
            logger.warning("最近事件的 raw_data 损坏，跳过上下文恢复：%s", exc)
            self._notice("最近一次事件的记录损坏，无法恢复上次上下文；历史仍可查看。")
            return False

        samples = SnapshotStore._validate_samples(raw)
        if not samples:
            return False
        last_ts = samples[-1][0]
        if self.scheduler.now() - last_ts > max_age_sec:
            return False                       # 太久以前的事，不该影响"现在"

        est = StateEstimator(params=self.params,
                             baseline=self.baseline_ctrl.current)
        est.initialize(timestamp=samples[0][0], valence=samples[0][1],
                       arousal=samples[0][2])
        for ts, v, a in samples[1:]:
            est.update(Observation(timestamp=ts, valence=v, arousal=a,
                                   source=ObservationSource.USER))
        self.estimator = est
        self._last_obs_ts = last_ts

        state = self.query.query_state()
        if state is not None:
            self.latest_state = state
            self.latest_state_ts = state.timestamp
            self._emit_readouts()
        return True

    def _restore_calibrator_corrections(self):
        """重启后把历史纠正喂回 calibrator——学习不因关窗归零。

        只喂 calibrator（学习信号），**不放进 `_edits`**：后者是"本波曲线的
        显示修正"，跨波复用会把上一波的修正画到这一波上。
        """
        for item in self.corrections:
            try:
                uc = UserCorrection(
                    timestamp=float(item["timestamp"]),
                    dimension=CorrectionDimension(item["dimension"]),
                    predicted_value=float(item.get("predicted_value", 0.5)),
                    corrected_value=float(item["corrected_value"]),
                    source=CorrectionSource(item.get("source", "drag")),
                    created_at=float(item.get("created_at", item["timestamp"])),
                )
            except Exception as exc:
                logger.debug("跳过一条无法还原的纠正记录：%s", exc)
                continue
            try:
                self.calibrator.ingest_correction(uc)
            except Exception as exc:
                logger.debug("回灌纠正失败：%s", exc)

    def ingest_corrections(self, applied, source=None):
        """把「时刻 → (v, a)」的纠正列表写进 calibrator 数据集。

        单独抽出：上滑框是模态的，测试无法走 exec_()，直接喂本方法即可。

        注意 **UserCorrection 是单维度的**（dimension + predicted/corrected
        单个数值），所以一次拖点若同时改了效价和唤醒，会产出 2 条纠正记录。
        """
        if not applied:
            return 0
        if source is None:
            source = CorrectionSource.DRAG
        n = 0
        for ts, v, a in applied:
            pred = next((s for t, s in self.states if abs(t - ts) < 0.5), None)
            if pred is None:
                continue
            for dim, pv, cv in (
                (CorrectionDimension.VALENCE, pred.valence, v),
                (CorrectionDimension.AROUSAL, pred.arousal, a),
            ):
                if abs(cv - pv) < 1e-9:
                    continue        # 该维度没动，不产生纠正
                uc = UserCorrection(
                    timestamp=ts,
                    dimension=dim,
                    predicted_value=pv,
                    corrected_value=cv,
                    source=source,
                    created_at=time.time(),
                )
                self.calibrator.ingest_correction(uc)
                self.corrections.append({
                    "timestamp": uc.timestamp,
                    "dimension": uc.dimension.value,
                    "predicted_value": uc.predicted_value,
                    "corrected_value": uc.corrected_value,
                    "source": uc.source.value,
                    "created_at": uc.created_at,
                    "reliability_weight": uc.reliability_weight,
                })
                # 同时作为伪观察喂给显示曲线 → 改点即时重拟合整条曲线
                self._edits.append({
                    "timestamp": ts,
                    "channel": dim.value,
                    "value": cv,
                    # 显示用固定高权重；学习仍用 uc.reliability_weight（见常量注释）
                    "reliability_weight": DISPLAY_CORRECTION_WEIGHT,
                })
                n += 1
        if n:
            self.store.save_corrections(self.corrections)
            self._emit_curve(refit=True)
        return n

    # ================================================================
    # 基线主权（三级：nudge / fork / reset）
    # ================================================================
    # 注意：v3 的这套同步写在 windows/baseline_tools.py 的按钮回调里
    # （`self._est.baseline = self._ctrl.current`）。解耦时如果只搬
    # ctrl 调用而漏掉这行，曲线画的就是旧基线 —— test_baseline_nudge_fork_and_reset
    # 会红。同步责任现在归 presenter（换人群换 estimator 后也不会失效）。
    def _sync_estimator_baseline(self):
        self.estimator.baseline = self.baseline_ctrl.current
        self._tail = None            # 均值变了，旧尾巴要按新基线重建
        self.baseline_changed.emit(self.baseline_ctrl.current.valence,
                                   self.baseline_ctrl.current.arousal)

    def nudge(self, delta: float):
        bl = self.baseline_ctrl.current
        new_v = max(0.0, min(1.0, bl.valence + delta))
        try:
            self.baseline_ctrl.nudge(valence=new_v, reason="user_nudge")
        except TypeError:                       # 兼容旧签名 nudge(v)
            self.baseline_ctrl.nudge(new_v, reason="user_nudge")
        self._sync_estimator_baseline()

    def fork(self):
        """按当前值开一个新 regime（硬变点）。"""
        cur = self.baseline_ctrl.current
        self.baseline_ctrl.fork(timestamp=time.time(),
                                new_baseline=Baseline(valence=cur.valence,
                                                      arousal=cur.arousal),
                                reason="user_fork",
                                source=BaselineSource.USER_FORK)
        self._sync_estimator_baseline()

    def reset(self):
        """L3 重置：回到群体先验（Baseline() 默认 0.55/0.42）。"""
        self.baseline_ctrl.reset(reason="user_reset")
        self._sync_estimator_baseline()

    # ================================================================
    # 学习 / 换人群
    # ================================================================
    def learn(self):
        # 先算到局部变量，再一次性赋两处：若中途抛异常，不会留下
        # presenter.params 已是新的、estimator.params 还是旧的 这种混合状态。
        new_params = self.calibrator.learn(self.params)
        self.params = new_params
        self.estimator.params = new_params
        # 学习改变了 λ → 整条显示曲线必须按新参数重拟合。
        # v3 是 _learn() 之后紧跟 _refresh_curve()；漏掉这一步时，
        # 紧接着的衰减 tick 会输出"旧参数历史主体 + 新参数外推尾"。
        self._emit_curve(refit=True)

    def set_archetype(self, key: str):
        """切换精力人群先验。换人群 = 换起点先验，历史曲线随之作废。"""
        self.archetype_key = key
        self.archetype = get_archetype(key)
        self.params = self.archetype.to_params()
        self.baseline_ctrl = BaselineController(
            initial_baseline=self.archetype.to_baseline())
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.calibrator = Calibrator(population=self.archetype.to_params())
        self._restore_calibrator_corrections()
        self.observations = []
        self.states = []
        self._segment = []
        self._edits = []
        self.recording = False
        self._wave_active = False
        self._last_obs_ts = 0.0
        self._decay_now = 0.0
        self.last_horizon = 0.0
        self.latest_state = None
        self.latest_state_ts = 0.0
        self._tail = None
        self.recorder.reset()
        self.recorder.set_enabled(False)
        self.timer.stop()
        self.curve_cleared.emit()
        self.readouts_reset.emit()
        self.mode_changed.emit("idle")
        self.count_changed.emit(0)
        self._clear_active_wave()

    # ================================================================
    # 推给视图的数据封装
    # ================================================================
    # ================================================================
    # 现状快照（供「晚于构造函数接线」的视图做初始同步）
    # ================================================================
    def current_mode(self) -> str:
        """当前生命周期模式：recording / decaying / idle。"""
        if self.recording:
            return "recording"
        if self._wave_active:
            return "decaying"
        return "idle"

    def is_wave_active(self) -> bool:
        """是否处于「衰减动画未结束」阶段（测试与视图都可用的公共读法）。"""
        return bool(self._wave_active)

    def current_count(self) -> int:
        return len(self.observations)

    def has_estimator_state(self) -> bool:
        """模型是否已有状态（供视图决定是否尝试恢复上下文）。"""
        return bool(self.estimator.is_initialized)

    def has_readout(self) -> bool:
        """是否已有可展示的读数（波结束后 latest_state 仍常驻）。"""
        return self.latest_state is not None or bool(self.states)

    def sync_snapshot(self) -> dict:
        """晚挂载视图的初始同步包——避免视图直接摸 presenter 内部。"""
        return {
            "mode": self.current_mode(),
            "count": self.current_count(),
            "readout": self.readout_payload() if self.has_readout() else None,
            "curve": self.current_curve_payload(),
        }

    def curve_payload(self, start_ts=None, end_ts=None, resolution=None):
        """统一曲线查询（带缓存）：主窗、挂件、未来的定时刷新都走这里。

        缓存键覆盖所有会影响结果的输入：观察数、纠正数、参数对象、基线、窗口。
        任一变化 → 重算；都没变 → 直接返回上次结果。
        """
        key = (len(self.observations), len(self._edits), id(self.params),
               self.baseline_ctrl.current.valence, start_ts, end_ts, resolution)
        if key == self._curve_key:
            return self._curve_cache
        payload = self.query.query_curve(
            self.observations, self.params, self._edits,
            start_ts=start_ts, end_ts=end_ts, resolution=resolution,
        )
        if payload is not None:
            payload["corrections"] = self.correction_marks()
        self._curve_key, self._curve_cache = key, payload
        return payload

    def current_curve_payload(self):
        """当前应显示的曲线数据；无数据返回 None。"""
        return self.curve_payload()

    def correction_marks(self):
        """用户修正点（画在曲线上，与真实观察点区分开）。"""
        return [{"ts": e["timestamp"], "channel": e["channel"], "value": e["value"]}
                for e in self._edits]

    def correction_context(self):
        """纠正框需要的全部数据——视图不再自己从 states/observations 拼。

        Returns:
            dict；`has_data` 为 False 时调用方应直接返回（少于 2 点无法编辑）
        """
        return {
            "has_data": len(self.states) >= 2,
            "timestamps": [t for t, _ in self.states],
            "model_v": [s.valence for _, s in self.states],
            "model_a": [s.arousal for _, s in self.states],
            "baseline_v": self.baseline_ctrl.current.valence,
            "current_state": self.states[-1][1] if self.states else None,
        }

    def readout_payload(self):
        """算出读数 payload（视图主动要一份时用，如启动时的首次刷新）。

        v4：优先用 `latest_state`（"现在"的估计），而不是 `states[-1]`
        （"最后一次自评时"的估计）。两者在波结束后不再相同——
        前者仍在，后者被清空。
        """
        st = self.latest_state or (self.states[-1][1] if self.states else None)
        if st is None:
            return (0.0, 0.0, 0.0, "—", {}, 0.0, 0.0)
        lo, hi = st.confidence_interval("valence", z=1.0)
        ci = f"{int(lo*100)} – {int(hi*100)}"
        params_text = {
            'confidence': f"{int(st.confidence*100)}%",
            'confidence_desc': f"={int(st.confidence*100)}%",
            'ci': ci,
            'ci_desc': f"估 {int(st.valence*100)}±{int((hi-lo)*50)}",
        }
        return (st.valence, st.arousal, st.confidence, ci, params_text,
                float(st.timestamp), float(self._last_obs_ts))

    def _emit_readouts(self):
        """把最新状态推给视图：读数 + 参数说明共用一个 payload。"""
        if self.latest_state is None and not self.states:
            return
        v, a, c, ci, params_text, state_ts, last_obs_ts = self.readout_payload()
        self.readouts_changed.emit(v, a, c, ci, params_text, state_ts, last_obs_ts)

    def _emit_curve(self, refit=False, points=None):
        """推送曲线数据。

        refit=True  → 用 RTS 平滑重算整条显示曲线（采样/纠正后）
        points=…    → 直接推一段外推尾巴（衰减中）
        """
        if refit:
            payload = self.curve_payload()
            if payload is None:
                return
            self.curve_changed.emit(payload)
            return
        baseline_v = self.baseline_ctrl.current.valence
        if points is None:
            return
        ts_list = [t for t, _ in self.states] + [p[0] for p in points]
        mv = [s.valence for _, s in self.states] + [p[1] for p in points]
        ma = [s.arousal for _, s in self.states] + [p[2] for p in points]
        vv = [s.variance_valence for _, s in self.states] + [p[3] for p in points]
        va = [s.variance_arousal for _, s in self.states] + [p[4] for p in points]
        raw = [(o.timestamp, o.valence) for o in self.observations]
        self.curve_changed.emit({
            "ts": ts_list, "mv": mv, "ma": ma, "vv": vv, "va": va,
            "baseline_v": baseline_v, "raw": raw,
            # 最后一次自评之后的点都是外推 → 视图画虚线
            "predicted_from": self._last_obs_ts,
            "corrections": self.correction_marks(),
        })

