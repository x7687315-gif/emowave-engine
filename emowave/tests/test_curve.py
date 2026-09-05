"""EmotionCurve / CurveEdit / CurveEditor / 节点网格单元测试。

覆盖 REFACTOR_PLAN.md §28 Phase 3 完成标准：
  "用户可以直接通过曲线修改当前情绪状态"

重点验证：
  - 节点网格降采样（part2 §2.4）
  - CurveEdit append-only + 峰终加权 reliability_weight
  - 三条曲线（raw / estimate / corrected）
  - 拖拽 / 手动输入 / undo / redo
  - 原始观察不可变（编辑不覆盖 raw）
  - 派生产物可重建性
"""

import time

import pytest

from emowave.core.curve.curve import (
    CurveChannel,
    CurveEdit,
    CurveEditor,
    EmotionCurve,
    build_node_grid,
    _nearest_index,
)
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource


def make_obs(n, start_ts=1000.0, v=0.5, a=0.5):
    return [
        Observation(timestamp=start_ts + i, valence=v, arousal=a,
                    source=ObservationSource.USER)
        for i in range(n)
    ]


# ============================================================
# build_node_grid（part2 §2.4 降采样）
# ============================================================


def test_node_grid_fewer_points_than_nodes_returns_all():
    ts = [float(i) for i in range(10)]
    grid = build_node_grid(ts, n_nodes=300)
    assert grid == list(range(10))


def test_node_grid_downsamples_large_series():
    ts = [float(i) for i in range(2000)]
    grid = build_node_grid(ts, n_nodes=300)
    assert len(grid) <= 300
    assert len(grid) > 250  # 接近目标节点数
    # 索引应正序、去重
    assert grid == sorted(grid)
    assert len(set(grid)) == len(grid)
    # 应覆盖首尾
    assert grid[0] == 0
    assert grid[-1] == 1999


def test_node_grid_endpoints_included():
    ts = [float(i) for i in range(1000)]
    grid = build_node_grid(ts, n_nodes=100)
    assert grid[0] == 0
    assert grid[-1] == 999


def test_node_grid_empty():
    assert build_node_grid([], n_nodes=300) == []


def test_node_grid_uniform_spacing():
    """节点应大致均匀分布。"""
    ts = [float(i) for i in range(900)]
    grid = build_node_grid(ts, n_nodes=9)
    # 9 个节点应大致每 100 个采样取一个
    diffs = [grid[i + 1] - grid[i] for i in range(len(grid) - 1)]
    assert max(diffs) - min(diffs) <= 2  # 大致均匀


def test_nearest_index():
    ts = [0.0, 10.0, 20.0, 30.0]
    assert _nearest_index(ts, 0.0) == 0
    assert _nearest_index(ts, 9.0) == 1
    assert _nearest_index(ts, 11.0) == 1
    assert _nearest_index(ts, 35.0) == 3
    assert _nearest_index(ts, -5.0) == 0


# ============================================================
# CurveEdit
# ============================================================


def test_curve_edit_minimal():
    e = CurveEdit(timestamp=1000.0, channel=CurveChannel.VALENCE,
                  value_before=0.4, value_after=0.7)
    assert e.channel == CurveChannel.VALENCE
    assert e.delta == pytest.approx(0.3)
    assert 0.0 <= e.reliability_weight <= 1.0
    assert e.edit_id.startswith("edit_")


def test_curve_edit_channel_accepts_string():
    e = CurveEdit(timestamp=1000.0, channel="arousal", value_before=0.4, value_after=0.6)
    assert e.channel == CurveChannel.AROUSAL


def test_curve_edit_clips_values():
    e = CurveEdit(timestamp=1000.0, channel="valence", value_before=1.5, value_after=-0.5)
    assert e.value_before == 1.0
    assert e.value_after == 0.0


def test_curve_edit_rejects_nonpositive_timestamp():
    with pytest.raises(ValueError):
        CurveEdit(timestamp=0.0, channel="valence", value_before=0.4, value_after=0.6)


