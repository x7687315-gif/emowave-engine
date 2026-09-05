"""BaselineController + ChangePointDetector 单元测试（L4 主权层）。

覆盖 REFACTOR_PLAN.md §28 Phase 5 与 ARCHITECTURE part1 §5：
  - 三级用户主权 nudge/fork/reset（part1 §5.2）
  - regime 分段（fork 引入硬变点）
  - 基线历史 / 版本链 / BaselineShiftEvent
  - 基于新基线重新计算阈值
  - §22 Baseline Test：用户重标定后，新正常状态不再被判为异常
  - BOCPD 提议 + 用户裁决闭环（part1 §5.3）+ 阈值数据驱动校准
"""

import time

import pytest

from emowave.core.calibration.baseline_control import (
    BaselineController,
    BaselineRegime,
    ChangePointDetector,
    ChangePointProposal,
)
from emowave.core.domain.baseline import Baseline, BaselineSource
from emowave.core.domain.model_parameters import ModelParameters


# ============================================================
# BaselineController 初始化与查询
# ============================================================


def test_controller_starts_with_initial_baseline():
    ctrl = BaselineController()
    assert ctrl.current is not None
    assert len(ctrl.history) == 1
    assert len(ctrl.regimes) == 1
    assert ctrl.regimes[0].source == "initial"


def test_controller_custom_initial_baseline():
    bl = Baseline(valence=0.7, arousal=0.3)
    ctrl = BaselineController(initial_baseline=bl)
    assert ctrl.current.valence == pytest.approx(0.7)


def test_controller_baseline_at_returns_active():
    ctrl = BaselineController()
    now = time.time()
    bl = ctrl.baseline_at(now)
    assert bl is not None


# ============================================================
# L1 Nudge 微调（part1 §5.2）
# ============================================================


def test_nudge_creates_new_version():
    ctrl = BaselineController()
    old_id = ctrl.current.baseline_id
    old_version = ctrl.current.version
    event = ctrl.nudge(valence=0.67, reason="我最近正常水平更高")
    assert ctrl.current.valence == pytest.approx(0.67)
    assert ctrl.current.baseline_id != old_id
    assert ctrl.current.version == old_version + 1
    assert ctrl.current.parent_id == old_id


def test_nudge_does_not_create_new_regime():
    """nudge 是同一 regime 内的版本更新，不分段。"""
    ctrl = BaselineController()
    assert len(ctrl.regimes) == 1
    ctrl.nudge(valence=0.6)
    assert len(ctrl.regimes) == 1  # 未分段


def test_nudge_records_shift_event():
    ctrl = BaselineController()
    event = ctrl.nudge(valence=0.67, arousal=0.52, reason="微调")
    assert len(ctrl.shift_events) == 1
    assert event.shift_type == BaselineSource.USER_NUDGE
    assert event.user_confirmed is True
    assert "valence" in event.deltas


def test_nudge_preserves_untouched_dimensions():
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5, arousal=0.4, intensity=0.35))
    ctrl.nudge(valence=0.7)
    assert ctrl.current.arousal == pytest.approx(0.4)  # 未变
    assert ctrl.current.intensity == pytest.approx(0.35)


def test_nudge_requires_at_least_one_dimension():
    ctrl = BaselineController()
    with pytest.raises(ValueError):
        ctrl.nudge(reason="空")


def test_nudge_closes_previous_baseline_validity():
    """nudge 后旧基线的 effective_to 被关闭，新基线接管。"""
    ctrl = BaselineController()
    t0 = time.time()
    first = ctrl.current
    ctrl.nudge(valence=0.7, timestamp=t0 + 10)
    # 旧基线在 t0+10 之前生效，之后失效
    assert first.effective_to is None  # 历史里的旧对象引用（已被重建关闭）
    closed = ctrl.history[0]
    assert closed.effective_to == pytest.approx(t0 + 10)
    assert ctrl.baseline_at(t0 + 5).baseline_id == closed.baseline_id
    assert ctrl.baseline_at(t0 + 20).valence == pytest.approx(0.7)


# ============================================================
# L2 Fork 分叉（part1 §5.2 最有力量的一级）
# ============================================================


