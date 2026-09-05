"""CorrectionDataset + BiasStatistics + OnlineResidualRegression 单元测试。

覆盖 REFACTOR_PLAN.md §28 Phase 4：
  - 保存 UserCorrection / 建立 correction dataset
  - 统计系统性模型偏差（part1 §4.4 诊断信号）
  - 实现 Online Regression（§9.3 Online Ridge Regression）
  - 验证"修正越多，误差是否下降"（Phase 4 完成标准）
"""

import math

import pytest

from emowave.core.calibration.corrections import (
    BiasStatistics,
    CorrectionDataset,
    OnlineResidualRegression,
)
from emowave.core.domain.correction import (
    CorrectionDimension,
    CorrectionSource,
    UserCorrection,
)


# ============================================================
# CorrectionDataset
# ============================================================


def test_dataset_starts_empty():
    ds = CorrectionDataset()
    assert len(ds) == 0
    assert ds.effective_sample_size() == 0.0


def test_dataset_add_raw():
    ds = CorrectionDataset()
    ds.add_raw(timestamp=1.0, predicted=0.4, corrected=0.6, weight=1.0, salience=0.8)
    assert len(ds) == 1
    assert ds.records[0]["delta"] == pytest.approx(0.2)


def test_dataset_add_user_correction():
    import time
    now = time.time()
    c = UserCorrection(
        timestamp=now - 10,
        dimension=CorrectionDimension.VALENCE,
        predicted_value=0.3,
        corrected_value=0.7,
        source=CorrectionSource.SLIDER,
        salience=0.9,
        created_at=now,
    )
    ds = CorrectionDataset()
    ds.add_user_correction(c)
    assert len(ds) == 1
    assert ds.records[0]["delta"] == pytest.approx(0.4)
    assert ds.records[0]["weight"] == pytest.approx(c.reliability_weight)


def test_dataset_effective_sample_size_is_weighted_sum():
    ds = CorrectionDataset()
    ds.add_raw(1.0, 0.4, 0.6, weight=0.8)
    ds.add_raw(2.0, 0.4, 0.5, weight=0.5)
    ds.add_raw(3.0, 0.4, 0.7, weight=1.0)
    assert ds.effective_sample_size() == pytest.approx(2.3)


def test_dataset_records_are_copies():
    """records 返回拷贝，外部修改不污染内部。"""
    ds = CorrectionDataset()
    ds.add_raw(1.0, 0.4, 0.6)
    recs = ds.records
    recs[0]["delta"] = 999.0
    assert ds.records[0]["delta"] != 999.0


def test_dataset_clear():
    ds = CorrectionDataset()
    ds.add_raw(1.0, 0.4, 0.6)
    ds.clear()
    assert len(ds) == 0


# ============================================================
# BiasStatistics（part1 §4.4 诊断信号）
# ============================================================


def test_bias_stats_empty_dataset():
    ds = CorrectionDataset()
    stats = ds.bias_statistics()
    assert stats.n_corrections == 0
    assert stats.weighted_mean_delta == 0.0


def test_bias_stats_systematic_underestimate():
    """用户总是拉高（corrected > predicted）→ weighted_mean_delta > 0（模型低估）。"""
    ds = CorrectionDataset()
    for i in range(10):
        ds.add_raw(float(i), predicted=0.3, corrected=0.6, weight=1.0, salience=0.5)
    stats = ds.bias_statistics()
    assert stats.weighted_mean_delta == pytest.approx(0.3)
    assert stats.weighted_mean_delta > 0


def test_bias_stats_systematic_overestimate():
    """用户总是拉低 → weighted_mean_delta < 0（模型高估）。"""
    ds = CorrectionDataset()
    for i in range(10):
        ds.add_raw(float(i), predicted=0.8, corrected=0.5, weight=1.0, salience=0.5)
    stats = ds.bias_statistics()
    assert stats.weighted_mean_delta == pytest.approx(-0.3)


def test_bias_stats_direction_consistency_high_when_systematic():
    """所有纠正同向 → direction_consistency 接近 1（强系统性）。"""
    ds = CorrectionDataset()
    for i in range(10):
        ds.add_raw(float(i), predicted=0.3, corrected=0.6, weight=1.0)
    stats = ds.bias_statistics()
    assert stats.direction_consistency == pytest.approx(1.0, abs=0.01)


def test_bias_stats_direction_consistency_low_when_random():
    """纠正正负抵消 → direction_consistency 接近 0（随机无模式）。"""
    ds = CorrectionDataset()
    for i in range(10):
        corr = 0.7 if i % 2 == 0 else 0.1  # 交替拉高拉低
        ds.add_raw(float(i), predicted=0.4, corrected=corr, weight=1.0)
    stats = ds.bias_statistics()
    assert stats.direction_consistency < 0.3


