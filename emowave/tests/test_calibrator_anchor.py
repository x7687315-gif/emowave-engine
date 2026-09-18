"""test_calibrator_anchor — 层次收缩锚点可注入（向所选人群先验收缩）。"""

from emowave.core.calibration.calibrator import Calibrator
from emowave.core.domain.model_parameters import ModelParameters


def test_default_anchor_is_population_prior():
    cal = Calibrator()
    pop = ModelParameters.population_prior()
    assert cal._population.ell_valence == pop.ell_valence
    assert cal._population.ell_arousal == pop.ell_arousal


def test_calibrator_accepts_custom_population_anchor():
    anchor = ModelParameters(ell_valence=123.0, ell_arousal=99.0,
                             sigma_valence=0.2, sigma_arousal=0.3, sigma_noise=0.1)
    cal = Calibrator(population=anchor)
    assert cal._population.ell_valence == 123.0
    assert cal._population.ell_arousal == 99.0


def test_learn_uses_injected_anchor_when_no_current_params():
    """无当前参数时 learn 以注入的锚为起点（冷启动=该人群先验）。"""
    anchor = ModelParameters(ell_valence=240.0, ell_arousal=180.0,
                             sigma_valence=0.2, sigma_arousal=0.28, sigma_noise=0.1)
    cal = Calibrator(population=anchor)
    out = cal.learn()          # params=None → 以 _population 起
    assert out.ell_valence == 240.0
    assert out.ell_arousal == 180.0
