"""
report_generator.py — 心潮 EmoWave P4 周报生成器

本模块负责从积累的情绪事件数据中生成每周文字报告。
设计原则：
  - 离线优先隐私设计：所有数据在设备本地处理，不上传
  - 模板+规则的自然语言生成（NLG）：用预设模板拼接可读段落
  - 基于条件概率的简单模式发现
  - 全部代码注释与生成文本均为中文
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from datetime import datetime
from collections import Counter, defaultdict

from models import (
    EventProfile,
    DailySummary,
    BaselineShiftEvent,
    PhysiologicalPeak,
    AlertLevel,
)


# ================================================================
# 数据类定义
# ================================================================

@dataclass
class WeeklyData:
    """一周的聚合数据"""
    date_range: Tuple[str, str]  # (start_date, end_date)，格式 YYYY-MM-DD
    event_profiles: List[EventProfile]
    daily_summaries: List[DailySummary]
    baseline_shifts: List[BaselineShiftEvent]


@dataclass
class PatternRule:
    """一条模式规则"""
    condition: str       # 描述触发条件
    observation: str     # 描述观察到的模式
    frequency: float     # 该模式在数据中的出现频率（0~1）
    confidence: float    # 模式置信度（0~1）


@dataclass
class ReportSection:
    """周报的一个段落"""
    heading: str                         # 段落标题
    content: str                          # 自然语言文本
    data_highlights: Dict[str, float] = field(default_factory=dict)  # 关键数据点


@dataclass
class WeeklyReport:
    """周报结构化输出"""
    title: str
    date_range: Tuple[str, str]
    sections: List[ReportSection] = field(default_factory=list)


@dataclass
class WeeklySummaryStats:
    """一周汇总统计指标"""
    # 基本统计
    total_events: int = 0
    avg_peak_arousal: float = 0.0
    avg_peak_valence: float = 0.0
    avg_subjective_peak: float = 0.0

    # 触发因素 top-5：{触发标签: 出现次数}
    top_triggers: List[Tuple[str, int]] = field(default_factory=list)

    # 应对方式效果 top-5：{(方法名): 平均评分}
    top_coping_methods: List[Tuple[str, float]] = field(default_factory=list)

    # 恢复统计
    avg_recovery_duration: float = 0.0   # 平均恢复时长（秒）
    avg_recovery_speed: float = 0.0      # 平均恢复速度

    # 基线漂移告警数
    baseline_shift_count: int = 0

    # 星期分布：{星期名: 事件数}  如 {"周一": 3, "周二": 5, ...}
    day_of_week_distribution: Dict[str, int] = field(default_factory=dict)

    # 时段分布：{"早晨": n, "下午": n, "晚间": n, "深夜": n}
    time_of_day_distribution: Dict[str, int] = field(default_factory=dict)

    # 趋势对比（可选，上周数据可用时填充）
    trend_vs_last_week: Optional[Dict[str, str]] = None  # {指标名: 趋势描述}

    # 生理统计
    avg_hr_at_peak: float = 0.0          # 极点时刻平均心率
    avg_hrv_drop_at_peak: float = 0.0    # 极点时刻平均 HRV 下降百分比


# ================================================================
# 辅助函数
# ================================================================

def _get_time_of_day(hour: int) -> str:
    """
    根据小时返回时段名称。
    早晨 (6-10), 下午 (11-17), 晚间 (18-21), 深夜 (22-5)
    """
    if 6 <= hour <= 10:
        return "早晨"
    elif 11 <= hour <= 17:
        return "下午"
    elif 18 <= hour <= 21:
        return "晚间"
    else:
        return "深夜"


def _format_duration(seconds: float) -> str:
    """将秒数转换为人类可读的时长字符串"""
    if seconds < 0:
        return "未知"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes == 0:
        return f"{secs}秒"
    elif minutes < 60:
        return f"{minutes}分{secs}秒"
    else:
        hours = minutes // 60
        remaining_min = minutes % 60
        return f"{hours}小时{remaining_min}分"


def _trend_description(this_week: float, last_week: float, metric_name: str) -> str:
    """
    生成本周与上周对比的趋势描述文本。
    返回形如 "上升了X%"、"保持稳定"、"下降了X%" 的字符串。
    """
    if last_week == 0:
        if this_week > 0:
            return f"{metric_name}本周为 {this_week:.1f}（上周无数据）"
        return f"{metric_name}暂无足够数据对比"

    change_pct = ((this_week - last_week) / last_week) * 100.0
    abs_change = abs(change_pct)

    if abs_change < 5:
        return f"{metric_name}保持稳定"
    elif change_pct > 0:
        return f"{metric_name}上升了{change_pct:.1f}%"
    else:
        return f"{metric_name}下降了{abs_change:.1f}%"


# ================================================================
# 一、WeeklySummarizer — 数据聚合
# ================================================================

class WeeklySummarizer:
    """
    数据聚合器：从 WeeklyData 中提取一周统计指标。
    所有计算均在本地完成，不上传任何数据。
    """

    def summarize(
        self,
        weekly_data: WeeklyData,
        last_week_data: Optional[WeeklyData] = None,
    ) -> WeeklySummaryStats:
        """
        汇总一周数据，返回统计指标。
        如果提供 last_week_data，会额外计算趋势对比。
        """
        stats = WeeklySummaryStats()
        events = weekly_data.event_profiles
        summaries = weekly_data.daily_summaries

        # --- 空数据保护 ---
        if not events:
            stats.date_range = weekly_data.date_range
            return stats

        # --- 基本统计 ---
        stats.total_events = len(events)
        stats.avg_peak_arousal = self._safe_mean([e.peak_arousal for e in events])
        stats.avg_peak_valence = self._safe_mean([e.peak_valence for e in events])
        subjective_peaks = [e.subjective_peak for e in events if e.subjective_peak is not None]
        stats.avg_subjective_peak = self._safe_mean(subjective_peaks)

        # --- 恢复统计 ---
        stats.avg_recovery_duration = self._safe_mean([e.recovery_duration for e in events])
        stats.avg_recovery_speed = self._safe_mean([e.recovery_speed for e in events])

        # --- 基线漂移 ---
        stats.baseline_shift_count = len(weekly_data.baseline_shifts)

        # --- 触发因素 top-5 ---
        stats.top_triggers = self._extract_triggers(events)

        # --- 应对方式效果 top-5 ---
        stats.top_coping_methods = self._extract_coping_methods(events)

        # --- 星期分布 ---
        stats.day_of_week_distribution = self._compute_day_of_week(events)

        # --- 时段分布 ---
        stats.time_of_day_distribution = self._compute_time_of_day(events)

        # --- 生理统计 ---
        stats.avg_hr_at_peak = self._compute_avg_hr_at_peak(events)
        stats.avg_hrv_drop_at_peak = self._compute_avg_hrv_drop(events)

        # --- 趋势对比 ---
        if last_week_data is not None:
            stats.trend_vs_last_week = self._compute_trend(weekly_data, last_week_data)

        return stats

    @staticmethod
    def _safe_mean(values: List[float]) -> float:
        """安全的均值计算，空列表返回 0.0"""
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _extract_triggers(self, events: List[EventProfile]) -> List[Tuple[str, int]]:
        """提取 top-5 触发因素及其出现次数"""
        # 需要从事件关联的原始数据中获取触发标签
        # EventProfile 本身没有 trigger_tags 字段，
        # 但我们可以从 EmotionEventRaw 获取。
        # 这里假设 EventProfile 可通过扩展属性或外部映射获取触发标签。
        # 由于 EventProfile 没有直接的 trigger_tags，我们使用关联方式。
        # 在实际集成中，需要传入 trigger 映射。
        # 暂时返回空列表，由 PatternMiner 从外部数据补充。
        return []

    def _extract_coping_methods(self, events: List[EventProfile]) -> List[Tuple[str, float]]:
        """提取 top-5 应对方式及其平均评分"""
        # 同理，EventProfile 没有直接的 coping_ratings 字段。
        # 返回空列表，由外部集成时补充。
        return []

    def _compute_day_of_week(self, events: List[EventProfile]) -> Dict[str, int]:
        """计算星期分布（周一至周日的事件数）"""
        day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        distribution = {name: 0 for name in day_names}
        for event in events:
            dt = datetime.fromtimestamp(event.onset_time)
            # weekday(): 周一=0, 周日=6
            distribution[day_names[dt.weekday()]] += 1
        return distribution

    def _compute_time_of_day(self, events: List[EventProfile]) -> Dict[str, int]:
        """计算时段分布（早晨/下午/晚间/深夜）"""
        distribution: Dict[str, int] = defaultdict(int)
        for event in events:
            dt = datetime.fromtimestamp(event.onset_time)
            tod = _get_time_of_day(dt.hour)
            distribution[tod] += 1
        return dict(distribution)

    def _compute_avg_hr_at_peak(self, events: List[EventProfile]) -> float:
        """计算极点时刻的平均心率"""
        hr_values: List[float] = []
        for event in events:
            for peak in event.physio_peaks:
                # physio_peaks 里没有直接的 hr 值，但有 hr_zscore
                # 我们需要通过 hr_zscore 和基线 hr 推算
                # 简化处理：如果 physio_peak 有 hr_zscore，暂记为缺失
                pass
        # 如果没有直接 hr 数据，返回 0
        return 0.0

    def _compute_avg_hrv_drop(self, events: List[EventProfile]) -> float:
        """计算极点时刻的平均 HRV 下降百分比"""
        hrv_drops: List[float] = []
        for event in events:
            for peak in event.physio_peaks:
                if peak.hrv_drop_pct > 0:
                    hrv_drops.append(peak.hrv_drop_pct)
        return self._safe_mean(hrv_drops)

    def _compute_trend(
        self,
        this_week: WeeklyData,
        last_week: WeeklyData,
    ) -> Dict[str, str]:
        """计算本周与上周的趋势对比描述"""
        trends: Dict[str, str] = {}

        this_events = this_week.event_profiles
        last_events = last_week.event_profiles

        # 事件总数趋势
        trends["情绪事件数"] = _trend_description(
            len(this_events), len(last_events), "情绪事件数"
        )

        # 平均唤醒度趋势
        this_arousal = self._safe_mean([e.peak_arousal for e in this_events])
        last_arousal = self._safe_mean([e.peak_arousal for e in last_events])
        trends["平均唤醒度"] = _trend_description(
            this_arousal, last_arousal, "平均唤醒度"
        )

        # 平均恢复时长趋势
        this_recovery = self._safe_mean([e.recovery_duration for e in this_events])
        last_recovery = self._safe_mean([e.recovery_duration for e in last_events])
        trends["平均恢复时长"] = _trend_description(
            this_recovery, last_recovery, "平均恢复时长"
        )

        return trends


# ================================================================
# 二、PatternMiner — 模式发现
# ================================================================

class PatternMiner:
    """
    简单模式挖掘器：基于条件概率发现数据中的规律。
    所有计算均在本地完成，保护用户隐私。
    """

    def mine_patterns(
        self,
        weekly_data: WeeklyData,
        stats: WeeklySummaryStats,
        trigger_tags_map: Optional[Dict[str, List[str]]] = None,
        coping_ratings_map: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> List[PatternRule]:
        """
        从一周数据中挖掘模式规则。

        参数：
            weekly_data: 一周聚合数据
            stats: 已计算的汇总统计
            trigger_tags_map: {event_id: [触发标签列表]}，外部传入的触发标签映射
            coping_ratings_map: {event_id: {方法: 评分}}，外部传入的应对评分映射
        """
        patterns: List[PatternRule] = []
        events = weekly_data.event_profiles
        if not events:
            return patterns

        total = stats.total_events

        # --- a. 触发因素模式 ---
        if trigger_tags_map:
            patterns.extend(
                self._mine_trigger_patterns(events, total, trigger_tags_map)
            )

        # --- b. 时段模式 ---
        patterns.extend(self._mine_time_patterns(events, stats))

        # --- c. 应对效果模式 ---
        if coping_ratings_map:
            patterns.extend(
                self._mine_coping_patterns(events, coping_ratings_map)
            )

        # --- d. 恢复速度模式 ---
        if coping_ratings_map:
            patterns.extend(
                self._mine_recovery_patterns(events, coping_ratings_map)
            )

        # --- e. 周末 vs 工作日差异 ---
        patterns.extend(self._mine_weekend_patterns(events, stats))

        # 按置信度降序排列
        patterns.sort(key=lambda p: p.confidence, reverse=True)

        return patterns

    def _mine_trigger_patterns(
        self,
        events: List[EventProfile],
        total: int,
        trigger_tags_map: Dict[str, List[str]],
    ) -> List[PatternRule]:
        """
        触发因素模式：P(event | trigger_tag=X) > overall_event_rate * 1.5
        """
        patterns: List[PatternRule] = []

        # 统计每个触发标签的出现次数和关联事件数
        tag_event_count: Dict[str, int] = Counter()      # 每个标签关联的事件数
        tag_total_appearances: Dict[str, int] = Counter() # 标签总出现次数

        for event in events:
            tags = trigger_tags_map.get(event.event_id, [])
            unique_tags = set(tags)
            for tag in unique_tags:
                tag_event_count[tag] += 1
            for tag in tags:
                tag_total_appearances[tag] += 1

        if not tag_event_count:
            return patterns

        overall_rate = total / max(total, 1)  # 实际就是 1.0，但保持公式通用性

        for tag, count in tag_event_count.items():
            frequency = count / total
            if frequency > overall_rate * 1.5 / total:  # 高于平均出现率的 1.5 倍
                # 实际含义：该触发因素出现频率高于平均水平
                # 置信度基于样本量
                confidence = min(1.0, count / 10.0)
                patterns.append(PatternRule(
                    condition=f"触发因素为「{tag}」时",
                    observation=f"「{tag}」是本周高频触发因素，共出现 {count} 次",
                    frequency=frequency,
                    confidence=confidence,
                ))

        return patterns

    def _mine_time_patterns(
        self,
        events: List[EventProfile],
        stats: WeeklySummaryStats,
    ) -> List[PatternRule]:
        """
        时段模式：P(event | time_of_day=X) > 平均时段事件数
        """
        patterns: List[PatternRule] = []
        tod_dist = stats.time_of_day_distribution
        if not tod_dist:
            return patterns

        total = stats.total_events
        num_periods = len(tod_dist) or 1
        avg_per_period = total / num_periods

        period_names_cn = {
            "早晨": "早晨（6:00-10:00）",
            "下午": "下午（11:00-17:00）",
            "晚间": "晚间（18:00-21:00）",
            "深夜": "深夜（22:00-5:00）",
        }

        for period, count in tod_dist.items():
            if count > avg_per_period * 1.3:  # 高于平均 30%
                frequency = count / total
                confidence = min(1.0, count / 10.0)
                period_cn = period_names_cn.get(period, period)
                patterns.append(PatternRule(
                    condition=f"在{period_cn}时段",
                    observation=f"{period_cn}是情绪事件高发时段，共 {count} 次事件",
                    frequency=frequency,
                    confidence=confidence,
                ))

        return patterns

    def _mine_coping_patterns(
        self,
        events: List[EventProfile],
        coping_ratings_map: Dict[str, Dict[str, int]],
    ) -> List[PatternRule]:
        """
        应对效果模式：某应对方式的效果评分显著高于/低于平均值
        """
        patterns: List[PatternRule] = []

        # 汇总每种应对方式的评分
        method_ratings: Dict[str, List[int]] = defaultdict(list)
        for event in events:
            ratings = coping_ratings_map.get(event.event_id, {})
            for method, rating in ratings.items():
                method_ratings[method].append(rating)

        if not method_ratings:
            return patterns

        # 计算全局平均评分
        all_ratings = [r for ratings in method_ratings.values() for r in ratings]
        if not all_ratings:
            return patterns
        global_mean = sum(all_ratings) / len(all_ratings)

        for method, ratings in method_ratings.items():
            method_mean = sum(ratings) / len(ratings)
            n = len(ratings)
            frequency = n / len(events)
            confidence = min(1.0, n / 10.0)

            if method_mean > global_mean * 1.2:
                patterns.append(PatternRule(
                    condition=f"使用应对方式「{method}」时",
                    observation=f"「{method}」效果显著优于平均，平均评分 {method_mean:.1f} 分",
                    frequency=frequency,
                    confidence=confidence,
                ))
            elif method_mean < global_mean * 0.8:
                patterns.append(PatternRule(
                    condition=f"使用应对方式「{method}」时",
                    observation=f"「{method}」效果低于平均水平，平均评分仅 {method_mean:.1f} 分",
                    frequency=frequency,
                    confidence=confidence,
                ))

        return patterns

    def _mine_recovery_patterns(
        self,
        events: List[EventProfile],
        coping_ratings_map: Dict[str, Dict[str, int]],
    ) -> List[PatternRule]:
        """
        恢复速度模式：使用某应对方式后恢复更快/更慢
        """
        patterns: List[PatternRule] = []

        # 按应对方式分组恢复时长
        method_recovery: Dict[str, List[float]] = defaultdict(list)
        for event in events:
            methods = list(coping_ratings_map.get(event.event_id, {}).keys())
            if not methods and event.recovery_duration > 0:
                # 无应对方式记录，归入"未使用应对策略"
                method_recovery["未使用应对策略"].append(event.recovery_duration)
            for method in methods:
                if event.recovery_duration > 0:
                    method_recovery[method].append(event.recovery_duration)

        if not method_recovery:
            return patterns

        # 计算全局平均恢复时长
        all_durations = [d for durations in method_recovery.values() for d in durations]
        if not all_durations:
            return patterns
        global_mean_recovery = sum(all_durations) / len(all_durations)

        for method, durations in method_recovery.items():
            method_mean = sum(durations) / len(durations)
            n = len(durations)
            frequency = n / len(events)
            confidence = min(1.0, n / 10.0)

            if method_mean < global_mean_recovery * 0.7:  # 快于平均 30%
                patterns.append(PatternRule(
                    condition=f"使用「{method}」后",
                    observation=f"使用「{method}」后恢复较快，平均 {_format_duration(method_mean)}",
                    frequency=frequency,
                    confidence=confidence,
                ))
            elif method_mean > global_mean_recovery * 1.3:  # 慢于平均 30%
                patterns.append(PatternRule(
                    condition=f"使用「{method}」后",
                    observation=f"使用「{method}」后恢复较慢，平均 {_format_duration(method_mean)}",
                    frequency=frequency,
                    confidence=confidence,
                ))

        return patterns

    def _mine_weekend_patterns(
        self,
        events: List[EventProfile],
        stats: WeeklySummaryStats,
    ) -> List[PatternRule]:
        """
        周末 vs 工作日差异：事件频率或强度差异超过 20%
        """
        patterns: List[PatternRule] = []

        weekday_count = 0
        weekday_arousal_sum = 0.0
        weekend_count = 0
        weekend_arousal_sum = 0.0

        for event in events:
            dt = datetime.fromtimestamp(event.onset_time)
            if dt.weekday() < 5:  # 周一到周五
                weekday_count += 1
                weekday_arousal_sum += event.peak_arousal
            else:  # 周六、周日
                weekend_count += 1
                weekend_arousal_sum += event.peak_arousal

        # 频率差异（按天均摊）
        if weekday_count > 0 and weekend_count > 0:
            avg_weekday = weekday_count / 5.0
            avg_weekend = weekend_count / 2.0

            if avg_weekday > 0:
                diff_pct = abs(avg_weekend - avg_weekday) / avg_weekday * 100
                if diff_pct > 20:
                    if avg_weekend > avg_weekday:
                        observation = (
                            f"周末日均情绪事件（{avg_weekend:.1f} 次）"
                            f"比工作日（{avg_weekday:.1f} 次）多 {diff_pct:.0f}%"
                        )
                    else:
                        observation = (
                            f"工作日日均情绪事件（{avg_weekday:.1f} 次）"
                            f"比周末（{avg_weekend:.1f} 次）多 {diff_pct:.0f}%"
                        )
                    frequency = max(weekday_count, weekend_count) / stats.total_events
                    confidence = min(1.0, max(weekday_count, weekend_count) / 10.0)
                    patterns.append(PatternRule(
                        condition="对比工作日与周末时",
                        observation=observation,
                        frequency=frequency,
                        confidence=confidence,
                    ))

            # 强度差异
            if weekday_count > 0 and weekend_count > 0:
                avg_arousal_wd = weekday_arousal_sum / weekday_count
                avg_arousal_we = weekend_arousal_sum / weekend_count
                if avg_arousal_wd > 0:
                    intensity_diff = abs(avg_arousal_we - avg_arousal_wd) / avg_arousal_wd * 100
                    if intensity_diff > 20:
                        if avg_arousal_we > avg_arousal_wd:
                            obs = (
                                f"周末平均情绪唤醒度（{avg_arousal_we:.2f}）"
                                f"高于工作日（{avg_arousal_wd:.2f}）{intensity_diff:.0f}%"
                            )
                        else:
                            obs = (
                                f"工作日平均情绪唤醒度（{avg_arousal_wd:.2f}）"
                                f"高于周末（{avg_arousal_we:.2f}）{intensity_diff:.0f}%"
                            )
                        frequency = max(weekday_count, weekend_count) / stats.total_events
                        confidence = min(1.0, max(weekday_count, weekend_count) / 10.0)
                        patterns.append(PatternRule(
                            condition="对比工作日与周末情绪强度时",
                            observation=obs,
                            frequency=frequency,
                            confidence=confidence,
                        ))

        return patterns


# ================================================================
# 三、NLGEngine — 自然语言生成
# ================================================================

class NLGEngine:
    """
    自然语言生成引擎：将统计指标和模式转化为可读的周报段落。
    使用模板+规则方式拼接中文自然语言文本。
    所有文本生成均在设备本地完成。
    """

    def generate_report(
        self,
        weekly_data: WeeklyData,
        stats: WeeklySummaryStats,
        patterns: List[PatternRule],
    ) -> WeeklyReport:
        """
        生成完整周报。
        """
        start_date, end_date = weekly_data.date_range

        report = WeeklyReport(
            title=f"心潮周报 · {start_date} 至 {end_date}",
            date_range=weekly_data.date_range,
        )

        # 依次生成各段落
        report.sections.append(self._generate_overview(stats))
        report.sections.append(self._generate_triggers(stats))
        report.sections.append(self._generate_coping_effectiveness(stats))
        report.sections.append(self._generate_pattern_discovery(patterns))
        report.sections.append(self._generate_baseline_changes(weekly_data))
        report.sections.append(self._generate_suggestions(stats, patterns, weekly_data))

        return report

    def _generate_overview(self, stats: WeeklySummaryStats) -> ReportSection:
        """生成「情绪概览」段落"""
        n_events = stats.total_events
        if n_events == 0:
            content = "本周没有记录到情绪事件，心情看起来很平静呢。"
            return ReportSection(
                heading="情绪概览",
                content=content,
            )

        lines = [f"本周共记录了 {n_events} 次情绪事件。"]

        # 平均唤醒度
        arousal = stats.avg_peak_arousal
        if arousal > 0.7:
            lines.append(f"平均情绪唤醒度较高（{arousal:.2f}），说明本周经历了较多强烈情绪。")
        elif arousal > 0.4:
            lines.append(f"平均情绪唤醒度为 {arousal:.2f}，整体情绪波动适中。")
        else:
            lines.append(f"平均情绪唤醒度较低（{arousal:.2f}），本周情绪相对平稳。")

        # 平均效价
        valence = stats.avg_peak_valence
        if valence < 0.3:
            lines.append(f"平均效价为 {valence:.2f}，本周负面情绪体验偏多。")
        elif valence < 0.5:
            lines.append(f"平均效价为 {valence:.2f}，情绪体验中性偏消极。")
        else:
            lines.append(f"平均效价为 {valence:.2f}，本周正面情绪体验较多。")

        # 恢复统计
        if stats.avg_recovery_duration > 0:
            lines.append(
                f"平均恢复时长为 {_format_duration(stats.avg_recovery_duration)}。"
            )

        # 基线漂移告警
        if stats.baseline_shift_count > 0:
            lines.append(
                f"本周检测到 {stats.baseline_shift_count} 次基线漂移告警，请关注。"
            )

        # 趋势对比
        if stats.trend_vs_last_week:
            for metric, desc in stats.trend_vs_last_week.items():
                lines.append(desc + "。")

        content = "\n".join(lines)

        highlights = {
            "总事件数": float(n_events),
            "平均唤醒度": stats.avg_peak_arousal,
            "平均效价": stats.avg_peak_valence,
            "平均恢复时长": stats.avg_recovery_duration,
        }
        if stats.avg_subjective_peak > 0:
            highlights["平均自评峰值"] = stats.avg_subjective_peak

        return ReportSection(
            heading="情绪概览",
            content=content,
            data_highlights=highlights,
        )

    def _generate_triggers(self, stats: WeeklySummaryStats) -> ReportSection:
        """生成「高频触发因素」段落"""
        if not stats.top_triggers:
            content = "本周暂无触发因素数据。建议在记录情绪事件时标注触发因素，以便更好地了解自己的情绪模式。"
            return ReportSection(
                heading="高频触发因素",
                content=content,
            )

        lines = ["本周最常见的触发因素包括："]
        highlights: Dict[str, float] = {}
        for i, (trigger, count) in enumerate(stats.top_triggers[:5], 1):
            lines.append(f"  {i}. {trigger}（{count} 次）")
            highlights[trigger] = float(count)

        content = "\n".join(lines)
        return ReportSection(
            heading="高频触发因素",
            content=content,
            data_highlights=highlights,
        )

    def _generate_coping_effectiveness(self, stats: WeeklySummaryStats) -> ReportSection:
        """生成「应对策略效果」段落"""
        if not stats.top_coping_methods:
            content = "本周暂无应对策略数据。建议在情绪恢复后评估应对方式的效果，帮助我们找到最适合你的策略。"
            return ReportSection(
                heading="应对策略效果",
                content=content,
            )

        lines = ["本周应对策略效果排名："]
        highlights: Dict[str, float] = {}
        for i, (method, rating) in enumerate(stats.top_coping_methods[:5], 1):
            # 评分 1-5，映射为星级描述
            if rating >= 4.5:
                star_desc = "非常有效"
            elif rating >= 3.5:
                star_desc = "比较有效"
            elif rating >= 2.5:
                star_desc = "一般"
            else:
                star_desc = "效果有限"
            lines.append(f"  {i}. {method}：{rating:.1f} 分（{star_desc}）")
            highlights[method] = rating

        content = "\n".join(lines)
        return ReportSection(
            heading="应对策略效果",
            content=content,
            data_highlights=highlights,
        )

    def _generate_pattern_discovery(self, patterns: List[PatternRule]) -> ReportSection:
        """生成「模式发现」段落"""
        if not patterns:
            content = "本周数据量较少，暂未发现显著的情绪模式。持续记录可以帮助我们发现更多有价值的规律。"
            return ReportSection(
                heading="模式发现",
                content=content,
            )

        lines = ["通过分析本周数据，我们发现以下模式："]
        highlights: Dict[str, float] = {}

        for i, pattern in enumerate(patterns[:8], 1):  # 最多展示 8 条
            confidence_desc = ""
            if pattern.confidence >= 0.8:
                confidence_desc = "（高度可信）"
            elif pattern.confidence >= 0.5:
                confidence_desc = "（中度可信）"
            else:
                confidence_desc = "（待进一步验证）"

            lines.append(f"  {i}. {pattern.observation}{confidence_desc}")
            # 使用条件作为 key 避免重复
            key = f"模式{i}_{pattern.condition}"
            highlights[key] = pattern.frequency

        content = "\n".join(lines)
        return ReportSection(
            heading="模式发现",
            content=content,
            data_highlights=highlights,
        )

    def _generate_baseline_changes(self, weekly_data: WeeklyData) -> ReportSection:
        """生成「基线变化」段落"""
        shifts = weekly_data.baseline_shifts
        if not shifts:
            content = "本周未检测到基线漂移，你的生理基线保持稳定，状态良好。"
            return ReportSection(
                heading="基线变化",
                content=content,
            )

        lines = [f"本周检测到 {len(shifts)} 次基线漂移告警："]
        highlights: Dict[str, float] = {}

        for i, shift in enumerate(shifts, 1):
            # 告警级别描述
            level_desc = {
                AlertLevel.INFO: "轻微偏离",
                AlertLevel.WARNING: "中度偏离",
                AlertLevel.ACTION: "严重偏离",
            }.get(shift.alert_level, "未知级别")

            dims = "、".join(shift.shifted_dimensions) if shift.shifted_dimensions else "未指定维度"
            line = f"  {i}. [{level_desc}] {shift.detected_date}：{dims}"
            if shift.message:
                line += f" — {shift.message}"
            lines.append(line)

            for dim, magnitude in shift.shift_magnitudes.items():
                highlights[f"{shift.detected_date}_{dim}"] = magnitude

        lines.append("")
        if any(s.alert_level == AlertLevel.ACTION for s in shifts):
            lines.append("存在严重基线漂移，建议关注近期的生活节奏和压力水平，必要时调整阈值设置。")
        elif any(s.alert_level == AlertLevel.WARNING for s in shifts):
            lines.append("部分基线指标出现中度偏离，建议保持关注，确保充足的休息。")
        else:
            lines.append("基线漂移程度较轻，属于正常波动范围。")

        content = "\n".join(lines)
        return ReportSection(
            heading="基线变化",
            content=content,
            data_highlights=highlights,
        )

    def _generate_suggestions(
        self,
        stats: WeeklySummaryStats,
        patterns: List[PatternRule],
        weekly_data: WeeklyData,
    ) -> ReportSection:
        """生成「下周建议」段落：基于发现的模式给出个性化建议"""
        suggestions: List[str] = []
        trigger_tags_map: Dict[str, List[str]] = {}  # 本方法内不直接使用
        highlights: Dict[str, float] = {}

        # ---- 策略1：工作日下午焦虑高峰 -> 会前深呼吸 ----
        afternoon_work_anxiety = any(
            "下午" in p.condition and ("焦虑" in p.observation or "高发" in p.observation)
            for p in patterns
        )
        if afternoon_work_anxiety:
            suggestions.append(
                "下午是情绪事件高发时段，建议在重要会议或任务前花 2-3 分钟做深呼吸练习，"
                "帮助提前稳定情绪状态。"
            )
            highlights["会前深呼吸建议"] = 1.0

        # ---- 策略2：睡眠与事件关联 -> 睡眠卫生 ----
        low_sleep_days: List[DailySummary] = [
            s for s in weekly_data.daily_summaries if s.sleep_score < 5.0
        ]
        high_event_days: List[DailySummary] = [
            s for s in weekly_data.daily_summaries if s.event_count >= 3
        ]
        low_sleep_dates = {s.date for s in low_sleep_days}
        high_event_dates = {s.date for s in high_event_days}
        if low_sleep_dates & high_event_dates:
            suggestions.append(
                "数据显示，睡眠质量较低的日子往往伴随更多情绪事件。"
                "建议本周注意睡眠卫生：保持规律作息、睡前避免屏幕蓝光、"
                "创造安静舒适的睡眠环境。"
            )
            highlights["睡眠卫生建议"] = 1.0

        # ---- 策略3：有效的应对方式 -> 鼓励继续使用 ----
        effective_methods = [
            p for p in patterns
            if "效果显著优于平均" in p.observation
        ]
        if effective_methods:
            method_names: List[str] = []
            for p in effective_methods:
                # 提取方法名
                import re
                match = re.search(r"「(.+?)」", p.observation)
                if match:
                    method_names.append(match.group(1))
            if method_names:
                methods_str = "、".join(method_names)
                suggestions.append(
                    f"「{methods_str}」在本周表现出了很好的情绪调节效果，"
                    f"建议继续在日常中使用这些方法。"
                )
                highlights["有效应对建议"] = 1.0

        # ---- 策略4：恢复较慢 -> 建议新策略 ----
        slow_recovery = any(
            "恢复较慢" in p.observation for p in patterns
        )
        if slow_recovery:
            suggestions.append(
                "部分情况下恢复时间较长，建议尝试加入新的应对策略，"
                "如渐进式肌肉放松、正念冥想或短暂散步。"
                "多种策略组合使用往往能取得更好的恢复效果。"
            )
            highlights["新策略建议"] = 1.0

        # ---- 策略5：周末差异明显 -> 建议周末活动安排 ----
        weekend_patterns = [
            p for p in patterns
            if "周末" in p.observation and "工作日" in p.observation
        ]
        for p in weekend_patterns:
            if "周末" in p.observation and "多" in p.observation:
                suggestions.append(
                    "周末的情绪事件频率较高，建议适当安排放松活动，"
                    "比如户外运动、与朋友聚会或培养兴趣爱好，"
                    "帮助平衡周末的情绪状态。"
                )
                highlights["周末活动建议"] = 1.0
                break

        # ---- 策略6：基线漂移 -> 生活节奏调整 ----
        if stats.baseline_shift_count > 0:
            action_shifts = [
                s for s in weekly_data.baseline_shifts
                if s.alert_level in (AlertLevel.WARNING, AlertLevel.ACTION)
            ]
            if action_shifts:
                suggestions.append(
                    "本周出现了基线漂移告警，这可能意味着身体正在承受持续性压力。"
                    "建议本周注意劳逸结合，适当减少高强度工作时段，"
                    "保证每天有足够的休息和放松时间。"
                )
                highlights["生活节奏建议"] = 1.0

        # ---- 默认建议（无具体模式时） ----
        if not suggestions:
            if stats.total_events == 0:
                suggestions.append(
                    "本周情绪状态平稳，继续保持良好的生活习惯。"
                    "持续记录情绪数据可以帮助我们更好地了解你的情绪模式。"
                )
            else:
                suggestions.append(
                    "本周情绪数据已记录。随着数据积累，我们将能提供更有针对性的个性化建议。"
                    "建议坚持每天记录，尤其是在情绪波动明显的时候。"
                )

        content = "\n".join(suggestions)
        return ReportSection(
            heading="下周建议",
            content=content,
            data_highlights=highlights,
        )


# ================================================================
# 四、便捷入口函数
# ================================================================

def generate_weekly_report(
    weekly_data: WeeklyData,
    last_week_data: Optional[WeeklyData] = None,
    trigger_tags_map: Optional[Dict[str, List[str]]] = None,
    coping_ratings_map: Optional[Dict[str, Dict[str, int]]] = None,
) -> WeeklyReport:
    """
    一键生成周报的便捷函数。

    参数：
        weekly_data: 本周聚合数据
        last_week_data: 上周聚合数据（可选，用于趋势对比）
        trigger_tags_map: {event_id: [触发标签]} 外部触发标签映射
        coping_ratings_map: {event_id: {方法: 评分}} 外部应对评分映射

    返回：
        WeeklyReport 周报结构化对象
    """
    # 步骤1：数据聚合
    summarizer = WeeklySummarizer()
    stats = summarizer.summarize(weekly_data, last_week_data)

    # 如果有外部传入的触发标签和应对评分，补充统计
    if trigger_tags_map and weekly_data.event_profiles:
        event_ids = {e.event_id for e in weekly_data.event_profiles}
        trigger_counter: Counter = Counter()
        for eid in event_ids:
            tags = trigger_tags_map.get(eid, [])
            for tag in set(tags):
                trigger_counter[tag] += 1
        stats.top_triggers = trigger_counter.most_common(5)

    if coping_ratings_map and weekly_data.event_profiles:
        method_ratings: Dict[str, List[int]] = defaultdict(list)
        for event in weekly_data.event_profiles:
            ratings = coping_ratings_map.get(event.event_id, {})
            for method, rating in ratings.items():
                method_ratings[method].append(rating)
        method_avg = {
            m: sum(rs) / len(rs) for m, rs in method_ratings.items() if rs
        }
        sorted_methods = sorted(method_avg.items(), key=lambda x: x[1], reverse=True)
        stats.top_coping_methods = sorted_methods[:5]

    # 步骤2：模式挖掘
    miner = PatternMiner()
    patterns = miner.mine_patterns(
        weekly_data, stats, trigger_tags_map, coping_ratings_map
    )

    # 步骤3：自然语言生成
    nlg = NLGEngine()
    report = nlg.generate_report(weekly_data, stats, patterns)

    return report
