"""Personal Dynamics Model 单元测试（dynamics + predictor）。

覆盖 REFACTOR_PLAN.md §28 Phase 6 与 §10：
  - 状态转移模型 E_{t+1}=f(E_t,X_t,U_t,Δt)
  - temporal decay / 恢复速度（recovery_half_time ≈ ℓ）
  - signal sensitivity 学习（哪些信号对这个用户影响最大）
  - 个人波动范围（σ）
  - 短期趋势预测 + uncertainty（置信带随 horizon 变宽）
  - §10 四个问题的回答能力
"""

import math

import pytest

from emowave.core.domain.emotion_state import EmotionState, Trend, compute_intensity
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource
from emowave.core.inference.dynamics import (
    DynamicsLearner,
    OnlineFeatureRegression,
    PersonalDynamicsModel,
    SignalSensitivity,
    predict_forward,
    recovery_half_time,
)
from emowave.core.inference.predictor import TrendForecast, TrendPredictor


# ============================================================
# recovery_half_time（temporal decay 可解释翻译）
# ============================================================


def test_recovery_half_time_proportional_to_ell():
    """恢复半衰期 ≈ 0.969·ℓ（ℓ 本质就是恢复时间尺度）。"""
    assert recovery_half_time(300.0) == pytest.approx(290.7, abs=1.0)
    assert recovery_half_time(100.0) == pytest.approx(96.9, abs=1.0)


def test_recovery_half_time_monotonic_in_ell():
    """ℓ 越大（惯性越强），恢复越慢（半衰期越长）。"""
    assert recovery_half_time(600.0) > recovery_half_time(300.0) > recovery_half_time(60.0)


def test_recovery_half_time_rejects_nonpositive():
    with pytest.raises(ValueError):
        recovery_half_time(0.0)
    with pytest.raises(ValueError):
        recovery_half_time(-100.0)


def test_recovery_half_time_matches_matern_decay():
    """半衰期处 Matérn 偏离确实衰减到约 0.5（验证 0.969 系数）。

    d(Δt)/d₀ = e^(-λΔt)(1+λΔt)，在 Δt=recovery_half_time(ℓ) 处应 ≈ 0.5。
    """
    ell = 300.0
    lam = math.sqrt(3.0) / ell
    t_half = recovery_half_time(ell)
    decay = math.exp(-lam * t_half) * (1 + lam * t_half)
    assert decay == pytest.approx(0.5, abs=0.01)


# ============================================================
# OnlineFeatureRegression（signal sensitivity 学习）
# ============================================================


def test_feature_regression_learns_linear():
    """学习 y = 2·x1 - 1·x2 + 0.5。"""
    reg = OnlineFeatureRegression(n_features=3, reg_lambda=0.01)
    import random
    rng = random.Random(0)
    for _ in range(200):
        x1 = rng.uniform(-1, 1)
        x2 = rng.uniform(-1, 1)
        y = 2.0 * x1 - 1.0 * x2 + 0.5
        reg.update([x1, x2], y, weight=1.0)
    theta = reg.theta  # [截距, x1, x2]
    assert theta[0] == pytest.approx(0.5, abs=0.05)
    assert theta[1] == pytest.approx(2.0, abs=0.05)
    assert theta[2] == pytest.approx(-1.0, abs=0.05)


def test_feature_regression_r2_high_for_clean_signal():
    reg = OnlineFeatureRegression(n_features=2, reg_lambda=0.01)
    import random
    rng = random.Random(1)
    for _ in range(100):
        x = rng.uniform(0, 1)
        reg.update([x], 3.0 * x + 1.0, weight=1.0)
    assert reg.r_squared() > 0.9


def test_feature_regression_r2_low_for_noise():
    """目标是纯噪声时 R² 应很低（信号无解释力 → 该信号是噪声，§9.2）。"""
    reg = OnlineFeatureRegression(n_features=2, reg_lambda=0.01)
    import random
    rng = random.Random(2)
    for _ in range(100):
        x = rng.uniform(0, 1)
        y = rng.gauss(0, 1)  # 与 x 无关的纯噪声
        reg.update([x], y, weight=1.0)
    assert reg.r_squared() < 0.3