def test_fork_creates_new_regime():
    ctrl = BaselineController()
    t_fork = time.time() + 100
    new_bl = Baseline(valence=0.3, arousal=0.6)
    ctrl.fork(timestamp=t_fork, new_baseline=new_bl, reason="失恋后我的正常变了")
    assert len(ctrl.regimes) == 2
    # 旧 regime 被关闭
    assert ctrl.regimes[0].end_time == pytest.approx(t_fork)
    # 新 regime 从分叉点开始
    assert ctrl.regimes[1].start_time == pytest.approx(t_fork)
    assert ctrl.regimes[1].end_time is None


def test_fork_changes_current_baseline():
    ctrl = BaselineController()
    t_fork = time.time() + 100
    new_bl = Baseline(valence=0.3, arousal=0.6)
    ctrl.fork(timestamp=t_fork, new_baseline=new_bl)
    # 分叉后（时间 > t_fork）当前基线是新基线
    assert ctrl.baseline_at(t_fork + 50).valence == pytest.approx(0.3)
    # 分叉前仍是旧基线
    assert ctrl.baseline_at(t_fork - 50).valence != pytest.approx(0.3)


def test_fork_records_shift_event_with_fork_type():
    ctrl = BaselineController()
    t_fork = time.time() + 100
    event = ctrl.fork(timestamp=t_fork, new_baseline=Baseline(valence=0.3), reason="分叉")
    assert event.shift_type == BaselineSource.USER_FORK
    assert event.user_confirmed is True
    assert len(ctrl.shift_events) == 1


def test_fork_default_baseline_when_none():
    """fork 不提供新基线时，沿用旧值但开启新 regime。"""
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.55))
    t_fork = time.time() + 100
    ctrl.fork(timestamp=t_fork, reason="新阶段")
    assert len(ctrl.regimes) == 2
    assert ctrl.regimes[1].baseline.valence == pytest.approx(0.55)


def test_fork_rejects_nonpositive_timestamp():
    ctrl = BaselineController()
    with pytest.raises(ValueError):
        ctrl.fork(timestamp=0.0)


def test_regimes_do_not_overlap():
    """多次 fork 后 regime 分段不重叠、首尾相接。"""
    ctrl = BaselineController()
    base = time.time()
    ctrl.fork(timestamp=base + 100, new_baseline=Baseline(valence=0.3))
    ctrl.fork(timestamp=base + 200, new_baseline=Baseline(valence=0.6))
    ctrl.fork(timestamp=base + 300, new_baseline=Baseline(valence=0.45))
    regimes = ctrl.regimes
    assert len(regimes) == 4
    for i in range(len(regimes) - 1):
        assert regimes[i].end_time == pytest.approx(regimes[i + 1].start_time)


def test_active_regime_at():
    ctrl = BaselineController()
    base = time.time()
    ctrl.fork(timestamp=base + 100, new_baseline=Baseline(valence=0.3))
    r_before = ctrl.active_regime_at(base + 50)
    r_after = ctrl.active_regime_at(base + 150)
    assert r_before is not None and r_after is not None
    assert r_before.regime_id != r_after.regime_id


# ============================================================
# L3 Reset 重置（part1 §5.2）
# ============================================================


def test_reset_returns_to_population_prior():
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.9, arousal=0.9))
    ctrl.reset(reason="忘掉我，重新开始")
    pop = Baseline()  # 群体先验默认值
    assert ctrl.current.valence == pytest.approx(pop.valence)
    assert ctrl.current.arousal == pytest.approx(pop.arousal)
    assert ctrl.current.source == BaselineSource.USER_RESET


def test_reset_creates_new_regime():
    ctrl = BaselineController()
    ctrl.reset()
    assert len(ctrl.regimes) == 2
    assert ctrl.regimes[1].source == "user_reset"


def test_reset_records_shift_event():
    ctrl = BaselineController()
    event = ctrl.reset(reason="重置")
    assert event.shift_type == BaselineSource.USER_RESET
    assert event.user_confirmed is True


# ============================================================
# 阈值重算（REFACTOR_PLAN §8）+ Baseline Test（§22）
# ============================================================


def test_recompute_thresholds_relative_to_baseline():
    """阈值 = 基线 ± k·σ（基线相对）。"""
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5, arousal=0.5))
    params = ModelParameters(sigma_valence=0.1, sigma_arousal=0.1)
    th = ctrl.recompute_thresholds(params=params, warning_k=1.5, high_risk_k=2.5)
    assert th["warning_valence"] == pytest.approx(0.5 - 1.5 * 0.1)  # 0.35
    assert th["high_risk_valence"] == pytest.approx(0.5 - 2.5 * 0.1)  # 0.25
    assert th["warning_arousal"] == pytest.approx(0.5 + 1.5 * 0.1)  # 0.65
    assert th["high_risk_arousal"] == pytest.approx(0.5 + 2.5 * 0.1)  # 0.75


