"""presenters/ — 视图与内核之间的编排层（MVP 的 "P"）

=== 为什么要有这一层 ===
v3 的 `windows/main_console.py` 同时承担两件事：既构造控件（QSS/布局/信号），
又直接持有 `StateEstimator / BaselineController / Calibrator / QTimer / db`
并写全部内核算法（含一份手写的 Matérn 衰减外推）。后果：

  · 单元测试必须 `new MainConsole()`（连带建整个 Qt 树）才能测内核行为；
  · 想做第二个视图（迷你置顶窗 / 将来的 Flet 版）只能复制一份状态；
  · UI 层自己实现了内核已有的数学（`_decay_points` 手写衰减）。

分层后：

    View（windows/*.py）     只画控件、只发/接信号，不认识 emowave
    Presenter（本目录）      编排内核、发信号、不 import 任何 PyQt5.QtWidgets
    Model（emowave/ core）   纯计算，零 Qt

=== 目录 ===
    engine.py             内核深路径 import 的聚合层（包门面只导出数据模型）
    console_presenter.py  主控制台的编排：采样/记录/衰减/纠正/学习/落库
"""
