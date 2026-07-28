#!/usr/bin/env python3
"""
data_simulator_v2.py — 心潮 EmoWave 多用户画像数据集生成器（V2）

本模块为四种典型用户画像生成完整的 7 天模拟数据集，用于：
  - 算法验证与回归测试
  - 推荐引擎冷启动实验
  - 阈值校准的基准数据

四种用户画像：
  A. 高压白领型 (high_pressure_white_collar) — 工作日压力显著，周末缓和
  B. 焦虑敏感型 (anxious_sensitive) — 高唤醒基线，恢复缓慢，微小刺激即可触发
  C. 情绪稳定型 (emotionally_stable) — 对照组，事件少、峰值低、恢复快
  D. 经期关联型 (menstrual_cycle_related) — 女性画像，情绪与月经周期相关

核心改进（相比 V1）：
  1. 三次样条轨迹生成，取代简单 Sigmoid+指数衰减
  2. 微波动（内心挣扎振荡）模拟
  3. 可选二次爆发（反刍思维引起的次级峰值）
  4. 心身解离：生理信号领先/滞后于主观报告
  5. 完整的每日数据结构（睡眠、EMA、事件、汇总）
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import os
import json
import math
import random
import hashlib
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Tuple, Any
from datetime import datetime, timedelta
import numpy as np

# 尝试导入 scipy 的三次样条，不可用时回退到 numpy 多项式
try:
    from scipy.interpolate import CubicSpline as ScipyCubicSpline
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

from kalman_filter import SliderObservation, PhysioInput
from simulator import TrajectoryPoint

# 输出目录
OUTPUT_DIR = "/workspace/emowave-engine/test_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ================================================================
# 全局常量：触发器代码映射
# ================================================================

TRIGGER_LIST = [
    "工作会议",   # 1
    "任务截止",   # 2
    "通勤压力",   # 3
    "社交冲突",   # 4
    "健康担忧",   # 5
    "财务压力",   # 6
    "睡眠不足",   # 7
    "家庭事务",   # 8
]

# 触发器名称 → 整数编码
TRIGGER_TO_CODE = {name: i + 1 for i, name in enumerate(TRIGGER_LIST)}

# 策略列表（与 recommender.py 保持一致）
STRATEGY_LIST = [
    "deep_breathing",       # 深呼吸练习
    "body_scan",            # 身体扫描放松
    "short_walk",           # 短暂散步
    "stretching",           # 拉伸运动
    "listen_music",         # 听音乐
    "journaling",           # 情绪日记书写
    "cold_water",           # 冷水洗脸
    "talk_friend",          # 联系朋友聊天
    "progressive_relax",    # 渐进式肌肉放松
    "grounding_543",        # 5-4-3-2-1 接地练习
]

# 策略中文名映射
STRATEGY_CN = {
    "deep_breathing": "深呼吸",
    "body_scan": "身体扫描",
    "short_walk": "短暂散步",
    "stretching": "拉伸运动",
    "listen_music": "听音乐",
    "journaling": "情绪日记",
    "cold_water": "冷水洗脸",
    "talk_friend": "联系朋友",
    "progressive_relax": "渐进放松",
    "grounding_543": "接地练习",
}

# 应对方式列表（供随机选取）
COPING_METHODS = [
    "深呼吸", "短暂散步", "听音乐", "喝水", "闭眼休息",
    "拉伸身体", "与朋友聊天", "写日记", "冥想", "冷水洗脸",
    "数数放松", "离开现场", "看窗外", "吃东西",
]

# 躯体症状池
BODY_SYMPTOM_POOL = [
    "头痛", "胸闷", "心悸", "胃部不适", "肌肉紧张",
    "呼吸急促", "手心出汗", "面部潮红", "疲劳感",
    "腹胀", "腰酸", "注意力难以集中", "头晕",
]

# 星期中文名
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ================================================================
# UserArchetypeV2 — 用户画像数据类
# ================================================================

@dataclass
class UserArchetypeV2:
    """
    用户画像定义（V2 增强版）。

    包含生理基线、人格特征、睡眠模式、触发器分布、
    时间偏好、策略修正、躯体症状倾向等完整参数。
    """
    name: str                    # 画像中文名
    name_en: str                 # 画像英文名（用于文件名）
    description: str             # 画像描述

    # --- 基线生理参数 ---
    baseline_valence: float      # 基线效价（0-1）
    baseline_arousal: float     # 基线唤醒度（0-1）
    base_hr: float               # 静息心率（BPM）
    base_hrv: float              # 静息 HRV（ms）
    base_hr_std: float           # 心率日常波动标准差
    base_hrv_std: float          # HRV 日常波动标准差

    # --- 人格特征 ---
    stress_sensitivity: float   # 压力敏感度（0-1，越高越容易受刺激）
    recovery_speed: float       # 恢复速度（0-1，越高恢复越快）
    event_probability_base: float  # 每日事件发生的基础概率

    # --- 睡眠参数 ---
    sleep_duration_mean: float  # 平均睡眠时长（小时）
    sleep_duration_std: float   # 睡眠时长标准差
    sleep_quality_mean: float   # 平均睡眠质量（1-10）
    sleep_quality_std: float    # 睡眠质量标准差

    # --- 触发器分布 ---
    trigger_weights: Dict[str, float]  # 触发器名称 → 权重

    # --- 时段事件权重（小时 → 权重） ---
    event_hour_weights: Dict[int, float]

    # --- 策略效果修正因子 ---
    strategy_modifier: Dict[str, float]  # 策略 ID → 效果修正（>1 更有效）

    # --- 躯体症状倾向 ---
    body_symptom_tendency: float          # 出现躯体症状的概率（0-1）
    common_body_symptoms: List[str]      # 常见症状列表

    # --- 噪声 ---
    slider_noise_std: float   # 滑条观测噪声标准差

    # --- 月经周期参数（仅经期关联型使用） ---
    menstrual_cycle_length: int = 28         # 周期长度（天）
    menstrual_cycle_start_day: int = 1       # 模拟开始时处于周期的第几天


# ================================================================
# 四种用户画像定义
# ================================================================

ARCHETYPES: List[UserArchetypeV2] = [
    # -----------------------------------------------------------
    # A. 高压白领型
    # -----------------------------------------------------------
    UserArchetypeV2(
        name="高压白领型",
        name_en="high_pressure_white_collar",
        description="工作日压力显著，会议前后愤怒概率高，睡眠不足，周末明显缓和",
        baseline_valence=0.40,
        baseline_arousal=0.45,
        base_hr=78.0,
        base_hrv=40.0,
        base_hr_std=4.0,
        base_hrv_std=6.0,
        stress_sensitivity=0.85,
        recovery_speed=0.45,
        event_probability_base=0.65,
        sleep_duration_mean=6.2,
        sleep_duration_std=0.8,
        sleep_quality_mean=5.0,
        sleep_quality_std=1.0,
        trigger_weights={
            "工作会议": 0.40,
            "任务截止": 0.25,
            "通勤压力": 0.15,
            "社交冲突": 0.10,
            "睡眠不足": 0.03,
            "健康担忧": 0.02,
            "财务压力": 0.03,
            "家庭事务": 0.02,
        },
        # 工作日会议时段权重更高（9-11, 14-16）
        event_hour_weights={
            7: 0.05, 8: 0.08, 9: 0.15, 10: 0.18, 11: 0.14,
            12: 0.04, 13: 0.06, 14: 0.16, 15: 0.17, 16: 0.12,
            17: 0.06, 18: 0.05, 19: 0.04, 20: 0.03, 21: 0.02,
            22: 0.01,
        },
        strategy_modifier={
            "deep_breathing": 1.3,
            "short_walk": 1.2,
            "listen_music": 1.0,
            "cold_water": 0.9,
            "journaling": 0.8,
            "body_scan": 0.9,
            "stretching": 1.0,
            "talk_friend": 0.7,
            "progressive_relax": 0.9,
            "grounding_543": 1.0,
        },
        body_symptom_tendency=0.65,
        common_body_symptoms=["头痛", "胸闷", "肌肉紧张", "胃部不适", "疲劳感"],
        slider_noise_std=0.06,
    ),

    # -----------------------------------------------------------
    # B. 焦虑敏感型
    # -----------------------------------------------------------
    UserArchetypeV2(
        name="焦虑敏感型",
        name_en="anxious_sensitive",
        description="整体高唤醒基线，轻微刺激即可触发中等强度事件，恢复缓慢，夜间易醒",
        baseline_valence=0.30,
        baseline_arousal=0.50,
        base_hr=83.0,
        base_hrv=45.0,
        base_hr_std=5.0,
        base_hrv_std=7.0,
        stress_sensitivity=0.80,
        recovery_speed=0.30,
        event_probability_base=0.55,
        sleep_duration_mean=7.0,
        sleep_duration_std=1.0,
        sleep_quality_mean=5.0,
        sleep_quality_std=1.2,
        trigger_weights={
            "健康担忧": 0.15,
            "财务压力": 0.15,
            "睡眠不足": 0.20,
            "工作会议": 0.20,
            "社交冲突": 0.15,
            "家庭事务": 0.05,
            "任务截止": 0.05,
            "通勤压力": 0.05,
        },
        # 焦虑型全天分布较均匀，但深夜和清晨偏高
        event_hour_weights={
            7: 0.06, 8: 0.08, 9: 0.10, 10: 0.09, 11: 0.08,
            12: 0.05, 13: 0.06, 14: 0.09, 15: 0.09, 16: 0.08,
            17: 0.06, 18: 0.05, 19: 0.05, 20: 0.04, 21: 0.04,
            22: 0.03,
        },
        strategy_modifier={
            "body_scan": 1.3,
            "journaling": 1.2,
            "deep_breathing": 1.1,
            "progressive_relax": 1.2,
            "grounding_543": 1.1,
            "listen_music": 1.0,
            "short_walk": 0.8,
            "cold_water": 0.7,
            "stretching": 0.9,
            "talk_friend": 0.8,
        },
        body_symptom_tendency=0.80,
        common_body_symptoms=[
            "心悸", "呼吸急促", "手心出汗", "肌肉紧张",
            "头晕", "胃部不适", "注意力难以集中",
        ],
        slider_noise_std=0.07,
    ),

    # -----------------------------------------------------------
    # C. 情绪稳定型（对照组）
    # -----------------------------------------------------------
    UserArchetypeV2(
        name="情绪稳定型",
        name_en="emotionally_stable",
        description="对照组：事件少、峰值低、恢复快，触发器分布均匀且强度低",
        baseline_valence=0.65,
        baseline_arousal=0.25,
        base_hr=68.0,
        base_hrv=62.0,
        base_hr_std=3.0,
        base_hrv_std=5.0,
        stress_sensitivity=0.25,
        recovery_speed=0.85,
        event_probability_base=0.20,
        sleep_duration_mean=7.8,
        sleep_duration_std=0.5,
        sleep_quality_mean=8.0,
        sleep_quality_std=0.8,
        trigger_weights={
            "工作会议": 0.12,
            "任务截止": 0.10,
            "通勤压力": 0.10,
            "社交冲突": 0.10,
            "健康担忧": 0.08,
            "财务压力": 0.08,
            "睡眠不足": 0.12,
            "家庭事务": 0.10,
        },
        # 事件时段分布均匀
        event_hour_weights={
            7: 0.06, 8: 0.08, 9: 0.09, 10: 0.09, 11: 0.08,
            12: 0.04, 13: 0.06, 14: 0.09, 15: 0.09, 16: 0.08,
            17: 0.06, 18: 0.05, 19: 0.04, 20: 0.03, 21: 0.02,
            22: 0.01,
        },
        strategy_modifier={
            "deep_breathing": 1.0,
            "body_scan": 1.0,
            "short_walk": 1.0,
            "stretching": 1.0,
            "listen_music": 1.0,
            "journaling": 1.0,
            "cold_water": 1.0,
            "talk_friend": 1.0,
            "progressive_relax": 1.0,
            "grounding_543": 1.0,
        },
        body_symptom_tendency=0.20,
        common_body_symptoms=["疲劳感"],
        slider_noise_std=0.04,
    ),

    # -----------------------------------------------------------
    # D. 经期关联型
    # -----------------------------------------------------------
    UserArchetypeV2(
        name="经期关联型",
        name_en="menstrual_cycle_related",
        description=(
            "女性画像，情绪事件频率与月经周期阶段相关。"
            "卵泡期稳定，排卵期轻微升高，黄体期（尤其经前）敏感性显著增加"
        ),
        baseline_valence=0.50,
        baseline_arousal=0.35,
        base_hr=74.0,
        base_hrv=50.0,
        base_hr_std=4.0,
        base_hrv_std=6.0,
        stress_sensitivity=0.40,  # 基础值（卵泡期），随周期阶段动态调整
        recovery_speed=0.55,
        event_probability_base=0.35,
        sleep_duration_mean=7.2,
        sleep_duration_std=0.8,
        sleep_quality_mean=7.0,
        sleep_quality_std=1.0,
        trigger_weights={
            "工作会议": 0.15,
            "任务截止": 0.10,
            "通勤压力": 0.08,
            "社交冲突": 0.10,
            "健康担忧": 0.08,
            "财务压力": 0.07,
            "睡眠不足": 0.17,
            "家庭事务": 0.15,
        },
        event_hour_weights={
            7: 0.06, 8: 0.07, 9: 0.10, 10: 0.10, 11: 0.08,
            12: 0.05, 13: 0.06, 14: 0.10, 15: 0.10, 16: 0.08,
            17: 0.06, 18: 0.05, 19: 0.04, 20: 0.03, 21: 0.02,
            22: 0.01,
        },
        strategy_modifier={
            "listen_music": 1.3,
            "progressive_relax": 1.2,
            "deep_breathing": 1.1,
            "short_walk": 1.0,
            "body_scan": 1.1,
            "journaling": 1.0,
            "stretching": 0.9,
            "cold_water": 0.8,
            "talk_friend": 1.0,
            "grounding_543": 0.9,
        },
        body_symptom_tendency=0.60,
        common_body_symptoms=[
            "腹胀", "疲劳感", "腰酸", "头痛",
            "肌肉紧张", "注意力难以集中",
        ],
        slider_noise_std=0.05,
        menstrual_cycle_length=28,
        menstrual_cycle_start_day=5,  # 从卵泡期第5天开始模拟
    ),
]


# ================================================================
# 辅助函数
# ================================================================

def _weighted_choice(rng: random.Random, items: List, weights: List) -> Any:
    """按权重随机选择一个元素。"""
    total = sum(weights)
    r = rng.random() * total
    cumulative = 0.0
    for item, w in zip(items, weights):
        cumulative += w
        if r <= cumulative:
            return item
    return items[-1]


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """将值限制在 [lo, hi] 范围内。"""
    return max(lo, min(hi, v))


def _make_event_id(day_index: int, event_index: int, trigger: str) -> str:
    """生成唯一事件 ID。"""
    raw = f"evt_d{day_index}_{event_index}_{trigger}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"evt_d{day_index}_e{event_index}_{h}"


# ================================================================
# 三次样条轨迹生成（带微波动和可选二次爆发）
# ================================================================

def _cubic_spline_interpolate(
    control_t: List[float],
    control_v: List[float],
    eval_t: np.ndarray,
) -> np.ndarray:
    """
    三次样条插值：优先使用 scipy，不可用时回退到 numpy 多项式。
    """
    if _HAS_SCIPY:
        cs = ScipyCubicSpline(control_t, control_v, bc_type='clamped')
        return cs(eval_t)
    else:
        # numpy 回退：使用分段线性 + 平滑
        # 用 numpy 的 polyfit 做三次多项式拟合（不保证经过控制点）
        # 改用手动分段三次 Hermite 插值以通过所有控制点
        result = np.zeros_like(eval_t, dtype=float)
        for i in range(len(control_t) - 1):
            t0 = control_t[i]
            t1 = control_t[i + 1]
            v0 = control_v[i]
            v1 = control_v[i + 1]
            # 估计斜率
            if i > 0:
                m0 = (control_v[i] - control_v[i - 1]) / (control_t[i] - control_t[i - 1])
            else:
                m0 = (v1 - v0) / (t1 - t0)
            if i < len(control_t) - 2:
                m1 = (control_v[i + 2] - control_v[i + 1]) / (control_t[i + 2] - control_t[i + 1])
            else:
                m1 = (v1 - v0) / (t1 - t0)
            # 三次 Hermite 基函数
            dt = t1 - t0
            mask = (eval_t >= t0) & (eval_t <= t1)
            if i == len(control_t) - 2:
                mask = (eval_t >= t0) & (eval_t <= t1 + 1e-9)
            s = (eval_t[mask] - t0) / dt
            h00 = 2 * s ** 3 - 3 * s ** 2 + 1
            h10 = s ** 3 - 2 * s ** 2 + s
            h01 = -2 * s ** 3 + 3 * s ** 2
            h11 = s ** 3 - s ** 2
            result[mask] = h00 * v0 + h10 * dt * m0 + h01 * v1 + h11 * dt * m1
        return result


def generate_realistic_trajectory(
    duration_sec: float,
    dt: float,
    start_valence: float,
    start_arousal: float,
    peak_valence: float,
    peak_arousal: float,
    peak_time_fraction: float,
    recovery_speed: float,
    noise_std: float,
    allow_secondary_peak: bool = True,
    secondary_peak_probability: float = 0.25,
    seed: Optional[int] = None,
) -> List[TrajectoryPoint]:
    """
    生成逼真的情绪轨迹。

    特性：
      1. 控制点 + 三次样条插值，生成平滑曲线
      2. 微波动（内心挣扎振荡）：高频小幅振荡叠加在主轨迹上
      3. 可选二次爆发：反刍思维引起的次级峰值（约 25% 概率）
      4. 观测噪声和触摸行为模拟

    参数：
      duration_sec: 事件总时长（秒）
      dt: 采样间隔（秒）
      start_valence/arousal: 起始基线值
      peak_valence/arousal: 峰值（负性效价低、唤醒高）
      peak_time_fraction: 峰值出现在总时长的比例位置
      recovery_speed: 恢复速度（0-1）
      noise_std: 观测噪声标准差
      allow_secondary_peak: 是否允许二次爆发
      secondary_peak_probability: 二次爆发概率
      seed: 随机种子（可复现）

    返回：
      TrajectoryPoint 列表
    """
    rng = random.Random(seed)
    np_rng = np.random.RandomState(seed)

    n_steps = int(duration_sec / dt)
    if n_steps < 4:
        n_steps = 4

    # 时间数组
    t_array = np.linspace(0, duration_sec, n_steps)

    # --- 判断是否生成二次爆发 ---
    has_secondary = (
        allow_secondary_peak
        and rng.random() < secondary_peak_probability
    )

    # --- 构建控制点 ---
    delta_v = peak_valence - start_valence  # 效价变化量（通常为负）
    delta_a = peak_arousal - start_arousal  # 唤醒变化量（通常为正）

    peak_t = peak_time_fraction * duration_sec
    recovery_dur = (1.0 - peak_time_fraction) * duration_sec

    # 控制点时间
    if has_secondary:
        # 有二次爆发：控制点更多
        sec_peak_t = peak_t + recovery_dur * 0.5
        sec_peak_v = start_valence + delta_v * 0.35  # 次级峰值为初级峰值的 40-70%
        sec_peak_a = start_arousal + delta_a * 0.40
        end_residual_v = start_valence + delta_v * 0.05  # 最终回归基线，留小残差
        end_residual_a = start_arousal + delta_a * 0.03

        ctrl_t = [
            0.0,
            peak_t * 0.5,              # 上升早期
            peak_t,                      # 主峰
            peak_t + recovery_dur * 0.25,  # 初步恢复
            sec_peak_t,                  # 次级峰值
            duration_sec,                # 结束
        ]
        ctrl_v_vals = [
            start_valence,
            start_valence + delta_v * 0.25,
            peak_valence,
            peak_valence + (-delta_v) * 0.50,  # 回复 50%
            sec_peak_v,
            end_residual_v,
        ]
        ctrl_a_vals = [
            start_arousal,
            start_arousal + delta_a * 0.30,
            peak_arousal,
            peak_arousal + (-delta_a) * 0.45,  # 回复 45%
            sec_peak_a,
            end_residual_a,
        ]
    else:
        # 无二次爆发：5 个控制点
        end_residual_v = start_valence + delta_v * 0.03
        end_residual_a = start_arousal + delta_a * 0.02

        ctrl_t = [
            0.0,
            peak_t * 0.6,               # 上升早期
            peak_t,                      # 主峰
            peak_t + recovery_dur * 0.35,  # 初步恢复
            duration_sec,                # 结束
        ]
        ctrl_v_vals = [
            start_valence,
            start_valence + delta_v * 0.20,
            peak_valence,
            peak_valence + (-delta_v) * 0.55,
            end_residual_v,
        ]
        ctrl_a_vals = [
            start_arousal,
            start_arousal + delta_a * 0.30,
            peak_arousal,
            peak_arousal + (-delta_a) * 0.50,
            end_residual_a,
        ]

    # 对控制点加微小扰动
    ctrl_v_vals = [
        _clip(v + rng.gauss(0, 0.01)) for v in ctrl_v_vals
    ]
    ctrl_a_vals = [
        _clip(a + rng.gauss(0, 0.01)) for a in ctrl_a_vals
    ]

    # --- 三次样条插值 ---
    true_valence_arr = _cubic_spline_interpolate(ctrl_t, ctrl_v_vals, t_array)
    true_arousal_arr = _cubic_spline_interpolate(ctrl_t, ctrl_a_vals, t_array)

    # --- 微波动（内心挣扎振荡） ---
    # 频率 0.05-0.1 Hz，振幅正比于当前强度
    osc_freq = rng.uniform(0.05, 0.10)
    osc_phase_v = rng.uniform(0, 2 * math.pi)
    osc_phase_a = rng.uniform(0, 2 * math.pi)

    for i in range(n_steps):
        t_sec = t_array[i]
        # 当前情绪强度（偏离基线的程度）
        intensity_v = abs(true_valence_arr[i] - start_valence)
        intensity_a = abs(true_arousal_arr[i] - start_arousal)
        intensity = (intensity_v + intensity_a) / 2.0

        # 振幅与强度成正比，最大 0.03
        amplitude = 0.03 * intensity / max(0.01, max(abs(delta_v), abs(delta_a)))

        osc_v = amplitude * math.sin(2 * math.pi * osc_freq * t_sec + osc_phase_v)
        osc_a = amplitude * math.sin(2 * math.pi * osc_freq * t_sec + osc_phase_a)

        true_valence_arr[i] = _clip(true_valence_arr[i] + osc_v)
        true_arousal_arr[i] = _clip(true_arousal_arr[i] + osc_a)

    # --- 构建 TrajectoryPoint 列表 ---
    points: List[TrajectoryPoint] = []
    last_touch_t = 0.0

    for i in range(n_steps):
        t = float(t_array[i])
        tv = float(true_valence_arr[i])
        ta = float(true_arousal_arr[i])

        # 触摸速度
        if i > 0:
            dv = abs(tv - points[-1].true_valence)
            da = abs(ta - points[-1].true_arousal)
            touch_vel = (dv + da) / dt
        else:
            touch_vel = 0.0

        # 停顿间隔
        if rng.random() < 0.04:
            stillness = rng.uniform(2.0, 5.0)
        else:
            stillness = dt

        # 观测噪声
        if rng.random() < 0.02:
            # 偶尔跳变（误触/延迟补录）
            obs_v = tv + rng.gauss(0, noise_std * 4)
            obs_a = ta + rng.gauss(0, noise_std * 4)
        else:
            obs_v = tv + rng.gauss(0, noise_std)
            obs_a = ta + rng.gauss(0, noise_std)

        obs_v = _clip(obs_v)
        obs_a = _clip(obs_a)

        points.append(TrajectoryPoint(
            t=round(t, 2),
            true_valence=round(tv, 4),
            true_arousal=round(ta, 4),
            obs_valence=round(obs_v, 4),
            obs_arousal=round(obs_a, 4),
            touch_velocity=round(touch_vel, 4),
            stillness=stillness,
        ))

        last_touch_t = t

    return points


# ================================================================
# 心身解离生理信号生成
# ================================================================

def generate_physio_with_dissociation(
    true_arousals: List[float],
    base_hr: float,
    base_hrv: float,
    dissociation_prob: float = 0.12,
    dissociation_lag_sec: float = 15.0,
    seed: Optional[int] = None,
) -> Tuple[List[Optional[PhysioInput]], List[bool]]:
    """
    生成带心身解离的生理信号。

    在约 10-15% 的时间段内，生理信号变化领先或滞后于主观报告，
    模拟自主神经系统反应与主观情绪觉察之间的时间差。

    参数：
      true_arousals: 真实唤醒度序列
      base_hr: 基线心率
      base_hrv: 基线 HRV
      dissociation_prob: 出现解离的时间段概率
      dissociation_lag_sec: 解离的时间延迟（秒）
      seed: 随机种子

    返回：
      (physio_signals, dissociation_flags)
      - physio_signals: 生理信号列表（可能含 None）
      - dissociation_flags: 每个时间点是否处于解离状态
    """
    rng = random.Random(seed)

    n = len(true_arousals)
    lag_steps = max(1, int(dissociation_lag_sec))  # 假设 dt=1s
    signals: List[Optional[PhysioInput]] = [None] * n
    flags: List[bool] = [False] * n

    # 生成"延迟版"唤醒度（用于解离期间）
    lagged_arousals = [true_arousals[0]] * n
    for i in range(1, n):
        # 简单移动平均作为延迟信号
        idx = max(0, i - lag_steps)
        lagged_arousals[i] = true_arousals[idx]

    # 判断哪些时间段处于解离状态
    # 以连续片段形式出现（而非逐点随机）
    in_dissociation = False
    diss_start = 0
    diss_end = 0

    for i in range(n):
        if not in_dissociation:
            if rng.random() < dissociation_prob * 0.1:  # 每个点开始解离段的小概率
                in_dissociation = True
                diss_start = i
                diss_end = i + rng.randint(10, 25)  # 持续 10-25 秒
        else:
            if i >= diss_end:
                in_dissociation = False

        flags[i] = in_dissociation

    hr_sensitivity = 40.0
    hrv_sensitivity = 0.4
    noise_std_hr = 2.0
    noise_std_hrv = 3.0
    dropout_prob = 0.05
    rssi_mean = -55.0

    for i, arousal in enumerate(true_arousals):
        # 信号丢失
        if rng.random() < dropout_prob:
            signals[i] = None
            continue

        # 解离时使用延迟的唤醒度
        if flags[i]:
            used_arousal = lagged_arousals[i]
        else:
            used_arousal = arousal

        hr = base_hr + hr_sensitivity * used_arousal + rng.gauss(0, noise_std_hr)
        hrv_ratio = 1.0 - hrv_sensitivity * used_arousal
        hrv_drop = max(0, 1.0 - hrv_ratio)
        hrv = base_hrv * hrv_ratio + rng.gauss(0, noise_std_hrv)

        rssi = rssi_mean + rng.gauss(0, 8)
        quality = 1.0 if rssi > -60 else max(0.2, 1.0 + (rssi + 60) / 40.0)

        signals[i] = PhysioInput(
            timestamp=float(i),
            hrv_drop_ratio=round(hrv_drop, 3),
            hr_change=round(hr - base_hr, 1),
            signal_quality=round(_clip(quality), 2),
        )

    return signals, flags


# ================================================================
# VirtualUserV2 — 虚拟用户模拟器
# ================================================================

class VirtualUserV2:
    """
    虚拟用户模拟器（V2）。

    基于画像参数模拟用户的每日行为，包括：
      - 月经周期阶段计算（经期关联型）
      - 睡眠数据生成
      - EMA（生态瞬时评估）数据
      - 情绪事件生成（含轨迹和生理信号）
    """

    def __init__(self, archetype: UserArchetypeV2, seed: int = 42):
        self.archetype = archetype
        self.rng = random.Random(seed)
        self.np_rng = np.random.RandomState(seed)
        self._seed = seed

    def get_cycle_phase(self, day_index: int) -> Tuple[int, Optional[str]]:
        """
        计算月经周期阶段。

        仅经期关联型返回有意义值，其他画像返回 (0, None)。

        参数：
          day_index: 第几天（0-based）

        返回：
          (cycle_day, phase_name)
          - cycle_day: 周期中的天数（1-28）
          - phase_name: "follicular" / "ovulatory" / "luteal" / None
        """
        if self.archetype.name_en != "menstrual_cycle_related":
            return (0, None)

        cycle_len = self.archetype.menstrual_cycle_length
        start_day = self.archetype.menstrual_cycle_start_day
        cycle_day = ((start_day - 1 + day_index) % cycle_len) + 1

        if cycle_day <= 13:
            phase = "follicular"
        elif cycle_day <= 16:
            phase = "ovulatory"
        else:
            phase = "luteal"

        return (cycle_day, phase)

    def _phase_stress_sensitivity(self, cycle_phase: Optional[str]) -> float:
        """根据周期阶段返回调整后的压力敏感度。"""
        base = self.archetype.stress_sensitivity
        if cycle_phase == "follicular":
            return 0.40
        elif cycle_phase == "ovulatory":
            return 0.60
        elif cycle_phase == "luteal":
            return 0.85
        return base

    def _phase_event_probability(self, cycle_phase: Optional[str], weekday_index: int) -> float:
        """根据周期阶段和星期返回事件概率。"""
        base = self.archetype.event_probability_base

        # 经期关联型的周期调整
        if self.archetype.name_en == "menstrual_cycle_related":
            if cycle_phase == "follicular":
                base *= 0.8
            elif cycle_phase == "ovulatory":
                base *= 1.0
            elif cycle_phase == "luteal":
                # 经前（24-28天）额外增加
                base *= 1.5

        # 高压白领型的周末调整
        if self.archetype.name_en == "high_pressure_white_collar":
            if weekday_index >= 5:  # 周末
                return 0.15
            else:
                return 0.70

        return base

    def generate_sleep(
        self,
        day_index: int,
        prev_day_event_count: int,
        cycle_phase: Optional[str],
    ) -> dict:
        """
        生成单日睡眠数据。

        参数：
          day_index: 天数索引（0-based）
          prev_day_event_count: 前一天事件数量（影响睡眠质量）
          cycle_phase: 月经周期阶段

        返回：
          睡眠数据字典
        """
        a = self.archetype
        rng = self.rng

        # 基础参数
        dur_mean = a.sleep_duration_mean
        dur_std = a.sleep_duration_std
        qual_mean = a.sleep_quality_mean
        qual_std = a.sleep_quality_std

        # 前一天事件多 → 睡眠质量下降
        event_penalty = min(prev_day_event_count * 0.3, 2.0)
        qual_mean_adj = max(1.0, qual_mean - event_penalty)

        # 经期关联型：黄体期睡眠受扰
        if a.name_en == "menstrual_cycle_related" and cycle_phase == "luteal":
            dur_mean -= 0.5
            qual_mean_adj -= 0.8

        # 周末睡眠略多（高压白领型）
        # day_index 0 对应第一天（可能是周三），需要根据 weekday 判断
        # 在 generate_7day_dataset 中已考虑，此处不做额外调整

        duration = max(4.0, min(10.0, rng.gauss(dur_mean, dur_std)))
        quality = max(1.0, min(10.0, rng.gauss(qual_mean_adj, qual_std)))

        # 深睡和 REM 比例
        deep_ratio = max(0.05, min(0.35, rng.gauss(0.18, 0.05)))
        if quality < 5:
            deep_ratio *= 0.7
        rem_ratio = max(0.1, min(0.3, rng.gauss(0.22, 0.04)))

        # 夜间醒来次数
        awakenings_base = 1 if quality >= 6 else 3
        awakenings = max(0, int(rng.gauss(awakenings_base, 1.0)))

        # 焦虑型更多醒来
        if a.name_en == "anxious_sensitive":
            awakenings = max(awakenings, int(rng.gauss(3, 1.5)))

        return {
            "duration_hours": round(duration, 2),
            "quality_score": round(quality, 1),
            "deep_sleep_ratio": round(deep_ratio, 3),
            "rem_ratio": round(rem_ratio, 3),
            "awakenings": awakenings,
        }

    def generate_ema(
        self,
        timestamp: float,
        sleep_quality: float,
        time_label: str,
        cycle_phase: Optional[str],
    ) -> dict:
        """
        生成 EMA（生态瞬时评估）数据。

        参数：
          timestamp: Unix 时间戳
          sleep_quality: 前夜睡眠质量
          time_label: "morning" 或 "evening"
          cycle_phase: 月经周期阶段

        返回：
          EMA 数据字典
        """
        a = self.archetype
        rng = self.rng

        base_v = a.baseline_valence
        base_a = a.baseline_arousal

        # 睡眠质量影响早晨情绪
        sleep_effect = (sleep_quality - 7.0) * 0.03  # 质量低于7则负面

        # 周期阶段影响
        cycle_v_shift = 0.0
        cycle_a_shift = 0.0
        if a.name_en == "menstrual_cycle_related":
            if cycle_phase == "luteal":
                cycle_v_shift = -0.10
                cycle_a_shift = 0.10
            elif cycle_phase == "ovulatory":
                cycle_v_shift = 0.05
                cycle_a_shift = 0.03

        # 傍晚情绪通常比早晨低
        if time_label == "evening":
            time_v_shift = -0.05
            time_a_shift = 0.02
        else:
            time_v_shift = 0.0
            time_a_shift = 0.0

        valence = _clip(
            base_v + sleep_effect + cycle_v_shift + time_v_shift
            + rng.gauss(0, 0.08)
        )
        arousal = _clip(
            base_a - sleep_effect * 0.5 + cycle_a_shift + time_a_shift
            + rng.gauss(0, 0.06)
        )

        # 简单备注
        notes = ""
        if time_label == "morning":
            if sleep_quality < 5:
                notes = "昨晚睡得不好"
            else:
                notes = "早上状态一般"
        else:
            notes = "一天结束了"

        return {
            "timestamp": timestamp,
            "valence": round(valence, 4),
            "arousal": round(arousal, 4),
            "notes": notes,
        }

    def generate_event(
        self,
        day_index: int,
        sleep_quality: float,
        base_timestamp: float,
        weekday_index: int,
        cycle_phase: Optional[str],
        event_index: int,
    ) -> Optional[dict]:
        """
        生成一个情绪事件。

        参数：
          day_index: 天数索引
          sleep_quality: 当日（或前夜）睡眠质量
          base_timestamp: 当天 0:00 的 Unix 时间戳
          weekday_index: 星期几（0=周一，6=周日）
          cycle_phase: 月经周期阶段
          event_index: 当天第几个事件（0-based）

        返回：
          事件数据字典，或 None（如果没有事件发生）
        """
        a = self.archetype
        rng = self.rng

        # 事件概率
        event_prob = self._phase_event_probability(cycle_phase, weekday_index)

        # 睡眠不足增加事件概率
        if sleep_quality < 5:
            event_prob = min(0.95, event_prob + 0.15)

        if rng.random() > event_prob:
            return None

        # --- 选择触发器 ---
        trigger = _weighted_choice(
            rng,
            list(a.trigger_weights.keys()),
            list(a.trigger_weights.values()),
        )
        trigger_code = TRIGGER_TO_CODE.get(trigger, 1)

        # --- 选择事件发生时间 ---
        event_hour = _weighted_choice(
            rng,
            list(a.event_hour_weights.keys()),
            list(a.event_hour_weights.values()),
        )
        # 加随机分钟偏移
        event_minute = rng.randint(0, 59)
        start_time = base_timestamp + event_hour * 3600 + event_minute * 60

        # --- 事件时长（120-600 秒） ---
        duration_sec = rng.randint(120, 600)
        end_time = start_time + duration_sec
        dt = 1.0

        # --- 峰值参数 ---
        sensitivity = self._phase_stress_sensitivity(cycle_phase)

        # 压力敏感度越高 → 峰值唤醒越高、效价越低
        peak_arousal = _clip(
            a.baseline_arousal
            + sensitivity * rng.uniform(0.35, 0.55)
            + (7.0 - sleep_quality) * 0.02
        )
        peak_valence = _clip(
            a.baseline_valence
            - sensitivity * rng.uniform(0.25, 0.45)
            - (7.0 - sleep_quality) * 0.015
        )

        peak_time_frac = rng.uniform(0.25, 0.45)
        recovery = a.recovery_speed

        # 事件种子（确保可复现）
        event_seed = self._seed + day_index * 1000 + event_index * 100 + int(event_hour)

        # --- 生成轨迹 ---
        trajectory = generate_realistic_trajectory(
            duration_sec=duration_sec,
            dt=dt,
            start_valence=a.baseline_valence,
            start_arousal=a.baseline_arousal,
            peak_valence=peak_valence,
            peak_arousal=peak_arousal,
            peak_time_fraction=peak_time_frac,
            recovery_speed=recovery,
            noise_std=a.slider_noise_std,
            allow_secondary_peak=True,
            secondary_peak_probability=0.25,
            seed=event_seed,
        )

        # --- 生成生理信号（含心身解离） ---
        true_arousals = [p.true_arousal for p in trajectory]
        physio_seed = event_seed + 99999
        physio_signals, diss_flags = generate_physio_with_dissociation(
            true_arousals=true_arousals,
            base_hr=a.base_hr + rng.gauss(0, a.base_hr_std * 0.3),
            base_hrv=a.base_hrv + rng.gauss(0, a.base_hrv_std * 0.3),
            dissociation_prob=0.12,
            dissociation_lag_sec=15.0,
            seed=physio_seed,
        )

        # --- 策略推荐 ---
        # 选择效果修正最高的策略
        best_strategy_id = max(
            a.strategy_modifier, key=lambda k: a.strategy_modifier[k]
        )
        # 加入一些随机性
        if rng.random() < 0.3:
            # 30% 概率选择次优策略
            sorted_strats = sorted(
                a.strategy_modifier.items(), key=lambda x: x[1], reverse=True
            )
            best_strategy_id = sorted_strats[rng.randint(0, min(2, len(sorted_strats) - 1))][0]

        modifier = a.strategy_modifier.get(best_strategy_id, 1.0)
        predicted_score = round(_clip(3.0 * modifier + rng.gauss(0, 0.3), 1.0, 5.0), 2)
        ucb_score = round(predicted_score + rng.uniform(0.1, 0.5), 2)

        strategy_rec = {
            "id": best_strategy_id,
            "name": STRATEGY_CN.get(best_strategy_id, best_strategy_id),
            "predicted_score": predicted_score,
            "ucb_score": ucb_score,
        }

        # --- 用户评分 ---
        true_effect = modifier * 0.8
        user_rating = round(_clip(true_effect * 3.5 + rng.gauss(0, 0.5), 1, 5))
        user_rating = int(user_rating)

        # --- 躯体症状 ---
        n_symptoms = 0
        if rng.random() < a.body_symptom_tendency:
            n_symptoms = rng.randint(1, 3)
        body_symptoms = rng.sample(
            a.common_body_symptoms,
            min(n_symptoms, len(a.common_body_symptoms)),
        )

        # --- 应对方式 ---
        n_coping = rng.randint(1, 3)
        coping = rng.sample(COPING_METHODS, min(n_coping, len(COPING_METHODS)))

        # --- 构建 JSON 友好的轨迹和生理数据 ---
        traj_list = []
        physio_list = []
        for j, tp in enumerate(trajectory):
            traj_list.append({
                "t": tp.t,
                "true_valence": tp.true_valence,
                "true_arousal": tp.true_arousal,
                "obs_valence": tp.obs_valence,
                "obs_arousal": tp.obs_arousal,
                "touch_velocity": tp.touch_velocity,
                "stillness": round(tp.stillness, 2),
            })

            if j < len(physio_signals) and physio_signals[j] is not None:
                ps = physio_signals[j]
                physio_list.append({
                    "t": round(ps.timestamp, 2),
                    "hrv_drop_ratio": ps.hrv_drop_ratio,
                    "hr_change": ps.hr_change,
                    "signal_quality": ps.signal_quality,
                })
            else:
                physio_list.append(None)

        event_id = _make_event_id(day_index, event_index, trigger)

        return {
            "event_id": event_id,
            "trigger": trigger,
            "trigger_code": trigger_code,
            "start_time": start_time,
            "end_time": end_time,
            "duration_sec": duration_sec,
            "peak_valence": round(peak_valence, 4),
            "peak_arousal": round(peak_arousal, 4),
            "trajectory": traj_list,
            "physio_signals": physio_list,
            "strategy_recommendation": strategy_rec,
            "user_rating": user_rating,
            "body_symptoms": body_symptoms,
            "coping_methods": coping,
        }


# ================================================================
# DatasetGenerator — 7 天数据集生成器
# ================================================================

class DatasetGenerator:
    """
    完整 7 天数据集生成器。

    为指定用户画像生成包含睡眠、EMA、事件、汇总的完整数据集。
    """

    def __init__(self, archetype: UserArchetypeV2, seed: int = 42):
        self.archetype = archetype
        self.seed = seed
        self.user = VirtualUserV2(archetype, seed=seed)

    def generate_7day_dataset(self, start_date: datetime) -> dict:
        """
        生成完整的 7 天数据集。

        参数：
          start_date: 模拟起始日期

        返回：
          完整数据集字典（可直接序列化为 JSON）
        """
        a = self.archetype

        # 画像信息
        archetype_info = {
            "name": a.name,
            "name_en": a.name_en,
            "description": a.description,
            "parameters": {
                "baseline_valence": a.baseline_valence,
                "baseline_arousal": a.baseline_arousal,
                "base_hr": a.base_hr,
                "base_hrv": a.base_hrv,
                "stress_sensitivity": a.stress_sensitivity,
                "recovery_speed": a.recovery_speed,
                "event_probability_base": a.event_probability_base,
                "sleep_duration_mean": a.sleep_duration_mean,
                "sleep_quality_mean": a.sleep_quality_mean,
                "trigger_weights": a.trigger_weights,
                "strategy_modifier": a.strategy_modifier,
            },
        }

        # 模拟周期
        end_date = start_date + timedelta(days=6)
        sim_period = {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "days": 7,
        }

        daily_data: List[dict] = []
        prev_day_event_count = 0

        # 周统计
        total_events = 0
        sleep_qualities = []
        morning_valences = []
        trigger_dist: Dict[str, int] = {}
        strategy_eff: Dict[str, List[float]] = {}
        symptom_freq: Dict[str, int] = {}

        for day_idx in range(7):
            current_date = start_date + timedelta(days=day_idx)
            date_str = current_date.strftime("%Y-%m-%d")
            weekday_index = current_date.weekday()  # 0=周一
            weekday_cn = WEEKDAY_CN[weekday_index]

            # 当天 0:00 的 Unix 时间戳
            day_start_ts = int(current_date.timestamp())

            # 周期阶段
            cycle_day, cycle_phase = self.user.get_cycle_phase(day_idx)

            # --- 睡眠（前一天晚上的睡眠，影响当天） ---
            sleep = self.user.generate_sleep(
                day_index=day_idx,
                prev_day_event_count=prev_day_event_count,
                cycle_phase=cycle_phase,
            )

            # --- 早晨 EMA（8:00） ---
            morning_ts = day_start_ts + 8 * 3600
            morning_ema = self.user.generate_ema(
                timestamp=morning_ts,
                sleep_quality=sleep["quality_score"],
                time_label="morning",
                cycle_phase=cycle_phase,
            )

            # --- 傍晚 EMA（21:00） ---
            evening_ts = day_start_ts + 21 * 3600
            evening_ema = self.user.generate_ema(
                timestamp=evening_ts,
                sleep_quality=sleep["quality_score"],
                time_label="evening",
                cycle_phase=cycle_phase,
            )

            # --- 生成事件 ---
            events: List[dict] = []
            max_events_per_day = 5
            for evt_idx in range(max_events_per_day):
                event = self.user.generate_event(
                    day_index=day_idx,
                    sleep_quality=sleep["quality_score"],
                    base_timestamp=day_start_ts,
                    weekday_index=weekday_index,
                    cycle_phase=cycle_phase,
                    event_index=evt_idx,
                )
                if event is not None:
                    events.append(event)

            prev_day_event_count = len(events)

            # --- 日汇总 ---
            peak_arousals = [e["peak_arousal"] for e in events] if events else [a.baseline_arousal]
            peak_v_vals = [e["peak_valence"] for e in events] if events else [a.baseline_valence]

            # 策略使用统计
            strategy_usage: Dict[str, int] = {}
            strategy_ratings: Dict[str, List[float]] = {}
            for e in events:
                sid = e["strategy_recommendation"]["id"]
                strategy_usage[sid] = strategy_usage.get(sid, 0) + 1
                rating = e["user_rating"]
                if sid not in strategy_ratings:
                    strategy_ratings[sid] = []
                strategy_ratings[sid].append(rating)

            avg_rating = 0.0
            all_ratings = []
            for sid, ratings in strategy_ratings.items():
                all_ratings.extend(ratings)
            if all_ratings:
                avg_rating = round(sum(all_ratings) / len(all_ratings), 2)

            daily_summary = {
                "event_count": len(events),
                "peak_arousal_max": round(max(peak_arousals), 4),
                "avg_valence": round(sum(peak_v_vals) / len(peak_v_vals), 4),
                "avg_arousal": round(sum(peak_arousals) / len(peak_arousals), 4),
                "strategy_usage": strategy_usage,
                "avg_strategy_rating": avg_rating,
            }

            # 构建日数据
            day_data = {
                "date": date_str,
                "weekday": weekday_cn,
                "weekday_index": weekday_index,
                "cycle_day": cycle_day if cycle_phase else None,
                "cycle_phase": cycle_phase,
                "sleep": sleep,
                "morning_ema": morning_ema,
                "evening_ema": evening_ema,
                "events": events,
                "daily_summary": daily_summary,
            }
            daily_data.append(day_data)

            # --- 累计周统计 ---
            total_events += len(events)
            sleep_qualities.append(sleep["quality_score"])
            morning_valences.append(morning_ema["valence"])

            for e in events:
                # 触发器分布
                trig = e["trigger"]
                trigger_dist[trig] = trigger_dist.get(trig, 0) + 1

                # 策略效果
                sid = e["strategy_recommendation"]["id"]
                rating = e["user_rating"]
                if sid not in strategy_eff:
                    strategy_eff[sid] = []
                strategy_eff[sid].append(float(rating))

                # 症状频率
                for sym in e["body_symptoms"]:
                    symptom_freq[sym] = symptom_freq.get(sym, 0) + 1

        # --- 周统计 ---
        avg_sleep_quality = round(
            sum(sleep_qualities) / len(sleep_qualities), 2
        ) if sleep_qualities else 0.0
        avg_morning_valence = round(
            sum(morning_valences) / len(morning_valences), 4
        ) if morning_valences else 0.0

        # 策略平均效果
        strategy_effectiveness: Dict[str, float] = {}
        for sid, ratings in strategy_eff.items():
            strategy_effectiveness[sid] = round(sum(ratings) / len(ratings), 2)

        weekly_stats = {
            "total_events": total_events,
            "avg_sleep_quality": avg_sleep_quality,
            "avg_morning_valence": avg_morning_valence,
            "trigger_distribution": trigger_dist,
            "strategy_effectiveness": strategy_effectiveness,
            "body_symptoms_frequency": symptom_freq,
        }

        return {
            "archetype": archetype_info,
            "simulation_period": sim_period,
            "daily_data": daily_data,
            "weekly_stats": weekly_stats,
        }


# ================================================================
# main() — 入口函数
# ================================================================

def main():
    """生成所有画像的 7 天数据集。"""
    start_date = datetime(2026, 7, 1)  # 2026年7月1日（周三）

    for archetype in ARCHETYPES:
        gen = DatasetGenerator(archetype, seed=42)
        dataset = gen.generate_7day_dataset(start_date)

        filename = f"virtual_user_{archetype.name_en}_7days.json"
        filepath = os.path.join(OUTPUT_DIR, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)

        # 打印摘要
        stats = dataset["weekly_stats"]
        print(
            f"[OK] {archetype.name} ({archetype.name_en}) -> {filepath}\n"
            f"     总事件数: {stats['total_events']}, "
            f"平均睡眠质量: {stats['avg_sleep_quality']}, "
            f"平均早晨效价: {stats['avg_morning_valence']}\n"
            f"     触发器分布: {stats['trigger_distribution']}\n"
            f"     策略效果: {stats['strategy_effectiveness']}\n"
            f"     躯体症状: {stats['body_symptoms_frequency']}\n"
        )

    print(f"\n所有数据集已生成至: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
