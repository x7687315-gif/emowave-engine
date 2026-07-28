"""
baseline.py — 心潮 EmoWave 个人情绪校准引擎 · 个人基线建模与漂移检测

本模块负责：
  1. 维护每个用户的"静息基线向量"（每日更新）
  2. 使用指数加权移动平均（EWMA）平滑更新基线
  3. 检测基线漂移（连续多日偏离超过阈值）

算法选择理由：
  - EWMA：计算量极低（O(1)），适合设备端实时更新；
    alpha=1/8 对应约 7 天等效窗口，适合捕捉周级别趋势
  - 变点检测：使用"连续 N 天超过 K 个标准差"的简单规则，
    相比 BOCPD 等贝叶斯方法，无需维护后验分布，内存占用最小

替换指南：
  - 若需更高灵敏度的变点检测，设置 config.STRATEGY_SHIFT_DETECTION = "bocpd"
    并实现 detect_shift_bocpd() 函数
  - 若需季节性分解，可在 update_baseline() 中添加 STL 分解步骤
"""

from typing import Optional, List, Dict

import config
from models import (
    BaselineVector,
    DailySummary,
    BaselineShiftEvent,
    AlertLevel,
)


class BaselineManager:
    """
    个人基线管理器。

    职责：
      - 接收每日摘要，更新基线向量
      - 维护基线历史记录
      - 检测基线漂移并生成告警

    使用方式：
      manager = BaselineManager()
      manager.update_baseline(today_summary)
      alert = manager.detect_shift()
    """

    def __init__(self, initial_baseline: Optional[BaselineVector] = None):
        """
        Args:
            initial_baseline: 初始基线（可选）。
                若为 None，将使用第一次 update_baseline() 的数据初始化。
        """
        self._baseline = initial_baseline or BaselineVector()
        self._history: List[BaselineVector] = []
        # 存储原始每日输入值（未经 EWMA 平滑），用于漂移检测
        self._raw_daily_values: List[dict] = []
        if initial_baseline and initial_baseline.date:
            self._history.append(initial_baseline)

    @property
    def current_baseline(self) -> BaselineVector:
        """获取当前基线向量的只读视图。"""
        return self._baseline

    @property
    def history(self) -> List[BaselineVector]:
        """获取基线历史记录。"""
        return list(self._history)

    @property
    def history_length(self) -> int:
        """已积累的历史天数。"""
        return len(self._history)

    # ================================================================
    # 核心：更新基线
    # ================================================================

    def update_baseline(self, daily: DailySummary) -> BaselineVector:
        """
        使用每日摘要更新静息基线向量。

        算法：EWMA（指数加权移动平均）
          baseline_new = alpha * x_new + (1 - alpha) * baseline_old

        每个维度独立更新，alpha 由 config.EWMA_ALPHA 控制。
        首次调用时直接使用输入值初始化（不做平滑）。

        Args:
            daily: 当日的 DailySummary 数据

        Returns:
            更新后的 BaselineVector
        """
        alpha = config.EWMA_ALPHA
        bl = self._baseline

        if not self._history:
            # 首次初始化：直接使用当日数据
            bl = BaselineVector(
                resting_hrv_mean=daily.avg_resting_hrv,
                resting_hr=daily.avg_resting_hr,
                sleep_score=daily.sleep_score,
                typical_valence_8am=daily.morning_valence_avg,
                typical_valence_6pm=daily.evening_valence_avg,
                date=daily.date,
            )
        else:
            # EWMA 更新
            bl.resting_hrv_mean = alpha * daily.avg_resting_hrv + (1 - alpha) * bl.resting_hrv_mean
            bl.resting_hr = alpha * daily.avg_resting_hr + (1 - alpha) * bl.resting_hr
            bl.sleep_score = alpha * daily.sleep_score + (1 - alpha) * bl.sleep_score
            bl.typical_valence_8am = alpha * daily.morning_valence_avg + (1 - alpha) * bl.typical_valence_8am
            bl.typical_valence_6pm = alpha * daily.evening_valence_avg + (1 - alpha) * bl.typical_valence_6pm
            bl.date = daily.date

        self._baseline = bl
        self._history.append(bl)

        # 存储原始每日值（用于漂移检测时对比原始输入，而非平滑后的基线）
        self._raw_daily_values.append({
            "date": daily.date,
            "resting_hrv": daily.avg_resting_hrv,
            "resting_hr": daily.avg_resting_hr,
            "sleep_score": daily.sleep_score,
            "morning_valence": daily.morning_valence_avg,
            "evening_valence": daily.evening_valence_avg,
        })

        # 限制历史长度，防止内存无限增长
        if len(self._history) > config.MAX_BASELINE_HISTORY_DAYS:
            self._history = self._history[-config.MAX_BASELINE_HISTORY_DAYS:]
            self._raw_daily_values = self._raw_daily_values[-config.MAX_BASELINE_HISTORY_DAYS:]

        return bl

    # ================================================================
    # 核心：漂移检测
    # ================================================================

    def detect_shift(self) -> Optional[BaselineShiftEvent]:
        """
        检测基线是否发生了显著漂移。

        算法（std_cumulative 策略）：
          1. 从原始每日输入值（未经 EWMA 平滑）中计算参考期的均值和标准差
          2. 检查最近 N 天的原始值是否超过 mean ± K * std
          3. 如果某个维度连续 N 天都超过阈值，触发告警

        设计理由：
          使用原始每日输入而非 EWMA 平滑后的基线来做漂移检测，
          是因为 EWMA 的指数衰减会导致基线"追着"异常值跑，
          使平滑后的值永远不会有足够大的偏离。
          原始值能更忠实地反映实际生理状态的突然变化。

        Args:
            无（使用内部维护的 _raw_daily_values 和 _history）

        Returns:
            BaselineShiftEvent（如果检测到漂移），否则 None
        """
        raw = self._raw_daily_values
        n = len(raw)

        # 历史数据不足时无法进行漂移检测
        if n < config.BASELINE_MIN_HISTORY_DAYS + config.SHIFT_CONSECUTIVE_DAYS:
            return None

        # 要检查的维度
        dim_keys = ["resting_hrv", "resting_hr", "sleep_score",
                     "morning_valence", "evening_valence"]

        shifted_dims = []
        shift_magnitudes = {}

        for dim_name in dim_keys:
            # 参考期：去掉最近 SHIFT_CONSECUTIVE_DAYS 天
            check_start = n - config.SHIFT_CONSECUTIVE_DAYS
            ref_values = [raw[i][dim_name] for i in range(check_start)]

            if len(ref_values) < config.BASELINE_MIN_HISTORY_DAYS:
                continue

            ref_mean = sum(ref_values) / len(ref_values)
            ref_std = _std(ref_values)

            if ref_std < 1e-9:
                continue  # 方差为零的维度跳过

            # 检查最近 N 天的原始值是否每天都偏离
            consecutive_shift = True
            for i in range(check_start, n):
                day_value = raw[i][dim_name]
                deviation = abs(day_value - ref_mean) / ref_std
                if deviation < config.SHIFT_STD_DEVIATIONS:
                    consecutive_shift = False
                    break

            if consecutive_shift:
                # 使用最近一天的偏离量作为报告值
                latest_deviation = abs(raw[-1][dim_name] - ref_mean) / ref_std
                shifted_dims.append(dim_name)
                shift_magnitudes[dim_name] = round(latest_deviation, 2)

        if not shifted_dims:
            return None

        # 确定告警级别
        max_dev = max(shift_magnitudes.values())
        if max_dev >= 3.0:
            level = AlertLevel.ACTION
        elif max_dev >= 2.5:
            level = AlertLevel.WARNING
        else:
            level = AlertLevel.INFO

        # 生成人类可读的描述
        dim_labels = {
            "resting_hrv": "静息HRV",
            "resting_hr": "静息心率",
            "sleep_score": "睡眠质量",
            "morning_valence": "晨间情绪",
            "evening_valence": "晚间情绪",
        }
        desc_parts = [f"{dim_labels.get(d, d)}偏离 {shift_magnitudes[d]:.1f}σ" for d in shifted_dims]
        message = f"检测到基线漂移：{'、'.join(desc_parts)}，连续{config.SHIFT_CONSECUTIVE_DAYS}天偏离正常范围。建议检查近期生活变化，必要时调整预警阈值。"

        return BaselineShiftEvent(
            alert_level=level,
            detected_date=self._baseline.date,
            shifted_dimensions=shifted_dims,
            shift_magnitudes=shift_magnitudes,
            message=message,
        )

    # ================================================================
    # 辅助方法
    # ================================================================

    def _get_dim_value(self, bl: BaselineVector, dim_name: str) -> float:
        """从 BaselineVector 中提取指定维度的值。"""
        mapping = {
            "resting_hrv": bl.resting_hrv_mean,
            "resting_hr": bl.resting_hr,
            "sleep_score": bl.sleep_score,
            "morning_valence": bl.typical_valence_8am,
            "evening_valence": bl.typical_valence_6pm,
        }
        return mapping.get(dim_name, 0.0)

    def get_baseline_stats(self) -> Dict[str, Dict[str, float]]:
        """
        获取每个基线维度的统计摘要（均值、标准差、当前值）。
        用于 UI 展示和调试。
        """
        result = {}
        dimensions = ["resting_hrv", "resting_hr", "sleep_score", "morning_valence", "evening_valence"]
        for dim in dimensions:
            values = [self._get_dim_value(h, dim) for h in self._history]
            if values:
                result[dim] = {
                    "mean": round(sum(values) / len(values), 2),
                    "std": round(_std(values), 2),
                    "current": round(values[-1], 2),
                    "n_days": len(values),
                }
        return result


def _std(values: List[float]) -> float:
    """计算总体标准差。"""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((x - mean) ** 2 for x in values) / n) ** 0.5