def test_feature_regression_weighted():
    reg = OnlineFeatureRegression(n_features=2, reg_lambda=0.01)
    # 高权重样本主导
    for _ in range(20):
        reg.update([1.0], 5.0, weight=1.0)
    for _ in range(20):
        reg.update([1.0], -5.0, weight=0.01)
    assert reg.predict([1.0]) == pytest.approx(5.0, abs=0.5)


def test_feature_regression_zero_weight_ignored():
    reg = OnlineFeatureRegression(n_features=2)
    reg.update([1.0], 5.0, weight=0.0)
    assert reg.n_samples == 0


def test_feature_regression_dimension_mismatch():
    reg = OnlineFeatureRegression(n_features=3)
    with pytest.raises(ValueError):
        reg.update([1.0], 5.0)  # 只给 1 个特征，期望 2 个


def test_feature_regression_rejects_invalid_params():
    with pytest.raises(ValueError):
        OnlineFeatureRegression(n_features=0)
    with pytest.raises(ValueError):
        OnlineFeatureRegression(n_features=2, reg_lambda=-1.0)


# ============================================================
# DynamicsLearner（signal sensitivity）
# ============================================================


def make_physio_series(n, start_ts=1000.0, hr_trend=0.0, hrv_trend=0.0, seed=0):
    """生成带生理信号的观察序列，hr/hrv 随时间变化。"""
    import random
    rng = random.Random(seed)
    obs = []
    for i in range(n):
        hr = 72.0 + hr_trend * i + rng.gauss(0, 1)
        hrv = 50.0 + hrv_trend * i + rng.gauss(0, 1)
        # 唤醒随 hr 上升而上升（模拟生理驱动）
        arousal = min(1.0, max(0.0, 0.4 + 0.005 * (hr - 72.0)))
        valence = min(1.0, max(0.0, 0.6 - 0.003 * (hr - 72.0)))
        obs.append(Observation(
            timestamp=start_ts + i, valence=valence, arousal=arousal,
            hr=hr, hrv=hrv, source=ObservationSource.SENSOR,
        ))
    return obs


def test_dynamics_learner_ingests_series():
    learner = DynamicsLearner()
    obs = make_physio_series(30, hr_trend=0.5)
    n = learner.ingest_series(obs)
    assert n == 29  # 30 个点 → 29 个转移


def test_dynamics_learner_builds_model():
    learner = DynamicsLearner()
    obs = make_physio_series(40, hr_trend=0.5)
    learner.ingest_series(obs)
    params = ModelParameters(ell_valence=300.0, ell_arousal=240.0)
    model = learner.build_model(params, mean_valence=0.55, mean_arousal=0.5)
    assert isinstance(model, PersonalDynamicsModel)
    assert model.recovery_half_time_valence == pytest.approx(recovery_half_time(300.0))
    assert model.volatility_valence == params.sigma_valence
    assert model.mean_valence == 0.55
    assert model.confidence > 0.3  # 有样本后置信度上升


def test_dynamics_learner_hrv_drives_arousal():
    """HRV 下降驱动唤醒上升的用户，应学到 w_hrv_arousal > 0。"""
    import random
    rng = random.Random(5)
    learner = DynamicsLearner()
    obs = []
    hrv = 50.0
    for i in range(60):
        hrv -= 0.3  # HRV 持续下降
        # 唤醒随 HRV 下降而上升
        arousal = min(1.0, 0.4 + 0.01 * (50.0 - hrv))
        obs.append(Observation(
            timestamp=1000.0 + i, valence=0.5, arousal=arousal,
            hr=72.0, hrv=hrv, source=ObservationSource.SENSOR,
        ))
    learner.ingest_series(obs, baseline_arousal=0.4, resting_hrv=50.0)
    params = ModelParameters()
    model = learner.build_model(params)
    # HRV 下降（hrv_drop 正）应正相关于唤醒上升 → w_hrv_arousal > 0
    assert model.signal_sensitivity.w_hrv_arousal > 0


