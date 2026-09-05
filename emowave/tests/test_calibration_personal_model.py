"""PersonalModelLearner + kalman_log_likelihood + Calibrator 单元测试。

覆盖 ARCHITECTURE part1 §3.3 L3 学习层：
  - 层次先验收缩（小样本向群体先验收缩，防过拟合）
  - Coactive 有界更新（单次大幅拖动只产生有界影响）
  - part1 §4.4 诊断表（峰值偏差→σ，时间持续→ℓ，随机散度→σ_noise）
  - 边际似然（学习应提升拟合优度）
  - REFACTOR_PLAN §28 Phase 4 完成标准：模型从纠正中发生可验证的个性化变化
  - part1 §8 阶段4：学到的 ℓ 能区分画像（稳定型 ℓ > 焦虑型 ℓ）
"""

import math

import pytest

from emowave.core.calibration.calibrator import Calibrator
from emowave.core.calibration.corrections import CorrectionDataset
from emowave.core.calibration.personal_model import (
    LearnerConfig,
    PersonalModelLearner,
    kalman_log_likelihood,
)
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource


# ============================================================
# kalman_log_likelihood
# ============================================================


def make_obs(n, start_ts=1000.0, v=0.5, a=0.5, noise=0.0, seed=0):
    import random
    rng = random.Random(seed)
    out = []
    for i in range(n):
        vv = v if noise == 0 else min(1.0, max(0.0, v + rng.gauss(0, noise)))
        aa = a if noise == 0 else min(1.0, max(0.0, a + rng.gauss(0, noise)))
        out.append(Observation(timestamp=start_ts + i, valence=vv, arousal=aa,
                               source=ObservationSource.USER))
    return out


def test_log_likelihood_returns_finite():
    obs = make_obs(20, v=0.6, a=0.4)
    ll = kalman_log_likelihood(obs, ModelParameters())
    assert math.isfinite(ll)


def test_log_likelihood_empty_is_zero():
    assert kalman_log_likelihood([], ModelParameters()) == 0.0


def test_log_likelihood_higher_for_matching_params():
    """与数据生成过程匹配的参数应有更高似然（边际似然最大化的基础）。

    生成 ℓ=300 的平滑数据，对照 ℓ=300（匹配）与 ℓ=30（失配）的似然。
    """
    # 用 ℓ=300 的平滑信号（慢变）
    obs = [
        Observation(timestamp=1000.0 + i,
                    valence=0.5 + 0.2 * math.sin(2 * math.pi * i / 300.0),
                    arousal=0.5)
        for i in range(200)
    ]
    ll_match = kalman_log_likelihood(obs, ModelParameters(ell_valence=300.0))
    ll_mismatch = kalman_log_likelihood(obs, ModelParameters(ell_valence=20.0))
    # 匹配的参数（长 ℓ，平滑）应比失配（短 ℓ，认为信号该剧烈变化）似然更高
    assert ll_match > ll_mismatch


def test_log_likelihood_ignores_pure_physio():
    obs = [Observation(timestamp=1000.0 + i, hr=80.0, hrv=40.0,
                       source=ObservationSource.SENSOR) for i in range(10)]
    assert kalman_log_likelihood(obs, ModelParameters()) == 0.0


# ============================================================
# PersonalModelLearner：层次先验收缩（part1 §3.3）
# ============================================================


def test_learner_cold_start_stays_near_population():
    """小样本（ESS<5）强制强收缩，参数接近群体先验（防过拟合，part1 §9 风险表）。"""
    learner = PersonalModelLearner()
    params = ModelParameters.population_prior()
    ds = CorrectionDataset()
    # 仅 2 条纠正（ESS≈2 < 5）
    ds.add_raw(1.0, 0.4, 0.8, weight=1.0, salience=0.9)
    ds.add_raw(2.0, 0.4, 0.8, weight=1.0, salience=0.9)
    new_params = learner.update(params, ds)
    pop = ModelParameters.population_prior()
    # 强收缩：与群体先验的相对偏离应很小（< max_relative_step）
    assert abs(new_params.sigma_valence - pop.sigma_valence) / pop.sigma_valence < 0.21
    assert abs(new_params.ell_valence - pop.ell_valence) / pop.ell_valence < 0.21


