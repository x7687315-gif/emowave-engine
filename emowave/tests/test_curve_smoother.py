"""RTS 平滑器单元测试。

重点验证 ARCHITECTURE part1 §3.2 L2 回顾层：
  - RTS 是非因果的：平滑用到未来观测，RMSE 必然优于因果滤波
  - O(N) 两遍算法正确性
  - 平滑后验方差 ≤ 滤波后验方差（未来信息减少不确定性）
  - 用户编辑作为加权伪观察注入，高权重强烈采纳、低权重温和采纳
  - 原始观察不可变（编辑不覆盖 raw）
  - 不发散（注入含噪声/偏差的合成编辑，曲线仍稳定）
"""

import math

import pytest

from emowave.core.curve.smoother import RTSSmoother, SmoothedTrajectory
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource
from emowave.core.estimator.estimator import EstimatorConfig, StateEstimator


def make_obs_series(n, start_ts=1000.0, v_fn=None, a_fn=None, noise=0.0, seed=0):
    """生成一段观察序列。v_fn/a_fn: i -> [0,1]。"""
    import random
    rng = random.Random(seed)
    obs = []
    for i in range(n):
        v = v_fn(i) if v_fn else 0.5
        a = a_fn(i) if a_fn else 0.5
        if noise > 0:
            v = min(1.0, max(0.0, v + rng.gauss(0, noise)))
            a = min(1.0, max(0.0, a + rng.gauss(0, noise)))
        obs.append(
            Observation(timestamp=start_ts + i, valence=v, arousal=a,
                        source=ObservationSource.USER)
        )
    return obs


# ============================================================
# 基本平滑
# ============================================================


def test_smooth_returns_trajectory_of_same_length():
    obs = make_obs_series(20, v_fn=lambda i: 0.5)
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)
    assert isinstance(traj, SmoothedTrajectory)
    assert len(traj) == 20
    assert len(traj.mean_valence) == 20
    assert len(traj.var_valence) == 20


def test_smooth_empty_returns_empty():
    smoother = RTSSmoother()
    traj = smoother.smooth([])
    assert len(traj) == 0


def test_smooth_single_observation():
    obs = make_obs_series(1, v_fn=lambda i: 0.7)
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)
    assert len(traj) == 1
    assert traj.mean_valence[0] == pytest.approx(0.7, abs=0.05)


def test_smooth_constant_signal_converges():
    """恒定信号平滑后应收敛到该常数值。"""
    obs = make_obs_series(30, v_fn=lambda i: 0.75, a_fn=lambda i: 0.35)
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)
    # 中段应非常接近真值
    mid = len(traj) // 2
    assert traj.mean_valence[mid] == pytest.approx(0.75, abs=0.02)
    assert traj.mean_arousal[mid] == pytest.approx(0.35, abs=0.02)


def test_smooth_output_in_unit_interval():
    obs = make_obs_series(30, v_fn=lambda i: 0.5, noise=0.2, seed=1)
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)
    for v in traj.mean_valence:
        assert 0.0 <= v <= 1.0
    for a in traj.mean_arousal:
        assert 0.0 <= a <= 1.0


def test_smooth_handles_unsorted_input():
    """乱序输入应被内部排序（防御性）。"""
    obs = make_obs_series(10, v_fn=lambda i: 0.5 + 0.01 * i)
    shuffled = [obs[5], obs[2], obs[8], obs[0], obs[9], obs[1], obs[7], obs[3], obs[6], obs[4]]
    smoother = RTSSmoother()
    traj = smoother.smooth(shuffled)
    assert len(traj) == 10
    # 时间戳应已排序
    assert traj.timestamps == sorted(traj.timestamps)


# ============================================================
# 非因果性：平滑优于滤波（part1 §3.2 核心卖点）
# ============================================================


def test_smoothing_beats_filtering_rmse():
    """平滑利用未来信息，RMSE 必然优于因果滤波（part1 §8 阶段2 验证）。

    构造与模型时间尺度匹配的慢变真值（周期 400s 的正弦 + 缓慢漂移，
    ℓ=300s 可追踪），加白噪声。这是 RTS 平滑（用过去+未来平均掉噪声）
    显著优于因果滤波（只用过去）的典型场景。
    """
    import random
    rng = random.Random(42)
    n = 300
    # 真值：慢正弦（周期 400s，与 ℓ=300s 同尺度）+ 缓慢漂移
    truth = [
        _clip01(0.5 + 0.25 * math.sin(2 * math.pi * i / 400.0) + 0.1 * (i / n))
        for i in range(n)
    ]
    # 带噪声观测
    obs = []
    for i in range(n):
        noisy = min(1.0, max(0.0, truth[i] + rng.gauss(0, 0.12)))
        obs.append(Observation(timestamp=1000.0 + i, valence=noisy, arousal=0.5))

    # RTS 平滑
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)

    # 因果滤波（对照）
    est = StateEstimator(config=EstimatorConfig())
    est.initialize(timestamp=1000.0, valence=obs[0].valence, arousal=0.5)
    filt = []
    for o in obs:
        st = est.update(o)
        filt.append(st.valence)

    # RMSE（避开首尾各 10 点的边界效应）
    lo, hi = 10, n - 10

    def rmse(preds):
        return math.sqrt(
            sum((preds[i] - truth[i]) ** 2 for i in range(lo, hi)) / (hi - lo)
        )

    rmse_smooth = rmse(traj.mean_valence)
    rmse_filt = rmse(filt)
    # 平滑应优于（或至少不劣于）滤波
    assert rmse_smooth <= rmse_filt + 1e-6


