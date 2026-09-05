"""calibrator — 个人校准编排器（L3 学习层的统一入口）。

把 CorrectionDataset（偏差统计）、OnlineResidualRegression（残差修正）、
PersonalModelLearner（超参数学习）编排为一个完整的校准闭环：

    Observation + UserCorrection + Baseline + Historical Model Error
            ↓
    Personal Model Update（REFACTOR_PLAN.md §21.3 长期学习）

两类学习并存（互补，不互斥）：
  1. **残差回归**（OnlineResidualRegression）：学习"模型输出 → 用户偏好"的
     系统性映射，直接补偿预测值。快、直接、可立即降低误差。
     对应 REFACTOR_PLAN.md §9.3 Online Ridge Regression。
  2. **超参数学习**（PersonalModelLearner）：学习 ℓ/σ/σ_noise 等动力学参数，
     改变模型本身的行为（惯性、幅度、噪声）。慢、深层、形成"个人动态模型"。
     对应 part1 §3.3 边际似然 + 层次先验。

为什么需要两者：残差回归补偿"静态偏移"（模型总是低估 0.1），超参数学习
补偿"动态结构"（模型的情绪惯性比用户实际的短）。前者立竿见影，后者长期塑形。

使用方式：
    cal = Calibrator()
    cal.ingest_correction(user_correction)   # 累积纠正
    cal.ingest_curve_edit(curve_edit)
    new_params = cal.learn(current_params)    # 事件结束时学习
    calibrated_pred = cal.calibrate(raw_pred) # 预测时施加残差修正
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from emowave.core.calibration.corrections import (
    BiasStatistics,
    CorrectionDataset,
    OnlineResidualRegression,
)
from emowave.core.calibration.personal_model import (
    LearnerConfig,
    PersonalModelLearner,
    kalman_log_likelihood,
)
from emowave.core.domain.correction import UserCorrection
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation


class Calibrator:
    """个人校准编排器。

    Attributes:
        dataset: 累积的纠正数据集（append-only）。
        residual_model: 在线残差回归（补偿静态偏移）。
        learner: 超参数学习器（塑形动态结构）。
    """

    def __init__(
        self,
        learner_config: Optional[LearnerConfig] = None,
        reg_lambda: float = 1.0,
        max_correction: float = 0.3,
    ) -> None:
        self.dataset = CorrectionDataset()
        self.residual_model = OnlineResidualRegression(
            reg_lambda=reg_lambda, max_correction=max_correction
        )
        self.learner = PersonalModelLearner(config=learner_config)
        self._population = ModelParameters.population_prior()

    # ============================================================
    # 摄入纠正
    # ============================================================

    def ingest_correction(self, c: UserCorrection) -> None:
        """摄入一条 UserCorrection（Phase 1 域对象）。

        同时喂给 dataset（超参数学习）与 residual_model（残差回归）。
        """
        self.dataset.add_user_correction(c)
        self.residual_model.update(
            predicted=c.predicted_value,
            corrected=c.corrected_value,
            weight=c.reliability_weight,
        )

    def ingest_curve_edit(self, edit: Any) -> None:
        """摄入一条 CurveEdit（Phase 3）。"""
        self.dataset.add_curve_edit(edit)
        self.residual_model.update(
            predicted=edit.value_before,
            corrected=edit.value_after,
            weight=edit.reliability_weight,
        )

    def ingest_raw(
        self,
        timestamp: float,
        predicted: float,
        corrected: float,
        weight: float = 1.0,
        salience: float = 0.5,
        dimension: str = "valence",
    ) -> None:
        """摄入一条原始纠正记录（便于测试与外部导入）。"""
        self.dataset.add_raw(
            timestamp=timestamp,
            predicted=predicted,
            corrected=corrected,
            weight=weight,
            salience=salience,
            dimension=dimension,
        )
        self.residual_model.update(
            predicted=predicted, corrected=corrected, weight=weight
        )

    # ============================================================
    # 学习
    # ============================================================

    def learn(
        self, params: Optional[ModelParameters] = None
    ) -> ModelParameters:
        """执行一步个人化超参数学习（通常在事件结束时调用）。

        Args:
            params: 当前个人参数（默认群体先验冷启动）

        Returns:
            更新后的 ModelParameters
        """
        current = params or self._population
        return self.learner.update(current, self.dataset, population=self._population)

    def bias_statistics(self) -> BiasStatistics:
        """当前纠正数据集的系统性偏差统计（诊断用，part1 §4.4）。"""
        return self.dataset.bias_statistics()

    # ============================================================
    # 预测时校准
    # ============================================================

    def calibrate(self, raw_prediction: float) -> float:
        """对模型原始预测施加学习到的残差修正。

        这是"修正越多误差下降"的直接体现：系统性残差被 residual_model
        捕捉，未来预测自动补偿（REFACTOR_PLAN.md §28 Phase 4 完成标准）。
        """
        return self.residual_model.apply(raw_prediction)

    def residual_at(self, predicted: float) -> float:
        """给定模型输出处的学习残差（诊断用）。"""
        return self.residual_model.predict_residual(predicted)

    # ============================================================
    # 边际似然评估（part1 §3.3）
    # ============================================================

    def evaluate_log_likelihood(
        self,
        observations: List[Observation],
        params: ModelParameters,
    ) -> float:
        """评估给定参数下观察序列的边际对数似然（超参数拟合优度）。

        可用于：
          - 对照学习前后参数的拟合优度（学习应提升似然）
          - A/B 不同候选 θ
        """
        return kalman_log_likelihood(observations, params)

    # ============================================================
    # 序列化
    # ============================================================

    def to_dict(self) -> Dict[str, Any]:
        return {
            "records": self.dataset.records,
            "residual_model": self.residual_model.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any], **kw: Any) -> "Calibrator":
        cal = cls(**kw)
        for r in d.get("records", []):
            cal.dataset.add_raw(
                timestamp=r["timestamp"],
                predicted=r["predicted"],
                corrected=r["corrected"],
                weight=r.get("weight", 1.0),
                salience=r.get("salience", 0.5),
                dimension=r.get("dimension", "valence"),
            )
        rm = d.get("residual_model")
        if rm:
            cal.residual_model = OnlineResidualRegression.from_dict(rm)
        return cal