def test_bias_stats_peak_delta_isolates_high_salience():
    """peak_weighted_delta 只统计高 salience（峰值）纠正 → σ 信号。"""
    ds = CorrectionDataset()
    # 峰值纠正：拉高 0.3（高 salience）
    for i in range(5):
        ds.add_raw(float(i), predicted=0.5, corrected=0.8, weight=1.0, salience=0.9)
    # 平淡纠正：拉低 0.1（低 salience，不应进 peak 统计）
    for i in range(5, 10):
        ds.add_raw(float(i), predicted=0.5, corrected=0.4, weight=1.0, salience=0.1)
    stats = ds.bias_statistics()
    # peak 只含高 salience 的 +0.3
    assert stats.peak_weighted_delta == pytest.approx(0.3)
    # 全局 mean 被平淡纠正稀释
    assert stats.weighted_mean_delta < stats.peak_weighted_delta


def test_bias_stats_scatter_high_when_variable():
    """delta 离散度大 → delta_scatter 大（σ_noise 信号）。"""
    ds = CorrectionDataset()
    for i in range(10):
        corr = 0.2 + 0.08 * i  # delta 从 -0.2 到 +0.5，高度离散
        ds.add_raw(float(i), predicted=0.4, corrected=corr, weight=1.0)
    stats = ds.bias_statistics()
    assert stats.delta_scatter > 0.1


def test_bias_stats_scatter_low_when_consistent():
    ds = CorrectionDataset()
    for i in range(10):
        ds.add_raw(float(i), predicted=0.4, corrected=0.6, weight=1.0)
    stats = ds.bias_statistics()
    assert stats.delta_scatter == pytest.approx(0.0, abs=1e-9)


def test_bias_stats_autocorr_positive_when_persistent():
    """纠正方向时间持续（连续同向）→ delta_autocorr > 0（ℓ 应增大信号）。"""
    ds = CorrectionDataset()
    # 持续正 delta（用户连续多步都把曲线往上拉）
    for i in range(20):
        ds.add_raw(float(i), predicted=0.4, corrected=0.6 + 0.005 * i, weight=1.0)
    stats = ds.bias_statistics()
    assert stats.delta_autocorr > 0.3


def test_bias_stats_autocorr_negative_when_alternating():
    """纠正交替（来回拉）→ delta_autocorr < 0（ℓ 应减小信号）。"""
    ds = CorrectionDataset()
    for i in range(20):
        corr = 0.7 if i % 2 == 0 else 0.2  # 严格交替
        ds.add_raw(float(i), predicted=0.45, corrected=corr, weight=1.0)
    stats = ds.bias_statistics()
    assert stats.delta_autocorr < 0.0


def test_bias_stats_weighted_by_reliability():
    """高权重纠正对统计贡献更大。"""
    ds = CorrectionDataset()
    # 一条高权重大 delta + 多条低权重小 delta
    ds.add_raw(1.0, predicted=0.4, corrected=0.9, weight=1.0)   # delta +0.5, w=1
    for i in range(10):
        ds.add_raw(float(i + 2), predicted=0.4, corrected=0.42, weight=0.05)  # delta +0.02, w=0.05
    stats = ds.bias_statistics()
    # 高权重的 +0.5 应主导
    assert stats.weighted_mean_delta > 0.2


# ============================================================
# OnlineResidualRegression（REFACTOR_PLAN.md §9.3）
# ============================================================


def test_regression_starts_with_zero_correction():
    reg = OnlineResidualRegression()
    assert reg.predict_residual(0.5) == 0.0
    assert reg.apply(0.5) == pytest.approx(0.5)


def test_regression_learns_constant_bias():
    """学习系统性偏移：模型总低估 0.2 → 回归应补偿 +0.2。"""
    reg = OnlineResidualRegression(reg_lambda=0.01)
    for _ in range(50):
        reg.update(predicted=0.4, corrected=0.6, weight=1.0)
    # 在 0.4 处应补偿约 +0.2
    assert reg.predict_residual(0.4) == pytest.approx(0.2, abs=0.03)
    assert reg.apply(0.4) == pytest.approx(0.6, abs=0.03)