def test_curve_edit_rejects_negative_latency():
    with pytest.raises(ValueError):
        CurveEdit(timestamp=1000.0, channel="valence", value_before=0.4,
                  value_after=0.6, edit_latency_sec=-1.0)


def test_curve_edit_is_frozen():
    e = CurveEdit(timestamp=1000.0, channel="valence", value_before=0.4, value_after=0.6)
    with pytest.raises(Exception):
        e.value_after = 0.9  # type: ignore[misc]


def test_curve_edit_peak_end_weighting():
    """峰值附近（高 salience）的编辑权重高于平淡中段（低 salience）。"""
    e_peak = CurveEdit(timestamp=time.time() - 5, channel="valence",
                       value_before=0.4, value_after=0.7, salience=0.95,
                       edit_latency_sec=5.0)
    e_flat = CurveEdit(timestamp=time.time() - 5, channel="valence",
                       value_before=0.4, value_after=0.7, salience=0.05,
                       edit_latency_sec=5.0)
    assert e_peak.reliability_weight > e_flat.reliability_weight


def test_curve_edit_to_pseudo_observation_dict():
    e = CurveEdit(timestamp=1000.0, channel="valence", value_before=0.4,
                  value_after=0.7, reliability_weight=0.8)
    d = e.to_pseudo_observation_dict()
    assert d["timestamp"] == 1000.0
    assert d["channel"] == "valence"
    assert d["value"] == 0.7
    assert d["reliability_weight"] == 0.8
    assert d["meta"]["edit_id"] == e.edit_id


def test_curve_edit_roundtrip():
    e = CurveEdit(timestamp=1000.0, channel="arousal", value_before=0.3,
                  value_after=0.6, salience=0.7, drag_velocity=0.2)
    d = e.to_dict()
    assert d["channel"] == "arousal"
    restored = CurveEdit.from_dict(d)
    assert restored.channel == e.channel
    assert restored.value_after == e.value_after
    assert restored.edit_id == e.edit_id


# ============================================================
# CurveEditor：rebuild 与三条曲线
# ============================================================


def test_editor_rebuild_returns_curve():
    obs = make_obs(30, v=0.6, a=0.4)
    editor = CurveEditor(obs, n_nodes=50)
    curve = editor.rebuild()
    assert isinstance(curve, EmotionCurve)
    assert len(curve) > 0
    assert curve.version == 1
    assert curve.n_edits_applied == 0


def test_editor_three_curves_present():
    """曲线应同时含 raw（原始）与 estimate（模型）两套数据（REFACTOR_PLAN §6.3）。"""
    obs = make_obs(30, v=0.6, a=0.4)
    editor = CurveEditor(obs, n_nodes=50)
    curve = editor.rebuild()
    # Model Estimate
    assert len(curve.mean_valence) == len(curve)
    # Raw Observation
    assert len(curve.raw_valence) == len(curve)
    # 置信带方差
    assert len(curve.var_valence) == len(curve)
    # 控制点
    assert len(curve.control_point_indices) >= 2


def test_editor_raw_observations_immutable():
    """编辑器持有的原始观察不可被外部修改（part1 §3.2.1）。"""
    obs = make_obs(20, v=0.5)
    editor = CurveEditor(obs)
    raw = editor.raw_observations
    raw.append(Observation(timestamp=9999.0, valence=1.0, arousal=1.0))
    # 内部 _raw 不受影响（返回的是拷贝）
    assert len(editor.raw_observations) == 20


def test_editor_rebuild_is_reproducible():
    """相同 (raw + edits + θ) 重建结果一致（REFACTOR_PLAN §22 Reproducibility）。"""
    obs = make_obs(30, v=0.6, a=0.4)
    e1 = CurveEditor(obs, n_nodes=50)
    e2 = CurveEditor(obs, n_nodes=50)
    c1 = e1.rebuild()
    c2 = e2.rebuild()
    assert c1.mean_valence == c2.mean_valence
    assert c1.mean_arousal == c2.mean_arousal


