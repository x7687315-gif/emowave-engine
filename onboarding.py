#!/usr/bin/env python3
"""
onboarding.py — 心潮 EmoWave 新用户引导与首次评估模块

本模块实现：
  1. OnboardingFlow — 模拟新用户首次打开App的完整引导流程
  2. DienerFlourishingScale — 繁荣度量表（Diener Flourishing Scale, 8题版）
  3. 首次EMA引导 — 效价/唤醒滑条使用说明与首次记录

运行方式：
  cd /workspace/emowave-engine && python3 -c "from onboarding import OnboardingFlow; flow = OnboardingFlow(); result = flow.run(); print(result)"
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import json
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from enum import Enum


# ================================================================
# 繁荣度量表（Diener Flourishing Scale, 8题版）
# ================================================================

class FlourishingQuestion:
    """繁荣度量表的单个题目。"""
    def __init__(self, question_id: int, text: str, dimension: str):
        self.question_id = question_id
        self.text = text
        self.dimension = dimension  # "social", "psychological", "purpose"


FLOURISHING_QUESTIONS = [
    FlourishingQuestion(1, "我引领着有意义且令人满足的生活", "purpose"),
    FlourishingQuestion(2, "我与社会及他人有着高品质且深厚的关系", "social"),
    FlourishingQuestion(3, "我对自己的生活能力感到自信", "psychological"),
    FlourishingQuestion(4, "我能积极地为社会和他人做出贡献", "social"),
    FlourishingQuestion(5, "我感觉自己是一个有能力的、正在成长的人", "psychological"),
    FlourishingQuestion(6, "我基本上是一个善良的人，希望能为他人着想", "social"),
    FlourishingQuestion(7, "人们通常尊重我", "social"),
    FlourishingQuestion(8, "我有一个方向和目标，让我的生活有意义", "purpose"),
]

# 7点李克特量表：1=强烈不同意 ... 7=强烈同意


@dataclass
class FlourishingResult:
    """繁荣度量表结果。"""
    raw_scores: Dict[int, int]  # question_id → score (1-7)
    total_score: int            # 8-56
    average_score: float        # 1-7
    social_score: float         # 社会维度均分
    psychological_score: float   # 心理维度均分
    purpose_score: float        # 目的意义维度均分
    flourishing_level: str      # "高繁荣", "中等", "需要关注"


# ================================================================
# 首次EMA结果
# ================================================================

@dataclass
class FirstEMARecord:
    """首次EMA记录。"""
    timestamp: float
    valence: float        # 0-1
    arousal: float        # 0-1
    touch_velocity: float
    stillness_sec: float
    understood_instructions: bool  # 用户是否理解了滑条含义


# ================================================================
# 用户画像预判
# ================================================================

@dataclass
class UserProfileGuess:
    """基于首次评估的用户画像预判。"""
    archetype: str          # 预判类型名
    confidence: float      # 预判置信度 (0-1)
    description: str       # 描述文本
    suggestions: List[str]  # 建议列表
    initial_baseline_valence: float
    initial_baseline_arousal: float
    initial_stress_sensitivity: float


# ================================================================
# OnboardingFlow — 引导流程
# ================================================================

class OnboardingStep(Enum):
    WELCOME = "welcome"
    PRIVACY_EXPLANATION = "privacy_explanation"
    FLOURISHING_SCALE = "flourishing_scale"
    FIRST_EMA_TUTORIAL = "first_ema_tutorial"
    FIRST_EMA_RECORD = "first_ema_record"
    PROFILE_PREVIEW = "profile_preview"
    COMPLETE = "complete"


@dataclass
class OnboardingResult:
    """完整的引导流程结果。"""
    completed: bool
    completed_at: str  # ISO timestamp
    steps_completed: List[str]
    flourishing_result: Optional[FlourishingResult]
    first_ema: Optional[FirstEMARecord]
    profile_guess: Optional[UserProfileGuess]
    all_responses: Dict[str, Any]
    feedback_messages: List[Dict[str, str]]  # [{step, message, tone}]


# 文案库 — 所有用户可见的文案
ONBOARDING_TEXTS = {
    "welcome_title": "欢迎来到心潮 💫",
    "welcome_body": (
        "心潮是一个帮助你更好地了解自己情绪的工具。\n"
        "它不会对你做任何诊断，也不会评判你的感受。\n"
        "在这里，每一种情绪都值得被看见。"
    ),
    "privacy_title": "你的数据，只属于你",
    "privacy_body": (
        "心潮重视你的隐私。以下是我们的承诺：\n\n"
        "🔒 所有数据默认保存在你的手机本地，不会上传到任何云端服务器。\n"
        "🧹 你可以随时查看、导出或删除你的所有数据。\n"
        "📊 如果你想分享数据给咨询师，我们支持导出为加密文件。\n"
        "⚙️ 你可以关闭某些数据收集（如手表数据），只用滑条记录也可以。\n\n"
        "我们相信，了解自己是第一步，而安全的空间是了解自己的前提。"
    ),
    "flourishing_intro": (
        "在开始之前，我们想先了解你最近的整体状态。\n\n"
        "请根据过去两周的感受，对以下每句话选择最符合你的程度。\n"
        "1 = 完全不符合，7 = 完全符合。没有对错之分，按你真实的感受选就好。\n"
    ),
    "ema_tutorial_title": "认识你的情绪滑条",
    "ema_tutorial_valence": (
        "📊 效价滑条（横轴）\n"
        "← 不舒服 ←————→ 舒服 →\n"
        "左端（0）代表你感觉非常不舒服、难过\n"
        "右端（1）代表你感觉很好、很舒服\n\n"
        "想象你站在一条线上，平时大概在中间。开心时你会往右挪，难过时往左挪。"
    ),
    "ema_tutorial_arousal": (
        "📊 唤醒滑条（纵轴）\n"
        "← 平静 ←————→ 激动 →\n"
        "左端（0）代表你感觉很平静、甚至困倦\n"
        "右端（1）代表你感觉非常激动、紧张或兴奋\n\n"
        "注意：激动不一定是坏事。大笑、兴奋、愤怒都会让唤醒度升高。"
    ),
    "ema_tutorial_tip": (
        "💡 小贴士：\n"
        "- 不需要精确到小数点后几位，大致的感觉就很好\n"
        "- 有时候你可能会觉得两个方向都不太对，这很正常，选最接近的就好\n"
        "- 这个工具没有'标准答案'，你的感觉就是对的"
    ),
    "ema_prompt": "现在，请感受一下你此刻的状态，拖动两个滑条记录下来。",
    "profile_preview_intro": (
        "根据你刚才的回答，我们对你的情绪模式做了一个初步了解。\n"
        "这只是一个初步印象，随着你使用心潮的时间越长，它会越来越准确。"
    ),
}


class OnboardingFlow:
    """
    新用户引导流程。

    完整流程：
      1. 欢迎页面
      2. 隐私说明
      3. 繁荣度量表（8题）
      4. 首次EMA引导与记录
      5. 画像预判与温和反馈

    所有文案强调自我觉察而非诊断，关怀备至、避免说教。
    """

    def __init__(self, user_id: str = ""):
        self.user_id = user_id
        self.current_step = OnboardingStep.WELCOME
        self._flourishing_scores: Dict[int, int] = {}
        self._ema_record: Optional[FirstEMARecord] = None
        self._all_responses: Dict[str, Any] = {}
        self._feedback_messages: List[Dict[str, str]] = []

    # ---- 流程控制方法 ----

    def get_current_step(self) -> OnboardingStep:
        """获取当前引导步骤。"""
        return self.current_step

    def get_step_content(self) -> Dict[str, Any]:
        """获取当前步骤的内容（标题、正文、选项等）。"""
        if self.current_step == OnboardingStep.WELCOME:
            return {
                "step": "welcome",
                "title": ONBOARDING_TEXTS["welcome_title"],
                "body": ONBOARDING_TEXTS["welcome_body"],
                "action": "agree_to_continue",
            }
        elif self.current_step == OnboardingStep.PRIVACY_EXPLANATION:
            return {
                "step": "privacy_explanation",
                "title": ONBOARDING_TEXTS["privacy_title"],
                "body": ONBOARDING_TEXTS["privacy_body"],
                "action": "accept_privacy",
            }
        elif self.current_step == OnboardingStep.FLOURISHING_SCALE:
            # 判断当前应展示哪道题
            current_q_idx = len(self._flourishing_scores)
            if current_q_idx < len(FLOURISHING_QUESTIONS):
                q = FLOURISHING_QUESTIONS[current_q_idx]
                return {
                    "step": "flourishing_scale",
                    "intro": ONBOARDING_TEXTS["flourishing_intro"] if current_q_idx == 0 else None,
                    "question_id": q.question_id,
                    "question_text": q.text,
                    "dimension": q.dimension,
                    "progress": f"{current_q_idx + 1}/{len(FLOURISHING_QUESTIONS)}",
                    "scale_min": 1,
                    "scale_max": 7,
                }
            else:
                # 所有题目已答完，自动推进
                self.current_step = OnboardingStep.FIRST_EMA_TUTORIAL
                return self.get_step_content()
        elif self.current_step == OnboardingStep.FIRST_EMA_TUTORIAL:
            return {
                "step": "first_ema_tutorial",
                "title": ONBOARDING_TEXTS["ema_tutorial_title"],
                "valence_explanation": ONBOARDING_TEXTS["ema_tutorial_valence"],
                "arousal_explanation": ONBOARDING_TEXTS["ema_tutorial_arousal"],
                "tips": ONBOARDING_TEXTS["ema_tutorial_tip"],
                "action": "ready_to_record",
            }
        elif self.current_step == OnboardingStep.FIRST_EMA_RECORD:
            return {
                "step": "first_ema_record",
                "prompt": ONBOARDING_TEXTS["ema_prompt"],
                "valence_range": [0, 1],
                "arousal_range": [0, 1],
                "action": "submit_ema",
            }
        elif self.current_step == OnboardingStep.PROFILE_PREVIEW:
            return {
                "step": "profile_preview",
                "intro": ONBOARDING_TEXTS["profile_preview_intro"],
                "action": "finish_onboarding",
            }
        elif self.current_step == OnboardingStep.COMPLETE:
            return {
                "step": "complete",
                "title": "一切就绪 🎉",
                "body": (
                    "你已经完成了初始设置！\n\n"
                    "从现在开始，心潮会陪伴你记录每一天的情绪起伏。\n"
                    "不需要每天都填，按照你自己的节奏来就好。\n\n"
                    "记住，这里的每一笔记录都是你送给未来自己的一份礼物。"
                ),
            }
        else:
            return {"step": "unknown", "error": "未知的引导步骤"}

    def submit_welcome(self, agreed: bool = True) -> Dict[str, Any]:
        """处理欢迎页面的确认。"""
        self._all_responses["welcome_agreed"] = agreed
        if agreed:
            self.current_step = OnboardingStep.PRIVACY_EXPLANATION
            self._add_feedback(
                "welcome",
                "很高兴你选择了心潮，让我们一起开始这段自我了解的旅程吧。",
                tone="warm",
            )
            return {"success": True, "next_step": self.current_step.value}
        else:
            return {"success": False, "message": "希望以后有机会再见到你。"}

    def submit_privacy_accepted(self, accepted: bool) -> Dict[str, Any]:
        """处理隐私说明的确认。"""
        self._all_responses["privacy_accepted"] = accepted
        if accepted:
            self.current_step = OnboardingStep.FLOURISHING_SCALE
            self._add_feedback(
                "privacy",
                "你的信任对我们很重要。我们会好好守护你的数据。",
                tone="warm",
            )
            return {"success": True, "next_step": self.current_step.value}
        else:
            return {"success": False, "message": "需要接受隐私协议才能继续使用心潮。"}

    def submit_flourishing_answer(self, question_id: int, score: int) -> Dict[str, Any]:
        """
        提交繁荣度量表的某题回答。
        score: 1-7 的整数，代表从强烈不同意到强烈同意。
        """
        # 参数校验
        if not (1 <= score <= 7):
            return {"success": False, "message": f"分数须在1-7之间，当前为: {score}"}
        if not (1 <= question_id <= 8):
            return {"success": False, "message": f"题目编号须在1-8之间，当前为: {question_id}"}
        if question_id in self._flourishing_scores:
            return {"success": False, "message": f"题目 {question_id} 已回答，不可重复提交。"}

        self._flourishing_scores[question_id] = score
        self._all_responses.setdefault("flourishing_answers", {})[question_id] = score

        # 判断是否全部答完
        answered_count = len(self._flourishing_scores)
        if answered_count < len(FLOURISHING_QUESTIONS):
            return {
                "success": True,
                "progress": f"{answered_count}/{len(FLOURISHING_QUESTIONS)}",
                "remaining": len(FLOURISHING_QUESTIONS) - answered_count,
            }
        else:
            # 全部答完，计算结果并推进
            result = self.get_flourishing_result()
            self.current_step = OnboardingStep.FIRST_EMA_TUTORIAL
            # 生成针对繁荣度的反馈
            self._generate_feedback_for_flourishing(result)
            return {
                "success": True,
                "flourishing_completed": True,
                "result": {
                    "total_score": result.total_score,
                    "flourishing_level": result.flourishing_level,
                },
                "next_step": self.current_step.value,
            }

    def get_flourishing_result(self) -> Optional[FlourishingResult]:
        """获取繁荣度量表结果（8题全部答完后）。"""
        if len(self._flourishing_scores) < len(FLOURISHING_QUESTIONS):
            return None

        # 计算各维度均分
        social_ids = [q.question_id for q in FLOURISHING_QUESTIONS if q.dimension == "social"]
        psych_ids = [q.question_id for q in FLOURISHING_QUESTIONS if q.dimension == "psychological"]
        purpose_ids = [q.question_id for q in FLOURISHING_QUESTIONS if q.dimension == "purpose"]

        social_avg = sum(self._flourishing_scores[i] for i in social_ids) / len(social_ids)
        psych_avg = sum(self._flourishing_scores[i] for i in psych_ids) / len(psych_ids)
        purpose_avg = sum(self._flourishing_scores[i] for i in purpose_ids) / len(purpose_ids)

        total = sum(self._flourishing_scores.values())
        avg = total / len(FLOURISHING_QUESTIONS)

        # 判断繁荣水平
        if avg >= 5.5:
            level = "高繁荣"
        elif avg >= 3.5:
            level = "中等"
        else:
            level = "需要关注"

        return FlourishingResult(
            raw_scores=dict(self._flourishing_scores),
            total_score=total,
            average_score=round(avg, 2),
            social_score=round(social_avg, 2),
            psychological_score=round(psych_avg, 2),
            purpose_score=round(purpose_avg, 2),
            flourishing_level=level,
        )

    def submit_first_ema(self, valence: float, arousal: float,
                         touch_velocity: float = 0.0,
                         stillness: float = 0.0,
                         understood: bool = True) -> Dict[str, Any]:
        """
        提交首次EMA记录。
        
        参数:
            valence: 效价值，0-1之间
            arousal: 唤醒值，0-1之间
            touch_velocity: 触摸速度（模拟滑条拖动行为）
            stillness: 记录前静息秒数
            understood: 用户是否表示理解了滑条的含义
        """
        # 参数校验
        if not (0.0 <= valence <= 1.0):
            return {"success": False, "message": f"效价值须在0-1之间，当前为: {valence}"}
        if not (0.0 <= arousal <= 1.0):
            return {"success": False, "message": f"唤醒值须在0-1之间，当前为: {arousal}"}

        self._ema_record = FirstEMARecord(
            timestamp=time.time(),
            valence=valence,
            arousal=arousal,
            touch_velocity=touch_velocity,
            stillness_sec=stillness,
            understood_instructions=understood,
        )

        self._all_responses["first_ema"] = {
            "valence": valence,
            "arousal": arousal,
            "touch_velocity": touch_velocity,
            "stillness": stillness,
            "understood": understood,
        }

        # 如果用户表示不理解，给出额外帮助
        if not understood:
            self._add_feedback(
                "first_ema",
                "没关系，慢慢来。效价就是'感觉舒服不舒服'，唤醒就是'感觉平静还是激动'。"
                "随着你多试几次，就会越来越自然了。",
                tone="gentle",
            )

        # 推进到画像预判步骤
        self.current_step = OnboardingStep.PROFILE_PREVIEW
        return {"success": True, "next_step": self.current_step.value}

    def generate_profile_guess(self) -> UserProfileGuess:
        """
        根据繁荣度得分和首次EMA，生成初始用户画像预判。

        分类逻辑：
          - 高繁荣 + 低唤醒 → "情绪平和型"
          - 高繁荣 + 中唤醒 → "积极活跃型"
          - 中繁荣 + 高唤醒 → "高敏感型"
          - 低繁荣 + 低唤醒 → "低动力型"
          - 低繁荣 + 高唤醒 → "压力负担型"
        """
        # 获取繁荣度均分，默认中等
        f_result = self.get_flourishing_result()
        if f_result is not None:
            f_avg = f_result.average_score
        else:
            f_avg = 4.0  # 量表中位

        # 获取首次EMA的唤醒和效价
        if self._ema_record is not None:
            arousal = self._ema_record.arousal
            valence = self._ema_record.valence
        else:
            arousal = 0.3
            valence = 0.5

        # 预估初始压力敏感度（综合繁荣度与效价）
        # 繁荣度低 + 效价低 → 压力敏感度偏高
        stress_sensitivity = max(0.0, min(1.0, (1.0 - f_avg / 7.0) * 0.6 + (1.0 - valence) * 0.4))

        # 分类逻辑
        if f_avg >= 5.5:
            if arousal < 0.35:
                archetype = "情绪平和型"
                confidence = 0.65
                description = (
                    "你看起来是一个内心比较平静、满足的人。"
                    "生活中你可能更倾向于稳定的节奏，不容易被外界的波动打扰。"
                    "这种内在的平和是非常宝贵的力量。"
                )
                suggestions = [
                    "可以留意一下你在什么时刻会感到特别放松——这些时刻值得被记住",
                    "偶尔尝试记录不同活动时的情绪变化，帮助自己发现更多让生活丰富的方式",
                ]
            else:
                archetype = "积极活跃型"
                confidence = 0.65
                description = (
                    "你似乎在生活中保持着不错的活力和积极的心态。"
                    "你可能比较善于主动寻找快乐和意义，这种能力非常棒。"
                    "心潮可以帮你捕捉那些让你充满能量的时刻。"
                )
                suggestions = [
                    "关注一下哪些社交活动让你感到最有活力，这些都是你的能量源泉",
                    "在忙碌之余，也可以试试记录一些安静独处的时刻，看看它们给你带来怎样的感受",
                ]
        elif f_avg >= 3.5:
            if arousal >= 0.6:
                archetype = "高敏感型"
                confidence = 0.55
                description = (
                    "你似乎对周围环境的变化比较敏感，内心世界也比较丰富。"
                    "高敏感并不一定是坏事——它意味着你能感受到很多别人可能忽略的细节。"
                    "学会和自己的敏感共处，是一份很特别的礼物。"
                )
                suggestions = [
                    "在感到压力大的时候，给自己安排一些'低刺激'的活动，比如散步、听轻音乐",
                    "留意一下哪些人、哪些场景会让你感到安心，这些是你的'充电站'",
                    "试着区分'激动的紧张'和'害怕的紧张'，它们可能带来相似的唤醒感，但含义不同",
                ]
            else:
                archetype = "探索成长型"
                confidence = 0.50
                description = (
                    "你的整体状态看起来还不错，同时似乎也正处于某种探索或过渡的阶段。"
                    "生活中总有些时期是在寻找方向的，这很自然。"
                    "心潮可以成为你这段旅程中一个小小的陪伴。"
                )
                suggestions = [
                    "尝试每天或每隔几天记录一次情绪，看看一周后是否能看到一些模式",
                    "如果某个维度（社会关系、心理、目标感）得分偏低，可以在日常中多留意相关的感受",
                ]
        else:
            if arousal < 0.35:
                archetype = "低动力型"
                confidence = 0.55
                description = (
                    "你最近可能感觉动力不太足，这有很多可能的原因——"
                    "也许是太累了，也许是正在经历一段调整期。"
                    "这不代表你有任何问题，有时候身体和心灵只是在告诉我们需要休息。"
                )
                suggestions = [
                    "如果你愿意的话，可以留意一下每天有没有哪怕一小会儿让你觉得还不错的事情",
                    "不需要强迫自己变得积极，接受现在的状态本身也是一种力量",
                    "如果这种状态持续了很长时间，也许可以考虑和信任的人聊一聊",
                ]
            else:
                archetype = "压力负担型"
                confidence = 0.55
                description = (
                    "你最近的感受可能比较复杂，似乎同时承受着不少压力。"
                    "这种感觉一定不容易，但你愿意面对它、愿意开始记录，"
                    "这本身就说明你在很认真地照顾自己。"
                )
                suggestions = [
                    "当你感到压力很大的时候，试试深呼吸几次——哪怕只是短短几秒钟也会有帮助",
                    "在心潮里标记那些让你感到压力的情境，随着记录增多，你可能会发现一些规律",
                    "记住，寻求帮助不是软弱的表现。如果需要，专业的支持可以是一份力量",
                ]

        return UserProfileGuess(
            archetype=archetype,
            confidence=confidence,
            description=description,
            suggestions=suggestions,
            initial_baseline_valence=valence,
            initial_baseline_arousal=arousal,
            initial_stress_sensitivity=round(stress_sensitivity, 2),
        )

    def get_feedback_messages(self) -> List[Dict[str, str]]:
        """获取所有累积的反馈消息。"""
        return list(self._feedback_messages)

    def run(self,
            flourishing_answers: Optional[Dict[int, int]] = None,
            first_ema_valence: float = 0.55,
            first_ema_arousal: float = 0.30) -> OnboardingResult:
        """
        一键运行完整引导流程（用于模拟/测试）。

        如果 flourishing_answers 为 None，则自动生成中等水平的回答。
        """
        steps_completed: List[str] = []

        # 步骤1：欢迎
        self.current_step = OnboardingStep.WELCOME
        self.submit_welcome(agreed=True)
        steps_completed.append("welcome")

        # 步骤2：隐私说明
        self.current_step = OnboardingStep.PRIVACY_EXPLANATION
        self.submit_privacy_accepted(accepted=True)
        steps_completed.append("privacy_explanation")

        # 步骤3：繁荣度量表
        self.current_step = OnboardingStep.FLOURISHING_SCALE
        if flourishing_answers is None:
            # 自动生成中等偏上水平的模拟回答（均分约4.5）
            flourishing_answers = {}
            for q in FLOURISHING_QUESTIONS:
                # 根据维度给不同的基础分，加入少量随机波动
                base_scores = {"social": 5, "psychological": 4, "purpose": 4}
                base = base_scores.get(q.dimension, 4)
                # 加入 ±1 的波动，限定在1-7范围内
                import random
                score = max(1, min(7, base + random.choice([-1, 0, 0, 1])))
                flourishing_answers[q.question_id] = score

        # 按顺序提交所有题目
        for q in FLOURISHING_QUESTIONS:
            self.submit_flourishing_answer(q.question_id, flourishing_answers[q.question_id])
        steps_completed.append("flourishing_scale")

        # 步骤4：首次EMA引导（模拟用户已阅读）
        self.current_step = OnboardingStep.FIRST_EMA_TUTORIAL
        self._add_feedback(
            "first_ema_tutorial",
            "滑条的使用方式已经了解啦。记住，没有标准答案，跟着感觉走就好。",
            tone="encouraging",
        )
        steps_completed.append("first_ema_tutorial")

        # 步骤5：首次EMA记录
        self.current_step = OnboardingStep.FIRST_EMA_RECORD
        self.submit_first_ema(
            valence=first_ema_valence,
            arousal=first_ema_arousal,
            touch_velocity=0.42,
            stillness=3.5,
            understood=True,
        )
        steps_completed.append("first_ema_record")

        # 步骤6：画像预判
        self.current_step = OnboardingStep.PROFILE_PREVIEW
        profile = self.generate_profile_guess()

        # 生成EMA相关的反馈
        self._generate_feedback_for_ema(self._ema_record, profile)
        steps_completed.append("profile_preview")

        # 完成
        self.current_step = OnboardingStep.COMPLETE
        steps_completed.append("complete")

        self._add_feedback(
            "complete",
            "你已经迈出了了解自己的第一步。心潮会一直在你身边，安静地陪伴你。",
            tone="warm",
        )

        return OnboardingResult(
            completed=True,
            completed_at=datetime.now().isoformat(),
            steps_completed=steps_completed,
            flourishing_result=self.get_flourishing_result(),
            first_ema=self._ema_record,
            profile_guess=profile,
            all_responses=self._all_responses,
            feedback_messages=self._feedback_messages,
        )

    # ---- 内部辅助方法 ----

    def _add_feedback(self, step: str, message: str, tone: str = "warm"):
        """
        添加一条反馈消息。
        
        tone 可选值：
          "warm"        — 温暖亲切
          "encouraging" — 鼓励支持
          "informative" — 中性告知
          "gentle"      — 轻柔关怀（用于用户可能感到不确定时）
        """
        self._feedback_messages.append({
            "step": step,
            "message": message,
            "tone": tone,
        })

    def _generate_feedback_for_flourishing(self, result: FlourishingResult) -> List[Dict]:
        """
        根据繁荣度得分生成温和的反馈。
        
        反馈原则：
          - 强调觉察而非诊断
          - 避免任何"你有问题"的暗示
          - 高分时给予温和的肯定，但不夸大
          - 低分时表达理解，而非焦虑
        """
        feedbacks = []
        avg = result.average_score

        if avg >= 5.5:
            # 高繁荣
            self._add_feedback(
                "flourishing_scale",
                "从你的回答来看，你最近的整体状态挺不错的。"
                "希望心潮能帮你捕捉更多让你感到充实和快乐的瞬间。",
                tone="encouraging",
            )
        elif avg >= 4.0:
            # 中等偏上
            self._add_feedback(
                "flourishing_scale",
                "谢谢你的坦诚分享。你的生活里既有让你满足的部分，"
                "也有可能让你觉得还可以更好的地方——这很正常，大多数人都是这样的。",
                tone="warm",
            )
        elif avg >= 3.5:
            # 中等
            self._add_feedback(
                "flourishing_scale",
                "感谢你愿意分享真实的感受。每个人的生活都会有起伏，"
                "你愿意停下来觉察自己的状态，这本身就很了不起。",
                tone="gentle",
            )
        else:
            # 需要关注
            self._add_feedback(
                "flourishing_scale",
                "谢谢你愿意在这里真实地表达自己。"
                "也许最近的生活让你觉得有些辛苦，但请记住——"
                "愿意开始了解自己，已经是一种非常勇敢的选择。",
                tone="gentle",
            )

        # 针对维度的具体反馈
        # 社会维度
        if result.social_score < 3.5:
            self._add_feedback(
                "flourishing_scale",
                "在人际关系方面，你似乎有些不太满足的感受。"
                "人与人之间的连接需要时间，不必给自己太大压力。",
                tone="gentle",
            )

        # 心理维度
        if result.psychological_score < 3.5:
            self._add_feedback(
                "flourishing_scale",
                "对自己能力的感受是会变化的，尤其是在疲惫或压力较大的时候。"
                "给自己多一点时间和耐心，好吗？",
                tone="gentle",
            )

        # 目标意义维度
        if result.purpose_score < 3.5:
            self._add_feedback(
                "flourishing_scale",
                "关于生活的方向和意义，有时候找不到答案并不代表没有答案——"
                "也许答案正在路上，只是还没到达而已。",
                tone="gentle",
            )

        # 各维度亮点
        if result.social_score >= 5.5:
            self._add_feedback(
                "flourishing_scale",
                "你在社会关系方面得分不错，看来身边有你珍惜的人，也有珍惜你的人。",
                tone="warm",
            )
        if result.psychological_score >= 5.5:
            self._add_feedback(
                "flourishing_scale",
                "你对自身能力的肯定让人感到欣慰。这种内在的力量会在你需要的时候支持你。",
                tone="encouraging",
            )
        if result.purpose_score >= 5.5:
            self._add_feedback(
                "flourishing_scale",
                "你对生活的目标和意义有比较清晰的感受，这是一种很珍贵的内在资源。",
                tone="encouraging",
            )

        return feedbacks

    def _generate_feedback_for_ema(self, ema: FirstEMARecord, profile: UserProfileGuess) -> List[Dict]:
        """
        根据首次EMA和画像生成反馈。
        
        反馈注重：
          - 肯定用户完成了首次记录
          - 温和介绍画像预判
          - 强调这只是初步印象，避免用户产生标签感
        """
        feedbacks = []

        # 肯定首次记录
        self._add_feedback(
            "profile_preview",
            "这是你的第一条情绪记录，也是一个全新的开始。"
            "随着时间推移，这些小小的记录会汇聚成一幅属于你自己的情绪画像。",
            tone="encouraging",
        )

        # 针对首次EMA值域的反馈
        if ema.valence >= 0.6:
            self._add_feedback(
                "profile_preview",
                "此刻你似乎感觉还不错，希望这份好心情能陪伴你一会儿。",
                tone="warm",
            )
        elif ema.valence <= 0.3:
            self._add_feedback(
                "profile_preview",
                "现在的你可能不太舒服，这完全没关系。"
                "每一种感受都是暂时的，而你有能力穿越它。",
                tone="gentle",
            )

        # 介绍画像预判（强调非诊断、非标签）
        self._add_feedback(
            "profile_preview",
            f"根据你刚才的回答，心潮对你的初步印象是「{profile.archetype}」。"
            f"不过别太在意这个标签——它只是基于有限的回答做的一个粗略判断，"
            f"远远不能定义你。随着你使用心潮越来越久，它会更了解真实的你。",
            tone="informative",
        )

        # 给出个性化建议中的一条
        if profile.suggestions:
            self._add_feedback(
                "profile_preview",
                f"一个小建议：{profile.suggestions[0]}",
                tone="informative",
            )

        return feedbacks


# ================================================================
# 主入口（演示）
# ================================================================

def main():
    """运行引导流程演示。"""
    print("=" * 60)
    print("  心潮 EmoWave — 新用户引导流程演示")
    print("=" * 60)

    flow = OnboardingFlow(user_id="demo_user")
    result = flow.run(first_ema_valence=0.55, first_ema_arousal=0.30)

    print(f"\n引导完成: {result.completed}")
    print(f"繁荣度总分: {result.flourishing_result.total_score}/56")
    print(f"繁荣水平: {result.flourishing_result.flourishing_level}")
    print(f"首次EMA: valence={result.first_ema.valence:.2f}, arousal={result.first_ema.arousal:.2f}")
    print(f"画像预判: {result.profile_guess.archetype} (置信度: {result.profile_guess.confidence:.0%})")

    print("\n反馈消息:")
    for fb in result.feedback_messages:
        print(f"  [{fb['tone']}] {fb['message']}")

    # 保存结果
    output = {
        "completed": result.completed,
        "completed_at": result.completed_at,
        "flourishing": asdict(result.flourishing_result),
        "first_ema": asdict(result.first_ema),
        "profile_guess": asdict(result.profile_guess),
        "feedback": result.feedback_messages,
    }
    outpath = os.path.join("/workspace/emowave-engine/test_data", "onboarding_demo.json")
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存至: {outpath}")


if __name__ == "__main__":
    main()
