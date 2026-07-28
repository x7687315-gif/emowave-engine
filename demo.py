"""
demo.py — 心潮 EmoWave 实时情绪状态估计与极点预警 · Demo

本脚本运行完整的模拟演示：
  1. 生成模拟的用户拖拽轨迹和生理信号
  2. 逐帧通过卡尔曼滤波器，输出平滑的情绪强度轨迹
  3. 预警引擎实时检查是否需要发出预警
  4. 输出文本格式的实时日志和摘要报告

运行方式：
  cd /workspace/emowave-engine && python3 demo.py
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import numpy as np
from typing import List, Tuple

from kalman_filter import (
    EmotionKalmanFilter,
    KalmanConfig,
    SliderObservation,
    PhysioInput,
    EmotionState,
)
from predictor import (
    PredictionEngine,
    PredictionConfig,
    PredictionResult,
    WarningLevel,
)
from simulator import (
    TrajectoryPoint,
    Scenario,
    trajectory_to_observations,
    generate_physio_signals,
)
from models import PersonalThresholds, ModelSource


# ================================================================
# 配色方案（终端输出）
# ================================================================
C_RESET = "\033[0m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_CYAN = "\033[36m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"


def warning_color(level: WarningLevel) -> str:
    if level == WarningLevel.CRITICAL:
        return C_RED
    elif level == WarningLevel.WARNING:
        return C_YELLOW
    elif level == WarningLevel.WATCH:
        return C_CYAN
    return C_GREEN


# ================================================================
# 阈值设置（使用群体阈值模拟冷启动场景）
# ================================================================

POPULATION_THRESHOLDS = PersonalThresholds(
    high_risk_arousal=0.85,
    high_risk_valence=0.15,
    hrv_drop_percent=0.30,
    hr_surge_zscore=2.5,
    dangerous_rise_slope=0.012,
    model_source=ModelSource.POPULATION,
    model_confidence=0.0,
    event_count=0,
)


# ================================================================
# 运行单场景
# ================================================================

def run_scenario(
    scenario_name: str,
    points: List[TrajectoryPoint],
    with_physio: bool = True,
    show_every_n: int = 15,
) -> dict:
    """
    运行一个场景的完整模拟。

    Args:
        scenario_name: 场景名称
        points: 模拟轨迹点
        with_physio: 是否生成生理信号
        show_every_n: 每隔多少帧打印一行日志

    Returns:
        摘要统计 dict
    """
    print(f"\n{'=' * 70}")
    print(f"  {C_BOLD}场景: {scenario_name}{C_RESET}")
    print(f"  总时长: {points[-1].t:.0f}s  |  采样点: {len(points)}  |  生理信号: {'✓' if with_physio else '✗'}")
    print(f"{'=' * 70}")

    # 初始化滤波器和预警引擎
    kf = EmotionKalmanFilter(KalmanConfig())
    kf.init(valence=points[0].true_valence, arousal=points[0].true_arousal)

    predictor = PredictionEngine(PredictionConfig())

    observations = trajectory_to_observations(points)

    # 生成生理信号
    true_arousals = [p.true_arousal for p in points]
    physio_signals = generate_physio_signals(
        true_arousals,
        base_hr=72.0,
        base_hrv=50.0,
        seed=42,
    ) if with_physio else [None] * len(points)

    # 收集结果
    filtered_states: List[EmotionState] = []
    raw_intensities = []
    filtered_intensities = []
    warnings = []
    peak_intensity = 0.0
    peak_time = 0.0
    first_warning_time = None

    # 逐帧处理
    for i, (obs, pt) in enumerate(zip(observations, points)):
        physio = physio_signals[i] if i < len(physio_signals) else None

        # 更新滤波器
        if physio is not None:
            state = kf.update_with_control(obs, physio)
        else:
            state = kf.update(obs)

        filtered_states.append(state)

        # 计算原始强度
        raw_intensity = min(1.0, np.sqrt(pt.obs_valence ** 2 + pt.obs_arousal ** 2) / np.sqrt(2))
        raw_intensities.append(raw_intensity)
        filtered_intensities.append(state.intensity)

        # 预警检查（每 10 帧检查一次，跳过前 30 帧的初始化预热期）
        latest_result = None
        if i >= 30 and i % 10 == 0:
            latest_result = predictor.predict(kf, POPULATION_THRESHOLDS, current_time=pt.t)
            if latest_result.warning_level != WarningLevel.NONE:
                # 去重：只在级别变化或首次出现时记录
                if (not warnings) or (warnings[-1][1].warning_level != latest_result.warning_level):
                    warnings.append((pt.t, latest_result))
                if first_warning_time is None and latest_result.warning_level in (WarningLevel.WARNING, WarningLevel.CRITICAL):
                    first_warning_time = pt.t

        # 记录峰值
        true_intensity = min(1.0, np.sqrt(pt.true_valence ** 2 + pt.true_arousal ** 2) / np.sqrt(2))
        if true_intensity > peak_intensity:
            peak_intensity = true_intensity
            peak_time = pt.t

        # 打印日志
        if i % show_every_n == 0:
            wl = latest_result.warning_level if latest_result else predictor.last_warning_level
            wc = warning_color(wl)
            wl_str = f"{wc}{wl.value:>8s}{C_RESET}"
            bar_raw = _intensity_bar(raw_intensity, 20)
            bar_filt = _intensity_bar(state.intensity, 20)
            print(
                f"  t={pt.t:5.0f}s  "
                f"raw=[{pt.obs_valence:.2f},{pt.obs_arousal:.2f}] "
                f"filt=[{state.valence:.2f},{state.arousal:.2f}]  "
                f"raw_I={bar_raw}  filt_I={bar_filt}  "
                f"{wl_str}"
            )

    # --- 摘要报告 ---
    # 找到真实峰值时间
    true_peak_i = max(range(len(points)), key=lambda i: np.sqrt(
        points[i].true_valence ** 2 + points[i].true_arousal ** 2
    ))
    true_peak_t = points[true_peak_i].t

    # 计算平滑误差
    errors = []
    for i, (state, pt) in enumerate(zip(filtered_states, points)):
        e_v = (state.valence - pt.true_valence) ** 2
        e_a = (state.arousal - pt.true_arousal) ** 2
        errors.append(np.sqrt(e_v + e_a))
    rmse = np.sqrt(np.mean(np.array(errors) ** 2))
    mae = np.mean(errors)

    # 预警统计（排除前 15% 的预热期误报）
    warmup_cutoff = points[-1].t * 0.15
    n_warnings_total = len(warnings)
    n_warnings = sum(1 for t, r in warnings if t > warmup_cutoff)
    n_critical = sum(1 for t, r in warnings if r.warning_level == WarningLevel.CRITICAL and t > warmup_cutoff)
    n_warning_level = sum(1 for t, r in warnings if r.warning_level == WarningLevel.WARNING and t > warmup_cutoff)
    n_watch = sum(1 for t, r in warnings if r.warning_level == WarningLevel.WATCH and t > warmup_cutoff)

    # 预警提前量
    lead_time = None
    if first_warning_time is not None and true_peak_t > first_warning_time:
        lead_time = true_peak_t - first_warning_time

    print(f"\n  {C_BOLD}--- 摘要报告 ---{C_RESET}")
    print(f"  真实峰值时间: {true_peak_t:.0f}s")
    print(f"  真实峰值强度: {peak_intensity:.3f}")
    print(f"  滤波器 RMSE:  {rmse:.4f}")
    print(f"  滤波器 MAE:   {mae:.4f}")
    print(f"  预警总次数:   {n_warnings} (CRITICAL={n_critical}, WARNING={n_warning_level}, WATCH={n_watch})")
    if lead_time is not None:
        print(f"  首次预警时间: {first_warning_time:.0f}s")
        print(f"  预警提前量:   {lead_time:.0f}s {'✓ 合理' if 30 <= lead_time <= 180 else '⚠ 偏离理想范围'}")
    else:
        print(f"  未触发预警")

    # 预警详情
    if warnings:
        print(f"\n  {C_DIM}预警详情:{C_RESET}")
        for t, r in warnings[:10]:  # 最多显示 10 条
            wc = warning_color(r.warning_level)
            print(f"    t={t:.0f}s  {wc}{r.warning_level.value}{C_RESET}  {r.reason}")
        if len(warnings) > 10:
            print(f"    ... 还有 {len(warnings) - 10} 条预警")

    return {
        "scenario": scenario_name,
        "peak_time": true_peak_t,
        "rmse": rmse,
        "mae": mae,
        "n_warnings": n_warnings,
        "lead_time": lead_time,
    }


def _intensity_bar(value: float, width: int) -> str:
    """生成强度条的可视化字符串。"""
    filled = int(value * width)
    empty = width - filled
    if value > 0.75:
        color = C_RED
    elif value > 0.6:
        color = C_YELLOW
    else:
        color = C_GREEN
    return f"{color}{'█' * filled}{'░' * empty}{C_RESET} {value:.2f}"


# ================================================================
# 主入口
# ================================================================

def main():
    print(f"\n{C_BOLD}心潮 EmoWave — 实时情绪状态估计与极点预警 · Demo{C_RESET}")
    print(f"卡尔曼滤波器 + 自适应噪声 + 极点提前预警")
    print(f"{'─' * 70}")

    scenarios = [
        ("正常日常波动", Scenario.normal_fluctuation()),
        ("快速极点事件", Scenario.rapid_peak()),
        ("缓慢攀升事件", Scenario.slow_climb()),
        ("多峰事件", Scenario.multi_peak()),
    ]

    results = []
    for name, points in scenarios:
        result = run_scenario(
            scenario_name=name,
            points=points,
            with_physio=True,
            show_every_n=15,
        )
        results.append(result)

    # 总评
    print(f"\n{'=' * 70}")
    print(f"  {C_BOLD}总体评估{C_RESET}")
    print(f"{'=' * 70}")
    print(f"  {'场景':<14s} {'RMSE':>8s} {'预警次数':>10s} {'提前量':>10s} {'评价':>10s}")
    print(f"  {'─' * 14} {'─' * 8} {'─' * 10} {'─' * 10} {'─' * 10}")

    for r in results:
        lt_str = f"{r['lead_time']:.0f}s" if r['lead_time'] else "N/A"
        if r['scenario'] == "正常日常波动":
            verdict = "✓ 无误报" if r['n_warnings'] == 0 else "⚠ 有误报"
        else:
            if r['lead_time'] and 20 <= r['lead_time'] <= 200:
                verdict = "✓ 成功预警"
            elif r['lead_time']:
                verdict = "⚠ 提前量偏离"
            else:
                verdict = "✗ 漏报"
        print(f"  {r['scenario']:<14s} {r['rmse']:>8.4f} {r['n_warnings']:>10d} {lt_str:>10s} {verdict:>10s}")

    print()
    print(f"  {C_DIM}滤波器说明:{C_RESET}")
    print(f"  {C_DIM}  状态向量: [valence, arousal, d_valence, d_arousal]{C_RESET}")
    print(f"  {C_DIM}  观测噪声: 自适应（快速移动时降低，静止跳变时增大）{C_RESET}")
    print(f"  {C_DIM}  生理输入: HRV下降 + 心率变化 → 唤醒速度先验{C_RESET}")
    print(f"  {C_DIM}  预警外推: 线性外推 10 分钟，检查是否进入危险区{C_RESET}")


if __name__ == "__main__":
    main()
