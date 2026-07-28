#!/usr/bin/env python3
"""
run_simulation.py — 心潮 EmoWave 全系统端到端模拟运行

本脚本将四个模块串联起来，用模拟数据跑通整个 App 的核心闭环：
  VirtualUser → P1 校准引擎 + P2 实时估计/预警 + P3 策略推荐 + P4 周报生成

运行方式：
  cd /workspace/emowave-engine && python3 run_simulation.py
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import json
import random
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from collections import defaultdict

# ================================================================
# 导入四个模块
# ================================================================
from models import (
    TimeSeriesSample,
    EmotionEventRaw,
    EventProfile,
    DailySummary,
    PersonalThresholds,
    BaselineVector,
    BaselineShiftEvent,
    AlertLevel,
    ModelSource,
)
from engine import EmoCalibrationEngine
from kalman_filter import (
    EmotionKalmanFilter,
    KalmanConfig,
    SliderObservation,
    PhysioInput,
    EmotionState,
)
from predictor import PredictionEngine, PredictionConfig, WarningLevel
from recommender import ContextualBandit, Context, DEFAULT_STRATEGIES
from report_generator import (
    generate_weekly_report,
    WeeklyData,
    WeeklyReport,
)
from simulator import (
    generate_emotion_trajectory,
    generate_physio_signals,
    TrajectoryPoint,
)
from dashboard_data import FrameRecorder


# ================================================================
# 终端配色
# ================================================================
C_RESET = "\033[0m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_CYAN = "\033[36m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"


# ================================================================
# 触发因素与策略目录
# ================================================================

TRIGGER_CATALOG = {
    "工作会议": 1,
    "通勤压力": 2,
    "社交冲突": 3,
    "任务截止": 4,
    "睡眠不足": 5,
    "家庭事务": 6,
    "财务压力": 7,
    "健康担忧": 8,
}


# ================================================================
# Ground Truth：策略在不同情境下的真实效果
# ================================================================

def compute_true_rating(strategy_id: str, context: Dict[str, Any]) -> float:
    """
    计算某策略在当前情境下的真实效果评分（1-5）。
    用于模拟用户的反馈，让推荐系统有可学习的目标。
    """
    arousal = context.get("arousal", 0.5)
    valence = context.get("valence", 0.5)
    trigger = context.get("trigger", "")
    hour = context.get("hour", 12)

    base = 3.0

    # --- 按策略类别调整 ---
    if strategy_id in ("deep_breathing", "body_scan", "progressive_relax"):
        # 呼吸类：低唤醒时效果最好
        if arousal < 0.4:
            base += 1.2
        elif arousal < 0.7:
            base += 0.8
        else:
            base += 0.3
    elif strategy_id in ("short_walk", "stretching"):
        # 运动类：高唤醒时效果最好
        if arousal > 0.6:
            base += 1.0
        elif arousal > 0.4:
            base += 0.6
        else:
            base += 0.2
    elif strategy_id in ("listen_music", "journaling", "grounding_543"):
        # 认知类：中等唤醒效果好
        if 0.3 < arousal < 0.7:
            base += 0.8
        else:
            base += 0.4
    elif strategy_id == "cold_water":
        # 感官类：极高唤醒时效果最好
        if arousal > 0.75:
            base += 1.3
        else:
            base += 0.1
    elif strategy_id == "talk_friend":
        # 社交类：白天效果好
        if 8 <= hour <= 18:
            base += 0.9
        else:
            base += 0.3

    # --- 按触发因素调整 ---
    if trigger in ("工作会议", "任务截止") and strategy_id in ("deep_breathing", "short_walk"):
        base += 0.3
    elif trigger == "社交冲突" and strategy_id == "talk_friend":
        base += 0.4
    elif trigger == "睡眠不足" and strategy_id in ("listen_music", "progressive_relax"):
        base += 0.3

    # --- 效价影响 ---
    if valence < 0.3:
        base -= 0.2

    # --- 噪声 ---
    noise = np.random.normal(0, 0.4)
    return float(np.clip(base + noise, 1.0, 5.0))


# ================================================================
# 数据结构：VirtualUser 的输出
# ================================================================

@dataclass
class SleepData:
    """一夜的睡眠数据"""
    duration_hours: float
    quality_score: float  # 0-10


@dataclass
class EMAResult:
    """一次 EMA（生态瞬时评估）结果"""
    timestamp: float
    valence: float
    arousal: float


@dataclass
class SimulatedEvent:
    """一次模拟的情绪事件"""
    trigger: str
    trigger_code: int
    start_time: float          # Unix 时间戳
    duration_sec: int
    trajectory: List[TrajectoryPoint]
    physio_signals: List[Optional[PhysioInput]]
    observations: List[SliderObservation]
    peak_valence: float
    peak_arousal: float


# ================================================================
# 虚拟用户模拟器
# ================================================================

class VirtualUser:
    """
    虚拟用户模拟器。

    模拟一个具有特定性格特质的人，在 N 天内的情绪起伏。
    所有随机性由 rng_seed 控制，保证结果可复现。
    """

    def __init__(self, seed: int = 42):
        self.rng = np.random.RandomState(seed)
        self.random = random.Random(seed)

        # 用户特质
        self.traits = {
            "baseline_valence": 0.55,
            "baseline_arousal": 0.30,
            "stress_sensitivity": 0.65,
            "recovery_bias": 0.5,
        }

        # 历史记录
        self.sleep_history: List[SleepData] = []
        self.ema_history: List[EMAResult] = []
        self.events: List[SimulatedEvent] = []

    def generate_sleep(self, day_index: int, prev_day_events: int) -> SleepData:
        """生成一夜睡眠数据。前一天事件多则睡眠差。"""
        base_duration = 7.5
        if prev_day_events > 0:
            base_duration -= prev_day_events * 0.6

        duration = float(np.clip(self.rng.normal(base_duration, 0.7), 4.5, 10.0))
        quality = float(np.clip(duration * 0.9 + self.rng.normal(0, 0.8), 2.0, 9.5))
        return SleepData(duration_hours=duration, quality_score=quality)

    def generate_ema(self, timestamp: float, sleep_quality: float,
                     time_label: str) -> EMAResult:
        """生成一次 EMA 情绪评估。"""
        base_v = self.traits["baseline_valence"]
        base_a = self.traits["baseline_arousal"]

        base_v += (sleep_quality - 6.0) * 0.025

        if time_label == "morning":
            valence = base_v + self.rng.normal(0, 0.06)
            arousal = base_a + self.rng.normal(0, 0.05)
        else:  # evening
            valence = base_v - 0.05 + self.rng.normal(0, 0.08)
            arousal = base_a + 0.1 + self.rng.normal(0, 0.07)

        return EMAResult(
            timestamp=timestamp,
            valence=float(np.clip(valence, 0, 1)),
            arousal=float(np.clip(arousal, 0, 1)),
        )

    def generate_emotion_event(
        self,
        day_index: int,
        sleep_quality: float,
        base_timestamp: float,
    ) -> Optional[SimulatedEvent]:
        """
        生成一次情绪事件。
        触发概率受睡眠质量和压力敏感度影响。
        """
        trigger_prob = 0.20 + self.traits["stress_sensitivity"] * 0.15
        if sleep_quality < 5.0:
            trigger_prob += 0.15

        if self.rng.random() >= trigger_prob:
            return None

        # --- 触发因素 ---
        triggers = list(TRIGGER_CATALOG.keys())
        weights = [0.30, 0.12, 0.08, 0.20, 0.10, 0.12, 0.04, 0.04]
        trigger = self.random.choices(triggers, weights=weights)[0]
        trigger_code = TRIGGER_CATALOG[trigger]

        # --- 事件发生时刻（8:00 - 21:00）---
        event_hour = int(self.rng.choice([9, 10, 11, 14, 15, 16, 17, 20, 21]))
        event_minute = int(self.rng.randint(0, 59))
        start_time = base_timestamp + event_hour * 3600 + event_minute * 60

        # --- 事件参数 ---
        duration = int(self.rng.randint(180, 600))  # 3-10 分钟
        peak_arousal = float(self.rng.uniform(0.65, 0.95))
        peak_valence = float(self.rng.uniform(0.05, 0.25))

        # --- 生成轨迹 ---
        trajectory = generate_emotion_trajectory(
            duration_sec=duration,
            dt=1.0,
            start_valence=0.55,
            start_arousal=0.30,
            peak_arousal=peak_arousal,
            peak_valence=peak_valence,
            peak_time_fraction=float(self.rng.uniform(0.2, 0.5)),
            recovery_speed=float(self.rng.uniform(0.4, 0.8)),
            noise_std=0.05,
            seed=int(self.rng.randint(0, 100000)),
        )

        # --- 生成生理信号 ---
        true_arousals = [p.true_arousal for p in trajectory]
        physio_signals = generate_physio_signals(
            true_arousals,
            base_hr=72.0,
            base_hrv=50.0,
            seed=int(self.rng.randint(0, 100000)),
        )

        # --- 转换为带绝对时间戳的 SliderObservation ---
        observations = []
        for p in trajectory:
            observations.append(SliderObservation(
                timestamp=start_time + p.t,
                valence=p.obs_valence,
                arousal=p.obs_arousal,
                touch_velocity=p.touch_velocity,
                seconds_since_last_touch=p.stillness,
            ))

        return SimulatedEvent(
            trigger=trigger,
            trigger_code=trigger_code,
            start_time=start_time,
            duration_sec=duration,
            trajectory=trajectory,
            physio_signals=physio_signals,
            observations=observations,
            peak_valence=peak_valence,
            peak_arousal=peak_arousal,
        )


# ================================================================
# 系统集成主循环
# ================================================================

class SimulationRunner:
    """
    系统集成主循环。

    按时间顺序推进 N 天，串联四个模块：
      P1 校准引擎 → P2 实时估计/预警 → P3 策略推荐 → P4 周报生成
    """

    def __init__(self, start_date: datetime, days: int = 7, seed: int = 42):
        self.start_date = start_date
        self.days = days
        self.seed = seed

        # 四个模块实例
        self.engine = EmoCalibrationEngine(user_id="sim_user_001")
        self.kf = EmotionKalmanFilter(KalmanConfig())
        self.predictor = PredictionEngine(PredictionConfig())
        self.bandit = ContextualBandit(strategies=DEFAULT_STRATEGIES)

        # 虚拟用户
        self.user = VirtualUser(seed=seed)

        # 运行日志
        self.logs: List[Dict[str, Any]] = []

        # 汇总数据（用于周报）
        self.all_event_profiles: List[EventProfile] = []
        self.all_daily_summaries: List[DailySummary] = []
        self.all_baseline_shifts: List[BaselineShiftEvent] = []
        self.trigger_tags_map: Dict[str, List[str]] = {}
        self.coping_ratings_map: Dict[str, Dict[str, int]] = {}
        self.warning_log: List[Dict[str, Any]] = []

        # 当前日期的睡眠
        self.current_sleep: Optional[SleepData] = None

        # 帧数据记录器（仪表盘回放）
        self.frame_recorder = FrameRecorder()

    def _log(self, msg: str, level: str = "info"):
        """打印并记录日志。"""
        color = C_GREEN
        if level == "warn":
            color = C_YELLOW
        elif level == "alert":
            color = C_RED
        elif level == "system":
            color = C_CYAN

        print(f"  {color}{msg}{C_RESET}")
        self.logs.append({
            "msg": msg,
            "level": level,
            "time": datetime.now().isoformat(),
        })

    def _timestamp_for_day(self, day_index: int, hour: int = 0,
                           minute: int = 0) -> float:
        """计算某天的 Unix 时间戳。"""
        dt = self.start_date + timedelta(days=day_index, hours=hour,
                                          minutes=minute)
        return dt.timestamp()

    # ============================================================
    # 主入口
    # ============================================================

    def run(self) -> WeeklyReport:
        """运行完整的 N 天模拟。"""
        print(f"\n{C_BOLD}{'=' * 70}{C_RESET}")
        print(f"  {C_BOLD}心潮 EmoWave — 全系统端到端模拟运行{C_RESET}")
        print(f"  模拟周期: {self.start_date.strftime('%Y-%m-%d')} 起，"
              f"共 {self.days} 天")
        print(f"  模块: P1校准 + P2实时估计/预警 + P3策略推荐 + P4周报生成")
        print(f"{C_BOLD}{'=' * 70}{C_RESET}\n")

        for day in range(self.days):
            self._run_day(day)

        # 生成周报
        print(f"\n{C_BOLD}{'=' * 70}{C_RESET}")
        print(f"  {C_BOLD}第 {self.days} 天结束 — 生成周报{C_RESET}")
        print(f"{C_BOLD}{'=' * 70}{C_RESET}")

        report = self._generate_weekly_report()

        # 保存仪表盘回放数据
        self.frame_recorder.start_timestamp = self.start_date.isoformat()
        self.frame_recorder.end_timestamp = (
            self.start_date + timedelta(days=self.days)
        ).isoformat()
        self.frame_recorder.set_daily_summaries(
            [asdict(d) for d in self.all_daily_summaries]
        )
        self.frame_recorder.set_warnings(self.warning_log)
        self.frame_recorder.set_strategy_stats(self.bandit.get_strategy_stats())
        self.frame_recorder.set_engine_diagnostics(self.engine.diagnostics())
        self.frame_recorder.save(
            "/workspace/emowave-engine/dashboard/static/data/simulation_frames.json"
        )

        return report

    # ============================================================
    # 单日循环
    # ============================================================

    def _run_day(self, day_index: int):
        """运行一天。"""
        date_str = (self.start_date + timedelta(days=day_index)).strftime(
            "%Y-%m-%d"
        )
        weekday = (self.start_date + timedelta(days=day_index)).weekday()
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]

        print(f"\n{C_BOLD}【第 {day_index + 1} 天】{date_str}  "
              f"周{weekday_names[weekday]}{C_RESET}")
        print(f"  {'─' * 60}")

        # --- 清晨：睡眠数据 ---
        prev_events = sum(
            1 for e in self.user.events
            if datetime.fromtimestamp(e.start_time).date()
            == (self.start_date + timedelta(days=day_index - 1)).date()
        )
        sleep = self.user.generate_sleep(day_index, prev_events)
        self.current_sleep = sleep

        self._log(
            f"🌙 睡眠: {sleep.duration_hours:.1f}小时, "
            f"质量 {sleep.quality_score:.1f}/10"
        )

        # --- 早晨 EMA ---
        morning_ts = self._timestamp_for_day(day_index, 8, 0)
        morning_ema = self.user.generate_ema(
            morning_ts, sleep.quality_score, "morning"
        )
        self.user.ema_history.append(morning_ema)
        self._log(
            f"🌅 早晨 EMA: valence={morning_ema.valence:.2f}, "
            f"arousal={morning_ema.arousal:.2f}"
        )

        # --- 白天：情绪事件 ---
        event = self.user.generate_emotion_event(
            day_index, sleep.quality_score,
            self._timestamp_for_day(day_index, 0, 0)
        )

        if event:
            self.user.events.append(event)
            self._process_emotion_event(event, day_index, weekday,
                                        sleep.quality_score)
        else:
            self._log("✓ 今日无情绪事件")

        # --- 晚上 EMA ---
        evening_ts = self._timestamp_for_day(day_index, 21, 0)
        evening_ema = self.user.generate_ema(
            evening_ts, sleep.quality_score, "evening"
        )
        self.user.ema_history.append(evening_ema)
        self._log(
            f"🌙 晚间 EMA: valence={evening_ema.valence:.2f}, "
            f"arousal={evening_ema.arousal:.2f}"
        )

        # --- 日终：DailySummary → P1 update_daily ---
        daily = self._build_daily_summary(day_index, event)
        self.all_daily_summaries.append(daily)

        shift = self.engine.update_daily(daily)
        if shift:
            self.all_baseline_shifts.append(shift)
            self._log(f"⚠ 基线漂移告警: {shift.message}", level="warn")

        self._log(
            f"📊 日终更新完成，事件数={daily.event_count}, "
            f"引擎事件总数={len(self.engine._event_profiles)}"
        )

    # ============================================================
    # 情绪事件处理（核心闭环）
    # ============================================================

    def _process_emotion_event(
        self,
        event: SimulatedEvent,
        day_index: int,
        weekday: int,
        sleep_quality: float,
    ):
        """
        处理一次情绪事件：
          1. P2 实时追踪 + 预警检查
          2. P1 处理完整事件数据，更新阈值
          3. P3 推荐策略
          4. 模拟用户反馈，更新 P3
        """
        self._log(
            f"🔥 情绪事件触发: [{event.trigger}] "
            f"预计持续 {event.duration_sec}秒",
            level="warn",
        )

        # --- 步骤 1：P2 实时追踪 ---
        self.kf.init(
            valence=event.trajectory[0].true_valence,
            arousal=event.trajectory[0].true_arousal,
        )
        thresholds = self.engine.get_thresholds()

        event_id = f"evt_d{day_index}_{int(event.start_time)}"
        warning_triggered = False
        warning_time = None
        max_filtered_intensity = 0.0
        result = None
        event_warning = None

        for i, (obs, physio) in enumerate(
            zip(event.observations, event.physio_signals)
        ):
            if physio is not None:
                state = self.kf.update_with_control(obs, physio)
            else:
                state = self.kf.update(obs)

            if state.intensity > max_filtered_intensity:
                max_filtered_intensity = state.intensity

            # 记录当前帧数据
            hr = 72.0 + physio.hr_change if physio is not None else None
            hrv = 50.0 * (1.0 - physio.hrv_drop_ratio) if physio is not None else None
            self.frame_recorder.add_frame(
                t=event.trajectory[i].t,
                day=day_index,
                type="event",
                event_id=event_id,
                frame_idx=i,
                valence_raw=obs.valence,
                arousal_raw=obs.arousal,
                valence_kf=state.valence,
                arousal_kf=state.arousal,
                intensity=state.intensity,
                intensity_dot=state.intensity_dot,
                hr=hr,
                hrv=hrv,
                warning_level=result.warning_level.value if result else "NONE",
                threshold_arousal=thresholds.high_risk_arousal,
                threshold_valence=thresholds.high_risk_valence,
            )

            # 每 5 秒检查一次预警（跳过前 15 秒的 KF 预热期）
            if i >= 15 and i % 5 == 0:
                result = self.predictor.predict(
                    self.kf, thresholds, current_time=obs.timestamp
                )
                if result.warning_level in (
                    WarningLevel.WARNING,
                    WarningLevel.CRITICAL,
                ):
                    if not warning_triggered:
                        warning_triggered = True
                        warning_time = obs.timestamp - event.start_time
                        level_str = (
                            "WARNING"
                            if result.warning_level == WarningLevel.WARNING
                            else "CRITICAL"
                        )
                        self._log(
                            f"  🚨 {level_str} 预警触发! "
                            f"提前量={warning_time:.0f}s, "
                            f"理由: {result.reason}",
                            level="alert",
                        )
                        event_warning = {
                            "day": day_index,
                            "trigger": event.trigger,
                            "level": level_str,
                            "lead_time_sec": warning_time,
                            "reason": result.reason,
                        }
                        self.warning_log.append(event_warning)

        # --- 步骤 2：P1 处理完整事件 ---
        samples = []
        for p, physio in zip(event.trajectory, event.physio_signals):
            hr = None
            hrv = None
            if physio is not None:
                hr = 72.0 + physio.hr_change
                hrv = 50.0 * (1 - physio.hrv_drop_ratio)
            samples.append(TimeSeriesSample(
                timestamp=event.start_time + p.t,
                valence=p.obs_valence,
                arousal=p.obs_arousal,
                hr=hr,
                hrv=hrv,
            ))

        raw_event = EmotionEventRaw(
            event_id=event_id,
            samples=samples,
            user_peak_rating=None,
            recovery_duration=event.duration_sec,
            trigger_tags=[event.trigger],
            coping_methods=[],
            coping_ratings={},
            body_symptoms=[],
            calm_timestamp=event.start_time + event.duration_sec,
        )

        profile, updated_thresholds = self.engine.process_event(raw_event)
        self.all_event_profiles.append(profile)

        self._log(
            f"  📋 P1 事件标注: peak_arousal={profile.peak_arousal:.2f}, "
            f"recovery={profile.recovery_duration:.0f}s, "
            f"threshold_source={updated_thresholds.model_source.value}, "
            f"confidence={updated_thresholds.model_confidence:.2f}"
        )

        # --- 步骤 3：P3 策略推荐 ---
        # 用事件峰值时的情境构造 context
        peak_idx = max(
            range(len(event.trajectory)),
            key=lambda i: event.trajectory[i].true_arousal,
        )
        peak_obs = event.observations[peak_idx]
        event_hour = (event.start_time % 86400) / 3600

        context = Context.from_raw(
            valence=peak_obs.valence,
            arousal=peak_obs.arousal,
            hour=event_hour,
            weekday=weekday,
            sleep=sleep_quality,
            trigger_code=event.trigger_code,
        )

        rec = self.bandit.recommend(context)
        self._log(
            f"  💡 P3 策略推荐: 「{rec.strategy_name}」 "
            f"(预测评分 {rec.predicted_score:.1f}, UCB={rec.ucb_score:.2f})"
        )

        # --- 步骤 4：模拟用户采纳并评分 ---
        true_rating = compute_true_rating(
            rec.strategy_id,
            {
                "arousal": event.peak_arousal,
                "valence": event.peak_valence,
                "trigger": event.trigger,
                "hour": event_hour,
            },
        )
        int_rating = int(round(np.clip(true_rating, 1, 5)))

        self.bandit.update(rec.strategy_id, context, float(int_rating))
        self._log(
            f"  ⭐ 用户评分: {int_rating}/5 "
            f"(真实效果 {true_rating:.1f})"
        )

        # 记录事件元数据（仪表盘回放）
        self.frame_recorder.add_event_meta(
            event_id=raw_event.event_id,
            day=day_index,
            trigger=event.trigger,
            start_time=event.start_time,
            duration_sec=event.duration_sec,
            peak_arousal=event.peak_arousal,
            peak_valence=event.peak_valence,
            strategy_name=rec.strategy_name,
            user_rating=int_rating,
            warning_dict=event_warning,
        )

        # 记录外部映射（用于周报）
        self.trigger_tags_map[raw_event.event_id] = [event.trigger]
        self.coping_ratings_map[raw_event.event_id] = {
            rec.strategy_id: int_rating
        }

    # ============================================================
    # 辅助方法
    # ============================================================

    def _build_daily_summary(
        self, day_index: int, event: Optional[SimulatedEvent]
    ) -> DailySummary:
        """构建当日 DailySummary。"""
        date_str = (self.start_date + timedelta(days=day_index)).strftime(
            "%Y-%m-%d"
        )
        baseline = self.engine.get_baseline()

        peak_arousal_max = event.peak_arousal if event else 0.0

        return DailySummary(
            date=date_str,
            avg_resting_hrv=baseline.resting_hrv_mean
            + np.random.normal(0, 2),
            avg_resting_hr=baseline.resting_hr + np.random.normal(0, 1),
            sleep_score=self.current_sleep.quality_score
            if self.current_sleep
            else 7.0,
            morning_valence_avg=0.55,
            evening_valence_avg=0.50,
            event_count=1 if event else 0,
            peak_arousal_max=peak_arousal_max,
        )

    def _generate_weekly_report(self) -> WeeklyReport:
        """调用 P4 生成周报。"""
        date_start = self.start_date.strftime("%Y-%m-%d")
        date_end = (
            self.start_date + timedelta(days=self.days - 1)
        ).strftime("%Y-%m-%d")

        weekly_data = WeeklyData(
            date_range=(date_start, date_end),
            event_profiles=self.all_event_profiles,
            daily_summaries=self.all_daily_summaries,
            baseline_shifts=self.all_baseline_shifts,
        )

        report = generate_weekly_report(
            weekly_data=weekly_data,
            trigger_tags_map=self.trigger_tags_map,
            coping_ratings_map=self.coping_ratings_map,
        )
        return report

    def save_simulation_data(self, filepath: str):
        """保存所有模拟数据为 JSON。"""
        data = {
            "logs": self.logs,
            "event_count": len(self.all_event_profiles),
            "daily_summary_count": len(self.all_daily_summaries),
            "baseline_shift_count": len(self.all_baseline_shifts),
            "warning_count": len(self.warning_log),
            "warnings": self.warning_log,
            "strategy_stats": self.bandit.get_strategy_stats(),
            "engine_diagnostics": self.engine.diagnostics(),
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n  💾 模拟数据已保存至: {filepath}")


# ================================================================
# 主入口
# ================================================================

def main():
    """主入口。"""
    start = datetime(2026, 7, 1, 0, 0, 0)
    runner = SimulationRunner(start_date=start, days=7, seed=42)

    report = runner.run()

    # 打印周报
    print(f"\n{C_BOLD}{'=' * 70}{C_RESET}")
    print(f"  {C_BOLD}📰 周报全文{C_RESET}")
    print(f"{C_BOLD}{'=' * 70}{C_RESET}")
    print()
    print(f"{C_BOLD}{report.title}{C_RESET}")
    print()
    for section in report.sections:
        print(f"\n{C_CYAN}【{section.heading}】{C_RESET}")
        print(section.content)

    # 保存数据
    runner.save_simulation_data(
        "/workspace/emowave-engine/simulation_output.json"
    )

    # 统计摘要
    print(f"\n{C_BOLD}{'=' * 70}{C_RESET}")
    print(f"  {C_BOLD}📊 模拟运行摘要{C_RESET}")
    print(f"{C_BOLD}{'=' * 70}{C_RESET}")
    print(f"  总情绪事件数: {len(runner.all_event_profiles)}")
    print(f"  预警触发次数: {len(runner.warning_log)}")
    print(f"  基线漂移告警: {len(runner.all_baseline_shifts)}")
    print(f"  策略推荐次数: {runner.bandit._total_recommendations}")
    print(f"  周报段落数: {len(report.sections)}")

    # 策略学习效果
    print(f"\n  {C_BOLD}策略学习统计:{C_RESET}")
    stats = runner.bandit.get_strategy_stats()
    for sid, s in sorted(
        stats.items(), key=lambda x: x[1]["n_samples"], reverse=True
    )[:5]:
        print(
            f"    {s['name']}: {s['n_samples']} 次, "
            f"均奖 {s['avg_reward']}, theta_norm={s['theta_norm']}"
        )

    print()


if __name__ == "__main__":
    main()