def test_thresholds_shift_after_nudge():
    """基线 nudge 后阈值自动跟随平移（REFACTOR_PLAN §8 基于新基线重算阈值）。"""
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5, arousal=0.5))
    params = ModelParameters(sigma_valence=0.1, sigma_arousal=0.1)
    th_before = ctrl.recompute_thresholds(params=params)
    ctrl.nudge(valence=0.7, arousal=0.5)
    th_after = ctrl.recompute_thresholds(params=params)
    # 效价基线上移 0.2，预警阈值也应上移 0.2
    assert th_after["warning_valence"] == pytest.approx(th_before["warning_valence"] + 0.2)


def test_baseline_test_recalibration_stops_false_alarm():
    """§22 Baseline Test：用户重标定后，新正常状态不再被判为异常。

    场景：用户状态长期从 0.5 漂移到 0.7（生活变化）。
      - 重标定前：0.7 相对旧基线 0.5 偏离大 → 判为异常
      - 用户 nudge 基线到 0.7（"这是我的新正常"）
      - 重标定后：0.7 相对新基线 0.7 偏离≈0 → 不再异常
    """
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5, arousal=0.4))
    params = ModelParameters(sigma_valence=0.08, sigma_arousal=0.08)

    # 重标定前：状态 0.7 相对基线 0.5 是异常（z = 0.2/0.08 = 2.5 > 2）
    assert ctrl.is_abnormal(0.7, 0.4, params=params, z_threshold=2.0) is True

    # 用户重标定："0.7 才是我的新正常"
    ctrl.nudge(valence=0.7, reason="这不是异常，这是新的我")

    # 重标定后：同样状态 0.7 不再异常（z ≈ 0）
    assert ctrl.is_abnormal(0.7, 0.4, params=params, z_threshold=2.0) is False


def test_baseline_test_fork_recalibration():
    """fork 分叉后，分叉后的状态相对新 regime 基线判断，不再误报历史异常。"""
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5, arousal=0.4))
    params = ModelParameters(sigma_valence=0.08, sigma_arousal=0.08)
    base = time.time()
    t_fork = base + 100
    # 分叉到 valence=0.3 的新 regime
    ctrl.fork(timestamp=t_fork, new_baseline=Baseline(valence=0.3, arousal=0.4),
              reason="人生阶段转变")
    # 分叉后状态 0.3 相对新基线不异常
    assert ctrl.is_abnormal(0.3, 0.4, params=params, timestamp=t_fork + 50) is False
    # 分叉前状态 0.5 相对旧基线不异常
    assert ctrl.is_abnormal(0.5, 0.4, params=params, timestamp=t_fork - 50) is False


# ============================================================
# ChangePointDetector（part1 §5.3）
# ============================================================


def test_detector_no_changepoint_for_stable_signal():
    """稳定信号（围绕基线波动）不应提议变点。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=3)
    values = [0.5, 0.51, 0.49, 0.5, 0.52, 0.48, 0.5]
    ts = [float(i) for i in range(len(values))]
    prop = det.scan(values, ts, baseline_value=0.5, sigma=0.1)
    assert prop is None


def test_detector_proposes_on_sustained_deviation():
    """持续偏离基线应提议变点。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=3)
    # 前 5 点在基线附近，后 6 点持续高 0.4（z=4 > 2）
    values = [0.5, 0.51, 0.49, 0.5, 0.52] + [0.9, 0.91, 0.9, 0.89, 0.9, 0.91]
    ts = [float(i) for i in range(len(values))]
    prop = det.scan(values, ts, baseline_value=0.5, sigma=0.1)
    assert prop is not None
    assert isinstance(prop, ChangePointProposal)
    assert prop.sustained_steps >= 3
    assert prop.deviation_magnitude > 2.0
    # 提议的变点应在偏离段起点附近
    assert prop.timestamp >= 4.0


