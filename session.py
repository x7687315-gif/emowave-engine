"""session.py — 视图层最小会话

2.0 之后，引擎、推荐、估计全部收进 `emowave/core/**`，由 `presenters/` 直接驱动；
视图层（`windows/**`）只需要一个东西：**数据库句柄**。

所以这里只保留一个 12 行的适配器，取代 1.x 那个把
`EmoCalibrationEngine + ContextualBandit + DatabaseManager` 三者绑在一起的
159 行 `SessionController`，也取代 `windows/legacy_embed.py` 里重复的 `_DbSession`。
"""


class SessionController:
    """视图层最小会话：只持有 db。"""

    __slots__ = ("db",)

    def __init__(self, db):
        self.db = db
