"""baseline_control — L4 主权层：基线制度与变点检测。

REFACTOR_PLAN.md §8 Baseline Control：用户可能经历长期变化后发现
"不是我长期异常，而是我的正常状态已经发生变化"。系统提供 Baseline Control，
让用户重新定义"新的正常状态"，概念上："这不是异常，这是新的我"。

ARCHITECTURE part1 §5 基线主权模型：

  §5.1 基线 = GP 的均值函数 m(t)：
      情绪(t) = m(t) + f(t)
                │       └─ 零均值 GP：情绪波动部分
                └─ 均值函数：基线，缓慢漂移
      好处：基线编辑和曲线编辑共用同一套数学。

  §5.2 三级权限：
      1. Nudge 微调：上下拖动基线虚线 → 立即生效，EWMA 自然衰减
      2. Fork  分叉：标记某时刻为"新基线起点" → 历史分段，此后学习只用分叉后数据
      3. Reset 重置：清空个人模型 → 回到群体先验
      "分叉"是最有力量的一级，对"大波折"需求的正面回应（引入硬变点）。

  §5.3 BOCPD 提议，用户裁决：
      检测器生成候选分叉点 + 置信度 → 用户确认/拒绝 → 带标签变点数据集
      → 校准检测器阈值（把 SHIFT_CONSECUTIVE_DAYS=3/SHIFT_STD_DEVIATIONS=2.0
      这两个拍脑袋常数换成数据驱动的决策规则）。

设计约束：
  - 原始数据不变、历史记录不变，只有模型参数重新标定（REFACTOR_PLAN §8）
  - 零依赖（math + dataclass + 域对象）
  - 变点检测用轻量 EWMA + z-score 持续偏离（不引入重型 BOCPD 库，
    符合 part2 §4.4 "不需要 NN 推理栈" 与低配置原则）
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from emowave.core.domain.baseline import (
    Baseline,
    BaselineShiftEvent,
    BaselineSource,
    select_active_baseline,
)
from emowave.core.domain.model_parameters import ModelParameters


# ============================================================
# Regime（基线分段，由 fork 产生）
# ============================================================


@dataclass(frozen=True)
class BaselineRegime:
    """基线分段（part1 §5.2 分叉产生硬变点）。

    生活事件（换工作、失恋、开始服药、搬迁）会让历史数据失去参考价值。
    分叉让用户明确告诉系统："这段历史不再适用于我"。

    Attributes:
        regime_id: 分段唯一标识。
        start_time: 分段起始 Unix 时间戳。
        end_time: 分段结束时间戳，None=仍在持续。
        baseline: 该分段的基线。
        source: 分段来源（user_fork / auto_bocpd / initial）。
        confidence: 分段置信度 [0,1]。
        params_version: 该分段使用的 ModelParameters 版本（分叉后独立学习）。
    """

    regime_id: str
    start_time: float
    end_time: Optional[float]
    baseline: Baseline
    source: str = "initial"
    confidence: float = 1.0
    params_version: int = 1

    def __post_init__(self) -> None:
        if self.start_time <= 0:
            raise ValueError(f"BaselineRegime.start_time 必须为正，得到 {self.start_time}")
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError(
                f"BaselineRegime.end_time ({self.end_time}) < start_time ({self.start_time})"
            )
        c = float(self.confidence)
        object.__setattr__(self, "confidence", max(0.0, min(1.0, c)))

    def contains(self, timestamp: float) -> bool:
        if timestamp < self.start_time:
            return False
        if self.end_time is not None and timestamp >= self.end_time:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["baseline"] = self.baseline.to_dict()
        return d


# ============================================================
# 变点提议（part1 §5.3 BOCPD 提议）
# ============================================================


@dataclass(frozen=True)
class ChangePointProposal:
    """检测器生成的候选分叉点 + 置信度（part1 §5.3）。

    Attributes:
        proposal_id: 提议唯一标识。
        timestamp: 提议的变点时刻（持续偏离的起点）。
        confidence: 检测器置信度 [0,1]。
        deviation_magnitude: 偏离幅度（平均 |z-score|）。
        sustained_steps: 持续偏离步数。
        suggested_baseline: 建议的新基线（偏离段的均值）。
        detector_params: 产生此提议的检测器参数快照（用于裁决后校准）。
        created_at: 提议生成时间。
    """

    proposal_id: str
    timestamp: float
    confidence: float
    deviation_magnitude: float
    sustained_steps: int
    suggested_baseline: Dict[str, float] = field(default_factory=dict)
    detector_params: Dict[str, float] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ChangePointDetector:
    """轻量变点检测器：EWMA + z-score 持续偏离（part1 §5.3 BOCPD 的轻量化实现）。

    为什么不用重型 BOCPD：part2 §4.4 论证本项目无需复杂推理栈；
    低配置原则（REFACTOR_PLAN §12）要求 CPU 优先、小内存。
    用"连续 N 步 z-score 超阈值"检测持续漂移，足够支撑"提议-裁决"闭环，
    且阈值可被用户裁决数据驱动地校准（取代旧版拍脑袋常数）。

    可校准参数（part1 §5.3：用户裁决 → 带标签样本 → 拟合阈值）：
        z_threshold: 偏离多少标准差算"显著"（旧版 SHIFT_STD_DEVIATIONS=2.0）
        min_sustained: 连续多少步才算"持续"（旧版 SHIFT_CONSECUTIVE_DAYS=3）
    """

    def __init__(
        self,
        z_threshold: float = 2.0,
        min_sustained: int = 3,
        ewma_alpha: float = 0.3,
    ) -> None:
        if z_threshold <= 0:
            raise ValueError(f"z_threshold 必须为正，得到 {z_threshold}")
        if min_sustained < 1:
            raise ValueError(f"min_sustained 必须 ≥1，得到 {min_sustained}")
        self.z_threshold = z_threshold
        self.min_sustained = min_sustained
        self.ewma_alpha = ewma_alpha

    def scan(
        self,
        values: Sequence[float],
        timestamps: Sequence[float],
        baseline_value: float,
        sigma: float,
    ) -> Optional[ChangePointProposal]:
        """扫描一段状态值，检测相对基线的持续偏离，提议变点。

        Args:
            values: 状态值序列（如 valence 或 arousal 的平滑曲线）
            timestamps: 对应时间戳
            baseline_value: 当前基线值
            sigma: 参考标准差（个人波动幅度，来自 ModelParameters）

        Returns:
            ChangePointProposal（若检测到持续偏离）或 None
        """
        if len(values) < self.min_sustained:
            return None
        sigma = max(sigma, 1e-6)

        # 计算每点 z-score（相对基线）
        z_scores = [(v - baseline_value) / sigma for v in values]

        # 找最长的"连续同向超阈值"段
        best_run_start = -1
        best_run_len = 0
        best_run_sign = 0
        cur_start = -1
        cur_len = 0
        cur_sign = 0
        for i, z in enumerate(z_scores):
            sign = 1 if z >= self.z_threshold else (-1 if z <= -self.z_threshold else 0)
            if sign != 0 and sign == cur_sign:
                cur_len += 1
            elif sign != 0:
                cur_sign = sign
                cur_start = i
                cur_len = 1
            else:
                cur_sign = 0
                cur_len = 0
            if cur_len > best_run_len:
                best_run_len = cur_len
                best_run_start = cur_start
                best_run_sign = cur_sign

        if best_run_len < self.min_sustained or best_run_start < 0:
            return None

        # 偏离段的统计
        run_end = best_run_start + best_run_len
        run_values = values[best_run_start:run_end]
        run_z = z_scores[best_run_start:run_end]
        deviation_magnitude = sum(abs(z) for z in run_z) / len(run_z)
        suggested_new_baseline = sum(run_values) / len(run_values)

        # 置信度：综合持续长度与偏离幅度（越长越偏离越可信）
        length_conf = min(1.0, best_run_len / (2.0 * self.min_sustained))
        magnitude_conf = min(1.0, (deviation_magnitude - self.z_threshold) / self.z_threshold + 0.5)
        confidence = max(0.0, min(1.0, 0.5 * length_conf + 0.5 * magnitude_conf))

        return ChangePointProposal(
            proposal_id=f"cp_{uuid.uuid4().hex[:10]}",
            timestamp=timestamps[best_run_start],
            confidence=confidence,
            deviation_magnitude=deviation_magnitude,
            sustained_steps=best_run_len,
            suggested_baseline={"value": suggested_new_baseline, "sign": float(best_run_sign)},
            detector_params={
                "z_threshold": self.z_threshold,
                "min_sustained": float(self.min_sustained),
            },
        )

    def calibrate(self, adjudications: Sequence[Dict[str, Any]]) -> None:
        """根据用户裁决校准检测器阈值（part1 §5.3 数据驱动取代拍脑袋常数）。

        规则：
          - 拒绝率偏高（误报多）→ 提高 z_threshold / min_sustained（更保守）
          - 确认率偏高（漏报可能）→ 适度降低（更敏感）
        用带标签裁决数据集的确认率作为反馈信号。
        """
        if not adjudications:
            return
        accepted = sum(1 for a in adjudications if a.get("accepted"))
        total = len(adjudications)
        if total < 3:
            return  # 样本太少，不校准（防过拟合，呼应 part1 §9）
        accept_rate = accepted / total
        if accept_rate < 0.3:
            # 误报多：更保守
            self.z_threshold *= 1.15
            self.min_sustained += 1
        elif accept_rate > 0.8:
            # 几乎全确认：可能漏报，适度更敏感（但有下限）
            self.z_threshold = max(1.0, self.z_threshold * 0.9)
            self.min_sustained = max(2, self.min_sustained - 1)
        # 0.3-0.8 之间：阈值合适，不调整


# ============================================================
# 基线主权控制器
# ============================================================


class BaselineController:
    """基线制度：三级用户主权（nudge/fork/reset）+ 变点提议-裁决闭环。

    REFACTOR_PLAN.md §7 核心原则：模型永远只是估计，用户拥有最终解释权。
    §8：用户可以重新标定基线，"这不是异常，这是新的我"。

    不变量（REFACTOR_PLAN.md §8）：
      - 原始数据不变
      - 历史记录不变
      - 只有模型参数（基线/regime/阈值）发生重新标定

    使用方式：
        ctrl = BaselineController()
        ctrl.nudge(valence=0.67)                    # L1 微调
        ctrl.fork(timestamp, new_baseline)            # L2 分叉（硬变点）
        ctrl.reset()                                  # L3 重置
        prop = ctrl.detector.scan(...)                # 检测器提议
        ctrl.adjudicate(prop.proposal_id, accepted)   # 用户裁决
        thresholds = ctrl.recompute_thresholds(params)# 基于新基线重算阈值
    """

    def __init__(
        self,
        initial_baseline: Optional[Baseline] = None,
        detector: Optional[ChangePointDetector] = None,
    ) -> None:
        now = time.time()
        self._initial = initial_baseline or Baseline(effective_from=now)
        # 基线历史（append-only，版本链）
        self._history: List[Baseline] = [self._initial]
        # regime 分段
        self._regimes: List[BaselineRegime] = [
            BaselineRegime(
                regime_id=f"regime_{uuid.uuid4().hex[:8]}",
                start_time=self._initial.effective_from,
                end_time=None,
                baseline=self._initial,
                source="initial",
            )
        ]
        # 基线变更事件流（append-only）
        self._shift_events: List[BaselineShiftEvent] = []
        self.detector = detector or ChangePointDetector()
        # 变点提议与裁决记录
        self._proposals: Dict[str, ChangePointProposal] = {}
        self._adjudications: List[Dict[str, Any]] = []

    # ---------- 查询 ----------

    @property
    def current(self) -> Baseline:
        """当前（最新定义的）基线。

        返回历史中最后一条——即用户最近一次 nudge/fork/reset 设定的"新正常"。
        操作按顺序追加且立即生效，故 history[-1] 即当前基线，且对
        effective_from 未来时间戳的情形也稳健（baseline_at 才做历史时刻查询）。
        """
        return self._history[-1]

    @property
    def history(self) -> List[Baseline]:
        """基线历史（拷贝）。"""
        return list(self._history)

    @property
    def regimes(self) -> List[BaselineRegime]:
        """regime 分段（拷贝）。"""
        return list(self._regimes)

    @property
    def shift_events(self) -> List[BaselineShiftEvent]:
        """基线变更事件流（拷贝，append-only）。"""
        return list(self._shift_events)

    @property
    def adjudications(self) -> List[Dict[str, Any]]:
        """用户裁决记录（带标签变点数据集，part1 §5.3）。"""
        return list(self._adjudications)

    def baseline_at(self, timestamp: float) -> Optional[Baseline]:
        """指定时刻生效的基线（支持"用新基线重算历史"）。"""
        return select_active_baseline(self._history, timestamp)

    def active_regime_at(self, timestamp: float) -> Optional[BaselineRegime]:
        """指定时刻所属的 regime 分段。"""
        for r in reversed(self._regimes):
            if r.contains(timestamp):
                return r
        return None

    # ---------- L1 Nudge 微调 ----------

    def nudge(
        self,
        valence: Optional[float] = None,
        arousal: Optional[float] = None,
        intensity: Optional[float] = None,
        stability: Optional[float] = None,
        reason: str = "",
        timestamp: Optional[float] = None,
    ) -> BaselineShiftEvent:
        """L1 微调：上下拖动基线虚线（part1 §5.2）。

        语义："我最近的正常水平就是这样"。立即生效，产生新 Baseline 版本
        （version+1，parent 指向旧版），但不分段（同一 regime 内）。
        """
        ts = timestamp or time.time()
        old = self.current
        updates: Dict[str, Any] = {}
        if valence is not None:
            updates["valence"] = valence
        if arousal is not None:
            updates["arousal"] = arousal
        if intensity is not None:
            updates["intensity"] = intensity
        if stability is not None:
            updates["stability"] = stability
        if not updates:
            raise ValueError("nudge 至少需要提供一个维度的新值")
        updates["source"] = BaselineSource.USER_NUDGE
        updates["effective_from"] = ts

        new_baseline = old.with_updates(**updates)
        # 关闭旧基线的有效期
        self._close_last_baseline(ts)
        self._history.append(new_baseline)

        deltas = {
            k: float(getattr(new_baseline, k) - getattr(old, k))
            for k in ("valence", "arousal", "intensity", "stability")
            if abs(getattr(new_baseline, k) - getattr(old, k)) > 1e-9
        }
        event = BaselineShiftEvent(
            timestamp=ts,
            old_baseline_id=old.baseline_id,
            new_baseline_id=new_baseline.baseline_id,
            shift_type=BaselineSource.USER_NUDGE,
            reason=reason,
            deltas=deltas,
            user_confirmed=True,
        )
        self._shift_events.append(event)
        return event

    # ---------- L2 Fork 分叉 ----------

    def fork(
        self,
        timestamp: float,
        new_baseline: Optional[Baseline] = None,
        reason: str = "",
        source: str = "user_fork",
        confidence: float = 1.0,
    ) -> BaselineShiftEvent:
        """L2 分叉：标记某时刻为"新基线起点"（part1 §5.2 最有力量的一级）。

        语义："从这里开始，我是另一个人了"。引入硬变点：
          - 关闭当前 regime（end_time = timestamp）
          - 开启新 regime（start_time = timestamp，独立 baseline 与后续学习）
        此后学习只用分叉后数据（REFACTOR_PLAN §8：历史分段）。
        """
        if timestamp <= 0:
            raise ValueError(f"fork.timestamp 必须为正，得到 {timestamp}")
        old = self.current
        if timestamp < old.effective_from:
            raise ValueError(
                f"fork.timestamp ({timestamp}) 早于当前基线生效时间 "
                f"({old.effective_from})：不能在当前 regime 开始之前分叉"
            )
        if new_baseline is None:
            # 默认：以分叉时刻为起点，沿用旧基线值但标记为新 regime
            new_baseline = old.with_updates(
                source=BaselineSource.USER_FORK, effective_from=timestamp
            )
        else:
            # 用户提供了显式新基线
            new_baseline = Baseline(
                valence=new_baseline.valence,
                arousal=new_baseline.arousal,
                intensity=new_baseline.intensity,
                stability=new_baseline.stability,
                resting_hr=new_baseline.resting_hr,
                resting_hrv_mean=new_baseline.resting_hrv_mean,
                sleep_score=new_baseline.sleep_score,
                source=BaselineSource.USER_FORK,
                effective_from=timestamp,
                regime_id=f"regime_{uuid.uuid4().hex[:8]}",
                version=1,
                confidence=confidence,
            )

        # 关闭旧基线有效期 + 旧 regime
        self._close_last_baseline(timestamp)
        self._close_last_regime(timestamp)

        self._history.append(new_baseline)
        regime_id = new_baseline.regime_id or f"regime_{uuid.uuid4().hex[:8]}"
        self._regimes.append(
            BaselineRegime(
                regime_id=regime_id,
                start_time=timestamp,
                end_time=None,
                baseline=new_baseline,
                source=source,
                confidence=confidence,
            )
        )

        deltas = {
            k: float(getattr(new_baseline, k) - getattr(old, k))
            for k in ("valence", "arousal", "intensity", "stability")
            if abs(getattr(new_baseline, k) - getattr(old, k)) > 1e-9
        }
        event = BaselineShiftEvent(
            timestamp=timestamp,
            old_baseline_id=old.baseline_id,
            new_baseline_id=new_baseline.baseline_id,
            shift_type=BaselineSource.USER_FORK,
            reason=reason,
            deltas=deltas,
            user_confirmed=(source == "user_fork"),
            detector_confidence=confidence if source == "auto_bocpd" else 0.0,
        )
        self._shift_events.append(event)
        return event

    # ---------- L3 Reset 重置 ----------

    def reset(self, reason: str = "", timestamp: Optional[float] = None) -> BaselineShiftEvent:
        """L3 重置：清空个人模型，回到群体先验（part1 §5.2）。

        语义："忘掉我，重新开始"。基线回到群体先验，开启全新 regime。
        """
        ts = timestamp or time.time()
        old = self.current
        pop = Baseline(
            source=BaselineSource.USER_RESET,
            effective_from=ts,
            regime_id=f"regime_{uuid.uuid4().hex[:8]}",
            version=1,
            confidence=0.3,
        )
        self._close_last_baseline(ts)
        self._close_last_regime(ts)
        self._history.append(pop)
        self._regimes.append(
            BaselineRegime(
                regime_id=pop.regime_id or f"regime_{uuid.uuid4().hex[:8]}",
                start_time=ts,
                end_time=None,
                baseline=pop,
                source="user_reset",
                confidence=1.0,
            )
        )
        deltas = {
            k: float(getattr(pop, k) - getattr(old, k))
            for k in ("valence", "arousal", "intensity", "stability")
            if abs(getattr(pop, k) - getattr(old, k)) > 1e-9
        }
        event = BaselineShiftEvent(
            timestamp=ts,
            old_baseline_id=old.baseline_id,
            new_baseline_id=pop.baseline_id,
            shift_type=BaselineSource.USER_RESET,
            reason=reason,
            deltas=deltas,
            user_confirmed=True,
        )
        self._shift_events.append(event)
        return event

    # ---------- 内部：关闭有效期 ----------

    def _close_last_baseline(self, ts: float) -> None:
        """把历史中最后一条基线的 effective_to 设为 ts（frozen 需重建）。"""
        if not self._history:
            return
        last = self._history[-1]
        if last.effective_to is None:
            # Baseline 是 frozen，用 with_updates 会改 version；这里直接重建同版本
            closed = Baseline(
                valence=last.valence,
                arousal=last.arousal,
                intensity=last.intensity,
                stability=last.stability,
                resting_hr=last.resting_hr,
                resting_hrv_mean=last.resting_hrv_mean,
                sleep_score=last.sleep_score,
                baseline_id=last.baseline_id,
                source=last.source,
                effective_from=last.effective_from,
                effective_to=ts,
                regime_id=last.regime_id,
                version=last.version,
                parent_id=last.parent_id,
                confidence=last.confidence,
                meta=dict(last.meta),
            )
            self._history[-1] = closed

    def _close_last_regime(self, ts: float) -> None:
        """把最后一个 regime 的 end_time 设为 ts（frozen 需重建）。"""
        if not self._regimes:
            return
        last = self._regimes[-1]
        if last.end_time is None:
            closed = BaselineRegime(
                regime_id=last.regime_id,
                start_time=last.start_time,
                end_time=ts,
                baseline=last.baseline,
                source=last.source,
                confidence=last.confidence,
                params_version=last.params_version,
            )
            self._regimes[-1] = closed

    # ---------- 变点提议-裁决闭环（part1 §5.3） ----------

    def propose(
        self,
        values: Sequence[float],
        timestamps: Sequence[float],
        channel: str = "valence",
        params: Optional[ModelParameters] = None,
    ) -> Optional[ChangePointProposal]:
        """用检测器扫描状态序列，生成候选分叉点提议。"""
        params = params or ModelParameters()
        baseline_value = (
            self.current.valence if channel == "valence" else self.current.arousal
        )
        sigma = (
            params.sigma_valence if channel == "valence" else params.sigma_arousal
        )
        proposal = self.detector.scan(values, timestamps, baseline_value, sigma)
        if proposal is not None:
            self._proposals[proposal.proposal_id] = proposal
        return proposal

    def adjudicate(self, proposal_id: str, accepted: bool, reason: str = "") -> Optional[BaselineShiftEvent]:
        """用户裁决一个变点提议（part1 §5.3 闭环）。

        确认 → 采纳分叉（auto_bocpd 来源的 fork）；
        拒绝 → 抑制该点（记录为负样本）。
        裁决结果累积为带标签数据集，用于校准检测器阈值。

        Returns:
            若确认则返回 BaselineShiftEvent，否则 None。
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ValueError(f"未知提议 id: {proposal_id}")

        self._adjudications.append(
            {
                "proposal_id": proposal_id,
                "accepted": accepted,
                "confidence": proposal.confidence,
                "timestamp": proposal.timestamp,
                "reason": reason,
            }
        )

        event = None
        if accepted:
            # 采纳：以提议的新基线分叉
            suggested_value = proposal.suggested_baseline.get("value")
            new_bl = None
            if suggested_value is not None:
                cur = self.current
                new_bl = Baseline(
                    valence=suggested_value,
                    arousal=cur.arousal,
                    intensity=cur.intensity,
                    stability=cur.stability,
                    resting_hr=cur.resting_hr,
                    resting_hrv_mean=cur.resting_hrv_mean,
                    sleep_score=cur.sleep_score,
                )
            event = self.fork(
                timestamp=proposal.timestamp,
                new_baseline=new_bl,
                reason=reason or "BOCPD 提议经用户确认",
                source="auto_bocpd",
                confidence=proposal.confidence,
            )
        # 拒绝：仅记录负样本，不改基线

        # 每次裁决后尝试校准检测器（数据驱动取代拍脑袋常数）
        self.detector.calibrate(self._adjudications)
        return event

    # ---------- 阈值重算（REFACTOR_PLAN §8 / §28 Phase 5） ----------

    def recompute_thresholds(
        self,
        params: Optional[ModelParameters] = None,
        baseline: Optional[Baseline] = None,
        warning_k: float = 1.5,
        high_risk_k: float = 2.5,
    ) -> Dict[str, float]:
        """基于（新）基线重新计算个人阈值。

        阈值 = 基线 + k·σ（基线相对），因此基线 nudge/fork 后阈值自动跟随平移。
        这实现 REFACTOR_PLAN §8："基于新基线重新计算阈值"，并支撑 §22 Baseline Test：
        用户重标定后，新正常状态相对新基线偏离≈0，不再被判为异常。

        Args:
            params: 个人参数（提供 σ）
            baseline: 目标基线（默认当前基线）
            warning_k: 预警阈值的 σ 倍数
            high_risk_k: 高风险阈值的 σ 倍数

        Returns:
            {"warning_valence","high_risk_valence","warning_arousal","high_risk_arousal",
             "baseline_valence","baseline_arousal"}
        """
        params = params or ModelParameters()
        bl = baseline or self.current
        return {
            "baseline_valence": bl.valence,
            "baseline_arousal": bl.arousal,
            # 效价：低于基线 k·σ 为预警/高风险（效价越低越负面）
            "warning_valence": _clip(bl.valence - warning_k * params.sigma_valence),
            "high_risk_valence": _clip(bl.valence - high_risk_k * params.sigma_valence),
            # 唤醒：高于基线 k·σ 为预警/高风险（唤醒越高越激动）
            "warning_arousal": _clip(bl.arousal + warning_k * params.sigma_arousal),
            "high_risk_arousal": _clip(bl.arousal + high_risk_k * params.sigma_arousal),
        }

    def is_abnormal(
        self,
        valence: float,
        arousal: float,
        params: Optional[ModelParameters] = None,
        z_threshold: float = 2.0,
        timestamp: Optional[float] = None,
    ) -> bool:
        """判断给定状态相对（指定时刻的）基线是否异常。

        用于 §22 Baseline Test：用户重标定基线后，新正常状态不再被判异常。
        """
        params = params or ModelParameters()
        ts = timestamp or time.time()
        bl = self.baseline_at(ts) or self.current
        z_v = abs(valence - bl.valence) / max(params.sigma_valence, 1e-6)
        z_a = abs(arousal - bl.arousal) / max(params.sigma_arousal, 1e-6)
        return z_v > z_threshold or z_a > z_threshold


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
