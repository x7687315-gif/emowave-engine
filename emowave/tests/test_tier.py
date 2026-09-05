"""Tier 能力档位探测单元测试。

覆盖 ARCHITECTURE LIGHTWEIGHT part2 §5.2 能力分层与 REFACTOR_PLAN §18 优雅降级：
  - 三档能力预设（T0/T1/T2）正确
  - L1 因果滤波在任何档位都保留（核心体验一致）
  - 显式 override（EMOWAVE_TIER=0|1|2）
  - auto 探测：CPU/内存/平台/省电模式
  - 省电模式强制 ≤ T1
  - 探测失败时保守降档
"""

import pytest

from emowave.core import tier
from emowave.core.tier import (
    Tier,
    TierCapabilities,
    capabilities_for,
    detect_tier,
    resolve_capabilities,
)


# ============================================================
# Tier 枚举与能力预设
# ============================================================


def test_tier_ordering():
    """Tier 可比较（IntEnum），T0 < T1 < T2。"""
    assert Tier.EMBED < Tier.LITE < Tier.FULL
    assert int(Tier.EMBED) == 0
    assert int(Tier.FULL) == 2


def test_capabilities_t0_embed():
    """T0：仅 L1，内存存储，无曲线/学习（part2 §5.2 表）。"""
    caps = capabilities_for(Tier.EMBED)
    assert caps.enable_l1_filtering is True   # L1 任何档位保留
    assert caps.enable_l2_curve is False
    assert caps.enable_l3_learning is False
    assert caps.storage_backend == "memory"
    assert caps.curve_node_count == 0


def test_capabilities_t1_lite():
    """T1：L1 + L2（降级，节点减半），关闭 L3 学习，SQLite。"""
    caps = capabilities_for(Tier.LITE)
    assert caps.enable_l1_filtering is True
    assert caps.enable_l2_curve is True
    assert caps.enable_l2_editing is True
    assert caps.enable_l3_learning is False   # 关闭个性化学习
    assert caps.storage_backend == "sqlite"
    assert caps.curve_node_count == 150       # 节点减半


def test_capabilities_t2_full():
    """T2：完整四层，节点 300。"""
    caps = capabilities_for(Tier.FULL)
    assert caps.enable_l1_filtering is True
    assert caps.enable_l2_curve is True
    assert caps.enable_l3_learning is True
    assert caps.enable_l4_sovereignty is True
    assert caps.storage_backend == "sqlite"
    assert caps.curve_node_count == 300


def test_l1_filtering_preserved_in_all_tiers():
    """关键不变量：L1 因果滤波在所有档位都启用（part2 §5.2）。"""
    for t in Tier:
        assert capabilities_for(t).enable_l1_filtering is True


def test_capabilities_monotonic():
    """高档位能力是低档位的超集（曲线/学习逐项递增）。"""
    c0 = capabilities_for(Tier.EMBED)
    c1 = capabilities_for(Tier.LITE)
    c2 = capabilities_for(Tier.FULL)
    # 节点数递增
    assert c0.curve_node_count <= c1.curve_node_count <= c2.curve_node_count
    # 学习能力递增
    assert int(c0.enable_l3_learning) <= int(c1.enable_l3_learning) <= int(c2.enable_l3_learning)


def test_capabilities_to_dict():
    d = capabilities_for(Tier.LITE).to_dict()
    assert d["tier"] == 1
    assert d["storage_backend"] == "sqlite"
    assert d["curve_node_count"] == 150


# ============================================================
# detect_tier：显式 override
# ============================================================


@pytest.mark.parametrize("override,expected", [
    ("0", Tier.EMBED),
    ("1", Tier.LITE),
    ("2", Tier.FULL),
])
def test_detect_tier_explicit_override(override, expected):
    """EMOWAVE_TIER=0|1|2 直接锁定档位，忽略探测。"""
    assert detect_tier(override=override) == expected


def test_detect_tier_override_ignores_device():
    """显式 override 优先于设备探测（即使设备很强，override=0 也锁 T0）。"""
    assert detect_tier(override="0", cpus=16, memory_mb=32768, mobile=False) == Tier.EMBED


