"""tests/test_correction_sheet.py — 纠正上滑框的首次直接测试

=== 为什么此前是空白 ===
769 条测试只覆盖 `correction_context()` 的打包结构，**从未构造过**
`CorrectionSheet` / `CorrectionCanvas` 本身——"点纠正闪退"因此能在全绿
状态下存在（详见 docs/bugfix/2026-10-25-纠正闪退修复.md）。

本文件补三类覆盖：
  1. 弹窗构造与交互（选中 / 拖动 / 手动输入 / 提交打包）；
  2. 画布最近点命中与脏索引防御；
  3. MainConsole._open_correction_sheet 的三重加固（无数据提示、
     模态期间节律暂停、异常不外泄）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest

from presenters.console_presenter import ConsolePresenter
from windows.correction_canvas import CorrectionCanvas
from windows.correction_sheet import CorrectionSheet
from tests.helpers import FakeClock


# ------------------------------------------------------------
# 夹具：一个有 3 个状态的 presenter（时间戳必须互不相同）
# ------------------------------------------------------------
@pytest.fixture
def seeded_presenter(qapp):
    clock = FakeClock(start=1_700_000_000.0)
    p = ConsolePresenter(db=None, archetype_key=None, clock=clock)
    p.toggle_recording(True)
    for v, a in ((0.6, 0.4), (0.65, 0.45), (0.7, 0.5)):
        p.recorder.submit(v, a)
        p.recorder.flush(force=True)
        clock.advance(2.0)
    p.toggle_recording(False)
    return p


@pytest.fixture
def sheet(qapp, seeded_presenter):
    ctx = seeded_presenter.correction_context()
    assert ctx["has_data"]
    s = CorrectionSheet(
        timestamps=ctx["timestamps"],
        model_v=ctx["model_v"],
        model_a=ctx["model_a"],
        baseline_v=ctx["baseline_v"],
        current_state=ctx["current_state"],
    )
    yield s
    s.deleteLater()


# ------------------------------------------------------------
# 1. 构造与交互
# ------------------------------------------------------------
def test_sheet_constructs_and_selects(qapp, sheet):
    sheet._on_select(1)
    assert sheet._selected_index == 1
    # 原值标签被填充（不再是 "原 —"）
    assert sheet._v_orig_lbl.text().startswith("原 ")
    assert sheet.tm_lbl.text() != "—"


def test_drag_updates_edits_spinboxes_and_canvas(qapp, sheet):
    sheet._on_drag(0, 0.9, 0.2)
    assert sheet._edits == {0: (0.9, 0.2)}
    assert sheet.v_sb.value() == pytest.approx(0.9)
    assert sheet.a_sb.value() == pytest.approx(0.2)
    assert sheet.canvas._edits == {0: (0.9, 0.2)}
    assert sheet.count_lbl.text() == "已修改 1 个点"


def test_drag_values_clamped_to_unit_range(qapp, sheet):
    sheet._on_drag(0, 5.0, -3.0)
    assert sheet._edits == {0: (1.0, 0.0)}


def test_drag_out_of_range_index_is_ignored(qapp, sheet):
    n = len(sheet._timestamps)
    sheet._on_drag(n + 5, 0.9, 0.2)
    sheet._on_drag(-1, 0.9, 0.2)
    assert sheet._edits == {}
    sheet._on_select(n + 5)
    assert sheet._selected_index is None


def test_manual_edit_only_after_selection(qapp, sheet):
    # 没选中时手动输入不应产生编辑
    sheet.v_sb.valueChanged.emit(0.8)
    assert sheet._edits == {}
    # 选中后生效
    sheet._on_select(2)
    sheet.v_sb.valueChanged.emit(0.8)
    assert 2 in sheet._edits
    assert sheet._edits[2][0] == pytest.approx(0.8)


def test_apply_packs_timestamped_payload(qapp, sheet):
    ts = list(sheet._timestamps)
    sheet._on_drag(1, 0.8, 0.3)
    sheet._apply()
    assert sheet.applied == [(ts[1], 0.8, 0.3)]
    assert sheet.result() == sheet.Accepted


def test_apply_with_no_edits_is_empty(qapp, sheet):
    sheet._apply()
    assert sheet.applied == []


# ------------------------------------------------------------
# 2. 画布
# ------------------------------------------------------------
def _ctx_of(p):
    ctx = p.correction_context()
    return (ctx["timestamps"], ctx["model_v"], ctx["model_a"],
            ctx["baseline_v"])


def test_canvas_nearest_point_picks_closest_timestamp(qapp, seeded_presenter):
    ts, mv, ma, bv = _ctx_of(seeded_presenter)
    canvas = CorrectionCanvas(timestamps=ts, model_v=mv, model_a=ma,
                              baseline_v=bv)
    canvas.resize(400, 300)
    canvas.show()
    qapp.processEvents()
    # 瞄准第 2 个点（用画布自己的映射函数算像素坐标）
    t0, t1 = ts[0], ts[-1]
    span = t1 - t0
    x0, _, pw, _ = canvas._plot_rect(0, 2)
    ox = int(canvas._x(ts[1], t0, span, x0, pw))
    idx = canvas._nearest_point(QPoint(ox, 40))
    assert idx == 1
    canvas.deleteLater()


def test_canvas_survives_ragged_series(qapp):
    """时间戳比 model_v/model_a 长时，交互与重绘都不许抛异常。"""
    ts = [100.0, 102.0, 104.0, 106.0]
    canvas = CorrectionCanvas(timestamps=ts, model_v=[0.6, 0.5],
                              model_a=[0.4], baseline_v=0.55)
    canvas.resize(400, 300)
    canvas.show()
    qapp.processEvents()
    # 命中"存在"的最近点后强制一次完整重绘（覆盖 edits/selected 叠层）
    canvas._edits = {3: (0.9, 0.1)}
    canvas._selected = 3
    canvas.repaint()
    QTest.mouseMove(canvas, QPoint(50, 50))
    qapp.processEvents()
    canvas.deleteLater()


# ------------------------------------------------------------
# 3. MainConsole._open_correction_sheet 的加固
# ------------------------------------------------------------
def _make_console(qapp, monkeypatch):
    from windows.main_console import MainConsole
    console = MainConsole(db=None, archetype_key=None,
                          clock=FakeClock(start=1_700_000_000.0))
    return console


def test_open_sheet_without_data_notifies_instead_of_silence(
        qapp, monkeypatch):
    console = _make_console(qapp, monkeypatch)
    seen = []
    console.presenter.notice_changed.connect(
        lambda sev, text: seen.append(text))
    console._open_correction_sheet()          # 无任何记录 → has_data False
    assert seen, "无数据时应给出可见提示，而不是静默返回"


def test_open_sheet_pauses_and_resumes_timers(qapp, monkeypatch):
    console = _make_console(qapp, monkeypatch)
    p = console.presenter
    # 造 3 个状态并让心跳处于运行态（decaying）
    p.toggle_recording(True)
    for v, a in ((0.6, 0.4), (0.65, 0.45), (0.7, 0.5)):
        p.recorder.submit(v, a)
        p.recorder.flush(force=True)
    p.toggle_recording(False)
    assert p.timer.isActive()

    observed = {}

    def fake_exec(self):
        observed["timer_active_during_exec"] = p.timer.isActive()
        return None                            # 立即关闭，走 ingest 空列表

    monkeypatch.setattr(CorrectionSheet, "exec_", fake_exec)
    console._open_correction_sheet()
    assert observed["timer_active_during_exec"] is False, "模态期间心跳必须暂停"
    assert p.timer.isActive(), "exec_ 返回后心跳必须恢复"


def test_open_sheet_swallows_construction_errors(qapp, monkeypatch):
    console = _make_console(qapp, monkeypatch)
    p = console.presenter
    p.toggle_recording(True)
    for v, a in ((0.6, 0.4), (0.65, 0.45)):
        p.recorder.submit(v, a)
        p.recorder.flush(force=True)
    p.toggle_recording(False)

    def boom(*a, **kw):
        raise RuntimeError("构造失败")

    monkeypatch.setattr("windows.main_console.CorrectionSheet", boom)
    seen = []
    p.notice_changed.connect(lambda sev, text: seen.append(text))
    # 修复点：槽内异常不再外泄（外泄 = PyQt5 qFatal abort = 闪退）
    console._open_correction_sheet()
    assert seen, "异常应转化为用户可见提示"
    assert p.timer.isActive(), "异常路径也要恢复心跳节律"


def test_ingest_after_sheet_submit_roundtrip(qapp, seeded_presenter, sheet):
    """弹窗提交 → presenter.ingest：纠正入库（内存）并触发曲线重拟合。"""
    p = seeded_presenter
    sheet._on_drag(1, 0.8, 0.3)
    sheet._apply()
    got = []
    p.curve_changed.connect(lambda payload: got.append(payload))
    n = p.ingest_corrections(sheet.applied)
    assert n == 2                              # v、a 两个维度各一条
    assert len(p.corrections) == 2
    assert len(p._edits) == 2
    assert got, "ingest 后必须推送重拟合曲线"
    payload = got[-1]
    assert any(c["channel"] == "valence" for c in payload["corrections"])