def test_learner_more_evidence_allows_more_personalization():
    """证据越多（ESS 大），收缩越弱，个性化越强。"""
    learner = PersonalModelLearner()
    pop = ModelParameters.population_prior()

    # 少证据
    ds_few = CorrectionDataset()
    for i in range(3):
        ds_few.add_raw(float(i), 0.4, 0.8, weight=1.0, salience=0.9)
    p_few = learner.update(pop, ds_few)

    # 多证据（ESS=50）
    ds_many = CorrectionDataset()
    for i in range(50):
        ds_many.add_raw(float(i), 0.4, 0.8, weight=1.0, salience=0.9)
    p_many = learner.update(pop, ds_many)

    # 多证据的 shrinkage_alpha（个人权重）应更大
    assert p_many.shrinkage_alpha > p_few.shrinkage_alpha


def test_learner_shrinkage_alpha_increases_with_evidence():
    """shrinkage_alpha（个人证据权重）随累积纠正证据（ESS）单调上升。

    现实流程：每个事件追加更多纠正，dataset 累积 ESS 渐增 → 群体收缩渐弱
    → 个人权重 alpha 渐增（part1 §3.3 "shrinkage_alpha 随数据量单调上升"）。
    """
    learner = PersonalModelLearner()
    params = ModelParameters.population_prior()
    ds = CorrectionDataset()
    alphas = []
    for step in range(5):
        # 每步事件加入更多纠正（ESS 增长）
        for j in range(12):
            ds.add_raw(float(step * 100 + j), 0.4, 0.65, weight=1.0, salience=0.7)
        params = learner.update(params, ds)
        alphas.append(params.shrinkage_alpha)
    # ESS: 12→24→36→48→60，alpha 应整体上升
    assert alphas[-1] > alphas[0]
    assert alphas[-1] == pytest.approx(0.8, abs=0.01)  # ESS≥40 → w_pop=0.2 → alpha=0.8


# ============================================================
# Coactive 有界更新（part1 §4.3）
# ============================================================


def test_learner_single_step_bounded():
    """单步参数相对变化不超过 max_relative_step（Coactive 有界）。"""
    cfg = LearnerConfig(max_relative_step=0.2)
    learner = PersonalModelLearner(config=cfg)
    params = ModelParameters.population_prior()
    ds = CorrectionDataset()
    # 极端纠正（试图把参数拉很远）
    for i in range(50):
        ds.add_raw(float(i), 0.1, 0.99, weight=1.0, salience=1.0)
    new_params = learner.update(params, ds)
    # 每个参数的相对变化都应 ≤ 20%
    for name in ["ell_valence", "ell_arousal", "sigma_valence", "sigma_arousal", "sigma_noise"]:
        cur = getattr(params, name)
        new = getattr(new_params, name)
        rel = abs(new - cur) / cur
        assert rel <= 0.2 + 1e-6, f"{name} 变化 {rel:.3f} 超过 20% 上限"


def test_learner_extreme_single_drag_does_not_destroy_model():
    """单次极端拖动（即便高权重）只产生有界影响，不毁掉模型。"""
    learner = PersonalModelLearner(config=LearnerConfig(max_relative_step=0.15))
    params = ModelParameters.population_prior()
    ds = CorrectionDataset()
    # 一条极端纠正
    ds.add_raw(1.0, 0.0, 1.0, weight=1.0, salience=1.0)
    new_params = learner.update(params, ds)
    # 参数仍应在合理范围（没有被拉到极端）
    assert new_params.ell_valence > 0
    assert new_params.sigma_valence > 0
    assert abs(new_params.ell_valence - params.ell_valence) / params.ell_valence <= 0.15 + 1e-6


def test_learner_gradual_convergence_over_many_steps():
    """渐进式个性化：多步累积后参数显著偏离群体先验（但每步有界）。"""
    learner = PersonalModelLearner(config=LearnerConfig(max_relative_step=0.2))
    params = ModelParameters.population_prior()
    pop = ModelParameters.population_prior()
    ds = CorrectionDataset()
    # 持续的系统性纠正（用户总把峰值拉高）
    for i in range(60):
        ds.add_raw(float(i), 0.4, 0.75, weight=1.0, salience=0.9)
    for _ in range(30):
        params = learner.update(params, ds)
    # 多步后 σ 应显著增大（用户总拉高峰值 → 模型幅度不够 → σ 增大）
    assert params.sigma_valence > pop.sigma_valence * 1.2


