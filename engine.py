"""
engine.py — 心潮 EmoWave 个人情绪校准引擎 · 主编排器

本模块是引擎的顶层入口，负责：
  1. 编排三大子模块（annotator → baseline → threshold）的工作流
  2. 管理引擎状态的生命周期（创建、恢复、持久化）
  3. 对外暴露简洁的 API

架构设计：
  Engine (本文件)
    ├── Annotator    (annotator.py)   — 事件标注
    ├── BaselineManager (baseline.py) — 基线管理
    └── ThresholdManager (threshold.py) — 阈值管理

数据流：
  用户操作 → EmotionEventRaw → Engine.process_event()
                              → annotator.annotate_event() → EventProfile
                              → threshold.ingest_event()
                              → threshold.get_personalized_thresholds()
                              → 返回 EventProfile + PersonalThresholds

  每日更新 → DailySummary → Engine.update_daily()
                       → baseline.update_baseline()
                       → baseline.detect_shift()
                       → 返回 Optional[BaselineShiftEvent]

隐私设计：
  - 所有数据仅存储在设备端
  - 提供序列化/反序列化方法用于本地持久化
  - 不包含任何网络请求逻辑

使用示例：
  engine = EmoCalibrationEngine(user_id="user_001")

  # 处理一次情绪事件
  profile, thresholds = engine.process_event(raw_event)

  # 每日更新
  shift_alert = engine.update_daily(daily_summary)

  # 持久化
  state_json = engine.serialize_state()
  # ... 写入本地文件 ...

  # 恢复
  engine = EmoCalibrationEngine.load(state_json)
"""

import json
from typing import Tuple, Optional, Dict, Any

from models import (
    EmotionEventRaw,
    EventProfile,
    DailySummary,
    PersonalThresholds,
    BaselineShiftEvent,
    BaselineVector,
    EngineState,
)
import config
from annotator import annotate_event
from baseline import BaselineManager
from threshold import ThresholdManager


