"""presenters/console_presenter.py — 主控制台的内核编排层

=== 职责边界 ===
本类**不 import 任何 PyQt5.QtWidgets**（只允许 QtCore 的 QObject/QTimer/pyqtSignal），
也就是 MVP 里"可被 mock、可在无 GUI 环境下单测"的那一层。它：

  · 持有全部内核对象（estimator / baseline_ctrl / calibrator / observations …）
  · 持有 1Hz 心跳 QTimer（采样节奏是编排责任，不是内核责任）
  · 把结果用**信号**推给视图；输入（滑条当前值）由调用方以参数显式传入
    —— 这样第二个视图（迷你置顶窗）可以复用同一个 presenter 而不抢状态

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
import time
import uuid

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from .engine import (
    Observation, ObservationSource, UserCorrection,
    CorrectionDimension, CorrectionSource,
    get_archetype, DEFAULT_ARCHETYPE_KEY,
    Baseline, BaselineSource,
    StateEstimator, RTSSmoother, BaselineController, Calibrator,
    predict_forward,
)

logger = logging.getLogger(__name__)

# ---- 情绪波生命周期常量（记录→衰减回基线→消失→待下次记录）----
# 从 v3 的 main_console.py 原样搬来，并在那里保留同名 re-export 以兼容旧引用。
SETTLE_EPS = 0.05        # 距基线小于此值视为"回归基线"
SETTLE_MIN_SEC = 3.0     # 至少衰减这么久才允许判定结束（避免刚停就消失）
MAX_DECAY_SEC = 600.0    # 衰减外推的硬上限（防止无限外推）
DECAY_DT = 1.0           # 每个定时器 tick 向前推演的秒数（实时、无需后台进程）


class ConsolePresenter(QObject):
    """主控制台的内核编排。视图只接信号、只传参数。"""

    # ---- 推给视图的信号 ----
    readouts_changed = pyqtSignal(float, float, float, str, dict)  # v,a,conf,ci,params_text
    readouts_reset = pyqtSignal()
    curve_changed = pyqtSignal(dict)      # {ts, mv, ma, vv, va, baseline_v, raw}
    curve_cleared = pyqtSignal()
    mode_changed = pyqtSignal(str)        # idle / recording / decaying
    count_changed = pyqtSignal(int)
    history_changed = pyqtSignal()        # 有新事件落库 → 历史页该刷新了
    input_changed = pyqtSignal(float, float)   # 输入源变更 → 各视图镜像滑杆
    baseline_changed = pyqtSignal(float, float)   # 基线变了 → 基线卡显示该刷新

    def __init__(self, db=None, archetype_key=None, parent=None):
        super().__init__(parent)
        self.db = db

        # 精力人群先验：开局选一类 → 用它 seed 默认曲线（ModelParameters）+ 中心（Baseline）
        self.archetype_key = archetype_key or DEFAULT_ARCHETYPE_KEY
        self.archetype = get_archetype(self.archetype_key)

        self.params = self.archetype.to_params()
        self.baseline_ctrl = BaselineController(initial_baseline=self.archetype.to_baseline())
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.calibrator = Calibrator(population=self.archetype.to_params())

        self.observations = []
        self.states = []
        self.recording = False
        self._segment = []          # 本次「开始→停止记录」采集到的观察
        self._edits = []            # 用户对曲线点的编辑（伪观察），驱动 RTS 重拟合

        # 情绪波生命周期：idle → recording → decaying → 回归基线后消失 → idle
        self._wave_active = False
        self._last_obs_ts = 0.0
        self._decay_now = 0.0

        # 记录中 1Hz 采样用的"当前输入值"（由视图滑杆推动；
        # 默认值与 InputPanel 的滑杆初值一致：效价 50 / 唤醒 40）
        self._input_v = 0.50
        self._input_a = 0.40

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_tick)

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
            self.mode_changed.emit("recording")
            self.timer.start(1000)
        else:
            if not self.recording:
                return
            self.recording = False
            self._persist_segment_event()        # 落库 + 自动学习（保留 states/observations）
            if not self.states:                  # 没采到点 → 直接回 idle
                self._end_wave()
                return
            self._last_obs_ts = self.states[-1][0]
            self._decay_now = self._last_obs_ts
            self.mode_changed.emit("decaying")
            self._store_active_wave()            # 存快照：关掉重开也能"慢慢推演"
            self.timer.start(1000)               # 继续跑 → _decay_step 衰减回基线

    def sample(self, valence: float, arousal: float):
        """1Hz 采样：喂一个观察给内核，并把新状态推给视图。"""
        ts = time.time()
        obs = Observation(timestamp=ts, valence=valence, arousal=arousal,
                          source=ObservationSource.USER)
        self.observations.append(obs)
        self._segment.append(obs)
        self._last_obs_ts = ts
        if not self.estimator.is_initialized:
            self.estimator.initialize(timestamp=ts, valence=valence, arousal=arousal)
        state = self.estimator.update(obs)
        self.states.append((ts, state))
        self.count_changed.emit(len(self.observations))
        self._emit_readouts()
        self._emit_curve(refit=True)

    def _on_tick(self):
        """1Hz 心跳：记录中按当前滑条值采样；衰减中向前推演。"""
        if self.recording:
            self.sample(self._input_v, self._input_a)
        elif self._wave_active:
            self.decay_step()

    # ================================================================
    # 衰减外推（改调内核 predict_forward，删掉 v3 手写实现）
    # ================================================================
    def decay_step(self):
        if not self.states:
            self._end_wave()
            return
        self._decay_now += DECAY_DT
        horizon = self._decay_now - self._last_obs_ts
        if horizon <= 0:
            return
        pts = self._decay_points(horizon)          # 向基线衰减 + 方差递增
        self._emit_curve(points=pts)
        last_v, last_a = (pts[-1][1], pts[-1][2]) if pts else (
            self.states[-1][1].valence, self.states[-1][1].arousal)
        bv = self.baseline_ctrl.current.valence
        ba = self.baseline_ctrl.current.arousal
        settled = (abs(last_v - bv) < SETTLE_EPS and abs(last_a - ba) < SETTLE_EPS
                   and horizon >= SETTLE_MIN_SEC)
        if settled or horizon >= MAX_DECAY_SEC:
            self._end_wave()                       # 回归基线 → 曲线消失

    def _decay_points(self, horizon: float):
        """从末态外推回基线。

        v3 这里是手写的 Matérn 衰减（`d=e^(-λΔt)(1+λΔt)`），与本内核
        `predict_forward` 是同一套数学；内核版额外做对两件事：
          · 在**偏差空间**传播（相对基线 mean，而非相对 0）
          · 每步叠过程噪声 Q → 外推越远方差越大（"越预测越不确定"）
        """
        mean = (self.baseline_ctrl.current.valence,
                self.baseline_ctrl.current.arousal)
        traj = predict_forward(
            state_vector=self.estimator.state_vector,
            covariance=self.estimator.covariance,
            params=self.params,
            horizon_sec=horizon,
            dt=DECAY_DT,
            mean=mean,
        )
        base = self._last_obs_ts
        return [(base + p["timestamp_offset"], p["valence"], p["arousal"],
                 p["var_valence"], p["var_arousal"]) for p in traj]

    def _end_wave(self):
        """一波结束：曲线消失、读数复位、估计器重置回基线，回到待记录(idle)。"""
        self.timer.stop()
        self.recording = False
        self._wave_active = False
        self.curve_cleared.emit()
        self.readouts_reset.emit()
        self.states = []
        self.observations = []
        self._edits = []
        self._segment = []
        self.estimator = StateEstimator(params=self.params,
                                        baseline=self.baseline_ctrl.current)
        self.mode_changed.emit("idle")
        self.count_changed.emit(0)
        self._clear_active_wave()

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
        except Exception as exc:                   # 落库失败不打断 UI，仅记日志
            logger.warning("情绪事件落库失败（%d 个采样）：%s", len(seg), exc)
        finally:
            self._segment = []                     # 无论成败都消费掉，避免重复落库
        # 停止即自动个性化一步（用本次记录 + 已有纠正）
        try:
            self.learn()
        except Exception as exc:
            logger.warning("停止记录后自动学习失败：%s", exc)

    def _store_active_wave(self):
        if self.db is None:
            return
        try:
            snap = {"end_ts": self._last_obs_ts,
                    "samples": [[o.timestamp, o.valence, o.arousal]
                                for o in self.observations]}
            self.db.set_state("active_wave", json.dumps(snap))
        except Exception as exc:
            logger.warning("保存活动情绪波快照失败：%s", exc)

    def _clear_active_wave(self):
        if self.db is None:
            return
        try:
            self.db.set_state("active_wave", "")
        except Exception as exc:
            logger.warning("清除活动情绪波快照失败：%s", exc)

    def resume_active_wave(self):
        """开机若有未走完的波：重放观察、重建估计器，进入衰减"慢慢推演"。"""
        if self.db is None:
            return
        try:
            raw = self.db.get_state("active_wave", "") or ""
        except Exception as exc:
            logger.debug("读取 active_wave 快照失败：%s", exc)
            return
        if not raw:
            return
        try:
            data = json.loads(raw)
            samples = data.get("samples", [])
            end_ts = float(data.get("end_ts", 0.0))
        except Exception as exc:
            logger.warning("active_wave 快照损坏，已清除：%s", exc)
            self._clear_active_wave()
            return
        if not samples:
            self._clear_active_wave()
            return
        for ts, v, a in samples:
            obs = Observation(timestamp=ts, valence=v, arousal=a,
                              source=ObservationSource.USER)
            self.observations.append(obs)
            if not self.estimator.is_initialized:
                self.estimator.initialize(timestamp=ts, valence=v, arousal=a)
            state = self.estimator.update(obs)
            self.states.append((ts, state))
        self._last_obs_ts = end_ts or self.states[-1][0]
        self._decay_now = self._last_obs_ts
        self._wave_active = True
        self.recording = False
        self.mode_changed.emit("decaying")
        self.count_changed.emit(len(self.observations))
        self.timer.start(1000)                         # 前台心跳推演（无后台进程）

    # ================================================================
    # 纠正 → calibrator + RTS 重拟合
    # ================================================================
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
                # 同时作为伪观察喂给显示曲线 → 改点即时重拟合整条曲线
                self._edits.append({
                    "timestamp": ts,
                    "channel": dim.value,
                    "value": cv,
                    "reliability_weight": uc.reliability_weight,
                })
                n += 1
        if n:
            self._emit_curve(refit=True)
        return n

    def observations_ts_for_curve(self):
        return [t for t, _ in self.states]

    # ================================================================
    # 基线主权（三级：nudge / fork / reset）
    # ================================================================
    # 注意：v3 的这套同步写在 windows/baseline_tools.py 的按钮回调里
    # （`self._est.baseline = self._ctrl.current`）。解耦时如果只搬
    # ctrl 调用而漏掉这行，曲线画的就是旧基线 —— test_baseline_nudge_fork_and_reset
    # 会红。同步责任现在归 presenter（换人群换 estimator 后也不会失效）。
    def _sync_estimator_baseline(self):
        self.estimator.baseline = self.baseline_ctrl.current
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
        self.observations = []
        self.states = []
        self._segment = []
        self._edits = []
        self.recording = False
        self._wave_active = False
        self._last_obs_ts = 0.0
        self._decay_now = 0.0
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

    def current_count(self) -> int:
        return len(self.observations)

    def current_curve_payload(self):
        """当前应显示的曲线数据；无数据返回 None。"""
        if len(self.states) < 2 or len(self.observations) < 2:
            return None
        traj = RTSSmoother(self.params).smooth(self.observations, self._edits)
        raw = [(o.timestamp, o.valence) for o in self.observations]
        return {
            "ts": list(traj.timestamps),
            "mv": list(traj.mean_valence),
            "ma": list(traj.mean_arousal),
            "vv": list(traj.var_valence),
            "va": list(traj.var_arousal),
            "baseline_v": self.baseline_ctrl.current.valence,
            "raw": raw,
        }

    def _readout_payload(self):
        """算出读数 payload（视图主动要一份时用，如启动时的首次刷新）。"""
        if not self.states:
            return (0.0, 0.0, 0.0, "—", {})
        st = self.states[-1][1]
        lo, hi = st.confidence_interval("valence", z=1.0)
        ci = f"{int(lo*100)} – {int(hi*100)}"
        params_text = {
            'confidence': f"{int(st.confidence*100)}%",
            'confidence_desc': f"={int(st.confidence*100)}%",
            'ci': ci,
            'ci_desc': f"估 {int(st.valence*100)}±{int((hi-lo)*50)}",
        }
        return (st.valence, st.arousal, st.confidence, ci, params_text)

    def _emit_readouts(self):
        """把最新状态推给视图：读数 + 参数说明共用一个 payload。"""
        if not self.states:
            return
        v, a, c, ci, params_text = self._readout_payload()
        self.readouts_changed.emit(v, a, c, ci, params_text)

    def _emit_curve(self, refit=False, points=None):
        """推送曲线数据。

        refit=True  → 用 RTS 平滑重算整条显示曲线（采样/纠正后）
        points=…    → 直接推一段外推尾巴（衰减中）
        """
        baseline_v = self.baseline_ctrl.current.valence
        if refit:
            if len(self.observations) < 2:
                return
            traj = RTSSmoother(self.params).smooth(self.observations, self._edits)
            raw = [(o.timestamp, o.valence) for o in self.observations]
            self.curve_changed.emit({
                "ts": list(traj.timestamps),
                "mv": list(traj.mean_valence),
                "ma": list(traj.mean_arousal),
                "vv": list(traj.var_valence),
                "va": list(traj.var_arousal),
                "baseline_v": baseline_v,
                "raw": raw,
            })
            return
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
        })

