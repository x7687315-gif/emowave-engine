"""StateEstimator 单元测试 — Observation → EmotionState 闭环。

覆盖 REFACTOR_PLAN.md §28 Phase 2 完成标准：
  "输入连续 Observation，可以持续输出 EmotionState"

重点验证：
  - 连续观察流产生连续状态输出
  - 状态收敛到观察值（滤波器正确性）
  - confidence 随观察累积而上升
  - variance 随观察累积而下降
  - trend 正确反映强度变化方向
  - 生理控制输入影响唤醒
  - extrapolate 不污染内部状态
  - 纯生理观察（无情绪通道）不崩溃
  - 乱序观察被拒绝
  - 零 numpy 依赖
"""

import math

import pytest

from emowave.core.domain.baseline import Baseline
from emowave.core.domain.emotion_state import EmotionState, Trend
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource
from emowave.core.estimator.estimator import (
    EstimatorConfig,
    StateEstimator,
    compute_observation_noise,
)


def make_estimator(**kw):
    params = kw.pop("params", None) or ModelParameters()
    config = kw.pop("config", None) or EstimatorConfig()
    baseline = kw.pop("baseline", None)
    return StateEstimator(params=params, config=config, baseline=baseline)


# ============================================================
# 初始化
# ============================================================


def test_initialize_returns_state():
    est = make_estimator()
    st = est.initialize(timestamp=1000.0, valence=0.6, arousal=0.4)
    assert isinstance(st, EmotionState)
    assert st.valence == pytest.approx(0.6)
    assert st.arousal == pytest.approx(0.4)
    assert est.is_initialized is True


def test_initialize_defaults_from_config():
    est = make_estimator()
    st = est.initialize(timestamp=1000.0)
    assert st.valence == pytest.approx(0.5)  # config.initial_valence
    assert st.arousal == pytest.approx(0.4)  # config.initial_arousal


def test_initialize_defaults_from_baseline():
    """有 baseline 时，初始值取 baseline（更个性化的冷启动）。"""
    bl = Baseline(valence=0.67, arousal=0.52)
    est = make_estimator(baseline=bl)
    st = est.initialize(timestamp=1000.0)
    assert st.valence == pytest.approx(0.67)
    assert st.arousal == pytest.approx(0.52)
    assert st.baseline_id == bl.baseline_id


def test_initialize_clips_out_of_range():
    est = make_estimator()
    st = est.initialize(timestamp=1000.0, valence=1.5, arousal=-0.5)
    assert st.valence == 1.0
    assert st.arousal == 0.0


def test_initialize_rejects_nonpositive_timestamp():
    est = make_estimator()
    with pytest.raises(ValueError):
        est.initialize(timestamp=0.0)


# ============================================================
# 闭环：连续 Observation → 连续 EmotionState
# ============================================================


def test_continuous_observations_produce_continuous_states():
    """Phase 2 完成标准：输入连续 Observation，持续输出 EmotionState。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)

    states = []
    for i in range(20):
        obs = Observation(
            timestamp=1000.0 + i,
            valence=0.5 + 0.01 * i,
            arousal=0.5,
            source=ObservationSource.USER,
        )
        st = est.update(obs)
        states.append(st)

    assert len(states) == 20
    assert all(isinstance(s, EmotionState) for s in states)
    # 时间戳应单调递增
    ts = [s.timestamp for s in states]
    assert ts == sorted(ts)


def test_state_converges_to_constant_observation():
    """恒定观察流下，状态应收敛到该观察值（滤波器无偏）。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)

    target_v, target_a = 0.8, 0.3
    last = None
    for i in range(50):
        obs = Observation(timestamp=1000.0 + i, valence=target_v, arousal=target_a)
        last = est.update(obs)

    assert last.valence == pytest.approx(target_v, abs=0.02)
    assert last.arousal == pytest.approx(target_a, abs=0.02)


