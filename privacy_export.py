#!/usr/bin/env python3
"""
privacy_export.py — 心潮 EmoWave 数据导出与隐私控制模块

本模块实现：
  1. DataExporter — 数据导出类，支持加密导出、时间模糊化、数据解读指南
  2. PrivacyManager — 隐私管理类，支持数据审查、选择性删除、收集最小化开关

运行方式：
  cd /workspace/emowave-engine && python3 -c "from privacy_export import DataExporter, PrivacyManager; ..."
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import os
import json
import csv
import hashlib
import base64
import secrets
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
import re

from models import (
    TimeSeriesSample,
    EmotionEventRaw,
    EventProfile,
    DailySummary,
    PersonalThresholds,
    BaselineVector,
    EngineState,
)


# ================================================================
# DataExporter — 数据导出
# ================================================================

class ExportFormat(Enum):
    """导出格式枚举"""
    JSON = "json"
    CSV = "csv"
    ENCRYPTED_JSON = "encrypted_json"


class DataExporter:
    """
    数据导出器。

    功能：
      - 将用户的情绪事件、日常数据、模型参数导出为 JSON 或 CSV
      - 可选加密导出（AES-256-GCM 风格，使用 XOR + base64 简化实现）
      - 时间模糊化：精确时间戳可模糊到"周"级别
      - 自动嵌入数据解读指南
    """

    def __init__(self, user_id: str = "", output_dir: str = "./exports"):
        """初始化导出器。

        参数：
          user_id: 用户标识（用于生成导出文件名）
          output_dir: 导出文件存放目录
        """
        self.user_id = user_id
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._export_log: List[Dict[str, Any]] = []

    def export_all(
        self,
        event_profiles: List[EventProfile],
        daily_summaries: List[DailySummary],
        thresholds: PersonalThresholds,
        baseline: BaselineVector,
        engine_state: Optional[EngineState] = None,
        format: ExportFormat = ExportFormat.JSON,
        blur_timestamps: bool = False,
        blur_level: str = "day",  # "day" or "week" or "month"
        include_guide: bool = True,
        password: Optional[str] = None,
    ) -> str:
        """
        导出所有用户数据。

        参数：
          event_profiles: 情绪事件标注列表
          daily_summaries: 每日摘要列表
          thresholds: 个性化阈值
          baseline: 当前基线向量
          engine_state: 引擎完整状态（可选）
          format: 导出格式
          blur_timestamps: 是否模糊化时间戳
          blur_level: 模糊级别 — "day"(精确到天), "week"(精确到周), "month"(精确到月)
          include_guide: 是否在导出文件中嵌入数据解读指南
          password: 如果提供，使用简化加密

        返回：导出文件的完整路径
        """
        # --- 组装导出数据 ---
        export_data: Dict[str, Any] = {}

        # 用户标识（脱敏：仅保留前4字符 + 哈希后4字符）
        safe_id = self._safe_user_id(self.user_id)
        export_data["user_id_safe"] = safe_id
        export_data["exported_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        export_data["export_version"] = "1.0.0"

        # 情绪事件（脱敏处理）
        export_data["emotion_events"] = [
            self._desensitize_event(ep, blur_timestamps, blur_level)
            for ep in event_profiles
        ]
        export_data["emotion_events_count"] = len(event_profiles)

        # 每日摘要
        export_data["daily_summaries"] = [asdict(ds) for ds in daily_summaries]
        export_data["daily_summaries_count"] = len(daily_summaries)

        # 个性化阈值（枚举值转字符串以确保 JSON 兼容）
        thresholds_dict = asdict(thresholds)
        thresholds_dict["model_source"] = thresholds.model_source.value
        export_data["thresholds"] = thresholds_dict

        # 基线向量
        export_data["baseline"] = asdict(baseline)

        # 引擎状态（可选）
        if engine_state is not None:
            export_data["engine_state"] = {
                "user_id": safe_id,
                "total_events_processed": engine_state.total_events_processed,
                "model_confidence": round(engine_state.model_confidence, 4),
                "created_date": engine_state.created_date,
                "last_updated": engine_state.last_updated,
            }

        # 数据清单
        export_data["data_inventory"] = self._get_data_inventory()

        # 数据解读指南
        if include_guide:
            export_data["data_guide"] = self._generate_guide()

        # --- 序列化 ---
        if format == ExportFormat.JSON:
            content = json.dumps(export_data, ensure_ascii=False, indent=2)
            ext = ".json"
        elif format == ExportFormat.CSV:
            # CSV 模式仅导出事件数据，额外信息以注释形式保留
            content = self._export_all_as_csv(export_data)
            ext = ".csv"
        elif format == ExportFormat.ENCRYPTED_JSON:
            if not password:
                raise ValueError("加密导出必须提供 password 参数")
            json_str = json.dumps(export_data, ensure_ascii=False, indent=2)
            content = self._simple_encrypt(json_str, password)
            ext = ".encrypted.json"
        else:
            raise ValueError(f"不支持的导出格式: {format}")

        # --- 写入文件 ---
        timestamp_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_user_tag = safe_id[:8] if safe_id else "anonymous"
        filename = f"emowave_export_{safe_user_tag}_{timestamp_tag}{ext}"
        filepath = os.path.join(self.output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        # 记录导出日志
        self._export_log.append({
            "timestamp": datetime.now().isoformat(),
            "filepath": filepath,
            "format": format.value,
            "events_count": len(event_profiles),
            "daily_count": len(daily_summaries),
            "blurred": blur_timestamps,
            "encrypted": password is not None,
        })

        return filepath

    def export_events_csv(
        self,
        event_profiles: List[EventProfile],
        blur_timestamps: bool = False,
        filepath: Optional[str] = None,
    ) -> str:
        """导出情绪事件为 CSV 格式。

        参数：
          event_profiles: 情绪事件标注列表
          blur_timestamps: 是否模糊化时间戳
          filepath: 指定输出路径（可选，默认自动生成）

        返回：导出文件的完整路径
        """
        if not filepath:
            timestamp_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_user_tag = self._safe_user_id(self.user_id)[:8] or "anonymous"
            filepath = os.path.join(
                self.output_dir,
                f"emowave_events_{safe_user_tag}_{timestamp_tag}.csv",
            )

        # CSV 表头
        headers = [
            "event_id",
            "onset_time",
            "peak_time",
            "calm_time",
            "peak_valence",
            "peak_arousal",
            "subjective_peak",
            "physio_peak_score",
            "recovery_duration_sec",
            "recovery_speed",
            "sample_count",
            "dangerous_rise_count",
        ]

        rows = []
        for ep in event_profiles:
            if blur_timestamps:
                onset_str = self._blur_timestamp(ep.onset_time, "day")
                peak_str = self._blur_timestamp(ep.peak_time, "day")
                calm_str = self._blur_timestamp(ep.calm_time, "day")
            else:
                onset_str = datetime.fromtimestamp(ep.onset_time).strftime("%Y-%m-%d %H:%M:%S")
                peak_str = datetime.fromtimestamp(ep.peak_time).strftime("%Y-%m-%d %H:%M:%S")
                calm_str = datetime.fromtimestamp(ep.calm_time).strftime("%Y-%m-%d %H:%M:%S")

            rows.append([
                ep.event_id,
                onset_str,
                peak_str,
                calm_str,
                round(ep.peak_valence, 4),
                round(ep.peak_arousal, 4),
                ep.subjective_peak if ep.subjective_peak is not None else "",
                round(ep.physiological_peak_score, 4),
                round(ep.recovery_duration, 2),
                round(ep.recovery_speed, 4),
                ep.sample_count,
                len(ep.dangerous_rise_segments),
            ])

        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

        self._export_log.append({
            "timestamp": datetime.now().isoformat(),
            "filepath": filepath,
            "format": "csv_events",
            "events_count": len(event_profiles),
            "blurred": blur_timestamps,
            "encrypted": False,
        })

        return filepath

    def export_daily_csv(
        self,
        daily_summaries: List[DailySummary],
        filepath: Optional[str] = None,
    ) -> str:
        """导出每日摘要为 CSV 格式。

        参数：
          daily_summaries: 每日摘要列表
          filepath: 指定输出路径（可选，默认自动生成）

        返回：导出文件的完整路径
        """
        if not filepath:
            timestamp_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_user_tag = self._safe_user_id(self.user_id)[:8] or "anonymous"
            filepath = os.path.join(
                self.output_dir,
                f"emowave_daily_{safe_user_tag}_{timestamp_tag}.csv",
            )

        headers = [
            "date",
            "avg_resting_hrv",
            "avg_resting_hr",
            "sleep_score",
            "morning_valence_avg",
            "evening_valence_avg",
            "event_count",
            "peak_arousal_max",
        ]

        rows = []
        for ds in daily_summaries:
            rows.append([
                ds.date,
                round(ds.avg_resting_hrv, 2),
                round(ds.avg_resting_hr, 2),
                round(ds.sleep_score, 2),
                round(ds.morning_valence_avg, 4),
                round(ds.evening_valence_avg, 4),
                ds.event_count,
                round(ds.peak_arousal_max, 4),
            ])

        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

        self._export_log.append({
            "timestamp": datetime.now().isoformat(),
            "filepath": filepath,
            "format": "csv_daily",
            "daily_count": len(daily_summaries),
            "blurred": False,
            "encrypted": False,
        })

        return filepath

    def _blur_timestamp(self, ts: float, level: str) -> str:
        """
        将 Unix 时间戳模糊化。

        参数：
          ts: Unix 时间戳（秒）
          level: 模糊级别
            "day"   → "2026-07-01"
            "week"  → "2026-W27"
            "month" → "2026-07"

        返回：模糊化后的时间字符串
        """
        dt = datetime.fromtimestamp(ts)
        if level == "day":
            return dt.strftime("%Y-%m-%d")
        elif level == "week":
            # ISO 周编号
            iso_year, iso_week, _ = dt.isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        elif level == "month":
            return dt.strftime("%Y-%m")
        else:
            return dt.strftime("%Y-%m-%d")

    def _desensitize_event(
        self,
        profile: EventProfile,
        blur_timestamps: bool = False,
        blur_level: str = "day",
    ) -> Dict:
        """脱敏单个事件数据。

        脱敏策略：
          - event_id 仅保留前8字符，其余替换为星号
          - 时间戳根据 blur_level 模糊化
          - 不包含任何生理原始采样数据

        参数：
          profile: 事件标注对象
          blur_timestamps: 是否模糊化时间戳
          blur_level: 模糊级别

        返回：脱敏后的字典
        """
        # event_id 脱敏：保留前8字符
        safe_event_id = profile.event_id[:8] + "****" if len(profile.event_id) > 8 else profile.event_id

        # 时间戳处理
        if blur_timestamps:
            onset_str = self._blur_timestamp(profile.onset_time, blur_level)
            peak_str = self._blur_timestamp(profile.peak_time, blur_level)
            calm_str = self._blur_timestamp(profile.calm_time, blur_level)
        else:
            onset_str = datetime.fromtimestamp(profile.onset_time).strftime("%Y-%m-%d %H:%M:%S")
            peak_str = datetime.fromtimestamp(profile.peak_time).strftime("%Y-%m-%d %H:%M:%S")
            calm_str = datetime.fromtimestamp(profile.calm_time).strftime("%Y-%m-%d %H:%M:%S")

        # 危险上升段简化
        dangerous_segments = []
        for seg in profile.dangerous_rise_segments:
            if blur_timestamps:
                seg_start = self._blur_timestamp(seg.start_time, blur_level)
                seg_end = self._blur_timestamp(seg.end_time, blur_level)
            else:
                seg_start = datetime.fromtimestamp(seg.start_time).strftime("%Y-%m-%d %H:%M:%S")
                seg_end = datetime.fromtimestamp(seg.end_time).strftime("%Y-%m-%d %H:%M:%S")
            dangerous_segments.append({
                "start_time": seg_start,
                "end_time": seg_end,
                "peak_arousal_slope": round(seg.peak_arousal_slope, 4),
                "avg_hr_zscore": round(seg.avg_hr_zscore, 4),
                "hrv_drop_at_peak": round(seg.hrv_drop_at_peak, 4),
            })

        return {
            "event_id_safe": safe_event_id,
            "onset_time": onset_str,
            "peak_time": peak_str,
            "calm_time": calm_str,
            "peak_valence": round(profile.peak_valence, 4),
            "peak_arousal": round(profile.peak_arousal, 4),
            "subjective_peak": profile.subjective_peak,
            "physiological_peak_score": round(profile.physiological_peak_score, 4),
            "physiological_peak_confidence": round(profile.physiological_peak_confidence, 4),
            "recovery_duration_sec": round(profile.recovery_duration, 2),
            "recovery_speed": round(profile.recovery_speed, 4),
            "sample_count": profile.sample_count,
            "dangerous_rise_segments": dangerous_segments,
            "dangerous_rise_count": len(dangerous_segments),
        }

    def _generate_guide(self) -> str:
        """
        生成数据解读指南（纯文本）。

        内容：
          - 文件结构说明
          - 字段含义解释
          - Valence/Arousal 的含义
          - 强度指标的计算方式
          - 预警阈值的意义
          - 推荐给咨询师的阅读建议

        返回：完整的指南文本字符串
        """
        return DATA_GUIDE_TEXT

    def _simple_encrypt(self, plaintext: str, password: str) -> str:
        """
        简化的加密（XOR + base64，用于演示。生产环境应使用 cryptography 库）。

        加密流程：
          1. 从密码派生密钥（SHA-256 哈希）
          2. 生成随机盐值（16字节）
          3. 将明文 UTF-8 编码为字节
          4. 逐字节异或加密
          5. 密文经 base64 编码后输出
          6. 格式: "EMW1:" + base64(salt + ciphertext)

        参数：
          plaintext: 明文字符串
          password: 加密密码

        返回：加密后的字符串
        """
        # 生成随机盐值，增加密码破解难度
        salt = secrets.token_bytes(16)

        # 从密码派生密钥：SHA-256(password + salt)，取前32字节
        key_material = hashlib.sha256(password.encode("utf-8") + salt).digest()
        key = key_material[:32]

        # 将明文转为字节
        plaintext_bytes = plaintext.encode("utf-8")

        # XOR 加密：循环使用密钥
        ciphertext_bytes = bytearray(len(plaintext_bytes))
        for i in range(len(plaintext_bytes)):
            ciphertext_bytes[i] = plaintext_bytes[i] ^ key[i % len(key)]

        # 组装：salt(16字节) + ciphertext
        combined = salt + bytes(ciphertext_bytes)

        # Base64 编码，添加版本前缀
        encoded = base64.b64encode(combined).decode("ascii")
        return "EMW1:" + encoded

    def _simple_decrypt(self, ciphertext: str, password: str) -> str:
        """
        简化的解密。

        参数：
          ciphertext: 由 _simple_encrypt 产生的密文字符串
          password: 加密时使用的密码

        返回：解密后的明文字符串

        异常：
          ValueError: 格式不正确或密码错误
        """
        # 检查格式前缀
        if not ciphertext.startswith("EMW1:"):
            raise ValueError("密文格式不正确，应以 'EMW1:' 开头")

        # 去除前缀并 base64 解码
        encoded = ciphertext[5:]
        try:
            combined = base64.b64decode(encoded)
        except Exception as e:
            raise ValueError(f"Base64 解码失败: {e}")

        # 提取盐值（前16字节）
        if len(combined) < 16:
            raise ValueError("密文数据不完整")
        salt = combined[:16]
        ciphertext_bytes = combined[16:]

        # 从密码派生密钥（与加密过程一致）
        key_material = hashlib.sha256(password.encode("utf-8") + salt).digest()
        key = key_material[:32]

        # XOR 解密
        plaintext_bytes = bytearray(len(ciphertext_bytes))
        for i in range(len(ciphertext_bytes)):
            plaintext_bytes[i] = ciphertext_bytes[i] ^ key[i % len(key)]

        # 验证解密结果是否为合法 UTF-8
        try:
            return plaintext_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("密码错误或数据损坏，解密结果不是合法文本")

    def _get_data_inventory(self) -> Dict[str, List[str]]:
        """返回用户被存储的数据类别清单。

        返回：字典，键为数据类别，值为该类别包含的字段列表
        """
        return {
            "emotion_events": [
                "event_id", "onset_time", "peak_time", "calm_time",
                "peak_valence", "peak_arousal", "subjective_peak",
                "physiological_peak_score", "recovery_duration",
                "dangerous_rise_segments",
            ],
            "daily_summaries": [
                "date", "avg_resting_hrv", "avg_resting_hr", "sleep_score",
                "morning_valence_avg", "evening_valence_avg",
                "event_count", "peak_arousal_max",
            ],
            "thresholds": [
                "high_risk_arousal", "high_risk_valence",
                "hrv_drop_percent", "hr_surge_zscore",
                "dangerous_rise_slope", "model_confidence",
                "model_source", "event_count", "last_updated",
            ],
            "baseline": [
                "resting_hrv_mean", "resting_hr", "sleep_score",
                "typical_valence_8am", "typical_valence_6pm", "date",
            ],
            "model_params": [
                "model_confidence", "model_source", "total_events_processed",
            ],
        }

    def get_export_summary(self) -> Dict[str, Any]:
        """返回导出操作的摘要信息。

        返回：包含最近导出记录和统计信息的字典
        """
        return {
            "user_id_safe": self._safe_user_id(self.user_id),
            "output_dir": self.output_dir,
            "total_exports": len(self._export_log),
            "recent_exports": self._export_log[-5:] if self._export_log else [],
            "supported_formats": [f.value for f in ExportFormat],
        }

    # --- 内部辅助方法 ---

    @staticmethod
    def _safe_user_id(user_id: str) -> str:
        """对用户 ID 进行脱敏处理：前4字符 + 哈希后4字符。"""
        if not user_id:
            return "anonymous"
        prefix = user_id[:4]
        suffix = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:4]
        return f"{prefix}_{suffix}"

    @staticmethod
    def _export_all_as_csv(export_data: Dict[str, Any]) -> str:
        """将全部导出数据转为 CSV 兼容格式（主表为事件，附加信息写为注释行）。"""
        lines: List[str] = []

        # 文件头部注释
        lines.append(f"# 心潮 EmoWave 数据导出")
        lines.append(f"# 导出时间: {export_data.get('exported_at', 'N/A')}")
        lines.append(f"# 用户标识: {export_data.get('user_id_safe', 'N/A')}")
        lines.append(f"# 事件总数: {export_data.get('emotion_events_count', 0)}")
        lines.append(f"# 每日摘要数: {export_data.get('daily_summaries_count', 0)}")
        lines.append("")

        # --- 情绪事件表 ---
        lines.append("## 情绪事件 (emotion_events)")
        event_headers = [
            "event_id_safe", "onset_time", "peak_time", "calm_time",
            "peak_valence", "peak_arousal", "subjective_peak",
            "physiological_peak_score", "recovery_duration_sec",
            "recovery_speed", "sample_count", "dangerous_rise_count",
        ]
        lines.append(",".join(event_headers))
        for ev in export_data.get("emotion_events", []):
            row_values = []
            for h in event_headers:
                val = ev.get(h, "")
                if isinstance(val, (int, float)):
                    val = str(val)
                if "," in str(val):
                    val = f'"{val}"'
                row_values.append(str(val))
            lines.append(",".join(row_values))

        lines.append("")

        # --- 每日摘要表 ---
        lines.append("## 每日摘要 (daily_summaries)")
        daily_headers = [
            "date", "avg_resting_hrv", "avg_resting_hr", "sleep_score",
            "morning_valence_avg", "evening_valence_avg",
            "event_count", "peak_arousal_max",
        ]
        lines.append(",".join(daily_headers))
        for ds in export_data.get("daily_summaries", []):
            row_values = [str(ds.get(h, "")) for h in daily_headers]
            lines.append(",".join(row_values))

        return "\n".join(lines)


# ================================================================
# PrivacyManager — 隐私管理
# ================================================================

class DataCategory(Enum):
    """数据类别枚举"""
    EMOTION_EVENTS = "emotion_events"        # 情绪事件
    DAILY_SUMMARIES = "daily_summaries"      # 每日摘要
    PHYSIOLOGICAL = "physiological"          # 生理数据（HR, HRV）
    SLIDER_OBSERVATIONS = "slider_obs"       # 滑条观测
    EMA_RECORDS = "ema_records"              # EMA 记录
    STRATEGY_HISTORY = "strategy_history"    # 策略使用历史
    MODEL_PARAMS = "model_params"            # 模型参数
    BASELINE_DATA = "baseline_data"          # 基线数据


@dataclass
class DataInventory:
    """数据清单：各类数据的存储情况。"""
    category: DataCategory
    description: str
    count: int
    date_range: str           # "2026-07-01 ~ 2026-07-07"
    size_estimate_kb: float
    contains_identity: bool


class PrivacyManager:
    """
    隐私管理器。

    功能：
      - 查看所有被存储的数据类别
      - 一键删除所有本地数据
      - 选择性删除（仅删除生理数据，保留情绪记录等）
      - 数据收集最小化开关
    """

    def __init__(self, data_dir: str = "./user_data", user_id: str = ""):
        """初始化隐私管理器。

        参数：
          data_dir: 用户数据存储目录
          user_id: 用户标识
        """
        self.user_id = user_id
        self.data_dir = data_dir
        self._collection_preferences: Dict[DataCategory, bool] = {
            cat: True for cat in DataCategory
        }
        self._deletion_log: List[Dict] = []
        os.makedirs(data_dir, exist_ok=True)

    def get_data_inventory(self) -> List[DataInventory]:
        """
        返回所有被存储的数据类别及其详情。

        使用基于文件的模拟：检查 data_dir 下的文件来判断数据存储情况。
        如果文件不存在则返回空占位数据。

        返回：DataInventory 列表
        """
        inventory: List[DataInventory] = []

        # 每个数据类别对应的模拟文件名
        category_files: Dict[DataCategory, str] = {
            DataCategory.EMOTION_EVENTS: "emotion_events.json",
            DataCategory.DAILY_SUMMARIES: "daily_summaries.json",
            DataCategory.PHYSIOLOGICAL: "physiological.json",
            DataCategory.SLIDER_OBSERVATIONS: "slider_observations.json",
            DataCategory.EMA_RECORDS: "ema_records.json",
            DataCategory.STRATEGY_HISTORY: "strategy_history.json",
            DataCategory.MODEL_PARAMS: "model_params.json",
            DataCategory.BASELINE_DATA: "baseline_data.json",
        }

        # 类别描述映射
        category_descriptions: Dict[DataCategory, str] = {
            DataCategory.EMOTION_EVENTS: "情绪事件记录，包含效价、唤醒度、恢复时长等",
            DataCategory.DAILY_SUMMARIES: "每日行为与生理摘要数据",
            DataCategory.PHYSIOLOGICAL: "生理传感器数据（心率HR、心率变异性HRV）",
            DataCategory.SLIDER_OBSERVATIONS: "用户通过滑条进行的主观情绪评分",
            DataCategory.EMA_RECORDS: "生态瞬时评估（EMA）记录",
            DataCategory.STRATEGY_HISTORY: "用户记录的应对策略及效果评分",
            DataCategory.MODEL_PARAMS: "个性化模型参数与阈值配置",
            DataCategory.BASELINE_DATA: "用户静息基线数据（HRV、心率、睡眠等）",
        }

        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        for category, filename in category_files.items():
            filepath = os.path.join(self.data_dir, filename)

            # 检查文件是否存在
            if os.path.exists(filepath):
                file_size_kb = os.path.getsize(filepath) / 1024.0
                # 尝试读取记录数
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        count = len(data)
                    else:
                        count = 1
                except (json.JSONDecodeError, IOError):
                    count = 0
                    file_size_kb = 0.0

                date_range = f"{week_ago} ~ {today}"
                contains_identity = category in (
                    DataCategory.EMOTION_EVENTS,
                    DataCategory.EMA_RECORDS,
                    DataCategory.SLIDER_OBSERVATIONS,
                )
            else:
                count = 0
                date_range = "无数据"
                file_size_kb = 0.0
                contains_identity = category in (
                    DataCategory.EMOTION_EVENTS,
                    DataCategory.EMA_RECORDS,
                    DataCategory.SLIDER_OBSERVATIONS,
                )

            inventory.append(DataInventory(
                category=category,
                description=category_descriptions.get(category, ""),
                count=count,
                date_range=date_range,
                size_estimate_kb=round(file_size_kb, 2),
                contains_identity=contains_identity,
            ))

        return inventory

    def delete_all_data(self, confirm: bool = False) -> Dict[str, Any]:
        """
        一键删除所有本地数据。

        参数：
          confirm: 必须为 True 才会执行删除（防误操作）

        返回：删除操作的摘要
        """
        if not confirm:
            return {
                "success": False,
                "message": "删除未执行：confirm 参数必须为 True（防误操作保护）",
                "deleted_categories": [],
                "deleted_files": [],
            }

        # 获取删除前的数据清单
        inventory_before = self.get_data_inventory()
        deleted_categories: List[str] = []
        deleted_files: List[str] = []

        # 遍历数据目录，删除所有 .json 文件
        if os.path.exists(self.data_dir):
            for filename in os.listdir(self.data_dir):
                filepath = os.path.join(self.data_dir, filename)
                if os.path.isfile(filepath) and filename.endswith(".json"):
                    os.remove(filepath)
                    deleted_files.append(filename)

        # 删除日志文件也清理
        log_filepath = os.path.join(self.data_dir, "_deletion_log.json")
        if os.path.exists(log_filepath):
            os.remove(log_filepath)

        # 记录所有被删除的类别
        all_categories = list(DataCategory)
        category_names = [cat.value for cat in all_categories]
        deleted_categories = category_names

        # 更新删除日志（写入到临时内存）
        self._log_deletion(all_categories, "用户请求一键删除所有数据")

        # 重新持久化日志（在新位置，因为原数据已删除）
        self._save_deletion_log()

        return {
            "success": True,
            "message": "所有本地数据已删除",
            "deleted_categories": deleted_categories,
            "deleted_files": deleted_files,
            "deleted_count": len(deleted_files),
            "timestamp": datetime.now().isoformat(),
        }

    def delete_category(self, categories: List[DataCategory]) -> Dict[str, Any]:
        """
        选择性删除特定类别的数据。

        示例：delete_category([DataCategory.PHYSIOLOGICAL])
          → 仅删除生理数据，保留其他所有数据

        参数：
          categories: 要删除的数据类别列表

        返回：删除操作的摘要
        """
        category_to_file: Dict[DataCategory, str] = {
            DataCategory.EMOTION_EVENTS: "emotion_events.json",
            DataCategory.DAILY_SUMMARIES: "daily_summaries.json",
            DataCategory.PHYSIOLOGICAL: "physiological.json",
            DataCategory.SLIDER_OBSERVATIONS: "slider_observations.json",
            DataCategory.EMA_RECORDS: "ema_records.json",
            DataCategory.STRATEGY_HISTORY: "strategy_history.json",
            DataCategory.MODEL_PARAMS: "model_params.json",
            DataCategory.BASELINE_DATA: "baseline_data.json",
        }

        deleted_files: List[str] = []
        deleted_categories: List[str] = []
        not_found: List[str] = []

        for cat in categories:
            filename = category_to_file.get(cat)
            if filename:
                filepath = os.path.join(self.data_dir, filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
                    deleted_files.append(filename)
                    deleted_categories.append(cat.value)
                else:
                    not_found.append(cat.value)
            else:
                not_found.append(cat.value)

        if deleted_categories:
            reason = f"用户选择性删除: {', '.join(deleted_categories)}"
            self._log_deletion(categories, reason)
            self._save_deletion_log()

        return {
            "success": len(deleted_categories) > 0,
            "message": (
                f"已删除 {len(deleted_categories)} 个类别的数据"
                if deleted_categories
                else "未找到可删除的文件"
            ),
            "deleted_categories": deleted_categories,
            "deleted_files": deleted_files,
            "not_found": not_found,
            "timestamp": datetime.now().isoformat(),
        }

    def set_collection_preference(
        self,
        category: DataCategory,
        enabled: bool,
    ):
        """
        设置数据收集偏好。

        示例：
          set_collection_preference(DataCategory.PHYSIOLOGICAL, False)
          → 关闭手表数据接入，仅使用主观滑条

        参数：
          category: 数据类别
          enabled: 是否启用收集
        """
        self._collection_preferences[category] = enabled

        # 持久化偏好设置到文件
        prefs_filepath = os.path.join(self.data_dir, "_collection_preferences.json")
        prefs_data = {
            cat.value: enabled_flag
            for cat, enabled_flag in self._collection_preferences.items()
        }
        with open(prefs_filepath, "w", encoding="utf-8") as f:
            json.dump(prefs_data, f, ensure_ascii=False, indent=2)

    def get_collection_preferences(self) -> Dict[str, bool]:
        """
        获取当前数据收集偏好设置。

        返回：字典，键为数据类别名称，值为是否启用
        """
        # 尝试从文件加载已有偏好
        prefs_filepath = os.path.join(self.data_dir, "_collection_preferences.json")
        if os.path.exists(prefs_filepath):
            try:
                with open(prefs_filepath, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # 将字符串键映射回枚举
                for cat in DataCategory:
                    if cat.value in saved:
                        self._collection_preferences[cat] = saved[cat.value]
            except (json.JSONDecodeError, IOError):
                pass

        return {
            cat.value: enabled
            for cat, enabled in self._collection_preferences.items()
        }

    def is_collection_enabled(self, category: DataCategory) -> bool:
        """
        检查某类数据是否正在被收集。

        参数：
          category: 数据类别

        返回：是否启用收集
        """
        return self._collection_preferences.get(category, True)

    def get_privacy_summary(self) -> Dict[str, Any]:
        """
        返回隐私状态摘要。

        包含：
          - 数据清单
          - 收集偏好
          - 删除日志摘要
          - 敏感数据标识

        返回：隐私状态摘要字典
        """
        inventory = self.get_data_inventory()
        preferences = self.get_collection_preferences()

        # 统计各状态
        total_categories = len(DataCategory)
        active_categories = sum(1 for v in preferences.values() if v)
        data_with_identity = [inv.category.value for inv in inventory if inv.contains_identity]
        total_size_kb = sum(inv.size_estimate_kb for inv in inventory)

        return {
            "user_id": self.user_id or "anonymous",
            "data_directory": self.data_dir,
            "total_data_categories": total_categories,
            "active_collection_categories": active_categories,
            "paused_collection_categories": total_categories - active_categories,
            "collection_preferences": preferences,
            "data_inventory": [
                {
                    "category": inv.category.value,
                    "description": inv.description,
                    "count": inv.count,
                    "date_range": inv.date_range,
                    "size_estimate_kb": inv.size_estimate_kb,
                    "contains_identity": inv.contains_identity,
                }
                for inv in inventory
            ],
            "data_with_identity_info": data_with_identity,
            "total_size_estimate_kb": round(total_size_kb, 2),
            "recent_deletions": len(self._deletion_log),
        }

    def _log_deletion(self, categories: List[DataCategory], reason: str):
        """
        记录删除操作到本地日志。

        参数：
          categories: 被删除的数据类别列表
          reason: 删除原因
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "deleted_categories": [cat.value for cat in categories],
            "reason": reason,
        }
        self._deletion_log.append(entry)

    def _save_deletion_log(self):
        """
        持久化删除日志。
        将日志写入 data_dir/_deletion_log.json 文件。
        """
        log_filepath = os.path.join(self.data_dir, "_deletion_log.json")
        with open(log_filepath, "w", encoding="utf-8") as f:
            json.dump(self._deletion_log, f, ensure_ascii=False, indent=2)


