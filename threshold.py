"""
threshold.py — 心潮 EmoWave 个人情绪校准引擎 · 冷启动与渐进过渡

本模块负责：
  1. 管理冷启动→个人模型的渐进切换
  2. 计算模型置信度
  3. 输出个性化的情绪极点警戒阈值

设计原则：
  - 事件数 < N 时使用群体通用阈值（安全保守）
  - 随着事件积累，逐步增加个人化权重
  - 当置信度超过阈值时自动切换到纯个人模型
  - 所有阈值都附带 model_confidence 和 model_source，方便 UI 层展示

替换指南：
  - 若需使用更复杂的置信度模型（如贝叶斯后验概率），
    替换 _compute_confidence() 的内部实现即可
  - 若需添加新的阈值维度，修改 PersonalThresholds 数据结构和
    _compute_personal_thresholds() 即可
"""

from typing import Dict, Optional, List

import config
from models import (
    PersonalThresholds,
    ModelSource,
    EventProfile,
    BaselineVector,
)


class ThresholdManager:
    """
    阈值管理器。

    职责：
      - 根据事件积累量和个人数据计算个性化阈值
      - 管理冷启动→混合→纯个人的三阶段过渡
      - 输出带置信度的阈值供预警系统使用

    使用方式：
      tm = ThresholdManager()
      thresholds = tm.get_personalized_thresholds()
    """

    def __init__(self):
        self._event_count: int = 0
        self._baseline_age_days: int = 0
        self._recent_peak_arousals: List[float] = []
        self._recent_peak_valences: List[float] = []
        self._model_confidence: float = 0.0
        self._model_source: ModelSource = ModelSource.POPULATION

    @property
    def model_confidence(self) -> float:
        """当前模型置信度（0-1）。"""
        return self._model_confidence

    @property
    def model_source(self) -> ModelSource:
        """当前阈值来源。"""
        return self._model_source

    # ================================================================
    # 核心：获取个性化阈值
    # ================================================================

    def get_personalized_thresholds(self) -> PersonalThresholds:
        """
        获取当前的个性化情绪极点警戒阈值。

        根据当前事件积累量和置信度，自动选择：
          - POPULATION：冷启动阶段，纯群体阈值
          - HYBRID：过渡阶段，群体与个人混合
          - PERSONAL：纯个人模型

        Returns:
            PersonalThresholds 包含所有警戒阈值和元信息
        """
        # --- 更新置信度 ---
        self._model_confidence = self._compute_confidence()

        # --- 确定模型来源 ---
        if self._event_count < config.COLD_START_MIN_EVENTS:
            self._model_source = ModelSource.POPULATION
        elif self._model_confidence < config.CONFIDENCE_SWITCH_THRESHOLD:
            self._model_source = ModelSource.HYBRID
        else:
            self._model_source = ModelSource.PERSONAL

        # --- 根据来源计算阈值 ---
        if self._model_source == ModelSource.POPULATION:
            thresholds = self._population_thresholds()
        elif self._model_source == ModelSource.HYBRID:
            thresholds = self._hybrid_thresholds()
        else:
            thresholds = self._personal_thresholds()

        thresholds.model_confidence = round(self._model_confidence, 3)
        thresholds.model_source = self._model_source
        thresholds.event_count = self._event_count

        return thresholds

    # ================================================================
    # 核心：录入事件数据（用于更新个人化参数）
    # ================================================================

    def ingest_event(
        self,
        profile: EventProfile,
        baseline: Optional[BaselineVector] = None,
    ) -> None:
        """
        将一次标注后的事件数据录入阈值管理器。

        每次 ingest 后会更新：
          - 事件计数
          - 近期极值缓存（用于计算个人阈值）
          - 置信度

        Args:
            profile: 已标注的事件数据
            baseline: 当前基线（可选，用于年龄计算）
        """
        self._event_count += 1

        # 缓存近期的极值（保留最近 50 次事件）
        self._recent_peak_arousals.append(profile.peak_arousal)
        self._recent_peak_valences.append(profile.peak_valence)
        if len(self._recent_peak_arousals) > 50:
            self._recent_peak_arousals = self._recent_peak_arousals[-50:]
            self._recent_peak_valences = self._recent_peak_valences[-50:]

    def set_baseline_age(self, days: int) -> None:
        """设置基线数据的积累天数。由 Engine 在调用时设置。"""
        self._baseline_age_days = days

    # ================================================================
    # 内部：三种阈值计算策略
    # ================================================================

    def _population_thresholds(self) -> PersonalThresholds:
        """
        纯群体通用阈值（冷启动阶段）。
        直接使用 config.POPULATION_THRESHOLDS 中的硬编码值。
        """
        return PersonalThresholds(
            high_risk_arousal=config.POPULATION_THRESHOLDS["high_risk_arousal"],
            high_risk_valence=config.POPULATION_THRESHOLDS["high_risk_valence"],
            hrv_drop_percent=config.POPULATION_THRESHOLDS["hrv_drop_percent"],
            hr_surge_zscore=config.POPULATION_THRESHOLDS["hr_surge_zscore"],
            dangerous_rise_slope=config.POPULATION_THRESHOLDS["dangerous_rise_slope"],
        )

    def _personal_thresholds(self) -> PersonalThresholds:
        """
        纯个人化阈值。
        基于用户历史事件的统计分布计算。
        """
        if len(self._recent_peak_arousals) < 5:
            # 数据不足，退回群体阈值
            return self._population_thresholds()

        # 唤醒度阈值：取历史峰值的 P75 分位数
        arousal_threshold = _percentile(self._recent_peak_arousals, 75)

        # 效价阈值：取历史峰值的 P25 分位数（越低越危险）
        valence_values = [1.0 - v for v in self._recent_peak_valences]  # 反转：高值 = 高负性
        valence_threshold = 1.0 - _percentile(valence_values, 75)  # 反转回来

        # HRV 下降和心率阈值暂用群体值（需更多生理数据才能个性化）
        hrv_drop = config.POPULATION_THRESHOLDS["hrv_drop_percent"]
        hr_z = config.POPULATION_THRESHOLDS["hr_surge_zscore"]
        slope = config.POPULATION_THRESHOLDS["dangerous_rise_slope"]

        return PersonalThresholds(
            high_risk_arousal=round(arousal_threshold, 3),
            high_risk_valence=round(max(0.05, valence_threshold), 3),
            hrv_drop_percent=hrv_drop,
            hr_surge_zscore=hr_z,
            dangerous_rise_slope=slope,
        )

    def _hybrid_thresholds(self) -> PersonalThresholds:
        """
        混合阈值：按置信度在群体阈值和个人阈值之间加权插值。

        公式：threshold_hybrid = confidence * threshold_personal + (1 - confidence) * threshold_population

        置信度越高，越接近个人阈值。
        """
        pop = self._population_thresholds()
        personal = self._personal_thresholds()
        w = self._model_confidence

        return PersonalThresholds(
            high_risk_arousal=round(w * personal.high_risk_arousal + (1 - w) * pop.high_risk_arousal, 3),
            high_risk_valence=round(w * personal.high_risk_valence + (1 - w) * pop.high_risk_valence, 3),
            hrv_drop_percent=round(w * personal.hrv_drop_percent + (1 - w) * pop.hrv_drop_percent, 3),
            hr_surge_zscore=round(w * personal.hr_surge_zscore + (1 - w) * pop.hr_surge_zscore, 3),
            dangerous_rise_slope=round(w * personal.dangerous_rise_slope + (1 - w) * pop.dangerous_rise_slope, 4),
        )

    # ================================================================
    # 内部：置信度计算
    # ================================================================

    def _compute_confidence(self) -> float:
        """
        计算当前个人化模型的置信度（0-1）。

        三维度加权：
          1. 事件数量维度（40%）：事件越多越可信
          2. 基线年龄维度（25%）：基线历史越长越可信
          3. 一致性维度（35%）：近期极值分布越稳定越可信

        各维度独立映射到 [0, 1] 后加权求和。
        """
        w_count, w_age, w_consistency = config.CONFIDENCE_WEIGHTS

        # --- 1. 事件数量得分 ---
        #   Sigmoid 函数：0 次时为 0，COLD_START_MIN_EVENTS 次时约 0.73，
        #   40 次时约 0.95
        count_score = _sigmoid(
            (self._event_count - config.COLD_START_MIN_EVENTS * 0.5) / (config.COLD_START_MIN_EVENTS * 0.3)
        )

        # --- 2. 基线年龄得分 ---
        #   线性映射：0 天 = 0，7 天 = 0.5，30 天 = 1.0
        age_score = min(1.0, self._baseline_age_days / 30.0)

        # --- 3. 一致性得分 ---
        #   基于近期峰值变异系数（CV）的倒数
        #   CV 越小，说明用户的情绪模式越稳定，模型越可信
        consistency_score = 0.5  # 默认值
        if len(self._recent_peak_arousals) >= 5:
            cv = _cv(self._recent_peak_arousals)
            if cv > 0:
                # CV = 0.1（非常稳定）→ 得分 ~0.9
                # CV = 0.5（不稳定）→ 得分 ~0.3
                consistency_score = max(0.0, min(1.0, 1.0 - cv * 1.2))

        total = w_count * count_score + w_age * age_score + w_consistency * consistency_score
        return round(max(0.0, min(1.0, total)), 3)


# ================================================================
# 辅助函数
# ================================================================

def _sigmoid(x: float) -> float:
    """Logistic sigmoid 函数。"""
    if x > 20:
        return 1.0
    if x < -20:
        return 0.0
    return 1.0 / (1.0 + 2.718281828 ** (-x))


def _percentile(values: List[float], p: int) -> float:
    """
    计算第 p 百分位数（线性插值法）。
    p 范围：0-100。
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return sorted_vals[-1]
    d = k - f
    return sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f])


def _cv(values: List[float]) -> float:
    """计算变异系数（CV = std / mean）。"""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    if abs(mean) < 1e-9:
        return 0.0
    variance = sum((x - mean) ** 2 for x in values) / n
    return (variance ** 0.5) / abs(mean)
