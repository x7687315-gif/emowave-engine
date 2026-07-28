"""
simulator.py — 心潮 EmoWave 实时情绪系统 · 模拟环境

本模块提供完整的模拟环境，用于演示和验证卡尔曼滤波器 + 预警引擎。

模拟内容：
  1. 用户滑条轨迹生成器
     - 模拟用户在效价-唤醒平面上的拖拽行为
     - 包含"平静→快速上升→峰值→缓慢恢复"的典型情绪事件
     - 模拟真实交互噪声（抖动、停顿、跳变）

  2. 生理信号生成器
     - 模拟心率、HRV 随情绪状态变化
     - 包含传感器噪声和偶发的信号丢失

  3. 场景编排
     - 内置多个预定义场景（正常波动、快速极点、缓慢攀升）
     - 方便一键运行不同测试场景
"""

import numpy as np
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Generator

from kalman_filter import (
    SliderObservation,
    PhysioInput,
    EmotionState,
)


# ================================================================
# 用户滑条轨迹生成器
# ================================================================

@dataclass
class TrajectoryPoint:
    """轨迹中的一个点：真实状态 + 带噪声的观测。"""
    t: float             # 时间（秒）
    true_valence: float   # 真实效价
    true_arousal: float   # 真实唤醒
    obs_valence: float    # 带噪声的观测效价
    obs_arousal: float    # 带噪声的观测唤醒
    touch_velocity: float  # 触摸速度
    stillness: float       # 距上次触摸间隔


def generate_emotion_trajectory(
    duration_sec: float = 300.0,
    dt: float = 1.0,
    start_valence: float = 0.6,
    start_arousal: float = 0.25,
    peak_arousal: float = 0.90,
    peak_valence: float = 0.10,
    peak_time_fraction: float = 0.4,
    recovery_speed: float = 0.7,
    noise_std: float = 0.05,
    jump_probability: float = 0.02,
    stillness_probability: float = 0.05,
    seed: Optional[int] = None,
) -> List[TrajectoryPoint]:
    """
    生成一条模拟的用户拖拽轨迹。

    轨迹形状：平静 → 快速上升 → 峰值平台 → 缓慢恢复

    参数说明：
      duration_sec: 总时长（秒）
      peak_time_fraction: 峰值出现在总时长的百分比位置
      recovery_speed: 恢复速度（0-1，越大恢复越快）
      noise_std: 基础观测噪声
      jump_probability: 每个采样点出现"跳变噪声"的概率
      stillness_probability: 每个采样点出现"停顿后跳变"的概率
    """
    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random.Random()

    n_steps = int(duration_sec / dt)
    points = []
    last_touch_t = 0.0

    for i in range(n_steps):
        t = i * dt
        progress = i / n_steps  # 0 → 1

        # --- 真实轨迹（钟形曲线 + 非对称恢复） ---
        peak_t = peak_time_fraction

        if progress < peak_t:
            # 上升段：S 型曲线
            x = progress / peak_t
            rise = 1.0 / (1.0 + np.exp(-8 * (x - 0.5)))  # Sigmoid
            arousal = start_arousal + (peak_arousal - start_arousal) * rise
            valence = start_valence + (peak_valence - start_valence) * rise
        else:
            # 恢复段：指数衰减
            x = (progress - peak_t) / (1.0 - peak_t)
            decay = np.exp(-3.0 * x * recovery_speed)
            arousal = peak_arousal * decay + start_arousal * (1 - decay)
            valence = peak_valence * decay + start_valence * (1 - decay)

        valence = max(0, min(1, valence))
        arousal = max(0, min(1, arousal))

        # --- 计算触摸速度 ---
        if i > 0:
            dv = abs(valence - points[-1].true_valence)
            da = abs(arousal - points[-1].true_arousal)
            touch_vel = (dv + da) / dt
        else:
            touch_vel = 0.0

        # --- 停顿间隔 ---
        if rng.random() < stillness_probability:
            # 模拟偶尔的停顿
            stillness = rng.uniform(2.0, 5.0)
        else:
            stillness = dt

        # --- 观测噪声 ---
        if rng.random() < jump_probability:
            # 偶尔的大跳变（模拟误触或延迟补录）
            obs_v = valence + rng.gauss(0, noise_std * 4)
            obs_a = arousal + rng.gauss(0, noise_std * 4)
        else:
            obs_v = valence + rng.gauss(0, noise_std)
            obs_a = arousal + rng.gauss(0, noise_std)

        obs_v = max(0, min(1, obs_v))
        obs_a = max(0, min(1, obs_a))

        points.append(TrajectoryPoint(
            t=t,
            true_valence=round(valence, 4),
            true_arousal=round(arousal, 4),
            obs_valence=round(obs_v, 4),
            obs_arousal=round(obs_a, 4),
            touch_velocity=round(touch_vel, 4),
            stillness=stillness,
        ))

        last_touch_t = t

    return points


