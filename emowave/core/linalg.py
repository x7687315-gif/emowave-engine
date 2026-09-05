"""linalg — 纯标准库矩阵运算（零依赖）。

ARCHITECTURE LIGHTWEIGHT part2 §2.2 的核心结论：内核的 numpy 表面积只有
约 15 个 API 调用，且全部可平凡替换。核心矩阵是 4×4 和 10×10 —— 这是
"线性代数的玩具规模"，不是需要 BLAS 的规模。numpy 在本项目里买的性能
（单步 64µs vs 22µs）远远不值它的导入成本（1082ms）。

本模块用纯 Python list[list[float]] 实现 Kalman 滤波所需的全部矩阵运算：
  - 加法 / 减法 / 数乘
  - 矩阵乘法 / 矩阵-向量乘法
  - 转置
  - n×n 求逆（高斯-约当消元 + 部分主元选取，数值稳定）
  - 单位阵 / 零阵 / 迹 / 对角阵

矩阵表示：list[list[float]]，行主序。M[i][j] = 第 i 行第 j 列。
向量表示：list[float]。

复杂度：n×n 求逆 O(n³)。n=4 时约 64 次乘加，n=10 时约 1000 次，
在 1Hz 采样下完全无压力（part2 §2.1 实测）。
"""

from __future__ import annotations

from typing import List, Sequence

# 类型别名：矩阵 = 浮点数二维列表，向量 = 浮点数一维列表
Matrix = List[List[float]]
Vector = List[float]


# ============================================================
# 构造
# ============================================================


def zeros(rows: int, cols: int) -> Matrix:
    """构造 rows×cols 零矩阵。"""
    return [[0.0 for _ in range(cols)] for _ in range(rows)]


def identity(n: int) -> Matrix:
    """构造 n×n 单位阵。"""
    m = zeros(n, n)
    for i in range(n):
        m[i][i] = 1.0
    return m


def diag(values: Sequence[float]) -> Matrix:
    """由对角元素构造对角阵。"""
    n = len(values)
    m = zeros(n, n)
    for i in range(n):
        m[i][i] = float(values[i])
    return m


def mat(values: Sequence[Sequence[float]]) -> Matrix:
    """从嵌套序列构造矩阵（深拷贝，避免外部引用污染）。"""
    return [[float(x) for x in row] for row in values]


# ============================================================
# 基本运算
# ============================================================


def shape(m: Matrix) -> tuple:
    """返回矩阵 (行数, 列数)。"""
    if not m:
        return (0, 0)
    return (len(m), len(m[0]))


def add(a: Matrix, b: Matrix) -> Matrix:
    """矩阵加法 A + B。"""
    ra, ca = shape(a)
    rb, cb = shape(b)
    if (ra, ca) != (rb, cb):
        raise ValueError(f"add 形状不匹配: {ra}x{ca} vs {rb}x{cb}")
    return [[a[i][j] + b[i][j] for j in range(ca)] for i in range(ra)]


def sub(a: Matrix, b: Matrix) -> Matrix:
    """矩阵减法 A - B。"""
    ra, ca = shape(a)
    rb, cb = shape(b)
    if (ra, ca) != (rb, cb):
        raise ValueError(f"sub 形状不匹配: {ra}x{ca} vs {rb}x{cb}")
    return [[a[i][j] - b[i][j] for j in range(ca)] for i in range(ra)]


def scale(a: Matrix, k: float) -> Matrix:
    """数乘 k·A。"""
    ra, ca = shape(a)
    return [[a[i][j] * k for j in range(ca)] for i in range(ra)]


def mul(a: Matrix, b: Matrix) -> Matrix:
    """矩阵乘法 A @ B。

    朴素三重循环。对 4×4 / 10×10 规模足够快（part2 §2.1 实测）。
    """
    ra, ca = shape(a)
    rb, cb = shape(b)
    if ca != rb:
        raise ValueError(f"mul 形状不匹配: {ra}x{ca} @ {rb}x{cb}")
    result = zeros(ra, cb)
    for i in range(ra):
        ai = a[i]
        ri = result[i]
        for k in range(ca):
            aik = ai[k]
            if aik == 0.0:
                continue  # 跳过零元素，稀疏矩阵加速
            bk = b[k]
            for j in range(cb):
                ri[j] += aik * bk[j]
    return result