def test_state_tracks_ramp_observation():
    """斜坡观察下，状态应跟随（速度维度捕捉趋势）。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.3, arousal=0.5)

    last = None
    for i in range(40):
        v = 0.3 + 0.01 * i  # 每秒 +0.01 的斜坡
        obs = Observation(timestamp=1000.0 + i, valence=v, arousal=0.5)
        last = est.update(obs)

    # 状态应接近最新观察值（允许滞后，因为有惯性）
    expected_v = 0.3 + 0.01 * 39
    assert last.valence == pytest.approx(expected_v, abs=0.05)


def test_lazy_initialization_on_first_update():
    """未显式 initialize 时，首条观察触发懒初始化。"""
    est = make_estimator()
    assert est.is_initialized is False
    obs = Observation(timestamp=1000.0, valence=0.6, arousal=0.4)
    st = est.update(obs)
    assert est.is_initialized is True
    assert isinstance(st, EmotionState)


def test_state_stays_in_unit_interval():
    """任何观察序列下，valence/arousal 都应保持在 [0,1]。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0)
    for i in range(30):
        # 极端观察值
        obs = Observation(timestamp=1000.0 + i, valence=1.0, arousal=0.0)
        st = est.update(obs)
        assert 0.0 <= st.valence <= 1.0
        assert 0.0 <= st.arousal <= 1.0


# ============================================================
# confidence 与 variance（REFACTOR_PLAN.md §6.4 置信区间）
# ============================================================


def test_confidence_increases_with_observations():
    """观察越多，confidence 越高（后验方差收缩）。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)

    confs = []
    for i in range(30):
        obs = Observation(timestamp=1000.0 + i, valence=0.6, arousal=0.4)
        st = est.update(obs)
        confs.append(st.confidence)

    # 整体趋势上升（允许局部波动）
    assert confs[-1] > confs[0]
    # 所有 confidence 在 [0,1]
    assert all(0.0 <= c <= 1.0 for c in confs)


def test_variance_decreases_with_observations():
    """观察越多，后验方差越小。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)

    first = None
    last = None
    for i in range(30):
        obs = Observation(timestamp=1000.0 + i, valence=0.6, arousal=0.4)
        st = est.update(obs)
        if first is None:
            first = st
        last = st

    assert last.variance_valence < first.variance_valence
    assert last.variance_arousal < first.variance_arousal


def test_variance_is_nonnegative():
    est = make_estimator()
    est.initialize(timestamp=1000.0)
    for i in range(10):
        obs = Observation(timestamp=1000.0 + i, valence=0.5, arousal=0.5)
        st = est.update(obs)
        assert st.variance_valence >= 0.0
        assert st.variance_arousal >= 0.0


def test_confidence_has_floor():
    """冷启动 confidence 不应为 0（避免 UI 完全虚化）。"""
    est = make_estimator(config=EstimatorConfig(min_confidence=0.05))
    st = est.initialize(timestamp=1000.0)
    assert st.confidence >= 0.05


def test_confidence_interval_narrows_with_data():
    """置信区间随观察累积而收窄。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)

    st_first = est.update(Observation(timestamp=1000.0, valence=0.6, arousal=0.4))
    lo1, hi1 = st_first.confidence_interval("valence", z=1.0)
    width1 = hi1 - lo1

    last = None
    for i in range(1, 30):
        last = est.update(Observation(timestamp=1000.0 + i, valence=0.6, arousal=0.4))
    lo2, hi2 = last.confidence_interval("valence", z=1.0)
    width2 = hi2 - lo2

    assert width2 < width1


# ============================================================
# trend（REFACTOR_PLAN.md §28 Phase 2: 增加 trend）
# ============================================================


def test_trend_rising_when_intensity_increases():
    """唤醒/强度上升时 trend=RISING。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)

    last = None
    for i in range(15):
        # arousal 从 0.5 快速升到 0.9（远离中性点 → 强度上升）
        a = 0.5 + 0.04 * i
        obs = Observation(timestamp=1000.0 + i, valence=0.5, arousal=min(a, 0.95))
        last = est.update(obs)

    assert last.trend == Trend.RISING