def trajectory_to_observations(points: List[TrajectoryPoint]) -> List[SliderObservation]:
    """将轨迹点转换为滑条观测列表。"""
    return [
        SliderObservation(
            timestamp=p.t,
            valence=p.obs_valence,
            arousal=p.obs_arousal,
            touch_velocity=p.touch_velocity,
            seconds_since_last_touch=p.stillness,
        )
        for p in points
    ]


# ================================================================
# 生理信号生成器
# ================================================================

def generate_physio_signals(
    true_arousals: List[float],
    base_hr: float = 72.0,
    base_hrv: float = 50.0,
    hr_sensitivity: float = 40.0,    # 唤醒度从 0→1 时心率增加的 BPM
    hrv_sensitivity: float = 0.4,    # 唤醒度从 0→1 时 HRV 下降的比例
    noise_std_hr: float = 2.0,
    noise_std_hrv: float = 3.0,
    dropout_probability: float = 0.05,  # 信号丢失概率
    rssi_mean: float = -55.0,
    seed: Optional[int] = None,
) -> List[Optional[PhysioInput]]:
    """
    根据真实唤醒度序列生成模拟的生理信号。

    模型：
      HR = base_hr + hr_sensitivity * arousal + noise
      HRV = base_hrv * (1 - hrv_sensitivity * arousal) + noise
      signal_quality = f(rssi, random_drop)
    """
    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random.Random()

    signals = []
    for i, arousal in enumerate(true_arousals):
        # 偶尔信号丢失
        if rng.random() < dropout_probability:
            signals.append(None)
            continue

        hr = base_hr + hr_sensitivity * arousal + rng.gauss(0, noise_std_hr)
        hrv_ratio = 1.0 - hrv_sensitivity * arousal
        hrv_drop = max(0, 1.0 - hrv_ratio)
        hrv = base_hrv * hrv_ratio + rng.gauss(0, noise_std_hrv)

        rssi = rssi_mean + rng.gauss(0, 8)
        quality = 1.0 if rssi > -60 else max(0.2, 1.0 + (rssi + 60) / 40)

        signals.append(PhysioInput(
            timestamp=i * 1.0,
            hrv_drop_ratio=round(hrv_drop, 3),
            hr_change=round(hr - base_hr, 1),
            signal_quality=round(max(0, min(1, quality)), 2),
        ))

    return signals


# ================================================================
# 预定义场景
# ================================================================

class Scenario:
    """预定义的情绪事件场景。"""

    @staticmethod
    def normal_fluctuation():
        """正常日常波动：小幅起伏，不会触发预警。"""
        return generate_emotion_trajectory(
            duration_sec=300,
            peak_arousal=0.45,
            peak_valence=0.40,
            peak_time_fraction=0.3,
            recovery_speed=0.8,
            noise_std=0.04,
            seed=42,
        )

    @staticmethod
    def rapid_peak():
        """快速极点：30 秒内急剧上升到高位，然后缓慢恢复。"""
        return generate_emotion_trajectory(
            duration_sec=300,
            start_valence=0.6,
            start_arousal=0.25,
            peak_arousal=0.92,
            peak_valence=0.08,
            peak_time_fraction=0.15,
            recovery_speed=0.5,
            noise_std=0.06,
            jump_probability=0.03,
            stillness_probability=0.04,
            seed=123,
        )

    @staticmethod
    def slow_climb():
        """缓慢攀升：在 2-3 分钟内逐渐攀升到危险区。"""
        return generate_emotion_trajectory(
            duration_sec=300,
            start_valence=0.5,
            start_arousal=0.30,
            peak_arousal=0.88,
            peak_valence=0.12,
            peak_time_fraction=0.6,
            recovery_speed=0.4,
            noise_std=0.03,
            seed=456,
        )

    @staticmethod
    def multi_peak():
        """多峰事件：连续两次情绪波动，第一次较轻第二次更重。"""
        # 生成两段轨迹并拼接
        p1 = generate_emotion_trajectory(
            duration_sec=120,
            peak_arousal=0.65,
            peak_valence=0.25,
            peak_time_fraction=0.5,
            recovery_speed=0.9,
            noise_std=0.04,
            seed=789,
        )
        p2 = generate_emotion_trajectory(
            duration_sec=180,
            start_valence=0.5,
            start_arousal=0.35,
            peak_arousal=0.90,
            peak_valence=0.10,
            peak_time_fraction=0.35,
            recovery_speed=0.5,
            noise_std=0.05,
            jump_probability=0.03,
            seed=101,
        )
        # 调整第二段时间偏移
        offset = p1[-1].t + 1.0
        for p in p2:
            p.t += offset
        return p1 + p2
