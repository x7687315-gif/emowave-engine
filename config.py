"""
config.py — 心潮 EmoWave 个人情绪校准引擎 · 全局配置

本模块集中管理所有可调参数。
设计原则：每一个参数都附带注释说明其来源或选择理由，
         方便未来替换统计方法时快速定位需要改动的常量。

典型替换场景：
  - 将 EWMA 替换为贝叶斯在线模型时，只需修改此文件中的 alpha、window 等参数
  - 调整冷启动策略时，修改 COLD_START 相关常量即可
"""

from dataclasses import dataclass, field
from typing import List


# ================================================================
# 一、自动标注参数
# ================================================================

# HRV 突降检测窗口（秒）
#   理由：HRV 对急性压力的反应通常在 10-30 秒内显现 [参考文献: Kim et al., 2018]
#   窗口过短会捕获噪声，过长会延迟极点检测
HRV_DROP_WINDOW_SECONDS: int = 20

# 判定"HRV 突降"的最小下降百分比
#   理由：HRV 下降 20%+ 在文献中通常被认为是有意义的自主神经变化
#   设为 25% 以平衡灵敏度与误报
HRV_DROP_THRESHOLD_PERCENT: float = 0.25

# 心率激增检测：与滑动平均基线偏差的 z-score 阈值
#   理由：z > 2.5 在正态假设下对应 p < 0.01 的极端事件
HR_SURGE_ZSCORE_THRESHOLD: float = 2.5

# 心率滑动平均窗口长度（采样点数，假设 ~1Hz 采样）
HR_MA_WINDOW: int = 10

# 生理极点得分的组成权重
#   格式: (hr_surge_weight, hrv_drop_weight, arousal_spike_weight)
PHYSIO_PEAK_WEIGHTS: tuple = (0.35, 0.40, 0.25)
#   理由：HRV 被赋最高权重，因为它对情绪极点的特异性最高；
#         心率次之（受运动干扰）；唤醒度最末（主观、有延迟）

# 转折点检测的最小间隔（秒），避免在极短时间内重复检测
TURNING_POINT_MIN_GAP_SECONDS: float = 30.0

# "危险上升段"判定：arousal 斜率超过此阈值时认为进入危险上升
#   单位：arousal_unit / second。假设用户每 5 秒采样一次，
#   arousal 在 30 秒内从 0.3 上升到 0.8 → 斜率 ≈ 0.017，阈值设为 0.012
DANGEROUS_RISE_SLOPE_THRESHOLD: float = 0.012

# 危险上升段的最短持续时间（秒），过滤毛刺
DANGEROUS_RISE_MIN_DURATION: float = 15.0


# ================================================================
# 二、基线建模参数
# ================================================================

# 指数加权移动平均的衰减因子 alpha
#   公式: baseline_new = alpha * x_new + (1 - alpha) * baseline_old
#   alpha = 1/(N+1), 此处 N=7 对应约一周的等效记忆
#   理由：情绪基线的变化通常是昼夜周期叠加慢趋势，
#         7 天的等效窗口能捕捉周级别的漂移而不过度响应单日噪声
EWMA_ALPHA: float = 1.0 / 8.0  # ≈ 0.125, 等效窗口 ≈ 7 天

# 变点检测：连续多少天偏离超过 N 个标准差才触发
#   理由：3 天可容忍周末效应等短期波动；
#        5 天的标准差确保统计稳定性（需至少 7 天历史数据）
SHIFT_CONSECUTIVE_DAYS: int = 3
SHIFT_STD_DEVIATIONS: float = 2.0

# 基线向量所需的最低历史天数才能进行漂移检测
#   理由：低于此值时标准差估计不可靠
BASELINE_MIN_HISTORY_DAYS: int = 7


# ================================================================
# 三、冷启动参数
# ================================================================

# 触发冷启动→个人模型切换的最小事件数
#   理由：20 次事件约覆盖 2-3 周的典型使用，
#         此时 EWMA 基线已有足够数据，置信度有意义
COLD_START_MIN_EVENTS: int = 20

# 置信度达到此阈值时切换到纯个人模型（0-1）
#   理由：0.75 表示模型对个人阈值的估计已有较高确定性
CONFIDENCE_SWITCH_THRESHOLD: float = 0.75

# 通用群体安全阈值（冷启动期间使用）
#   来源：基于情绪心理学文献中"高负性高唤醒"象限的操作化定义
POPULATION_THRESHOLDS = {
    "high_risk_arousal": 0.85,        # 唤醒度 > 0.85 视为高唤醒
    "high_risk_valence": 0.15,        # 效价 < 0.15 视为高负性
    "hrv_drop_percent": 0.30,         # HRV 下降 > 30%
    "hr_surge_zscore": 2.5,           # 心率 z-score > 2.5
    "dangerous_rise_slope": 0.012,     # 同上 DANGEROUS_RISE_SLOPE_THRESHOLD
}

# 置信度计算中各维度权重
#   (event_count_weight, baseline_age_weight, consistency_weight)
CONFIDENCE_WEIGHTS: tuple = (0.40, 0.25, 0.35)


# ================================================================
# 四、数据存储参数
# ================================================================

# 本地持久化时保留的历史事件最大数量
#   理由：控制设备端存储占用；90 天约覆盖一个完整季节周期
MAX_STORED_EVENTS: int = 500

# 基线历史保留天数
MAX_BASELINE_HISTORY_DAYS: int = 90


# ================================================================
# 五、可替换策略接口标识
# ================================================================
# 所有核心算法函数的默认实现标识符。
# 如需替换为贝叶斯模型或其他方法，修改此处并实现对应函数即可。

STRATEGY_BASELINE = "ewma"           # 当前: 指数加权移动平均 | 可选: "bayesian_online"
STRATEGY_SHIFT_DETECTION = "std_cumulative"  # 当前: 累积标准差偏离 | 可选: "bocpd"
STRATEGY_PEAK_DETECTION = "multi_signal"     # 当前: 多信号融合 | 可选: "rule_based"