def test_dynamics_learner_confidence_grows_with_samples():
    learner = DynamicsLearner()
    params = ModelParameters()
    # 少样本
    learner.ingest_series(make_physio_series(5))
    m_few = learner.build_model(params)
    # 更多样本
    learner.ingest_series(make_physio_series(50))
    m_many = learner.build_model(params)
    assert m_many.confidence > m_few.confidence


def test_dynamics_learner_dominant_signals():
    learner = DynamicsLearner()
    learner.ingest_series(make_physio_series(40, hr_trend=0.5))
    model = learner.build_model(ModelParameters())
    top = model.signal_sensitivity.dominant_signals(3)
    assert len(top) == 3
    # 按 |权重| 降序
    weights = [abs(w) for _, w in top]
    assert weights == sorted(weights, reverse=True)


def test_signal_sensitivity_defaults():
    s = SignalSensitivity()
    assert s.w_hrv_arousal == 0.3
    top = s.dominant_signals(2)
    assert len(top) == 2


# ============================================================
# predict_forward（带不确定性增长）
# ============================================================


def test_predict_forward_returns_trajectory():
    params = ModelParameters()
    x = [0.6, 0.0, 0.4, 0.0]
    p = [[0.01, 0, 0, 0], [0, 0.01, 0, 0], [0, 0, 0.01, 0], [0, 0, 0, 0.01]]
    traj = predict_forward(x, p, params, horizon_sec=10.0, dt=1.0)
    assert len(traj) == 10
    assert all("valence" in t and "var_valence" in t for t in traj)


def test_predict_forward_uncertainty_grows():
    """预测越远，方差越大（置信带变宽，§6.4 诚实表达不确定性）。"""
    params = ModelParameters()
    x = [0.6, 0.0, 0.4, 0.0]
    p = [[0.001, 0, 0, 0], [0, 0.001, 0, 0], [0, 0, 0.001, 0], [0, 0, 0, 0.001]]
    traj = predict_forward(x, p, params, horizon_sec=60.0, dt=1.0)
    var_early = traj[5]["var_valence"]
    var_late = traj[-1]["var_valence"]
    assert var_late > var_early


def test_predict_forward_variance_bounded_by_stationary():
    """方差增长趋近平稳方差 σ²，不无限发散。"""
    params = ModelParameters(sigma_valence=0.15)
    x = [0.6, 0.0, 0.4, 0.0]
    p = [[0.001, 0, 0, 0], [0, 0.001, 0, 0], [0, 0, 0.001, 0], [0, 0, 0, 0.001]]
    traj = predict_forward(x, p, params, horizon_sec=2000.0, dt=1.0)
    stationary_var = params.sigma_valence ** 2
    assert traj[-1]["var_valence"] <= stationary_var + 1e-6


def test_predict_forward_mean_reverts_to_zero_velocity():
    """无观测时速度按 e^(-λΔt) 衰减，位置趋于稳定（均值回复）。"""
    params = ModelParameters(ell_valence=300.0)
    x = [0.6, 0.05, 0.4, 0.0]  # 有正速度
    p = [[0.01, 0, 0, 0], [0, 0.01, 0, 0], [0, 0, 0.01, 0], [0, 0, 0, 0.01]]
    traj = predict_forward(x, p, params, horizon_sec=100.0, dt=1.0)
    # 速度衰减 → 后期 valence 变化率减小
    early_slope = traj[5]["valence"] - traj[4]["valence"]
    late_slope = traj[-1]["valence"] - traj[-2]["valence"]
    assert abs(late_slope) < abs(early_slope)


