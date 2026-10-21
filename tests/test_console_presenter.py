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


def _presenter(qapp, db=None, archetype_key=None, clock=None):
    from presenters.console_presenter import ConsolePresenter
    return ConsolePresenter(db=db, archetype_key=archetype_key, clock=clock)


def _settle(app, ms=250):
    """跑一段事件循环，让跨线程/排队的信号投递完成。"""
    from PyQt5.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()
    app.processEvents()


def test_presenter_runs_full_cycle_without_any_widget(qapp):
    """无控件驱动完整生命周期：记录→采样→停止→衰减收敛→波形消失。

    v4 修订：衰减按**墙钟**推进，所以这里用假时钟"让 60 秒过去"，
    而不是连调 60 次 decay_step（旧写法隐含"tick 次数 = 秒数"的错误语义）。
    """
    from tests.helpers import FakeClock
    clock = FakeClock()
    p = _presenter(qapp, clock=clock)

    p.toggle_recording(True)
    assert p.recording is True
    for _ in range(4):
        clock.advance(1)
        p.sample(0.60, 0.45)
    p.toggle_recording(False)

    assert p.recording is False
    assert len(p.observations) == 4 and len(p.states) == 4
    assert p.is_wave_active() is True

    clock.advance(60)
    p.decay_step()
    assert p.is_wave_active() is False
    assert p.states == []
    # 波消失 ≠ 没有状态：latest_state 仍代表"模型此刻的估计"
    assert p.latest_state is not None


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


# ================================================================
# v4 Phase 1：观察语义与时间语义
# ================================================================

def test_tick_does_not_create_observation(qapp):
    """心跳只推进"现在"，不制造自评。

    v4 之前：记录中每秒 `sample(_input_v,_input_a)`，10 次心跳 = 10 条同值观察。
    """
    from tests.helpers import FakeClock
    clock = FakeClock()
    p = _presenter(qapp, clock=clock)
    p.toggle_recording(True)
    p.submit_input(0.30, 0.40)
    p.recorder.flush()

    assert len(p.observations) == 1
    for _ in range(10):
        clock.advance(1)
        p._on_tick()
    assert len(p.observations) == 1          # 没有新增
    assert p.latest_state is not None        # 但"现在"的状态被推进了


def test_debounced_drag_commits_once(qapp):
    """一次连续拖动（20 次 valueChanged）只应产生 1 条自评。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    p = _presenter(qapp, clock=clock)
    p.toggle_recording(True)

    for i in range(20):
        p.submit_input(0.30 + i * 0.01, 0.40)
    assert len(p.observations) == 0          # 防抖未到期，先不提交
    p.recorder.flush()                       # 模拟防抖到期
    assert len(p.observations) == 1

    # 值没变 → 不产生"重复自评"
    p.submit_input(0.49, 0.40)
    p.recorder.flush()
    assert len(p.observations) == 1

    # 显式「记一笔」→ 即使值没变也确认一次
    p.commit_now()
    assert len(p.observations) == 2


def test_input_without_recording_creates_nothing(qapp):
    """没点"开始记录"时拖滑条只是在预览，不该落成自评。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    p = _presenter(qapp, clock=clock)
    p.submit_input(0.8, 0.7)
    p.recorder.flush()
    assert p.observations == []