class EmoCalibrationEngine:
    """
    个人情绪校准引擎 — 主编排器。

    所有公共方法都是线程安全的（在单用户设备端场景下，
    通常只有一个线程访问，但保持无副作用的纯函数设计便于测试）。
    """

    def __init__(self, user_id: str = ""):
        """
        创建新的引擎实例。

        Args:
            user_id: 用户标识符
        """
        self._user_id = user_id
        self._baseline_mgr = BaselineManager()
        self._threshold_mgr = ThresholdManager()
        self._event_profiles: list = []

    # ================================================================
    # 核心 API
    # ================================================================

    def process_event(
        self,
        raw_event: EmotionEventRaw,
    ) -> Tuple[EventProfile, PersonalThresholds]:
        """
        处理一次完整的情绪事件。

        完整流程：
          1. 自动标注：从原始时序中检测极点、危险段等
          2. 录入阈值管理器：更新个人化参数
          3. 获取最新阈值：根据当前置信度输出警戒线
          4. 存储事件档案

        Args:
            raw_event: 一次情绪事件的原始数据

        Returns:
            (EventProfile, PersonalThresholds) 元组：
            - EventProfile: 本次事件的完整标注结果
            - PersonalThresholds: 更新后的个性化警戒阈值
        """
        # 1. 自动标注
        profile = annotate_event(
            raw_event,
            baseline=self._baseline_mgr.current_baseline,
        )

        # 2. 录入阈值管理器
        self._threshold_mgr.ingest_event(
            profile,
            baseline=self._baseline_mgr.current_baseline,
        )
        self._threshold_mgr.set_baseline_age(self._baseline_mgr.history_length)

        # 3. 获取最新阈值
        thresholds = self._threshold_mgr.get_personalized_thresholds()

        # 4. 存储事件档案
        self._event_profiles.append(profile)
        if len(self._event_profiles) > config.MAX_STORED_EVENTS:
            self._event_profiles = self._event_profiles[-config.MAX_STORED_EVENTS:]

        return profile, thresholds

    def update_daily(
        self,
        daily: DailySummary,
    ) -> Optional[BaselineShiftEvent]:
        """
        每日更新基线并检测漂移。

        应在每日结束时（或用户首次打开 app 时）调用一次。

        Args:
            daily: 当日的 DailySummary

        Returns:
            如果检测到基线漂移，返回 BaselineShiftEvent；
            否则返回 None。
        """
        # 更新基线
        self._baseline_mgr.update_baseline(daily)

        # 同步基线年龄到阈值管理器
        self._threshold_mgr.set_baseline_age(self._baseline_mgr.history_length)

        # 检测漂移
        return self._baseline_mgr.detect_shift()

    def get_thresholds(self) -> PersonalThresholds:
        """
        获取当前的个性化阈值（不处理新事件）。
        可用于 UI 层实时查询。
        """
        self._threshold_mgr.set_baseline_age(self._baseline_mgr.history_length)
        return self._threshold_mgr.get_personalized_thresholds()

    def get_baseline(self) -> BaselineVector:
        """获取当前基线向量。"""
        return self._baseline_mgr.current_baseline

    def get_baseline_stats(self) -> Dict[str, Dict[str, float]]:
        """获取基线的统计摘要（均值、标准差、当前值）。用于调试和 UI 展示。"""
        return self._baseline_mgr.get_baseline_stats()

    # ================================================================
    # 状态持久化
    # ================================================================

    def serialize_state(self) -> str:
        """
        将引擎完整状态序列化为 JSON 字符串。
        用于写入设备端本地存储。
        """
        state = EngineState(
            user_id=self._user_id,
            baseline=self._baseline_mgr.current_baseline,
            baseline_history=self._baseline_mgr.history,
            event_profiles=self._event_profiles,
            thresholds=self.get_thresholds(),
            total_events_processed=len(self._event_profiles),
            model_confidence=self._threshold_mgr.model_confidence,
            created_date="",
            last_updated=self._baseline_mgr.current_baseline.date,
        )
        return _dataclass_to_json(state)

    @classmethod
    def load(cls, state_json: str) -> "EmoCalibrationEngine":
        """
        从 JSON 字符串恢复引擎状态。

        Args:
            state_json: 之前通过 serialize_state() 导出的 JSON

        Returns:
            恢复后的引擎实例
        """
        state = _json_to_dataclass(json.loads(state_json), EngineState)
        engine = cls(user_id=state.user_id)

        # 恢复基线
        if state.baseline_history:
            first = state.baseline_history[0]
            engine._baseline_mgr = BaselineManager(initial_baseline=first)
            for bl in state.baseline_history[1:]:
                # 不调用 update_baseline（避免重复计算），
                # 直接注入历史
                engine._baseline_mgr._history.append(bl)
                engine._baseline_mgr._baseline = bl
        elif state.baseline:
            engine._baseline_mgr = BaselineManager(initial_baseline=state.baseline)

        # 恢复事件档案
        engine._event_profiles = state.event_profiles or []

        # 恢复阈值管理器状态
        engine._threshold_mgr._event_count = state.total_events_processed
        engine._threshold_mgr.set_baseline_age(engine._baseline_mgr.history_length)

        # 重新计算置信度（基于恢复的数据）
        for profile in engine._event_profiles:
            engine._threshold_mgr._recent_peak_arousals.append(profile.peak_arousal)
            engine._threshold_mgr._recent_peak_valences.append(profile.peak_valence)
        if len(engine._threshold_mgr._recent_peak_arousals) > 50:
            engine._threshold_mgr._recent_peak_arousals = engine._threshold_mgr._recent_peak_arousals[-50:]
            engine._threshold_mgr._recent_peak_valences = engine._threshold_mgr._recent_peak_valences[-50:]

        return engine

    # ================================================================
    # 诊断与调试
    # ================================================================

    def diagnostics(self) -> Dict[str, Any]:
        """
        返回引擎的诊断信息。
        用于调试面板或开发者模式。
        """
        thresholds = self.get_thresholds()
        return {
            "user_id": self._user_id,
            "total_events": len(self._event_profiles),
            "baseline_days": self._baseline_mgr.history_length,
            "model_confidence": self._threshold_mgr.model_confidence,
            "model_source": self._threshold_mgr.model_source.value,
            "thresholds": {
                "high_risk_arousal": thresholds.high_risk_arousal,
                "high_risk_valence": thresholds.high_risk_valence,
                "hrv_drop_percent": thresholds.hrv_drop_percent,
                "hr_surge_zscore": thresholds.hr_surge_zscore,
            },
            "baseline": {
                "resting_hrv": round(self._baseline_mgr.current_baseline.resting_hrv_mean, 2),
                "resting_hr": round(self._baseline_mgr.current_baseline.resting_hr, 2),
                "sleep_score": round(self._baseline_mgr.current_baseline.sleep_score, 2),
            },
            "baseline_stats": self._baseline_mgr.get_baseline_stats(),
        }


