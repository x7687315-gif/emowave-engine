#!/usr/bin/env python3
"""
evaluation.py — 心潮 EmoWave 离线评估与参数敏感性分析

本脚本为情绪引擎的核心模块建立离线评估框架：
  1. 扩展虚拟用户模拟器，生成已知真相的情绪事件
  2. 定义预警/推荐/拟合三类核心评估指标
  3. 对关键参数进行网格扫描，找出较优配置
  4. 生成 HTML 评估报告（含趋势图和弱点分析）

运行方式：
  cd /workspace/emowave-engine && python3 evaluation.py

输出：
  /workspace/emowave-engine/evaluation_report/
    report.html      — 完整评估报告
    *.png            — 趋势图与对比图
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import os
import json
import random
import itertools
import numpy as np
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import defaultdict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from models import (
    TimeSeriesSample,
    EmotionEventRaw,
    EventProfile,
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
)
from predictor import PredictionEngine, PredictionConfig, WarningLevel
from recommender import ContextualBandit, Context, DEFAULT_STRATEGIES
from simulator import (
    generate_emotion_trajectory,
    generate_physio_signals,
    TrajectoryPoint,
)


# ================================================================
# 输出目录
# ================================================================
OUTPUT_DIR = "/workspace/emowave-engine/evaluation_report"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ================================================================
# 用户原型定义
# ================================================================

@dataclass
class UserArchetype:
    """用户原型：定义一类用户的性格特质"""
    name: str              # 中文名
    name_en: str           # 英文名（用于文件名）
    baseline_valence: float
    baseline_arousal: float
    stress_sensitivity: float   # 0-1，越高越易触发
    recovery_speed: float       # 恢复速率（对应 trajectory 的 recovery_speed）
    noise_level: float          # 滑条噪声标准差
    somatic_intensity: float    # 0-1，生理信号变化剧烈程度


ARCHETYPES = [
    UserArchetype(
        name="易怒型", name_en="irritable",
        baseline_valence=0.45, baseline_arousal=0.55,
        stress_sensitivity=0.85, recovery_speed=0.35,
        noise_level=0.06, somatic_intensity=0.7,
    ),
    UserArchetype(
        name="焦虑型", name_en="anxious",
        baseline_valence=0.30, baseline_arousal=0.40,
        stress_sensitivity=0.75, recovery_speed=0.45,
        noise_level=0.05, somatic_intensity=0.5,
    ),
    UserArchetype(
        name="恢复快型", name_en="resilient",
        baseline_valence=0.60, baseline_arousal=0.30,
        stress_sensitivity=0.40, recovery_speed=0.85,
        noise_level=0.04, somatic_intensity=0.3,
    ),
    UserArchetype(
        name="躯体化型", name_en="somatic",
        baseline_valence=0.50, baseline_arousal=0.45,
        stress_sensitivity=0.65, recovery_speed=0.50,
        noise_level=0.05, somatic_intensity=0.95,
    ),
]


# ================================================================
# Ground Truth 事件规格
# ================================================================

@dataclass
class GroundTruthEventSpec:
    """
    已知真相的情绪事件规格。
    用于精确控制模拟事件的参数，使评估有明确的对照基准。
    """
    trigger: str = "预设爆发"
    trigger_code: int = 99
    start_hour: int = 14          # 事件开始时刻（小时）
    start_minute: int = 0
    duration_sec: int = 600       # 事件总时长（秒）
    peak_time_sec: int = 180      # 从事件开始算，峰值到达时间（秒）
    peak_valence: float = 0.05    # 峰值效价（低效价 = 负面）
    peak_arousal: float = 0.98    # 峰值唤醒
    noise_std: float = 0.05       # 观测噪声
    seed: int = 42

    @property
    def peak_intensity(self) -> float:
        """峰值强度（归一化到 [0,1]）"""
        return min(1.0, np.sqrt(self.peak_valence**2 + self.peak_arousal**2) / np.sqrt(2))


# ================================================================
# Ground Truth 事件生成器
# ================================================================

def generate_ground_truth_event(
    spec: GroundTruthEventSpec,
    archetype: UserArchetype,
    base_timestamp: float = 0.0,
) -> Tuple[List[TrajectoryPoint], List[Optional[PhysioInput]], List[SliderObservation]]:
    """
    根据已知真相规格生成情绪事件。

    返回：
      trajectory: 真实轨迹点列表
      physio_signals: 生理信号列表
      observations: 带时间戳的滑条观测列表
    """
    # 计算 peak_time_fraction
    peak_time_fraction = spec.peak_time_sec / spec.duration_sec

    # 生成轨迹
    trajectory = generate_emotion_trajectory(
        duration_sec=spec.duration_sec,
        dt=1.0,
        start_valence=archetype.baseline_valence,
        start_arousal=archetype.baseline_arousal,
        peak_arousal=spec.peak_arousal,
        peak_valence=spec.peak_valence,
        peak_time_fraction=peak_time_fraction,
        recovery_speed=archetype.recovery_speed,
        noise_std=spec.noise_std,
        seed=spec.seed,
    )

    # 生成生理信号（躯体化型有更强的生理反应）
    true_arousals = [p.true_arousal for p in trajectory]
    physio_signals = generate_physio_signals(
        true_arousals,
        base_hr=72.0,
        base_hrv=50.0,
        seed=spec.seed,
    )

    # 对躯体化型增强生理信号变化
    if archetype.somatic_intensity > 0.7:
        for i, physio in enumerate(physio_signals):
            if physio is not None:
                physio.hrv_drop_ratio *= (1.0 + archetype.somatic_intensity * 0.5)
                physio.hr_change *= (1.0 + archetype.somatic_intensity * 0.3)

    # 转换为带绝对时间戳的 SliderObservation
    start_time = base_timestamp + spec.start_hour * 3600 + spec.start_minute * 60
    observations = []
    for p in trajectory:
        observations.append(SliderObservation(
            timestamp=start_time + p.t,
            valence=p.obs_valence,
            arousal=p.obs_arousal,
            touch_velocity=p.touch_velocity,
            seconds_since_last_touch=p.stillness,
        ))

    return trajectory, physio_signals, observations


# ================================================================
# 单次评估运行
# ================================================================

@dataclass
class EvaluationMetrics:
    """单次评估运行的完整指标集"""
    # --- 预警指标 ---
    warning_recall: float = 0.0           # 召回率：真实危险事件中被预警的比例
    warning_precision: float = 0.0        # 精确率：预警中真正危险的比例
    avg_lead_time_sec: Optional[float] = None  # 平均提前预警时间（秒）
    n_warnings: int = 0                   # 预警总次数
    n_true_warnings: int = 0              # 正确预警次数
    missed_events: int = 0                # 漏报事件数
    false_alarms: int = 0                 # 误报次数

    # --- 拟合指标 ---
    rmse_valence: float = 0.0             # 效价 RMSE
    rmse_arousal: float = 0.0             # 唤醒 RMSE
    rmse_intensity: float = 0.0           # 强度 RMSE
    max_intensity_error: float = 0.0      # 最大强度误差

    # --- 推荐指标 ---
    bandit_avg_reward: float = 0.0        # 平均奖励
    bandit_cumulative_regret: float = 0.0 # 累积遗憾
    bandit_best_strategy_hit_rate: float = 0.0  # 最优策略命中率

    # --- 综合 ---
    combined_score: float = 0.0           # 综合评分（用于排序）


def evaluate_single_run(
    spec: GroundTruthEventSpec,
    archetype: UserArchetype,
    kf_config: KalmanConfig,
    pred_config: PredictionConfig,
    bandit_alpha: float,
    n_recommendations: int = 10,
    seed: int = 42,
) -> EvaluationMetrics:
    """
    执行一次完整的评估运行。

    流程：
      1. 生成 Ground Truth 事件
      2. 运行 KF + Predictor，记录预警
      3. 运行 Bandit 推荐，模拟用户反馈
      4. 计算所有指标
    """
    rng = np.random.RandomState(seed)
    metrics = EvaluationMetrics()

    # --- 1. 生成事件 ---
    trajectory, physio_signals, observations = generate_ground_truth_event(
        spec, archetype, base_timestamp=0.0
    )

    # --- 2. 运行 KF + Predictor ---
    kf = EmotionKalmanFilter(kf_config)
    kf.init(valence=trajectory[0].true_valence,
            arousal=trajectory[0].true_arousal)
    predictor = PredictionEngine(pred_config)

    # 使用群体阈值（模拟冷启动）
    thresholds = PersonalThresholds(
        high_risk_arousal=0.85,
        high_risk_valence=0.15,
        hrv_drop_percent=0.30,
        hr_surge_zscore=2.5,
        dangerous_rise_slope=0.012,
        model_source=ModelSource.POPULATION,
        model_confidence=0.0,
        event_count=0,
    )

    # 记录预警
    warnings_fired = []  # [(time_sec, level, reason)]
    first_warning_time = None
    warning_levels_over_time = []

    # KF 轨迹记录
    kf_valences = []
    kf_arousals = []
    kf_intensities = []

    for i, (obs, physio) in enumerate(zip(observations, physio_signals)):
        if physio is not None:
            state = kf.update_with_control(obs, physio)
        else:
            state = kf.update(obs)

        kf_valences.append(state.valence)
        kf_arousals.append(state.arousal)
        kf_intensities.append(state.intensity)

        # 每 5 秒检查一次预警（跳过预热期）
        if i >= 15 and i % 5 == 0:
            result = predictor.predict(kf, thresholds, current_time=obs.timestamp)
            warning_levels_over_time.append(result.warning_level)

            if result.warning_level in (WarningLevel.WARNING, WarningLevel.CRITICAL):
                rel_time = obs.timestamp - observations[0].timestamp
                warnings_fired.append((rel_time, result.warning_level.value, result.reason))
                if first_warning_time is None:
                    first_warning_time = rel_time

    # --- 3. 预警指标计算 ---
    peak_time = spec.peak_time_sec
    is_dangerous = spec.peak_intensity > pred_config.warning_intensity

    if is_dangerous:
        if first_warning_time is not None:
            metrics.warning_recall = 1.0
            metrics.avg_lead_time_sec = peak_time - first_warning_time
            metrics.n_true_warnings = 1
        else:
            metrics.warning_recall = 0.0
            metrics.missed_events = 1
    else:
        # 非危险事件不应触发预警
        if warnings_fired:
            metrics.false_alarms = len(warnings_fired)
            metrics.warning_precision = 0.0
        else:
            metrics.warning_precision = 1.0

    metrics.n_warnings = len(warnings_fired)

    # --- 4. 拟合指标计算 ---
    true_valences = [p.true_valence for p in trajectory]
    true_arousals = [p.true_arousal for p in trajectory]
    true_intensities = [
        min(1.0, np.sqrt(p.true_valence**2 + p.true_arousal**2) / np.sqrt(2))
        for p in trajectory
    ]

    metrics.rmse_valence = np.sqrt(np.mean((np.array(kf_valences) - np.array(true_valences))**2))
    metrics.rmse_arousal = np.sqrt(np.mean((np.array(kf_arousals) - np.array(true_arousals))**2))
    metrics.rmse_intensity = np.sqrt(np.mean((np.array(kf_intensities) - np.array(true_intensities))**2))
    metrics.max_intensity_error = np.max(np.abs(np.array(kf_intensities) - np.array(true_intensities)))

    # --- 5. Bandit 推荐评估 ---
    bandit = ContextualBandit(strategies=DEFAULT_STRATEGIES)
    bandit.alpha = bandit_alpha  # 修改探索参数

    # 模拟多次推荐（同一事件的不同情境点）
    rewards = []
    best_rewards = []
    strategy_hits = []

    # 在事件的几个关键时间点进行推荐
    key_indices = [int(len(trajectory) * 0.2), int(len(trajectory) * 0.5),
                   int(len(trajectory) * 0.8)]

    for idx in key_indices:
        obs = observations[idx]
        context = Context.from_raw(
            valence=obs.valence,
            arousal=obs.arousal,
            hour=spec.start_hour,
            weekday=2,  # 周三
            sleep=7.0,
            trigger_code=spec.trigger_code,
        )

        rec = bandit.recommend(context)

        # Ground Truth 奖励（简化：用 peak_arousal 判断最优策略）
        # 高唤醒 → 运动/感官类好；低唤醒 → 呼吸类好
        best_strategy = _get_best_strategy_for_context(obs.arousal, spec.trigger_code)
        true_reward = _compute_strategy_reward(rec.strategy_id, obs.arousal, spec.trigger_code)
        best_reward = _compute_strategy_reward(best_strategy, obs.arousal, spec.trigger_code)

        bandit.update(rec.strategy_id, context, float(true_reward))
        rewards.append(true_reward)
        best_rewards.append(best_reward)
        strategy_hits.append(1 if rec.strategy_id == best_strategy else 0)

    metrics.bandit_avg_reward = np.mean(rewards)
    metrics.bandit_cumulative_regret = np.sum(np.array(best_rewards) - np.array(rewards))
    metrics.bandit_best_strategy_hit_rate = np.mean(strategy_hits)

    # --- 6. 综合评分 ---
    # 加权综合：拟合占 40%，预警占 40%，推荐占 20%
    fit_score = max(0, 1.0 - (metrics.rmse_intensity / 0.3))  # RMSE < 0.1 = 满分
    warning_score = (metrics.warning_recall * 0.6 +
                     (metrics.warning_precision if metrics.n_warnings > 0 else 1.0) * 0.4)
    bandit_score = metrics.bandit_best_strategy_hit_rate

    lead_time_bonus = 0.0
    if metrics.avg_lead_time_sec is not None:
        lead_time_bonus = min(1.0, metrics.avg_lead_time_sec / 60.0) * 0.1

    metrics.combined_score = fit_score * 0.4 + warning_score * 0.4 + bandit_score * 0.2 + lead_time_bonus

    return metrics


def _get_best_strategy_for_context(arousal: float, trigger_code: int) -> str:
    """根据情境返回最优策略（Ground Truth）"""
    if arousal > 0.75:
        return "cold_water"
    elif arousal > 0.55:
        return "short_walk"
    elif arousal > 0.40:
        return "deep_breathing"
    else:
        return "listen_music"


def _compute_strategy_reward(strategy_id: str, arousal: float, trigger_code: int) -> int:
    """计算策略的真实奖励（1-5）"""
    best = _get_best_strategy_for_context(arousal, trigger_code)
    if strategy_id == best:
        return 5
    elif strategy_id in ("deep_breathing", "short_walk", "cold_water") and arousal > 0.5:
        return 4
    elif strategy_id in ("listen_music", "journaling") and arousal <= 0.5:
        return 4
    else:
        return 3


# ================================================================
# 参数扫描
# ================================================================

@dataclass
class ParamCombo:
    """一组参数组合"""
    q_position_std: float
    r_base_std: float
    max_prediction_window_sec: float
    bandit_alpha: float
    label: str = ""


@dataclass
class SweepResult:
    """参数扫描的单个结果"""
    combo: ParamCombo
    archetype: UserArchetype
    metrics_list: List[EvaluationMetrics]

    @property
    def avg_metrics(self) -> EvaluationMetrics:
        """返回平均指标"""
        n = len(self.metrics_list)
        if n == 0:
            return EvaluationMetrics()
        return EvaluationMetrics(
            warning_recall=np.mean([m.warning_recall for m in self.metrics_list]),
            warning_precision=np.mean([m.warning_precision for m in self.metrics_list]),
            avg_lead_time_sec=np.mean([m.avg_lead_time_sec or 0 for m in self.metrics_list]),
            n_warnings=int(np.mean([m.n_warnings for m in self.metrics_list])),
            n_true_warnings=int(np.mean([m.n_true_warnings for m in self.metrics_list])),
            missed_events=int(np.mean([m.missed_events for m in self.metrics_list])),
            false_alarms=int(np.mean([m.false_alarms for m in self.metrics_list])),
            rmse_valence=np.mean([m.rmse_valence for m in self.metrics_list]),
            rmse_arousal=np.mean([m.rmse_arousal for m in self.metrics_list]),
            rmse_intensity=np.mean([m.rmse_intensity for m in self.metrics_list]),
            max_intensity_error=np.mean([m.max_intensity_error for m in self.metrics_list]),
            bandit_avg_reward=np.mean([m.bandit_avg_reward for m in self.metrics_list]),
            bandit_cumulative_regret=np.mean([m.bandit_cumulative_regret for m in self.metrics_list]),
            bandit_best_strategy_hit_rate=np.mean([m.bandit_best_strategy_hit_rate for m in self.metrics_list]),
            combined_score=np.mean([m.combined_score for m in self.metrics_list]),
        )


def run_parameter_sweep(
    param_grid: List[ParamCombo],
    archetypes: List[UserArchetype],
    spec: GroundTruthEventSpec,
    n_runs_per_combo: int = 3,
) -> List[SweepResult]:
    """
    执行参数扫描。

    对每个参数组合 × 每个用户原型，运行 n_runs_per_combo 次模拟，
    返回平均指标。
    """
    results = []
    total = len(param_grid) * len(archetypes)
    done = 0

    print(f"\n开始参数扫描：{len(param_grid)} 组参数 × {len(archetypes)} 种原型 × {n_runs_per_combo} 次 = {total * n_runs_per_combo} 次模拟\n")

    for combo in param_grid:
        for archetype in archetypes:
            metrics_list = []
            for run_idx in range(n_runs_per_combo):
                kf_config = KalmanConfig(
                    q_position_std=combo.q_position_std,
                    r_base_std=combo.r_base_std,
                )
                pred_config = PredictionConfig(
                    max_prediction_window_sec=combo.max_prediction_window_sec,
                )

                spec_run = GroundTruthEventSpec(
                    seed=combo.label + archetype.name_en + str(run_idx),
                    noise_std=archetype.noise_level,
                )

                metrics = evaluate_single_run(
                    spec=spec_run,
                    archetype=archetype,
                    kf_config=kf_config,
                    pred_config=pred_config,
                    bandit_alpha=combo.bandit_alpha,
                    n_recommendations=10,
                    seed=hash(combo.label + archetype.name_en + str(run_idx)) % 10000,
                )
                metrics_list.append(metrics)

            results.append(SweepResult(combo=combo, archetype=archetype, metrics_list=metrics_list))
            done += 1
            avg = results[-1].avg_metrics
            print(f"  [{done}/{total}] {combo.label} + {archetype.name}: "
                  f"combined={avg.combined_score:.3f}, recall={avg.warning_recall:.2f}, "
                  f"rmse_intensity={avg.rmse_intensity:.4f}, lead_time={avg.avg_lead_time_sec:.0f}s")

    return results


# ================================================================
# 图表生成
# ================================================================

def plot_param_sensitivity(results: List[SweepResult], output_dir: str):
    """生成参数敏感性趋势图"""
    # 按参数分组计算平均指标
    param_keys = ["q_position_std", "r_base_std", "max_prediction_window_sec", "bandit_alpha"]
    param_labels = {
        "q_position_std": "KF 过程噪声 (q_position_std)",
        "r_base_std": "KF 观测噪声 (r_base_std)",
        "max_prediction_window_sec": "预警外推窗口 (秒)",
        "bandit_alpha": "UCB 探索系数 (alpha)",
    }

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("参数敏感性分析", fontsize=16, fontweight='bold')

    for idx, param_key in enumerate(param_keys):
        ax = axes[idx // 2][idx % 2]

        # 收集该参数的所有取值和对应指标
        param_values = sorted(list(set(
            getattr(r.combo, param_key) for r in results
        )))

        recalls = []
        precisions = []
        rmses = []
        scores = []

        for val in param_values:
            subset = [r for r in results if getattr(r.combo, param_key) == val]
            recalls.append(np.mean([r.avg_metrics.warning_recall for r in subset]))
            precisions.append(np.mean([r.avg_metrics.warning_precision for r in subset]))
            rmses.append(np.mean([r.avg_metrics.rmse_intensity for r in subset]))
            scores.append(np.mean([r.avg_metrics.combined_score for r in subset]))

        ax2 = ax.twinx()
        ax.plot(param_values, recalls, 'o-', color='#e74c3c', label='预警召回率', linewidth=2)
        ax.plot(param_values, scores, 's-', color='#2ecc71', label='综合评分', linewidth=2)
        ax2.plot(param_values, rmses, '^--', color='#3498db', label='强度 RMSE', linewidth=2)

        ax.set_xlabel(param_labels[param_key], fontsize=10)
        ax.set_ylabel('召回率 / 综合评分', fontsize=10)
        ax2.set_ylabel('RMSE', fontsize=10, color='#3498db')
        ax.set_ylim(0, 1.2)
        ax2.set_ylim(0, max(rmses) * 1.5 if rmses else 1)
        ax.legend(loc='upper left', fontsize=8)
        ax2.legend(loc='upper right', fontsize=8)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "param_sensitivity.png"), dpi=150)
    plt.close()
    print(f"  已保存: {output_dir}/param_sensitivity.png")


def plot_archetype_comparison(results: List[SweepResult], output_dir: str):
    """生成用户原型对比图"""
    archetype_names = sorted(list(set(r.archetype.name for r in results)))

    metrics_to_plot = [
        ("warning_recall", "预警召回率"),
        ("rmse_intensity", "强度 RMSE"),
        ("avg_lead_time_sec", "平均提前量 (秒)"),
        ("combined_score", "综合评分"),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle("用户原型对比", fontsize=16, fontweight='bold')

    for idx, (metric_key, metric_label) in enumerate(metrics_to_plot):
        ax = axes[idx // 2][idx % 2]
        values = []
        for name in archetype_names:
            subset = [r.avg_metrics for r in results if r.archetype.name == name]
            val = np.mean([getattr(m, metric_key) or 0 for m in subset])
            values.append(val)

        colors = ['#e74c3c', '#f39c12', '#2ecc71', '#9b59b6']
        bars = ax.bar(archetype_names, values, color=colors, edgecolor='white', linewidth=1.5)
        ax.set_ylabel(metric_label, fontsize=10)
        ax.set_ylim(0, max(values) * 1.3 if values else 1)

        # 在柱子上标注数值
        for bar, val in zip(bars, values):
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{val:.3f}', ha='center', va='bottom', fontsize=9)

        ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "archetype_comparison.png"), dpi=150)
    plt.close()
    print(f"  已保存: {output_dir}/archetype_comparison.png")


def plot_kf_trajectory_fit(spec: GroundTruthEventSpec, output_dir: str):
    """展示 KF 轨迹拟合示例"""
    archetype = ARCHETYPES[0]  # 易怒型
    kf_configs = [
        KalmanConfig(q_position_std=0.01, r_base_std=0.05),
        KalmanConfig(q_position_std=0.02, r_base_std=0.08),
        KalmanConfig(q_position_std=0.05, r_base_std=0.12),
    ]
    labels = ["低噪声", "默认", "高噪声"]
    colors = ['#2ecc71', '#3498db', '#e74c3c']

    trajectory, physio_signals, observations = generate_ground_truth_event(spec, archetype)
    true_intensities = [min(1.0, np.sqrt(p.true_valence**2 + p.true_arousal**2) / np.sqrt(2))
                        for p in trajectory]
    time_axis = [p.t for p in trajectory]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("卡尔曼滤波器轨迹拟合对比", fontsize=14, fontweight='bold')

    # 左图：强度拟合
    ax = axes[0]
    ax.plot(time_axis, true_intensities, 'k-', linewidth=2, label='真实强度', alpha=0.7)

    for kf_config, label, color in zip(kf_configs, labels, colors):
        kf = EmotionKalmanFilter(kf_config)
        kf.init(valence=trajectory[0].true_valence, arousal=trajectory[0].true_arousal)
        kf_intensities = []
        for obs, physio in zip(observations, physio_signals):
            if physio is not None:
                state = kf.update_with_control(obs, physio)
            else:
                state = kf.update(obs)
            kf_intensities.append(state.intensity)
        rmse = np.sqrt(np.mean((np.array(kf_intensities) - np.array(true_intensities))**2))
        ax.plot(time_axis, kf_intensities, '--', color=color, linewidth=1.5,
                label=f'{label} (RMSE={rmse:.4f})')

    ax.set_xlabel('时间 (秒)', fontsize=10)
    ax.set_ylabel('情绪强度', fontsize=10)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0.65, color='orange', linestyle=':', alpha=0.5, label='WARNING 阈值')
    ax.axhline(y=0.78, color='red', linestyle=':', alpha=0.5, label='CRITICAL 阈值')

    # 右图：效价-唤醒散点
    ax = axes[1]
    ax.scatter([p.true_valence for p in trajectory], [p.true_arousal for p in trajectory],
               c=time_axis, cmap='viridis', s=5, alpha=0.5, label='真实轨迹')

    kf = EmotionKalmanFilter(KalmanConfig())
    kf.init(valence=trajectory[0].true_valence, arousal=trajectory[0].true_arousal)
    kf_vs, kf_as = [], []
    for obs, physio in zip(observations, physio_signals):
        if physio is not None:
            state = kf.update_with_control(obs, physio)
        else:
            state = kf.update(obs)
        kf_vs.append(state.valence)
        kf_as.append(state.arousal)

    ax.plot(kf_vs, kf_as, 'r-', linewidth=1.5, alpha=0.7, label='KF 平滑轨迹')
    ax.scatter([kf_vs[0]], [kf_as[0]], c='green', s=50, marker='o', label='起点')
    ax.scatter([kf_vs[-1]], [kf_as[-1]], c='blue', s=50, marker='s', label='终点')

    # 危险区域
    ax.axvline(x=0.15, color='red', linestyle='--', alpha=0.3)
    ax.axhline(y=0.85, color='red', linestyle='--', alpha=0.3)
    ax.fill_between([0, 0.15], 0.85, 1.0, color='red', alpha=0.1)

    ax.set_xlabel('效价 (Valence)', fontsize=10)
    ax.set_ylabel('唤醒 (Arousal)', fontsize=10)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "kf_trajectory_fit.png"), dpi=150)
    plt.close()
    print(f"  已保存: {output_dir}/kf_trajectory_fit.png")


# ================================================================
# HTML 报告生成
# ================================================================

def generate_html_report(
    results: List[SweepResult],
    best_combo: ParamCombo,
    output_dir: str,
):
    """生成 HTML 评估报告"""

    # 找出弱点
    weaknesses = []
    avg_recall = np.mean([r.avg_metrics.warning_recall for r in results])
    avg_precision = np.mean([r.avg_metrics.warning_precision for r in results])
    avg_rmse = np.mean([r.avg_metrics.rmse_intensity for r in results])
    avg_lead = np.mean([r.avg_metrics.avg_lead_time_sec or 0 for r in results])
    missed_rate = np.mean([r.avg_metrics.missed_events for r in results])

    if avg_recall < 0.8:
        weaknesses.append(f"预警召回率偏低 ({avg_recall:.1%})，建议降低预警强度阈值或扩大外推窗口")
    if avg_precision < 0.7:
        weaknesses.append(f"预警精确率偏低 ({avg_precision:.1%})，存在误报，建议增加前置条件过滤")
    if avg_rmse > 0.15:
        weaknesses.append(f"KF 强度 RMSE 偏高 ({avg_rmse:.4f})，建议调小过程噪声或观测噪声")
    if avg_lead < 30:
        weaknesses.append(f"平均提前预警时间过短 ({avg_lead:.0f}秒)，用户可能来不及反应")
    if missed_rate > 0.1:
        weaknesses.append(f"存在 {missed_rate*100:.0f}% 的漏报事件，对突发短时峰值检测能力不足")

    if not weaknesses:
        weaknesses.append("整体表现良好，无明显弱点")

    # 按综合评分排序取 Top 10
    sorted_results = sorted(results, key=lambda r: r.avg_metrics.combined_score, reverse=True)[:10]

    # 构建 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>EmoWave 离线评估报告</title>
<style>
  body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{ color: #38bdf8; border-bottom: 2px solid #38bdf8; padding-bottom: 10px; }}
  h2 {{ color: #7dd3fc; margin-top: 30px; }}
  .summary-card {{ background: rgba(30, 41, 59, 0.8); border-radius: 12px; padding: 20px; margin: 15px 0; backdrop-filter: blur(10px); border: 1px solid rgba(56, 189, 248, 0.2); }}
  .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 15px 0; }}
  .metric-box {{ background: rgba(15, 23, 42, 0.6); border-radius: 8px; padding: 15px; text-align: center; border-left: 4px solid #38bdf8; }}
  .metric-value {{ font-size: 28px; font-weight: bold; color: #38bdf8; }}
  .metric-label {{ font-size: 12px; color: #94a3b8; margin-top: 5px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 13px; }}
  th {{ background: rgba(56, 189, 248, 0.15); padding: 10px; text-align: left; border-bottom: 2px solid #38bdf8; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid rgba(148, 163, 184, 0.2); }}
  tr:hover {{ background: rgba(56, 189, 248, 0.05); }}
  .highlight {{ background: rgba(46, 204, 113, 0.15); }}
  .weakness {{ background: rgba(231, 76, 60, 0.1); border-left: 4px solid #e74c3c; padding: 12px 15px; margin: 10px 0; border-radius: 6px; }}
  .chart-container {{ text-align: center; margin: 20px 0; }}
  .chart-container img {{ max-width: 100%; border-radius: 8px; border: 1px solid rgba(56, 189, 248, 0.2); }}
  .best-params {{ background: rgba(46, 204, 113, 0.1); border: 1px solid rgba(46, 204, 113, 0.3); border-radius: 10px; padding: 20px; margin: 15px 0; }}
  .best-params h3 {{ color: #2ecc71; margin-top: 0; }}
  .param-row {{ display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }}
  .timestamp {{ color: #64748b; font-size: 12px; text-align: right; margin-top: 30px; }}
</style>
</head>
<body>
<div class="container">
<h1>心潮 EmoWave — 离线评估与参数敏感性分析报告</h1>
<div class="timestamp">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>

<div class="summary-card">
  <h2>总体概览</h2>
  <div class="metric-grid">
    <div class="metric-box">
      <div class="metric-value">{len(results)}</div>
      <div class="metric-label">参数组合数</div>
    </div>
    <div class="metric-box">
      <div class="metric-value">{len(ARCHETYPES)}</div>
      <div class="metric-label">用户原型</div>
    </div>
    <div class="metric-box">
      <div class="metric-value">{avg_recall:.1%}</div>
      <div class="metric-label">平均预警召回率</div>
    </div>
    <div class="metric-box">
      <div class="metric-value">{avg_rmse:.4f}</div>
      <div class="metric-label">平均强度 RMSE</div>
    </div>
    <div class="metric-box">
      <div class="metric-value">{avg_lead:.0f}s</div>
      <div class="metric-label">平均预警提前量</div>
    </div>
    <div class="metric-box">
      <div class="metric-value">{missed_rate*100:.0f}%</div>
      <div class="metric-label">漏报率</div>
    </div>
  </div>
</div>

<div class="best-params">
  <h3>推荐最优参数组合</h3>
  <div class="param-row"><span>KF 过程噪声 (q_position_std)</span><span><b>{best_combo.q_position_std}</b></span></div>
  <div class="param-row"><span>KF 观测噪声 (r_base_std)</span><span><b>{best_combo.r_base_std}</b></span></div>
  <div class="param-row"><span>预警外推窗口 (秒)</span><span><b>{best_combo.max_prediction_window_sec}</b></span></div>
  <div class="param-row"><span>UCB 探索系数 (alpha)</span><span><b>{best_combo.bandit_alpha}</b></span></div>
</div>

<div class="summary-card">
  <h2>弱点识别</h2>
"""

    for w in weaknesses:
        html += f'  <div class="weakness">{w}</div>\n'

    html += """
</div>

<div class="summary-card">
  <h2>参数敏感性趋势</h2>
  <div class="chart-container">
    <img src="param_sensitivity.png" alt="参数敏感性分析">
  </div>
</div>

<div class="summary-card">
  <h2>用户原型对比</h2>
  <div class="chart-container">
    <img src="archetype_comparison.png" alt="用户原型对比">
  </div>
</div>

<div class="summary-card">
  <h2>KF 轨迹拟合示例</h2>
  <div class="chart-container">
    <img src="kf_trajectory_fit.png" alt="KF 轨迹拟合对比">
  </div>
</div>

<div class="summary-card">
  <h2>Top 10 参数组合排名</h2>
  <table>
    <thead>
      <tr>
        <th>排名</th>
        <th>参数标签</th>
        <th>原型</th>
        <th>综合评分</th>
        <th>召回率</th>
        <th>精确率</th>
        <th>RMSE(强度)</th>
        <th>提前量(s)</th>
        <th>漏报</th>
        <th>误报</th>
      </tr>
    </thead>
    <tbody>
"""

    for rank, r in enumerate(sorted_results, 1):
        m = r.avg_metrics
        highlight = 'highlight' if rank == 1 else ''
        html += f"""
      <tr class="{highlight}">
        <td>{rank}</td>
        <td>{r.combo.label}</td>
        <td>{r.archetype.name}</td>
        <td>{m.combined_score:.3f}</td>
        <td>{m.warning_recall:.2f}</td>
        <td>{m.warning_precision:.2f}</td>
        <td>{m.rmse_intensity:.4f}</td>
        <td>{m.avg_lead_time_sec:.0f}</td>
        <td>{m.missed_events}</td>
        <td>{m.false_alarms}</td>
      </tr>
"""

    html += """
    </tbody>
  </table>
</div>

<div class="summary-card">
  <h2>指标说明</h2>
  <p><b>预警召回率</b>：真实危险事件中被成功预警的比例。越高表示漏报越少。</p>
  <p><b>预警精确率</b>：所有发出的预警中，真正对应危险事件的比例。越高表示误报越少。</p>
  <p><b>强度 RMSE</b>：卡尔曼滤波器输出强度与真实强度之间的均方根误差。越低表示拟合越好。</p>
  <p><b>综合评分</b>：拟合(40%) + 预警(40%) + 推荐(20%) 的加权总分。用于排序最优参数。</p>
  <p><b>提前预警时间</b>：从预警触发到情绪峰值的时间差。理想范围 30-120 秒。</p>
</div>

<div class="timestamp">报告由 evaluation.py 自动生成</div>
</div>
</body>
</html>
"""

    filepath = os.path.join(output_dir, "report.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  已保存: {filepath}")