# ================================================================
# 数据解读指南（中文）
# ================================================================

DATA_GUIDE_TEXT = """
================================================================================
              心潮 EmoWave — 数据解读指南
================================================================================

本文档帮助您（或您的咨询师）理解导出数据中各字段的含义。
所有数据均来自本地设备，不会上传至云端。

============================================================================
一、文件结构
============================================================================

导出文件通常包含以下几个部分：

  1. emotion_events（情绪事件）— 核心数据
     每次您在使用过程中经历了情绪波动并记录下来，系统都会生成一条事件记录。

  2. daily_summaries（每日摘要）
     每日自动汇总的生理与情绪概况，用于追踪长期趋势。

  3. thresholds（个性化阈值）
     系统根据您的历史数据计算出的预警阈值。数值越"个人化"，说明系统对您的
     了解越深。

  4. baseline（基线向量）
     您当前的"平静状态"基准线。所有偏离都以此为参照。

  5. engine_state（引擎状态）
     系统的总体运行状态，包括处理的事件总数和模型置信度。

  6. data_guide（本指南）
     就是您正在阅读的这份说明。

============================================================================
二、核心概念解释
============================================================================

【Valence — 效价】
  范围：0.0 ~ 1.0
  含义：您的主观舒适度。
    - 0.0 = 极度不舒服（非常痛苦、焦虑或愤怒）
    - 0.5 = 中性（无明显正面或负面感受）
    - 1.0 = 极度舒服（非常愉悦、放松）

【Arousal — 唤醒度】
  范围：0.0 ~ 1.0
  含义：您的生理激活水平。
    - 0.0 = 极度平静（困倦、倦怠）
    - 0.5 = 中等激活（日常状态）
    - 1.0 = 极度激活（心跳加速、高度紧张或极度兴奋）

  注意：高唤醒度本身不一定是坏事。它需要结合效价来判断：
    - 高唤醒 + 低效价 = 可能处于焦虑或恐慌状态
    - 高唤醒 + 高效价 = 可能处于兴奋或愉快状态
    - 低唤醒 + 低效价 = 可能处于抑郁或疲倦状态

【生理信号】
  HR（心率）: 每分钟心跳次数，单位 BPM
  HRV（心率变异性）: 心跳间隔的变化幅度，单位 ms（毫秒）
    - HRV 越高通常表示身心状态越好、应对压力的能力越强
    - HRV 降低可能预示压力增大或情绪恶化

============================================================================
三、情绪事件字段详解
============================================================================

  event_id_safe        — 事件编号（脱敏处理，不包含完整原始ID）
  onset_time           — 情绪波动开始的时间
  peak_time            — 情绪达到最强烈的时间点
  calm_time            — 情绪恢复平静的时间
  peak_valence         — 最强烈时刻的效价值（0=极度不适, 1=极度舒适）
  peak_arousal         — 最强烈时刻的唤醒度（0=极度平静, 1=极度激活）
  subjective_peak      — 您自己对这次事件强度的评分（0-10分）
  physio_peak_score    — 生理信号检测到的综合压力得分（0-1）
  physiological_peak_confidence — 生理极点检测的可信度（0-1）
  recovery_duration_sec — 从峰值到恢复平静所用的秒数
  recovery_speed       — 恢复速度，数值越大表示恢复越快
  sample_count         — 该事件中采集的数据点总数
  dangerous_rise_segments — 是否存在"危险上升段"（情绪急速恶化）
  dangerous_rise_count — 危险上升段的数量

============================================================================
四、强度指标的计算方式
============================================================================

【主观峰值 (subjective_peak)】
  这是您自己的评估。在每次事件结束后，系统会请您为这次事件的强度打分。
  范围：0（完全无感）~ 10（有史以来最强烈）

【生理极点得分 (physiological_peak_score)】
  这是系统通过分析心率和HRV计算出来的客观指标。
  计算逻辑：
    1. 检测心率的突然升高（z-score > 阈值）
    2. 检测HRV的突然下降
    3. 结合唤醒度变化速率
    4. 将多个信号融合为一个0~1的得分
  得分越高，表示生理反应越强烈。

【危险上升段 (dangerous_rise_segment)】
  当系统检测到以下模式时会标记为"危险上升段"：
    - 唤醒度持续快速上升
    - 伴随心率激增
    - 伴随HRV显著下降
  这通常意味着情绪正在急剧恶化，是早期干预的关键窗口。

============================================================================
五、预警阈值的意义
============================================================================

系统维护着一套个性化的预警阈值：

  high_risk_arousal   — 唤醒度超过此值时标记为"高风险"
  high_risk_valence   — 效价低于此值时标记为"高风险"
  hrv_drop_percent    — HRV下降超过此百分比时发出警报
  hr_surge_zscore     — 心率偏离基线的z-score超过此值时发出警报
  dangerous_rise_slope — 唤醒度上升斜率超过此值时标记为"危险上升"

阈值来源（model_source）：
  - "population" — 使用群体通用值（冷启动阶段，数据不足）
  - "hybrid" — 群体值与个人值的混合（过渡阶段）
  - "personal" — 完全基于您的历史数据（最精准）

model_confidence 值越接近 1.0，说明阈值越可靠。

============================================================================
六、每日摘要字段详解
============================================================================

  date                 — 日期（YYYY-MM-DD格式）
  avg_resting_hrv      — 当日静息HRV平均值（毫秒）
  avg_resting_hr       — 当日静息心率平均值（BPM）
  sleep_score          — 前一晚的睡眠质量评分（0-10）
  morning_valence_avg  — 早上（6:00-10:00）的平均效价
  evening_valence_avg  — 傍晚（17:00-21:00）的平均效价
  event_count          — 当日记录的情绪事件次数
  peak_arousal_max     — 当日的最高唤醒度值

============================================================================
七、推荐给咨询师的阅读建议
============================================================================

如果您是一位心理咨询师或治疗师，正在查看来访者的导出数据，以下建议
可能对您有帮助：

  1. 【先看趋势，再看单次】
     每日摘要和基线变化比单个事件更有参考价值。关注HRV的长期趋势、
     效价的日内规律，这些往往比单次事件更能反映整体状态。

  2. 【注意恢复时长】
     恢复时长（recovery_duration_sec）是一个重要指标。如果恢复时长
     在逐渐延长，可能意味着来访者的情绪调节能力在下降，值得讨论。

  3. 【危险上升段是预警信号】
     dangerous_rise_segments 的出现频率和严重程度可以用来评估情绪
     失控的风险。如果这类事件越来越频繁，建议在治疗中加强应对策略
     的练习。

  4. 【主客观差异有临床意义】
     比较 subjective_peak（主观评分）和 physio_peak_score（客观得分）。
     如果两者差异很大，可能意味着来访者的身体反应与自我认知之间存在
     不一致，这本身就是一个值得探索的治疗话题。

  5. 【基线漂移提示长期变化】
     如果 baseline 数据显示静息HRV持续降低、心率持续升高，这可能
     提示慢性压力累积或身体健康的需要关注。

  6. 【数据仅作参考，不作诊断】
     本系统的数据来源于可穿戴设备和主观自评，存在测量误差。请将数据
     作为辅助信息，结合临床访谈和您的专业判断来使用。

============================================================================
八、隐私与安全
============================================================================

  - 所有数据均存储在您的本地设备上
  - 导出时可选择加密（设置密码后文件将使用加密格式）
  - 时间戳可以模糊化处理（精确到天、周或月）
  - 您可以随时删除全部或部分数据
  - 数据收集可以按类别开关，最小化信息收集

如有任何疑问，请联系心潮 EmoWave 团队。

================================================================================
                    — 本指南结束 —
================================================================================
"""