def test_editor_confidence_band():
    obs = make_obs(30, v=0.6)
    editor = CurveEditor(obs, n_nodes=50)
    curve = editor.rebuild()
    lo, hi = curve.confidence_band(len(curve) // 2, "valence", z=1.0)
    assert lo <= curve.mean_valence[len(curve) // 2] <= hi
    # ±2σ 带应比 ±1σ 宽
    lo2, hi2 = curve.confidence_band(len(curve) // 2, "valence", z=2.0)
    assert (hi2 - lo2) >= (hi - lo) - 1e-9


# ============================================================
# CurveEditor：拖拽编辑
# ============================================================


def test_drag_edit_creates_correction_and_bumps_version():
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=50)
    c0 = editor.rebuild()
    edit = editor.drag_edit(timestamp=1015.0, channel="valence", value_after=0.8)
    assert isinstance(edit, CurveEdit)
    assert editor.version == c0.version + 1
    assert len(editor.edits) == 1


def test_drag_edit_moves_corrected_curve():
    """拖拽后 User Corrected 曲线应向拖拽值移动（Phase 3 完成标准）。

    注意：测试用假时间戳（~1000.0），必须显式传 edit_latency_sec，
    否则 drag_edit 按 time.time()-timestamp 算出 ~17 亿秒延迟，
    时近性权重 exp(-Δt/τ)→0，编辑被完全忽略。
    """
    obs = make_obs(40, v=0.4)
    editor = CurveEditor(obs, n_nodes=40)
    before = editor.rebuild()
    mid = len(before) // 2
    ts_mid = before.timestamps[mid]

    editor.drag_edit(timestamp=ts_mid, channel="valence", value_after=0.85,
                     salience=0.9, drag_velocity=0.0, edit_latency_sec=5.0)
    after = editor.rebuild()

    # 找最接近 ts_mid 的节点
    idx = min(range(len(after)), key=lambda i: abs(after.timestamps[i] - ts_mid))
    assert after.mean_valence[idx] > before.mean_valence[idx] + 0.1


def test_drag_edit_does_not_change_raw_curve():
    """拖拽改变 Model Estimate / Corrected，但 Raw Observation 曲线不变。"""
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    before = editor.rebuild()
    raw_before = list(before.raw_valence)

    editor.drag_edit(timestamp=before.timestamps[15], channel="valence", value_after=0.9)
    after = editor.rebuild()
    # raw 曲线不受编辑影响（原始数据不可变）
    assert after.raw_valence == raw_before


def test_manual_edit():
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    edit = editor.manual_edit(timestamp=1015.0, channel="valence", value=0.75)
    assert isinstance(edit, CurveEdit)
    assert edit.value_after == 0.75
    assert len(editor.edits) == 1


def test_multiple_drag_edits_accumulate():
    obs = make_obs(40, v=0.4)
    editor = CurveEditor(obs, n_nodes=40)
    editor.drag_edit(timestamp=1010.0, channel="valence", value_after=0.8)
    editor.drag_edit(timestamp=1020.0, channel="valence", value_after=0.8)
    editor.drag_edit(timestamp=1030.0, channel="arousal", value_after=0.7)
    assert len(editor.edits) == 3
    curve = editor.rebuild()
    assert curve.n_edits_applied == 3


# ============================================================
# Undo / Redo（REFACTOR_PLAN §28 Phase 3）
# ============================================================


def test_undo_removes_last_edit():
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    editor.drag_edit(timestamp=1015.0, channel="valence", value_after=0.8)
    assert editor.can_undo() is True
    undone = editor.undo()
    assert isinstance(undone, CurveEdit)
    assert len(editor.edits) == 0
    assert editor.can_undo() is False


def test_undo_restores_curve():
    """undo 后曲线应回到编辑前的形状。"""
    obs = make_obs(40, v=0.4)
    editor = CurveEditor(obs, n_nodes=40)
    before = editor.rebuild()
    mid = len(before) // 2

    editor.drag_edit(timestamp=before.timestamps[mid], channel="valence",
                     value_after=0.9, salience=0.9, edit_latency_sec=5.0)
    after = editor.rebuild()
    idx = min(range(len(after)), key=lambda i: abs(after.timestamps[i] - before.timestamps[mid]))
    assert after.mean_valence[idx] > before.mean_valence[idx]

    editor.undo()
    restored = editor.rebuild()
    idx2 = min(range(len(restored)), key=lambda i: abs(restored.timestamps[i] - before.timestamps[mid]))
    assert restored.mean_valence[idx2] == pytest.approx(before.mean_valence[mid], abs=1e-6)


def test_redo_reapplies_edit():
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    editor.drag_edit(timestamp=1015.0, channel="valence", value_after=0.8)
    editor.undo()
    assert editor.can_redo() is True
    redone = editor.redo()
    assert isinstance(redone, CurveEdit)
    assert len(editor.edits) == 1
    assert editor.can_redo() is False


def test_undo_on_empty_returns_none():
    obs = make_obs(10, v=0.5)
    editor = CurveEditor(obs)
    assert editor.can_undo() is False
    assert editor.undo() is None


def test_redo_on_empty_returns_none():
    obs = make_obs(10, v=0.5)
    editor = CurveEditor(obs)
    assert editor.can_redo() is False
    assert editor.redo() is None


def test_new_edit_clears_redo_stack():
    """新编辑应清空 redo 栈（标准 undo/redo 语义：新分支丢弃旧 redo）。"""
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    editor.drag_edit(timestamp=1015.0, channel="valence", value_after=0.8)
    editor.undo()
    assert editor.can_redo() is True
    # 新编辑
    editor.drag_edit(timestamp=1020.0, channel="valence", value_after=0.7)
    assert editor.can_redo() is False


def test_multiple_undo_redo_sequence():
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    editor.drag_edit(timestamp=1010.0, channel="valence", value_after=0.7)
    editor.drag_edit(timestamp=1015.0, channel="valence", value_after=0.8)
    editor.drag_edit(timestamp=1020.0, channel="valence", value_after=0.9)
    assert len(editor.edits) == 3
    editor.undo()
    editor.undo()
    assert len(editor.edits) == 1
    editor.redo()
    assert len(editor.edits) == 2


# ============================================================
# 导出
# ============================================================


def test_export_edits():
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    editor.drag_edit(timestamp=1015.0, channel="valence", value_after=0.8)
    exported = editor.export_edits()
    assert len(exported) == 1
    assert exported[0]["channel"] == "valence"
    assert exported[0]["value_after"] == 0.8


def test_editor_with_custom_params():
    """编辑器应接受自定义 ModelParameters（个人化 ℓ/σ）。"""
    params = ModelParameters(ell_valence=500.0, sigma_valence=0.2)
    obs = make_obs(30, v=0.5)
    editor = CurveEditor(obs, params=params, n_nodes=30)
    curve = editor.rebuild()
    assert curve.hyperparameters["ell_valence"] == 500.0
    assert curve.hyperparameters["sigma_valence"] == 0.2


def test_curve_version_increments_per_edit():
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    v0 = editor.version
    editor.drag_edit(timestamp=1015.0, channel="valence", value_after=0.8)
    assert editor.version == v0 + 1
    editor.drag_edit(timestamp=1020.0, channel="valence", value_after=0.7)
    assert editor.version == v0 + 2


def test_drag_edit_baseline_channel_rejected():
    """baseline 通道不能通过曲线拖拽编辑（MEDIUM 修复：消除静默空操作）。

    基线是 GP 均值函数，走 L4 主权 BaselineController.nudge/fork；
    曲线拖拽的伪观察对 baseline 无意义（smoother 会静默忽略）。
    """
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    with pytest.raises(ValueError):
        editor.drag_edit(timestamp=1015.0, channel="baseline", value_after=0.6)


def test_manual_edit_baseline_channel_rejected():
    obs = make_obs(30, v=0.4)
    editor = CurveEditor(obs, n_nodes=30)
    with pytest.raises(ValueError):
        editor.manual_edit(timestamp=1015.0, channel="baseline", value=0.6)