def test_detect_tier_override_case_insensitive():
    assert detect_tier(override="2") == Tier.FULL


# ============================================================
# detect_tier：auto 探测
# ============================================================


def test_detect_tier_auto_high_end():
    """高配（多核 + 大内存 + 非移动）→ T2。"""
    assert detect_tier(override="auto", cpus=8, memory_mb=16384, mobile=False,
                       battery_saver=False) == Tier.FULL


def test_detect_tier_auto_low_memory():
    """内存 <1024MB → T0（极低配）。"""
    assert detect_tier(override="auto", cpus=4, memory_mb=512, mobile=False,
                       battery_saver=False) == Tier.EMBED


def test_detect_tier_auto_single_cpu():
    """单核 CPU → T0。"""
    assert detect_tier(override="auto", cpus=1, memory_mb=8192, mobile=False,
                       battery_saver=False) == Tier.EMBED


def test_detect_tier_auto_mobile():
    """移动端 → 至多 T1。"""
    assert detect_tier(override="auto", cpus=8, memory_mb=8192, mobile=True,
                       battery_saver=False) == Tier.LITE


def test_detect_tier_auto_mid_memory():
    """内存 <3072MB → T1（低配）。"""
    assert detect_tier(override="auto", cpus=4, memory_mb=2048, mobile=False,
                       battery_saver=False) == Tier.LITE


def test_detect_tier_auto_dual_cpu():
    """双核 → T1。"""
    assert detect_tier(override="auto", cpus=2, memory_mb=8192, mobile=False,
                       battery_saver=False) == Tier.LITE


# ============================================================
# 省电模式强制 ≤ T1（part2 §5.2）
# ============================================================


def test_battery_saver_caps_at_lite():
    """省电模式即使设备是 T2 也强制降到 T1。"""
    assert detect_tier(override="auto", cpus=8, memory_mb=16384, mobile=False,
                       battery_saver=True) == Tier.LITE


def test_battery_saver_does_not_raise_tier():
    """省电模式不会把已经更低的档位抬高（T0 仍 T0）。"""
    assert detect_tier(override="auto", cpus=1, memory_mb=512, mobile=False,
                       battery_saver=True) == Tier.EMBED


# ============================================================
# 探测失败的保守降级
# ============================================================


def test_unknown_memory_conservative():
    """内存探测失败（None）时保守取 T1，不上 T2（不确定就不冒进）。"""
    assert detect_tier(override="auto", cpus=8, memory_mb=None, mobile=False,
                       battery_saver=False) == Tier.LITE


def test_unknown_memory_with_low_cpu_still_embed():
    """内存未知但单核 → T0。"""
    assert detect_tier(override="auto", cpus=1, memory_mb=None, mobile=False,
                       battery_saver=False) == Tier.EMBED


# ============================================================
# 设备探测函数（标准库）
# ============================================================


def test_cpu_count_positive():
    assert tier.cpu_count() >= 1


def test_total_memory_mb_reasonable_or_none():
    mem = tier.total_memory_mb()
    # 要么 None（探测失败），要么合理范围（>100MB）
    assert mem is None or mem > 100


def test_is_mobile_returns_bool():
    assert isinstance(tier.is_mobile(), bool)


def test_is_battery_saver_default_false(monkeypatch):
    monkeypatch.delenv(tier.ENV_BATTERY_SAVER, raising=False)
    assert tier.is_battery_saver() is False


def test_is_battery_saver_env_true(monkeypatch):
    monkeypatch.setenv(tier.ENV_BATTERY_SAVER, "1")
    assert tier.is_battery_saver() is True


def test_detect_tier_reads_env_override(monkeypatch):
    """detect_tier 无显式 override 时读 EMOWAVE_TIER 环境变量。"""
    monkeypatch.setenv(tier.ENV_TIER, "1")
    assert detect_tier() == Tier.LITE


def test_resolve_capabilities():
    caps = resolve_capabilities(override="2")
    assert caps.tier == Tier.FULL
    assert caps.enable_l3_learning is True


def test_resolve_capabilities_t0():
    caps = resolve_capabilities(override="0")
    assert caps.tier == Tier.EMBED
    assert caps.storage_backend == "memory"
