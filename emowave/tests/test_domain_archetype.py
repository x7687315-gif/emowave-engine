"""test_domain_archetype — 三类精力人群先验（默认曲线函数）的正确性。"""

import pytest

from emowave.core.domain.archetype import (
    EnergyArchetype,
    ARCHETYPES,
    ARCHETYPE_ORDER,
    DEFAULT_ARCHETYPE_KEY,
    get_archetype,
)
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.baseline import Baseline, BaselineSource


def test_three_archetypes_exist_in_order():
    assert set(ARCHETYPES) == {"high", "medium", "low"}
    assert [a.key for a in ARCHETYPE_ORDER] == ["high", "medium", "low"]


def test_default_is_medium_and_matches_population_prior():
    med = get_archetype("medium")
    params = med.to_params()
    pop = ModelParameters.population_prior()
    # 中精力刻意等于群体先验默认值（"不选=与旧行为一致"）
    assert params.ell_valence == pop.ell_valence
    assert params.ell_arousal == pop.ell_arousal
    assert params.sigma_valence == pop.sigma_valence
    assert params.sigma_arousal == pop.sigma_arousal
    assert params.sigma_noise == pop.sigma_noise
    assert DEFAULT_ARCHETYPE_KEY == "medium"


def test_unknown_key_falls_back_to_default():
    assert get_archetype("nope").key == DEFAULT_ARCHETYPE_KEY
    assert get_archetype(None if False else "").key == DEFAULT_ARCHETYPE_KEY


def test_energy_ordering_arousal_amplitude_inertia():
    hi, med, lo = (get_archetype(k) for k in ("high", "medium", "low"))
    # 精力≈唤醒：基线唤醒 高>中>低
    assert hi.baseline_arousal > med.baseline_arousal > lo.baseline_arousal
    # 波动幅度 σ 高>中>低
    assert hi.sigma_arousal > med.sigma_arousal > lo.sigma_arousal
    assert hi.sigma_valence > med.sigma_valence > lo.sigma_valence
    # 惯性 ℓ 高<中<低（高精力变化更快 → ℓ 更短）
    assert hi.ell_arousal < med.ell_arousal < lo.ell_arousal
    assert hi.ell_valence < med.ell_valence < lo.ell_valence


def test_to_params_and_to_baseline_types_and_values():
    hi = get_archetype("high")
    p = hi.to_params()
    b = hi.to_baseline()
    assert isinstance(p, ModelParameters) and isinstance(b, Baseline)
    assert p.ell_valence == hi.ell_valence
    assert p.sigma_arousal == hi.sigma_arousal
    assert b.valence == hi.baseline_valence
    assert b.arousal == hi.baseline_arousal
    assert b.source == BaselineSource.POPULATION


def test_archetype_params_are_valid_and_positive():
    for a in ARCHETYPE_ORDER:
        p = a.to_params()          # 构造即校验（ModelParameters.__post_init__）
        assert p.ell_valence > 0 and p.ell_arousal > 0
        assert p.sigma_valence > 0 and p.sigma_arousal > 0 and p.sigma_noise > 0
        assert 0.0 <= a.baseline_valence <= 1.0
        assert 0.0 <= a.baseline_arousal <= 1.0


def test_energyarchetype_is_frozen():
    with pytest.raises(Exception):
        get_archetype("high").label = "改一下"   # frozen dataclass 不可变