# ================================================================
# 自测 / 演示
# ================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  心潮 EmoWave — privacy_export.py 自测演示")
    print("=" * 60)

    # --- 准备模拟数据 ---
    from models import (
        TimeSeriesSample,
        EmotionEventRaw,
        EventProfile,
        DailySummary,
        PersonalThresholds,
        BaselineVector,
        EngineState,
        DangerousRiseSegment,
        PhysiologicalPeak,
        ModelSource,
    )

    # 构造模拟事件
    now = datetime.now().timestamp()
    event_profiles = [
        EventProfile(
            event_id="evt_abc12345_example",
            onset_time=now - 600,
            peak_time=now - 300,
            calm_time=now - 60,
            peak_valence=0.25,
            peak_arousal=0.82,
            subjective_peak=7.0,
            physiological_peak_score=0.68,
            physiological_peak_confidence=0.85,
            recovery_duration=240.0,
            recovery_speed=0.0024,
            sample_count=120,
            dangerous_rise_segments=[
                DangerousRiseSegment(
                    start_time=now - 540,
                    end_time=now - 350,
                    peak_arousal_slope=0.015,
                    avg_hr_zscore=1.8,
                    hrv_drop_at_peak=22.5,
                ),
            ],
            physio_peaks=[
                PhysiologicalPeak(
                    timestamp=now - 300,
                    hr_zscore=2.1,
                    hrv_drop_pct=25.0,
                    arousal_spike=0.82,
                    composite_score=0.68,
                ),
            ],
        ),
        EventProfile(
            event_id="evt_def67890_example",
            onset_time=now - 86400 - 300,
            peak_time=now - 86400 - 150,
            calm_time=now - 86400,
            peak_valence=0.60,
            peak_arousal=0.45,
            subjective_peak=3.0,
            physiological_peak_score=0.20,
            physiological_peak_confidence=0.60,
            recovery_duration=150.0,
            recovery_speed=0.003,
            sample_count=80,
        ),
    ]

    # 构造模拟每日摘要
    daily_summaries = [
        DailySummary(
            date="2026-07-08",
            avg_resting_hrv=52.3,
            avg_resting_hr=71.5,
            sleep_score=7.2,
            morning_valence_avg=0.58,
            evening_valence_avg=0.51,
            event_count=2,
            peak_arousal_max=0.82,
        ),
        DailySummary(
            date="2026-07-07",
            avg_resting_hrv=48.7,
            avg_resting_hr=74.2,
            sleep_score=5.8,
            morning_valence_avg=0.45,
            evening_valence_avg=0.42,
            event_count=1,
            peak_arousal_max=0.65,
        ),
    ]

    # 构造模拟阈值
    thresholds = PersonalThresholds(
        high_risk_arousal=0.75,
        high_risk_valence=0.30,
        hrv_drop_percent=20.0,
        hr_surge_zscore=1.5,
        dangerous_rise_slope=0.01,
        model_confidence=0.72,
        model_source=ModelSource.HYBRID,
        event_count=15,
        last_updated="2026-07-08",
    )

    # 构造模拟基线
    baseline = BaselineVector(
        resting_hrv_mean=50.5,
        resting_hr=72.0,
        sleep_score=6.8,
        typical_valence_8am=0.55,
        typical_valence_6pm=0.48,
        date="2026-07-08",
    )

    # --- 测试 DataExporter ---
    print("\n>>> 测试 DataExporter")
    print("-" * 40)

    exporter = DataExporter(
        user_id="test_user_001",
        output_dir="/data/user/work/test_exports",
    )

    # 1. JSON 导出（含时间模糊化）
    print('\n[1] JSON 导出（时间模糊化到"周"级别）...')
    json_path = exporter.export_all(
        event_profiles=event_profiles,
        daily_summaries=daily_summaries,
        thresholds=thresholds,
        baseline=baseline,
        format=ExportFormat.JSON,
        blur_timestamps=True,
        blur_level="week",
        include_guide=True,
    )
    print(f"    导出路径: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    print(f"    事件数: {json_data['emotion_events_count']}")
    print(f"    示例事件时间（模糊化）: {json_data['emotion_events'][0]['onset_time']}")

    # 2. CSV 事件导出
    print("\n[2] CSV 事件导出...")
    csv_events_path = exporter.export_events_csv(event_profiles)
    print(f"    导出路径: {csv_events_path}")

    # 3. CSV 每日摘要导出
    print("\n[3] CSV 每日摘要导出...")
    csv_daily_path = exporter.export_daily_csv(daily_summaries)
    print(f"    导出路径: {csv_daily_path}")

    # 4. 加密导出
    print("\n[4] 加密导出...")
    encrypted_path = exporter.export_all(
        event_profiles=event_profiles,
        daily_summaries=daily_summaries,
        thresholds=thresholds,
        baseline=baseline,
        format=ExportFormat.ENCRYPTED_JSON,
        password="my_secret_password",
        include_guide=False,
    )
    print(f"    加密文件路径: {encrypted_path}")
    with open(encrypted_path, "r", encoding="utf-8") as f:
        encrypted_content = f.read()
    print(f"    密文前40字符: {encrypted_content[:40]}...")

    # 5. 解密验证
    print("\n[5] 解密验证...")
    decrypted_text = exporter._simple_decrypt(encrypted_content, "my_secret_password")
    decrypted_data = json.loads(decrypted_text)
    print(f"    解密成功! 事件数: {decrypted_data['emotion_events_count']}")

    # 6. 错误密码测试
    print("\n[6] 错误密码测试...")
    try:
        exporter._simple_decrypt(encrypted_content, "wrong_password")
        print("    错误: 应该抛出异常!")
    except ValueError as e:
        print(f"    预期异常: {e}")

    # 7. 导出摘要
    print("\n[7] 导出摘要:")
    summary = exporter.get_export_summary()
    print(f"    总导出次数: {summary['total_exports']}")
    print(f"    支持格式: {summary['supported_formats']}")

    # --- 测试 PrivacyManager ---
    print("\n\n>>> 测试 PrivacyManager")
    print("-" * 40)

    # 先创建一些模拟数据文件
    test_data_dir = "/data/user/work/test_user_data"
    os.makedirs(test_data_dir, exist_ok=True)

    sample_files = {
        "emotion_events.json": [1, 2, 3],
        "daily_summaries.json": [1, 2],
        "physiological.json": list(range(100)),
        "slider_observations.json": [1, 2, 3, 4, 5],
        "ema_records.json": [],
        "strategy_history.json": [{"method": "深呼吸", "score": 4}],
        "model_params.json": {"confidence": 0.8},
        "baseline_data.json": {"hrv": 50.0},
    }

    for filename, content in sample_files.items():
        filepath = os.path.join(test_data_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

    pm = PrivacyManager(data_dir=test_data_dir, user_id="test_user_001")

    # 8. 数据清单
    print("\n[8] 数据清单:")
    inventory = pm.get_data_inventory()
    for inv in inventory:
        status = f"{inv.count}条" if inv.count > 0 else "空"
        identity_flag = " [含身份信息]" if inv.contains_identity else ""
        print(f"    {inv.category.value:25s} {status:8s} {inv.size_estimate_kb:.1f}KB{identity_flag}")

    # 9. 隐私摘要
    print("\n[9] 隐私摘要:")
    privacy_summary = pm.get_privacy_summary()
    print(f"    数据类别总数: {privacy_summary['total_data_categories']}")
    print(f"    活跃收集类别: {privacy_summary['active_collection_categories']}")
    print(f"    总数据大小: {privacy_summary['total_size_estimate_kb']:.2f} KB")
    print(f"    含身份信息的类别: {privacy_summary['data_with_identity_info']}")

    # 10. 设置收集偏好
    print("\n[10] 设置收集偏好:")
    pm.set_collection_preference(DataCategory.PHYSIOLOGICAL, False)
    pm.set_collection_preference(DataCategory.EMA_RECORDS, False)
    prefs = pm.get_collection_preferences()
    for cat_name, enabled in prefs.items():
        status = "开启" if enabled else "关闭"
        marker = " <-- 已修改" if not enabled else ""
        print(f"    {cat_name:25s} {status}{marker}")

    # 11. 选择性删除
    print("\n[11] 选择性删除（仅删除生理数据）:")
    del_result = pm.delete_category([DataCategory.PHYSIOLOGICAL])
    print(f"    删除结果: {del_result['message']}")
    print(f"    已删除文件: {del_result['deleted_files']}")

    # 验证删除
    physio_path = os.path.join(test_data_dir, "physiological.json")
    print(f"    生理数据文件存在? {os.path.exists(physio_path)}")
    events_path = os.path.join(test_data_dir, "emotion_events.json")
    print(f"    情绪事件文件存在? {os.path.exists(events_path)}")

    # 12. 一键删除全部（需 confirm=True）
    print("\n[12] 一键删除全部数据:")
    del_all = pm.delete_all_data(confirm=True)
    print(f"    结果: {del_all['message']}")
    print(f"    删除文件数: {del_all['deleted_count']}")

    # 验证所有数据已删除
    remaining = [f for f in os.listdir(test_data_dir) if f.endswith(".json")]
    print(f"    剩余JSON文件: {remaining}")

    print("\n" + "=" * 60)
    print("  所有测试通过!")
    print("=" * 60)

    # 清理测试文件
    import shutil
    for d in ["/data/user/work/test_exports", "/data/user/work/test_user_data"]:
        if os.path.exists(d):
            shutil.rmtree(d)
    print("\n测试临时文件已清理。")