# ================================================================
# 主入口
# ================================================================

def main():
    print("=" * 70)
    print("  心潮 EmoWave — 离线评估与参数敏感性分析")
    print("=" * 70)

    # --- 1. 定义 Ground Truth 事件 ---
    spec = GroundTruthEventSpec(
        trigger="愤怒爆发",
        trigger_code=99,
        start_hour=14,
        start_minute=0,
        duration_sec=600,
        peak_time_sec=180,
        peak_valence=0.05,
        peak_arousal=0.98,
        noise_std=0.05,
        seed=42,
    )

    print(f"\nGround Truth 事件规格:")
    print(f"  触发因素: {spec.trigger}")
    print(f"  峰值强度: {spec.peak_intensity:.3f}")
    print(f"  峰值时间: {spec.peak_time_sec}s (事件开始后)")
    print(f"  事件时长: {spec.duration_sec}s")

    # --- 2. 定义参数网格 ---
    q_values = [0.01, 0.05]
    r_values = [0.05, 0.12]
    window_values = [60.0, 120.0]
    alpha_values = [0.5, 2.0]

    param_grid = []
    for q, r, w, a in itertools.product(q_values, r_values, window_values, alpha_values):
        param_grid.append(ParamCombo(
            q_position_std=q,
            r_base_std=r,
            max_prediction_window_sec=w,
            bandit_alpha=a,
            label=f"q{q}_r{r}_w{int(w)}_a{a}",
        ))

    print(f"\n参数网格: {len(param_grid)} 种组合")
    print(f"  q_position_std: {q_values}")
    print(f"  r_base_std: {r_values}")
    print(f"  max_prediction_window: {window_values}")
    print(f"  bandit_alpha: {alpha_values}")

    # --- 3. 运行参数扫描 ---
    results = run_parameter_sweep(
        param_grid=param_grid,
        archetypes=ARCHETYPES,
        spec=spec,
        n_runs_per_combo=2,
    )

    # --- 4. 找出最优组合 ---
    best_result = max(results, key=lambda r: r.avg_metrics.combined_score)
    best_combo = best_result.combo

    print(f"\n{'=' * 70}")
    print(f"  最优参数组合: {best_combo.label}")
    print(f"  对应原型: {best_result.archetype.name}")
    print(f"  综合评分: {best_result.avg_metrics.combined_score:.3f}")
    print(f"  预警召回率: {best_result.avg_metrics.warning_recall:.2f}")
    print(f"  强度 RMSE: {best_result.avg_metrics.rmse_intensity:.4f}")
    print(f"  平均提前量: {best_result.avg_metrics.avg_lead_time_sec:.0f}s")
    print(f"{'=' * 70}")

    # --- 5. 生成图表 ---
    print(f"\n生成可视化图表...")
    plot_param_sensitivity(results, OUTPUT_DIR)
    plot_archetype_comparison(results, OUTPUT_DIR)
    plot_kf_trajectory_fit(spec, OUTPUT_DIR)

    # --- 6. 生成 HTML 报告 ---
    print(f"\n生成 HTML 报告...")
    generate_html_report(results, best_combo, OUTPUT_DIR)

    print(f"\n评估完成。所有输出保存在: {OUTPUT_DIR}/")
    print(f"  report.html          — 完整评估报告")
    print(f"  param_sensitivity.png — 参数敏感性趋势")
    print(f"  archetype_comparison.png — 用户原型对比")
    print(f"  kf_trajectory_fit.png — KF 拟合示例")


if __name__ == "__main__":
    main()
