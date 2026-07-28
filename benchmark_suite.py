#!/usr/bin/env python3
"""
benchmark_suite.py — 心潮 EmoWave 基准测试套件

本模块对 EmoWave 情绪追踪引擎进行端到端的系统模拟基准测试，
使用 data_simulator_v2.py 生成的多用户画像模拟数据集，
量化评估以下核心维度：

  1. 预警能力：召回率、精确率、F1、提前量
  2. 推荐质量：累积遗憾、最优策略命中率、平均奖励
  3. 状态估计精度：RMSE（效价、唤醒、强度）
  4. 模型收敛：个人化模型达到高置信度所需事件数

运行方式：
  cd /workspace/emowave-engine && python3 benchmark_suite.py
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import os
import json
import glob
import math
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any

# ================================================================
# 导入引擎核心模块
# ================================================================
from models import (
    TimeSeriesSample,
    EmotionEventRaw,
    DailySummary,
    PersonalThresholds,
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

# ================================================================
# 触发因素目录 & 真实评分函数（与 data_simulator_v2 保持一致）
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


def compute_true_rating(strategy_id: str, context: Dict[str, Any]) -> float:
    """
    计算某策略在当前情境下的真实效果评分（1-5）。
    与 data_simulator_v2 / run_simulation.py 中的实现保持一致，
    用于评估 Bandit 推荐系统的遗憾值。
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

    return float(np.clip(base, 1.0, 5.0))


# ================================================================
# 辅助函数
# ================================================================

def compute_intensity(valence: float, arousal: float) -> float:
    """
    计算情绪强度：intensity = sqrt(v^2 + a^2) / sqrt(2)
    归一化到 [0, 1] 区间。
    """
    return min(1.0, math.sqrt(valence ** 2 + arousal ** 2) / math.sqrt(2))


# ================================================================
# 基准测试结果数据结构
# ================================================================

@dataclass
class BenchmarkResult:
    """
    单个数据集（用户画像）的基准测试结果。
    包含预警、推荐、拟合、模型收敛四大维度的指标。
    """
    archetype_name: str                      # 画像中文名（如"高压白领型"）
    archetype_name_en: str                   # 画像英文名（如"high_pressure_white_collar"）
    total_events: int                        # 总事件数
    total_days: int                           # 总天数

    # --- 预警指标 ---
    warning_recall: float                     # 召回率（危险事件中被正确预警的比例）
    warning_precision: float                 # 精确率（预警中真正危险的比例）
    warning_f1: float                         # F1 分数
    avg_lead_time_sec: float                  # 平均预警提前量（秒）
    total_warnings: int                       # 总预警次数
    true_warnings: int                        # 真正的预警（TP）
    false_alarms: int                         # 虚警（FP）
    missed_events: int                       # 漏报（FN）

    # --- 推荐指标 ---
    cumulative_regret: float                  # 累积遗憾值
    best_strategy_hit_rate: float             # 最优策略命中率
    avg_bandit_reward: float                 # Bandit 平均奖励

    # --- 拟合指标 ---
    rmse_valence: float                       # 效价 RMSE
    rmse_arousal: float                       # 唤醒 RMSE
    rmse_intensity: float                     # 强度 RMSE
    max_intensity_error: float                # 最大强度误差

    # --- 模型收敛 ---
    events_to_high_confidence: int            # 达到 confidence >= 0.5 的事件数
    final_model_confidence: float             # 最终模型置信度
    final_model_source: str                   # 最终模型来源（"population"/"hybrid"/"personal"）

    # --- 综合评分 ---
    combined_score: float                     # 综合评分（加权）

    # --- 每事件详细数据 ---
    per_event_details: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """将结果转换为可序列化的字典。"""
        d = asdict(self)
        return d


# ================================================================
# 基准测试套件
# ================================================================