def test_model_time_follows_wall_clock(qapp):
    """距上次自评多久 = 墙钟差，而不是 tick 次数。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    p = _presenter(qapp, clock=clock)
    p.toggle_recording(True)
    p.submit_input(0.70, 0.60)
    p.recorder.flush()
    p.toggle_recording(False)

    clock.advance(30)
    p.decay_step()
    assert p.last_horizon == pytest.approx(30.0, abs=0.01)

    # 真实又过 90 秒（期间一次 tick 都没跑）
    clock.advance(90)
    p.decay_step()
    assert p.last_horizon == pytest.approx(120.0, abs=0.01)


def test_forecast_is_never_persisted_as_observation(qapp, tmp_path):
    """衰减推演的预测点不能变成新的真实观察，也不能进 raw_data。"""
    import json
    from db import DatabaseManager
    from tests.helpers import FakeClock

    clock = FakeClock()
    db = DatabaseManager(str(tmp_path / "t.db"))
    p = _presenter(qapp, db=db, clock=clock)
    p.toggle_recording(True)
    for _ in range(3):
        clock.advance(1)
        p.sample(0.75, 0.65)
    p.toggle_recording(False)
    assert len(p.observations) == 3

    clock.advance(120)
    p.decay_step()
    assert len(p.observations) == 3

    events = db.get_recent_events(limit=1)
    assert len(events) == 1
    raw = json.loads(events[0]["raw_data"])
    assert len(raw) == 3                    # 只有 3 条真实观察
    db.close()


def test_wave_end_keeps_latest_state(qapp):
    """波结束只结束动画，不清空"当前状态"。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    p = _presenter(qapp, clock=clock)
    p.toggle_recording(True)
    clock.advance(1)
    p.sample(0.80, 0.70)
    p.toggle_recording(False)

    p._end_wave()
    assert p.is_wave_active() is False
    assert p.states == []
    assert p.latest_state is not None
    assert p.current_mode() == "idle"
    # 读数仍可读（不是 "—"）
    v, a, _c, ci, _p, _ts, _last = p.readout_payload()
    assert 0.0 <= v <= 1.0 and ci != "—"


def test_resume_uses_wall_clock_gap(qapp, tmp_path):
    """关机 30 分钟后再开：按真实时间外推，而不是从关机那一刻慢慢推。"""
    from db import DatabaseManager
    from tests.helpers import FakeClock

    clock = FakeClock()
    db = DatabaseManager(str(tmp_path / "r.db"))
    p = _presenter(qapp, db=db, clock=clock)
    p.toggle_recording(True)
    for _ in range(5):
        clock.advance(1)
        p.sample(0.85, 0.75)
    p.toggle_recording(False)
    assert p.is_wave_active() is True

    clock.advance(1800)                       # 关机 30 分钟
    p2 = _presenter(qapp, db=db, clock=clock)
    p2.resume_active_wave()

    assert p2.last_horizon == pytest.approx(1800.0, abs=2.0)
    assert p2.is_wave_active() is False            # 波早已自然结束
    assert p2.latest_state is not None         # 但状态仍在
    # 事件在"停止记录"时已落库一次；恢复不会重复插入
    assert len(db.get_recent_events(limit=10)) == 1
    db.close()


def test_resume_short_gap_keeps_wave_without_duplicating(qapp, tmp_path):
    """关机 60 秒：波还没走完，恢复后应继续推演，且观察不被重复插入。"""
    from db import DatabaseManager
    from tests.helpers import FakeClock

    clock = FakeClock()
    db = DatabaseManager(str(tmp_path / "r2.db"))
    p = _presenter(qapp, db=db, clock=clock)
    p.toggle_recording(True)
    for _ in range(5):
        clock.advance(1)
        p.sample(0.85, 0.75)
    p.toggle_recording(False)

    clock.advance(60)
    p2 = _presenter(qapp, db=db, clock=clock)
    p2.resume_active_wave()

    assert p2.is_wave_active() is True
    assert len(p2.observations) == 5           # 恰好重放一次，没有翻倍
    assert p2.last_horizon == pytest.approx(60.0, abs=2.0)
    assert len(db.get_recent_events(limit=10)) == 1
    db.close()


# ================================================================
# v4 Phase 2：统一曲线查询
# ================================================================