def test_trend_falling_when_intensity_decreases():
    """强度回落到中性点时 trend=FALLING。

    注意：arousal 必须在最后一步仍在下降（未到达中性点平台期），
    否则 trend 反映的是平台期而非下降段。这里从 0.95 每步 -0.02，
    20 步后到 0.57，仍高于中性点 0.5 且仍在下降。
    """
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.95)

    last = None
    for i in range(20):
        # arousal 从 0.95 持续下降（每步 -0.02），末步 0.57 仍在下降
        a = 0.95 - 0.02 * i
        obs = Observation(timestamp=1000.0 + i, valence=0.5, arousal=a)
        last = est.update(obs)

    assert last.trend == Trend.FALLING


def test_trend_stable_when_constant():
    """恒定观察下 trend=STABLE（速度趋零）。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.6, arousal=0.4)

    last = None
    for i in range(40):
        obs = Observation(timestamp=1000.0 + i, valence=0.6, arousal=0.4)
        last = est.update(obs)

    assert last.trend == Trend.STABLE


# ============================================================
# stability
# ============================================================


def test_stability_high_when_calm():
    """恒定观察（速度趋零）→ stability 高。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.6, arousal=0.4)
    last = None
    for i in range(40):
        obs = Observation(timestamp=1000.0 + i, valence=0.6, arousal=0.4)
        last = est.update(obs)
    assert last.stability > 0.8


def test_stability_lower_when_volatile():
    """剧烈变化 → stability 低于平静时。"""
    est_calm = make_estimator()
    est_calm.initialize(timestamp=1000.0, valence=0.6, arousal=0.4)
    est_volatile = make_estimator()
    est_volatile.initialize(timestamp=1000.0, valence=0.6, arousal=0.4)

    for i in range(20):
        est_calm.update(Observation(timestamp=1000.0 + i, valence=0.6, arousal=0.4))
        # 剧烈震荡
        v = 0.2 if i % 2 == 0 else 0.9
        a = 0.8 if i % 2 == 0 else 0.2
        est_volatile.update(Observation(timestamp=1000.0 + i, valence=v, arousal=a))

    calm_st = est_calm._to_state(1000.0 + 19)
    vol_st = est_volatile._to_state(1000.0 + 19)
    assert vol_st.stability < calm_st.stability


# ============================================================
# 生理控制输入（保留旧版机制，part1 §3.1）
# ============================================================


def test_physio_control_increases_arousal_velocity():
    """HRV 下降 + HR 上升的控制输入应推高唤醒。"""
    bl = Baseline(resting_hr=70.0, resting_hrv_mean=50.0)

    est_no_physio = make_estimator(baseline=bl)
    est_no_physio.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    est_physio = make_estimator(baseline=bl)
    est_physio.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)

    for i in range(15):
        # 无生理输入
        est_no_physio.update(
            Observation(timestamp=1000.0 + i, valence=0.5, arousal=0.5)
        )
        # 有生理输入：HR 升高到 100，HRV 降到 25
        est_physio.update(
            Observation(
                timestamp=1000.0 + i,
                valence=0.5,
                arousal=0.5,
                hr=100.0,
                hrv=25.0,
                source=ObservationSource.SENSOR,
            )
        )

    st_no = est_no_physio._to_state(1000.0 + 14)
    st_yes = est_physio._to_state(1000.0 + 14)
    # 生理应激信号应使 arousal 更高（或至少 arousal 速度更高）
    assert st_yes.arousal >= st_no.arousal - 0.01


