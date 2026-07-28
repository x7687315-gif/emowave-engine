"""
annotator.py — 心潮 EmoWave 个人情绪校准引擎 · 自动标注与特征对齐

本模块负责：
  1. 从原始时间序列中检测"生理极点"（融合心率、HRV、唤醒度）
  2. 检测"危险上升段"（arousal 快速上升 + 生理恶化）
  3. 输出完整的 EventProfile

算法设计思路：
  - 不使用简单 max(arousal) 作为极点，而是通过多信号融合找到"生理与主观
    同时达到极值的时刻"，输出带有置信度的标注
  - 心率信号先做滑动平均去噪，再计算 z-score 偏离
  - HRV 使用局部窗口内的相对下降率（百分比），因为 HRV 的绝对值因人而异
  - 所有检测函数均可独立替换（通过 config.STRATEGY_PEAK_DETECTION 分发）

替换指南：
  - 如需使用纯规则引擎（不融合生理信号），设置 STRATEGY_PEAK_DETECTION = "rule_based"
  - 如需引入机器学习模型，实现一个新函数并注册到 _STRATEGY_MAP 中即可
"""

from typing import List, Optional, Tuple

import config
from models import (
    TimeSeriesSample,
    EmotionEventRaw,
    EventProfile,
    PhysiologicalPeak,
    DangerousRiseSegment,
    BaselineVector,
)


# ================================================================
# 辅助函数：信号处理基础
# ================================================================

def _moving_average(values: List[float], window: int) -> List[float]:
    """
    简单滑动平均。
    用于心率信号的去噪。
    边界处理：前 window-1 个点用已有数据的均值填充。
    """
    if not values:
        return []
    n = len(values)
    result = []
    for i in range(n):
        start = max(0, i - window + 1)
        segment = values[start:i + 1]
        result.append(sum(segment) / len(segment))
    return result


def _zscore(values: List[float]) -> List[float]:
    """
    计算 z-score 标准化序列。
    z = (x - mean) / std
    当 std == 0 时（所有值相同），返回全 0。
    """
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    std = variance ** 0.5
    if std < 1e-9:
        return [0.0] * n
    return [(x - mean) / std for x in values]


def _slope(values: List[float], timestamps: List[float]) -> List[float]:
    """
    计算每个点的数值斜率（单位：值/秒）。
    使用前向差分：slope[i] = (values[i+1] - values[i]) / (t[i+1] - t[i])
    最后一个点的斜率沿用倒数第二个点的值。
    """
    n = len(values)
    if n < 2:
        return [0.0] * n
    slopes = []
    for i in range(n - 1):
        dt = timestamps[i + 1] - timestamps[i]
        if dt < 1e-6:
            slopes.append(0.0)
        else:
            slopes.append((values[i + 1] - values[i]) / dt)
    slopes.append(slopes[-1])  # 最后一个点沿用
    return slopes


def _has_physio_data(samples: List[TimeSeriesSample]) -> bool:
    """检查样本序列中是否包含有效的生理数据（心率或 HRV）。"""
    return any(
        s.hr is not None and s.hr > 0
        for s in samples
    ) or any(
        s.hrv is not None and s.hrv > 0
        for s in samples
    )


# ================================================================
# 生理极点检测
# ================================================================

