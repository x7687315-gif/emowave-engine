"""
predictor.py — 心潮 EmoWave 极点提前预警引擎

本模块负责：
  1. 从卡尔曼滤波器的当前状态进行短期外推
  2. 判断外推轨迹是否会进入危险区
  3. 计算最优预警提前量（平衡"足够反应时间"与"不过早打扰"）
  4. 输出预警级别和预估到达极点时间

设计理念：
  - 预警的核心矛盾：太早预警用户会"狼来了"效应（降低采纳率），
    太晚预警用户来不及反应（增加漏报率）。
  - 本模块通过"最优提前量"概念来平衡：
    lead_time = max(min_lead, min(到达危险区时间 - safety_margin, max_lead))

替换指南：
  - 若需更精确的预测（非线性轨迹），可替换 _extrapolate_with_model()
  - 若需个性化 lead_time 参数，可从 PersonalThresholds 中读取
  - 若需接入 A/B 测试，通过 PredictionConfig 的 ab_test_variant 分发
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional, List, Tuple
from enum import Enum

from kalman_filter import (
    EmotionKalmanFilter,
    KalmanConfig,
    EmotionState,
)
from models import PersonalThresholds


# ================================================================
# 配置
# ================================================================

@dataclass
class PredictionConfig:
    """预警引擎的可调参数。"""

    # 外推参数
    extrapolation_horizon_sec: float = 600.0   # 外推 10 分钟
    extrapolation_dt_sec: float = 1.0           # 步长 1 秒

    # 有效预测窗口：只关心此窗口内的预测
    #   理由：超过此时间的预测不确定性太大，不应触发预警
    max_prediction_window_sec: float = 90.0      # 最多提前 1.5 分钟预测有效

    # 预警提前量
    min_lead_time_sec: float = 30.0             # 至少提前 30 秒预警
    max_lead_time_sec: float = 180.0            # 最多提前 3 分钟预警
    safety_margin_sec: float = 15.0            # 安全边际（从预测到达时间减去）

    # 危险区定义（与 PersonalThresholds 对齐）
    #   当 valence < high_risk_valence AND arousal > high_risk_arousal 时视为危险区
    #   这些默认值会被 get_personalized_thresholds() 动态覆盖

    # 预警级别阈值（intensity 空间中的边界）
    warning_intensity: float = 0.65             # 进入 WARNING 级（提高以减少误报）
    critical_intensity: float = 0.78             # 进入 CRITICAL 级

    # 最小强度变化率要求（intensity_dot > 此值才触发 WARNING/CRITICAL）
    #   理由：强度本身高但不变化（稳定情绪）不应触发预警；
    #         只有强度在上升（趋势恶化）时才需要预警
    min_intensity_dot_for_warning: float = 0.001  # 单位/秒

    # 当前强度最低门槛：当前强度必须超过此值才考虑预警
    #   理由：强度很低时（如 0.3），即使外推显示会到达危险区，
    #         也不应触发预警——因为到达危险区需要很长时间，不确定性太大
    min_current_intensity_for_warning: float = 0.45

    # 预警触发要求：外推峰值必须显著高于当前强度
    #   理由：如果外推峰值和当前强度差不多，说明轨迹已经很平，
    #         即使碰巧过了阈值也不需要紧急预警
    min_peak_excess: float = 0.20  # 峰值必须比当前强度高出至少 0.20

    # A/B 测试 & RL 接口
    ab_test_variant: str = "default"


# ================================================================
# 数据结构
# ================================================================

class WarningLevel(Enum):
    """预警级别"""
    NONE = "none"           # 安全，无需预警
    WATCH = "watch"         # 关注，轨迹可能趋近危险区
    WARNING = "warning"      # 预警，预计将进入危险区
    CRITICAL = "critical"    # 紧急，已处于或即将到达危险区


@dataclass
class PredictionResult:
    """
    预警引擎的输出。
    """
    warning_level: WarningLevel
    current_intensity: float = 0.0               # 当前情绪强度
    intensity_dot: float = 0.0                  # 强度变化率
    estimated_time_to_peak: Optional[float] = None  # 预估到达危险区的秒数（None = 不会到达）
    optimal_lead_time: Optional[float] = None     # 最优预警提前量（秒）
    peak_intensity: float = 0.0                  # 外推期内预测的最大强度
    extrapolation_valid: bool = True              # 外推是否在有效范围内
    reason: str = ""                              # 人类可读的预警理由


# ================================================================
# 预警引擎
# ================================================================

class PredictionEngine:
    """
    极点提前预警引擎。

    使用方式：
      predictor = PredictionEngine(config)
      result = predictor.predict(kalman_filter, thresholds)
      if result.warning_level != WarningLevel.NONE:
          trigger_alert(result)
    """

    def __init__(self, config: Optional[PredictionConfig] = None):
        self.config = config or PredictionConfig()
        self._last_warning_level = WarningLevel.NONE
        self._warning_cooldown: float = 0.0     # 预警冷却计时器（避免频繁重复预警）
        self._cooldown_duration: float = 60.0    # 同级预警的最小间隔

    @property
    def last_warning_level(self) -> WarningLevel:
        return self._last_warning_level

    def predict(
        self,
        kf: EmotionKalmanFilter,
        thresholds: PersonalThresholds,
        current_time: float = 0.0,
    ) -> PredictionResult:
        """
        基于当前滤波器状态预测是否需要预警。

        流程：
          1. 获取当前状态
          2. 外推未来轨迹
          3. 检查轨迹是否进入危险区
          4. 计算预警级别和最优提前量
          5. 应用冷却逻辑避免重复预警

        Args:
            kf: 已更新的卡尔曼滤波器实例
            thresholds: 个性化警戒阈值
            current_time: 当前时间戳

        Returns:
            PredictionResult 预测结果
        """
        # --- 1. 当前状态 ---
        state = kf._to_state(current_time)

        # --- 2. 外推轨迹 ---
        trajectory = kf.extrapolate(
            horizon_sec=self.config.extrapolation_horizon_sec,
            dt=self.config.extrapolation_dt_sec,
        )

        # --- 3. 检查轨迹是否进入危险区（仅在有效预测窗口内） ---
        danger_entry_time = self._find_danger_entry_time(
            trajectory, thresholds
        )
        # 超过有效预测窗口的发现视为"不会在近期发生"
        if danger_entry_time is not None and danger_entry_time > self.config.max_prediction_window_sec:
            danger_entry_time = None

        # --- 4. 检查轨迹是否达到 WARNING 级强度 ---
        warning_entry_time = self._find_intensity_crossing_time(
            trajectory, self.config.warning_intensity
        )
        if warning_entry_time is not None and warning_entry_time > self.config.max_prediction_window_sec:
            warning_entry_time = None

        # --- 5. 检查轨迹是否达到 CRITICAL 级强度 ---
        critical_entry_time = self._find_intensity_crossing_time(
            trajectory, self.config.critical_intensity
        )
        if critical_entry_time is not None and critical_entry_time > self.config.max_prediction_window_sec:
            critical_entry_time = None

        # --- 确定预警级别 ---
        peak_intensity = max(s.intensity for s in trajectory)
        peak_time_idx = max(range(len(trajectory)), key=lambda i: trajectory[i].intensity)
        peak_time_sec = trajectory[peak_time_idx].timestamp - trajectory[0].timestamp

        # 前置条件：当前强度必须足够高才有意义去预警
        intensity_sufficient = state.intensity >= self.config.min_current_intensity_for_warning
        derivative_positive = state.intensity_dot > self.config.min_intensity_dot_for_warning

        # 前置条件：外推峰值必须显著高于当前强度
        peak_excess = peak_intensity - state.intensity
        peak_sufficient = peak_excess >= self.config.min_peak_excess

        if not intensity_sufficient or not peak_sufficient:
            # 强度太低，不需要预警
            warning_level = WarningLevel.NONE
            time_to_event = None
            reason = "情绪状态稳定"

        if intensity_sufficient and danger_entry_time is not None and derivative_positive:
            warning_level = WarningLevel.CRITICAL
            time_to_event = danger_entry_time
            reason = f"预计 {time_to_event:.0f} 秒后进入高唤醒低效价危险区"
        elif intensity_sufficient and critical_entry_time is not None and derivative_positive:
            warning_level = WarningLevel.CRITICAL
            time_to_event = critical_entry_time
            reason = f"预计 {time_to_event:.0f} 秒后强度达到临界值 {self.config.critical_intensity:.2f}"
        elif intensity_sufficient and warning_entry_time is not None and derivative_positive:
            warning_level = WarningLevel.WARNING
            time_to_event = warning_entry_time
            reason = f"预计 {time_to_event:.0f} 秒后强度达到警戒值 {self.config.warning_intensity:.2f}"
        elif state.intensity_dot > 0.005 and state.intensity > 0.45:
            # 虽然未达到预警阈值，但强度在加速上升且已偏高
            warning_level = WarningLevel.WATCH
            time_to_event = None
            reason = "情绪强度加速上升中，持续关注"
        else:
            warning_level = WarningLevel.NONE
            time_to_event = None
            reason = "情绪状态稳定"

        # --- 7. 计算最优预警提前量 ---
        optimal_lead = None
        if time_to_event is not None:
            optimal_lead = self._compute_optimal_lead_time(time_to_event)

        # --- 8. 构造结果 ---
        return PredictionResult(
            warning_level=warning_level,
            current_intensity=state.intensity,
            intensity_dot=state.intensity_dot,
            estimated_time_to_peak=time_to_event,
            optimal_lead_time=optimal_lead,
            peak_intensity=round(peak_intensity, 3),
            extrapolation_valid=True,
            reason=reason,
        )

    # ============================================================
    # 内部方法
    # ============================================================

    def _find_danger_entry_time(
        self,
        trajectory: List[EmotionState],
        thresholds: PersonalThresholds,
    ) -> Optional[float]:
        """
        在外推轨迹中找到第一个进入危险区的时间点。

        危险区定义：valence < high_risk_valence AND arousal > high_risk_arousal

        Returns:
            进入危险区的时间（秒），如果不会进入则返回 None
        """
        if not trajectory:
            return None

        t0 = trajectory[0].timestamp
        for state in trajectory:
            if (state.valence < thresholds.high_risk_valence
                    and state.arousal > thresholds.high_risk_arousal):
                return state.timestamp - t0
        return None

    def _find_intensity_crossing_time(
        self,
        trajectory: List[EmotionState],
        threshold: float,
    ) -> Optional[float]:
        """
        在外推轨迹中找到强度首次超过阈值的时间。

        Args:
            trajectory: 外推轨迹
            threshold: 强度阈值

        Returns:
            超过阈值的时间（秒），如果不会超过则返回 None
        """
        if not trajectory:
            return None

        t0 = trajectory[0].timestamp
        for state in trajectory:
            if state.intensity >= threshold:
                return state.timestamp - t0
        return None

    def _compute_optimal_lead_time(self, time_to_event: float) -> float:
        """
        计算最优预警提前量。

        约束：
          - lead_time >= min_lead_time（给用户足够反应时间）
          - lead_time <= max_lead_time（避免过早打扰）
          - lead_time = time_to_event - safety_margin

        设计理由：
          safety_margin 确保预警触发后用户还有一定缓冲时间。
          如果 time_to_event - safety_margin < min_lead_time，
          说明已经来不及了，立即预警。
        """
        cfg = self.config
        ideal = time_to_event - cfg.safety_margin_sec
        lead = max(cfg.min_lead_time_sec, min(ideal, cfg.max_lead_time_sec))
        return round(lead, 1)

    # ============================================================
    # A/B 测试 & RL 调参接口（预留）
    # ============================================================

    def record_feedback(
        self,
        warning_level: WarningLevel,
        user_action: str,
        effectiveness: Optional[int] = None,
    ) -> None:
        """
        记录用户对预警的反馈。

        预留接口，将来用于：
          - A/B 测试：比较不同预警参数的采纳率
          - 强化学习：根据用户反馈调整预警阈值

        Args:
            warning_level: 发出的预警级别
            user_action: 用户行为（"breathing", "dismiss", "ignore", "escalate"）
            effectiveness: 有效性评分（1-5，可选）
        """
        # TODO: 接入 A/B 测试框架和 RL 参数优化器
        pass