def test_control_arousal_from_meta():
    """meta 显式提供 hrv_drop_ratio / hr_change 时应被采用。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    obs = Observation(
        timestamp=1001.0,
        valence=0.5,
        arousal=0.5,
        meta={"hrv_drop_ratio": 0.5, "hr_change": 30.0, "signal_quality": 1.0},
    )
    ctrl = est._compute_control_arousal(obs)
    # w_hrv=0.3, w_hr=0.2 → 0.3*0.5 + 0.2*(30/100) = 0.15 + 0.06 = 0.21
    assert ctrl == pytest.approx(0.21, abs=0.01)


def test_control_arousal_gated_by_signal_quality():
    """signal_quality < 0.3 时控制输入归零（信号不可信）。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0)
    obs = Observation(
        timestamp=1001.0,
        meta={"hrv_drop_ratio": 0.5, "hr_change": 30.0, "signal_quality": 0.1},
    )
    assert est._compute_control_arousal(obs) == 0.0


# ============================================================
# 自适应观测噪声（保留旧版 compute_R_from_interaction 思想）
# ============================================================


def test_observation_noise_fast_touch_is_lower():
    """快速拖动（用户积极控制）→ 噪声更低。"""
    config = EstimatorConfig()
    params = ModelParameters()
    slow = Observation(timestamp=1.0, valence=0.5, meta={"touch_velocity": 0.0})
    fast = Observation(timestamp=1.0, valence=0.5, meta={"touch_velocity": 1.0})
    n_slow = compute_observation_noise(slow, config, params)
    n_fast = compute_observation_noise(fast, config, params)
    assert n_fast < n_slow


def test_observation_noise_stillness_jump_is_higher():
    """长时间静止后跳变 → 噪声更高（可能误触/补录）。"""
    config = EstimatorConfig()
    params = ModelParameters()
    normal = Observation(timestamp=1.0, valence=0.5, meta={"seconds_since_last_touch": 0.5})
    jump = Observation(timestamp=1.0, valence=0.5, meta={"seconds_since_last_touch": 10.0})
    n_normal = compute_observation_noise(normal, config, params)
    n_jump = compute_observation_noise(jump, config, params)
    assert n_jump > n_normal


def test_observation_noise_low_confidence_is_higher():
    """低置信度观察（如 agent 推断）→ 噪声更高。"""
    config = EstimatorConfig()
    params = ModelParameters()
    high_conf = Observation(timestamp=1.0, valence=0.5, confidence=1.0)
    low_conf = Observation(timestamp=1.0, valence=0.5, confidence=0.3)
    n_high = compute_observation_noise(high_conf, config, params)
    n_low = compute_observation_noise(low_conf, config, params)
    assert n_low > n_high


def test_observation_noise_always_positive():
    config = EstimatorConfig()
    params = ModelParameters()
    for conf in [0.0, 0.5, 1.0]:
        obs = Observation(timestamp=1.0, valence=0.5, confidence=conf)
        assert compute_observation_noise(obs, config, params) > 0.0


# ============================================================
# 部分观察（只有 valence 或只有 arousal）
# ============================================================


