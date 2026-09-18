"""archetype — 三类"精力人群"的先验（默认曲线函数）。

产品设定：把人群按精力分为 高 / 中 / 低 三类，每类有一条**默认的情绪曲线函数**。
在 EmoWave 的内核里，"曲线函数"就是 Matérn ν=3/2 高斯过程，其形状由
`(ℓ 惯性, σ 幅度)` 决定、中心由 `Baseline(v, a)` 决定。所以"一类人群一条默认
曲线" = 给这三类各配一组 `ModelParameters + Baseline`。

精力 ≈ 唤醒（arousal，Russell 环状模型的能量轴）：
  · 高精力：基线唤醒更高、波动幅度 σ 更大、惯性 ℓ 更短（情绪来得快走得也快）
  · 中精力：≈ 群体先验（默认，与 ModelParameters 冷启动一致）
  · 低精力：基线唤醒更低、σ 更小、ℓ 更长（更平、更缓）

用户开局选一类 → 用它 seed 起点；之后随记录与纠正，向"个人"收缩（个性化）。

设计约束：零依赖（只用标准库 + 域对象），frozen dataclass。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from emowave.core.domain.baseline import Baseline, BaselineSource
from emowave.core.domain.model_parameters import ModelParameters


@dataclass(frozen=True)
class EnergyArchetype:
    """一个精力人群先验：一条默认曲线（GP 超参）+ 一个默认中心（基线）。

    Attributes:
        key: 机器可读标识（'high' / 'medium' / 'low'）。
        label: 中文显示名。
        desc: 一句话描述（UI 提示用）。
        ell_valence / ell_arousal: 效价 / 唤醒的时间相关尺度（秒，情绪惯性）。
        sigma_valence / sigma_arousal: 效价 / 唤醒的波动幅度。
        sigma_noise: 观测噪声标准差。
        baseline_valence / baseline_arousal: 默认基线中心 [0, 1]。
    """

    key: str
    label: str
    desc: str
    ell_valence: float
    ell_arousal: float
    sigma_valence: float
    sigma_arousal: float
    sigma_noise: float
    baseline_valence: float
    baseline_arousal: float

    def to_params(self) -> ModelParameters:
        """该人群的默认曲线超参（ModelParameters）。"""
        return ModelParameters(
            ell_valence=self.ell_valence,
            ell_arousal=self.ell_arousal,
            sigma_valence=self.sigma_valence,
            sigma_arousal=self.sigma_arousal,
            sigma_noise=self.sigma_noise,
        )

    def to_baseline(self) -> Baseline:
        """该人群的默认基线中心（Baseline，来源标记为群体先验）。"""
        return Baseline(
            valence=self.baseline_valence,
            arousal=self.baseline_arousal,
            source=BaselineSource.POPULATION,
            confidence=0.3,
        )


# ---- 三类精力人群（唤醒为主轴的映射）----
# 中精力刻意等于 ModelParameters 的群体先验默认值，保证"不选=与旧行为一致"。
HIGH_ENERGY = EnergyArchetype(
    key="high", label="高精力", desc="基线唤醒高、波动大、变化快",
    ell_valence=240.0, ell_arousal=180.0,
    sigma_valence=0.20, sigma_arousal=0.28, sigma_noise=0.10,
    baseline_valence=0.58, baseline_arousal=0.60,
)
MEDIUM_ENERGY = EnergyArchetype(
    key="medium", label="中精力", desc="接近群体平均（默认）",
    ell_valence=300.0, ell_arousal=240.0,
    sigma_valence=0.15, sigma_arousal=0.20, sigma_noise=0.10,
    baseline_valence=0.55, baseline_arousal=0.42,
)
LOW_ENERGY = EnergyArchetype(
    key="low", label="低精力", desc="基线唤醒低、波动小、变化慢",
    ell_valence=380.0, ell_arousal=320.0,
    sigma_valence=0.11, sigma_arousal=0.14, sigma_noise=0.10,
    baseline_valence=0.50, baseline_arousal=0.25,
)

ARCHETYPES: Dict[str, EnergyArchetype] = {
    a.key: a for a in (HIGH_ENERGY, MEDIUM_ENERGY, LOW_ENERGY)
}
# UI 展示顺序（高→中→低）
ARCHETYPE_ORDER: List[EnergyArchetype] = [HIGH_ENERGY, MEDIUM_ENERGY, LOW_ENERGY]
DEFAULT_ARCHETYPE_KEY = "medium"


def get_archetype(key: str) -> EnergyArchetype:
    """按 key 取人群先验；未知 key 回退到默认（中精力），保证 UI 永不崩。"""
    return ARCHETYPES.get(key, ARCHETYPES[DEFAULT_ARCHETYPE_KEY])
