"""ModelParameters 单元测试。

重点验证：
  - 群体先验默认值（冷启动可用）
  - Matérn ν=3/2 派生量：λ = √3/ℓ，自相关 ρ(τ) = (1+√3τ/ℓ)·exp(-√3τ/ℓ)
  - 层次先验收缩（ARCHITECTURE part1 §3.3）：n_events 越少收缩越强
  - 学习阶段自动推导
  - 不可变更新语义（with_updates）
  - 修正旧版 velocity_damping=0.85 隐含的 ℓ≈11-22s 记忆问题（part1 §1.2）
"""

import math

import pytest

from emowave.core.domain.model_parameters import (
    ModelParameters,
    LearningStage,
)


def test_model_parameters_defaults_are_population_prior():
    """默认值 = 群体先验（冷启动可用，REFACTOR_PLAN.md §9.1）。"""
    p = ModelParameters()
    assert p.ell_valence == pytest.approx(300.0)
    assert p.ell_arousal == pytest.approx(240.0)
    assert p.sigma_valence == pytest.approx(0.15)
    assert p.sigma_arousal == pytest.approx(0.20)
    assert p.sigma_noise == pytest.approx(0.10)
    assert p.n_events_fitted == 0
    assert p.stage == LearningStage.COLD_START
    assert p.shrinkage_alpha == 0.0


def test_model_parameters_population_prior_factory():
    """population_prior() 返回纯群体先验。"""
    p = ModelParameters.population_prior()
    assert p.n_events_fitted == 0
    assert p.stage == LearningStage.COLD_START


def test_model_parameters_fixes_memory_problem():
    """新版 ℓ=300s 修正旧版 velocity_damping=0.85 隐含的 ℓ≈11-22s 记忆问题。

    ARCHITECTURE part1 §1.2 实测表格（本次复算验证一致）：
        ℓ= 10s → ρ(60)=0.000   ← 旧版量级，模型认为 1 分钟前情绪与现在无关
        ℓ= 30s → ρ(60)=0.140
        ℓ= 60s → ρ(60)=0.483
        ℓ=300s → ρ(60)=0.952   ← 新版默认，强记忆
        ℓ=900s → ρ(60)=0.994

    旧版 velocity_damping=0.85 反推 ℓ≈10.7s（口径A）或 21.8s（口径B），
    在这个尺度下 60 秒后自相关≈0，导致 extrapolate(600s) 在数学上无意义，
    这是基准测试中预警精确率仅 0.198 的结构性原因（part1 §1.2）。
    """
    p = ModelParameters()  # 默认 ℓ_valence=300
    rho_60_new = p.autocorrelation("valence", 60.0)
    # 新版 ℓ=300s：60 秒后自相关 ≈ 0.952（强记忆）
    assert rho_60_new == pytest.approx(0.952, abs=0.01)

    # 对照：旧版隐含 ℓ≈15s（取 part1 §1.2 两口径中值），60 秒后自相关≈0
    p_old = ModelParameters(ell_valence=15.0)
    rho_60_old = p_old.autocorrelation("valence", 60.0)
    assert rho_60_old < 0.01  # 旧版"记忆问题"的量化证据

    # 新版相对旧版的记忆改善是数量级的
    assert rho_60_new > rho_60_old + 0.9


def test_lambda_is_sqrt3_over_ell():
    """Matérn ν=3/2 的 λ = √3 / ℓ（ARCHITECTURE part1 §2.4）。"""
    p = ModelParameters(ell_valence=300.0, ell_arousal=240.0)
    assert p.lambda_valence() == pytest.approx(math.sqrt(3.0) / 300.0)
    assert p.lambda_arousal() == pytest.approx(math.sqrt(3.0) / 240.0)


def test_autocorrelation_at_zero_lag_is_one():
    """τ=0 时自相关 = 1（任何 ℓ）。"""
    p = ModelParameters()
    assert p.autocorrelation("valence", 0.0) == pytest.approx(1.0)
    assert p.autocorrelation("arousal", 0.0) == pytest.approx(1.0)


def test_autocorrelation_decays_with_lag():
    """自相关随 lag 单调衰减。"""
    p = ModelParameters(ell_valence=300.0)
    lags = [0.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0]
    rhos = [p.autocorrelation("valence", lag) for lag in lags]
    assert rhos == sorted(rhos, reverse=True)
    # ℓ=300 时，lag=300 的自相关 = (1+√3)·exp(-√3) ≈ 0.483
    # （τ=ℓ 是 Matérn ν=3/2 的特征衰减点，不是 e^-1≈0.37 而是 0.483）
    assert rhos[4] == pytest.approx(0.483, abs=0.01)


def test_autocorrelation_larger_ell_decays_slower():
    """ℓ 越大（情绪惯性越强），自相关衰减越慢。"""
    p_short = ModelParameters(ell_valence=60.0)
    p_long = ModelParameters(ell_valence=600.0)
    # 在 lag=300 时，长 ℓ 的自相关应显著高于短 ℓ
    assert p_long.autocorrelation("valence", 300.0) > p_short.autocorrelation(
        "valence", 300.0
    )


def test_model_parameters_rejects_nonpositive_ell():
    """ℓ 必须为正（长度尺度不能 ≤ 0）。"""
    with pytest.raises(ValueError):
        ModelParameters(ell_valence=0.0)
    with pytest.raises(ValueError):
        ModelParameters(ell_arousal=-100.0)


def test_model_parameters_rejects_nonpositive_sigma():
    with pytest.raises(ValueError):
        ModelParameters(sigma_valence=0.0)
    with pytest.raises(ValueError):
        ModelParameters(sigma_noise=-0.1)