# ============================================================
# part1 §4.4 诊断表
# ============================================================


def test_learner_peak_pull_up_increases_sigma():
    """总把峰值拉高 → σ 被低估 → σ 增大（part1 §4.4 第1行）。"""
    learner = PersonalModelLearner()
    params = ModelParameters(n_events_fitted=50)  # 已有足够证据，弱收缩
    ds = CorrectionDataset()
    for i in range(50):
        ds.add_raw(float(i), predicted=0.5, corrected=0.85, weight=1.0, salience=0.9)
    new_params = learner.update(params, ds)
    assert new_params.sigma_valence > params.sigma_valence


def test_learner_peak_pull_down_decreases_sigma():
    """总把峰值拉平 → σ 被高估 → σ 减小（part1 §4.4 第1行反向）。"""
    learner = PersonalModelLearner()
    params = ModelParameters(n_events_fitted=50)
    ds = CorrectionDataset()
    for i in range(50):
        ds.add_raw(float(i), predicted=0.85, corrected=0.5, weight=1.0, salience=0.9)
    new_params = learner.update(params, ds)
    assert new_params.sigma_valence < params.sigma_valence


def test_learner_persistent_corrections_increase_ell():
    """纠正方向时间持续 → 模型缺少慢动态 → ℓ 增大（part1 §4.4 第2行）。"""
    learner = PersonalModelLearner()
    params = ModelParameters(n_events_fitted=50)
    ds = CorrectionDataset()
    # 持续正 delta（连续同向拉，autocorr > 0）
    for i in range(50):
        ds.add_raw(float(i), predicted=0.4, corrected=0.6 + 0.004 * i, weight=1.0, salience=0.5)
    assert ds.bias_statistics().delta_autocorr > 0.3
    new_params = learner.update(params, ds)
    assert new_params.ell_valence > params.ell_valence


def test_learner_alternating_corrections_decrease_ell():
    """纠正交替（来回拉）→ 模型过度平滑 → ℓ 减小（part1 §4.4 第3行）。"""
    learner = PersonalModelLearner()
    params = ModelParameters(n_events_fitted=50)
    ds = CorrectionDataset()
    for i in range(50):
        corr = 0.7 if i % 2 == 0 else 0.2
        ds.add_raw(float(i), predicted=0.45, corrected=corr, weight=1.0, salience=0.5)
    assert ds.bias_statistics().delta_autocorr < 0.0
    new_params = learner.update(params, ds)
    assert new_params.ell_valence < params.ell_valence


def test_learner_random_corrections_increase_noise():
    """拖动随机无模式 → σ_noise 偏小 → σ_noise 增大（part1 §4.4 第4行）。"""
    learner = PersonalModelLearner()
    params = ModelParameters(n_events_fitted=50)
    ds = CorrectionDataset()
    import random
    rng = random.Random(0)
    for i in range(60):
        # 随机 delta，均值≈0 但散度大（无系统模式）
        corr = 0.5 + rng.gauss(0, 0.25)
        ds.add_raw(float(i), predicted=0.5, corrected=min(1.0, max(0.0, corr)),
                   weight=1.0, salience=0.5)
    stats = ds.bias_statistics()
    assert stats.direction_consistency < 0.4  # 无系统方向
    assert stats.delta_scatter > 0.1          # 高散度
    new_params = learner.update(params, ds)
    assert new_params.sigma_noise > params.sigma_noise


# ============================================================
# part1 §8 阶段4：ℓ 能区分画像
# ============================================================


