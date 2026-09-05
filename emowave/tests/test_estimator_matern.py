"""Matérn ν=3/2 状态空间闭式解单元测试。

重点验证 ARCHITECTURE part1 §2.4 的数学正确性：
  - F(Δt) = e^(-λΔt)·[[1+λΔt, Δt], [-λ²Δt, 1-λΔt]]
  - Δt→0 时 F→I
  - dF/dΔt|₀ = A = [[0,1],[-λ²,-2λ]]
  - P∞ = σ²·[[1,0],[0,λ²]]
  - Q(Δt) = P∞ - F·P∞·Fᵀ，且 Q(0)=0，Q(∞)→P∞
"""

import math

import pytest

from emowave.core import linalg
from emowave.core.estimator.matern import (
    OBSERVATION_MATRIX,
    build_process_noise,
    build_stationary_covariance,
    build_transition,
    matern_process_noise,
    matern_stationary_covariance,
    matern_transition,
)


# ============================================================
# matern_transition：F(Δt) 闭式解
# ============================================================


def test_transition_at_zero_dt_is_identity():
    """Δt→0 时 F→I（part1 §2.4 已验证性质）。"""
    lam = math.sqrt(3.0) / 300.0
    f = matern_transition(lam, 0.0)
    assert f[0][0] == pytest.approx(1.0, abs=1e-12)
    assert f[0][1] == pytest.approx(0.0, abs=1e-12)
    assert f[1][0] == pytest.approx(0.0, abs=1e-12)
    assert f[1][1] == pytest.approx(1.0, abs=1e-12)


def test_transition_derivative_at_zero_equals_A():
    """dF/dΔt|₀ = A = [[0,1],[-λ²,-2λ]]（part1 §2.4 已验证性质）。

    用数值微分（小 Δt 的差分）验证闭式解的导数等于连续状态矩阵 A。
    """
    lam = math.sqrt(3.0) / 300.0
    h = 1e-6
    f0 = matern_transition(lam, 0.0)
    fh = matern_transition(lam, h)
    # 数值导数 (F(h) - F(0)) / h
    d00 = (fh[0][0] - f0[0][0]) / h
    d01 = (fh[0][1] - f0[0][1]) / h
    d10 = (fh[1][0] - f0[1][0]) / h
    d11 = (fh[1][1] - f0[1][1]) / h
    # A = [[0, 1], [-λ², -2λ]]
    assert d00 == pytest.approx(0.0, abs=1e-4)
    assert d01 == pytest.approx(1.0, abs=1e-4)
    assert d10 == pytest.approx(-lam * lam, abs=1e-4)
    assert d11 == pytest.approx(-2.0 * lam, abs=1e-4)


def test_transition_decays_with_dt():
    """F 的元素随 Δt 增大而衰减（e^(-λΔt) 主导）。"""
    lam = math.sqrt(3.0) / 60.0  # 短 ℓ，衰减快
    f_small = matern_transition(lam, 1.0)
    f_large = matern_transition(lam, 1000.0)
    # 大 Δt 时 F 应趋近 0
    assert abs(f_large[0][0]) < abs(f_small[0][0])
    assert abs(f_large[0][0]) < 1e-6


def test_transition_rejects_negative_dt():
    with pytest.raises(ValueError):
        matern_transition(0.01, -1.0)


def test_transition_rejects_nonpositive_lambda():
    with pytest.raises(ValueError):
        matern_transition(0.0, 1.0)
    with pytest.raises(ValueError):
        matern_transition(-0.01, 1.0)


# ============================================================
# matern_stationary_covariance：P∞
# ============================================================


def test_stationary_covariance_structure():
    """P∞ = σ²·[[1,0],[0,λ²]]。"""
    sigma = 0.15
    lam = math.sqrt(3.0) / 300.0
    p = matern_stationary_covariance(sigma, lam)
    assert p[0][0] == pytest.approx(sigma ** 2)
    assert p[0][1] == pytest.approx(0.0)
    assert p[1][0] == pytest.approx(0.0)
    assert p[1][1] == pytest.approx(sigma ** 2 * lam ** 2)


def test_stationary_covariance_rejects_nonpositive():
    with pytest.raises(ValueError):
        matern_stationary_covariance(0.0, 0.01)
    with pytest.raises(ValueError):
        matern_stationary_covariance(0.1, 0.0)


# ============================================================
# matern_process_noise：Q(Δt) = P∞ - F·P∞·Fᵀ
# ============================================================


def test_process_noise_at_zero_dt_is_zero():
    """Δt=0 时 Q=0（无时间流逝，无过程噪声）。"""
    sigma = 0.15
    lam = math.sqrt(3.0) / 300.0
    q = matern_process_noise(sigma, lam, 0.0)
    for i in range(2):
        for j in range(2):
            assert q[i][j] == pytest.approx(0.0, abs=1e-12)


