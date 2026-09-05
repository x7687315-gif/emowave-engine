"""tier — 能力档位探测与降级（Lite Runtime 的核心机制）。

ARCHITECTURE LIGHTWEIGHT part2 §5.2 能力分层（Tier）：
"低配设备流畅运行"的核心机制——不是靠优化让它跑得动，而是按档位决定跑什么。

    | Tier | 目标              | L1滤波 | L2曲线 | L3学习 | 存储   | 节点数 |
    | T0   | Amiya插件/极低配  | ✅     | ❌     | ❌     | 内存   | —     |
    | T1   | 低端手机/低配PC   | ✅     | ✅降级 | ❌     | SQLite | ~150  |
    | T2   | 中高端设备        | ✅     | ✅     | ✅     | SQLite | ~300  |

关键原则：**Tier 只削减"非实时的、批量的"功能，L1 因果滤波在任何档位都保留。**
这保证情绪追踪的核心体验在所有设备上一致。

降级触发（part2 §5.2）：
    EMOWAVE_TIER=auto        默认：自动探测
    EMOWAVE_TIER=0|1|2       显式锁定
    auto 探测依据（全部标准库可得）：os.cpu_count() / 平台 / 可用内存 / 省电模式

REFACTOR_PLAN.md §18 Graceful Degradation：任何可选能力都必须能失效而不摧毁
核心系统。Tier 是这一原则在性能维度的落地——设备能力不足时优雅降级而非崩溃。

设计约束：零依赖（os / sys / ctypes 均为标准库）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, Optional


class Tier(IntEnum):
    """能力档位。用 IntEnum 便于比较（T0 < T1 < T2）与环境变量解析。"""

    EMBED = 0    # T0：Amiya 插件 / 极低配设备，仅 L1 因果滤波，内存存储
    LITE = 1     # T1：低端手机 / 低配 PC，L1 + L2（降级），SQLite
    FULL = 2     # T2：中高端设备，完整四层架构


# 环境变量名
ENV_TIER = "EMOWAVE_TIER"
ENV_BATTERY_SAVER = "EMOWAVE_BATTERY_SAVER"

# 注入参数哨兵：区分"未提供（实际探测）"与显式 None（探测失败/未知）
_UNSET = object()


@dataclass(frozen=True)
class TierCapabilities:
    """某档位启用的能力集合（part2 §5.2 能力分层表）。

    Attributes:
        tier: 档位。
        enable_l1_filtering: L1 因果滤波（所有档位都 True）。
        enable_l2_curve: L2 RTS 平滑 + 曲线可视化。
        enable_l2_editing: L2 曲线编辑（拖拽）。
        enable_l3_learning: L3 个性化学习。
        enable_l4_sovereignty: L4 基线主权。
        storage_backend: "memory" | "sqlite"。
        curve_node_count: 曲线节点数（降采样目标，part2 §2.4）。
        max_history_seconds: 内存中保留的历史时长上限。
        async_persistence: 是否异步/批量持久化。
    """

    tier: Tier
    enable_l1_filtering: bool = True
    enable_l2_curve: bool = True
    enable_l2_editing: bool = True
    enable_l3_learning: bool = True
    enable_l4_sovereignty: bool = True
    storage_backend: str = "sqlite"
    curve_node_count: int = 300
    max_history_seconds: float = 86400.0
    async_persistence: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": int(self.tier),
            "enable_l1_filtering": self.enable_l1_filtering,
            "enable_l2_curve": self.enable_l2_curve,
            "enable_l2_editing": self.enable_l2_editing,
            "enable_l3_learning": self.enable_l3_learning,
            "enable_l4_sovereignty": self.enable_l4_sovereignty,
            "storage_backend": self.storage_backend,
            "curve_node_count": self.curve_node_count,
            "max_history_seconds": self.max_history_seconds,
            "async_persistence": self.async_persistence,
        }


# 三档能力预设（part2 §5.2 表）
CAPABILITIES: Dict[Tier, TierCapabilities] = {
    Tier.EMBED: TierCapabilities(
        tier=Tier.EMBED,
        enable_l1_filtering=True,   # L1 任何档位都保留
        enable_l2_curve=False,
        enable_l2_editing=False,
        enable_l3_learning=False,
        enable_l4_sovereignty=False,
        storage_backend="memory",
        curve_node_count=0,
        max_history_seconds=600.0,  # 仅保留 10 分钟滚动窗口
        async_persistence=False,
    ),
    Tier.LITE: TierCapabilities(
        tier=Tier.LITE,
        enable_l1_filtering=True,
        enable_l2_curve=True,        # 曲线可视化保留（用户最需要）
        enable_l2_editing=True,
        enable_l3_learning=False,    # 关闭个性化学习（需跨事件累积计算）
        enable_l4_sovereignty=True,
        storage_backend="sqlite",
        curve_node_count=150,        # 节点数减半
        max_history_seconds=86400.0,
        async_persistence=True,
    ),
    Tier.FULL: TierCapabilities(
        tier=Tier.FULL,
        enable_l1_filtering=True,
        enable_l2_curve=True,
        enable_l2_editing=True,
        enable_l3_learning=True,
        enable_l4_sovereignty=True,
        storage_backend="sqlite",
        curve_node_count=300,
        max_history_seconds=86400.0 * 7,
        async_persistence=True,
    ),
}


def capabilities_for(tier: Tier) -> TierCapabilities:
    """返回指定档位的能力集合。"""
    return CAPABILITIES[tier]


# ============================================================
# 设备探测（全部标准库）
# ============================================================


def cpu_count() -> int:
    """CPU 核心数（os.cpu_count，失败回退 1）。"""
    try:
        return max(1, os.cpu_count() or 1)
    except Exception:
        return 1


def total_memory_mb() -> Optional[float]:
    """物理内存总量（MB）。跨平台，失败返回 None。

    Windows: ctypes GlobalMemoryStatusEx
    Linux/macOS: os.sysconf
    """
    try:
        if sys.platform.startswith("win"):
            return _windows_memory_mb()
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PAGE_SIZE")
            total = os.sysconf("SC_PHYS_PAGES")
            if pages > 0 and total > 0:
                return pages * total / (1024 * 1024)
    except Exception:
        return None
    return None


def _windows_memory_mb() -> Optional[float]:
    """Windows 物理内存（ctypes GlobalMemoryStatusEx）。"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys / (1024 * 1024)
    except Exception:
        return None