def test_predict_forward_empty_for_nonpositive_horizon():
    params = ModelParameters()
    x = [0.6, 0.0, 0.4, 0.0]
    p = [[0.01, 0, 0, 0], [0, 0.01, 0, 0], [0, 0, 0.01, 0], [0, 0, 0, 0.01]]
    assert predict_forward(x, p, params, horizon_sec=0.0) == []
    assert predict_forward(x, p, params, horizon_sec=-5.0) == []


def test_predict_forward_stays_in_unit_interval():
    params = ModelParameters()
    x = [0.95, 0.1, 0.9, 0.1]  # 接近边界 + 正速度
    p = [[0.01, 0, 0, 0], [0, 0.01, 0, 0], [0, 0, 0.01, 0], [0, 0, 0, 0.01]]
    traj = predict_forward(x, p, params, horizon_sec=50.0, dt=1.0)
    for t in traj:
        assert 0.0 <= t["valence"] <= 1.0
        assert 0.0 <= t["arousal"] <= 1.0


# ============================================================
# TrendPredictor（§10 四个问题）
# ============================================================


def test_predictor_forecast_returns_trend_forecast():
    params = ModelParameters()
    predictor = TrendPredictor(params=params)
    state = EmotionState(timestamp=1000.0, valence=0.6, arousal=0.7,
                         variance_valence=0.01, variance_arousal=0.01, confidence=0.8)
    fc = predictor.forecast(state, horizon_sec=30.0)
    assert isinstance(fc, TrendForecast)
    assert fc.horizon_sec == 30.0
    assert fc.trend in list(Trend)


def test_predictor_forecast_ci_widens_with_horizon():
    """预测置信区间随 horizon 变宽（不确定性增长）。"""
    params = ModelParameters()
    predictor = TrendPredictor(params=params)
    state = EmotionState(timestamp=1000.0, valence=0.5, arousal=0.5,
                         variance_valence=0.001, variance_arousal=0.001, confidence=0.9)
    fc_short = predictor.forecast(state, horizon_sec=10.0)
    fc_long = predictor.forecast(state, horizon_sec=120.0)
    width_short = fc_short.ci_valence[1] - fc_short.ci_valence[0]
    width_long = fc_long.ci_valence[1] - fc_long.ci_valence[0]
    assert width_long > width_short


def test_predictor_confidence_decreases_with_horizon():
    """预测置信度随 horizon 下降（诚实表达不确定性）。"""
    params = ModelParameters()
    predictor = TrendPredictor(params=params)
    state = EmotionState(timestamp=1000.0, valence=0.5, arousal=0.5,
                         variance_valence=0.01, variance_arousal=0.01, confidence=0.9)
    fc_short = predictor.forecast(state, horizon_sec=10.0)
    fc_long = predictor.forecast(state, horizon_sec=300.0)
    assert fc_long.confidence < fc_short.confidence


def test_predictor_rising_intensity_trend():
    """从低唤醒向高唤醒的状态（带正速度）预测 trend=RISING。"""
    params = ModelParameters()
    predictor = TrendPredictor(params=params)
    # meta 带正速度（唤醒上升中）
    state = EmotionState(timestamp=1000.0, valence=0.5, arousal=0.5,
                         variance_valence=0.01, variance_arousal=0.01,
                         confidence=0.8, meta={"d_arousal": 0.02, "d_valence": 0.0})
    fc = predictor.forecast(state, horizon_sec=20.0)
    assert fc.trend == Trend.RISING


def test_predictor_stable_trend_at_rest():
    """静止状态（无速度，已在均值回复平衡）预测 trend=STABLE。"""
    params = ModelParameters()
    predictor = TrendPredictor(params=params)
    state = EmotionState(timestamp=1000.0, valence=0.5, arousal=0.5,
                         variance_valence=0.01, variance_arousal=0.01, confidence=0.8)
    fc = predictor.forecast(state, horizon_sec=30.0)
    assert fc.trend == Trend.STABLE


