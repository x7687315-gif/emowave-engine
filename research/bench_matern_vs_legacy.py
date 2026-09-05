"""bench_matern_vs_legacy — 新 Matérn 状态空间估计器 vs 旧版 Kalman 的 RMSE 对照。

ARCHITECTURE part1 §8 阶段 1 的验证要求：
    "新参数化在现有 test_data/ 上 RMSE 不劣于旧实现"

本脚本用合成数据做公平对照，重点检验 part1 §1.2 指出的结构性弱点：
旧版 velocity_damping=0.85 隐含 ℓ≈11-22s，模型认为 60 秒前的情绪与现在
无关，因此在**观测缺失（gap）**期间无法维持状态，外推发散。
新版 ℓ=300s 修正了这一"记忆问题"。

方法：
  1. 生成 Matérn ν=3/2 平滑轨迹作为 ground truth（ℓ=300s，1Hz，600s）
  2. 加高斯观测噪声（σ=0.08），并随机挖掉 30% 的观测制造 gap
  3. 旧版 EmotionKalmanFilter（numpy）与新版 StateEstimator（纯 Python）
     在**完全相同**的观测序列上运行
  4. 对照 RMSE：全局 + 仅 gap 区域（记忆能力的关键考验）

运行：
    python research/bench_matern_vs_legacy.py

预期结论：新版全局 RMSE ≤ 旧版，gap 区域 RMSE 显著优于旧版。
"""

from __future__ import annotations

import math
import os
import random
import sys

# 让脚本可独立运行（把项目根加入 path）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource
from emowave.core.estimator.estimator import EstimatorConfig, StateEstimator

# 旧版（numpy）滤波器，仅用于对照
from kalman_filter import EmotionKalmanFilter, KalmanConfig, SliderObservation


# ============================================================
# 合成 ground truth：Matérn ν=3/2 平滑轨迹
# ============================================================


def generate_matern_trajectory(
    n_steps: int, ell: float, sigma: float, seed: int = 42
) -> list:
    """用状态空间递推生成一条 Matérn ν=3/2 平滑轨迹（作为 ground truth）。

    x_{k+1} = F(Δt)·x_k + w，w ~ N(0, Q)。取位置分量作为真值情绪。
    这保证 ground truth 本身就是"ℓ=ell 的平滑情绪"，公平检验估计器。
    """
    rng = random.Random(seed)
    lam = math.sqrt(3.0) / ell
    dt = 1.0
    decay = math.exp(-lam * dt)
    # F = decay·[[1+λdt, dt], [-λ²dt, 1-λdt]]
    f00 = decay * (1 + lam * dt)
    f01 = decay * dt
    f10 = decay * (-lam * lam * dt)
    f11 = decay * (1 - lam * dt)
    # P∞ = σ²[[1,0],[0,λ²]]，Q = P∞ - F·P∞·Fᵀ（标量近似用 Cholesky 太繁，
    # 这里直接用稳态方差采样过程噪声，保证轨迹平稳）
    q_pos = sigma * sigma * (1 - decay * decay)
    q_pos = max(q_pos, 1e-9)

    pos, vel = 0.5, 0.0
    traj = []
    for _ in range(n_steps):
        # 高斯噪声（Box-Muller）
        u1 = max(rng.random(), 1e-12)
        u2 = rng.random()
        g = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        w = g * math.sqrt(q_pos)
        new_pos = f00 * pos + f01 * vel + w
        new_vel = f10 * pos + f11 * vel
        pos, vel = new_pos, new_vel
        # 裁剪到 [0,1] 作为合法情绪真值
        traj.append(min(1.0, max(0.0, pos)))
    return traj


def make_observations(
    traj: list, noise_sigma: float, gap_ratio: float, seed: int = 7
) -> tuple:
    """从 ground truth 生成带噪声 + gap 的观测序列。

    Returns:
        (observations, mask)：mask[i]=True 表示第 i 步有观测。
    """
    rng = random.Random(seed)
    obs_list = []
    mask = []
    for i, true_v in enumerate(traj):
        has_obs = rng.random() > gap_ratio
        mask.append(has_obs)
        if has_obs:
            u1 = max(rng.random(), 1e-12)
            u2 = rng.random()
            g = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
            noisy = min(1.0, max(0.0, true_v + g * noise_sigma))
            obs_list.append((i, noisy))
    return obs_list, mask


# ============================================================
# 运行两个估计器
# ============================================================