def test_learner_distinguishes_stable_vs_anxious_archetypes():
    """情绪稳定型（纠正持续，高惯性）学到的 ℓ 应大于焦虑敏感型（纠正交替，低惯性）。

    part1 §8 阶段4 验证：模拟器画像的 ℓ 是已知 ground truth，
    学习器应能恢复出稳定型 ℓ > 焦虑型 ℓ 的区分。
    """
    learner = PersonalModelLearner()

    # 稳定型：纠正方向持续（情绪惯性强，autocorr 高）
    ds_stable = CorrectionDataset()
    for i in range(50):
        ds_stable.add_raw(float(i), 0.45, 0.6 + 0.004 * i, weight=1.0, salience=0.5)
    p_stable = ModelParameters(n_events_fitted=50)
    for _ in range(15):
        p_stable = learner.update(p_stable, ds_stable)

    # 焦虑型：纠正交替（情绪快速波动，惯性弱，autocorr 负）
    ds_anxious = CorrectionDataset()
    for i in range(50):
        corr = 0.75 if i % 2 == 0 else 0.25
        ds_anxious.add_raw(float(i), 0.5, corr, weight=1.0, salience=0.5)
    p_anxious = ModelParameters(n_events_fitted=50)
    for _ in range(15):
        p_anxious = learner.update(p_anxious, ds_anxious)

    # 稳定型的 ℓ 应显著大于焦虑型
    assert p_stable.ell_valence > p_anxious.ell_valence


# ============================================================
# Calibrator 编排
# ============================================================


def test_calibrator_ingest_and_calibrate():
    """摄入纠正后，calibrate 应补偿系统性偏差。"""
    cal = Calibrator(reg_lambda=0.01)
    # 模型总低估 0.2
    for i in range(40):
        cal.ingest_raw(timestamp=float(i), predicted=0.4, corrected=0.6, weight=1.0)
    # 校准后应补偿
    assert cal.calibrate(0.4) == pytest.approx(0.6, abs=0.05)


def test_calibrator_ingest_user_correction():
    import time
    from emowave.core.domain.correction import (
        CorrectionDimension,
        CorrectionSource,
        UserCorrection,
    )
    now = time.time()
    cal = Calibrator(reg_lambda=0.01)
    for i in range(30):
        c = UserCorrection(
            timestamp=now - 30 + i,
            dimension=CorrectionDimension.VALENCE,
            predicted_value=0.4,
            corrected_value=0.65,
            source=CorrectionSource.SLIDER,
            salience=0.7,
            created_at=now,
        )
        cal.ingest_correction(c)
    assert len(cal.dataset) == 30
    assert cal.calibrate(0.4) > 0.5  # 向 0.65 补偿


def test_calibrator_learn_returns_new_params():
    cal = Calibrator()
    for i in range(50):
        cal.ingest_raw(float(i), 0.4, 0.75, weight=1.0, salience=0.9)
    params = cal.learn(ModelParameters.population_prior())
    assert isinstance(params, ModelParameters)
    assert params.n_events_fitted >= 1


def test_calibrator_bias_statistics_exposed():
    cal = Calibrator()
    for i in range(10):
        cal.ingest_raw(float(i), 0.3, 0.6, weight=1.0)
    stats = cal.bias_statistics()
    assert stats.weighted_mean_delta == pytest.approx(0.3)


def test_calibrator_evaluate_log_likelihood():
    cal = Calibrator()
    obs = make_obs(30, v=0.6, a=0.4)
    ll = cal.evaluate_log_likelihood(obs, ModelParameters())
    assert math.isfinite(ll)


def test_calibrator_error_decreases_end_to_end():
    """端到端验证 Phase 4 完成标准：修正越多，校准后误差下降。"""
    cal = Calibrator(reg_lambda=0.05)

    def user_truth(p):
        return min(1.0, p + 0.12 + 0.25 * p)

    held_out = [{"predicted": p, "corrected": user_truth(p), "weight": 1.0}
                for p in [0.3, 0.5, 0.7]]

    err_before = cal.residual_model.held_out_error(held_out)
    for round_idx in range(4):
        for p in [0.2, 0.35, 0.5, 0.65, 0.8]:
            cal.ingest_raw(timestamp=float(round_idx * 10 + int(p * 10)),
                           predicted=p, corrected=user_truth(p), weight=1.0)
    err_after = cal.residual_model.held_out_error(held_out)
    assert err_after < err_before
    assert err_after < 0.05


def test_calibrator_roundtrip():
    cal = Calibrator(reg_lambda=0.5)
    for i in range(10):
        cal.ingest_raw(float(i), 0.4, 0.6, weight=1.0)
    d = cal.to_dict()
    restored = Calibrator.from_dict(d, reg_lambda=0.5)
    assert len(restored.dataset) == len(cal.dataset)
    assert restored.calibrate(0.4) == pytest.approx(cal.calibrate(0.4))