def _clip01(x):
    return min(1.0, max(0.0, x))


def test_smoothed_variance_le_filtered_variance():
    """平滑后验方差应 ≤ 滤波后验方差（未来信息减少不确定性）。"""
    obs = make_obs_series(40, v_fn=lambda i: 0.5 + 0.005 * i, noise=0.08, seed=3)
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)

    # 因果滤波的方差
    est = StateEstimator()
    est.initialize(timestamp=obs[0].timestamp, valence=0.5, arousal=0.5)
    filt_vars = []
    for o in obs:
        st = est.update(o)
        filt_vars.append(st.variance_valence)

    # 中段比较（避开边界效应）
    mid = len(traj) // 2
    assert traj.var_valence[mid] <= filt_vars[mid] + 1e-9


# ============================================================
# 用户编辑作为加权伪观察（part1 §4.2 峰终加权）
# ============================================================


def test_high_weight_edit_strongly_adopted():
    """高可靠性权重的编辑应被强烈采纳（曲线明显向编辑值移动）。"""
    obs = make_obs_series(30, v_fn=lambda i: 0.4)
    smoother = RTSSmoother()
    base = smoother.smooth(obs)
    base_mid = base.mean_valence[15]

    # 高权重编辑：把中点拖到 0.8
    edits_high = [{"timestamp": 1015.0, "channel": "valence", "value": 0.8,
                   "reliability_weight": 1.0}]
    traj_high = smoother.smooth(obs, edits=edits_high)
    # 找到最接近 1015 的索引
    idx = min(range(len(traj_high)), key=lambda i: abs(traj_high.timestamps[i] - 1015.0))
    assert traj_high.mean_valence[idx] > base_mid + 0.15  # 明显上移


def test_low_weight_edit_weakly_adopted():
    """低可靠性权重的编辑只被温和采纳（曲线移动幅度小）。"""
    obs = make_obs_series(30, v_fn=lambda i: 0.4)
    smoother = RTSSmoother()
    base = smoother.smooth(obs)
    base_mid = base.mean_valence[15]

    edits_low = [{"timestamp": 1015.0, "channel": "valence", "value": 0.8,
                  "reliability_weight": 0.05}]
    traj_low = smoother.smooth(obs, edits=edits_low)
    idx = min(range(len(traj_low)), key=lambda i: abs(traj_low.timestamps[i] - 1015.0))
    # 低权重：移动幅度应小于高权重情况
    assert traj_low.mean_valence[idx] < base_mid + 0.15


def test_edit_weight_monotonic_effect():
    """编辑权重越高，曲线向编辑值移动越多（单调性）。"""
    obs = make_obs_series(30, v_fn=lambda i: 0.4)
    smoother = RTSSmoother()
    moves = []
    for w in [0.1, 0.4, 0.7, 1.0]:
        edits = [{"timestamp": 1015.0, "channel": "valence", "value": 0.85,
                  "reliability_weight": w}]
        traj = smoother.smooth(obs, edits=edits)
        idx = min(range(len(traj)), key=lambda i: abs(traj.timestamps[i] - 1015.0))
        moves.append(traj.mean_valence[idx])
    # 单调不降
    for i in range(len(moves) - 1):
        assert moves[i] <= moves[i + 1] + 1e-6


def test_edit_does_not_mutate_raw_observations():
    """编辑不覆盖原始观察（part1 §3.2.1 原始数据不可变）。"""
    obs = make_obs_series(20, v_fn=lambda i: 0.4)
    raw_vals_before = [o.valence for o in obs]
    smoother = RTSSmoother()
    edits = [{"timestamp": 1010.0, "channel": "valence", "value": 0.9,
              "reliability_weight": 1.0}]
    smoother.smooth(obs, edits=edits)
    # 原始观察值不变
    assert [o.valence for o in obs] == raw_vals_before


