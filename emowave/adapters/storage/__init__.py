"""Storage adapters — 持久化桥接层。

Core 硬边界（REFACTOR_PLAN.md §11.1）：Core 不知道 SQLite 实现细节。
存储是 adapter 的职责——这一层**可以**认识 SQLite，把 Core 的领域对象
（Observation/EmotionState/...）落盘并读回。
"""

from emowave.adapters.storage.sqlite import SQLiteStorage

__all__ = ["SQLiteStorage"]
