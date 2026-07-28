"""
test_report.py — 心潮 EmoWave P4 周报生成器 · 仿真测试脚本

本脚本执行以下测试：
  a. 完整数据测试：输入一周模拟 JSON 数据，输出完整文本报告
  b. 空数据测试：验证空 WeeklyData 时报告的优雅处理
  c. 趋势对比测试：生成两周数据，验证趋势对比功能
  d. 模式挖掘验证：验证预期模式是否被正确发现
  e. 报告结构验证：验证报告恰好包含 6 个段落及正确标题
  f. 报告文本质量验证：验证各段落内容非空，关键短语出现

运行方式：cd /workspace/emowave-engine && python3 test_report.py
"""

import random
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from models import (
    EventProfile,
    DailySummary,
    BaselineShiftEvent,
    PhysiologicalPeak,
    AlertLevel,
)

from report_generator import (
    WeeklyData,
    WeeklySummaryStats,
    WeeklyReport,
    PatternRule,
    WeeklySummarizer,
    PatternMiner,
    NLGEngine,
    generate_weekly_report,
)


# ================================================================
# ANSI 颜色常量（与 demo.py / test_recommender.py 风格一致）
# ================================================================

C_RESET = "\033[0m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_CYAN = "\033[36m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"


# ================================================================
# 辅助函数
# ================================================================

def pass_msg(msg: str) -> str:
    """绿色 PASS 消息"""
    return f"{C_GREEN}PASS{C_RESET} — {msg}"


def fail_msg(msg: str) -> str:
    """红色 FAIL 消息"""
    return f"{C_RED}FAIL{C_RESET} — {msg}"


def section_header(msg: str) -> str:
    """青色加粗段落标题"""
    return f"{C_BOLD}{C_CYAN}━━ {msg} ━━{C_RESET}"


def bold(msg: str) -> str:
    """加粗文本"""
    return f"{C_BOLD}{msg}{C_RESET}"


def format_report_text(report: WeeklyReport) -> str:
    """将 WeeklyReport 格式化为可打印的纯文本"""
    lines: List[str] = []
    lines.append(f"{C_BOLD}{'=' * 50}{C_RESET}")
    lines.append(f"{C_BOLD}{report.title}{C_RESET}")
    lines.append(f"{C_BOLD}{'=' * 50}{C_RESET}")
    for sec in report.sections:
        lines.append("")
        lines.append(f"{C_CYAN}{C_BOLD}【{sec.heading}】{C_RESET}")
        lines.append(sec.content)
    lines.append("")
    lines.append(f"{C_BOLD}{'=' * 50}{C_RESET}")
    return "\n".join(lines)


# ================================================================
# 一、模拟数据生成器
# ================================================================

# 定义一周的基准日期（2025-06-16 周一）
_WEEK_BASE = datetime(2025, 6, 16)


def _make_event(
    event_id: str,
    date: datetime,
    hour: int,
    minute: int,
    peak_arousal: float,
    peak_valence: float,
    recovery_duration: float,
    recovery_speed: float,
) -> EventProfile:
    """
    构造单个 EventProfile 对象。
    时间戳基于 date + hour:minute，peak_time 推后 2 分钟，calm_time 推后 recovery_duration。
    """
    onset = date.replace(hour=hour, minute=minute, second=0).timestamp()
    peak = date.replace(hour=hour, minute=minute + 2, second=0).timestamp()
    calm = onset + recovery_duration
    return EventProfile(
        event_id=event_id,
        onset_time=onset,
        peak_time=peak,
        calm_time=calm,
        peak_valence=peak_valence,
        peak_arousal=peak_arousal,
        subjective_peak=round(peak_arousal * 10, 1),  # 模拟自评峰值 0-10
        physiological_peak_score=round(peak_arousal * 0.9, 2),
        physiological_peak_confidence=round(0.6 + random.random() * 0.3, 2),
        recovery_duration=recovery_duration,
        recovery_speed=recovery_speed,
        sample_count=random.randint(30, 120),
        physio_peaks=[
            PhysiologicalPeak(
                timestamp=peak,
                hr_zscore=round(random.uniform(1.0, 3.0), 2),
                hrv_drop_pct=round(random.uniform(10, 40), 1),
                arousal_spike=peak_arousal,
                composite_score=round(peak_arousal * 0.85, 2),
            )
        ],
    )


def generate_mock_weekly_data(
    start_date: datetime = _WEEK_BASE,
    num_events: int = 13,
) -> Tuple[WeeklyData, Dict[str, List[str]], Dict[str, Dict[str, int]]]:
    """
    生成一周的模拟 WeeklyData 及配套的 trigger_tags_map 和 coping_ratings_map。

    事件分布设计：
      - 工作日下午事件较多（模拟工作压力模式）
      - 周末早晨有部分事件（模拟周末焦虑）
      - 少量深夜事件
    """
    # 定义事件场景：(星期偏移, 时, 分, peak_arousal, peak_valence, recovery_duration, 模式标签)
    # 星期偏移 0=周一 ... 6=周日
    event_scenarios = [
        # 工作日下午事件（偏多，模拟工作压力）
        (0, 14, 30, 0.72, 0.22, 300, "工作日下午"),
        (0, 15, 10, 0.55, 0.30, 180, "工作日下午"),
        (1, 13, 45, 0.80, 0.15, 420, "工作日下午"),
        (2, 14, 00, 0.65, 0.25, 240, "工作日下午"),
        (2, 16, 30, 0.88, 0.12, 480, "工作日下午"),
        (3, 10, 15, 0.50, 0.35, 150, "工作日上午"),
        (3, 15, 45, 0.78, 0.18, 360, "工作日下午"),
        (4, 14, 20, 0.68, 0.28, 270, "工作日下午"),
        # 周末早晨事件（模拟周末焦虑）
        (5, 8, 30, 0.45, 0.38, 120, "周末早晨"),
        (5, 9, 15, 0.52, 0.32, 200, "周末早晨"),
        # 少量深夜事件
        (1, 23, 30, 0.60, 0.20, 350, "深夜"),
        (4, 23, 00, 0.70, 0.16, 450, "深夜"),
        # 补充事件
        (6, 10, 00, 0.42, 0.40, 100, "周末上午"),
    ]

    # 取前 num_events 个场景
    event_scenarios = event_scenarios[:num_events]

    events: List[EventProfile] = []
    trigger_tags_map: Dict[str, List[str]] = {}
    coping_ratings_map: Dict[str, Dict[str, int]] = {}

    # 预设的触发标签库
    trigger_pool = {
        "工作日下午": ["工作会议", "通勤压力", "截止日期压力"],
        "工作日上午": ["通勤压力", "工作任务", "社交冲突"],
        "周末早晨": ["睡眠不足", "过度思考", "社交焦虑"],
        "深夜": ["睡眠不足", "过度思考"],
        "周末上午": ["社交焦虑", "过度思考"],
    }

    # 预设的应对方式库
    # 设计要点：
    #   - "深呼吸" 和 "散步" 评分偏高（3-5）
    #   - "冷水洗脸" 评分偏低（1-3）
    #   - 使用 "散步" 的事件恢复较快
    coping_pool = {
        "工作日下午": [
            {"深呼吸": 4, "听音乐": 3, "短暂休息": 3},
            {"深呼吸": 5, "冷水洗脸": 2, "散步": 4},
            {"散步": 4, "听音乐": 3, "深呼吸": 5},
            {"深呼吸": 3, "冷水洗脸": 2, "短暂休息": 4},
        ],
        "工作日上午": [
            {"深呼吸": 4, "散步": 5},
            {"听音乐": 3, "冷水洗脸": 1},
        ],
        "周末早晨": [
            {"散步": 5, "深呼吸": 4},
            {"听音乐": 3, "正念冥想": 4},
        ],
        "深夜": [
            {"听音乐": 3, "深呼吸": 2, "正念冥想": 3},
            {"冷水洗脸": 2, "听音乐": 2},
        ],
        "周末上午": [
            {"散步": 4, "深呼吸": 4, "听音乐": 3},
        ],
    }

    for i, (day_offset, hour, minute, arousal, valence, recovery, pattern_label) in enumerate(event_scenarios):
        event_id = f"evt_{i+1:03d}"
        event_date = start_date + timedelta(days=day_offset)

        # 使用散步的事件恢复时长缩短（体现散步效果）
        adjusted_recovery = recovery
        coping_options = coping_pool.get(pattern_label, [])
        chosen_coping = coping_options[i % len(coping_options)] if coping_options else {}
        if "散步" in chosen_coping:
            adjusted_recovery = max(60, int(recovery * 0.6))  # 散步让恢复加快 40%

        recovery_speed = round(arousal / max(adjusted_recovery, 1), 4)

        event = _make_event(
            event_id=event_id,
            date=event_date,
            hour=hour,
            minute=minute,
            peak_arousal=arousal,
            peak_valence=valence,
            recovery_duration=adjusted_recovery,
            recovery_speed=recovery_speed,
        )
        events.append(event)

        # 触发标签：工作会议 出现频率最高
        tags = trigger_pool.get(pattern_label, ["未知触发"])
        # 让 "工作会议" 在更多工作日下午事件中出现
        if pattern_label == "工作日下午" and i < 6:
            if "工作会议" not in tags:
                tags.append("工作会议")
            # 额外增加工作会议的权重：部分事件重复添加
            if i % 2 == 0:
                tags.append("工作会议")
        trigger_tags_map[event_id] = tags

        # 应对评分
        coping_ratings_map[event_id] = chosen_coping

    # 生成 2-3 个 DailySummary
    daily_summaries: List[DailySummary] = [
        DailySummary(
            date=(start_date + timedelta(days=0)).strftime("%Y-%m-%d"),
            avg_resting_hrv=45.2,
            avg_resting_hr=75.0,
            sleep_score=4.2,  # 睡眠不佳
            morning_valence_avg=0.45,
            evening_valence_avg=0.40,
            event_count=2,
            peak_arousal_max=0.72,
        ),
        DailySummary(
            date=(start_date + timedelta(days=2)).strftime("%Y-%m-%d"),
            avg_resting_hrv=52.0,
            avg_resting_hr=70.0,
            sleep_score=7.5,  # 睡眠较好
            morning_valence_avg=0.55,
            evening_valence_avg=0.50,
            event_count=2,
            peak_arousal_max=0.88,
        ),
        DailySummary(
            date=(start_date + timedelta(days=5)).strftime("%Y-%m-%d"),
            avg_resting_hrv=48.0,
            avg_resting_hr=73.0,
            sleep_score=3.8,  # 睡眠差，周末事件多
            morning_valence_avg=0.42,
            evening_valence_avg=0.45,
            event_count=2,
            peak_arousal_max=0.52,
        ),
    ]

    # 生成 0-2 个 BaselineShiftEvent（一个 WARNING，一个 ACTION）
    baseline_shifts: List[BaselineShiftEvent] = [
        BaselineShiftEvent(
            alert_level=AlertLevel.WARNING,
            detected_date=(start_date + timedelta(days=2)).strftime("%Y-%m-%d"),
            shifted_dimensions=["HRV", "resting_hr"],
            shift_magnitudes={"HRV": 1.8, "resting_hr": 1.5},
            message="连续两天 HRV 下降超过 1.5 个标准差，静息心率轻度上升",
        ),
        BaselineShiftEvent(
            alert_level=AlertLevel.ACTION,
            detected_date=(start_date + timedelta(days=4)).strftime("%Y-%m-%d"),
            shifted_dimensions=["HRV", "sleep_score"],
            shift_magnitudes={"HRV": 2.5, "sleep_score": 2.0},
            message="HRV 持续下降超过 2 个标准差，睡眠质量显著恶化，建议立即关注",
        ),
    ]

    end_date = start_date + timedelta(days=6)
    weekly_data = WeeklyData(
        date_range=(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
        event_profiles=events,
        daily_summaries=daily_summaries,
        baseline_shifts=baseline_shifts,
    )

    return weekly_data, trigger_tags_map, coping_ratings_map


# ================================================================
# 二、精简版数据生成器（用于上周数据，事件数较少）
# ================================================================

def generate_lighter_weekly_data(
    start_date: datetime = _WEEK_BASE - timedelta(days=7),
    num_events: int = 7,
) -> WeeklyData:
    """
    生成事件数较少的一周数据（用于趋势对比的"上周"数据）。
    复用 generate_mock_weekly_data 的逻辑但返回更少事件。
    """
    weekly_data, _, _ = generate_mock_weekly_data(start_date=start_date, num_events=num_events)
    return weekly_data


# ================================================================
# 三、测试执行函数
# ================================================================

def test_full_data() -> bool:
    """
    测试场景 a：完整数据测试。
    生成一周完整数据，调用 generate_weekly_report()，打印完整报告。
    """
    print(section_header("测试 a：完整数据测试"))
    weekly_data, trigger_tags_map, coping_ratings_map = generate_mock_weekly_data()
    report = generate_weekly_report(
        weekly_data,
        trigger_tags_map=trigger_tags_map,
        coping_ratings_map=coping_ratings_map,
    )
    print(format_report_text(report))
    print(f"  事件总数: {len(weekly_data.event_profiles)}")
    print(f"  段落数量: {len(report.sections)}")
    print()
    return True


def test_empty_data() -> bool:
    """
    测试场景 b：空数据测试。
    生成空的 WeeklyData，验证报告不会崩溃。
    """
    print(section_header("测试 b：空数据测试"))
    empty_data = WeeklyData(
        date_range=("2025-06-16", "2025-06-22"),
        event_profiles=[],
        daily_summaries=[],
        baseline_shifts=[],
    )
    try:
        report = generate_weekly_report(empty_data)
        # 验证报告结构完整性
        if len(report.sections) == 6:
            print(pass_msg("空数据生成报告包含 6 个段落"))
        else:
            print(fail_msg(f"空数据生成报告段落数为 {len(report.sections)}，期望 6"))
            return False
        # 打印简化版报告
        for sec in report.sections:
            print(f"  {C_CYAN}{sec.heading}{C_RESET}: {sec.content[:80]}...")
        print()
        return True
    except Exception as e:
        print(fail_msg(f"空数据测试异常: {e}"))
        return False


def test_trend_comparison() -> bool:
    """
    测试场景 c：趋势对比测试。
    生成两周数据（上周较少、本周较多），验证趋势对比功能。
    """
    print(section_header("测试 c：趋势对比测试"))

    last_week = generate_lighter_weekly_data(num_events=7)
    this_week, trigger_tags_map, coping_ratings_map = generate_mock_weekly_data(num_events=13)

    try:
        report = generate_weekly_report(
            this_week,
            last_week_data=last_week,
            trigger_tags_map=trigger_tags_map,
            coping_ratings_map=coping_ratings_map,
        )
        # 验证概览段落中包含趋势描述
        overview = report.sections[0]
        trend_keywords = ["上升", "下降", "稳定"]
        found_trend = any(kw in overview.content for kw in trend_keywords)
        if found_trend:
            print(pass_msg("趋势对比信息已出现在概览段落中"))
        else:
            print(fail_msg("概览段落中未发现趋势对比关键词"))
            print(f"  概览内容: {overview.content[:200]}")
            return False

        # 打印概览段落供人工查看
        print(f"  {C_DIM}概览段落摘录:{C_RESET}")
        for line in overview.content.split("\n"):
            if any(kw in line for kw in trend_keywords):
                print(f"    {C_GREEN}{line}{C_RESET}")
        print()
        return True
    except Exception as e:
        print(fail_msg(f"趋势对比测试异常: {e}"))
        return False


def test_pattern_mining() -> bool:
    """
    测试场景 d：模式挖掘验证。
    验证预期模式是否被正确发现。
    """
    print(section_header("测试 d：模式挖掘验证"))

    weekly_data, trigger_tags_map, coping_ratings_map = generate_mock_weekly_data()

    # 先计算统计
    summarizer = WeeklySummarizer()
    stats = summarizer.summarize(weekly_data)

    # 补充触发因素和应对方式统计（模拟 generate_weekly_report 中的逻辑）
    from collections import Counter
    from collections import defaultdict as _defaultdict
    event_ids = {e.event_id for e in weekly_data.event_profiles}
    trigger_counter: Counter = Counter()
    for eid in event_ids:
        tags = trigger_tags_map.get(eid, [])
        for tag in set(tags):
            trigger_counter[tag] += 1
    stats.top_triggers = trigger_counter.most_common(5)

    method_ratings: Dict[str, List[int]] = _defaultdict(list)
    for event in weekly_data.event_profiles:
        ratings = coping_ratings_map.get(event.event_id, {})
        for method, rating in ratings.items():
            method_ratings[method].append(rating)
    method_avg = {
        m: sum(rs) / len(rs) for m, rs in method_ratings.items() if rs
    }
    sorted_methods = sorted(method_avg.items(), key=lambda x: x[1], reverse=True)
    stats.top_coping_methods = sorted_methods[:5]

    # 模式挖掘
    miner = PatternMiner()
    patterns = miner.mine_patterns(
        weekly_data, stats, trigger_tags_map, coping_ratings_map
    )

    all_pass = True

    # 验证 1："工作会议" 应该作为高频触发因素
    trigger_pattern_found = any("工作会议" in p.observation for p in patterns)
    if trigger_pattern_found:
        print(pass_msg("「工作会议」被识别为高频触发因素"))
    else:
        print(fail_msg("「工作会议」未被识别为高频触发因素"))
        all_pass = False

    # 验证 2：至少一个应对效果模式被发现
    coping_pattern_found = any(
        "效果显著优于平均" in p.observation or "效果低于平均水平" in p.observation
        for p in patterns
    )
    if coping_pattern_found:
        print(pass_msg("至少发现一个应对效果模式"))
    else:
        print(fail_msg("未发现任何应对效果模式"))
        all_pass = False

    # 验证 3：打印所有发现的模式供参考
    print(f"  {C_DIM}共发现 {len(patterns)} 条模式:{C_RESET}")
    for i, p in enumerate(patterns, 1):
        print(f"    {i}. [{p.confidence:.2f}] {p.observation}")

    print()
    return all_pass


def test_report_structure() -> bool:
    """
    测试场景 e：报告结构验证。
    验证生成的报告恰好包含 6 个段落，且标题正确。
    """
    print(section_header("测试 e：报告结构验证"))

    expected_headings = [
        "情绪概览",
        "高频触发因素",
        "应对策略效果",
        "模式发现",
        "基线变化",
        "下周建议",
    ]

    weekly_data, trigger_tags_map, coping_ratings_map = generate_mock_weekly_data()
    report = generate_weekly_report(
        weekly_data,
        trigger_tags_map=trigger_tags_map,
        coping_ratings_map=coping_ratings_map,
    )

    all_pass = True

    # 验证段落数量
    if len(report.sections) != 6:
        print(fail_msg(f"段落数量为 {len(report.sections)}，期望 6"))
        all_pass = False
    else:
        print(pass_msg("报告恰好包含 6 个段落"))

    # 验证每个段落标题
    for i, expected in enumerate(expected_headings):
        if i < len(report.sections):
            actual = report.sections[i].heading
            if actual == expected:
                print(pass_msg(f"段落 {i+1} 标题正确: 「{expected}」"))
            else:
                print(fail_msg(f"段落 {i+1} 标题为「{actual}」，期望「{expected}」"))
                all_pass = False
        else:
            print(fail_msg(f"缺少段落 {i+1}: 「{expected}」"))
            all_pass = False

    print()
    return all_pass


def test_report_text_quality() -> bool:
    """
    测试场景 f：报告文本质量验证。
    验证完整数据时各段落内容非空，且关键短语出现。
    """
    print(section_header("测试 f：报告文本质量验证"))

    weekly_data, trigger_tags_map, coping_ratings_map = generate_mock_weekly_data()
    report = generate_weekly_report(
        weekly_data,
        trigger_tags_map=trigger_tags_map,
        coping_ratings_map=coping_ratings_map,
    )

    all_pass = True

    # 验证各段落内容非空（对完整数据而言）
    for sec in report.sections:
        content_stripped = sec.content.strip()
        if content_stripped:
            print(pass_msg(f"「{sec.heading}」内容非空（{len(content_stripped)} 字）"))
        else:
            print(fail_msg(f"「{sec.heading}」内容为空"))
            all_pass = False

    # 验证关键短语出现
    overview = report.sections[0].content
    key_phrases = [
        ("情绪事件", "概览段落应提及情绪事件"),
        ("唤醒度", "概览段落应提及唤醒度"),
        ("效价", "概览段落应提及效价"),
    ]
    print()
    for phrase, desc in key_phrases:
        if phrase in overview:
            print(pass_msg(f"关键短语「{phrase}」出现在概览段落中"))
        else:
            print(fail_msg(f"关键短语「{phrase}」未出现（{desc}）"))
            all_pass = False

    # 验证触发因素段落包含具体标签
    trigger_section = report.sections[1].content
    if "工作会议" in trigger_section:
        print(pass_msg("「工作会议」出现在触发因素段落中"))
    else:
        print(fail_msg("「工作会议」未出现在触发因素段落中"))
        all_pass = False

    # 验证模式发现段落包含内容
    pattern_section = report.sections[3].content
    if "模式" in pattern_section:
        print(pass_msg("模式发现段落包含模式描述"))
    else:
        print(fail_msg("模式发现段落缺少模式描述"))
        all_pass = False

    # 验证基线变化段落包含告警信息
    baseline_section = report.sections[4].content
    if "基线漂移" in baseline_section or "偏离" in baseline_section:
        print(pass_msg("基线变化段落包含漂移/偏离信息"))
    else:
        print(fail_msg("基线变化段落缺少漂移/偏离信息"))
        all_pass = False

    # 验证下周建议段落包含建议内容
    suggestion_section = report.sections[5].content
    if len(suggestion_section.strip()) > 10:
        print(pass_msg("下周建议段落包含实质性建议"))
    else:
        print(fail_msg("下周建议段落内容过短"))
        all_pass = False

    print()
    return all_pass


# ================================================================
# 四、主函数
# ================================================================

def main():
    """运行所有测试场景并汇总结果"""

    print()
    print(f"{C_BOLD}心潮 EmoWave — P4 周报生成器 · 仿真测试{C_RESET}")
    print(f"{C_DIM}测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{C_RESET}")
    print()

    results: List[Tuple[str, bool]] = []

    # 依次执行各测试场景
    results.append(("a. 完整数据测试", test_full_data()))
    results.append(("b. 空数据测试", test_empty_data()))
    results.append(("c. 趋势对比测试", test_trend_comparison()))
    results.append(("d. 模式挖掘验证", test_pattern_mining()))
    results.append(("e. 报告结构验证", test_report_structure()))
    results.append(("f. 报告文本质量验证", test_report_text_quality()))

    # 汇总结果
    print(section_header("测试结果汇总"))
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed

    for name, ok in results:
        if ok:
            print(f"  {pass_msg(name)}")
        else:
            print(f"  {fail_msg(name)}")

    print()
    print(f"  {C_BOLD}总计: {total} 个测试, "
          f"{C_GREEN}{passed} 通过{C_RESET}, "
          f"{C_RED}{failed} 未通过{C_RESET}")
    print()

    if failed == 0:
        print(f"  {C_BOLD}{C_GREEN}所有测试通过！{C_RESET}")
    else:
        print(f"  {C_BOLD}{C_RED}有 {failed} 个测试未通过，请检查上方详细输出。{C_RESET}")

    print()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
