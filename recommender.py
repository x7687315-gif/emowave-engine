"""
recommender.py — 心潮 EmoWave 情境感知应对策略推荐引擎 · 上下文多臂老虎机

本模块实现一个基于 LinUCB 的上下文老虎机推荐系统，
根据情绪情境特征自动推荐最可能有效的应对策略。

核心问题建模：
  - 臂（Arm）：每个可选的应对策略（如"深呼吸"、"短暂散步"、"听音乐"等）
  - 上下文（Context）：当前情绪状态和环境的特征向量
  - 奖励（Reward）：用户事后对策略效果的评分（1-5）
  - 目标：在每次需要推荐时，选择当前情境下预期效果最好的策略

算法选择：LinUCB（线性上置信界）
  - 对每个臂维护一个线性模型：expected_reward = context^T * theta
  - UCB 候选分数 = predicted + alpha * sqrt(context^T * A^{-1} * context)
  - alpha 控制探索-利用权衡

设计理由：
  - LinUCB 是广告推荐领域的标准方法，适合"上下文→离散选项"的在线学习
  - 相比 Thompson Sampling，LinUCB 不需要假设高斯后验，计算更轻量
  - 相比协同过滤，不需要其他用户数据，纯设备端个性化
  - 冷启动：使用全局平均评分作为先验，新策略通过 UCB 上界自动探索

替换指南：
  - 若需非线性策略效果建模，可替换 LinUCB 为 KernelUCB 或 Neural Bandit
  - 若需接入 LLM 生成新策略，实现 generate_new_strategy() 接口
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import json


# ================================================================
# 数据结构
# ================================================================

@dataclass
class Context:
    """
    情境特征向量。

    维度说明：
      current_valence: 当前效价（0=极度不适, 1=极度舒适）
      current_arousal: 当前唤醒度（0=极困倦, 1=极兴奋）
      time_of_day: 一天中的时段（0-23 的浮点数，如 14.5 = 14:30）
      weekday: 星期几（0=周一, 6=周日）
      last_sleep_score: 前夜睡眠评分（0-10）
      trigger_category_code: 诱因类别的整数编码
      day_of_week_sin: weekday 的 sin 编码（捕捉周期性）
      day_of_week_cos: weekday 的 cos 编码（捕捉周期性）
      hour_sin: time_of_day 的小时 sin 编码
      hour_cos: time_of_day 的小时 cos 编码
    """
    current_valence: float
    current_arousal: float
    time_of_day: float
    weekday: int
    last_sleep_score: float
    trigger_category_code: int

    @property
    def feature_vector(self) -> np.ndarray:
        """转换为 10 维特征向量（含周期性编码）。"""
        weekday_rad = self.weekday * 2.0 * np.pi / 7.0
        hour_rad = (self.time_of_day % 24) * 2.0 * np.pi / 24.0
        return np.array([
            self.current_valence,
            self.current_arousal,
            self.time_of_day / 24.0,        # 归一化到 [0,1]
            self.weekday / 6.0,              # 归一化到 [0,1]
            self.last_sleep_score / 10.0,    # 归一化到 [0,1]
            self.trigger_category_code / 10.0,  # 归一化到 [0,1]
            np.sin(weekday_rad),
            np.cos(weekday_rad),
            np.sin(hour_rad),
            np.cos(hour_rad),
        ])

    @classmethod
    def from_raw(cls, valence: float, arousal: float, hour: float,
                weekday: int, sleep: float, trigger_code: int) -> "Context":
        """便捷构造方法。"""
        return cls(
            current_valence=valence,
            current_arousal=arousal,
            time_of_day=hour,
            weekday=weekday,
            last_sleep_score=sleep,
            trigger_category_code=trigger_code,
        )


@dataclass
class Strategy:
    """一个应对策略的定义。"""
    id: str
    name: str
    category: str  # 如 "breathing", "physical", "cognitive", "social"


@dataclass
class Recommendation:
    """推荐结果。"""
    strategy_id: str
    strategy_name: str
    predicted_score: float    # 预期评分（1-5）
    uncertainty: float       # 不确定性（UCB 上界宽度）
    ucb_score: float         # UCB 综合得分（用于排序）


# ================================================================
# 默认策略库
# ================================================================

DEFAULT_STRATEGIES = [
    Strategy("deep_breathing", "深呼吸练习", "breathing"),
    Strategy("body_scan", "身体扫描放松", "breathing"),
    Strategy("short_walk", "短暂散步", "physical"),
    Strategy("stretching", "拉伸运动", "physical"),
    Strategy("listen_music", "听音乐", "cognitive"),
    Strategy("journaling", "情绪日记书写", "cognitive"),
    Strategy("cold_water", "冷水洗脸", "sensory"),
    Strategy("talk_friend", "联系朋友聊天", "social"),
    Strategy("progressive_relax", "渐进式肌肉放松", "breathing"),
    Strategy("grounding_543", "5-4-3-2-1 接地练习", "cognitive"),
]


# ================================================================
# 特征工程
# ================================================================

def extract_context(
    current_valence: float,
    current_arousal: float,
    time_of_day: float,
    weekday: int,
    last_sleep_score: float,
    trigger_category_code: int,
) -> Context:
    """
    从原始数据构造情境特征向量。

    Args:
        current_valence: 当前效价（0-1）
        current_arousal: 当前唤醒度（0-1）
        time_of_day: 小时（0-23 的浮点数）
        weekday: 星期几（0=周一, 6=周日）
        last_sleep_score: 前夜睡眠评分（0-10）
        trigger_category_code: 诱因类别编码（整数）

    Returns:
        Context 对象
    """
    return Context.from_raw(
        valence=current_valence,
        arousal=current_arousal,
        hour=time_of_day,
        weekday=weekday,
        sleep=last_sleep_score,
        trigger_code=trigger_category_code,
    )


# ================================================================
# 上下文老虎机 · LinUCB
# ================================================================

class LinUCBArm:
    """
    LinUCB 的单个臂（策略）模型。

    维护：
      - A: d×d 的共轭先验矩阵（初始化为单位矩阵 × alpha_init）
      - b: d×1 的奖励累积向量
      - theta: A^{-1} b（当前最优系数）
      - n: 已收集的样本数
    """

    def __init__(self, d: int, alpha: float = 1.0):
        """
        Args:
            d: 特征维度
            alpha: 探索系数（越大越倾向探索）
        """
        self.d = d
        self.alpha = alpha
        self.A = np.eye(d) * alpha
        self.b = np.zeros(d)
        self.n = 0

    def get_ucb(self, context: np.ndarray) -> Tuple[float, float, float]:
        """
        计算 UCB 候选分数。

        Returns:
            (predicted_score, uncertainty, ucb_score)
        """
        try:
            A_inv = np.linalg.inv(self.A)
        except np.linalg.LinAlgError:
            A_inv = np.eye(self.d) / self.alpha

        theta = A_inv @ self.b
        predicted = float(context @ theta)

        # 不确定性：sqrt(context^T * A^{-1} * context)
        uncertainty = float(np.sqrt(max(0, context @ A_inv @ context)))

        ucb = predicted + self.alpha * uncertainty

        return predicted, uncertainty, ucb

    def update(self, context: np.ndarray, reward: float) -> None:
        """
        收集一个样本，更新模型参数。

        LinUCB 更新规则：
          A += context * context^T
          b += reward * context

        Args:
            context: 特征向量
            reward: 奖励值（用户评分 1-5）
        """
        self.A += np.outer(context, context)
        self.b += reward * context
        self.n += 1

    @property
    def theta(self) -> np.ndarray:
        """当前最优系数。"""
        try:
            return np.linalg.inv(self.A) @ self.b
        except np.linalg.LinAlgError:
            return np.zeros(self.d)


class ContextualBandit:
    """
    上下文多臂老虎机推荐引擎。

    使用方式：
      bandit = ContextualBandit(strategies=DEFAULT_STRATEGIES)
      rec = bandit.recommend(context)
      # ... 用户使用策略 ...
      bandit.update(rec.strategy_id, context, user_rating)

    特性：
      - 自动冷启动：新策略通过高 UCB 上界自动获得探索机会
      - 上下文感知：不同情境下同一策略有不同预期效果
      - 可扩展：支持动态添加新策略
      - 可序列化：状态可导出为 JSON 用于本地持久化
    """

    def __init__(
        self,
        strategies: Optional[List[Strategy]] = None,
        feature_dim: int = 10,
        alpha: float = 1.0,
        default_prior_mean: float = 3.0,
    ):
        """
        Args:
            strategies: 可选策略列表（默认使用 DEFAULT_STRATEGIES）
            feature_dim: 特征维度（必须与 Context.feature_vector 一致）
            alpha: 探索系数
            default_prior_mean: 新策略的先验平均评分
        """
        self.strategies = strategies or DEFAULT_STRATEGIES
        self.feature_dim = feature_dim
        self.alpha = alpha
        self.default_prior_mean = default_prior_mean

        # 每个策略一个 LinUCB 臂
        self.arms: Dict[str, LinUCBArm] = {}
        for s in self.strategies:
            self.arms[s.id] = LinUCBArm(d=feature_dim, alpha=alpha)

        # 全局奖励统计（用于冷启动先验）
        self.global_reward_sum = 0.0
        self.global_reward_count = 0
        self._total_recommendations = 0

    @property
    def strategy_map(self) -> Dict[str, Strategy]:
        """策略 ID → Strategy 的映射。"""
        return {s.id: s for s in self.strategies}

    def add_strategy(self, strategy: Strategy) -> None:
        """动态添加新策略（冷启动，自动获得探索机会）。"""
        if strategy.id not in self.arms:
            self.arms[strategy.id] = LinUCBArm(
                d=self.feature_dim, alpha=self.alpha
            )
            self.strategies.append(strategy)

    # ============================================================
    # 核心 API
    # ============================================================

    def recommend(self, context: Context) -> Recommendation:
        """
        根据当前情境推荐最优策略。

        流程：
          1. 对每个臂计算 UCB 分数
          2. 选择 UCB 最高的策略
          3. 返回推荐结果

        Args:
            context: 当前情境特征

        Returns:
            Recommendation 推荐结果
        """
        x = context.feature_vector

        best_id = None
        best_ucb = -float('inf')
        results = {}

        for s in self.strategies:
            arm = self.arms[s.id]
            predicted, uncertainty, ucb = arm.get_ucb(x)

            # 冷启动修正：如果样本很少，混合全局先验
            if arm.n < 3 and self.global_reward_count > 0:
                global_mean = self.global_reward_sum / self.global_reward_count
                prior_weight = 0.7  # 先验权重
                data_weight = 1.0 - prior_weight
                predicted = prior_weight * global_mean + data_weight * predicted

            results[s.id] = (predicted, uncertainty, ucb)

            if ucb > best_ucb:
                best_ucb = ucb
                best_id = s.id

        if best_id is None:
            best_id = self.strategies[0].id

        strategy = self.strategy_map[best_id]
        predicted, uncertainty, ucb = results[best_id]

        self._total_recommendations += 1

        return Recommendation(
            strategy_id=best_id,
            strategy_name=strategy.name,
            predicted_score=round(predicted, 2),
            uncertainty=round(uncertainty, 3),
            ucb_score=round(ucb, 3),
        )

    def update(self, strategy_id: str, context: Context, reward: float) -> None:
        """
        记录用户反馈，更新对应策略的模型。

        Args:
            strategy_id: 使用的策略 ID
            context: 推荐时的情境特征
            reward: 用户评分（1-5）
        """
        if strategy_id not in self.arms:
            return

        x = context.feature_vector
        self.arms[strategy_id].update(x, reward)

        # 更新全局统计
        self.global_reward_sum += reward
        self.global_reward_count += 1

    # ============================================================
    # 新策略生成接口（预留）
    # ============================================================

    def generate_new_strategy(
        self,
        user_history_summary: str,
        failed_strategies: List[str],
    ) -> Optional[Strategy]:
        """
        预留接口：基于用户历史和失败策略生成新应对策略。

        未来可接入：
          - 本地小型语言模型（如 phi-3-mini）
          - 规则引擎（从策略库中组合出新策略）
          - 云端 LLM API（需用户授权）

        Args:
            user_history_summary: 用户情绪历史的文本摘要
            failed_strategies: 近期效果不佳的策略 ID 列表

        Returns:
            新策略对象（如果成功生成），否则 None
        """
        # TODO: 实现策略生成逻辑
        # 示例：当"深呼吸"连续失败 3 次后，建议"渐进式肌肉放松"
        return None

    # ============================================================
    # 诊断与持久化
    # ============================================================

    def get_strategy_stats(self) -> Dict[str, Dict]:
        """
        获取每个策略的统计信息（用于 UI 展示和调试）。

        Returns:
            {strategy_id: {name, n, avg_reward, theta_norm, ...}}
        """
        stats = {}
        for s in self.strategies:
            arm = self.arms[s.id]
            theta = arm.theta
            theta_norm = float(np.linalg.norm(theta))
            avg_reward = 0.0
            if self.global_reward_count > 0 and arm.n < 3:
                avg_reward = self.global_reward_sum / self.global_reward_count
            elif arm.n > 0:
                # 用当前 theta 在零上下文的预测作为粗略的"平均效果"
                zero_ctx = np.zeros(self.feature_dim)
                avg_reward = float(zero_ctx @ theta)

            stats[s.id] = {
                "name": s.name,
                "category": s.category,
                "n_samples": arm.n,
                "avg_reward": round(avg_reward, 2),
                "theta_norm": round(theta_norm, 3),
            }
        return stats

    def serialize_state(self) -> str:
        """序列化引擎状态为 JSON 字符串（用于本地持久化）。"""
        state = {
            "alpha": self.alpha,
            "default_prior_mean": self.default_prior_mean,
            "global_reward_sum": self.global_reward_sum,
            "global_reward_count": self.global_reward_count,
            "total_recommendations": self._total_recommendations,
            "arms": {},
        }
        for sid, arm in self.arms.items():
            state["arms"][sid] = {
                "A": arm.A.tolist(),
                "b": arm.b.tolist(),
                "n": arm.n,
            }
        return json.dumps(state, ensure_ascii=False)

    @classmethod
    def load_state(cls, state_json: str, strategies: Optional[List[Strategy]] = None) -> "ContextualBandit":
        """从 JSON 恢复引擎状态。"""
        state = json.loads(state_json)
        bandit = cls(
            strategies=strategies,
            alpha=state.get("alpha", 1.0),
            default_prior_mean=state.get("default_prior_mean", 3.0),
        )
        bandit.global_reward_sum = state.get("global_reward_sum", 0.0)
        bandit.global_reward_count = state.get("global_reward_count", 0)
        bandit._total_recommendations = state.get("total_recommendations", 0)

        for sid, arm_data in state.get("arms", {}).items():
            if sid in bandit.arms:
                bandit.arms[sid].A = np.array(arm_data["A"])
                bandit.arms[sid].b = np.array(arm_data["b"])
                bandit.arms[sid].n = arm_data.get("n", 0)

        return bandit