def test_multiple_edits_all_applied():
    """多个编辑应同时生效。"""
    obs = make_obs_series(40, v_fn=lambda i: 0.4)
    smoother = RTSSmoother()
    edits = [
        {"timestamp": 1010.0, "channel": "valence", "value": 0.8, "reliability_weight": 1.0},
        {"timestamp": 1030.0, "channel": "valence", "value": 0.8, "reliability_weight": 1.0},
    ]
    traj = smoother.smooth(obs, edits=edits)
    idx10 = min(range(len(traj)), key=lambda i: abs(traj.timestamps[i] - 1010.0))
    idx30 = min(range(len(traj)), key=lambda i: abs(traj.timestamps[i] - 1030.0))
    assert traj.mean_valence[idx10] > 0.55
    assert traj.mean_valence[idx30] > 0.55


# ============================================================
# 不发散（part1 §8 阶段3 验证：注入含噪声/偏差的合成编辑，模型不发散）
# ============================================================


def test_extreme_edits_do_not_diverge():
    """极端编辑（拖到边界 + 高权重）不应使曲线发散或越界。"""
    obs = make_obs_series(30, v_fn=lambda i: 0.5)
    smoother = RTSSmoother()
    edits = [{"timestamp": 1000.0 + i, "channel": "valence",
              "value": 1.0 if i % 2 == 0 else 0.0, "reliability_weight": 1.0}
             for i in range(30)]
    traj = smoother.smooth(obs, edits=edits)
    for v in traj.mean_valence:
        assert 0.0 <= v <= 1.0
        assert not math.isnan(v)
        assert not math.isinf(v)


def test_biased_edits_stay_bounded():
    """系统性偏差编辑（全部往上拖）应被采纳但有界，不爆炸。"""
    obs = make_obs_series(30, v_fn=lambda i: 0.3)
    smoother = RTSSmoother()
    edits = [{"timestamp": 1000.0 + i, "channel": "valence", "value": 0.95,
              "reliability_weight": 0.9} for i in range(30)]
    traj = smoother.smooth(obs, edits=edits)
    # 应向上移动但不超过编辑值太多（有界）
    assert max(traj.mean_valence) <= 1.0
    assert traj.mean_valence[15] > 0.5  # 确实被拉高
    assert traj.mean_valence[15] < 0.99  # 但有界，不会爆到 1.0 以上


def test_arousal_channel_edit():
    """arousal 通道的编辑应只影响 arousal，不影响 valence。"""
    obs = make_obs_series(30, v_fn=lambda i: 0.5, a_fn=lambda i: 0.3)
    smoother = RTSSmoother()
    edits = [{"timestamp": 1015.0, "channel": "arousal", "value": 0.85,
              "reliability_weight": 1.0}]
    traj = smoother.smooth(obs, edits=edits)
    idx = min(range(len(traj)), key=lambda i: abs(traj.timestamps[i] - 1015.0))
    assert traj.mean_arousal[idx] > 0.5  # arousal 被拉高
    assert traj.mean_valence[idx] == pytest.approx(0.5, abs=0.05)  # valence 基本不变


# ============================================================
# 部分观察 / 纯生理观察
# ============================================================


def test_smooth_partial_observations():
    """只有 valence 的观察序列也能平滑。"""
    obs = [Observation(timestamp=1000.0 + i, valence=0.6, arousal=None)
           for i in range(20)]
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)
    assert len(traj) == 20
    assert traj.mean_valence[10] == pytest.approx(0.6, abs=0.05)


def test_smooth_pure_physio_does_not_crash():
    """纯生理观察（无情绪通道）只走预测，不崩溃。"""
    obs = [Observation(timestamp=1000.0 + i, hr=80.0, hrv=40.0,
                       source=ObservationSource.SENSOR) for i in range(10)]
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)
    assert len(traj) == 10
    for v in traj.mean_valence:
        assert not math.isnan(v)


# ============================================================
# 性能（part2 §2.4：节点网格降 N 使纯 Python RTS 可接受）
# ============================================================


def test_smooth_2000_points_completes():
    """N=2000 的纯 Python RTS 应能在合理时间完成（part2 §2.1 实测 ~172ms）。"""
    import time
    obs = make_obs_series(2000, v_fn=lambda i: 0.5 + 0.2 * math.sin(i / 100.0))
    smoother = RTSSmoother()
    t0 = time.time()
    traj = smoother.smooth(obs)
    elapsed = time.time() - t0
    assert len(traj) == 2000
    # 宽松上限：纯 Python 在慢机器上也可能到几百 ms，但不应到秒级
    assert elapsed < 3.0, f"RTS N=2000 耗时 {elapsed:.2f}s 过长"
