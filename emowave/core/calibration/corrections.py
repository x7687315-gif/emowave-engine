"""corrections — Correction Dataset + 系统性偏差统计 + Online Ridge Regression。

REFACTOR_PLAN.md §9.2 Personal Calibration：用户不断纠正，这些数据组成
Personal Calibration Dataset，模型开始学习"哪些输入经常高估/低估、哪些
信号对这个用户特别重要、哪些只是噪声"。

ARCHITECTURE part1 §4.4 系统性拖动 = 超参数错了，不是数据错了：

    | 系统性拖动模式      | 诊断                  | 修正        |
    | 总把峰值拉高/拉低   | σ（幅度）被低估/高估  | 调整 σ      |
    | 总把曲线拉得更陡    | ℓ 太大（过度平滑）    | 减小 ℓ      |
    | 总把曲线拉得更平    | ℓ 太小（过度跟随噪声）| 增大 ℓ      |
    | 拖动幅度随机、无模式| σ_noise 偏小          | 增大 σ_noise|

本模块提供从 correction 流中提取这些信号的统计量，以及一个在线岭回归
（REFACTOR_PLAN.md §9.3 推荐算法路线：Online Ridge Regression）直接学习
残差修正，使"修正越多，预测误差下降"（§28 Phase 4 完成标准）。

设计约束：
  - 零依赖：只用 math + dataclass + core.linalg
  - 加权：所有统计量按 reliability_weight 加权（part1 §4.2 峰终加权）
  - Coactive 语义（part1 §4.3）：correction 是"改进"不是"真值"，
    残差回归学习的是系统性可预测部分，单次大幅拖动被有界化
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from emowave.core import linalg
from emowave.core.domain.correction import UserCorrection


@dataclass
class BiasStatistics:
    """从 correction dataset 提取的系统性偏差统计量（part1 §4.4 诊断信号）。

    Attributes:
        n_corrections: 纠正总数。
        effective_sample_size: 加权和 Σw（有效样本量，可靠性加权）。
        weighted_mean_delta: 加权平均 delta（corrected-predicted）。
            >0 = 模型系统性低估（用户拉高），<0 = 系统性高估（用户拉低）。
        peak_weighted_delta: 高显著性（峰值）纠正的加权平均 delta。
            → σ（幅度）偏差信号：>0 表示用户把峰值拉得更高，σ 被低估。
        delta_scatter: delta 的加权标准差（去除系统分量后的离散度）。
            → σ_noise 信号：scatter 大且 mean≈0 表示拖动随机无模式。
        delta_autocorr: 相邻纠正 delta 的加权自相关（时间持续性）。
            → ℓ 信号：高正值=纠正方向持续（模型缺少慢动态，ℓ 应增大）；
            负值=纠正交替（模型过度平滑，ℓ 应减小）。
        direction_consistency: |weighted_mean_delta| / weighted_mean_|delta|。
            ∈[0,1]，1=所有纠正同向（强系统性），0=正负抵消（随机）。
    """

    n_corrections: int = 0
    effective_sample_size: float = 0.0
    weighted_mean_delta: float = 0.0
    peak_weighted_delta: float = 0.0
    delta_scatter: float = 0.0
    delta_autocorr: float = 0.0
    direction_consistency: float = 0.0


class CorrectionDataset:
    """Personal Calibration Dataset：append-only 的纠正集合 + 偏差统计。

    纠正来源可以是 UserCorrection（Phase 1 域对象）或 CurveEdit（Phase 3）。
    每条纠正携带 delta=corrected-predicted 与 reliability_weight。

    使用方式：
        ds = CorrectionDataset()
        ds.add_user_correction(correction)
        stats = ds.bias_statistics()
        # stats.peak_weighted_delta → σ 调整信号
        # stats.delta_scatter → σ_noise 调整信号
        # stats.delta_autocorr → ℓ 调整信号
    """

    # 高显著性阈值：salience ≥ 此值视为"峰值纠正"
    PEAK_SALIENCE_THRESHOLD = 0.6

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> List[Dict[str, Any]]:
        """纠正记录（拷贝）。每条是 dict：timestamp/dimension/predicted/
        corrected/delta/weight/salience。"""
        return [dict(r) for r in self._records]

    def add_user_correction(self, c: UserCorrection) -> None:
        """加入一条 UserCorrection（Phase 1 域对象）。"""
        self._records.append(
            {
                "timestamp": c.timestamp,
                "dimension": c.dimension.value,
                "predicted": c.predicted_value,
                "corrected": c.corrected_value,
                "delta": c.delta,
                "weight": c.reliability_weight,
                "salience": c.salience,
            }
        )

    def add_curve_edit(self, edit: Any) -> None:
        """加入一条 CurveEdit（Phase 3）。鸭子类型：读取其字段。"""
        self._records.append(
            {
                "timestamp": edit.timestamp,
                "dimension": getattr(edit.channel, "value", str(edit.channel)),
                "predicted": edit.value_before,
                "corrected": edit.value_after,
                "delta": edit.value_after - edit.value_before,
                "weight": edit.reliability_weight,
                "salience": edit.salience,
            }
        )

    def add_raw(
        self,
        timestamp: float,
        predicted: float,
        corrected: float,
        weight: float = 1.0,
        salience: float = 0.5,
        dimension: str = "valence",
    ) -> None:
        """加入一条原始纠正记录（便于测试与外部导入）。"""
        self._records.append(
            {
                "timestamp": timestamp,
                "dimension": dimension,
                "predicted": predicted,
                "corrected": corrected,
                "delta": corrected - predicted,
                "weight": max(0.0, min(1.0, weight)),
                "salience": max(0.0, min(1.0, salience)),
            }
        )

    def effective_sample_size(self) -> float:
        """加权样本量 Σw。决定层次收缩强度（part1 §3.3）。"""
        return sum(r["weight"] for r in self._records)

    def bias_statistics(self) -> BiasStatistics:
        """计算 part1 §4.4 的系统性偏差诊断信号。"""
        stats = BiasStatistics()
        n = len(self._records)
        stats.n_corrections = n
        if n == 0:
            return stats

        weights = [r["weight"] for r in self._records]
        deltas = [r["delta"] for r in self._records]
        w_sum = sum(weights)
        stats.effective_sample_size = w_sum
        if w_sum <= 1e-12:
            return stats

        # 加权平均 delta（系统性偏差）
        stats.weighted_mean_delta = sum(w * d for w, d in zip(weights, deltas)) / w_sum

        # 加权平均 |delta|（用于方向一致性）
        w_abs_mean = sum(w * abs(d) for w, d in zip(weights, deltas)) / w_sum
        stats.direction_consistency = (
            abs(stats.weighted_mean_delta) / w_abs_mean if w_abs_mean > 1e-12 else 0.0
        )

        # 峰值（高 salience）纠正的加权 delta → σ 信号
        peak_w = [
            (w, d)
            for w, d, r in zip(weights, deltas, self._records)
            if r["salience"] >= self.PEAK_SALIENCE_THRESHOLD
        ]
        if peak_w:
            pw_sum = sum(w for w, _ in peak_w)
            if pw_sum > 1e-12:
                stats.peak_weighted_delta = sum(w * d for w, d in peak_w) / pw_sum

        # delta 加权标准差（去系统分量）→ σ_noise 信号
        var = sum(
            w * (d - stats.weighted_mean_delta) ** 2 for w, d in zip(weights, deltas)
        ) / w_sum
        stats.delta_scatter = math.sqrt(max(0.0, var))

        # 相邻 delta 加权自相关（按时间排序）→ ℓ 信号
        stats.delta_autocorr = self._weighted_autocorr_lag1()

        return stats

    def _weighted_autocorr_lag1(self) -> float:
        """lag-1 加权自相关：corr(delta_t, delta_{t+1})。

        高正值 = 纠正方向在时间上持续（用户连续多步同向拉）→ 模型缺少
        慢动态，ℓ 应增大。负值 = 纠正交替（用户来回拉）→ 模型过度平滑
        或过度跟随，需具体分析（part1 §4.4）。
        """
        if len(self._records) < 3:
            return 0.0
        ordered = sorted(self._records, key=lambda r: r["timestamp"])
        d = [r["delta"] for r in ordered]
        w = [r["weight"] for r in ordered]
        w_sum = sum(w)
        if w_sum <= 1e-12:
            return 0.0
        mean = sum(wi * di for wi, di in zip(w, d)) / w_sum
        # 分子：Σ w_t·w_{t+1}·(d_t-mean)(d_{t+1}-mean)
        num = 0.0
        wnum = 0.0
        for i in range(len(d) - 1):
            wpair = min(w[i], w[i + 1])  # 成对权重取较小者（保守）
            num += wpair * (d[i] - mean) * (d[i + 1] - mean)
            wnum += wpair
        # 分母：Σ w·(d-mean)²
        den = sum(wi * (di - mean) ** 2 for wi, di in zip(w, d))
        if abs(den) < 1e-12 or wnum <= 1e-12:
            return 0.0
        # 归一化（wnum/den 量纲对齐）
        corr = num / den
        return max(-1.0, min(1.0, corr))

    def clear(self) -> None:
        self._records.clear()


# ============================================================
# Online Ridge Regression（REFACTOR_PLAN.md §9.3）
# ============================================================


class OnlineResidualRegression:
    """在线岭回归：从 (predicted → residual) 学习系统性残差修正。

    模型：residual ≈ θᵀ·φ(predicted)，φ(x) = [1, x]（偏置 + 斜率）。
    在线更新（REFACTOR_PLAN.md §9.3 Online Ridge Regression）：
        A ← A + w·φφᵀ，  b ← b + w·residual·φ
        θ = (A + λI)⁻¹·b

    预测时对模型输出施加修正：
        pred_corrected = pred + θᵀ·φ(pred)

    Coactive 有界化（part1 §4.3）：单次大幅拖动只产生有界影响——
      - 每条样本按 reliability_weight 加权（随意拖动权重低）
      - 岭正则 λ 防止单次极端样本主导 θ
      - 修正量裁剪到 ±max_correction（有界）

    这直接实现"修正越多，误差下降"（§28 Phase 4 完成标准）：
    系统性残差被 θ 捕捉，未来预测自动补偿。
    """

    def __init__(
        self,
        reg_lambda: float = 1.0,
        max_correction: float = 0.3,
        n_features: int = 2,
    ) -> None:
        if reg_lambda < 0:
            raise ValueError(f"reg_lambda 不能为负，得到 {reg_lambda}")
        self.reg_lambda = reg_lambda
        self.max_correction = max_correction
        self.n_features = n_features
        # A = Σ w φφᵀ （不含 λI，求解时再加），b = Σ w r φ
        self._A: List[List[float]] = linalg.zeros(n_features, n_features)
        self._b: List[float] = [0.0] * n_features
        self._n_updates = 0
        self._theta: Optional[List[float]] = None

    @property
    def n_updates(self) -> int:
        return self._n_updates

    @property
    def theta(self) -> List[float]:
        """当前回归系数（懒求解缓存）。"""
        if self._theta is None:
            self._theta = self._solve()
        return self._theta

    def _features(self, predicted: float) -> List[float]:
        """φ(x) = [1, x]。"""
        if self.n_features == 2:
            return [1.0, float(predicted)]
        # 扩展：[1, x, x², ...]（预留，默认 2 维）
        feats = [1.0]
        for k in range(1, self.n_features):
            feats.append(float(predicted) ** k)
        return feats

    def update(self, predicted: float, corrected: float, weight: float = 1.0) -> None:
        """加入一条样本并增量更新 A, b。

        Args:
            predicted: 模型原预测值
            corrected: 用户纠正值
            weight: 可靠性权重 [0,1]（Coactive：随意拖动权重低，影响有界）
        """
        w = max(0.0, min(1.0, float(weight)))
        if w <= 0.0:
            return  # 零权重样本不贡献（Coactive 有界化的极端情形）
        residual = corrected - predicted
        phi = self._features(predicted)
        # A += w·φφᵀ
        for i in range(self.n_features):
            for j in range(self.n_features):
                self._A[i][j] += w * phi[i] * phi[j]
        # b += w·residual·φ
        for i in range(self.n_features):
            self._b[i] += w * residual * phi[i]
        self._n_updates += 1
        self._theta = None  # 失效缓存

    def predict_residual(self, predicted: float) -> float:
        """预测给定模型输出处的系统性残差修正（有界裁剪）。"""
        if self._n_updates == 0:
            return 0.0
        theta = self.theta
        phi = self._features(predicted)
        r = linalg.dot(theta, phi)
        # Coactive 有界化：修正量裁剪到 ±max_correction
        return max(-self.max_correction, min(self.max_correction, r))

    def apply(self, predicted: float) -> float:
        """对模型输出施加学习到的修正，返回校准后的预测。"""
        corrected = predicted + self.predict_residual(predicted)
        return max(0.0, min(1.0, corrected))

    def _solve(self) -> List[float]:
        """θ = (A + λI)⁻¹·b。"""
        if self._n_updates == 0:
            return [0.0] * self.n_features
        reg = linalg.scale(linalg.identity(self.n_features), self.reg_lambda)
        a_reg = linalg.add(self._A, reg)
        try:
            a_inv = linalg.inv(a_reg)
        except ValueError:
            return [0.0] * self.n_features
        return linalg.mul_vec(a_inv, self._b)

    def held_out_error(self, samples: Sequence[Dict[str, float]]) -> float:
        """在一组样本上的加权均方残差误差（验证"修正越多误差下降"）。

        Args:
            samples: [{"predicted":..,"corrected":..,"weight":..}, ...]

        Returns:
            加权 RMSE：sqrt(Σw·(corrected - apply(predicted))² / Σw)
        """
        if not samples:
            return 0.0
        num = 0.0
        w_sum = 0.0
        for s in samples:
            w = float(s.get("weight", 1.0))
            pred = float(s["predicted"])
            corr = float(s["corrected"])
            residual_after = corr - self.apply(pred)
            num += w * residual_after ** 2
            w_sum += w
        if w_sum <= 1e-12:
            return 0.0
        return math.sqrt(num / w_sum)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reg_lambda": self.reg_lambda,
            "max_correction": self.max_correction,
            "n_features": self.n_features,
            "A": [row[:] for row in self._A],
            "b": list(self._b),
            "n_updates": self._n_updates,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OnlineResidualRegression":
        obj = cls(
            reg_lambda=d.get("reg_lambda", 1.0),
            max_correction=d.get("max_correction", 0.3),
            n_features=d.get("n_features", 2),
        )
        obj._A = [row[:] for row in d.get("A", linalg.zeros(obj.n_features, obj.n_features))]
        obj._b = list(d.get("b", [0.0] * obj.n_features))
        obj._n_updates = d.get("n_updates", 0)
        return obj
