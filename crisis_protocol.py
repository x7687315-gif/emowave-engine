#!/usr/bin/env python3
"""
crisis_protocol.py — 心潮 EmoWave 危机协议模块

本模块在检测到特定危险模式时触发柔性关怀响应：
  1. 连续3天以上情绪强度持续极高
  2. 事件诱因中出现自伤/自杀相关关键词
  3. 触发后：柔性提示、心理热线、EMA追问、本地日志记录

运行方式：
  cd /workspace/emowave-engine && python3 crisis_protocol.py
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import os
import json
import re
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum


# ================================================================
# 危机关键词库
# ================================================================

CRISIS_KEYWORDS = {
    "self_harm": [
        "自伤", "自残", "割", "割腕", "割手", "划手", "想伤害自己",
        "不想活", "想死", "自杀", "了结", "结束自己", "活不下去",
        "没有意义", "不如死了", "想消失", "活着好累", "太累了想放弃",
        "想跳", "想从楼上", "吞药", "吃安眠", "不想醒来",
        "hurt myself", "self-harm", "suicide", "want to die",
        "end it", "kill myself", "cut myself", "don't want to live",
    ],
    "severe_distress": [
        "崩溃", "受不了", "撑不住了", "撑不下去", "快要疯了",
        "无法呼吸", "窒息", "绝望", "世界崩塌", "完全失控",
        "panic", "breakdown", "can't breathe", "falling apart",
    ],
}

# 短关键词需在单词边界匹配（避免"割草"之类误报）
SHORT_KEYWORDS_REQUIRING_EXACT = {"割", "跳", "药"}


# ================================================================
# 全国心理援助热线
# ================================================================

CRISIS_HOTLINES = [
    {"name": "全国24小时心理援助热线", "number": "400-161-9995", "available": "24小时"},
    {"name": "北京心理危机研究与干预中心", "number": "010-82951332", "available": "24小时"},
    {"name": "希望24热线", "number": "400-161-9995", "available": "24小时"},
    {"name": "生命热线", "number": "400-821-1215", "available": "每天 8:00-22:00"},
    {"name": "青少年心理咨询热线", "number": "12355", "available": "每天 9:00-21:00"},
]


# ================================================================
# 数据结构
# ================================================================

class CrisisType(Enum):
    PERSISTENT_HIGH_INTENSITY = "persistent_high_intensity"
    SELF_HARM_KEYWORD = "self_harm_keyword"
    SEVERE_DISTRESS_KEYWORD = "severe_distress_keyword"


class CrisisSeverity(Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CrisisEvent:
    """一次危机事件记录。"""
    event_id: str
    detected_at: str                # ISO timestamp
    crisis_type: str               # CrisisType value
    severity: str                  # CrisisSeverity value
    trigger_source: str            # 触发来源描述
    details: Dict[str, Any] = field(default_factory=dict)
    response_actions: List[str] = field(default_factory=list)
    hotline_provided: bool = False
    ema_question_added: bool = False


@dataclass
class CrisisCheckResult:
    """危机检测结果。"""
    crisis_detected: bool = False
    crisis_type: Optional[str] = None
    severity: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    should_show_hotline: bool = False
    should_add_ema_question: bool = False
    in_app_message: Optional[str] = None
    ema_follow_up_question: Optional[str] = None


# ================================================================
# 柔性关怀文案
# ================================================================

CARE_MESSAGES = {
    "low": (
        "心潮注意到你最近的情绪波动比较大。\n"
        "这没什么不好的——愿意感受情绪，本身就是一种勇气。\n"
        "如果你愿意，可以试试今天特别推荐的放松练习。"
    ),
    "moderate": (
        "嗨，这几天似乎有些辛苦。\n"
        "心潮想温柔地提醒你：照顾好自己，是你可以为自己做的最重要的事。\n"
        "如果你觉得需要一些外界的支持，我们准备了一些专业的援助资源，随时可以查看。"
    ),
    "high": (
        "心潮注意到，你的情绪已经持续了一段时间的高强度状态。\n"
        "想让你知道：你现在感受到的一切都是真实的，你的感受值得被认真对待。\n"
        "有时候，和一位专业的人聊一聊，能让我们感到不那么孤单。\n"
        "下面是一些可以随时拨打的心理援助热线——24小时都有人在等你。"
    ),
    "critical": (
        "嗨，我注意到你最近可能在经历一些非常艰难的时刻。\n"
        "我不知道你具体在经历什么，但我想告诉你：你不是一个人。\n\n"
        "无论什么时候，都有人在愿意倾听你、帮助你。\n"
        "这些电话可以随时拨打，无论白天还是深夜：\n"
        "  全国心理援助热线: 400-161-9995 (24小时)\n"
        "  北京心理危机干预: 010-82951332 (24小时)\n"
        "  希望24热线: 400-161-9995 (24小时)\n\n"
        "你的存在本身就有意义。"
    ),
}

EMA_FOLLOW_UP_QUESTIONS = {
    "low": None,
    "moderate": "最近有什么特别让你感到压力的事情吗？（可选填）",
    "high": "最近是否感觉特别难以承受？如果愿意，可以简单说说你的感受。",
    "critical": "你最近是否有过伤害自己的想法？即使只是闪过的一个念头，也请告诉我们。我们会为你提供支持。",
}


# ================================================================
# CrisisProtocol — 危机协议
# ================================================================

class CrisisProtocol:
    """
    危机协议模块。

    在以下情况触发柔性关怀响应：
      1. 连续3天以上情绪强度持续处于极高区域（强度 > 0.85）
      2. 事件诱因文本中出现自伤/自杀相关关键词

    触发后行为：
      - 在 App 内弹出柔性关怀提示
      - 提供全国心理援助热线号码
      - 在当天的 EMA 问卷中增加温和追问
      - 记录在本地日志中（不上传）
    """

    def __init__(self, data_dir: str = "./crisis_logs", user_id: str = ""):
        self.user_id = user_id
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._crisis_events: List[CrisisEvent] = []
        self._intensity_history: List[Dict] = []
        self._consecutive_high_days: int = 0
        self._last_check_date: Optional[str] = None
        self._cooldown: Dict[str, str] = {}  # crisis_type → last_trigger_date
        self._min_cooldown_hours: int = 12
        self._load_history()

    # ============================================================
    # 持续高强度检测
    # ============================================================

    def check_intensity_pattern(
        self,
        daily_summaries: List[Dict],
        high_intensity_threshold: float = 0.85,
        consecutive_days_required: int = 3,
    ) -> CrisisCheckResult:
        """
        检测持续高强度模式。

        参数：
          daily_summaries: 每日摘要列表，每个包含 date, peak_arousal_max 等
          high_intensity_threshold: 高强度阈值（0-1）
          consecutive_days_required: 连续天数要求
        """
        if not daily_summaries:
            return CrisisCheckResult()

        # 更新强度历史
        for ds in daily_summaries:
            date = ds.get("date", "unknown")
            # 避免重复添加
            existing = [h for h in self._intensity_history if h["date"] == date]
            if not existing:
                self._intensity_history.append({
                    "date": date,
                    "peak_intensity": ds.get("peak_arousal_max", ds.get("peak_intensity", 0)),
                    "avg_intensity": ds.get("avg_arousal", ds.get("avg_intensity", 0)),
                })

        # 排序并取最近的 N 天
        sorted_history = sorted(
            self._intensity_history, key=lambda x: x["date"], reverse=True
        )

        # 检查连续高强度天数（从最近一天往前数）
        consecutive = 0
        for day_data in sorted_history:
            peak = day_data["peak_intensity"]
            avg = day_data["avg_intensity"]
            # 判定条件：峰值或均值超过阈值
            if peak >= high_intensity_threshold or avg >= (high_intensity_threshold * 0.85):
                consecutive += 1
            else:
                break

        if consecutive >= consecutive_days_required:
            self._consecutive_high_days = consecutive

            # 根据连续天数和强度水平判定严重级别
            avg_peak = float(np.mean([
                d["peak_intensity"] for d in sorted_history[:consecutive]
            ])) if consecutive > 0 else 0

            if avg_peak >= 0.95 and consecutive >= 5:
                severity = CrisisSeverity.CRITICAL
            elif avg_peak >= 0.90 or consecutive >= 4:
                severity = CrisisSeverity.HIGH
            elif consecutive >= 3:
                severity = CrisisSeverity.MODERATE
            else:
                severity = CrisisSeverity.LOW

            # 冷却检查
            if self.is_in_cooldown(CrisisType.PERSISTENT_HIGH_INTENSITY.value):
                return CrisisCheckResult(
                    crisis_detected=True,
                    crisis_type=CrisisType.PERSISTENT_HIGH_INTENSITY.value,
                    severity=severity.value,
                    details={
                        "consecutive_days": consecutive,
                        "avg_peak_intensity": round(avg_peak, 3),
                        "threshold": high_intensity_threshold,
                    },
                    should_show_hotline=severity in (CrisisSeverity.HIGH, CrisisSeverity.CRITICAL),
                    should_add_ema_question=severity.value != "low",
                    in_app_message=CARE_MESSAGES[severity.value],
                    ema_follow_up_question=EMA_FOLLOW_UP_QUESTIONS[severity.value],
                )

            return CrisisCheckResult(
                crisis_detected=True,
                crisis_type=CrisisType.PERSISTENT_HIGH_INTENSITY.value,
                severity=severity.value,
                details={
                    "consecutive_days": consecutive,
                    "avg_peak_intensity": round(avg_peak, 3),
                    "threshold": high_intensity_threshold,
                },
                should_show_hotline=severity in (CrisisSeverity.HIGH, CrisisSeverity.CRITICAL),
                should_add_ema_question=severity.value != "low",
                in_app_message=CARE_MESSAGES[severity.value],
                ema_follow_up_question=EMA_FOLLOW_UP_QUESTIONS[severity.value],
            )

        return CrisisCheckResult()

    # ============================================================
    # 关键词检测
    # ============================================================

    def check_trigger_keywords(
        self,
        trigger_text: str,
        body_symptoms: Optional[List[str]] = None,
        coping_methods: Optional[List[str]] = None,
    ) -> CrisisCheckResult:
        """
        检测诱因文本中的危机关键词。

        参数：
          trigger_text: 事件诱因文本
          body_symptoms: 躯体症状列表（可选）
          coping_methods: 应对方式列表（可选）
        """
        if not trigger_text:
            return CrisisCheckResult()

        text_lower = trigger_text.lower()
        matched_keywords = []
        matched_categories = []

        for category, keywords in CRISIS_KEYWORDS.items():
            for kw in keywords:
                kw_lower = kw.lower()

                # 短关键词需要更严格的匹配
                if kw in SHORT_KEYWORDS_REQUIRING_EXACT:
                    # 使用正则进行上下文匹配，避免"割草"等误报
                    if len(kw) <= 1:
                        continue
                    # 对于中文短词，检查是否包含自伤相关的组合
                    if kw_lower in text_lower:
                        # 检查上下文是否包含其他危险信号
                        context_indicators = ["自己", "自", "想", "了结", "死", "活不"]
                        has_context = any(ci in text_lower for ci in context_indicators)
                        if has_context or len(kw) >= 2:
                            matched_keywords.append(kw)
                            matched_categories.append(category)
                else:
                    if kw_lower in text_lower:
                        matched_keywords.append(kw)
                        matched_categories.append(category)

        # 去重
        matched_categories = list(set(matched_categories))

        if not matched_categories:
            return CrisisCheckResult()

        # 确定严重级别
        if "self_harm" in matched_categories:
            crisis_type = CrisisType.SELF_HARM_KEYWORD
            severity = CrisisSeverity.CRITICAL
        elif "severe_distress" in matched_categories:
            crisis_type = CrisisType.SEVERE_DISTRESS_KEYWORD
            severity = CrisisSeverity.HIGH
        else:
            crisis_type = CrisisType.SEVERE_DISTRESS_KEYWORD
            severity = CrisisSeverity.MODERATE

        # 冷却检查
        if self.is_in_cooldown(crisis_type.value):
            return CrisisCheckResult(
                crisis_detected=True,
                crisis_type=crisis_type.value,
                severity=severity.value,
                details={
                    "matched_keywords": matched_keywords,
                    "matched_categories": matched_categories,
                    "source_text": trigger_text[:50],  # 截断保存
                },
                should_show_hotline=True,
                should_add_ema_question=True,
                in_app_message=CARE_MESSAGES[severity.value],
                ema_follow_up_question=EMA_FOLLOW_UP_QUESTIONS[severity.value],
            )

        return CrisisCheckResult(
            crisis_detected=True,
            crisis_type=crisis_type.value,
            severity=severity.value,
            details={
                "matched_keywords": matched_keywords,
                "matched_categories": matched_categories,
                "source_text": trigger_text[:50],
            },
            should_show_hotline=True,
            should_add_ema_question=True,
            in_app_message=CARE_MESSAGES[severity.value],
            ema_follow_up_question=EMA_FOLLOW_UP_QUESTIONS[severity.value],
        )

    # ============================================================
    # 每日综合检测
    # ============================================================

    def check_daily(
        self,
        daily_summary: Dict,
        events_today: Optional[List[Dict]] = None,
        current_date: Optional[str] = None,
    ) -> List[CrisisCheckResult]:
        """
        每日综合危机检测。

        检测：
          1. 强度模式（使用累积的历史数据）
          2. 今日事件的关键词检测
        """
        results = []

        if current_date:
            self._last_check_date = current_date

        # 1. 强度模式检测
        intensity_result = self.check_intensity_pattern([daily_summary])
        if intensity_result.crisis_detected:
            results.append(intensity_result)

        # 2. 关键词检测（检查今日所有事件）
        if events_today:
            for evt in events_today:
                trigger = evt.get("trigger", evt.get("trigger_text", ""))
                symptoms = evt.get("body_symptoms", [])
                coping = evt.get("coping_methods", [])
                keyword_result = self.check_trigger_keywords(
                    trigger_text=str(trigger),
                    body_symptoms=symptoms,
                    coping_methods=coping,
                )
                if keyword_result.crisis_detected:
                    results.append(keyword_result)

        return results

    # ============================================================
    # 响应与消息
    # ============================================================

    def get_care_message(self, severity: str) -> str:
        """获取对应级别的柔性关怀消息。"""
        return CARE_MESSAGES.get(severity, CARE_MESSAGES["low"])

    def get_hotlines(self) -> List[Dict]:
        """获取心理援助热线列表。"""
        return list(CRISIS_HOTLINES)

    def get_ema_follow_up(self, severity: str) -> Optional[str]:
        """获取 EMA 追问问题（根据严重级别）。"""
        return EMA_FOLLOW_UP_QUESTIONS.get(severity)

    def trigger_crisis_response(self, result: CrisisCheckResult) -> CrisisEvent:
        """
        触发危机响应，记录事件。

        行动：
          1. 记录本地日志
          2. 设置冷却期
          3. 返回完整响应信息
        """
        now = datetime.now()
        event_id = f"crisis_{now.strftime('%Y%m%d%H%M%S')}_{now.microsecond}"

        # 确定响应行动
        actions = ["记录危机事件"]

        if result.should_show_hotline:
            actions.append("提供心理援助热线")
        if result.should_add_ema_question:
            actions.append("添加EMA追问")

        event = CrisisEvent(
            event_id=event_id,
            detected_at=now.isoformat(),
            crisis_type=result.crisis_type or "unknown",
            severity=result.severity or "unknown",
            trigger_source=result.details.get("source_text", "intensity_pattern"),
            details=result.details,
            response_actions=actions,
            hotline_provided=result.should_show_hotline,
            ema_question_added=result.should_add_ema_question,
        )

        # 设置冷却期
        if result.crisis_type:
            self._cooldown[result.crisis_type] = now.isoformat()

        self._crisis_events.append(event)
        self._log_crisis_event(event)

        return event

    # ============================================================
    # 历史与状态
    # ============================================================

    def get_crisis_history(self) -> List[CrisisEvent]:
        """获取所有危机事件记录。"""
        return list(self._crisis_events)

    def is_in_cooldown(self, crisis_type: str) -> bool:
        """检查某类危机是否在冷却期内。"""
        if crisis_type not in self._cooldown:
            return False
        last_time = datetime.fromisoformat(self._cooldown[crisis_type])
        elapsed = (datetime.now() - last_time).total_seconds() / 3600
        return elapsed < self._min_cooldown_hours

    def get_status_summary(self) -> Dict[str, Any]:
        """返回危机模块状态摘要。"""
        return {
            "user_id": self.user_id,
            "total_crisis_events": len(self._crisis_events),
            "intensity_history_days": len(self._intensity_history),
            "consecutive_high_days": self._consecutive_high_days,
            "active_cooldowns": {
                k: v for k, v in self._cooldown.items()
                if self.is_in_cooldown(k)
            },
            "recent_events": [
                {
                    "event_id": e.event_id,
                    "type": e.crisis_type,
                    "severity": e.severity,
                    "detected_at": e.detected_at,
                }
                for e in self._crisis_events[-5:]
            ],
        }

    def reset(self):
        """重置所有状态（用于测试）。"""
        self._crisis_events.clear()
        self._intensity_history.clear()
        self._consecutive_high_days = 0
        self._last_check_date = None
        self._cooldown.clear()

    # ============================================================
    # 持久化
    # ============================================================

    def _log_crisis_event(self, event: CrisisEvent):
        """将危机事件记录到本地文件。"""
        filepath = os.path.join(self.data_dir, "crisis_log.jsonl")
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[CrisisProtocol] 写入日志失败: {e}")

    def _load_history(self):
        """加载历史危机事件。"""
        filepath = os.path.join(self.data_dir, "crisis_log.jsonl")
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    event = CrisisEvent(**data)
                    self._crisis_events.append(event)
        except Exception as e:
            print(f"[CrisisProtocol] 加载历史失败: {e}")


# ================================================================
# 演示入口
# ================================================================

def main():
    """运行危机协议演示。"""
    print("=" * 60)
    print("  心潮 EmoWave — 危机协议模块演示")
    print("=" * 60)

    protocol = CrisisProtocol(
        data_dir="/workspace/emowave-engine/test_data/crisis_logs",
        user_id="demo_user"
    )
    protocol.reset()  # 演示前清空状态

    # 场景1: 关键词检测 — 自伤相关
    print("\n--- 场景1: 检测自伤相关关键词 ---")
    result1 = protocol.check_trigger_keywords(
        trigger_text="工作压力大，不想活了，活着好累",
        body_symptoms=["胸闷", "失眠"]
    )
    print(f"  危机检测: {result1.crisis_detected}")
    print(f"  类型: {result1.crisis_type}")
    print(f"  严重级别: {result1.severity}")
    print(f"  匹配关键词: {result1.details.get('matched_keywords', [])}")
    if result1.in_app_message:
        print(f"  App消息:\n    {result1.in_app_message.replace(chr(10), chr(10)+'    ')}")
    if result1.ema_follow_up_question:
        print(f"  EMA追问: {result1.ema_follow_up_question}")
    print(f"  提供热线: {result1.should_show_hotline}")

    # 触发响应
    event1 = protocol.trigger_crisis_response(result1)
    print(f"  [已触发] 危机事件ID: {event1.event_id}")

    # 场景2: 严重困扰关键词
    print("\n--- 场景2: 检测严重困扰关键词 ---")
    result2 = protocol.check_trigger_keywords(
        trigger_text="感觉快要崩溃了，完全失控，撑不住了"
    )
    print(f"  危机检测: {result2.crisis_detected}")
    print(f"  类型: {result2.crisis_type}")
    print(f"  严重级别: {result2.severity}")
    print(f"  匹配关键词: {result2.details.get('matched_keywords', [])}")

    # 场景3: 连续高强度
    print("\n--- 场景3: 连续3天高强度模式 ---")
    daily_summaries = [
        {"date": "2026-07-01", "peak_arousal_max": 0.88, "avg_arousal": 0.72},
        {"date": "2026-07-02", "peak_arousal_max": 0.92, "avg_arousal": 0.78},
        {"date": "2026-07-03", "peak_arousal_max": 0.90, "avg_arousal": 0.75},
    ]
    result3 = protocol.check_intensity_pattern(daily_summaries)
    print(f"  危机检测: {result3.crisis_detected}")
    print(f"  连续天数: {result3.details.get('consecutive_days')}")
    print(f"  平均峰值: {result3.details.get('avg_peak_intensity')}")
    if result3.in_app_message:
        msg_preview = result3.in_app_message[:60] + "..." if len(result3.in_app_message) > 60 else result3.in_app_message
        print(f"  App消息预览: {msg_preview}")

    # 场景4: 冷却期测试
    print("\n--- 场景4: 冷却期测试 ---")
    result4 = protocol.check_trigger_keywords(
        trigger_text="又想伤害自己了"
    )
    print(f"  同类型二次触发（在冷却期内）: {result4.crisis_detected}")

    # 场景5: 误报测试（正常文本）
    print("\n--- 场景5: 误报测试 ---")
    result5 = protocol.check_trigger_keywords(
        trigger_text="今天开会有点烦，想割草放松一下"
    )
    print(f"  正常文本检测到危机: {result5.crisis_detected}")

    # 状态摘要
    print(f"\n--- 危机模块状态摘要 ---")
    summary = protocol.get_status_summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # 热线信息
    print(f"\n--- 心理援助热线 ---")
    for h in protocol.get_hotlines():
        print(f"  {h['name']}: {h['number']} ({h['available']})")


if __name__ == "__main__":
    main()