def test_regression_learns_linear_bias():
    """学习线性残差：corrected = predicted×1.2 - 0.1。"""
    reg = OnlineResidualRegression(reg_lambda=0.01)
    for p in [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        c = min(1.0, p * 1.2 - 0.1)
        for _ in range(10):
            reg.update(predicted=p, corrected=c, weight=1.0)
    # 在未见过的 0.55 处验证泛化
    expected = 0.55 * 1.2 - 0.1  # 0.56
    assert reg.apply(0.55) == pytest.approx(expected, abs=0.05)


def test_regression_error_decreases_with_more_corrections():
    """Phase 4 完成标准：修正越多，误差下降。

    模拟一个有系统性偏差的用户（corrected = pred + 0.15 + 0.3×pred），
    随着纠正累积，回归在 held-out 样本上的误差应单调下降。
    """
    reg = OnlineResidualRegression(reg_lambda=0.05)

    def true_correction(p):
        return min(1.0, p + 0.15 + 0.3 * p)

    # held-out 验证集
    held_out = [
        {"predicted": p, "corrected": true_correction(p), "weight": 1.0}
        for p in [0.25, 0.45, 0.65]
    ]

    err_before = reg.held_out_error(held_out)
    errors = [err_before]
    # 逐步喂入纠正
    for round_idx in range(5):
        for p in [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]:
            reg.update(predicted=p, corrected=true_correction(p), weight=1.0)
        errors.append(reg.held_out_error(held_out))

    # 误差应整体下降
    assert errors[-1] < errors[0]
    # 最终误差应很小（学到了系统偏差）
    assert errors[-1] < 0.05
    # 单调不增（允许微小波动）
    for i in range(len(errors) - 1):
        assert errors[i + 1] <= errors[i] + 1e-6


def test_regression_weighted_by_reliability():
    """高权重样本影响更大（Coactive：随意拖动权重低，影响有界）。"""
    reg = OnlineResidualRegression(reg_lambda=0.01)
    # 高权重：补偿 +0.3
    for _ in range(20):
        reg.update(predicted=0.4, corrected=0.7, weight=1.0)
    r_high = reg.predict_residual(0.4)

    reg2 = OnlineResidualRegression(reg_lambda=0.01)
    # 低权重：同样的 delta 但权重 0.1
    for _ in range(20):
        reg2.update(predicted=0.4, corrected=0.7, weight=0.1)
    r_low = reg2.predict_residual(0.4)

    # 高权重学到的修正应更强（低权重被正则拉向 0）
    assert r_high > r_low


def test_regression_zero_weight_ignored():
    """零权重样本不贡献（Coactive 有界化极端情形）。"""
    reg = OnlineResidualRegression()
    reg.update(predicted=0.4, corrected=0.9, weight=0.0)
    assert reg.n_updates == 0
    assert reg.predict_residual(0.4) == 0.0


def test_regression_bounded_correction():
    """修正量裁剪到 ±max_correction（Coactive 有界，part1 §4.3）。"""
    reg = OnlineResidualRegression(reg_lambda=0.001, max_correction=0.2)
    # 极端 delta，但修正应被裁剪到 0.2
    for _ in range(50):
        reg.update(predicted=0.1, corrected=0.99, weight=1.0)
    assert reg.predict_residual(0.1) <= 0.2 + 1e-9
    assert reg.apply(0.1) <= 0.1 + 0.2 + 1e-9


def test_regression_apply_stays_in_unit_interval():
    reg = OnlineResidualRegression(reg_lambda=0.01)
    for _ in range(30):
        reg.update(predicted=0.9, corrected=1.0, weight=1.0)
    result = reg.apply(0.95)
    assert 0.0 <= result <= 1.0


def test_regression_single_extreme_sample_bounded():
    """单次极端拖动不毁掉模型（Coactive，part1 §4.3）。"""
    reg = OnlineResidualRegression(reg_lambda=1.0, max_correction=0.25)
    # 先学一个稳定的小偏差
    for _ in range(30):
        reg.update(predicted=0.5, corrected=0.55, weight=1.0)
    r_before = reg.predict_residual(0.5)
    # 单次极端拖动（权重 1，delta 巨大）
    reg.update(predicted=0.5, corrected=1.0, weight=1.0)
    r_after = reg.predict_residual(0.5)
    # 有界：单次极端样本不应让修正暴增超过 max_correction
    assert abs(r_after) <= 0.25 + 1e-9
    # 且变化有界（不会从 0.05 跳到 0.5）
    assert abs(r_after - r_before) < 0.25


def test_regression_roundtrip():
    reg = OnlineResidualRegression(reg_lambda=0.5, max_correction=0.4)
    for _ in range(10):
        reg.update(predicted=0.4, corrected=0.6, weight=1.0)
    d = reg.to_dict()
    restored = OnlineResidualRegression.from_dict(d)
    assert restored.n_updates == reg.n_updates
    assert restored.predict_residual(0.4) == pytest.approx(reg.predict_residual(0.4))


def test_regression_rejects_negative_lambda():
    with pytest.raises(ValueError):
        OnlineResidualRegression(reg_lambda=-1.0)
