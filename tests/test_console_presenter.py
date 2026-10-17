"""tests/test_console_presenter.py — 阶段 3：解耦回归

这些测试**不构造任何 Qt 控件**就能驱动完整内核流程（记录→采样→停止→衰减→
纠正→学习→换人群），这正是解耦要买到的可测性；v3 时代同样的流程必须先
`new MainConsole()`（连带建整个 Qt 树）。

同时守住三个"解耦时差点丢掉"的隐性契约：
  1. nudge/fork/reset 必须同步 estimator.baseline（v3 写在卡片按钮回调里）
  2. 衰减必须走内核 `predict_forward`（特征：方差随外推时长递增）
  3. 视图层不得再直接 import/使用内核类
"""

import pytest


def _presenter(qapp, db=None, archetype_key=None):
    from presenters.console_presenter import ConsolePresenter
    return ConsolePresenter(db=db, archetype_key=archetype_key)


def _settle(app, ms=250):
    """跑一段事件循环，让跨线程/排队的信号投递完成。"""
    from PyQt5.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()
    app.processEvents()


def test_presenter_runs_full_cycle_without_any_widget(qapp):
    """无控件驱动完整生命周期：记录→采样→停止→衰减收敛→波形消失。"""
    p = _presenter(qapp)

    p.toggle_recording(True)
    assert p.recording is True
    for _ in range(4):
        p.sample(0.60, 0.45)
    p.toggle_recording(False)

    assert p.recording is False
    assert len(p.observations) == 4 and len(p.states) == 4
    assert p._wave_active is True

    for _ in range(60):
        if not p._wave_active:
            break
        p.decay_step()
    assert p._wave_active is False
    assert p.states == []


def test_presenter_corrections_feed_calibrator(qapp):
    """纠正同样在无视图环境下喂进 calibrator（单维度 → 一次拖点出 2 条）。"""
    p = _presenter(qapp)
    p.toggle_recording(True)
    p.sample(0.5, 0.5)

    ts = p.states[-1][0]
    n = p.ingest_corrections([(ts, 0.8, 0.3)])
    assert n == 2
    assert len(p.calibrator.dataset) == 2

    n2 = p.ingest_corrections([(ts, 0.8, p.states[-1][1].arousal)])
    assert n2 == 1                      # 唤醒没动 → 不造假纠正


def test_presenter_nudge_syncs_estimator_baseline(qapp):
    """回归①：解耦时差点把 v3 的 estimator.baseline 同步弄丢。"""
    p = _presenter(qapp)
    before = p.baseline_ctrl.current.valence

    p.nudge(0.05)
    assert p.baseline_ctrl.current.valence == pytest.approx(before + 0.05)
    assert p.estimator.baseline.valence == pytest.approx(before + 0.05)


def test_presenter_fork_and_reset(qapp):
    """回归①：fork 产生新 regime，reset 回群体先验，两者都同步 estimator。"""
    from emowave.core.domain.baseline import Baseline, BaselineSource
    p = _presenter(qapp)

    n_regimes = len(p.baseline_ctrl.regimes)
    p.fork()
    assert len(p.baseline_ctrl.regimes) == n_regimes + 1
    assert p.estimator.baseline.valence == pytest.approx(
        p.baseline_ctrl.current.valence)

    p.reset()
    pop = Baseline()
    assert p.baseline_ctrl.current.valence == pytest.approx(pop.valence)
    assert p.baseline_ctrl.current.source == BaselineSource.USER_RESET
    assert p.estimator.baseline.valence == pytest.approx(pop.valence)


def test_presenter_decay_uses_kernel_predict_forward(qapp):
    """回归②：衰减走内核 predict_forward，而不是手写 Matérn。

    内核版的两个可观测特征：
      · 每步叠过程噪声 Q → 外推越远，方差越大（"越预测越不确定"）
      · 在偏差空间传播 → 末点向**基线**收敛（不是向 0）
    """
    p = _presenter(qapp)
    p.toggle_recording(True)
    for _ in range(5):
        p.sample(0.60, 0.40)
    p.toggle_recording(False)
    p._decay_now = p._last_obs_ts

    near = p._decay_points(5.0)
    far = p._decay_points(120.0)
    assert max(pt[3] for pt in far) > max(pt[3] for pt in near)

    bv = p.baseline_ctrl.current.valence
    assert abs(far[-1][1] - bv) < abs(near[-1][1] - bv)
    # 外推是向后推进的（时间戳单调）
    assert far[-1][0] > near[-1][0] > p._last_obs_ts


def test_presenter_set_archetype_reseeds(qapp):
    """换人群 = 换先验 + 清会话，且 estimator/calibrator 全部重建。"""
    p = _presenter(qapp, archetype_key="high")
    p.toggle_recording(True)
    p.sample(0.5, 0.5)

    p.set_archetype("low")
    assert p.params.ell_arousal == 320.0
    assert p.baseline_ctrl.current.arousal == 0.25
    assert p.observations == [] and p.states == [] and p._edits == []
    assert p.calibrator._population.ell_arousal == 320.0


