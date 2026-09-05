"""linalg 纯标准库矩阵运算单元测试。

重点验证 ARCHITECTURE LIGHTWEIGHT part2 §2.2 的替换映射表正确性，
特别是 4×4 / 10×10 求逆（Kalman 新息协方差与 LinUCB 的核心运算）。
"""

import math

import pytest

from emowave.core import linalg


# ============================================================
# 构造
# ============================================================


def test_zeros():
    m = linalg.zeros(2, 3)
    assert m == [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]


def test_identity():
    assert linalg.identity(3) == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]


def test_diag():
    assert linalg.diag([2.0, 3.0]) == [[2.0, 0.0], [0.0, 3.0]]


def test_mat_deep_copies():
    src = [[1.0, 2.0], [3.0, 4.0]]
    m = linalg.mat(src)
    m[0][0] = 99.0
    assert src[0][0] == 1.0  # 原数据不受影响


def test_shape():
    assert linalg.shape([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]) == (3, 2)
    assert linalg.shape([]) == (0, 0)


# ============================================================
# 基本运算
# ============================================================


def test_add():
    a = [[1.0, 2.0], [3.0, 4.0]]
    b = [[5.0, 6.0], [7.0, 8.0]]
    assert linalg.add(a, b) == [[6.0, 8.0], [10.0, 12.0]]


def test_add_shape_mismatch_raises():
    with pytest.raises(ValueError):
        linalg.add([[1.0]], [[1.0, 2.0]])


def test_sub():
    a = [[5.0, 6.0], [7.0, 8.0]]
    b = [[1.0, 2.0], [3.0, 4.0]]
    assert linalg.sub(a, b) == [[4.0, 4.0], [4.0, 4.0]]


def test_scale():
    a = [[1.0, 2.0], [3.0, 4.0]]
    assert linalg.scale(a, 2.0) == [[2.0, 4.0], [6.0, 8.0]]


def test_mul_2x2():
    a = [[1.0, 2.0], [3.0, 4.0]]
    b = [[5.0, 6.0], [7.0, 8.0]]
    # [[1*5+2*7, 1*6+2*8], [3*5+4*7, 3*6+4*8]] = [[19,22],[43,50]]
    assert linalg.mul(a, b) == [[19.0, 22.0], [43.0, 50.0]]


def test_mul_identity_is_noop():
    a = [[1.0, 2.0], [3.0, 4.0]]
    assert linalg.mul(a, linalg.identity(2)) == a
    assert linalg.mul(linalg.identity(2), a) == a


def test_mul_shape_mismatch_raises():
    with pytest.raises(ValueError):
        linalg.mul([[1.0, 2.0]], [[1.0, 2.0]])  # 1x2 @ 1x2 非法


def test_mul_vec():
    a = [[1.0, 2.0], [3.0, 4.0]]
    x = [5.0, 6.0]
    assert linalg.mul_vec(a, x) == [17.0, 39.0]


def test_mul_vec_shape_mismatch_raises():
    with pytest.raises(ValueError):
        linalg.mul_vec([[1.0, 2.0]], [1.0, 2.0, 3.0])


def test_transpose():
    a = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert linalg.transpose(a) == [[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]]


def test_transpose_involution():
    """转置两次回到原矩阵。"""
    a = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    assert linalg.transpose(linalg.transpose(a)) == a


def test_trace():
    a = [[1.0, 2.0], [3.0, 4.0]]
    assert linalg.trace(a) == 5.0


# ============================================================
# 向量运算
# ============================================================


def test_vec_add_sub_scale():
    assert linalg.vec_add([1.0, 2.0], [3.0, 4.0]) == [4.0, 6.0]
    assert linalg.vec_sub([3.0, 4.0], [1.0, 2.0]) == [2.0, 2.0]
    assert linalg.vec_scale([1.0, 2.0], 3.0) == [3.0, 6.0]


def test_dot():
    assert linalg.dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == 32.0


def test_vec_length_mismatch_raises():
    with pytest.raises(ValueError):
        linalg.vec_add([1.0], [1.0, 2.0])
    with pytest.raises(ValueError):
        linalg.dot([1.0], [1.0, 2.0])


# ============================================================
# 求逆（核心：Kalman 与 LinUCB 依赖）
# ============================================================


def test_inv_2x2_closed_form():
    """2×2 走闭式解。"""
    a = [[4.0, 7.0], [2.0, 6.0]]
    # det = 24-14 = 10, inv = [[6,-7],[-2,4]]/10
    expected = [[0.6, -0.7], [-0.2, 0.4]]
    result = linalg.inv(a)
    for i in range(2):
        for j in range(2):
            assert result[i][j] == pytest.approx(expected[i][j], abs=1e-9)


def test_inv_2x2_times_original_is_identity():
    a = [[3.0, 1.0], [2.0, 4.0]]
    product = linalg.mul(a, linalg.inv(a))
    ident = linalg.identity(2)
    for i in range(2):
        for j in range(2):
            assert product[i][j] == pytest.approx(ident[i][j], abs=1e-9)


def test_inv_4x4_gauss_jordan():
    """4×4 走通用高斯-约当（Kalman 状态协方差规模）。"""
    a = [
        [2.0, 1.0, 1.0, 0.0],
        [1.0, 3.0, 0.0, 1.0],
        [1.0, 0.0, 4.0, 1.0],
        [0.0, 1.0, 1.0, 5.0],
    ]
    a_inv = linalg.inv(a)
    product = linalg.mul(a, a_inv)
    ident = linalg.identity(4)
    for i in range(4):
        for j in range(4):
            assert product[i][j] == pytest.approx(ident[i][j], abs=1e-9)


def test_inv_10x10():
    """10×10 求逆（LinUCB 规模，part2 §2.2 提及）。"""
    n = 10
    # 构造一个对角占优的可逆矩阵
    a = linalg.identity(n)
    for i in range(n):
        a[i][i] = float(i + 2)
        if i + 1 < n:
            a[i][i + 1] = 0.5
    a_inv = linalg.inv(a)
    product = linalg.mul(a, a_inv)
    for i in range(n):
        for j in range(n):
            expected = 1.0 if i == j else 0.0
            assert product[i][j] == pytest.approx(expected, abs=1e-8)


def test_inv_singular_raises():
    """奇异矩阵（行列式为 0）应抛 ValueError，不返回垃圾。"""
    with pytest.raises(ValueError):
        linalg.inv([[1.0, 2.0], [2.0, 4.0]])  # 第二行=2×第一行，奇异
    with pytest.raises(ValueError):
        linalg.inv([[0.0, 0.0], [0.0, 0.0]])


def test_inv_non_square_raises():
    with pytest.raises(ValueError):
        linalg.inv([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])


def test_inv_partial_pivoting_handles_small_diagonal():
    """部分主元选取：对角元接近零时仍能稳定求逆。"""
    # 第一行对角元极小，需要换行
    a = [[1e-10, 1.0], [1.0, 1.0]]
    a_inv = linalg.inv(a)
    product = linalg.mul(a, a_inv)
    for i in range(2):
        for j in range(2):
            expected = 1.0 if i == j else 0.0
            assert product[i][j] == pytest.approx(expected, abs=1e-6)


def test_solve():
    """solve(A, b) 解 A·x = b。"""
    a = [[2.0, 1.0], [1.0, 3.0]]
    b = [5.0, 10.0]
    x = linalg.solve(a, b)
    # 验证 A·x = b
    check = linalg.mul_vec(a, x)
    assert check[0] == pytest.approx(b[0], abs=1e-9)
    assert check[1] == pytest.approx(b[1], abs=1e-9)