def is_mobile() -> bool:
    """是否为移动平台（Android/iOS）。

    Flet 移动端通过 serious_python 内嵌 CPython，sys.platform 可能仍报 linux/darwin，
    因此也检查环境变量与常见移动端标志。
    """
    p = sys.platform.lower()
    if "android" in p or "ios" in p:
        return True
    # Flet / serious_python 移动端常设的环境标志
    if os.getenv("ANDROID_ROOT") or os.getenv("ANDROID_DATA"):
        return True
    if os.getenv("EMOWAVE_MOBILE") == "1":
        return True
    return False


def is_battery_saver() -> bool:
    """是否处于省电模式（移动端强制 ≤ T1，part2 §5.2）。"""
    env = os.getenv(ENV_BATTERY_SAVER)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    return False


# ============================================================
# 档位决策
# ============================================================


def detect_tier(
    override: Optional[str] = None,
    cpus: Any = _UNSET,
    memory_mb: Any = _UNSET,
    mobile: Any = _UNSET,
    battery_saver: Any = _UNSET,
) -> Tier:
    """探测应使用的能力档位（part2 §5.2 auto 探测）。

    决策优先级：
      1. 显式 override（EMOWAVE_TIER=0|1|2）→ 直接锁定
      2. 移动端省电模式 → 强制 ≤ T1
      3. 自动探测：CPU / 内存 / 平台综合评分

    自动探测规则（保守，宁可降档不可卡顿）：
      - 内存 < 1024 MB 或 CPU == 1 → T0（极低配）
      - 移动端 或 内存 < 3072 MB 或 CPU <= 2 → T1（低配）
      - 其余 → T2（完整）

    Args:
        override: 显式档位字符串（"0"/"1"/"2"/"auto"/None）。None 时读环境变量。
        cpus/memory_mb/mobile/battery_saver: 可注入的探测值（测试用）。
            默认 _UNSET 表示"未提供，实际探测"；显式传 None（如 memory_mb=None）
            表示"探测失败/未知"，会触发保守降档。

    Returns:
        Tier
    """
    # 1. 显式覆盖
    if override is None:
        override = os.getenv(ENV_TIER, "auto")
    override = (override or "auto").strip().lower()
    if override in ("0", "1", "2"):
        return Tier(int(override))
    # "auto" 或其他 → 走自动探测

    # 注入或探测（_UNSET 才探测；显式 None 保留为"未知"）
    if cpus is _UNSET:
        cpus = cpu_count()
    if memory_mb is _UNSET:
        memory_mb = total_memory_mb()
    if mobile is _UNSET:
        mobile = is_mobile()
    if battery_saver is _UNSET:
        battery_saver = is_battery_saver()

    # 2. 省电模式强制 ≤ T1
    cap = Tier.FULL
    if battery_saver:
        cap = Tier.LITE

    # 3. 自动探测
    if memory_mb is not None and memory_mb < 1024:
        detected = Tier.EMBED
    elif cpus <= 1:
        detected = Tier.EMBED
    elif mobile:
        detected = Tier.LITE
    elif memory_mb is not None and memory_mb < 3072:
        detected = Tier.LITE
    elif cpus <= 2:
        detected = Tier.LITE
    else:
        detected = Tier.FULL

    # 内存探测失败时保守取 T1（不确定就不上 T2）
    if memory_mb is None and detected == Tier.FULL:
        detected = Tier.LITE

    # 应用省电上限
    return Tier(min(int(detected), int(cap)))


def resolve_capabilities(override: Optional[str] = None) -> TierCapabilities:
    """探测档位并返回对应能力集合（一站式入口）。"""
    return capabilities_for(detect_tier(override=override))