def mul_vec(a: Matrix, x: Vector) -> Vector:
    """矩阵-向量乘法 A @ x。"""
    ra, ca = shape(a)
    if ca != len(x):
        raise ValueError(f"mul_vec 形状不匹配: {ra}x{ca} @ vec{len(x)}")
    result = [0.0] * ra
    for i in range(ra):
        ai = a[i]
        s = 0.0
        for j in range(ca):
            s += ai[j] * x[j]
        result[i] = s
    return result


def transpose(a: Matrix) -> Matrix:
    """转置 Aᵀ。"""
    ra, ca = shape(a)
    return [[a[i][j] for i in range(ra)] for j in range(ca)]


def trace(a: Matrix) -> float:
    """矩阵的迹（对角线之和）。"""
    ra, ca = shape(a)
    n = min(ra, ca)
    return sum(a[i][i] for i in range(n))


# ============================================================
# 向量运算
# ============================================================


def vec_add(a: Vector, b: Vector) -> Vector:
    if len(a) != len(b):
        raise ValueError(f"vec_add 长度不匹配: {len(a)} vs {len(b)}")
    return [a[i] + b[i] for i in range(len(a))]


def vec_sub(a: Vector, b: Vector) -> Vector:
    if len(a) != len(b):
        raise ValueError(f"vec_sub 长度不匹配: {len(a)} vs {len(b)}")
    return [a[i] - b[i] for i in range(len(a))]


def vec_scale(a: Vector, k: float) -> Vector:
    return [x * k for x in a]


def dot(a: Vector, b: Vector) -> float:
    if len(a) != len(b):
        raise ValueError(f"dot 长度不匹配: {len(a)} vs {len(b)}")
    return sum(a[i] * b[i] for i in range(len(a)))


# ============================================================
# 求逆（高斯-约当消元 + 部分主元选取）
# ============================================================


def inv(a: Matrix) -> Matrix:
    """n×n 矩阵求逆，高斯-约当消元 + 部分主元选取。

    部分主元（partial pivoting）：每列选绝对值最大的行作为主元，
    避免除以接近零的数导致的数值不稳定。这是数值线性代数的标准做法，
    对 4×4（Kalman 新息协方差 S）和 10×10（LinUCB）规模足够稳健。

    Args:
        a: 方阵

    Returns:
        a 的逆矩阵

    Raises:
        ValueError: 非方阵或矩阵奇异（不可逆）
    """
    ra, ca = shape(a)
    if ra != ca:
        raise ValueError(f"inv 要求方阵，得到 {ra}x{ca}")
    n = ra

    # 2×2 闭式解（Kalman 新息协方差 S 常为 2×2，闭式解更快更稳）
    if n == 2:
        det = a[0][0] * a[1][1] - a[0][1] * a[1][0]
        if abs(det) < 1e-15:
            raise ValueError(f"inv 矩阵奇异: det={det}")
        inv_det = 1.0 / det
        return [
            [a[1][1] * inv_det, -a[0][1] * inv_det],
            [-a[1][0] * inv_det, a[0][0] * inv_det],
        ]

    # 构造增广矩阵 [A | I]
    aug = zeros(n, 2 * n)
    for i in range(n):
        for j in range(n):
            aug[i][j] = float(a[i][j])
        aug[i][n + i] = 1.0

    # 高斯-约当消元
    for col in range(n):
        # 部分主元选取：找 col 列中绝对值最大的行
        pivot_row = col
        pivot_val = abs(aug[col][col])
        for r in range(col + 1, n):
            v = abs(aug[r][col])
            if v > pivot_val:
                pivot_val = v
                pivot_row = r
        if pivot_val < 1e-15:
            raise ValueError(f"inv 矩阵奇异或病态: 列 {col} 主元 ≈ {pivot_val}")
        # 交换行
        if pivot_row != col:
            aug[col], aug[pivot_row] = aug[pivot_row], aug[col]

        # 归一化主元行
        pivot = aug[col][col]
        inv_pivot = 1.0 / pivot
        aug_col = aug[col]
        for j in range(2 * n):
            aug_col[j] *= inv_pivot

        # 消去其他行的该列
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor == 0.0:
                continue
            aug_r = aug[r]
            for j in range(2 * n):
                aug_r[j] -= factor * aug_col[j]

    # 提取右半部分即逆矩阵
    result = zeros(n, n)
    for i in range(n):
        for j in range(n):
            result[i][j] = aug[i][n + j]
    return result


def solve(a: Matrix, b: Vector) -> Vector:
    """解线性方程组 A·x = b（通过求逆，规模小时足够）。"""
    return mul_vec(inv(a), b)
