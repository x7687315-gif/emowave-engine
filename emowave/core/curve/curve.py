"""curve — EmotionCurve / CurveEdit / CurveEditor（可编辑情绪曲线）。

REFACTOR_PLAN.md §6 可编辑情绪曲线是新版第一核心交互：
    Anchor Points → Interpolation → Emotion Curve
用户可以直接拖动关键点调整状态。

三条曲线（REFACTOR_PLAN.md §6.3）：
    ① Raw Observation    原始数据是什么（不可变）
    ② Model Estimate     模型怎么理解
    ③ User Corrected     我自己怎么定义

关键约束（part1 §3.2.1 原始数据不可变）：
    raw_observations : append-only，永不修改
    user_edits       : append-only，独立日志
    EmotionCurve     : 派生产物，可由 (raw + edits + θ) 完全重建
用户编辑绝不能覆盖原始观测——否则模型会去拟合用户被记忆偏差污染后的回忆，
系统性摧毁学习信号。曲线是可重算的"视图"，原始观测是"事实"。

节点网格降采样（part2 §2.4）：
    原始采样 N=2000 → 节点网格 N≈300 → RTS → 光滑曲线 → 渲染
    曲线视觉平滑度取决于节点数而非采样点数，RTS O(N) 因此从 172ms 降到 ~26ms。
    用户拖动的是稀疏控制点，本就不是逐采样点拖拽。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from emowave.core.curve.smoother import RTSSmoother, SmoothedTrajectory
from emowave.core.domain.correction import compute_reliability_weight, CorrectionSource
from emowave.core.domain.observation import Observation


class CurveChannel(str, Enum):
    """可编辑的曲线通道。"""

    VALENCE = "valence"
    AROUSAL = "arousal"
    BASELINE = "baseline"


def build_node_grid(
    timestamps: List[float], n_nodes: int = 300
) -> List[int]:
    """从原始采样时间戳中抽取节点网格索引（part2 §2.4 降采样）。

    在曲线上均匀选取 n_nodes 个节点（按时间等距），返回其在原始序列中的
    最近索引。用户拖动作用于这些稀疏节点，而非全部采样点。

    Args:
        timestamps: 原始采样时间戳（正序）
        n_nodes: 目标节点数（T1 档 ~150，T2 档 ~300，见 part2 §5.2）

    Returns:
        节点索引列表（去重、正序）。若采样点数 ≤ n_nodes 则返回全部索引。
    """
    n = len(timestamps)
    if n == 0:
        return []
    if n <= n_nodes:
        return list(range(n))

    # 按时间等距选取 n_nodes 个目标点，映射到最近采样索引
    t0 = timestamps[0]
    t1 = timestamps[-1]
    span = t1 - t0
    indices: List[int] = []
    seen = set()
    for i in range(n_nodes):
        target_t = t0 + span * (i / (n_nodes - 1)) if n_nodes > 1 else t0
        # 二分/线性找最近索引（采样通常均匀，线性足够）
        idx = _nearest_index(timestamps, target_t)
        if idx not in seen:
            seen.add(idx)
            indices.append(idx)
    indices.sort()
    return indices


def _nearest_index(timestamps: List[float], target: float) -> int:
    """二分查找最接近 target 的索引。"""
    lo, hi = 0, len(timestamps) - 1
    if target <= timestamps[lo]:
        return lo
    if target >= timestamps[hi]:
        return hi
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if timestamps[mid] < target:
            lo = mid
        else:
            hi = mid
    # 比较 lo 与 hi 谁更近
    return lo if abs(timestamps[lo] - target) <= abs(timestamps[hi] - target) else hi


@dataclass(frozen=True)
class CurveEdit:
    """一次用户曲线编辑。append-only，永不修改原始观测（part1 §3.2.1）。

    Attributes:
        edit_id: 唯一标识。
        timestamp: 被编辑的时间点。
        channel: 被编辑的通道（valence/arousal/baseline）。
        value_before: 编辑前模型曲线的值。
        value_after: 用户拖到的值。
        edit_session_mood: 编辑时用户当前情绪强度 [0,1]（心境一致性降权）。
        edit_latency_sec: 距被编辑时刻的时间间隔（秒，时近性降权）。
        drag_velocity: 拖动速度（快速拖 = 更随意 = 降权）。
        salience: 被编辑点显著性 [0,1]（峰终定律，越高越可靠）。
        seconds_before_event_end: 距事件结尾秒数（结尾提升）。
        reliability_weight: 由峰终加权公式算出的最终权重 [0,1]。
        created_at: 编辑创建时间。
    """

    timestamp: float
    channel: CurveChannel
    value_before: float
    value_after: float
    edit_session_mood: float = 0.0
    edit_latency_sec: float = 0.0
    drag_velocity: float = 0.0
    salience: float = 0.5
    seconds_before_event_end: Optional[float] = None
    reliability_weight: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    edit_id: str = ""

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            raise ValueError(f"CurveEdit.timestamp 必须为正，得到 {self.timestamp}")
        if not isinstance(self.channel, CurveChannel):
            object.__setattr__(self, "channel", CurveChannel(self.channel))
        for name in ("value_before", "value_after", "edit_session_mood", "salience"):
            v = _clip(getattr(self, name))
            object.__setattr__(self, name, v)
        if self.edit_latency_sec < 0:
            raise ValueError(f"edit_latency_sec 不能为负，得到 {self.edit_latency_sec}")
        if self.drag_velocity < 0:
            raise ValueError(f"drag_velocity 不能为负，得到 {self.drag_velocity}")

        if self.reliability_weight is None:
            w = compute_reliability_weight(
                source=CorrectionSource.DRAG,
                edit_latency_sec=self.edit_latency_sec,
                salience=self.salience,
                current_mood_deviation=self.edit_session_mood,
                seconds_before_event_end=self.seconds_before_event_end,
            )
            if self.drag_velocity > 0:
                w *= 0.5 + 0.5 / (1.0 + self.drag_velocity)
            object.__setattr__(self, "reliability_weight", _clip(w))
        else:
            object.__setattr__(self, "reliability_weight", _clip(self.reliability_weight))

        if not self.edit_id:
            eid = f"edit_{int(self.timestamp * 1000)}_{self.channel.value}_{uuid.uuid4().hex[:6]}"
            object.__setattr__(self, "edit_id", eid)

    @property
    def delta(self) -> float:
        return self.value_after - self.value_before

    def to_pseudo_observation_dict(self) -> Dict[str, Any]:
        """转为 RTS 平滑器可消费的伪观察 dict（见 smoother._merge_edits）。"""
        return {
            "timestamp": self.timestamp,
            "channel": self.channel.value,
            "value": self.value_after,
            "reliability_weight": self.reliability_weight,
            "meta": {"edit_id": self.edit_id},
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["channel"] = self.channel.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CurveEdit":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)


@dataclass(frozen=True)
class EmotionCurve:
    """派生产物：可由 (raw observations + edits + θ) 完全重建（part1 §3.2.1）。

    Attributes:
        timestamps: (N,) 节点时间戳
        mean_valence: (N,) 平滑后效价均值（Model Estimate 曲线）
        mean_arousal: (N,) 平滑后唤醒均值
        var_valence: (N,) 效价后验方差（置信带）
        var_arousal: (N,) 唤醒后验方差
        raw_valence: (N,) 原始观察效价（Raw Observation 曲线，可能含 None）
        raw_arousal: (N,) 原始观察唤醒
        control_point_indices: 可拖拽控制点在节点序列中的索引
        hyperparameters: 本次平滑使用的 θ（ℓ/σ 等）
        version: 曲线版本（每次编辑后递增）
        n_edits_applied: 已应用的编辑数
    """

    timestamps: List[float] = field(default_factory=list)
    mean_valence: List[float] = field(default_factory=list)
    mean_arousal: List[float] = field(default_factory=list)
    var_valence: List[float] = field(default_factory=list)
    var_arousal: List[float] = field(default_factory=list)
    raw_valence: List[Optional[float]] = field(default_factory=list)
    raw_arousal: List[Optional[float]] = field(default_factory=list)
    control_point_indices: List[int] = field(default_factory=list)
    hyperparameters: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    n_edits_applied: int = 0

    def __len__(self) -> int:
        return len(self.timestamps)

    def valence_at(self, i: int) -> float:
        return self.mean_valence[i]

    def arousal_at(self, i: int) -> float:
        return self.mean_arousal[i]

    def confidence_band(
        self, i: int, channel: str = "valence", z: float = 1.0
    ) -> tuple:
        """第 i 个节点在 z 倍标准差下的置信区间（part1 §6.2 置信带渲染）。"""
        import math

        if channel == "valence":
            mean, var = self.mean_valence[i], self.var_valence[i]
        elif channel == "arousal":
            mean, var = self.mean_arousal[i], self.var_arousal[i]
        else:
            raise ValueError(f"未知通道: {channel}")
        std = math.sqrt(max(0.0, var))
        return (_clip(mean - z * std), _clip(mean + z * std))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CurveEditor:
    """可编辑情绪曲线的控制器：拖拽 / 手动输入 / undo / redo。

    职责（REFACTOR_PLAN.md §6, §7 Correction First）：
      - 持有不可变的 raw observations（原始事实，永不修改）
      - 维护 append-only 的 edit 日志
      - 每次编辑后调用 RTS 平滑重建曲线（增量重平滑，part1 §3.2）
      - 支持 undo/redo（编辑栈）

    核心原则（REFACTOR_PLAN.md §7）：模型永远只是估计，用户拥有最终解释权。
    但用户修改的是"模型解释"（曲线视图），不是篡改历史事实（raw observations）。

    使用方式：
        editor = CurveEditor(raw_observations, params)
        curve = editor.rebuild()                    # 初始曲线（Model Estimate）
        editor.drag_edit(timestamp, "valence", 0.7) # 用户拖动
        curve = editor.rebuild()                    # 重平滑（User Corrected）
        editor.undo()                               # 撤销
    """

    def __init__(
        self,
        observations: List[Observation],
        params=None,
        config=None,
        n_nodes: int = 300,
    ) -> None:
        # 原始观察：不可变事实，按时间排序后冻结
        self._raw: List[Observation] = sorted(observations, key=lambda o: o.timestamp)
        self._smoother = RTSSmoother(params=params, config=config)
        self._n_nodes = n_nodes
        # append-only 编辑日志（已应用的）
        self._edits: List[CurveEdit] = []
        # undo/redo 栈
        self._undo_stack: List[CurveEdit] = []
        self._redo_stack: List[CurveEdit] = []
        self._version = 1

    @property
    def raw_observations(self) -> List[Observation]:
        """原始观察（不可变事实）。返回拷贝防止外部修改。"""
        return list(self._raw)

    @property
    def edits(self) -> List[CurveEdit]:
        """已应用的编辑日志（append-only）。"""
        return list(self._edits)

    @property
    def version(self) -> int:
        return self._version

    def rebuild(self) -> EmotionCurve:
        """由 (raw + edits + θ) 重建曲线（part1 §3.2.1 派生产物可重建性）。

        流程：
          1. 在原始观察上跑 RTS 平滑（含编辑伪观察）得到全分辨率轨迹
          2. 抽取节点网格（part2 §2.4 降采样）
          3. 组装 EmotionCurve（三条曲线：raw / estimate / 置信带）
        """
        edit_dicts = [e.to_pseudo_observation_dict() for e in self._edits]
        traj = self._smoother.smooth(self._raw, edits=edit_dicts)

        if len(traj) == 0:
            return EmotionCurve(version=self._version, n_edits_applied=len(self._edits))

        # 节点网格降采样
        node_idx = build_node_grid(traj.timestamps, self._n_nodes)

        # 原始观察曲线（Raw Observation，从原始观察取值，编辑不影响它）
        raw_v: List[Optional[float]] = []
        raw_a: List[Optional[float]] = []
        obs_by_ts = {o.timestamp: o for o in self._raw}
        for i in node_idx:
            ts = traj.timestamps[i]
            o = obs_by_ts.get(ts)
            raw_v.append(o.valence if o else None)
            raw_a.append(o.arousal if o else None)

        # 控制点：节点网格中每隔几个取一个可拖拽点（稀疏）
        n_nodes = len(node_idx)
        n_controls = max(2, min(n_nodes, self._n_nodes // 3))
        step = max(1, n_nodes // n_controls)
        control_indices = list(range(0, n_nodes, step))
        if control_indices and control_indices[-1] != n_nodes - 1:
            control_indices.append(n_nodes - 1)

        curve = EmotionCurve(
            timestamps=[traj.timestamps[i] for i in node_idx],
            mean_valence=[traj.mean_valence[i] for i in node_idx],
            mean_arousal=[traj.mean_arousal[i] for i in node_idx],
            var_valence=[traj.var_valence[i] for i in node_idx],
            var_arousal=[traj.var_arousal[i] for i in node_idx],
            raw_valence=raw_v,
            raw_arousal=raw_a,
            control_point_indices=control_indices,
            hyperparameters=self._hyperparameter_snapshot(),
            version=self._version,
            n_edits_applied=len(self._edits),
        )
        return curve

    def _hyperparameter_snapshot(self) -> Dict[str, Any]:
        p = self._smoother.params
        return {
            "ell_valence": p.ell_valence,
            "ell_arousal": p.ell_arousal,
            "sigma_valence": p.sigma_valence,
            "sigma_arousal": p.sigma_arousal,
            "sigma_noise": p.sigma_noise,
            "model_version": p.extra.get("model_version", "") if isinstance(p.extra, dict) else "",
        }

    # ============================================================
    # 编辑操作
    # ============================================================

    @staticmethod
    def _check_editable_channel(channel: str) -> None:
        """曲线编辑只支持情绪通道；baseline 通道走 L4 主权（BaselineController）。

        part1 §5.1：基线是 GP 的均值函数 m(t)，通过 nudge/fork 编辑，
        不是通过曲线拖拽的伪观察。若允许 drag_edit(channel=baseline)，
        smoother._merge_edits 会因该伪观察无情绪通道而静默忽略它——
        用户改了基线却毫无反应（MEDIUM 静默 UX bug）。这里显式拒绝并指引正确入口。
        """
        ch = getattr(channel, "value", channel)
        if ch == CurveChannel.BASELINE.value:
            raise ValueError(
                "baseline 通道不能通过曲线拖拽编辑；基线是 GP 均值函数，"
                "请用 BaselineController.nudge()/fork()（L4 主权层，part1 §5.2）。"
            )

    def drag_edit(
        self,
        timestamp: float,
        channel: str,
        value_after: float,
        *,
        value_before: Optional[float] = None,
        edit_session_mood: float = 0.0,
        drag_velocity: float = 0.0,
        salience: float = 0.5,
        seconds_before_event_end: Optional[float] = None,
        edit_latency_sec: Optional[float] = None,
    ) -> CurveEdit:
        """用户拖动控制点（REFACTOR_PLAN.md §6.1 第一核心交互）。

        编辑作为加权伪观察记录，不覆盖原始观察。reliability_weight 由
        峰终加权公式自动计算（part1 §4.2）。

        Args:
            timestamp: 被拖动的时间点
            channel: "valence" | "arousal" | "baseline"
            value_after: 拖到的新值
            value_before: 拖动前模型值（可选，默认从当前曲线插值）
            edit_latency_sec: 距被编辑时刻的回忆延迟（秒）。None 时按
                time.time()-timestamp 自动计算（生产环境时间戳为真实 Unix 时间）。
                UI/测试可显式传入以精确控制时近性降权。
            其余：峰终加权所需上下文

        Returns:
            创建的 CurveEdit（已压入编辑日志）
        """
        self._check_editable_channel(channel)
        if edit_latency_sec is None:
            edit_latency = max(0.0, time.time() - timestamp)
        else:
            edit_latency = max(0.0, float(edit_latency_sec))
        if value_before is None:
            value_before = self._interpolate_model_value(timestamp, channel)

        edit = CurveEdit(
            timestamp=timestamp,
            channel=CurveChannel(channel),
            value_before=value_before,
            value_after=value_after,
            edit_session_mood=edit_session_mood,
            edit_latency_sec=edit_latency,
            drag_velocity=drag_velocity,
            salience=salience,
            seconds_before_event_end=seconds_before_event_end,
        )
        self._apply_edit(edit)
        return edit

    def manual_edit(
        self,
        timestamp: float,
        channel: str,
        value: float,
        reason: Optional[str] = None,
        edit_latency_sec: Optional[float] = None,
    ) -> CurveEdit:
        """用户手动输入数值（REFACTOR_PLAN.md §6.1）。

        与 drag_edit 的区别：source 语义上是"手动输入"，可靠性先验更高
        （ salience 默认给高值，因为手动输入通常是深思熟虑的）。
        """
        self._check_editable_channel(channel)
        value_before = self._interpolate_model_value(timestamp, channel)
        if edit_latency_sec is None:
            edit_latency = max(0.0, time.time() - timestamp)
        else:
            edit_latency = max(0.0, float(edit_latency_sec))
        edit = CurveEdit(
            timestamp=timestamp,
            channel=CurveChannel(channel),
            value_before=value_before,
            value_after=value,
            salience=0.8,  # 手动输入先验更可靠
            edit_latency_sec=edit_latency,
        )
        self._apply_edit(edit)
        return edit

    def _apply_edit(self, edit: CurveEdit) -> None:
        """把编辑压入日志，版本递增，清空 redo 栈（新编辑分支）。"""
        self._edits.append(edit)
        self._undo_stack.append(edit)
        self._redo_stack.clear()
        self._version += 1

    def _interpolate_model_value(self, timestamp: float, channel: str) -> float:
        """从当前曲线线性插值出 timestamp 处的模型值（作为 value_before）。"""
        traj = self._smoother.smooth(
            self._raw, edits=[e.to_pseudo_observation_dict() for e in self._edits]
        )
        if len(traj) == 0:
            return 0.5
        ts = traj.timestamps
        # 找到 timestamp 的插入位置
        if timestamp <= ts[0]:
            idx = 0
        elif timestamp >= ts[-1]:
            idx = len(ts) - 1
        else:
            idx = _nearest_index(ts, timestamp)
        vals = traj.mean_valence if channel == "valence" else traj.mean_arousal
        return _clip(vals[idx])

    # ============================================================
    # Undo / Redo（REFACTOR_PLAN.md §28 Phase 3）
    # ============================================================

    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    def undo(self) -> Optional[CurveEdit]:
        """撤销最近一次编辑。返回被撤销的 CurveEdit，无可撤销时返回 None。"""
        if not self._undo_stack:
            return None
        edit = self._undo_stack.pop()
        # 从已应用日志移除
        if edit in self._edits:
            self._edits.remove(edit)
        self._redo_stack.append(edit)
        self._version += 1
        return edit

    def redo(self) -> Optional[CurveEdit]:
        """重做最近一次撤销的编辑。"""
        if not self._redo_stack:
            return None
        edit = self._redo_stack.pop()
        self._edits.append(edit)
        self._undo_stack.append(edit)
        self._version += 1
        return edit

    # ============================================================
    # 导出
    # ============================================================

    def export_edits(self) -> List[Dict[str, Any]]:
        """导出编辑日志（append-only，供持久化与个人学习消费）。"""
        return [e.to_dict() for e in self._edits]


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
