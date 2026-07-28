#!/usr/bin/env python3
"""
dashboard_data.py — 心潮 EmoWave 仪表盘帧数据记录器

本模块提供 FrameRecorder 类，用于在仿真运行期间逐帧收集情绪状态数据，
并输出为 JSON 供仪表盘回放系统使用。
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


class FrameRecorder:
    """
    逐帧数据记录器。

    在情绪事件处理过程中收集每一帧的原始观测、卡尔曼滤波状态、
    生理信号、预警级别等信息，最终汇总统输出为 JSON。
    """

    def __init__(self):
        """初始化空的容器。"""
        self.frames: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.daily_summaries: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
        self.strategy_stats: Dict[str, Any] = {}
        self.engine_diagnostics: Dict[str, Any] = {}
        self.start_timestamp: Optional[str] = None
        self.end_timestamp: Optional[str] = None

    def add_event_meta(
        self,
        event_id: str,
        day: int,
        trigger: str,
        start_time: float,
        duration_sec: int,
        peak_arousal: float,
        peak_valence: float,
        strategy_name: str,
        user_rating: int,
        warning_dict: Optional[Dict[str, Any]],
    ) -> None:
        """
        记录事件级别的元数据。

        Args:
            event_id: 事件唯一标识
            day: 事件发生的模拟天数索引
            trigger: 触发因素名称
            start_time: 事件起始时间（Unix 时间戳）
            duration_sec: 事件持续秒数
            peak_arousal: 峰值唤醒
            peak_valence: 峰值效价
            strategy_name: 推荐策略名称
            user_rating: 用户评分（1-5）
            warning_dict: 预警信息字典，若无则为 None
        """
        self.events.append({
            "event_id": event_id,
            "day": day,
            "trigger": trigger,
            "start_time": start_time,
            "duration_sec": duration_sec,
            "peak_arousal": peak_arousal,
            "peak_valence": peak_valence,
            "strategy_name": strategy_name,
            "user_rating": user_rating,
            "warning": warning_dict,
        })

    def add_frame(
        self,
        t: float,
        day: int,
        type: str,
        event_id: str,
        frame_idx: int,
        valence_raw: float,
        arousal_raw: float,
        valence_kf: float,
        arousal_kf: float,
        intensity: float,
        intensity_dot: float,
        hr: Optional[float],
        hrv: Optional[float],
        warning_level: str,
        threshold_arousal: float,
        threshold_valence: float,
    ) -> None:
        """
        记录单帧数据。

        Args:
            t: 事件内相对时间（秒）
            day: 模拟天数索引
            type: 帧类型（如 "event"）
            event_id: 所属事件 ID
            frame_idx: 帧序号
            valence_raw: 原始效价观测
            arousal_raw: 原始唤醒观测
            valence_kf: 卡尔曼滤波后效价
            arousal_kf: 卡尔曼滤波后唤醒
            intensity: 情绪强度
            intensity_dot: 强度变化率
            hr: 心率（BPM），无数据则为 None
            hrv: HRV（ms），无数据则为 None
            warning_level: 预警级别字符串
            threshold_arousal: 高唤醒风险阈值
            threshold_valence: 低效价风险阈值
        """
        self.frames.append({
            "t": round(t, 3),
            "day": day,
            "type": type,
            "event_id": event_id,
            "frame_idx": frame_idx,
            "valence_raw": round(valence_raw, 4),
            "arousal_raw": round(arousal_raw, 4),
            "valence_kf": round(valence_kf, 4),
            "arousal_kf": round(arousal_kf, 4),
            "intensity": round(intensity, 4),
            "intensity_dot": round(intensity_dot, 4),
            "hr": round(hr, 2) if hr is not None else None,
            "hrv": round(hrv, 2) if hrv is not None else None,
            "warning_level": warning_level,
            "threshold_arousal": round(threshold_arousal, 4),
            "threshold_valence": round(threshold_valence, 4),
        })

    def set_daily_summaries(self, daily_summaries: List[Dict[str, Any]]) -> None:
        """存储每日汇总列表。"""
        self.daily_summaries = daily_summaries

    def set_warnings(self, warnings: List[Dict[str, Any]]) -> None:
        """存储预警列表。"""
        self.warnings = warnings

    def set_strategy_stats(self, stats: Dict[str, Any]) -> None:
        """存储策略统计字典。"""
        self.strategy_stats = stats

    def set_engine_diagnostics(self, diag: Dict[str, Any]) -> None:
        """存储引擎诊断字典。"""
        self.engine_diagnostics = diag

    def save(self, filepath: str) -> None:
        """
        将所有收集的数据保存为 JSON。

        Args:
            filepath: 输出 JSON 文件路径
        """
        # 确保目标目录存在
        dir_path = os.path.dirname(filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        data = {
            "meta": {
                "start_timestamp": self.start_timestamp,
                "end_timestamp": self.end_timestamp,
                "events": self.events,
                "daily_summaries": self.daily_summaries,
                "warnings": self.warnings,
                "strategy_stats": self.strategy_stats,
                "engine_diagnostics": self.engine_diagnostics,
            },
            "frames": self.frames,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