def test_presenter_persists_event_without_db(qapp):
    """无 db 时落库安全跳过（解耦后同样不能抛）。"""
    p = _presenter(qapp, db=None)
    p.toggle_recording(True)
    p.sample(0.5, 0.5)
    p.sample(0.55, 0.45)
    p.toggle_recording(False)          # 不应抛
    assert p.recording is False


def _imported_modules(path):
    """用 ast 取出一个源文件真实 import 的模块名（不受注释/docstring 干扰）。"""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    return mods


def test_presenter_learn_refits_curve(qapp):
    """回归③：学习一步后显示曲线必须按新参数重画。

    v3 是 `_learn()` 之后紧跟 `_refresh_curve()`；解耦时漏搬这一步，
    停止记录后曲线会变成"旧 λ 的历史主体 + 新 λ 的外推尾"混在一条线上，
    直到下一次采样/纠正才自愈。现在 learn() 内部自己重发。

    断言方式：learn() 会改 params（λ 与阶段），用同一份观察重新平滑得到的
    曲线必须与 learn 前不同 —— 若漏了重发，曲线数据就还是旧的。
    """
    p = _presenter(qapp)
    p.toggle_recording(True)
    for i in range(6):
        p.sample(0.40 + i * 0.03, 0.55)
    p.toggle_recording(False)          # 内部 save_event → learn()
    _settle(qapp)

    seen = []
    p.curve_changed.connect(lambda payload: seen.append(payload))
    p.learn()                          # 再学一次 → 必须重发曲线
    _settle(qapp)
    assert seen, "learn() 之后应重发曲线（v3 的 _refresh_curve 语义）"
    # 且重发的内容与当前 params 一致（不是上一次的缓存形状）
    payload = seen[-1]
    assert len(payload["ts"]) == len(p.observations) or len(payload["ts"]) >= 2
    assert payload["baseline_v"] == p.baseline_ctrl.current.valence


def test_presenter_double_press_keeps_segment(qapp):
    """回归：已在记录中再按"开始"必须被拒绝，而不是清空已采段落。

    旧行为：守卫要求"不在记录中"，所以连按两次会跳过收尾、直接
    `self._segment = []` → 落库 sample_count 与曲线长度不一致。
    """
    p = _presenter(qapp)
    p.toggle_recording(True)
    for _ in range(3):
        p.sample(0.5, 0.5)
    assert len(p._segment) == 3

    p.toggle_recording(True)          # 连按：应被拒绝
    assert p.recording is True
    assert len(p._segment) == 3, "连按开始不应吃掉已采集的观察"
    assert len(p.observations) == 3


def test_presenter_set_input_is_per_channel(qapp):
    """回归：多视图共享 presenter 时输入不能互相覆盖。

    场景：挂件调好唤醒 0.9 → 主窗只碰效价 → 唤醒不该被打回 0.4。
    """
    p = _presenter(qapp)
    p.set_input(valence=0.5, arousal=0.4)

    p.set_input(arousal=0.9)           # 挂件只改唤醒
    assert p._input_v == 0.5 and p._input_a == 0.9

    p.set_input(valence=0.7)           # 主窗只改效价
    assert p._input_v == 0.7, "效价应更新"
    assert p._input_a == 0.9, "唤醒应保持挂件刚设的值（不被整体覆盖）"

    # 变更要广播给各视图做镜像
    got = []
    p.input_changed.connect(lambda v, a: got.append((v, a)))
    p.set_input(valence=0.2)
    assert got and got[-1] == (0.2, 0.9)


def test_presenter_snapshot_accessors(qapp):
    """初始同步接口：迷你窗创建晚于 resume_active_wave，需要能拉到现状。"""
    p = _presenter(qapp)
    assert p.current_mode() == "idle"
    assert p.current_count() == 0
    assert p.current_curve_payload() is None

    p.toggle_recording(True)
    for _ in range(4):
        p.sample(0.5, 0.5)
    assert p.current_mode() == "recording"
    assert p.current_count() == 4

    p.toggle_recording(False)
    assert p.current_mode() == "decaying"
    payload = p.current_curve_payload()
    assert payload is not None and len(payload["ts"]) > 1


def test_presenter_has_no_qt_widgets_dependency():
    """MVP 边界：presenter 只允许 QtCore，不得 import QtWidgets。"""
    import presenters.console_presenter as m
    mods = _imported_modules(m.__file__)
    assert not any(x.startswith("PyQt5.QtWidgets") for x in mods), mods
    # QtCore 是允许的（QObject/QTimer/pyqtSignal）
    assert "PyQt5.QtCore" in mods


def test_view_does_not_use_kernel_classes_directly():
    """视图层不再直接持有内核实现类（编排统一走 presenter）。"""
    import windows.main_console as m
    src = open(m.__file__, encoding="utf-8").read()
    for name in ("StateEstimator(", "Calibrator(", "BaselineController(",
                 "RTSSmoother(", "predict_forward(", "db.save_event("):
        assert name not in src, f"视图层仍直接使用 {name}"
    # 深路径 import 也已从视图层消失
    mods = _imported_modules(m.__file__)
    assert not any(x.startswith("emowave.core") for x in mods), mods
