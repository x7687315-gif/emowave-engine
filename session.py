"""session.py — 心潮 EmoWave 会话控制器

桥接 UI 层与引擎层，管理：
  - EmoCalibrationEngine 的生命周期与状态持久化
  - ContextualBandit 策略推荐
  - DatabaseManager 数据存取
  - 仪表盘数据聚合
"""
import json
import time
import uuid
from datetime import datetime

from engine import EmoCalibrationEngine
from models import EmotionEventRaw, TimeSeriesSample, DailySummary
from recommender import ContextualBandit, extract_context, DEFAULT_STRATEGIES


class SessionController:
    """UI 层与引擎层之间的会话控制器。"""

    def __init__(self, db):
        self.db = db
        self.engine = EmoCalibrationEngine(user_id="local_user")
        self.bandit = ContextualBandit(DEFAULT_STRATEGIES)
        self._last_context = None
        self._restore_state()

    def _restore_state(self):
        """从数据库恢复引擎状态"""
        state_json = self.db.get_state('engine_state', '')
        if state_json:
            try:
                self.engine = EmoCalibrationEngine.load(state_json)
            except Exception:
                pass  # 恢复失败时使用新引擎

    # ================================================================
    # 事件处理
    # ================================================================

    def process_event(self, samples, trigger_tags=None, coping_methods=None,
                      coping_ratings=None, body_symptoms=None, user_peak_rating=None):
        """处理一次情绪事件：构造原始事件 → 引擎处理 → 存储结果"""
        trigger_tags = trigger_tags or []
        coping_methods = coping_methods or []
        coping_ratings = coping_ratings or {}
        body_symptoms = body_symptoms or []

        raw_event = EmotionEventRaw(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            samples=samples,
            user_peak_rating=user_peak_rating,
            trigger_tags=trigger_tags,
            coping_methods=coping_methods,
            coping_ratings=coping_ratings,
            body_symptoms=body_symptoms,
            calm_timestamp=samples[-1].timestamp if samples else time.time(),
        )

        profile, thresholds = self.engine.process_event(raw_event)

        # 存入数据库
        event_dict = {
            'event_id': profile.event_id,
            'start_time': profile.onset_time,
            'end_time': profile.calm_time,
            'peak_valence': profile.peak_valence,
            'peak_arousal': profile.peak_arousal,
            'peak_intensity': profile.subjective_peak or 0.0,
            'sample_count': profile.sample_count,
            'trigger_tags': trigger_tags,
            'coping_methods': coping_methods,
            'coping_ratings': coping_ratings,
            'body_symptoms': body_symptoms,
            'user_peak_rating': user_peak_rating,
        }
        self.db.save_event(event_dict)

        # 持久化引擎状态
        self._save_state()

        return {
            'event_id': profile.event_id,
            'peak_valence': profile.peak_valence,
            'peak_arousal': profile.peak_arousal,
            'peak_intensity': profile.subjective_peak or 0.0,
            'recovery_duration': profile.recovery_duration,
            'onset_time': profile.onset_time,
            'peak_time': profile.peak_time,
            'calm_time': profile.calm_time,
            'sample_count': profile.sample_count,
            'thresholds': {
                'high_risk_arousal': thresholds.high_risk_arousal,
                'high_risk_valence': thresholds.high_risk_valence,
                'model_confidence': thresholds.model_confidence,
            },
        }

    def _save_state(self):
        self.db.set_state('engine_state', self.engine.serialize_state())

    # ================================================================
    # 策略推荐
    # ================================================================

    def get_recommendation(self, valence, arousal, hour, weekday, sleep):
        """获取策略推荐"""
        ctx = extract_context(
            current_valence=valence,
            current_arousal=arousal,
            time_of_day=hour,
            weekday=weekday,
            last_sleep_score=sleep,
            trigger_category_code=0,
        )
        self._last_context = ctx
        rec = self.bandit.recommend(ctx)
        return {
            'strategy_id': rec.strategy_id,
            'strategy_name': rec.strategy_name,
            'predicted_score': rec.predicted_score,
            'uncertainty': rec.uncertainty,
        }

    def record_feedback(self, strategy_id, reward):
        """记录策略效果反馈"""
        if self._last_context is not None:
            self.bandit.update(strategy_id, self._last_context, reward)

    # ================================================================
    # 仪表盘数据
    # ================================================================

    def get_dashboard_data(self):
        """获取仪表盘展示数据"""
        recent = self.db.get_recent_events(limit=5)
        today = datetime.now().strftime('%Y-%m-%d')
        today_summary = self.db.get_daily_summary(today)
        baseline = self.engine.get_baseline()

        return {
            'recent_events': recent,
            'today_summary': today_summary,
            'baseline': {
                'resting_hr': baseline.resting_hr,
                'resting_hrv': baseline.resting_hrv_mean,
                'sleep_score': baseline.sleep_score,
            },
            'thresholds': self._get_thresholds_dict(),
        }

    def _get_thresholds_dict(self):
        t = self.engine.get_thresholds()
        return {
            'high_risk_arousal': t.high_risk_arousal,
            'high_risk_valence': t.high_risk_valence,
            'model_confidence': t.model_confidence,
        }