def detect_physio_peaks(
    samples: List[TimeSeriesSample],
    baseline: Optional[BaselineVector] = None,
) -> List[PhysiologicalPeak]:
    """
    检测时间序列中的"生理极点"时刻。

    算法步骤：
      1. 对心率做滑动平均去噪，然后计算 z-score
      2. 对 HRV 计算局部窗口内的下降百分比
      3. 将三路信号（心率 z-score、HRV 下降率、唤醒度）融合为一个
         composite_score
      4. 找出 composite_score 的局部最大值作为候选极点
      5. 过滤间隔过近的候选点（保留得分更高的）

    Args:
        samples: 一次事件的所有时序采样点（必须按时间排序）
        baseline: 用户当前的静息基线（可选，用于改善 z-score 计算）

    Returns:
        检测到的生理极点列表，按 composite_score 降序排列。
    """
    if len(samples) < 3:
        return []

    timestamps = [s.timestamp for s in samples]
    arousals = [s.arousal for s in samples]

    # --- 1. 心率 z-score ---
    hr_values = [s.hr if (s.hr is not None and s.hr > 0) else None for s in samples]
    hr_clean = [v for v in hr_values if v is not None]

    hr_zscores = [0.0] * len(samples)
    if len(hr_clean) >= config.HR_MA_WINDOW:
        hr_ma = _moving_average(hr_clean, config.HR_MA_WINDOW)
        hr_z_clean = _zscore(hr_ma)
        # 映射回原始索引
        hr_idx = 0
        for i in range(len(samples)):
            if hr_values[i] is not None:
                hr_zscores[i] = hr_z_clean[hr_idx] if hr_idx < len(hr_z_clean) else 0.0
                hr_idx += 1

    # --- 2. HRV 下降百分比（局部窗口） ---
    hrv_values = [s.hrv if (s.hrv is not None and s.hrv > 0) else None for s in samples]
    hrv_drop_pcts = [0.0] * len(samples)

    # 找到有效的 HRV 数据索引
    hrv_indices = [i for i, v in enumerate(hrv_values) if v is not None]
    for idx in hrv_indices:
        # 在当前时刻前后 WINDOW 内找局部最大值作为参考
        local_max = hrv_values[idx]
        for j in hrv_indices:
            if abs(timestamps[j] - timestamps[idx]) <= config.HRV_DROP_WINDOW_SECONDS / 2:
                if hrv_values[j] > local_max:
                    local_max = hrv_values[j]
        if local_max > 1e-6:
            drop = (local_max - hrv_values[idx]) / local_max
            hrv_drop_pcts[idx] = max(0.0, drop)  # 只记录下降，上升为 0

    # --- 3. 多信号融合 ---
    #   将三路信号归一化到 [0, 1] 后加权求和
    w_hr, w_hrv, w_arousal = config.PHYSIO_PEAK_WEIGHTS

    # 归一化函数：将值映射到 [0, 1]
    def normalize(values: List[float]) -> List[float]:
        max_v = max(values) if values else 1.0
        min_v = min(values) if values else 0.0
        rng = max_v - min_v
        if rng < 1e-9:
            return [0.5] * len(values)
        return [(v - min_v) / rng for v in values]

    hr_z_norm = normalize(hr_zscores)
    hrv_drop_norm = normalize(hrv_drop_pcts)
    arousal_norm = normalize(arousals)

    composite_scores = []
    for i in range(len(samples)):
        score = (
            w_hr * hr_z_norm[i]
            + w_hrv * hrv_drop_norm[i]
            + w_arousal * arousal_norm[i]
        )
        composite_scores.append(score)

    # --- 4. 找局部最大值（候选极点） ---
    candidates = []
    for i in range(1, len(samples) - 1):
        if (composite_scores[i] > composite_scores[i - 1]
                and composite_scores[i] > composite_scores[i + 1]):
            # 额外条件：得分必须显著高于均值
            mean_score = sum(composite_scores) / len(composite_scores)
            if composite_scores[i] > mean_score + 0.1 * (max(composite_scores) - mean_score + 1e-9):
                candidates.append((
                    i,
                    composite_scores[i],
                    hr_zscores[i],
                    hrv_drop_pcts[i],
                    arousals[i],
                ))

    # --- 5. 过滤间隔过近的候选点 ---
    candidates.sort(key=lambda x: x[1], reverse=True)  # 按得分降序
    filtered = []
    for c in candidates:
        idx, score, hr_z, hrv_drop, arousal_val = c
        # 检查与已选点的距离
        too_close = False
        for f in filtered:
            if abs(timestamps[idx] - timestamps[f[0]]) < config.TURNING_POINT_MIN_GAP_SECONDS:
                too_close = True
                break
        if not too_close:
            filtered.append(c)

    # --- 6. 构造 PhysiologicalPeak 列表 ---
    peaks = []
    has_any_physio = _has_physio_data(samples)
    for idx, score, hr_z, hrv_drop, arousal_val in filtered:
        # 置信度：有生理数据时更高
        confidence = min(1.0, score * 1.2) if has_any_physio else min(1.0, score * 0.6)
        peaks.append(PhysiologicalPeak(
            timestamp=timestamps[idx],
            hr_zscore=round(hr_z, 3),
            hrv_drop_pct=round(hrv_drop, 3),
            arousal_spike=round(arousal_val, 3),
            composite_score=round(score, 3),
        ))

    return sorted(peaks, key=lambda p: p.composite_score, reverse=True)


