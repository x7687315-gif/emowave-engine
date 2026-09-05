"""Observation — 系统观察到的事实。

Observation 是 EmoWave 数据流的入口，代表"系统看到了什么"，而不是
"用户处于什么情绪"。它可能来自：
  - 用户在 UI 上拖动滑条（source=user）
  - 手表 / 传感器采集的生理信号（source=sensor）
  - 从外部导入的历史数据（source=import）
  - Amiya 等 Agent 转发的对话推断（source=agent）

设计约束（详见 REFACTOR_PLAN.md §4.1 与 §13）：
  - **append-only**：Observation 一旦写入永不修改，即使用户事后纠正
    也另建 UserCorrection 记录，不覆盖原始事实。
  - **所有字段可选**：一个 Observation 可以只带 valence（用户拖了滑条），
    也可以只带 hr/hrv（手表自动采样），甚至只带 sleep（早晨回顾）。
  - **零依赖**：不 import numpy，只用 dataclass + typing + enum。
  - **可序列化**：to_dict / from_dict 支持 JSON 落盘与跨进程传输。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


class ObservationSource(str, Enum):
    """Observation 来源。用字符串枚举以便 JSON 序列化。"""

    USER = "user"        # 用户在 UI 上主动输入（滑条 / 手动打分）
    SENSOR = "sensor"    # 手表 / 手环 / 摄像头等传感器自动采集
    IMPORT = "import"    # 从 CSV / JSON / 其他系统导入的历史数据
    AGENT = "agent"      # Amiya 等外部 Agent 转发的推断
    MODEL = "model"      # 模型自身回填（罕见，仅用于诊断）


# valence / arousal 的合法区间。所有归一化维度统一使用 [0, 1]。
_V_MIN = 0.0
_V_MAX = 1.0


def _clip_unit(x: Optional[float]) -> Optional[float]:
    """把 [0, 1] 维度的值裁剪到合法区间。None 或非有限值（NaN/Inf）→ None。

    为什么需要 clip：用户从 UI 传来的滑条值可能是 0-100 的整数被误传，
    传感器可能因噪声返回 -0.02 或 1.05。Core 层必须自我保护，
    非法输入不应摧毁后续计算（REFACTOR_PLAN.md §12 低配置原则）。

    为什么 NaN/Inf → None 而非 0/1：`nan < 0` 与 `nan > 1` 都为 False，
    若只靠上下界裁剪，NaN 会原样穿透并永久污染 Kalman 状态
    （F·NaN=NaN，此后每条观察都产出 NaN）。坏读数/除零的正确语义是
    "未观察到"（None），让 estimator 跳过它而非把它当成真实情绪值。
    """
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    if x < _V_MIN:
        return _V_MIN
    if x > _V_MAX:
        return _V_MAX
    return x


@dataclass(frozen=True)
class Observation:
    """一次系统观察。

    不可变（frozen=True）：这是"原始数据不可变"原则的代码级保证。
    任何试图修改已存 Observation 的行为都会抛 FrozenInstanceError，
    这样即使上层代码写错也不会污染历史事实。

    Attributes:
        timestamp: Unix 时间戳（秒，float）。所有时间字段统一口径。
        valence: 效价 [0, 1]，0=极不舒服，1=极舒服。None=未观察。
        arousal: 唤醒 [0, 1]，0=极困倦，1=极兴奋。None=未观察。
        hr: 心率 BPM，None=未观察或未连接传感器。
        hrv: HRV RMSSD ms，None=未观察。
        sleep: 前夜睡眠评分 [0, 10]，None=未记录。
        activity: 活动强度 [0, 1]（静止→剧烈），None=未观察。
        source: 观察来源，见 ObservationSource。
        confidence: 该观察自身的可靠度 [0, 1]，用户主动输入通常 1.0，
            传感器推断可能 0.6-0.8，agent 转发通常更低。默认 1.0。
        meta: 附加元信息（如设备 ID、原始 payload 引用）。可空。
    """

    timestamp: float
    valence: Optional[float] = None
    arousal: Optional[float] = None
    hr: Optional[float] = None
    hrv: Optional[float] = None
    sleep: Optional[float] = None
    activity: Optional[float] = None
    source: ObservationSource = ObservationSource.USER
    confidence: float = 1.0
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验并归一化字段。

        frozen=True 阻止常规赋值，因此用 object.__setattr__ 写回裁剪后的值。
        这是 dataclass 中做"构造时归一化"的标准手法。
        """
        if not math.isfinite(self.timestamp) or self.timestamp <= 0:
            raise ValueError(
                f"Observation.timestamp 必须为正的有限 Unix 时间戳，得到 {self.timestamp}"
            )

        # 归一化 [0, 1] 维度（_clip_unit 已把 NaN/Inf 归为 None）
        object.__setattr__(self, "valence", _clip_unit(self.valence))
        object.__setattr__(self, "arousal", _clip_unit(self.arousal))
        object.__setattr__(self, "activity", _clip_unit(self.activity))

        # 归一化 confidence 到 [0, 1]；非有限值回退安全默认 1.0
        c = float(self.confidence)
        if not math.isfinite(c):
            c = 1.0
        if c < 0.0:
            c = 0.0
        elif c > 1.0:
            c = 1.0
        object.__setattr__(self, "confidence", c)

        # 生理字段：非有限值视为未观察（None），有限值做非负检查
        # （不做上限裁剪：BPM 可以到 220+）
        for name in ("hr", "hrv"):
            v = getattr(self, name)
            if v is not None:
                v = float(v)
                if not math.isfinite(v):
                    object.__setattr__(self, name, None)
                    continue
                if v < 0:
                    raise ValueError(f"Observation.{name} 不能为负，得到 {v}")
                object.__setattr__(self, name, v)

        if self.sleep is not None:
            s = float(self.sleep)
            if not math.isfinite(s):
                object.__setattr__(self, "sleep", None)
            elif s < 0 or s > 10:
                raise ValueError(f"Observation.sleep 必须在 [0, 10]，得到 {s}")
            else:
                object.__setattr__(self, "sleep", s)

        # source 允许传字符串，自动转 Enum
        if not isinstance(self.source, ObservationSource):
            object.__setattr__(self, "source", ObservationSource(self.source))

    def has_emotion_channel(self) -> bool:
        """该观察是否至少提供了 valence 或 arousal 之一。

        Estimator 只对含情绪通道的观察做状态更新；纯生理观察会走
        控制输入路径（详见 Phase 2 的 estimator 实现）。
        """
        return self.valence is not None or self.arousal is not None

    def has_physio_channel(self) -> bool:
        """该观察是否至少提供了 hr 或 hrv 之一。"""
        return self.hr is not None or self.hrv is not None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 JSON 兼容 dict。Enum 转字符串值。"""
        d = asdict(self)
        # asdict 会把 Enum 保留为 Enum 实例，需手动转字符串
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Observation":
        """从 dict 反序列化。容忍未知字段（前向兼容）。"""
        known = {f for f in cls.__dataclass_fields__.keys()}
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)