def test_detector_ignores_single_spike():
    """单点尖峰（非持续）不应提议变点（min_sustained 防护）。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=3)
    values = [0.5, 0.5, 0.95, 0.5, 0.5]  # 仅 1 点偏离
    ts = [float(i) for i in range(len(values))]
    prop = det.scan(values, ts, baseline_value=0.5, sigma=0.1)
    assert prop is None


def test_detector_suggests_new_baseline():
    """提议应包含偏离段均值作为建议新基线。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=3)
    values = [0.5, 0.5, 0.5] + [0.8, 0.82, 0.78, 0.8]
    ts = [float(i) for i in range(len(values))]
    prop = det.scan(values, ts, baseline_value=0.5, sigma=0.1)
    assert prop is not None
    assert prop.suggested_baseline["value"] == pytest.approx(0.8, abs=0.05)


def test_detector_confidence_higher_for_longer_stronger_deviation():
    """更长更偏离的段 → 置信度更高。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=3)
    ts_short = [float(i) for i in range(8)]
    short = det.scan([0.5]*5 + [0.75]*3, ts_short, 0.5, 0.1)  # z=2.5, 3 步
    ts_long = [float(i) for i in range(13)]
    long_ = det.scan([0.5]*5 + [0.95]*8, ts_long, 0.5, 0.1)    # z=4.5, 8 步
    assert long_.confidence > short.confidence


def test_detector_rejects_invalid_params():
    with pytest.raises(ValueError):
        ChangePointDetector(z_threshold=0.0)
    with pytest.raises(ValueError):
        ChangePointDetector(min_sustained=0)


def test_detector_too_short_series():
    det = ChangePointDetector(min_sustained=5)
    prop = det.scan([0.9, 0.9], [0.0, 1.0], baseline_value=0.5, sigma=0.1)
    assert prop is None


# ============================================================
# 提议-裁决闭环 + 阈值校准（part1 §5.3）
# ============================================================


def test_propose_and_adjudicate_accept_forks():
    """用户确认提议 → 采纳分叉。"""
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5))
    base = time.time()
    values = [0.5, 0.5, 0.5] + [0.85, 0.86, 0.84, 0.85, 0.86]
    ts = [base + 10 + i for i in range(len(values))]
    prop = ctrl.propose(values, ts, channel="valence",
                        params=ModelParameters(sigma_valence=0.1))
    assert prop is not None
    event = ctrl.adjudicate(prop.proposal_id, accepted=True, reason="确实变了")
    assert event is not None
    assert event.shift_type == BaselineSource.USER_FORK
    # 分叉后基线接近建议值
    assert ctrl.baseline_at(prop.timestamp + 10).valence == pytest.approx(0.85, abs=0.05)


def test_propose_and_adjudicate_reject_no_change():
    """用户拒绝提议 → 基线不变，仅记录负样本。"""
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5))
    old_id = ctrl.current.baseline_id
    values = [0.5, 0.5, 0.5] + [0.85, 0.86, 0.84, 0.85]
    ts = [1000.0 + i for i in range(len(values))]
    prop = ctrl.propose(values, ts, channel="valence",
                        params=ModelParameters(sigma_valence=0.1))
    event = ctrl.adjudicate(prop.proposal_id, accepted=False, reason="只是临时波动")
    assert event is None
    assert ctrl.current.baseline_id == old_id  # 基线未变
    assert len(ctrl.adjudications) == 1
    assert ctrl.adjudications[0]["accepted"] is False


def test_adjudicate_unknown_proposal_raises():
    ctrl = BaselineController()
    with pytest.raises(ValueError):
        ctrl.adjudicate("nonexistent_id", accepted=True)


def test_adjudications_accumulate_as_labeled_dataset():
    """裁决累积为带标签数据集（part1 §5.3 校准 BOCPD 的基础）。"""
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5))
    base = time.time()
    params = ModelParameters(sigma_valence=0.1)
    for round_idx in range(4):
        values = [0.5, 0.5, 0.5] + [0.85 + 0.01 * round_idx] * 5
        ts = [base + round_idx * 100 + 10 + i for i in range(len(values))]
        prop = ctrl.propose(values, ts, channel="valence", params=params)
        if prop:
            ctrl.adjudicate(prop.proposal_id, accepted=(round_idx % 2 == 0))
    assert len(ctrl.adjudications) >= 3
    accepted = sum(1 for a in ctrl.adjudications if a["accepted"])
    assert accepted >= 1


def test_detector_calibrates_conservative_on_many_rejections():
    """拒绝率偏高（误报多）→ 检测器提高阈值（更保守），part1 §5.3 数据驱动。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=3)
    z_before = det.z_threshold
    sustained_before = det.min_sustained
    # 模拟多次拒绝
    adjudications = [{"accepted": False} for _ in range(5)]
    det.calibrate(adjudications)
    assert det.z_threshold > z_before
    assert det.min_sustained > sustained_before


