"""仓库根 conftest.py — 给根目录遗留脚本式测试补 fixture

`test_recommender.py` 是 1.x 时代的**脚本式**测试（`main()` 打印报告，
打印而非 assert）。pytest 收集 `test_serialization(bandit)` 时因为缺
`bandit` fixture，从项目一开始就是 ERROR 状态——每次全量跑都带一个
与本次改动无关的 error，容易掩盖真实回归。

这里补一个最小 fixture，让全量套件达到 0 error（该用例本身是打印式
冒烟，不会失败；真正的行为断言在 `emowave/tests/` 与 `tests/`）。
"""
import pytest

from recommender import ContextualBandit, DEFAULT_STRATEGIES


@pytest.fixture
def bandit():
    """一台用默认策略库初始化的 LinUCB 老虎机。"""
    return ContextualBandit(strategies=DEFAULT_STRATEGIES)