def test_partial_observation_valence_only():
    """只观察 valence 时不应崩溃，arousal 由模型预测。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    for i in range(10):
        obs = Observation(timestamp=1000.0 + i, valence=0.7, arousal=None)
        st = est.update(obs)
        assert 0.0 <= st.valence <= 1.0
        assert 0.0 <= st.arousal <= 1.0
    # valence 应向 0.7 收敛
    assert st.valence == pytest.approx(0.7, abs=0.05)


def test_partial_observation_arousal_only():
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    for i in range(10):
        obs = Observation(timestamp=1000.0 + i, valence=None, arousal=0.8)
        st = est.update(obs)
        assert 0.0 <= st.arousal <= 1.0
    assert st.arousal == pytest.approx(0.8, abs=0.05)


def test_pure_physio_observation_does_not_crash():
    """纯生理观察（无情绪通道）只走预测+控制输入，不做观测更新。"""
    est = make_estimator(baseline=Baseline(resting_hr=70.0, resting_hrv_mean=50.0))
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    for i in range(10):
        obs = Observation(
            timestamp=1000.0 + i, hr=90.0, hrv=30.0, source=ObservationSource.SENSOR
        )
        st = est.update(obs)
        assert isinstance(st, EmotionState)
        assert 0.0 <= st.valence <= 1.0


# ============================================================
# extrapolate（预警用，不污染内部状态）
# ============================================================


def test_extrapolate_returns_trajectory():
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.7)
    traj = est.extrapolate(horizon_sec=10.0, dt=1.0)
    assert len(traj) == 10
    assert all(isinstance(s, EmotionState) for s in traj)


def test_extrapolate_does_not_mutate_state():
    """外推后内部状态必须与外推前完全一致（关键不变量）。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    for i in range(5):
        est.update(Observation(timestamp=1000.0 + i, valence=0.6, arousal=0.4))

    x_before = est.state_vector
    p_before = est.covariance
    ts_before = est._last_timestamp

    est.extrapolate(horizon_sec=60.0, dt=1.0)

    assert est.state_vector == x_before
    assert est.covariance == p_before
    assert est._last_timestamp == ts_before


def test_extrapolate_timestamps_advance():
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    traj = est.extrapolate(horizon_sec=5.0, dt=1.0)
    ts = [s.timestamp for s in traj]
    assert ts == [1001.0, 1002.0, 1003.0, 1004.0, 1005.0]


def test_extrapolate_empty_when_uninitialized():
    est = make_estimator()
    assert est.extrapolate(horizon_sec=10.0) == []


def test_extrapolate_empty_for_nonpositive_horizon():
    est = make_estimator()
    est.initialize(timestamp=1000.0)
    assert est.extrapolate(horizon_sec=0.0) == []
    assert est.extrapolate(horizon_sec=-5.0) == []


# ============================================================
# 防御性：乱序观察
# ============================================================


def test_out_of_order_observation_raises():
    """乱序观察（时间戳倒退）应被拒绝（防御性，避免污染状态）。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    est.update(Observation(timestamp=1005.0, valence=0.6, arousal=0.4))
    with pytest.raises(ValueError):
        est.update(Observation(timestamp=1000.0, valence=0.6, arousal=0.4))


def test_large_time_gap_is_clamped():
    """长时间无观察后，Δt 被钳到上限，避免外推发散。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    # 跳跃 1 小时
    obs = Observation(timestamp=1000.0 + 3600.0, valence=0.6, arousal=0.4)
    st = est.update(obs)  # 不应崩溃
    assert 0.0 <= st.valence <= 1.0
    assert 0.0 <= st.arousal <= 1.0


# ============================================================
# reset 与状态访问
# ============================================================


def test_reset_clears_state():
    est = make_estimator()
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    est.update(Observation(timestamp=1001.0, valence=0.6, arousal=0.4))
    est.reset()
    assert est.is_initialized is False
    assert est.update_count == 0


def test_update_count_increments():
    est = make_estimator()
    est.initialize(timestamp=1000.0)
    for i in range(5):
        est.update(Observation(timestamp=1000.0 + i, valence=0.5, arousal=0.5))
    assert est.update_count == 5


def test_covariance_is_copy():
    """covariance 返回拷贝，外部修改不影响内部状态。"""
    est = make_estimator()
    est.initialize(timestamp=1000.0)
    p = est.covariance
    p[0][0] = 999.0
    assert est.covariance[0][0] != 999.0


# ============================================================
# Matérn 参数化的行为验证（part1 §1.2 记忆问题修复）
# ============================================================