def test_predictor_recovery_estimate_far_from_baseline():
    """远离基线的状态，恢复时间 > 0（§10 问题4）。"""
    params = ModelParameters(ell_valence=300.0, sigma_valence=0.1, sigma_arousal=0.1)
    predictor = TrendPredictor(params=params, baseline_valence=0.5, baseline_arousal=0.5)
    # 状态远离基线（0.9 vs 0.5）
    state = EmotionState(timestamp=1000.0, valence=0.9, arousal=0.5,
                         variance_valence=0.01, variance_arousal=0.01)
    rec = predictor.recovery_estimate(state)
    assert rec is not None
    assert rec > 0


def test_predictor_recovery_estimate_at_baseline():
    """已在基线附近，恢复时间 ≈ 0。"""
    params = ModelParameters(sigma_valence=0.1, sigma_arousal=0.1)
    predictor = TrendPredictor(params=params, baseline_valence=0.5, baseline_arousal=0.5)
    state = EmotionState(timestamp=1000.0, valence=0.5, arousal=0.5)
    rec = predictor.recovery_estimate(state)
    assert rec == 0.0


def test_predictor_recovery_faster_for_shorter_ell():
    """ℓ 短（惯性弱）的用户恢复更快（§10 问题4：恢复速度差异）。"""
    state = EmotionState(timestamp=1000.0, valence=0.9, arousal=0.5,
                         variance_valence=0.01, variance_arousal=0.01)
    p_fast = ModelParameters(ell_valence=60.0, sigma_valence=0.1, sigma_arousal=0.1)
    p_slow = ModelParameters(ell_valence=600.0, sigma_valence=0.1, sigma_arousal=0.1)
    pred_fast = TrendPredictor(params=p_fast, baseline_valence=0.5, baseline_arousal=0.5)
    pred_slow = TrendPredictor(params=p_slow, baseline_valence=0.5, baseline_arousal=0.5)
    assert pred_fast.recovery_estimate(state) < pred_slow.recovery_estimate(state)


def test_predictor_recovery_speed_changing():
    """对比两个时期模型，判断恢复速度变化（§10 问题4）。"""
    prev = PersonalDynamicsModel(recovery_half_time_valence=300.0)
    curr_faster = PersonalDynamicsModel(recovery_half_time_valence=200.0)
    result = TrendPredictor().recovery_speed_changing(prev, curr_faster)
    assert result["faster"] is True
    assert result["ratio"] == pytest.approx(200.0 / 300.0)

    curr_slower = PersonalDynamicsModel(recovery_half_time_valence=400.0)
    result2 = TrendPredictor().recovery_speed_changing(prev, curr_slower)
    assert result2["slower"] is True


def test_predictor_most_influential_signals():
    """§10 问题2：什么因素最容易让这个用户变化。"""
    learner = DynamicsLearner()
    learner.ingest_series(make_physio_series(40, hr_trend=0.5))
    model = learner.build_model(ModelParameters())
    predictor = TrendPredictor(dynamics=model)
    signals = predictor.most_influential_signals(3)
    assert len(signals) == 3


def test_predictor_most_influential_signals_no_dynamics():
    predictor = TrendPredictor(dynamics=None)
    assert predictor.most_influential_signals() == []


def test_predictor_forecast_zero_horizon():
    predictor = TrendPredictor()
    state = EmotionState(timestamp=1000.0, valence=0.6, arousal=0.4, confidence=0.7)
    fc = predictor.forecast(state, horizon_sec=0.0)
    assert fc.horizon_sec == 0.0
    assert fc.predicted_valence == pytest.approx(0.6)


def test_trend_forecast_to_dict():
    predictor = TrendPredictor()
    state = EmotionState(timestamp=1000.0, valence=0.6, arousal=0.4, confidence=0.7)
    fc = predictor.forecast(state, horizon_sec=10.0)
    d = fc.to_dict()
    assert d["trend"] in ("rising", "falling", "stable", "unknown")
    assert "trajectory" in d


def test_personal_dynamics_model_to_dict():
    model = PersonalDynamicsModel()
    d = model.to_dict()
    assert "signal_sensitivity" in d
    assert d["ell_valence"] == 300.0