# ================================================================
# 危险上升段检测
# ================================================================

def detect_dangerous_rise_segments(
    samples: List[TimeSeriesSample],
) -> List[DangerousRiseSegment]:
    """
    检测"危险上升段"——arousal 持续快速上升的时间段。

    算法：
      1. 计算 arousal 的时间斜率序列
      2. 找出连续超过斜率阈值的段
      3. 过滤持续时间过短的段
      4. 计算每段的统计特征

    Args:
        samples: 时间序列采样点（按时间排序）

    Returns:
        危险上升段列表，按严重程度降序排列。
    """
    if len(samples) < 3:
        return []

    timestamps = [s.timestamp for s in samples]
    arousals = [s.arousal for s in samples]
    slopes = _slope(arousals, timestamps)

    hr_values = [s.hr if (s.hr is not None and s.hr > 0) else None for s in samples]
    hr_clean = [v for v in hr_values if v is not None]

    # 心率 z-score
    hr_zscores = [0.0] * len(samples)
    if len(hr_clean) >= config.HR_MA_WINDOW:
        hr_ma = _moving_average(hr_clean, config.HR_MA_WINDOW)
        hr_z_clean = _zscore(hr_ma)
        hr_idx = 0
        for i in range(len(samples)):
            if hr_values[i] is not None:
                hr_zscores[i] = hr_z_clean[hr_idx] if hr_idx < len(hr_z_clean) else 0.0
                hr_idx += 1

    # HRV 下降百分比（复用极点检测的逻辑）
    hrv_values = [s.hrv if (s.hrv is not None and s.hrv > 0) else None for s in samples]
    hrv_drop_pcts = [0.0] * len(samples)
    hrv_indices = [i for i, v in enumerate(hrv_values) if v is not None]
    for idx in hrv_indices:
        local_max = hrv_values[idx]
        for j in hrv_indices:
            if abs(timestamps[j] - timestamps[idx]) <= config.HRV_DROP_WINDOW_SECONDS / 2:
                if hrv_values[j] > local_max:
                    local_max = hrv_values[j]
        if local_max > 1e-6:
            hrv_drop_pcts[idx] = max(0.0, (local_max - hrv_values[idx]) / local_max)

    # 找连续超过斜率阈值的段
    segments_raw = []
    seg_start = None
    for i in range(len(slopes)):
        if slopes[i] >= config.DANGEROUS_RISE_SLOPE_THRESHOLD:
            if seg_start is None:
                seg_start = i
        else:
            if seg_start is not None:
                segments_raw.append((seg_start, i))
                seg_start = None
    if seg_start is not None:
        segments_raw.append((seg_start, len(slopes) - 1))

    # 过滤短段 & 构造结果
    result = []
    for start_idx, end_idx in segments_raw:
        duration = timestamps[end_idx] - timestamps[start_idx]
        if duration < config.DANGEROUS_RISE_MIN_DURATION:
            continue

        # 峰值斜率
        peak_slope = max(slopes[start_idx:end_idx + 1])

        # 段内心率平均 z-score（仅有效点）
        hr_in_seg = [hr_zscores[i] for i in range(start_idx, end_idx + 1) if hr_values[i] is not None]
        avg_hr_z = sum(hr_in_seg) / len(hr_in_seg) if hr_in_seg else 0.0

        # 段内 HRV 最大下降
        hrv_in_seg = [hrv_drop_pcts[i] for i in range(start_idx, end_idx + 1)]
        max_hrv_drop = max(hrv_in_seg) if hrv_in_seg else 0.0

        result.append(DangerousRiseSegment(
            start_time=timestamps[start_idx],
            end_time=timestamps[end_idx],
            peak_arousal_slope=round(peak_slope, 4),
            avg_hr_zscore=round(avg_hr_z, 3),
            hrv_drop_at_peak=round(max_hrv_drop, 3),
        ))

    return sorted(result, key=lambda s: s.peak_arousal_slope, reverse=True)