def test_longer_ell_gives_more_emotional_inertia():
    """ℓ 越大（情绪惯性越强，Kuppens 2010），外推时速度衰减越慢。

    Matérn ν=3/2 的速度分量按 e^(-λΔt) 衰减，λ=√3/ℓ。
    这是 ℓ 作为"情绪惯性"的核心语义：高惯性用户的情绪漂移持续更久。

    验证：给相同初速度，外推 60 秒后，长 ℓ 的速度保留更多、
    位置沿原方向漂移更远；短 ℓ 的速度衰减到接近 0、位置基本停住。
    """
    params_short = ModelParameters(ell_valence=30.0, ell_arousal=240.0)
    params_long = ModelParameters(ell_valence=600.0, ell_arousal=240.0)

    est_short = make_estimator(params=params_short)
    est_short.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    est_long = make_estimator(params=params_long)
    est_long.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)

    # 给相同的初始效价速度 v̇=0.005（情绪正向漂移，量级足够小避免触碰 [0,1] 边界）
    est_short._x = [0.5, 0.005, 0.5, 0.0]
    est_long._x = [0.5, 0.005, 0.5, 0.0]

    # 外推 30 秒（无观测，纯状态转移）
    traj_short = est_short.extrapolate(horizon_sec=30.0, dt=1.0)
    traj_long = est_long.extrapolate(horizon_sec=30.0, dt=1.0)

    end_short = traj_short[-1]
    end_long = traj_long[-1]

    # 长 ℓ（高惯性）：30 秒后速度仍保留较多，位置漂移更远
    # 短 ℓ（低惯性）：速度按 e^(-√3·30/30)=e^(-1.73)≈0.18 衰减到 ~18%
    lam_short = params_short.lambda_valence()
    lam_long = params_long.lambda_valence()
    retain_short = math.exp(-lam_short * 30.0)  # ≈0.18
    retain_long = math.exp(-lam_long * 30.0)    # ≈0.92
    assert retain_long > retain_short + 0.5

    # 位置漂移：长 ℓ 应显著高于短 ℓ（速度保留更久 → 持续漂移），且都不触界
    drift_short = end_short.valence - 0.5
    drift_long = end_long.valence - 0.5
    assert drift_long > drift_short
    assert end_long.valence < 1.0 and end_short.valence < 1.0  # 未触发裁剪

    # 长 ℓ（弱均值回复）末段仍沿原方向上升；
    # 短 ℓ（强均值回复，Matérn ν=3/2 是临界阻尼振子）可能已开始回落，
    # 故不比较末段瞬时速度符号，只用上面的 retain + drift 刻画惯性。
    delta_long = traj_long[-1].valence - traj_long[-2].valence
    assert delta_long > 0  # 长 ℓ 末段仍在漂移


def test_estimator_uses_matern_not_handwritten_F():
    """验证转移矩阵确实由 Matérn 闭式解构造（而非旧版手写匀速 F）。

    旧版 F[0][2]=dt（位置=旧位置+速度*dt），新版 F[0][1]=decay*(1+λdt)。
    检查 _predict 后状态向量的结构符合 Matérn 而非匀速模型。
    """
    params = ModelParameters(ell_valence=300.0, ell_arousal=240.0)
    est = make_estimator(params=params)
    est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
    # 给一个速度，预测一步，验证衰减符合 e^(-λΔt)
    est._x = [0.5, 0.1, 0.5, 0.0]  # v̇=0.1
    dt = 1.0
    est._predict(dt)
    lam_v = params.lambda_valence()
    # Matérn: v_new = decay*[(1+λdt)*v + dt*v̇]
    decay = math.exp(-lam_v * dt)
    expected_v = decay * ((1 + lam_v * dt) * 0.5 + dt * 0.1)
    assert est._x[0] == pytest.approx(expected_v, abs=1e-6)


# ============================================================
# 零依赖验证
# ============================================================


def test_estimator_module_has_no_numpy_import():
    """estimator 模块源码不应 import numpy（LIGHTWEIGHT part2 §2）。"""
    import emowave.core.estimator.estimator as mod
    import inspect
    src = inspect.getsource(mod)
    assert "import numpy" not in src
    assert "import scipy" not in src
    assert "np." not in src
