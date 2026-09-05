"""Adapters — EmoWave Core 与外部世界（存储/传感器/Agent）的桥接层。

Core 硬边界（REFACTOR_PLAN.md §11.1）：Core 不知道 PyQt/Flet/Android/iOS/
SQLite 实现细节/Amiya/LLM/TTS。所有跨边界交互都通过 adapters 完成。

子包：
  agent/    Amiya 等外部 Agent 的集成适配器（Phase 8）
  storage/  SQLite 等持久化适配器（Phase 9）
  sensors/  HR/HRV 等传感器适配器（Phase 9）
"""