# ================================================================
# 序列化辅助
# ================================================================

def _dataclass_to_json(obj) -> str:
    """将 dataclass 实例递归转换为 JSON 字符串。"""
    if hasattr(obj, "__dataclass_fields__"):
        return json.dumps(_dataclass_to_dict(obj), ensure_ascii=False, indent=2)
    return json.dumps(obj, ensure_ascii=False)


def _dataclass_to_dict(obj) -> Any:
    """递归地将 dataclass 转换为 dict。"""
    if hasattr(obj, "__dataclass_fields__"):
        result = {}
        for key in obj.__dataclass_fields__:
            value = getattr(obj, key)
            result[key] = _dataclass_to_dict(value)
        return result
    elif isinstance(obj, list):
        return [_dataclass_to_dict(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    elif hasattr(obj, "value"):
        return obj.value  # Enum
    else:
        return str(obj)


def _json_to_dataclass(data: dict, cls):
    """从 dict 递归地重建 dataclass 实例。"""
    if not isinstance(data, dict):
        return data

    import dataclasses
    if not dataclasses.is_dataclass(cls):
        return data

    field_types = {f.name: f.type for f in dataclasses.fields(cls)}
    kwargs = {}

    for field_name, field_type in field_types.items():
        if field_name not in data:
            continue

        value = data[field_name]

        # 处理 Optional
        actual_type = _unwrap_optional(field_type)
        if value is None:
            kwargs[field_name] = None
            continue

        # 处理 List[X]
        if _is_list_type(actual_type):
            inner_type = _get_list_inner_type(actual_type)
            if dataclasses.is_dataclass(inner_type) and isinstance(value, list):
                kwargs[field_name] = [_json_to_dataclass(item, inner_type) for item in value]
            else:
                kwargs[field_name] = value
            continue

        # 处理嵌套 dataclass
        if dataclasses.is_dataclass(actual_type) and isinstance(value, dict):
            kwargs[field_name] = _json_to_dataclass(value, actual_type)
            continue

        # 处理 Enum
        if hasattr(actual_type, "__members__") and isinstance(value, str):
            try:
                kwargs[field_name] = actual_type(value)
                continue
            except ValueError:
                pass

        kwargs[field_name] = value

    return cls(**kwargs)


def _unwrap_optional(tp):
    """解包 Optional[X] → X。"""
    import typing
    if typing.get_origin(tp) is typing.Union:
        args = typing.get_args(tp)
        for arg in args:
            if arg is not type(None):
                return arg
    return tp


def _is_list_type(tp) -> bool:
    """检查类型是否为 List[X]。"""
    import typing
    return typing.get_origin(tp) is list


def _get_list_inner_type(tp):
    """获取 List[X] 中的 X。"""
    import typing
    args = typing.get_args(tp)
    return args[0] if args else str