def run_legacy(obs_list: list, n_steps: int) -> list:
    """旧版 EmotionKalmanFilter（velocity_damping=0.85，手写 F）。

    对每个观测调用 update；gap 期间调用 extrapolate(1s) 维持状态。
    返回每步的 valence 预测。
    """
    kf = EmotionKalmanFilter(KalmanConfig())
    kf.init(valence=0.5, arousal=0.5)
    obs_by_step = {i: v for i, v in obs_list}
    preds = []
    for step in range(n_steps):
        if step in obs_by_step:
            so = SliderObservation(
                timestamp=float(step),
                valence=obs_by_step[step],
                arousal=0.5,
                touch_velocity=0.0,
                seconds_since_last_touch=0.0,
            )
            st = kf.update(so)
        else:
            # gap：外推 1 秒维持状态
            traj = kf.extrapolate(horizon_sec=1.0, dt=1.0)
            st = traj[-1] if traj else kf._to_state(float(step))
        preds.append(st.valence)
    return preds


def run_new(obs_list: list, n_steps: int, ell: float = 300.0) -> list:
    """新版 StateEstimator（Matérn ν=3/2 闭式解，ℓ=300s）。"""
    params = ModelParameters(ell_valence=ell, ell_arousal=240.0)
    est = StateEstimator(params=params, config=EstimatorConfig())
    est.initialize(timestamp=0.0 + 1.0, valence=0.5, arousal=0.5)
    obs_by_step = {i: v for i, v in obs_list}
    preds = []
    last_ts = 1.0
    for step in range(n_steps):
        ts = float(step) + 1.0
        if step in obs_by_step:
            obs = Observation(
                timestamp=ts,
                valence=obs_by_step[step],
                arousal=0.5,
                source=ObservationSource.USER,
            )
            st = est.update(obs)
        else:
            # gap：仅预测（无观测更新），Matérn 惯性维持状态
            est._predict(ts - last_ts)
            st = est._to_state(ts)
        last_ts = ts
        preds.append(st.valence)
    return preds


# ============================================================
# RMSE 计算
# ============================================================


def rmse(preds: list, truth: list, mask: list = None) -> float:
    """均方根误差。mask 非 None 时只统计 mask[i]=True 的位置。"""
    se = []
    for i, (p, t) in enumerate(zip(preds, truth)):
        if mask is not None and not mask[i]:
            continue
        se.append((p - t) ** 2)
    if not se:
        return float("nan")
    return math.sqrt(sum(se) / len(se))


def main():
    print("=" * 70)
    print("Matérn 状态空间估计器 vs 旧版 Kalman — RMSE 对照")
    print("=" * 70)

    n_steps = 600
    noise_sigma = 0.08
    gap_ratio = 0.30
    true_ell = 300.0

    results = []
    for seed in [42, 123, 999, 2024, 7]:
        truth = generate_matern_trajectory(n_steps, ell=true_ell, sigma=0.15, seed=seed)
        obs_list, mask = make_observations(truth, noise_sigma, gap_ratio, seed=seed + 1)
        gap_mask = [not m for m in mask]  # gap 区域

        legacy_preds = run_legacy(obs_list, n_steps)
        new_preds = run_new(obs_list, n_steps, ell=true_ell)

        r_legacy_all = rmse(legacy_preds, truth)
        r_new_all = rmse(new_preds, truth)
        r_legacy_gap = rmse(legacy_preds, truth, gap_mask)
        r_new_gap = rmse(new_preds, truth, gap_mask)

        results.append((r_legacy_all, r_new_all, r_legacy_gap, r_new_gap))
        print(
            f"seed={seed:5d}  全局RMSE  旧={r_legacy_all:.4f} 新={r_new_all:.4f} "
            f"| gap区域RMSE  旧={r_legacy_gap:.4f} 新={r_new_gap:.4f}"
        )

    # 汇总
    n = len(results)
    avg = [sum(r[i] for r in results) / n for i in range(4)]
    print("-" * 70)
    print(
        f"平均        全局RMSE  旧={avg[0]:.4f} 新={avg[1]:.4f} "
        f"| gap区域RMSE  旧={avg[2]:.4f} 新={avg[3]:.4f}"
    )
    print()
    global_ok = avg[1] <= avg[0] + 1e-6
    gap_ok = avg[3] < avg[2]
    print(f"[全局 RMSE 不劣于旧版] {'PASS' if global_ok else 'FAIL'} "
          f"(新 {avg[1]:.4f} vs 旧 {avg[0]:.4f})")
    print(f"[gap 区域 RMSE 优于旧版] {'PASS' if gap_ok else 'FAIL'} "
          f"(新 {avg[3]:.4f} vs 旧 {avg[2]:.4f}, 改善 {(1-avg[3]/avg[2])*100:.1f}%)")
    print()
    if global_ok and gap_ok:
        print("结论：新 Matérn 参数化全局不劣于旧版，且在观测缺失区域显著更优，")
        print("      验证了 part1 §1.2 '记忆问题' 的修复有效。")
    else:
        print("结论：未达预期，需检查参数化或对照方法。")


if __name__ == "__main__":
    main()
