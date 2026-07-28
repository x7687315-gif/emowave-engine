"""
models.py — 心潮 EmoWave 个人情绪校准引擎 · 核心数据结构

本模块定义引擎中所有关键数据对象。
设计原则：
  - 使用 dataclass 而非 dict，保证类型安全与 IDE 补全
  - 每个字段附带文档说明其语义与取值范围
  - 所有时间字段统一使用 Unix 时间戳（float，秒级精度）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Tuple


# ================================================================
# 枚举类型
# ================================================================

class AlertLevel(Enum):
    """基线漂移告警级别"""
    INFO = "info"           # 轻微偏离，仅记录
    WARNING = "warning"     # 中度偏离，建议用户关注
    ACTION = "action"       # 严重偏离，建议用户调整阈值


class ModelSource(Enum):
    """阈值来源：标识当前使用的是群体通用阈值还是个人化阈值"""
    POPULATION = "population"   # 冷启动阶段，使用群体通用阈值
    HYBRID = "hybrid"           # 过渡阶段，群体与个人混合
    PERSONAL = "personal"       # 已切换到纯个人模型


# ================================================================
# 一、原始采样数据
# ================================================================

@dataclass
class TimeSeriesSample:
    """
    单个时间点的采样数据。
    由 UI 层的滑条操作或手表传感器产生，每秒~1次。
    """
    timestamp: float       # Unix 时间戳（秒）
    valence: float         # 效价：0 = 极不舒服, 1 = 极舒服
    arousal: float         # 唤醒：0 = 极困倦, 1 = 极兴奋
    hr: Optional[float] = None        # 心率（BPM），手表未连接时为 None
    hrv: Optional[float] = None       # HRV（ms，RMSSD），手表未连接时为 None


@dataclass
class EmotionEventRaw:
    """
    一次完整的情绪事件原始数据（用户点击"已平静"后汇总）。
    这是引擎的核心输入。
    """
    event_id: str                              # 事件唯一标识
    samples: List[TimeSeriesSample]            # 本次事件的所有时序采样
    user_peak_rating: Optional[float] = None   # 用户事后自评峰值强度（0-10）
    recovery_duration: Optional[float] = None  # 用户报告的恢复时长（秒）
    trigger_tags: List[str] = field(default_factory=list)      # 诱因标签（如 "工作会议"）
    coping_methods: List[str] = field(default_factory=list)     # 应对方式（如 "深呼吸"）
    coping_ratings: Dict[str, int] = field(default_factory=dict)  # 应对效果评分 {方法: 1-5}
    body_symptoms: List[str] = field(default_factory=list)     # 躯体症状（如 "胸口压抑"）
    calm_timestamp: Optional[float] = None     # 用户点击"已平静"的时间戳


# ================================================================
# 二、标注输出
# ================================================================

@dataclass
class PhysiologicalPeak:
    """
    生理信号检测到的极点信息。
    annotator 模块的中间产物。
    """
    timestamp: float             # 极点发生时间
    hr_zscore: float = 0.0       # 心率偏离基线的 z-score
    hrv_drop_pct: float = 0.0    # HRV 相对于局部基线的下降百分比（正值 = 下降）
    arousal_spike: float = 0.0    # 唤醒度在该时刻的值
    composite_score: float = 0.0  # 多信号融合得分（0-1），越高越危险


@dataclass
class DangerousRiseSegment:
    """
    检测到的"危险上升段"。
    定义为：arousal 持续快速上升且伴随生理恶化的时间段。
    """
    start_time: float
    end_time: float
    peak_arousal_slope: float      # 该段内的最大 arousal 斜率
    avg_hr_zscore: float           # 该段内心率的平均 z-score
    hrv_drop_at_peak: float        # 该段内 HRV 的最大下降百分比


@dataclass
class EventProfile:
    """
    一次情绪事件的完整标注结果。
    这是 annotator 模块的核心输出。
    所有时间字段均为 Unix 时间戳。
    """
    event_id: str

    # --- 关键时间点 ---
    onset_time: float                          # 事件开始时间（第一个采样点）
    peak_time: float                            # 真正的极点时间（融合生理信号后）
    calm_time: float                            # 恢复平静的时间

    # --- 极点特征 ---
    peak_valence: float                         # 极点时刻的效价
    peak_arousal: float                         # 极点时刻的唤醒度
    subjective_peak: Optional[float] = None     # 用户自评峰值（0-10）
    physiological_peak_score: float = 0.0       # 生理极点融合得分（0-1）
    physiological_peak_confidence: float = 0.0  # 极点检测置信度（0-1）

    # --- 恢复特征 ---
    recovery_duration: float = 0.0              # 恢复时长（秒，从极点到平静）
    recovery_speed: float = 0.0                 # 恢复速度 = arousal_drop / duration

    # --- 危险段 ---
    dangerous_rise_segments: List[DangerousRiseSegment] = field(default_factory=list)
    trigger_window: Optional[Tuple[float, float]] = None  # 诱因推测时间窗 (start, end)

    # --- 原始数据引用 ---
    physio_peaks: List[PhysiologicalPeak] = field(default_factory=list)
    sample_count: int = 0                       # 总采样点数


# ================================================================
# 三、基线与漂移
# ================================================================

@dataclass
class BaselineVector:
    """
    用户当前的"静息基线向量"。
    由 baseline 模块每天更新一次。
    """
    resting_hrv_mean: float = 50.0     # 静息 HRV 均值（ms）
    resting_hr: float = 72.0            # 静息心率（BPM）
    sleep_score: float = 7.0            # 前夜睡眠评分（0-10）
    typical_valence_8am: float = 0.55   # 早间典型效价
    typical_valence_6pm: float = 0.50   # 晚间典型效价
    date: str = ""                       # 该基线对应的日期（YYYY-MM-DD）


@dataclass
class DailySummary:
    """
    某一天的行为与生理摘要，用于更新基线。
    由引擎在每日结束时（或用户首次打开 app 时）自动汇总。
    """
    date: str                              # YYYY-MM-DD
    avg_resting_hrv: float                 # 当日静息 HRV 均值
    avg_resting_hr: float                 # 当日静息心率均值
    sleep_score: float                    # 前夜睡眠评分
    morning_valence_avg: float            # 早间（6-10点）效价均值
    evening_valence_avg: float            # 晚间（17-21点）效价均值
    event_count: int = 0                  # 当日情绪事件次数
    peak_arousal_max: float = 0.0         # 当日最高唤醒度


@dataclass
class BaselineShiftEvent:
    """
    基线漂移告警。
    当检测到持续性偏离时由 baseline 模块生成。
    """
    alert_level: AlertLevel
    detected_date: str                     # YYYY-MM-DD
    shifted_dimensions: List[str]          # 发生漂移的维度名称列表
    shift_magnitudes: Dict[str, float]     # 各维度的偏离量（标准差倍数）
    message: str = ""                      # 人类可读的告警描述


# ================================================================
# 四、阈值与置信度
# ================================================================

@dataclass
class PersonalThresholds:
    """
    个性化的情绪极点警戒阈值。
    由 threshold 模块输出，供预警系统使用。
    """
    # --- 核心阈值 ---
    high_risk_arousal: float               # 高唤醒阈值（arousal > 此值 → 高风险）
    high_risk_valence: float               # 低效价阈值（valence < 此值 → 高风险）
    hrv_drop_percent: float                # HRV 下降百分比阈值
    hr_surge_zscore: float                # 心率激增 z-score 阈值
    dangerous_rise_slope: float            # 危险上升斜率阈值

    # --- 元信息 ---
    model_confidence: float = 0.0          # 模型置信度（0-1）
    model_source: ModelSource = ModelSource.POPULATION  # 阈值来源
    event_count: int = 0                   # 用于训练的事件总数
    last_updated: str = ""                 # 最后更新日期


# ================================================================
# 五、引擎状态（持久化对象）
# ================================================================

@dataclass
class EngineState:
    """
    引擎的完整可持久化状态。
    设计为可序列化为 JSON，存储在设备端本地。
    """
    user_id: str = ""
    baseline: BaselineVector = field(default_factory=BaselineVector)
    baseline_history: List[BaselineVector] = field(default_factory=list)
    event_profiles: List[EventProfile] = field(default_factory=list)
    thresholds: PersonalThresholds = field(default_factory=PersonalThresholds)
    total_events_processed: int = 0
    model_confidence: float = 0.0
    created_date: str = ""
    last_updated: str = ""