def test_detector_calibrates_sensitive_on_many_accepts():
    """确认率偏高 → 检测器适度降低阈值（更敏感），但有下限。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=4)
    z_before = det.z_threshold
    adjudications = [{"accepted": True} for _ in range(5)]
    det.calibrate(adjudications)
    assert det.z_threshold < z_before
    assert det.z_threshold >= 1.0  # 下限保护


def test_detector_no_calibration_on_few_samples():
    """裁决样本太少（<3）不校准（防过拟合，呼应 part1 §9）。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=3)
    z_before = det.z_threshold
    det.calibrate([{"accepted": False}, {"accepted": False}])
    assert det.z_threshold == z_before


def test_detector_stable_no_calibration_in_balanced_range():
    """确认率在 0.3-0.8 之间（阈值合适）→ 不调整。"""
    det = ChangePointDetector(z_threshold=2.0, min_sustained=3)
    z_before = det.z_threshold
    # 50% 确认率
    adjudications = [{"accepted": True}, {"accepted": False},
                     {"accepted": True}, {"accepted": False}]
    det.calibrate(adjudications)
    assert det.z_threshold == z_before


# ============================================================
# BaselineRegime
# ============================================================


def test_regime_contains():
    bl = Baseline(valence=0.5)
    r = BaselineRegime(regime_id="r1", start_time=100.0, end_time=200.0, baseline=bl)
    assert r.contains(150.0) is True
    assert r.contains(50.0) is False
    assert r.contains(250.0) is False


def test_regime_open_ended_contains():
    bl = Baseline(valence=0.5)
    r = BaselineRegime(regime_id="r1", start_time=100.0, end_time=None, baseline=bl)
    assert r.contains(1e9) is True
    assert r.contains(50.0) is False


def test_regime_rejects_invalid_times():
    bl = Baseline(valence=0.5)
    with pytest.raises(ValueError):
        BaselineRegime(regime_id="r1", start_time=0.0, end_time=None, baseline=bl)
    with pytest.raises(ValueError):
        BaselineRegime(regime_id="r1", start_time=200.0, end_time=100.0, baseline=bl)


def test_regime_clips_confidence():
    bl = Baseline(valence=0.5)
    r = BaselineRegime(regime_id="r1", start_time=100.0, end_time=None, baseline=bl, confidence=1.5)
    assert r.confidence == 1.0


def test_regime_to_dict():
    bl = Baseline(valence=0.5)
    r = BaselineRegime(regime_id="r1", start_time=100.0, end_time=None, baseline=bl)
    d = r.to_dict()
    assert d["regime_id"] == "r1"
    assert "baseline" in d


# ============================================================
# 完整主权流程集成
# ============================================================


def test_full_sovereignty_flow():
    """完整流程：初始 → nudge → 检测提议 → 裁决分叉 → reset，全程记录事件与 regime。"""
    ctrl = BaselineController(initial_baseline=Baseline(valence=0.5, arousal=0.4))
    base = time.time()

    # L1 nudge
    ctrl.nudge(valence=0.55, timestamp=base + 10, reason="微调")
    assert len(ctrl.shift_events) == 1

    # 检测器提议 + 用户确认分叉
    values = [0.55, 0.55, 0.55] + [0.85, 0.86, 0.84, 0.85, 0.86]
    ts = [base + 20 + i for i in range(len(values))]
    prop = ctrl.propose(values, ts, channel="valence",
                        params=ModelParameters(sigma_valence=0.1))
    assert prop is not None
    ctrl.adjudicate(prop.proposal_id, accepted=True, reason="确实进入新阶段")
    assert len(ctrl.shift_events) == 2
    assert len(ctrl.regimes) == 2

    # L3 reset
    ctrl.reset(timestamp=base + 200, reason="重新开始")
    assert len(ctrl.shift_events) == 3
    assert len(ctrl.regimes) == 3
    assert ctrl.current.source == BaselineSource.USER_RESET

    # 历史与版本链完整
    assert len(ctrl.history) >= 3
    # 阈值可基于当前基线重算
    th = ctrl.recompute_thresholds(params=ModelParameters())
    assert "warning_valence" in th and "high_risk_arousal" in th