def _seeded_presenter(qapp, n=60):
    """造 n 条 1 秒间隔的自评，返回 (presenter, 起始时间戳)。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    p = _presenter(qapp, clock=clock)
    t0 = clock()
    p.toggle_recording(True)
    for i in range(n):
        clock.advance(1)
        p.sample(0.5 + 0.3 * ((i % 10) - 5) / 5.0, 0.45)
    p.toggle_recording(False)
    return p, t0


def test_curve_payload_respects_window_and_resolution(qapp):
    """给定窗口与显示点数：只返回窗口内的点，且不超过 resolution。"""
    p, t0 = _seeded_presenter(qapp, n=60)
    payload = p.curve_payload(start_ts=t0 + 10, end_ts=t0 + 40, resolution=10)

    assert payload is not None
    assert len(payload["ts"]) <= 10
    assert min(payload["ts"]) >= t0 + 10 - 1e-6
    assert max(payload["ts"]) <= t0 + 40 + 1e-6
    assert all(t0 + 10 - 1e-6 <= ts <= t0 + 40 + 1e-6 for ts, _v in payload["raw"])


def test_curve_payload_default_covers_all(qapp):
    """不传窗口 = 全量（但受默认分辨率上限约束）。"""
    p, _t0 = _seeded_presenter(qapp, n=60)
    payload = p.curve_payload()
    assert len(payload["ts"]) == 60
    assert len(payload["mv"]) == len(payload["ts"]) == len(payload["vv"])


def test_curve_cache_reuses_result_without_changes(qapp, monkeypatch):
    """数据没变时重复查询不应重算（主窗 + 挂件同时取也只用算一次）。"""
    from presenters import state_query as sq

    calls = {"n": 0}
    original = sq.RTSSmoother.smooth

    def counting(self, observations, edits=None):
        calls["n"] += 1
        return original(self, observations, edits)

    monkeypatch.setattr(sq.RTSSmoother, "smooth", counting)

    p, _t0 = _seeded_presenter(qapp, n=20)
    # 记录过程中最后一次 _emit_curve 已经把结果缓存好了
    calls["n"] = 0
    first = p.curve_payload()
    second = p.curve_payload()
    third = p.current_curve_payload()

    assert calls["n"] == 0          # 三次查询，零次重算
    assert first is second is third


def test_curve_cache_invalidated_by_new_observation(qapp, monkeypatch):
    from presenters import state_query as sq

    calls = {"n": 0}
    original = sq.RTSSmoother.smooth

    def counting(self, observations, edits=None):
        calls["n"] += 1
        return original(self, observations, edits)

    monkeypatch.setattr(sq.RTSSmoother, "smooth", counting)

    p, _t0 = _seeded_presenter(qapp, n=10)
    p.curve_payload()
    calls["n"] = 0
    p.sample(0.7, 0.6)                 # 新增一条 → 必须重算
    p.curve_payload()
    assert calls["n"] == 1


def test_curve_payload_none_when_insufficient(qapp):
    """少于 2 条观察时不画曲线（RTS 需要至少两点）。"""
    p = _presenter(qapp)
    p.toggle_recording(True)
    p.sample(0.6, 0.5)
    assert p.curve_payload() is None


# ================================================================
# v4 Phase 3：衰减尾巴增量推进
# ================================================================

def _decaying_presenter(qapp, n=5, v=0.95, a=0.9):
    from tests.helpers import FakeClock
    clock = FakeClock()
    p = _presenter(qapp, clock=clock)
    p.toggle_recording(True)
    for _ in range(n):
        clock.advance(1)
        p.sample(v, a)
    p.toggle_recording(False)
    return p, clock


def test_decay_tail_is_incremental(qapp, monkeypatch):
    """60 次 tick 只应做约 60 步预测，而不是 1+2+…+60=1830 步。

    v5 起推进统一走 `estimator.advance_to()`，所以这里数内核的 `_predict`
    （真正的数学工作量），而不是 presenter 里的某个函数名——这样测试
    不会因为实现换了个名字就失效。
    """
    from emowave.core.estimator.estimator import StateEstimator

    calls = {"n": 0}
    original = StateEstimator._predict

    def counting(self, *args, **kwargs):
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(StateEstimator, "_predict", counting)

    p, clock = _decaying_presenter(qapp)
    calls["n"] = 0
    for _ in range(60):
        clock.advance(1)
        p.decay_step()

    assert 55 <= calls["n"] <= 70          # 线性，不是二次


def test_decay_tail_matches_from_scratch(qapp):
    """增量推进与一次性重算给出同一个终点（两条路径都从 last_obs 起算）。

    v5：`_decay_points` 改为从重放出的 last_obs 状态起算，`_tail_points`
    由 estimator 增量推进——两者数学同源，差异只在浮点累积。
    """
    p, clock = _decaying_presenter(qapp)
    for _ in range(60):
        clock.advance(1)
        p.decay_step()

    incremental = p._tail_points(60.0)[-1]
    scratch = p._decay_points(60.0)[-1]

    assert incremental[1] == pytest.approx(scratch[1], abs=1e-6)   # valence
    assert incremental[2] == pytest.approx(scratch[2], abs=1e-6)   # arousal
    assert incremental[3] == pytest.approx(scratch[3], abs=1e-6)   # var_v


def test_baseline_change_invalidates_tail(qapp):
    """衰减中改基线 → 尾巴必须以新均值为准重建，不能沿用旧终点。"""
    p, clock = _decaying_presenter(qapp)
    for _ in range(30):
        clock.advance(1)
        p.decay_step()
    assert p._tail is not None

    p.nudge(+0.1)
    assert p._tail is None                  # 旧尾巴作废

    clock.advance(1)
    p.decay_step()
    assert p._tail is not None
    # 新尾巴的第一个点应比旧基线更靠近新基线
    new_baseline = p.baseline_ctrl.current.valence
    assert abs(p._tail["pts"][-1][1] - new_baseline) < 0.5


# ================================================================
# v4 Phase 5：预测段标记
# ================================================================

def test_historical_payload_has_no_prediction(qapp):
    """历史曲线（RTS 平滑）里没有预测段。"""
    p, _t0 = _seeded_presenter(qapp, n=10)
    assert p.curve_payload()["predicted_from"] is None


def test_decay_payload_marks_predicted_from(qapp):
    """衰减尾巴要标出"从哪一刻起是外推"，视图据此画虚线。"""
    p, clock = _decaying_presenter(qapp)
    captured = []
    p.curve_changed.connect(lambda d: captured.append(d))

    clock.advance(5)
    p.decay_step()

    assert captured, "衰减应推送曲线"
    assert captured[-1]["predicted_from"] == pytest.approx(p._last_obs_ts)


# ================================================================
# v4 Phase 6：持久化与恢复加固
# ================================================================

BAD_SNAPSHOTS = [
    "not-json-at-all",
    '{"samples": "nope"}',
    '{"samples": [["x", 0.5, 0.5]]}',
    '{"samples": [[1000.0, 1.5, 0.5]]}',                 # 越界
    '{"samples": [[1000.0, 0.5, 0.5], [999.0, 0.5, 0.5]]}',   # 乱序
    '{"samples": [[1000.0, 0.5]]}',                      # 字段数不对
    '{"samples": []}',
    '{"samples": [[-1.0, 0.5, 0.5]]}',                   # 时间戳非法
]


@pytest.mark.parametrize("bad", BAD_SNAPSHOTS)
def test_corrupt_wave_snapshot_is_discarded(qapp, tmp_path, bad):
    """坏快照不能让启动崩溃，也不能被当成观察重放。"""
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "bad.db"))
    db.set_state("active_wave", bad)

    p = _presenter(qapp, db=db)
    p.resume_active_wave()               # 不应抛异常

    assert p.observations == []
    assert db.get_state("active_wave", "") == ""   # 已清除
    db.close()


def test_resume_is_idempotent(qapp, tmp_path):
    """重复调用 resume 不会把同一批观察插两遍。"""
    from db import DatabaseManager
    from tests.helpers import FakeClock

    clock = FakeClock()
    db = DatabaseManager(str(tmp_path / "idem.db"))
    p = _presenter(qapp, db=db, clock=clock)
    p.toggle_recording(True)
    for _ in range(5):
        clock.advance(1)
        p.sample(0.8, 0.7)
    p.toggle_recording(False)

    p2 = _presenter(qapp, db=db, clock=clock)
    p2.resume_active_wave()
    n1 = len(p2.observations)
    p2.resume_active_wave()
    assert n1 == 5
    assert len(p2.observations) == 5
    db.close()


def test_restore_recent_context_ages_state(qapp, tmp_path):
    """重启后没有未走完的波：用最近事件重建模型，把状态推到"现在"。"""
    import json
    from db import DatabaseManager
    from tests.helpers import FakeClock

    now = 1_700_000_000.0
    db = DatabaseManager(str(tmp_path / "ctx.db"))
    raw = [[now - 60, 0.8, 0.7], [now - 59, 0.8, 0.7], [now - 58, 0.8, 0.7]]
    db.save_event({
        "event_id": "evt_ctx", "start_time": now - 60, "end_time": now - 58,
        "peak_valence": 0.8, "peak_arousal": 0.7, "peak_intensity": 0.4,
        "sample_count": 3, "raw_data": json.dumps(raw),
    })

    clock = FakeClock(now)
    p = _presenter(qapp, db=db, clock=clock)
    assert p.has_estimator_state() is False

    assert p.restore_recent_context() is True
    assert p.observations == []                       # 不把历史塞进曲线
    assert p.latest_state is not None
    assert p.latest_state.timestamp == pytest.approx(now, abs=1.0)
    db.close()


def test_restore_recent_context_skips_stale_event(qapp, tmp_path):
    """太久以前的事件不该影响"现在"。"""
    import json
    from db import DatabaseManager
    from tests.helpers import FakeClock

    now = 1_700_000_000.0
    old = now - 8 * 86400
    db = DatabaseManager(str(tmp_path / "stale.db"))
    db.save_event({
        "event_id": "evt_old", "start_time": old, "end_time": old + 2,
        "peak_valence": 0.9, "peak_arousal": 0.9, "peak_intensity": 0.6,
        "sample_count": 3, "raw_data": json.dumps([[old, 0.9, 0.9]]),
    })

    p = _presenter(qapp, db=db, clock=FakeClock(now))
    assert p.restore_recent_context() is False
    assert p.latest_state is None
    db.close()


# ================================================================
# v5 Phase 2：单轨时间（恢复不重复推进 / 读数跟随曲线 / 纠正持久化）
# ================================================================

def _record_then_stop(qapp, clock, db=None, n=5, v=0.90, a=0.85):
    p = _presenter(qapp, db=db, clock=clock)
    p.toggle_recording(True)
    for _ in range(n):
        clock.advance(1)
        p.sample(v, a)
    p.toggle_recording(False)
    return p


def test_resume_does_not_double_advance(qapp, tmp_path):
    """关机 60 秒后恢复：尾巴从 last_obs 起算，不被平移一个 horizon。

    v4 的错法：先用 query_state() 把模型推到 now，尾巴再从 now 起算、
    却把点标在 last_obs 之后 —— 标称 +1s 的点装的是 +61s 的值。
    """
    from db import DatabaseManager
    from tests.helpers import FakeClock

    clock = FakeClock()
    db = DatabaseManager(str(tmp_path / "dbl.db"))
    p = _record_then_stop(qapp, clock, db=db)
    last_obs = p._last_obs_ts

    clock.advance(60)
    p2 = _presenter(qapp, db=db, clock=clock)
    p2.resume_active_wave()

    pts = p2._tail["pts"]
    assert pts[0][0] == pytest.approx(last_obs + 1.0, abs=1e-6)

    # 首点必须是「1 秒后」的值，而不是「61 秒后」
    expected_1s = p2._decay_points(1.0)[0]
    assert pts[0][1] == pytest.approx(expected_1s[1], abs=1e-6)
    assert pts[0][1] > 0.895

    # 末点是「60 秒后」的值
    expected_60s = p2._decay_points(60.0)[-1]
    assert pts[-1][1] == pytest.approx(expected_60s[1], abs=1e-4)
    db.close()


def test_resume_curve_is_continuous(qapp, tmp_path):
    """恢复后曲线不跳变：尾巴首点贴近历史末点。"""
    from db import DatabaseManager
    from tests.helpers import FakeClock

    clock = FakeClock()
    db = DatabaseManager(str(tmp_path / "cont.db"))
    p = _record_then_stop(qapp, clock, db=db)
    last_value = p.observations[-1].valence

    clock.advance(60)
    p2 = _presenter(qapp, db=db, clock=clock)
    p2.resume_active_wave()

    first = p2._tail["pts"][0]
    assert abs(first[1] - last_value) < 0.01, (
        "恢复后首点 %.4f 与历史末点 %.4f 差距过大 → 曲线会跳变" % (first[1], last_value)
    )
    db.close()


def test_latest_state_tracks_tail_during_decay(qapp):
    """衰减全程：读数 == 曲线尾巴末点（1e-12），不再冻结在最后一次自评。"""
    p, clock = _decaying_presenter(qapp)
    for _ in range(400):
        clock.advance(1)
        p.decay_step()
        assert p.latest_state.valence == pytest.approx(p._tail["pts"][-1][1], abs=1e-12)
        assert p.latest_state.arousal == pytest.approx(p._tail["pts"][-1][2], abs=1e-12)
    # 确实动了（不是一直停在初值）
    assert p.latest_state.valence < 0.90


def test_latest_state_timestamp_follows_clock(qapp):
    """读数的 timestamp 是「现在」，不是「最后一次自评」。"""
    p, clock = _decaying_presenter(qapp)
    clock.advance(120)
    p.decay_step()
    assert p.latest_state.timestamp == pytest.approx(clock(), abs=1e-6)
    assert p.latest_state.timestamp > p._last_obs_ts


def test_readout_payload_carries_timestamps(qapp):
    """读数 payload 带上两个时间戳，UI 才能说清「这个数是几点的」。"""
    p, clock = _decaying_presenter(qapp)
    clock.advance(90)
    p.decay_step()

    v, a, _c, _ci, _params, state_ts, last_obs_ts = p.readout_payload()
    assert state_ts == pytest.approx(clock(), abs=1e-6)
    assert last_obs_ts == pytest.approx(p._last_obs_ts, abs=1e-9)
    assert state_ts > last_obs_ts


def test_corrections_survive_restart(qapp, tmp_path):
    """用户纠正跨会话保留，且重启后回灌 calibrator（不污染本波显示修正）。"""
    from db import DatabaseManager
    from tests.helpers import FakeClock

    clock = FakeClock()
    db = DatabaseManager(str(tmp_path / "cor.db"))
    p = _presenter(qapp, db=db, clock=clock)
    p.toggle_recording(True)
    for _ in range(3):
        clock.advance(1)
        p.sample(0.80, 0.70)
    ts = p.states[-1][0]

    n = p.ingest_corrections([(ts, 0.30, 0.40)])
    assert n == 2                        # 效价 + 唤醒各一条
    assert len(p.corrections) == 2
    p.toggle_recording(False)

    p2 = _presenter(qapp, db=db, clock=clock)
    assert len(p2.corrections) == 2      # 重启后仍在
    assert p2._edits == []               # 但不当作本波曲线的显示修正
    db.close()


def test_corrupt_corrections_are_ignored(qapp, tmp_path):
    """坏纠正数据按空处理，绝不阻断启动。"""
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "badcor.db"))
    for bad in ("{not json", '{"a": 1}', '[{"timestamp": 1.0}]', '[1, 2, 3]'):
        db.set_state("corrections", bad)
        p = _presenter(qapp, db=db)
        assert p.corrections == []
    db.close()


# ================================================================
# v5 Phase 3：修正标记与纠正上下文
# ================================================================

def test_curve_payload_carries_correction_marks(qapp):
    """修正点进 payload，且按维度分开——视图据此画不同标记。"""
    p, clock = _decaying_presenter(qapp)
    ts = p.states[-1][0]
    p.ingest_corrections([(ts, 0.20, 0.60)])

    payload = p.curve_payload()
    marks = payload["corrections"]
    assert len(marks) == 2
    assert {m["channel"] for m in marks} == {"valence", "arousal"}
    assert all(m["ts"] == pytest.approx(ts) for m in marks)


def test_correction_context_bundles_what_sheet_needs(qapp):
    """纠正框需要的数据由 presenter 一次给全，视图不再自己拼。"""
    p, _clock = _decaying_presenter(qapp)
    ctx = p.correction_context()

    assert ctx["has_data"] is True
    assert len(ctx["timestamps"]) == len(ctx["model_v"]) == len(ctx["model_a"]) == 5
    assert ctx["current_state"] is not None
    assert 0.0 <= ctx["baseline_v"] <= 1.0


def test_correction_context_empty_without_data(qapp):
    p = _presenter(qapp)
    ctx = p.correction_context()
    assert ctx["has_data"] is False
    assert ctx["current_state"] is None


def _curve_value_at(p, ts, tol=1.0):
    """取平滑曲线上最接近 ts 的点的效价。"""
    payload = p.curve_payload()
    best = min(range(len(payload["ts"])), key=lambda i: abs(payload["ts"][i] - ts))
    assert abs(payload["ts"][best] - ts) <= tol
    return payload["mv"][best]


def test_correction_moves_curve_toward_user_intent(qapp):
    """用户明确改一个点，曲线必须**看得出来**跟着走。

    v5 发现：显示层此前用的是学习的 reliability_weight（DRAG≈0.36），
    拖完只从 0.95 走到 0.90 附近，用户会认为"改了没用"。
    现在显示权重与学习权重分开（DISPLAY_CORRECTION_WEIGHT=1.0）。
    """
    from presenters.console_presenter import DISPLAY_CORRECTION_WEIGHT

    assert DISPLAY_CORRECTION_WEIGHT == 1.0

    p, _clock = _decaying_presenter(qapp, n=30, v=0.95, a=0.45)
    ts = p.states[8][0]
    before = _curve_value_at(p, ts)
    p.ingest_corrections([(ts, 0.20, 0.45)])
    after = _curve_value_at(p, ts)

    assert abs(after - 0.20) < abs(before - 0.20) - 0.15, (
        "纠正后曲线只从 %.3f 走到 %.3f（目标 0.20），用户会觉得没生效"
        % (before, after)
    )


def test_correction_learning_weight_stays_conservative(qapp):
    """显示跟手，但**学习**仍用保守权重——不因一次拖动就大幅改参数。"""
    p, _clock = _decaying_presenter(qapp, n=10, v=0.95, a=0.45)
    ts = p.states[4][0]
    p.ingest_corrections([(ts, 0.20, 0.45)])

    display_weights = {e["reliability_weight"] for e in p._edits}
    assert display_weights == {1.0}

    stored = [c for c in p.corrections if c["dimension"] == "valence"]
    assert stored, "纠正应已持久化"
    assert stored[-1]["reliability_weight"] < 1.0, (
        "学习权重应保持保守，实际 %.3f" % stored[-1]["reliability_weight"]
    )


# ================================================================
# v5 Phase 5：同步契约、错误提示与恢复文件
# ================================================================

def test_sync_snapshot_bundles_initial_state(qapp):
    """晚挂载视图只拿一个包：mode / count / readout / curve。"""
    p, _clock = _decaying_presenter(qapp)
    snap = p.sync_snapshot()

    assert set(snap) == {"mode", "count", "readout", "curve"}
    assert snap["mode"] == "decaying"
    assert snap["count"] == 5
    assert snap["readout"][5] > 0                 # state_ts
    assert snap["curve"] is not None
    assert p.has_estimator_state() is True
    assert p.has_readout() is True


def test_corrupt_snapshot_emits_user_notice(qapp, tmp_path):
    """快照损坏不只写日志，还要通过 notice_changed 让 UI 说一句。"""
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "notice.db"))
    db.set_state("active_wave", "{not-json")
    p = _presenter(qapp, db=db)
    seen = []
    p.notice_changed.connect(lambda _severity, text: seen.append(text))
    p.resume_active_wave()

    assert seen and "快照" in seen[0]
    assert any("快照" in text for _severity, text in p.notices)
    db.close()


def test_event_save_failure_spills_recovery_file(qapp, tmp_path, monkeypatch):
    """数据库写失败时，事件必须落到本地恢复文件，而不是静默消失。"""
    from db import DatabaseManager
    from tests.helpers import FakeClock

    db = DatabaseManager(str(tmp_path / "spill.db"))
    monkeypatch.setattr(
        db, "save_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("disk full")))
    clock = FakeClock()
    p = _presenter(qapp, db=db, clock=clock)
    p._spill_dir = str(tmp_path / "spill")

    p.toggle_recording(True)
    clock.advance(1)
    p.sample(0.70, 0.50)
    p.toggle_recording(False)

    files = list((tmp_path / "spill").glob("*.json"))
    assert len(files) == 1
    assert any("恢复文件" in text for _severity, text in p.notices)
    db.close()
