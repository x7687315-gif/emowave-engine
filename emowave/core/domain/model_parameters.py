"""ModelParameters — 个人化模型参数集。

ModelParameters 是 EmoWave 2.0 长期学习的产物：随着用户不断提供
Observation 与 UserCorrection，这些参数从群体先验收缩到个人后验
（REFACTOR_PLAN.md §9 Personal Calibration）。

核心参数（ARCHITECTURE part1 §2.4）：

    ℓ_valence    效价的时间相关尺度（情绪惯性，Kuppens 2010）
    ℓ_arousal    唤醒的时间相关尺度
    σ_valence    效价的波动幅度
    σ_arousal    唤醒的波动幅度
    σ_noise      观测噪声（用户自我报告的可靠度倒数）

Matérn ν=3/2 状态空间形式（ARCHITECTURE part1 §2.4）：

    λ = √3 / ℓ
    F(Δt) = e^(-λΔt) · [[1+λΔt, Δt], [-λ²Δt, 1-λΔt]]
    P∞    = σ² · [[1, 0], [0, λ²]]
    Q(Δt) = P∞ - F(Δt)·P∞·F(Δt)ᵀ

层次先验收缩（ARCHITECTURE part1 §3.3，Taylor 2017 269 引用）：

    θ_user = argmax [ log p(y_user | θ) + log p(θ | μ_pop, Σ_pop) ]
    shrinkage_alpha = 个人证据 / (个人证据 + 群体先验)
    n_events < 20 → 强制强收缩，防小样本过拟合

设计约束：
  - 零依赖：只用 math + dataclass
  - 版本化：每次学习产生新版本，支持回滚与 A/B 测试
  - 冷启动可用：默认值 = 群体先验，未学习时也能跑
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


class LearningStage(str, Enum):
    """学习阶段。决定 shrinkage 强度与可用功能。"""

    COLD_START = "cold_start"       # n_events < 5，纯群体先验
    EARLY = "early"                 # 5 ≤ n < 20，强收缩
    TRANSITION = "transition"       # 20 ≤ n < 40，线性过渡到个人
    PERSONAL = "personal"           # n ≥ 40，个人为主


# 群体先验默认值（ARCHITECTURE part1 §1.2 反推 + Kuppens 情绪惯性研究）
# ℓ = 300 秒 = 5 分钟：情绪在 5 分钟尺度上保持相关，与心理学研究一致
# 旧版 velocity_damping=0.85 隐含 ℓ≈11-22 秒，是"记忆问题"（part1 §11）
_POP_ELL_VALENCE = 300.0
_POP_ELL_AROUSAL = 240.0
_POP_SIGMA_VALENCE = 0.15
_POP_SIGMA_AROUSAL = 0.20
_POP_SIGMA_NOISE = 0.10

# 收缩阈值
_N_EVENTS_COLD = 5
_N_EVENTS_EARLY = 20
_N_EVENTS_TRANSITION = 40


@dataclass(frozen=True)
class ModelParameters:
    """个人化模型参数集。

    Attributes:
        ell_valence: 效价的时间相关尺度（秒）。越大 = 情绪惯性越强。
        ell_arousal: 唤醒的时间相关尺度（秒）。
        sigma_valence: 效价的波动幅度（稳态标准差）。
        sigma_arousal: 唤醒的波动幅度。
        sigma_noise: 观测噪声标准差。
        n_events_fitted: 已用于拟合的事件数。决定 shrinkage 强度。
        log_likelihood: 最后一次拟合的边际对数似然（诊断用）。
        shrinkage_alpha: 向群体先验收缩的程度 [0, 1]。
            0 = 纯群体，1 = 纯个人。由 n_events_fitted 与拟合质量共同决定。
        stage: 学习阶段，见 LearningStage。
        version: 单调递增版本号，支持回滚。
        parent_version: 上一个版本号。
        updated_at: 最后一次更新的 Unix 时间戳。
        regime_id: 所属基线 regime。分叉后新 regime 独立学习。
        extra: 扩展参数槽（如未来的多输出 GP 耦合系数、周期核参数）。
    """

    ell_valence: float = _POP_ELL_VALENCE
    ell_arousal: float = _POP_ELL_AROUSAL
    sigma_valence: float = _POP_SIGMA_VALENCE
    sigma_arousal: float = _POP_SIGMA_AROUSAL
    sigma_noise: float = _POP_SIGMA_NOISE
    n_events_fitted: int = 0
    log_likelihood: float = 0.0
    shrinkage_alpha: float = 0.0
    stage: LearningStage = LearningStage.COLD_START
    version: int = 1
    parent_version: Optional[int] = None
    updated_at: float = field(default_factory=time.time)
    regime_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ℓ 必须为正（长度尺度不能 ≤ 0）
        for name in ("ell_valence", "ell_arousal"):
            v = float(getattr(self, name))
            if v <= 0:
                raise ValueError(f"ModelParameters.{name} 必须为正，得到 {v}")
            # 上限防护：ℓ > 1 小时（3600s）在情绪建模上没有物理意义
            if v > 3600.0:
                v = 3600.0
            object.__setattr__(self, name, v)

        # σ 必须为正
        for name in ("sigma_valence", "sigma_arousal", "sigma_noise"):
            v = float(getattr(self, name))
            if v <= 0:
                raise ValueError(f"ModelParameters.{name} 必须为正，得到 {v}")
            if v > 5.0:
                v = 5.0
            object.__setattr__(self, name, v)

        # n_events_fitted 非负
        n = int(self.n_events_fitted)
        if n < 0:
            raise ValueError(f"n_events_fitted 不能为负，得到 {n}")
        object.__setattr__(self, "n_events_fitted", n)

        # shrinkage_alpha 裁剪到 [0, 1]
        a = float(self.shrinkage_alpha)
        if a < 0.0:
            a = 0.0
        elif a > 1.0:
            a = 1.0
        object.__setattr__(self, "shrinkage_alpha", a)

        # stage 自动推导（若未显式传入或与 n_events 不一致，以 n_events 为准）
        derived = _derive_stage(n)
        if self.stage != derived:
            object.__setattr__(self, "stage", derived)

        # Enum 允许传字符串
        if not isinstance(self.stage, LearningStage):
            object.__setattr__(self, "stage", LearningStage(self.stage))

        if self.updated_at <= 0:
            raise ValueError(
                f"ModelParameters.updated_at 必须为正，得到 {self.updated_at}"
            )

    # ---------- 派生量 ----------

    def lambda_valence(self) -> float:
        """Matérn ν=3/2 的 λ = √3 / ℓ（效价维度）。"""
        return math.sqrt(3.0) / self.ell_valence

    def lambda_arousal(self) -> float:
        """Matérn ν=3/2 的 λ = √3 / ℓ（唤醒维度）。"""
        return math.sqrt(3.0) / self.ell_arousal

    def autocorrelation(self, dimension: str, lag_sec: float) -> float:
        """给定维度在 lag_sec 后的自相关系数（Matérn ν=3/2 闭式解）。

        Matérn ν=3/2 的自相关函数：
            ρ(τ) = (1 + √3·τ/ℓ) · exp(-√3·τ/ℓ)

        用于诊断"模型认为情绪在多长时间内保持相关"
        （ARCHITECTURE part1 §1.2 的量化脚本核心）。
        """
        ell = self.ell_valence if dimension == "valence" else self.ell_arousal
        x = math.sqrt(3.0) * abs(float(lag_sec)) / ell
        return (1.0 + x) * math.exp(-x)

    def population_shrinkage_weight(self) -> float:
        """当前应向群体先验收缩多少（0=纯个人，1=纯群体）。

        分段规则（ARCHITECTURE part1 §9 风险表：前 20 事件强制强收缩）：
            n < 5    → 1.00（纯群体）
            5 ≤ n<20 → 0.80（强收缩）
            20≤ n<40 → 线性 0.80 → 0.20
            n ≥ 40   → 0.20（个人为主，仍保留 20% 群体锚点防漂移）
        """
        n = self.n_events_fitted
        if n < _N_EVENTS_COLD:
            return 1.0
        if n < _N_EVENTS_EARLY:
            return 0.8
        if n < _N_EVENTS_TRANSITION:
            # 线性插值 0.8 → 0.2
            t = (n - _N_EVENTS_EARLY) / (_N_EVENTS_TRANSITION - _N_EVENTS_EARLY)
            return 0.8 - 0.6 * t
        return 0.2

    def with_updates(self, **kwargs: Any) -> "ModelParameters":
        """返回一个应用了 kwargs 更新的**新** ModelParameters。

        自动处理：version += 1，parent_version = 当前 version，updated_at = now。
        """
        current = asdict(self)
        current.pop("version")
        current.pop("parent_version")
        current.pop("updated_at")
        current.update(kwargs)
        current["version"] = self.version + 1
        current["parent_version"] = self.version
        current.setdefault("updated_at", time.time())
        return ModelParameters(**current)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["stage"] = self.stage.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ModelParameters":
        known = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)

    @classmethod
    def population_prior(cls) -> "ModelParameters":
        """返回纯群体先验参数（冷启动默认）。"""
        return cls()


def _derive_stage(n_events: int) -> LearningStage:
    """按事件数推导学习阶段。"""
    if n_events < _N_EVENTS_COLD:
        return LearningStage.COLD_START
    if n_events < _N_EVENTS_EARLY:
        return LearningStage.EARLY
    if n_events < _N_EVENTS_TRANSITION:
        return LearningStage.TRANSITION
    return LearningStage.PERSONAL