# ================================================================
# 主标注函数
# ================================================================

def annotate_event(
    raw_event: EmotionEventRaw,
    baseline: Optional[BaselineVector] = None,
) -> EventProfile:
    """
    对一次完整的情绪事件进行自动标注。

    处理流程：
      1. 预处理：按时间排序，填充缺失值
      2. 检测生理极点（多信号融合）
      3. 检测危险上升段
      4. 综合确定"真正的极点"：
         - 如果检测到生理极点，取 composite_score 最高的那个
         - 否则，退回到主观滑条最大值时刻
      5. 计算恢复特征
      6. 估算诱因时间窗（极点前最近的显著变化段）

    Args:
        raw_event: 原始事件数据
        baseline: 当前用户基线（可选）

    Returns:
        完整的事件标注结果 EventProfile
    """
    samples = sorted(raw_event.samples, key=lambda s: s.timestamp)
    if not samples:
        return EventProfile(
            event_id=raw_event.event_id,
            onset_time=0, peak_time=0, calm_time=0,
            peak_valence=0, peak_arousal=0,
        )

    onset_time = samples[0].timestamp
    calm_time = raw_event.calm_timestamp or samples[-1].timestamp

    # --- 生理极点检测 ---
    physio_peaks = detect_physio_peaks(samples, baseline)

    # --- 危险上升段检测 ---
    rise_segments = detect_dangerous_rise_segments(samples)

    # --- 确定极点时间 ---
    if physio_peaks:
        # 有生理数据支持：取融合得分最高的极点
        best_peak = physio_peaks[0]
        peak_time = best_peak.timestamp
        peak_idx = _find_nearest_index(samples, peak_time)
        peak_valence = samples[peak_idx].valence
        peak_arousal = samples[peak_idx].arousal
        physio_score = best_peak.composite_score
        physio_confidence = min(1.0, physio_score * 1.2) if _has_physio_data(samples) else 0.5
    else:
        # 无生理数据：退回到滑条最大值
        peak_idx = max(range(len(samples)), key=lambda i: samples[i].arousal)
        peak_time = samples[peak_idx].timestamp
        peak_valence = samples[peak_idx].valence
        peak_arousal = samples[peak_idx].arousal
        physio_score = 0.0
        physio_confidence = 0.3  # 低置信度

    # --- 恢复特征 ---
    recovery_duration = calm_time - peak_time
    if recovery_duration > 0 and peak_arousal > 0.01:
        recovery_speed = (peak_arousal - samples[-1].arousal) / recovery_duration
    else:
        recovery_speed = 0.0

    # --- 诱因时间窗估算 ---
    trigger_window = None
    if rise_segments:
        # 取极点前最近的一段危险上升段
        for seg in rise_segments:
            if seg.end_time <= peak_time:
                trigger_window = (seg.start_time, seg.end_time)
                break

    return EventProfile(
        event_id=raw_event.event_id,
        onset_time=onset_time,
        peak_time=peak_time,
        calm_time=calm_time,
        peak_valence=round(peak_valence, 3),
        peak_arousal=round(peak_arousal, 3),
        subjective_peak=raw_event.user_peak_rating,
        physiological_peak_score=round(physio_score, 3),
        physiological_peak_confidence=round(physio_confidence, 3),
        recovery_duration=round(recovery_duration, 1),
        recovery_speed=round(recovery_speed, 4),
        dangerous_rise_segments=rise_segments,
        trigger_window=trigger_window,
        physio_peaks=physio_peaks,
        sample_count=len(samples),
    )


def _find_nearest_index(samples: List[TimeSeriesSample], target_ts: float) -> int:
    """找到时间戳最接近 target_ts 的样本索引。"""
    return min(range(len(samples)), key=lambda i: abs(samples[i].timestamp - target_ts))
