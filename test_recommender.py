"""
test_recommender.py — 心潮 EmoWave · LinUCB 上下文老虎机推荐引擎 · 仿真测试脚本

本脚本通过随机生成的情境和仿真奖励，验证 LinUCB 推荐引擎的收敛性和策略学习效果。

仿真流程：
  1. 构造"真实环境"（GroundTruth）：根据情境特征定义每种策略的真实效果
  2. 运行 500 轮推荐-反馈循环
  3. 分块统计收敛指标（平均奖励、累积遗憾、推荐分布）
  4. 分析策略质量：学习排名 vs 真实排名
  5. 边缘用例测试：序列化、冷启动探索、动态添加策略

运行方式：
  cd /workspace/emowave-engine && python3 test_recommender.py
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import random
import copy
import numpy as np
from collections import Counter

from recommender import (
    Context,
    Strategy,
    Recommendation,
    ContextualBandit,
    DEFAULT_STRATEGIES,
    extract_context,
)

# ================================================================
# 终端配色（与 demo.py 风格一致）
# ================================================================
C_RESET = "\033[0m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_CYAN = "\033[36m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"


def _g(s):
    """绿色加粗文本。"""
    return f"{C_BOLD}{C_GREEN}{s}{C_RESET}"

def _y(s):
    """黄色文本。"""
    return f"{C_YELLOW}{s}{C_RESET}"

def _r(s):
    """红色文本。"""
    return f"{C_RED}{s}{C_RESET}"

def _c(s):
    """青色文本。"""
    return f"{C_CYAN}{s}{C_RESET}"

def _b(s):
    """加粗文本。"""
    return f"{C_BOLD}{s}{C_RESET}"

def _d(s):
    """暗色文本。"""
    return f"{C_DIM}{s}{C_RESET}"


# ================================================================
# 真实环境：GroundTruth 类
# ================================================================

class GroundTruth:
    """
    仿真真实环境：给定 (情境特征, 策略ID)，返回真实效果评分。

    设计原则（模拟真实心理干预效果）：
      - 深呼吸类（deep_breathing, body_scan, progressive_relax）
        → 适用于低唤醒、低效价状态（焦虑但较平静）
      - 体育活动类（short_walk, stretching）
        → 适用于高唤醒状态（精力旺盛或烦躁不安）
      - 认知策略类（listen_music, journaling, grounding_543）
        → 适用于中等唤醒状态
      - 社交策略（talk_friend）
        → 适用于工作日白天（更容易联系到朋友）
      - 感官策略（cold_water）
        → 适用于极高唤醒的紧急情况（如恐慌发作）
    """

    # 策略 ID → (基准评分, 适用唤醒区间, 适用效价区间)
    # 基准评分表示在"最佳匹配"情境下的期望得分
    STRATEGY_PROFILES = {
        "deep_breathing":     {"base": 4.2, "cat": "breathing",  "arousal_lo": 0.05, "arousal_hi": 0.40},
        "body_scan":          {"base": 3.8, "cat": "breathing",  "arousal_lo": 0.05, "arousal_hi": 0.35},
        "progressive_relax":  {"base": 4.0, "cat": "breathing",  "arousal_lo": 0.05, "arousal_hi": 0.38},
        "short_walk":         {"base": 4.3, "cat": "physical",   "arousal_lo": 0.55, "arousal_hi": 0.90},
        "stretching":         {"base": 3.9, "cat": "physical",   "arousal_lo": 0.50, "arousal_hi": 0.85},
        "listen_music":       {"base": 4.1, "cat": "cognitive",  "arousal_lo": 0.30, "arousal_hi": 0.65},
        "journaling":         {"base": 3.7, "cat": "cognitive",  "arousal_lo": 0.25, "arousal_hi": 0.60},
        "grounding_543":      {"base": 4.0, "cat": "cognitive",  "arousal_lo": 0.35, "arousal_hi": 0.70},
        "talk_friend":        {"base": 4.0, "cat": "social",     "arousal_lo": 0.20, "arousal_hi": 0.75},
        "cold_water":         {"base": 4.5, "cat": "sensory",    "arousal_lo": 0.80, "arousal_hi": 0.99},
    }

    # 噪声标准差
    NOISE_STD = 0.5

    def __init__(self, seed=42):
        """初始化随机种子，保证仿真可复现。"""
        self.rng = np.random.RandomState(seed)

    def get_reward(self, context: Context, strategy_id: str) -> float:
        """
        计算真实奖励（含噪声）。

        奖励计算逻辑：
          1. 查找策略的适用唤醒区间
          2. 唤醒匹配度 = 距区间中心的距离（越近越高）
          3. 社交策略额外受"白天+工作日"加权
          4. 感官策略额外受"极高唤醒"加权
          5. 加噪声

        Args:
            context: 情境特征
            strategy_id: 策略 ID

        Returns:
            仿真奖励值（1-5，含噪声后可能略微越界，需裁剪）
        """
        profile = self.STRATEGY_PROFILES.get(strategy_id)
        if profile is None:
            # 未知策略，返回中等偏低的随机奖励
            return float(np.clip(2.5 + self.rng.randn() * self.NOISE_STD, 1.0, 5.0))

        arousal = context.current_arousal
        valence = context.current_valence
        base = profile["base"]

        # ---- 唤醒匹配度 ----
        a_lo = profile["arousal_lo"]
        a_hi = profile["arousal_hi"]
        a_mid = (a_lo + a_hi) / 2.0
        a_radius = (a_hi - a_lo) / 2.0

        # 在区间内匹配度高，区间外线性衰减
        if a_lo <= arousal <= a_hi:
            arousal_fit = 1.0
        else:
            dist = abs(arousal - a_mid) - a_radius
            arousal_fit = max(0.0, 1.0 - dist * 2.0)  # 每偏离 0.1 降 0.2

        # ---- 效价辅助匹配（低效价更需要干预） ----
        # 低效价时策略更有价值（人在不舒服时更愿意尝试策略）
        valence_bonus = 0.0
        if valence < 0.4:
            valence_bonus = 0.3 * (1.0 - valence / 0.4)

        # ---- 社交策略：白天+工作日加权 ----
        social_bonus = 0.0
        if strategy_id == "talk_friend":
            # 白天（9-18点）加权
            hour = context.time_of_day
            if 9 <= hour <= 18:
                social_bonus += 0.4
            else:
                social_bonus -= 0.3
            # 工作日加权（weekday 0-4）
            if context.weekday <= 4:
                social_bonus += 0.3
            else:
                social_bonus -= 0.2

        # ---- 感官策略：极高唤醒加权 ----
        sensory_bonus = 0.0
        if strategy_id == "cold_water" and arousal >= 0.75:
            sensory_bonus = 0.5 * (arousal - 0.75) / 0.25  # 最高额外 +0.5
        elif strategy_id == "cold_water" and arousal < 0.60:
            sensory_bonus = -0.5  # 正常状态下冷水洗脸不太好

        # ---- 综合得分 ----
        score = base * arousal_fit + valence_bonus + social_bonus + sensory_bonus

        # 加噪声（高斯，σ=0.5）
        noise = self.rng.randn() * self.NOISE_STD

        reward = score + noise
        return float(np.clip(reward, 1.0, 5.0))

    def get_oracle_reward(self, context: Context) -> float:
        """
        计算该情境下的"全知最优"奖励（即所有策略中最好的）。
        不含噪声，用于计算遗憾。
        """
        best = -float("inf")
        for sid in self.STRATEGY_PROFILES:
            profile = self.STRATEGY_PROFILES[sid]
            arousal = context.current_arousal
            valence = context.current_valence
            base = profile["base"]

            a_lo = profile["arousal_lo"]
            a_hi = profile["arousal_hi"]
            a_mid = (a_lo + a_hi) / 2.0
            a_radius = (a_hi - a_lo) / 2.0

            if a_lo <= arousal <= a_hi:
                arousal_fit = 1.0
            else:
                dist = abs(arousal - a_mid) - a_radius
                arousal_fit = max(0.0, 1.0 - dist * 2.0)

            valence_bonus = 0.0
            if valence < 0.4:
                valence_bonus = 0.3 * (1.0 - valence / 0.4)

            social_bonus = 0.0
            if sid == "talk_friend":
                hour = context.time_of_day
                if 9 <= hour <= 18:
                    social_bonus += 0.4
                else:
                    social_bonus -= 0.3
                if context.weekday <= 4:
                    social_bonus += 0.3
                else:
                    social_bonus -= 0.2

            sensory_bonus = 0.0
            if sid == "cold_water" and arousal >= 0.75:
                sensory_bonus = 0.5 * (arousal - 0.75) / 0.25
            elif sid == "cold_water" and arousal < 0.60:
                sensory_bonus = -0.5

            score = base * arousal_fit + valence_bonus + social_bonus + sensory_bonus
            if score > best:
                best = score

        return best


# ================================================================
# 随机情境生成器
# ================================================================

def generate_random_context(rng: np.random.RandomState) -> Context:
    """
    生成随机情境特征。

    范围设计（覆盖多种真实场景）：
      - 效价：0.1 ~ 0.9（避免极端值）
      - 唤醒：0.1 ~ 0.9
      - 时段：6 ~ 22（覆盖早到晚）
      - 星期：0 ~ 6
      - 睡眠：4 ~ 9（小时）
      - 诱因：1 ~ 8（类别编码）
    """
    valence = rng.uniform(0.1, 0.9)
    arousal = rng.uniform(0.1, 0.9)
    hour = rng.uniform(6.0, 22.0)
    weekday = rng.randint(0, 7)      # 0~6
    sleep = rng.uniform(4.0, 9.0)
    trigger = rng.randint(1, 9)       # 1~8

    return extract_context(valence, arousal, hour, weekday, sleep, trigger)


# ================================================================
# 主仿真流程
# ================================================================

def run_simulation(n_rounds: int = 500, seed: int = 42):
    """
    运行完整的仿真循环。

    Args:
        n_rounds: 仿真轮次（默认 500）
        seed: 随机种子

    Returns:
        (rewards_list, regret_list, context_list, strategy_list)
    """
    rng = np.random.RandomState(seed)
    gt = GroundTruth(seed=seed)
    bandit = ContextualBandit(strategies=DEFAULT_STRATEGIES)

    rewards = []
    regrets = []
    contexts = []
    strategies = []

    for i in range(n_rounds):
        ctx = generate_random_context(rng)
        rec = bandit.recommend(ctx)
        reward = gt.get_reward(ctx, rec.strategy_id)
        oracle = gt.get_oracle_reward(ctx)
        regret = oracle - reward

        bandit.update(rec.strategy_id, ctx, reward)

        rewards.append(reward)
        regrets.append(regret)
        contexts.append(ctx)
        strategies.append(rec.strategy_id)

    return rewards, regrets, contexts, strategies, bandit


# ================================================================
# 收敛指标分析（分块统计）
# ================================================================

def analyze_convergence(rewards, regrets, strategies, block_size=100):
    """
    将仿真轮次分为若干块，统计每块的收敛指标。

    Args:
        rewards: 每轮奖励列表
        regrets: 每轮遗憾列表
        strategies: 每轮策略 ID 列表
        block_size: 每块大小（默认 100）

    Returns:
        blocks: 分块统计结果列表
    """
    n_blocks = len(rewards) // block_size
    blocks = []

    for b in range(n_blocks):
        start = b * block_size
        end = start + block_size

        block_rewards = rewards[start:end]
        block_regrets = regrets[start:end]
        block_strategies = strategies[start:end]

        avg_reward = np.mean(block_rewards)
        cum_regret = np.sum(block_regrets)

        # 统计 Top-3 推荐策略
        counter = Counter(block_strategies)
        top3 = counter.most_common(3)

        blocks.append({
            "block_id": b + 1,
            "rounds": f"{start+1}-{end}",
            "avg_reward": avg_reward,
            "cum_regret": cum_regret,
            "top3": top3,
        })

    return blocks


def print_convergence_report(blocks):
    """打印分块收敛报告。"""
    print()
    print(_b("=" * 70))
    print(_b("  收敛指标分析（每 100 轮一块）"))
    print(_b("=" * 70))
    print()
    # 表头
    header = (
        f"{'块':>4s}  {'轮次':>10s}  {'平均奖励':>10s}  "
        f"{'累积遗憾':>10s}  {'Top-3 推荐策略':<35s}"
    )
    print(_c(header))
    print(_d("-" * 70))

    for blk in blocks:
        # 根据平均奖励着色
        avg = blk["avg_reward"]
        if avg >= 3.5:
            color = _g
        elif avg >= 3.0:
            color = _y
        else:
            color = _r

        top3_str = ", ".join(
            f"{sid}({cnt})"
            for sid, cnt in blk["top3"]
        )

        regret_str = f"{blk['cum_regret']:>8.1f}"
        avg_str = f"{avg:>8.3f}"
        line = (
            f"  {blk['block_id']:>2d}    "
            f"{blk['rounds']:>10s}  "
            f"{color(avg_str)}    "
            f"{_y(regret_str)}    "
            f"{top3_str:<35s}"
        )
        print(line)

    print()
    # 收敛趋势判断
    if len(blocks) >= 2:
        first_avg = blocks[0]["avg_reward"]
        last_avg = blocks[-1]["avg_reward"]
        improvement = last_avg - first_avg
        if improvement > 0.3:
            print(_g(f"  -> 收敛趋势良好：平均奖励从 {first_avg:.3f} 提升至 {last_avg:.3f}（+{improvement:.3f}）"))
        elif improvement > 0:
            print(_y(f"  -> 收敛趋势一般：平均奖励从 {first_avg:.3f} 提升至 {last_avg:.3f}（+{improvement:.3f}）"))
        else:
            print(_r(f"  -> 收敛趋势不佳：平均奖励从 {first_avg:.3f} 下降至 {last_avg:.3f}（{improvement:.3f}）"))


# ================================================================
# 策略质量分析
# ================================================================

def analyze_strategy_quality(bandit: ContextualBandit, gt: GroundTruth,
                            rewards, strategies, rng):
    """
    分析每个策略的学习效果和偏好排名。

    Returns:
        quality_data: 每个策略的统计字典列表
    """
    strategy_ids = [s.id for s in DEFAULT_STRATEGIES]

    # 统计每个策略的推荐次数和平均奖励
    rec_counter = Counter(strategies)
    strategy_rewards = {sid: [] for sid in strategy_ids}

    for i, sid in enumerate(strategies):
        strategy_rewards[sid].append(rewards[i])

    quality_data = []
    for sid in strategy_ids:
        name = bandit.strategy_map[sid].name
        n_rec = rec_counter.get(sid, 0)
        avg_r = np.mean(strategy_rewards[sid]) if strategy_rewards[sid] else 0.0

        # 计算该策略在随机情境集上的"真实平均效果"
        # 生成 100 个随机情境采样
        sample_rewards = []
        for _ in range(100):
            ctx = generate_random_context(rng)
            sample_rewards.append(gt.get_reward(ctx, sid))
        true_avg = np.mean(sample_rewards)

        quality_data.append({
            "id": sid,
            "name": name,
            "n_rec": n_rec,
            "avg_reward_used": round(avg_r, 3),
            "true_avg_reward": round(true_avg, 3),
        })

    return quality_data


def print_strategy_quality_report(quality_data):
    """打印策略质量分析报告。"""
    print()
    print(_b("=" * 70))
    print(_b("  策略质量分析"))
    print(_b("=" * 70))
    print()

    # 按推荐次数降序排
    sorted_by_rec = sorted(quality_data, key=lambda x: x["n_rec"], reverse=True)

    # 按真实效果降序排（真实排名）
    sorted_by_true = sorted(quality_data, key=lambda x: x["true_avg_reward"], reverse=True)
    true_ranking = {d["id"]: i + 1 for i, d in enumerate(sorted_by_true)}

    # 按实际使用效果降序排（学习排名）
    sorted_by_used = sorted(
        [d for d in quality_data if d["n_rec"] > 0],
        key=lambda x: x["avg_reward_used"],
        reverse=True,
    )
    used_ranking = {d["id"]: i + 1 for i, d in enumerate(sorted_by_used)}

    # 表头
    header = (
        f"{'策略':<18s}  {'推荐次数':>8s}  {'使用时均奖':>10s}  "
        f"{'真实均奖':>10s}  {'真实排名':>8s}  {'学习排名':>8s}"
    )
    print(_c(header))
    print(_d("-" * 70))

    for d in sorted_by_rec:
        tr = true_ranking.get(d["id"], "-")
        ur = used_ranking.get(d["id"], "-")
        n = d["n_rec"]

        # 推荐次数着色
        if n >= 80:
            n_str = _g(f"{n:>8d}")
        elif n >= 40:
            n_str = _y(f"{n:>8d}")
        else:
            n_str = _r(f"{n:>8d}")

        tr_str = _g(f"{tr:>8d}") if tr == 1 else (str(tr).rjust(8))
        ur_str = _g(f"{ur:>8d}") if ur == 1 else (str(ur).rjust(8))

        line = (
            f"  {d['name']:<18s}  "
            f"{n_str}  "
            f"{d['avg_reward_used']:>10.3f}  "
            f"{d['true_avg_reward']:>10.3f}  "
            f"{tr_str}  "
            f"{ur_str}"
        )
        print(line)

    print()
    # 打印真实排名
    print(_d("  [真实效果排名] " + " > ".join(
        f"{d['name']}(={d['true_avg_reward']:.2f})" for d in sorted_by_true[:5]
    ) + " ..."))


# ================================================================
# 边缘用例测试
# ================================================================

def test_serialization(bandit: ContextualBandit):
    """测试序列化/反序列化往返。"""
    print()
    print(_b("=" * 70))
    print(_b("  边缘用例测试"))
    print(_b("=" * 70))
    print()

    # ---- 1. 序列化/反序列化往返 ----
    print(_b("  [1] 序列化/反序列化往返测试"))
    json_str = bandit.serialize_state()
    bandit_restored = ContextualBandit.load_state(
        json_str,
        strategies=DEFAULT_STRATEGIES,
    )

    # 验证：对同一情境，恢复后的老虎机给出相同推荐
    test_ctx = extract_context(0.5, 0.5, 14.0, 2, 7.0, 3)
    rec_orig = bandit.recommend(test_ctx)
    rec_rest = bandit_restored.recommend(test_ctx)

    if rec_orig.strategy_id == rec_rest.strategy_id:
        print(_g(f"    [通过] 序列化前后推荐一致：{rec_orig.strategy_name} (UCB={rec_orig.ucb_score})"))
    else:
        print(_r(f"    [失败] 序列化前推荐 {rec_orig.strategy_name}，恢复后推荐 {rec_rest.strategy_name}"))

    # 验证全局统计
    if (bandit_restored.global_reward_sum == bandit.global_reward_sum and
            bandit_restored.global_reward_count == bandit.global_reward_count):
        print(_g(f"    [通过] 全局奖励统计一致：sum={bandit.global_reward_sum:.1f}, count={bandit.global_reward_count}"))
    else:
        print(_r(f"    [失败] 全局奖励统计不一致"))

    # 验证各臂的样本数
    all_match = True
    for s in DEFAULT_STRATEGIES:
        if bandit.arms[s.id].n != bandit_restored.arms[s.id].n:
            all_match = False
            break
    if all_match:
        print(_g(f"    [通过] 各臂样本数全部一致"))
    else:
        print(_r(f"    [失败] 部分臂样本数不一致"))

    # ---- 2. 冷启动探索行为测试 ----
    print()
    print(_b("  [2] 冷启动探索行为测试"))
    fresh_bandit = ContextualBandit(strategies=DEFAULT_STRATEGIES)
    cold_starts = []
    for i in range(5):
        ctx = extract_context(
            random.uniform(0.2, 0.8),
            random.uniform(0.2, 0.8),
            random.uniform(8, 18),
            random.randint(0, 6),
            random.uniform(5, 8),
            random.randint(1, 8),
        )
        rec = fresh_bandit.recommend(ctx)
        cold_starts.append(rec.strategy_id)

    # 检查：前5次推荐是否体现探索行为
    # 注意：LinUCB 在所有臂零数据时 UCB 相等，倾向于选择第一个策略
    # 这是已知特性；真正的探索发生在获得初始数据后
    unique_count = len(set(cold_starts))
    if unique_count >= 3:
        print(_g(f"    [通过] 前 5 次推荐覆盖 {unique_count} 种不同策略（体现探索）：{cold_starts}"))
    elif unique_count >= 2:
        print(_y(f"    [提示] 前 5 次推荐覆盖 {unique_count} 种策略（LinUCB 零数据时各臂等价）：{cold_starts}"))
    else:
        print(_y(f"    [提示] 前 5 次推荐全部相同（{cold_starts[0]}），"
                 f"这是 LinUCB 在无先验数据时的正常行为——各臂 UCB 相等，选第一个"))
        # 进一步验证：用不同上下文和不同奖励初始化后，推荐是否呈现多样性
        warm_bandit = ContextualBandit(strategies=DEFAULT_STRATEGIES)
        # 给每个策略不同的初始数据（模拟不同情境下的不同效果）
        for idx, s in enumerate(DEFAULT_STRATEGIES):
            ctx_init = extract_context(
                0.1 + idx * 0.08,  # 不同效价
                0.1 + idx * 0.08,  # 不同唤醒
                8.0 + idx,         # 不同时段
                idx % 7,
                5.0 + idx * 0.4,
                1 + idx,
            )
            # 不同策略给予不同奖励，制造差异
            reward = 2.0 + (idx % 5) * 0.5
            warm_bandit.update(s.id, ctx_init, reward)
        warm_recs = []
        for _ in range(10):
            ctx = extract_context(
                random.uniform(0.2, 0.8),
                random.uniform(0.2, 0.8),
                random.uniform(8, 18),
                random.randint(0, 6),
                random.uniform(5, 8),
                random.randint(1, 8),
            )
            rec = warm_bandit.recommend(ctx)
            warm_recs.append(rec.strategy_id)
        warm_unique = len(set(warm_recs))
        if warm_unique >= 2:
            print(_g(f"    [通过] 初始数据后推荐呈现多样性（{warm_unique} 种策略 / 10 次推荐）：{warm_recs}"))
        else:
            print(_r(f"    [失败] 初始数据后推荐仍单一：{warm_recs}"))

    # ---- 3. 动态添加新策略测试 ----
    print()
    print(_b("  [3] 动态添加新策略测试"))

    # 检查新策略不在现有列表中
    new_sid = "guided_meditation"
    if new_sid in fresh_bandit.strategy_map:
        print(_y(f"    [跳过] 策略 {new_sid} 已存在"))
    else:
        new_strategy = Strategy(
            id="guided_meditation",
            name="引导冥想",
            category="cognitive",
        )
        fresh_bandit.add_strategy(new_strategy)

        if new_sid in fresh_bandit.strategy_map:
            print(_g(f"    [通过] 新策略已添加：{new_strategy.name} ({new_strategy.id})"))

            # 验证新策略可以被推荐
            # 由于新臂 UCB 不确定性高，在一定情境下可能被选中
            ctx_test = extract_context(0.3, 0.3, 10.0, 3, 6.0, 2)
            rec = fresh_bandit.recommend(ctx_test)
            # 即使没被选中，只要不报错就算通过
            print(_g(f"    [通过] 推荐正常运行，当前推荐：{rec.strategy_name}"))

            # 更新新策略几次
            for _ in range(3):
                r = random.uniform(2.0, 5.0)
                fresh_bandit.update(new_sid, ctx_test, r)

            arm = fresh_bandit.arms[new_sid]
            if arm.n == 3:
                print(_g(f"    [通过] 新策略累计更新 {arm.n} 次，学习正常"))
            else:
                print(_r(f"    [失败] 新策略累计更新次数异常：{arm.n}"))
        else:
            print(_r(f"    [失败] 新策略添加后无法在策略映射中找到"))


# ================================================================
# 入口
# ================================================================

def main():
    """主函数：运行完整仿真测试并输出报告。"""
    # 固定随机种子保证可复现
    np.random.seed(42)
    random.seed(42)

    print()
    print(_b("=" * 70))
    print(_g("  心潮 EmoWave · LinUCB 推荐引擎仿真测试"))
    print(_b("=" * 70))
    print(_d("  模拟 500 轮「情境→推荐→奖励反馈」循环，验证推荐效果与收敛性"))
    print()

    # ---- 运行仿真 ----
    print(_y("  [运行] 启动仿真..."))
    rewards, regrets, contexts, strategies, bandit = run_simulation(
        n_rounds=500, seed=42
    )

    # ---- 仿真总结 ----
    overall_avg = np.mean(rewards)
    overall_regret = np.sum(regrets)
    print(_g(f"  [完成] 仿真结束"))
    print(_d(f"  总轮次：500 | 总平均奖励：{overall_avg:.3f} | 总累积遗憾：{overall_regret:.1f}"))

    # ---- 分块收敛分析 ----
    blocks = analyze_convergence(rewards, regrets, strategies, block_size=100)
    print_convergence_report(blocks)

    # ---- 策略质量分析 ----
    rng_quality = np.random.RandomState(123)
    gt = GroundTruth(seed=123)
    quality_data = analyze_strategy_quality(bandit, gt, rewards, strategies, rng_quality)
    print_strategy_quality_report(quality_data)

    # ---- 边缘用例测试 ----
    test_serialization(bandit)

    # ---- 结束 ----
    print()
    print(_b("=" * 70))
    print(_g("  全部测试完成"))
    print(_b("=" * 70))
    print()


if __name__ == "__main__":
    main()