def test_model_parameters_clips_extreme_ell():
    """ℓ 超过 3600s（1 小时）应被裁剪，防数值爆炸。"""
    p = ModelParameters(ell_valence=10_000.0)
    assert p.ell_valence == 3600.0


def test_model_parameters_clips_extreme_sigma():
    p = ModelParameters(sigma_valence=100.0)
    assert p.sigma_valence == 5.0


def test_model_parameters_rejects_negative_n_events():
    with pytest.raises(ValueError):
        ModelParameters(n_events_fitted=-1)


# ============================================================
# 学习阶段自动推导
# ============================================================


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, LearningStage.COLD_START),
        (4, LearningStage.COLD_START),
        (5, LearningStage.EARLY),
        (19, LearningStage.EARLY),
        (20, LearningStage.TRANSITION),
        (39, LearningStage.TRANSITION),
        (40, LearningStage.PERSONAL),
        (1000, LearningStage.PERSONAL),
    ],
)
def test_stage_derived_from_n_events(n, expected):
    p = ModelParameters(n_events_fitted=n)
    assert p.stage == expected


def test_stage_overrides_inconsistent_input():
    """若显式传入的 stage 与 n_events 不一致，以 n_events 为准（防御性）。"""
    p = ModelParameters(n_events_fitted=100, stage=LearningStage.COLD_START)
    assert p.stage == LearningStage.PERSONAL


# ============================================================
# 层次先验收缩（ARCHITECTURE part1 §3.3，§9 风险表）
# ============================================================


def test_shrinkage_cold_start_is_pure_population():
    """n < 5：纯群体先验（shrinkage weight = 1.0）。"""
    for n in [0, 1, 2, 3, 4]:
        p = ModelParameters(n_events_fitted=n)
        assert p.population_shrinkage_weight() == 1.0


def test_shrinkage_early_is_strong():
    """5 ≤ n < 20：强收缩（0.8）。前 20 事件强制强收缩防过拟合。"""
    for n in [5, 10, 15, 19]:
        p = ModelParameters(n_events_fitted=n)
        assert p.population_shrinkage_weight() == pytest.approx(0.8)


def test_shrinkage_transition_is_linear():
    """20 ≤ n < 40：线性从 0.8 → 0.2。"""
    p20 = ModelParameters(n_events_fitted=20)
    p30 = ModelParameters(n_events_fitted=30)
    p39 = ModelParameters(n_events_fitted=39)
    assert p20.population_shrinkage_weight() == pytest.approx(0.8)
    assert p30.population_shrinkage_weight() == pytest.approx(0.5, abs=0.02)
    assert p39.population_shrinkage_weight() == pytest.approx(0.22, abs=0.02)


def test_shrinkage_personal_keeps_population_anchor():
    """n ≥ 40：仍保留 20% 群体锚点，防止长期漂移。"""
    p = ModelParameters(n_events_fitted=1000)
    assert p.population_shrinkage_weight() == pytest.approx(0.2)


def test_shrinkage_monotonically_decreasing():
    """shrinkage weight 随 n_events 单调不增（个人证据越多，群体权重越低）。"""
    ns = [0, 5, 10, 20, 30, 40, 100, 1000]
    ws = [ModelParameters(n_events_fitted=n).population_shrinkage_weight() for n in ns]
    # 非严格单调（分段常数 + 线性），但整体不增
    for i in range(len(ws) - 1):
        assert ws[i] >= ws[i + 1] - 1e-9


# ============================================================
# with_updates 不可变更新
# ============================================================


def test_with_updates_creates_new_version():
    p1 = ModelParameters(ell_valence=300.0, n_events_fitted=10)
    p2 = p1.with_updates(ell_valence=350.0, n_events_fitted=11)

    assert p2 is not p1
    assert p2.version == p1.version + 1
    assert p2.parent_version == p1.version
    assert p2.ell_valence == 350.0
    assert p2.n_events_fitted == 11
    # 未变的字段保留
    assert p2.sigma_valence == p1.sigma_valence
    # 旧对象不变
    assert p1.ell_valence == 300.0
    assert p1.version == 1


def test_with_updates_preserves_regime_id():
    p1 = ModelParameters(regime_id="regime_abc")
    p2 = p1.with_updates(ell_valence=400.0)
    assert p2.regime_id == "regime_abc"


def test_model_parameters_is_frozen():
    p = ModelParameters()
    with pytest.raises(Exception):
        p.ell_valence = 500.0  # type: ignore[misc]


# ============================================================
# 序列化
# ============================================================


def test_model_parameters_roundtrip():
    p = ModelParameters(
        ell_valence=320.0,
        ell_arousal=260.0,
        sigma_valence=0.18,
        sigma_arousal=0.22,
        sigma_noise=0.12,
        n_events_fitted=25,
        log_likelihood=-42.5,
        shrinkage_alpha=0.65,
        regime_id="regime_xyz",
        version=7,
        extra={"coupling_va": 0.15},
    )
    d = p.to_dict()
    assert d["stage"] == "transition"  # Enum → 字符串
    restored = ModelParameters.from_dict(d)
    assert restored.ell_valence == p.ell_valence
    assert restored.n_events_fitted == p.n_events_fitted
    assert restored.stage == p.stage
    assert restored.regime_id == p.regime_id
    assert restored.version == p.version
    assert restored.extra == p.extra


def test_model_parameters_from_dict_tolerates_unknown_fields():
    d = {
        "ell_valence": 300.0,
        "future_param": 1.23,
    }
    p = ModelParameters.from_dict(d)
    assert p.ell_valence == 300.0