class BenchmarkSuite:
    """
    EmoWave 基准测试套件。

    用法：
      suite = BenchmarkSuite(data_dir="./test_data", output_dir="./benchmark_results")
      results = suite.run_all_benchmarks()
      report = suite.generate_markdown_report(results)
    """

    def __init__(
        self,
        data_dir: str = "./test_data",
        output_dir: str = "./benchmark_results",
    ):
        """
        初始化基准测试套件。

        Args:
            data_dir: 测试数据 JSON 文件所在目录
            output_dir: 测试结果输出目录
        """
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.warning_intensity_threshold = 0.65  # 危险事件强度阈值
        self.prediction_check_interval = 5.0        # 预警检查间隔（秒）

        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)

    # ============================================================
    # 数据集发现
    # ============================================================

    def discover_datasets(self) -> List[str]:
        """
        自动发现 data_dir 中的 *7days.json 文件。

        Returns:
            匹配的 JSON 文件绝对路径列表
        """
        pattern = os.path.join(self.data_dir, "*7days.json")
        files = sorted(glob.glob(pattern))
        return files

    # ============================================================
    # 单数据集基准测试
    # ============================================================

    def run_benchmark(self, dataset_path: str) -> BenchmarkResult:
        """
        对单个数据集运行完整系统模拟。

        流程：
          1. 加载 JSON 数据
          2. 初始化引擎组件（KF, Predictor, Engine, Bandit）
          3. 逐天处理：
             a. 从 trajectory/physio_signals 构造 SliderObservation + PhysioInput
             b. 运行 KF 实时追踪
             c. 每5秒检查预警
             d. 构造 EmotionEventRaw → engine.process_event()
             e. 在事件峰值时用 Bandit 推荐
             f. 用真实评分更新 Bandit
          4. 计算所有核心指标
          5. 返回 BenchmarkResult

        Args:
            dataset_path: JSON 数据文件路径

        Returns:
            BenchmarkResult 包含所有指标的结果
        """
        # --- 1. 加载数据 ---
        with open(dataset_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        archetype = data["archetype"]
        daily_data = data["daily_data"]
        archetype_name = archetype["name"]
        archetype_name_en = archetype["name_en"]

        # --- 2. 初始化引擎组件 ---
        kf_config = KalmanConfig()
        kf = EmotionKalmanFilter(kf_config)

        pred_config = PredictionConfig()
        predictor = PredictionEngine(pred_config)

        engine = EmoCalibrationEngine(user_id=f"bench_{archetype_name_en}")

        bandit = ContextualBandit(strategies=DEFAULT_STRATEGIES)

        # 获取初始阈值（群体默认值）
        thresholds = engine.get_thresholds()

        # 用第一天早晨 EMA 初始化 KF
        first_morning = daily_data[0].get("morning_ema")
        if first_morning:
            kf.init(
                valence=first_morning["valence"],
                arousal=first_morning["arousal"],
            )
        else:
            kf.init(valence=0.5, arousal=0.3)

        # --- 3. 逐天处理 ---
        per_event_details = []

        # 统计累加器
        total_warnings = 0
        true_warnings = 0
        false_alarms = 0
        missed_events = 0
        lead_times = []

        cumulative_regret = 0.0
        bandit_rewards = []
        best_hit_count = 0
        total_events_with_recommendation = 0

        # 拟合误差累加
        valence_errors = []
        arousal_errors = []
        intensity_errors = []

        # 模型收敛追踪
        events_to_high_confidence = None
        final_model_confidence = 0.0
        final_model_source = "population"

        total_events = 0

        for day_idx, day in enumerate(daily_data):
            date = day["date"]
            weekday_index = day.get("weekday_index", day_idx % 7)
            sleep_data = day.get("sleep", {})
            sleep_quality = sleep_data.get("quality_score", 7.0)

            events = day.get("events", [])
            if not events:
                # 当天无事件，仍可构造 DailySummary 更新引擎基线
                self._update_engine_daily(engine, day, sleep_data, events)
                continue

            for evt in events:
                total_events += 1
                event_id = evt["event_id"]
                trigger = evt["trigger"]
                trigger_code = evt.get("trigger_code", 0)
                start_time = evt["start_time"]
                end_time = evt["end_time"]
                peak_valence_evt = evt.get("peak_valence", 0.0)
                peak_arousal_evt = evt.get("peak_arousal", 0.0)

                # 事件峰值强度（真值）
                peak_intensity = compute_intensity(peak_valence_evt, peak_arousal_evt)
                is_dangerous = peak_intensity > self.warning_intensity_threshold

                # --- 3a. 从 trajectory 构造观测和生理输入 ---
                trajectory = evt.get("trajectory", [])
                physio_signals = evt.get("physio_signals", [])

                # 构造时间索引映射：便于快速查找对应时刻的生理信号
                physio_by_time = {}
                if physio_signals:
                    for ps in physio_signals:
                        if ps is not None:
                            physio_by_time[ps["t"]] = ps

                # --- 3b. KF 实时追踪 ---
                event_warning_fired = False
                event_warning_level = "NONE"
                event_warning_time = None
                event_lead_time = None

                # 找到峰值时间索引（基于 true_arousal）
                peak_idx = 0
                peak_true_arousal = 0.0
                for i, pt in enumerate(trajectory):
                    if pt.get("true_arousal", 0) > peak_true_arousal:
                        peak_true_arousal = pt["true_arousal"]
                        peak_idx = i

                # 事件维度追踪累加器
                event_valence_errors = []
                event_arousal_errors = []
                event_intensity_errors = []

                last_check_time = -self.prediction_check_interval

                for pt_idx, pt in enumerate(trajectory):
                    t = pt["t"]
                    timestamp = start_time + t

                    # 构造 SliderObservation
                    obs = SliderObservation(
                        timestamp=timestamp,
                        valence=pt["obs_valence"],
                        arousal=pt["obs_arousal"],
                        touch_velocity=pt.get("touch_velocity", 0.0),
                        seconds_since_last_touch=pt.get("stillness", 0.0),
                    )

                    # 查找对应时刻的生理信号（允许一定容差）
                    physio = None
                    for ps in physio_signals:
                        if ps is not None and abs(ps["t"] - t) < 1.5:
                            physio = PhysioInput(
                                timestamp=timestamp + ps["t"] - t,
                                hrv_drop_ratio=ps["hrv_drop_ratio"],
                                hr_change=ps["hr_change"],
                                signal_quality=ps.get("signal_quality", 1.0),
                            )
                            break

                    # KF 更新（有生理信号时使用融合更新）
                    if physio is not None:
                        state = kf.update_with_control(obs, physio)
                    else:
                        state = kf.update(obs)

                    # 累计拟合误差
                    true_v = pt.get("true_valence", pt["obs_valence"])
                    true_a = pt.get("true_arousal", pt["obs_arousal"])
                    true_i = compute_intensity(true_v, true_a)

                    event_valence_errors.append((state.valence - true_v) ** 2)
                    event_arousal_errors.append((state.arousal - true_a) ** 2)
                    event_intensity_errors.append((state.intensity - true_i) ** 2)

                    # --- 3c. 每5秒检查预警 ---
                    if t - last_check_time >= self.prediction_check_interval:
                        last_check_time = t
                        # 更新阈值（使用引擎最新阈值）
                        thresholds = engine.get_thresholds()
                        pred_result = predictor.predict(kf, thresholds, timestamp)

                        if pred_result.warning_level in (
                            WarningLevel.WARNING, WarningLevel.CRITICAL
                        ):
                            if not event_warning_fired:
                                # 首次触发预警
                                event_warning_fired = True
                                event_warning_level = pred_result.warning_level.value
                                event_warning_time = t

                                # 计算提前量：预警时刻到峰值时刻的时间差
                                peak_time = trajectory[peak_idx]["t"]
                                lead = peak_time - t
                                if lead > 0:
                                    event_lead_time = lead
                                    lead_times.append(lead)

                # --- 3d. 构造 EmotionEventRaw → engine.process_event() ---
                raw_event = self._build_raw_event(
                    event_id=event_id,
                    trajectory=trajectory,
                    physio_signals=physio_signals,
                    start_time=start_time,
                    trigger=trigger,
                    trigger_code=trigger_code,
                    evt=evt,
                )

                try:
                    profile, updated_thresholds = engine.process_event(raw_event)
                    thresholds = updated_thresholds

                    # --- 追踪模型收敛 ---
                    confidence = updated_thresholds.model_confidence
                    source = updated_thresholds.model_source
                    final_model_confidence = confidence
                    final_model_source = source.value if hasattr(source, 'value') else str(source)

                    if events_to_high_confidence is None and confidence >= 0.5:
                        events_to_high_confidence = total_events
                except Exception as e:
                    # 引擎处理失败时使用默认阈值继续
                    print(f"  [警告] 事件 {event_id} 引擎处理失败: {e}")

                # --- 3e. 在事件峰值时用 Bandit 推荐 ---
                peak_pt = trajectory[peak_idx] if trajectory else None
                if peak_pt:
                    event_hour = (start_time % 86400) / 3600.0

                    context = Context.from_raw(
                        valence=peak_pt.get("obs_valence", peak_valence_evt),
                        arousal=peak_pt.get("obs_arousal", peak_arousal_evt),
                        hour=event_hour,
                        weekday=weekday_index,
                        sleep=sleep_quality,
                        trigger_code=trigger_code,
                    )

                    rec = bandit.recommend(context)

                    # --- 计算所有策略的真实评分，找出最优策略 ---
                    true_rating_context = {
                        "arousal": peak_arousal_evt,
                        "valence": peak_valence_evt,
                        "trigger": trigger,
                        "hour": event_hour,
                    }
                    best_score = -float('inf')
                    best_strategy_id = None
                    for strategy in DEFAULT_STRATEGIES:
                        score = compute_true_rating(strategy.id, true_rating_context)
                        if score > best_score:
                            best_score = score
                            best_strategy_id = strategy.id

                    # 实际推荐策略的真实评分
                    actual_score = compute_true_rating(rec.strategy_id, true_rating_context)
                    regret = best_score - actual_score

                    # --- 3f. 用真实评分更新 Bandit ---
                    int_rating = int(round(np.clip(actual_score, 1, 5)))
                    bandit.update(rec.strategy_id, context, float(int_rating))

                    # 累计推荐指标
                    cumulative_regret += regret
                    bandit_rewards.append(int_rating)

                    if rec.strategy_id == best_strategy_id:
                        best_hit_count += 1
                    total_events_with_recommendation += 1
                else:
                    rec = None
                    regret = 0.0
                    best_score = 0.0
                    best_strategy_id = None

                # --- 统计预警指标 ---
                if is_dangerous:
                    if event_warning_fired:
                        true_warnings += 1
                    else:
                        missed_events += 1
                else:
                    if event_warning_fired:
                        false_alarms += 1

                if event_warning_fired:
                    total_warnings += 1

                # --- 计算事件级拟合指标 ---
                event_rmse_i = math.sqrt(
                    sum(event_intensity_errors) / max(1, len(event_intensity_errors))
                )
                valence_errors.extend(event_valence_errors)
                arousal_errors.extend(event_arousal_errors)
                intensity_errors.extend(event_intensity_errors)

                # --- 记录每事件详细结果 ---
                detail = {
                    "event_id": event_id,
                    "trigger": trigger,
                    "peak_intensity": round(peak_intensity, 4),
                    "warning_fired": event_warning_fired,
                    "warning_level": event_warning_level,
                    "lead_time_sec": round(event_lead_time, 1) if event_lead_time is not None else None,
                    "recommended_strategy": rec.strategy_name if rec else None,
                    "optimal_strategy": None,
                    "regret": round(regret, 4),
                    "rmse_intensity": round(event_rmse_i, 4),
                    "model_confidence_after": round(final_model_confidence, 4),
                }

                # 填充最优策略名称
                if best_strategy_id:
                    for s in DEFAULT_STRATEGIES:
                        if s.id == best_strategy_id:
                            detail["optimal_strategy"] = s.name
                            break

                per_event_details.append(detail)

            # --- 日终更新 ---
            self._update_engine_daily(engine, day, sleep_data, events)

        # --- 4. 计算核心指标 ---
        # 预警指标
        warning_recall = true_warnings / max(1, true_warnings + missed_events)
        warning_precision = true_warnings / max(1, total_warnings)
        if warning_recall + warning_precision > 0:
            warning_f1 = 2 * warning_recall * warning_precision / (warning_recall + warning_precision)
        else:
            warning_f1 = 0.0

        avg_lead_time = float(np.mean(lead_times)) if lead_times else 0.0

        # 推荐指标
        best_strategy_hit_rate = (
            best_hit_count / max(1, total_events_with_recommendation)
        )
        avg_bandit_reward = float(np.mean(bandit_rewards)) if bandit_rewards else 0.0

        # 拟合指标
        rmse_valence = math.sqrt(sum(valence_errors) / max(1, len(valence_errors)))
        rmse_arousal = math.sqrt(sum(arousal_errors) / max(1, len(arousal_errors)))
        rmse_intensity = math.sqrt(sum(intensity_errors) / max(1, len(intensity_errors)))
        max_intensity_error = max(intensity_errors) ** 0.5 if intensity_errors else 0.0

        # 模型收敛
        if events_to_high_confidence is None:
            events_to_high_confidence = total_events  # 如果始终未达到，记录总数

        # --- 5. 计算综合评分 ---
        fit_score = max(0.0, 1.0 - rmse_intensity / 0.3)
        warning_score = warning_recall * 0.6 + warning_precision * 0.4
        bandit_score = best_strategy_hit_rate
        lead_time_bonus = min(1.0, avg_lead_time / 60.0) * 0.1 if avg_lead_time > 0 else 0.0
        combined_score = (
            fit_score * 0.4
            + warning_score * 0.4
            + bandit_score * 0.2
            + lead_time_bonus
        )

        return BenchmarkResult(
            archetype_name=archetype_name,
            archetype_name_en=archetype_name_en,
            total_events=total_events,
            total_days=len(daily_data),
            warning_recall=round(warning_recall, 4),
            warning_precision=round(warning_precision, 4),
            warning_f1=round(warning_f1, 4),
            avg_lead_time_sec=round(avg_lead_time, 1),
            total_warnings=total_warnings,
            true_warnings=true_warnings,
            false_alarms=false_alarms,
            missed_events=missed_events,
            cumulative_regret=round(cumulative_regret, 4),
            best_strategy_hit_rate=round(best_strategy_hit_rate, 4),
            avg_bandit_reward=round(avg_bandit_reward, 4),
            rmse_valence=round(rmse_valence, 4),
            rmse_arousal=round(rmse_arousal, 4),
            rmse_intensity=round(rmse_intensity, 4),
            max_intensity_error=round(max_intensity_error, 4),
            events_to_high_confidence=events_to_high_confidence,
            final_model_confidence=round(final_model_confidence, 4),
            final_model_source=final_model_source,
            combined_score=round(combined_score, 4),
            per_event_details=per_event_details,
        )

    # ============================================================
    # 批量基准测试
    # ============================================================

    def run_all_benchmarks(self) -> Dict[str, BenchmarkResult]:
        """
        对所有发现的数据集运行基准测试。

        Returns:
            {数据集文件名: BenchmarkResult} 字典
        """
        datasets = self.discover_datasets()
        if not datasets:
            print("[错误] 未发现测试数据文件，请检查 data_dir 路径。")
            return {}

        results = {}
        for ds_path in datasets:
            ds_name = os.path.basename(ds_path)
            print(f"\n{'='*60}")
            print(f"正在运行基准测试: {ds_name}")
            print(f"{'='*60}")
            try:
                result = self.run_benchmark(ds_path)
                results[ds_name] = result
                print(f"  画像: {result.archetype_name} ({result.archetype_name_en})")
                print(f"  总事件数: {result.total_events}")
                print(f"  综合评分: {result.combined_score:.4f}")
                print(f"  预警 F1: {result.warning_f1:.4f}")
                print(f"  推荐最优命中率: {result.best_strategy_hit_rate:.4f}")
                print(f"  强度 RMSE: {result.rmse_intensity:.4f}")
            except Exception as e:
                print(f"  [错误] 处理 {ds_name} 时发生异常: {e}")
                import traceback
                traceback.print_exc()

        return results

    # ============================================================
    # 报告生成
    # ============================================================

    def generate_markdown_report(self, results: Dict[str, BenchmarkResult]) -> str:
        """
        生成 Markdown 格式的报告，包含：
          - 按用户画像分组的指标表格
          - 总体统计
          - 模型收敛分析（个人化模型达到高置信度所需事件数）

        Args:
            results: {数据集文件名: BenchmarkResult} 字典

        Returns:
            Markdown 格式的报告字符串
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = []

        lines.append("# EmoWave 基准测试报告")
        lines.append("")
        lines.append(f"> 自动生成时间: {now_str}")
        lines.append(f"> 测试数据: test_data/ 目录下 {len(results)} 个用户画像数据集")
        lines.append("> 系统版本: 基线版本")
        lines.append("")

        # ============================================================
        # 总体概况
        # ============================================================
        lines.append("## 总体概况")
        lines.append("")

        # 收集所有指标名
        metric_keys = [
            ("预警召回率", "warning_recall"),
            ("预警精确率", "warning_precision"),
            ("预警 F1", "warning_f1"),
            ("平均提前量(s)", "avg_lead_time_sec"),
            ("最优策略命中率", "best_strategy_hit_rate"),
            ("累积遗憾", "cumulative_regret"),
            ("强度 RMSE", "rmse_intensity"),
            ("效价 RMSE", "rmse_valence"),
            ("唤醒 RMSE", "rmse_arousal"),
            ("综合评分", "combined_score"),
        ]

        # 计算总体均值和极值
        lines.append("| 指标 | 总体均值 | 最佳画像 | 最差画像 |")
        lines.append("|------|---------|---------|---------|")

        for label, key in metric_keys:
            values = [(r.archetype_name, getattr(r, key)) for r in results.values()]
            if not values:
                continue

            # 计算均值
            mean_val = sum(v for _, v in values) / len(values)

            # 对某些指标，"最佳"的含义不同
            higher_is_better = key in (
                "warning_recall", "warning_precision", "warning_f1",
                "best_strategy_hit_rate", "combined_score",
            )
            # 对累积遗憾和 RMSE，越低越好
            if higher_is_better:
                best_entry = max(values, key=lambda x: x[1])
                worst_entry = min(values, key=lambda x: x[1])
            else:
                best_entry = min(values, key=lambda x: x[1])
                worst_entry = max(values, key=lambda x: x[1])

            if key in ("cumulative_regret",):
                # 遗憾值显示为整数更直观
                lines.append(
                    f"| {label} | {mean_val:.2f} | "
                    f"{best_entry[0]} ({best_entry[1]:.2f}) | "
                    f"{worst_entry[0]} ({worst_entry[1]:.2f}) |"
                )
            else:
                lines.append(
                    f"| {label} | {mean_val:.4f} | "
                    f"{best_entry[0]} ({best_entry[1]:.4f}) | "
                    f"{worst_entry[0]} ({worst_entry[1]:.4f}) |"
                )

        lines.append("")

        # ============================================================
        # 按用户画像详细结果
        # ============================================================
        lines.append("## 按用户画像详细结果")
        lines.append("")

        for ds_name, result in results.items():
            lines.append(f"### {result.archetype_name} ({result.archetype_name_en})")
            lines.append("")

            # 画像级指标表
            detail_metrics = [
                ("总事件数", f"{result.total_events}"),
                ("总天数", f"{result.total_days}"),
                ("预警召回率", f"{result.warning_recall:.4f}"),
                ("预警精确率", f"{result.warning_precision:.4f}"),
                ("预警 F1", f"{result.warning_f1:.4f}"),
                ("平均提前量(s)", f"{result.avg_lead_time_sec:.1f}"),
                ("总预警次数", f"{result.total_warnings}"),
                ("真正预警", f"{result.true_warnings}"),
                ("虚警次数", f"{result.false_alarms}"),
                ("漏报次数", f"{result.missed_events}"),
                ("累积遗憾", f"{result.cumulative_regret:.2f}"),
                ("最优策略命中率", f"{result.best_strategy_hit_rate:.4f}"),
                ("Bandit 平均奖励", f"{result.avg_bandit_reward:.2f}"),
                ("效价 RMSE", f"{result.rmse_valence:.4f}"),
                ("唤醒 RMSE", f"{result.rmse_arousal:.4f}"),
                ("强度 RMSE", f"{result.rmse_intensity:.4f}"),
                ("最大强度误差", f"{result.max_intensity_error:.4f}"),
                ("达到高置信度事件数", f"{result.events_to_high_confidence}"),
                ("最终模型置信度", f"{result.final_model_confidence:.4f}"),
                ("最终模型来源", f"{result.final_model_source}"),
                ("综合评分", f"{result.combined_score:.4f}"),
            ]

            lines.append("| 指标 | 值 |")
            lines.append("|------|-----|")
            for label, val in detail_metrics:
                lines.append(f"| {label} | {val} |")
            lines.append("")

            # 每事件详细数据表
            if result.per_event_details:
                lines.append("#### 每事件详细数据")
                lines.append("")
                lines.append(
                    "| 事件ID | 触发器 | 峰值强度 | 预警 | 预警级别 | "
                    "提前量(s) | 推荐策略 | 最优策略 | 遗憾 | 强度RMSE | 置信度 |"
                )
                lines.append(
                    "|--------|--------|---------|------|---------|"
                    "----------|---------|---------|------|---------|--------|"
                )

                for det in result.per_event_details:
                    eid_short = det["event_id"][:20] if det["event_id"] else "-"
                    lead_str = (
                        f"{det['lead_time_sec']:.1f}"
                        if det["lead_time_sec"] is not None
                        else "-"
                    )
                    rec_str = (det["recommended_strategy"] or "-")[:8]
                    opt_str = (det["optimal_strategy"] or "-")[:8]
                    warning_mark = "是" if det["warning_fired"] else "否"

                    lines.append(
                        f"| {eid_short} | {det['trigger'][:6]} | "
                        f"{det['peak_intensity']:.3f} | {warning_mark} | "
                        f"{det['warning_level']} | {lead_str} | "
                        f"{rec_str} | {opt_str} | "
                        f"{det['regret']:.2f} | {det['rmse_intensity']:.4f} | "
                        f"{det['model_confidence_after']:.2f} |"
                    )

                lines.append("")

        # ============================================================
        # 模型收敛分析
        # ============================================================
        lines.append("## 模型收敛分析")
        lines.append("")
        lines.append("| 画像 | 达到高置信度所需事件数 | 最终置信度 | 最终模型来源 |")
        lines.append("|------|---------------------|-----------|------------|")

        for ds_name, result in results.items():
            lines.append(
                f"| {result.archetype_name} | "
                f"{result.events_to_high_confidence} | "
                f"{result.final_model_confidence:.4f} | "
                f"{result.final_model_source} |"
            )
        lines.append("")

        # ============================================================
        # 系统配置
        # ============================================================
        lines.append("## 系统配置")
        lines.append("")

        kf_config = KalmanConfig()
        pred_config = PredictionConfig()

        config_items = [
            ("KF q_position_std", f"{kf_config.q_position_std}"),
            ("KF q_velocity_std", f"{kf_config.q_velocity_std}"),
            ("KF r_base_std", f"{kf_config.r_base_std}"),
            ("KF r_jump_std", f"{kf_config.r_jump_std}"),
            ("KF velocity_damping", f"{kf_config.velocity_damping}"),
            ("KF hrv_control_weight", f"{kf_config.hrv_control_weight}"),
            ("KF hr_control_weight", f"{kf_config.hr_control_weight}"),
            ("预警外推时长(s)", f"{pred_config.extrapolation_horizon_sec}"),
            ("预警强度阈值", f"{pred_config.warning_intensity}"),
            ("临界强度阈值", f"{pred_config.critical_intensity}"),
            ("最小预警提前量(s)", f"{pred_config.min_lead_time_sec}"),
            ("最大预警提前量(s)", f"{pred_config.max_lead_time_sec}"),
            ("Bandit 探索系数(alpha)", "1.0"),
            ("默认策略数量", f"{len(DEFAULT_STRATEGIES)}"),
            ("危险事件强度阈值", f"{self.warning_intensity_threshold}"),
            ("预警检查间隔(s)", f"{self.prediction_check_interval}"),
        ]

        lines.append("| 参数 | 值 |")
        lines.append("|------|-----|")
        for label, val in config_items:
            lines.append(f"| {label} | {val} |")
        lines.append("")

        return "\n".join(lines)

    # ============================================================
    # 结果持久化
    # ============================================================

    def save_results(self, results: Dict[str, BenchmarkResult], output_path: str):
        """
        保存结果为 JSON 文件。

        Args:
            results: {数据集文件名: BenchmarkResult} 字典
            output_path: 输出 JSON 文件路径
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        serializable = {}
        for ds_name, result in results.items():
            serializable[ds_name] = result.to_dict()

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

        print(f"结果已保存到: {output_path}")

    # ============================================================
    # 内部辅助方法
    # ============================================================

    def _build_raw_event(
        self,
        event_id: str,
        trajectory: list,
        physio_signals: list,
        start_time: float,
        trigger: str,
        trigger_code: int,
        evt: dict,
    ) -> EmotionEventRaw:
        """
        从 JSON 中的事件数据构造 EmotionEventRaw 对象。

        Args:
            event_id: 事件唯一标识
            trajectory: 轨迹点列表
            physio_signals: 生理信号列表（可能含 None）
            start_time: 事件开始时间戳
            trigger: 触发因素名称
            trigger_code: 触发因素编码
            evt: 原始事件 JSON 字典

        Returns:
            EmotionEventRaw 对象
        """
        samples = []
        for pt in trajectory:
            t = pt["t"]
            timestamp = start_time + t

            # 查找对应时刻的生理信号
            hr = None
            hrv = None
            for ps in physio_signals:
                if ps is not None and abs(ps["t"] - t) < 1.5:
                    # 从 hrv_drop_ratio 反推 hrv（近似值）
                    hr_change = ps.get("hr_change", 0.0)
                    hr = 72.0 + hr_change  # 使用基线心率 + 变化
                    hrv_drop = ps.get("hrv_drop_ratio", 0.0)
                    hrv = 50.0 * (1.0 - hrv_drop)  # 使用基线 HRV * (1 - 下降比例)
                    break

            sample = TimeSeriesSample(
                timestamp=timestamp,
                valence=pt.get("true_valence", pt["obs_valence"]),
                arousal=pt.get("true_arousal", pt["obs_arousal"]),
                hr=hr,
                hrv=hrv,
            )
            samples.append(sample)

        # 处理应对方法和评分
        coping_methods = evt.get("coping_methods", [])
        coping_ratings = {}
        strategy_rec = evt.get("strategy_recommendation", {})
        if strategy_rec:
            user_rating = evt.get("user_rating", 0)
            if user_rating > 0 and strategy_rec.get("id"):
                coping_ratings[strategy_rec["id"]] = user_rating

        calm_ts = evt.get("end_time", None)
        if calm_ts is None and evt.get("duration_sec"):
            calm_ts = start_time + evt["duration_sec"]

        return EmotionEventRaw(
            event_id=event_id,
            samples=samples,
            trigger_tags=[trigger],
            coping_methods=coping_methods,
            coping_ratings=coping_ratings,
            body_symptoms=evt.get("body_symptoms", []),
            calm_timestamp=calm_ts,
        )

    def _update_engine_daily(
        self,
        engine: EmoCalibrationEngine,
        day: dict,
        sleep_data: dict,
        events: list,
    ):
        """
        构造 DailySummary 并调用引擎的 update_daily() 方法。

        Args:
            engine: 校准引擎实例
            day: 当天的 JSON 数据
            sleep_data: 当天的睡眠数据
            events: 当天的事件列表
        """
        date = day["date"]
        morning_ema = day.get("morning_ema", {})
        evening_ema = day.get("evening_ema", {})
        daily_summary_data = day.get("daily_summary", {})

        # 构造 DailySummary
        try:
            baseline = engine.get_baseline()

            daily = DailySummary(
                date=date,
                avg_resting_hrv=baseline.resting_hrv_mean,
                avg_resting_hr=baseline.resting_hr,
                sleep_score=sleep_data.get("quality_score", 7.0),
                morning_valence_avg=morning_ema.get("valence", 0.5),
                evening_valence_avg=evening_ema.get("valence", 0.5),
                event_count=len(events),
                peak_arousal_max=daily_summary_data.get("peak_arousal_max", 0.0),
            )

            engine.update_daily(daily)
        except Exception as e:
            # 日终更新失败不影响主要流程
            print(f"  [警告] 日终更新失败 ({date}): {e}")


# ================================================================
# 主入口
# ================================================================

def main():
    """
    基准测试主函数。

    流程：
      1. 初始化 BenchmarkSuite
      2. 发现并运行所有数据集的基准测试
      3. 生成 Markdown 报告
      4. 保存 JSON 结果
      5. 打印摘要
    """
    suite = BenchmarkSuite(
        data_dir="/workspace/emowave-engine/test_data",
        output_dir="/workspace/emowave-engine/benchmark_results",
    )

    # 运行所有基准测试
    results = suite.run_all_benchmarks()

    if not results:
        print("\n[错误] 未生成任何基准测试结果，退出。")
        return

    # 生成 Markdown 报告
    md_report = suite.generate_markdown_report(results)

    # 保存 Markdown 报告
    report_path = os.path.join(suite.output_dir, "BENCHMARK_REPORT.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(md_report)
    print(f"\nMarkdown 报告已保存到: {report_path}")

    # 保存 JSON 结果
    json_path = os.path.join(suite.output_dir, "benchmark_results.json")
    suite.save_results(results, json_path)

    # ============================================================
    # 打印摘要
    # ============================================================
    print(f"\n{'='*60}")
    print("基准测试摘要")
    print(f"{'='*60}")

    print(f"\n共处理 {len(results)} 个用户画像数据集:\n")

    # 表头
    print(
        f"{'画像':<20s} {'事件':>4s} {'预警F1':>8s} "
        f"{'命中率':>8s} {'强度RMSE':>9s} {'综合评分':>8s}"
    )
    print("-" * 65)

    for ds_name, r in results.items():
        print(
            f"{r.archetype_name:<20s} {r.total_events:>4d} "
            f"{r.warning_f1:>8.4f} "
            f"{r.best_strategy_hit_rate:>8.4f} "
            f"{r.rmse_intensity:>9.4f} "
            f"{r.combined_score:>8.4f}"
        )

    # 总体均值
    avg_f1 = np.mean([r.warning_f1 for r in results.values()])
    avg_hit = np.mean([r.best_strategy_hit_rate for r in results.values()])
    avg_rmse = np.mean([r.rmse_intensity for r in results.values()])
    avg_score = np.mean([r.combined_score for r in results.values()])

    print("-" * 65)
    print(
        f"{'总体均值':<20s} {'':>4s} "
        f"{avg_f1:>8.4f} "
        f"{avg_hit:>8.4f} "
        f"{avg_rmse:>9.4f} "
        f"{avg_score:>8.4f}"
    )
    print(f"\n详细报告: {report_path}")
    print(f"JSON 结果: {json_path}")


if __name__ == "__main__":
    main()
