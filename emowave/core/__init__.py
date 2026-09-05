"""EmoWave Core — 领域模型、估计器、校准器与协议。

Core 是 EmoWave 的心脏。它只认识自己的领域对象和算法，不认识：
  - UI 框架（PyQt / Flet / Android / iOS）
  - 存储实现（SQLite / JSON / 云端）
  - 外部 Agent（Amiya / LLM / TTS）
  - 平台特性（Windows / Linux / 移动端）

所有跨边界的交互都通过 adapters/ 完成，Core 通过纯 Python 数据类型
（dataclass / dict / list / float / str）暴露接口。
"""