def test_process_noise_at_large_dt_approaches_stationary():
    """Δt→∞ 时 Q→P∞（完全去相关，方差=平稳方差）。"""
    sigma = 0.15
    lam = math.sqrt(3.0) / 60.0
    q = matern_process_noise(sigma, lam, 100000.0)
    p_inf = matern_stationary_covariance(sigma, lam)
    for i in range(2):
        for j in range(2):
            assert q[i][j] == pytest.approx(p_inf[i][j], abs=1e-6)


def test_process_noise_is_symmetric():
    """Q 必须对称（协方差矩阵性质）。"""
    sigma = 0.2
    lam = math.sqrt(3.0) / 120.0
    q = matern_process_noise(sigma, lam, 5.0)
    assert q[0][1] == pytest.approx(q[1][0], abs=1e-12)


def test_process_noise_is_positive_semidefinite():
    """Q 半正定：对角元非负，行列式非负。"""
    sigma = 0.15
    lam = math.sqrt(3.0) / 300.0
    for dt in [0.1, 1.0, 10.0, 100.0]:
        q = matern_process_noise(sigma, lam, dt)
        assert q[0][0] >= -1e-12
        assert q[1][1] >= -1e-12
        det = q[0][0] * q[1][1] - q[0][1] * q[1][0]
        assert det >= -1e-12


def test_process_noise_monotonic_in_dt():
    """Q 的位置方差随 Δt 单调不减（时间越长积累越多噪声）。"""
    sigma = 0.15
    lam = math.sqrt(3.0) / 300.0
    dts = [0.0, 1.0, 10.0, 100.0, 1000.0]
    q00 = [matern_process_noise(sigma, lam, dt)[0][0] for dt in dts]
    for i in range(len(q00) - 1):
        assert q00[i] <= q00[i + 1] + 1e-12


def test_process_noise_rejects_negative_dt():
    with pytest.raises(ValueError):
        matern_process_noise(0.15, 0.01, -1.0)


# ============================================================
# 双通道 4×4 构造
# ============================================================


def test_build_transition_is_block_diagonal():
    """4×4 F 是块对角：[v,v̇] 与 [a,ȧ] 不耦合。"""
    lam_v = math.sqrt(3.0) / 300.0
    lam_a = math.sqrt(3.0) / 240.0
    f = build_transition(lam_v, lam_a, 1.0)
    # 非对角块应为 0
    assert f[0][2] == 0.0 and f[0][3] == 0.0
    assert f[1][2] == 0.0 and f[1][3] == 0.0
    assert f[2][0] == 0.0 and f[2][1] == 0.0
    assert f[3][0] == 0.0 and f[3][1] == 0.0
    # 对角块应等于单通道 F
    fv = matern_transition(lam_v, 1.0)
    fa = matern_transition(lam_a, 1.0)
    assert f[0][0] == pytest.approx(fv[0][0])
    assert f[1][1] == pytest.approx(fv[1][1])
    assert f[2][2] == pytest.approx(fa[0][0])
    assert f[3][3] == pytest.approx(fa[1][1])


def test_build_transition_different_lambdas():
    """valence 与 arousal 用不同 ℓ 时，两个对角块不同。"""
    lam_v = math.sqrt(3.0) / 300.0
    lam_a = math.sqrt(3.0) / 100.0  # arousal 衰减更快
    f = build_transition(lam_v, lam_a, 10.0)
    # arousal 块衰减更快 → f[2][2] < f[0][0]
    assert f[2][2] < f[0][0]


def test_build_stationary_covariance_block_diagonal():
    lam_v = math.sqrt(3.0) / 300.0
    lam_a = math.sqrt(3.0) / 240.0
    p = build_stationary_covariance(0.15, 0.20, lam_v, lam_a)
    assert p[0][0] == pytest.approx(0.15 ** 2)
    assert p[2][2] == pytest.approx(0.20 ** 2)
    # 跨通道协方差为 0
    assert p[0][2] == 0.0
    assert p[1][3] == 0.0


def test_build_process_noise_block_diagonal():
    lam_v = math.sqrt(3.0) / 300.0
    lam_a = math.sqrt(3.0) / 240.0
    q = build_process_noise(0.15, 0.20, lam_v, lam_a, 5.0)
    assert q[0][2] == 0.0
    assert q[1][3] == 0.0
    # 对角块非负
    assert q[0][0] >= -1e-12
    assert q[2][2] >= -1e-12


# ============================================================
# 观测矩阵 H
# ============================================================


def test_observation_matrix_selects_positions():
    """H 观测 v（索引0）和 a（索引2），不观测速度。"""
    h = OBSERVATION_MATRIX
    assert h == [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
    ]
    # H @ [v, dv, a, da] = [v, a]
    x = [0.3, 0.01, 0.7, -0.02]
    z = linalg.mul_vec(h, x)
    assert z == [pytest.approx(0.3), pytest.approx(0.7)]
