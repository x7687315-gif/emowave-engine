"""matern — Matérn ν=3/2 状态空间形式的闭式解。

ARCHITECTURE part1 §2 的核心定理（Hartikainen & Särkkä 2010，219 引用）：
一维时序高斯过程回归，若核函数谱密度是有理函数（Matérn 族满足），
可精确转化为线性随机微分方程，其解由 Kalman 滤波 + RTS 平滑给出，
复杂度 O(N) 而非 O(N³)。

**这意味着不需要在 Kalman 和 GP 之间取舍：Kalman 是计算后端，GP 是数学语义。**

天作之合（part1 §2.3）：现有状态向量 [v, a, v̇, ȧ] 与 Matérn ν=3/2 的
[v, v̇, a, ȧ] 完全同构（仅顺序不同）。因此迁移不是"重写"而是"参数化升级"。

本模块采用 Matérn 标准排序 x = [v, v̇, a, ȧ]（按通道分组），使转移矩阵
F 呈块对角（每通道一个 2×2 块），结构更清晰。

单通道连续状态矩阵（part1 §2.4）：
    A = [[0, 1], [-λ², -2λ]]，  λ = √3 / ℓ

离散转移矩阵闭式解（已验证 Δt→0 时 F→I 且 dF/dΔt|₀ = A）：
    F(Δt) = e^(-λΔt) · [[1+λΔt, Δt], [-λ²Δt, 1-λΔt]]

稳态协方差与过程噪声：
    P∞ = σ² · [[1, 0], [0, λ²]]
    Q(Δt) = P∞ - F(Δt)·P∞·F(Δt)ᵀ

Q(Δt) 公式的自洽性：若状态服从平稳分布 P∞，经 F(Δt) 传播后协方差变为
F·P∞·Fᵀ，为维持平稳性需补充 Q = P∞ - F·P∞·Fᵀ。
  - Δt→0：F→I ⇒ Q→0（无时间流逝，无过程噪声）✓
  - Δt→∞：F→0 ⇒ Q→P∞（完全去相关，方差=平稳方差）✓
"""

from __future__ import annotations

import math
from typing import List

from emowave.core import linalg

# 观测矩阵 H：观测 v 和 a 的位置（索引 0 和 2），不观测速度。
# 状态 x = [v, v̇, a, ȧ]，观测 z = [v, a]。
OBSERVATION_MATRIX: List[List[float]] = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
]


def matern_transition(lam: float, dt: float) -> List[List[float]]:
    """单通道 Matérn ν=3/2 离散转移矩阵 F(Δt)（2×2）。

    F(Δt) = e^(-λΔt) · [[1+λΔt, Δt], [-λ²Δt, 1-λΔt]]

    Args:
        lam: λ = √3 / ℓ，衰减率（1/秒）
        dt: 时间步长（秒），必须 ≥ 0

    Returns:
        2×2 转移矩阵
    """
    if dt < 0:
        raise ValueError(f"matern_transition: dt 不能为负，得到 {dt}")
    if lam <= 0:
        raise ValueError(f"matern_transition: λ 必须为正，得到 {lam}")

    x = lam * dt
    decay = math.exp(-x)
    # F = decay · [[1+x, dt], [-λ²·dt, 1-x]]
    return [
        [decay * (1.0 + x), decay * dt],
        [decay * (-lam * lam * dt), decay * (1.0 - x)],
    ]


def matern_stationary_covariance(sigma: float, lam: float) -> List[List[float]]:
    """单通道 Matérn ν=3/2 稳态协方差 P∞（2×2）。

    P∞ = σ² · [[1, 0], [0, λ²]]

    位置维度的稳态方差 = σ²（幅度超参数的平方），
    速度维度的稳态方差 = σ²λ²（衰减越快，速度波动越大）。
    """
    if sigma <= 0:
        raise ValueError(f"matern_stationary_covariance: σ 必须为正，得到 {sigma}")
    if lam <= 0:
        raise ValueError(f"matern_stationary_covariance: λ 必须为正，得到 {lam}")
    s2 = sigma * sigma
    return [
        [s2, 0.0],
        [0.0, s2 * lam * lam],
    ]


def matern_process_noise(sigma: float, lam: float, dt: float) -> List[List[float]]:
    """单通道 Matérn ν=3/2 离散过程噪声 Q(Δt)（2×2）。

    Q(Δt) = P∞ - F(Δt)·P∞·F(Δt)ᵀ
    """
    if dt < 0:
        raise ValueError(f"matern_process_noise: dt 不能为负，得到 {dt}")
    p_inf = matern_stationary_covariance(sigma, lam)
    if dt == 0.0:
        return linalg.zeros(2, 2)
    f = matern_transition(lam, dt)
    # Q = P∞ - F·P∞·Fᵀ
    f_p = linalg.mul(f, p_inf)
    f_p_ft = linalg.mul(f_p, linalg.transpose(f))
    q = linalg.sub(p_inf, f_p_ft)
    # 数值防护：Q 理论上半正定，浮点误差可能产生极小负值，钳到 0
    for i in range(2):
        for j in range(2):
            if -1e-12 < q[i][j] < 0.0:
                q[i][j] = 0.0
    return q


def build_transition(
    lam_valence: float, lam_arousal: float, dt: float
) -> List[List[float]]:
    """构造 4×4 双通道转移矩阵（块对角）。

    状态 x = [v, v̇, a, ȧ]：
        F = [[F_v, 0  ],
             [0,   F_a]]
    """
    fv = matern_transition(lam_valence, dt)
    fa = matern_transition(lam_arousal, dt)
    return [
        [fv[0][0], fv[0][1], 0.0, 0.0],
        [fv[1][0], fv[1][1], 0.0, 0.0],
        [0.0, 0.0, fa[0][0], fa[0][1]],
        [0.0, 0.0, fa[1][0], fa[1][1]],
    ]


def build_stationary_covariance(
    sigma_valence: float,
    sigma_arousal: float,
    lam_valence: float,
    lam_arousal: float,
) -> List[List[float]]:
    """构造 4×4 双通道稳态协方差（块对角）。"""
    pv = matern_stationary_covariance(sigma_valence, lam_valence)
    pa = matern_stationary_covariance(sigma_arousal, lam_arousal)
    return [
        [pv[0][0], pv[0][1], 0.0, 0.0],
        [pv[1][0], pv[1][1], 0.0, 0.0],
        [0.0, 0.0, pa[0][0], pa[0][1]],
        [0.0, 0.0, pa[1][0], pa[1][1]],
    ]


def build_process_noise(
    sigma_valence: float,
    sigma_arousal: float,
    lam_valence: float,
    lam_arousal: float,
    dt: float,
) -> List[List[float]]:
    """构造 4×4 双通道过程噪声 Q(Δt)（块对角）。"""
    qv = matern_process_noise(sigma_valence, lam_valence, dt)
    qa = matern_process_noise(sigma_arousal, lam_arousal, dt)
    return [
        [qv[0][0], qv[0][1], 0.0, 0.0],
        [qv[1][0], qv[1][1], 0.0, 0.0],
        [0.0, 0.0, qa[0][0], qa[0][1]],
        [0.0, 0.0, qa[1][0], qa[1][1]],
    ]
