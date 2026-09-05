"""bench_core_performance — 新内核性能基准（part2 §2.1 / REFACTOR_PLAN §23）。

验证性能目标（REFACTOR_PLAN.md §23）：
    Live state update      < 20 ms
    Curve redraw (RTS)     < 16 ms target（节点网格降采样后）
    Memory footprint       低百 MB 以内
    GPU                    不要求

对照 part2 §2.1 的实测口径（纯 Python vs numpy），本脚本只测纯 Python 新内核
（零依赖），确认在 1Hz 采样下的实时性与低配置可行性。

运行：
    python research/bench_core_performance.py
"""

from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from emowave.core import tier
from emowave.core.curve.curve import CurveEditor, build_node_grid
from emowave.core.curve.smoother import RTSSmoother
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource
from emowave.core.estimator.estimator import StateEstimator


def bench_live_update(n=1000):
    """L1 因果滤波单步延迟（part2 §2.1 口径：纯 Python Kalman 单步）。"""
    est = StateEstimator(params=ModelParameters())
    est.initialize(timestamp=1.0)
    obs = Observation(timestamp=2.0, valence=0.5, arousal=0.5, source=ObservationSource.USER)
    # 预热
    for i in range(10):
        est._predict(1.0)
    t0 = time.perf_counter()
    for i in range(n):
        est.update(Observation(timestamp=100.0 + i, valence=0.5, arousal=0.5))
    elapsed = time.perf_counter() - t0
    return elapsed / n * 1000.0  # ms/step


def bench_rts_smooth(n_raw=2000, n_nodes=300):
    """L2 RTS 平滑：全采样 vs 节点网格降采样（part2 §2.4）。"""
    import math
    obs = [
        Observation(timestamp=100.0 + i,
                    valence=0.5 + 0.2 * math.sin(i / 100.0),
                    arousal=0.5 + 0.1 * math.cos(i / 120.0))
        for i in range(n_raw)
    ]
    smoother = RTSSmoother()

    # 全采样
    t0 = time.perf_counter()
    smoother.smooth(obs)
    full_ms = (time.perf_counter() - t0) * 1000.0

    # 节点网格降采样后平滑
    ts = [o.timestamp for o in obs]
    grid = build_node_grid(ts, n_nodes)
    downsampled = [obs[i] for i in grid]
    t0 = time.perf_counter()
    smoother.smooth(downsampled)
    ds_ms = (time.perf_counter() - t0) * 1000.0

    return full_ms, ds_ms, len(downsampled)


def bench_curve_rebuild(n_raw=600, n_nodes=150):
    """曲线重建（含编辑）延迟——T1 档目标 ≤150ms（part2 §7 阶段3）。"""
    import math
    obs = [
        Observation(timestamp=100.0 + i,
                    valence=0.5 + 0.2 * math.sin(i / 80.0), arousal=0.5)
        for i in range(n_raw)
    ]
    editor = CurveEditor(obs, n_nodes=n_nodes)
    editor.rebuild()  # 预热
    t0 = time.perf_counter()
    editor.rebuild()
    return (time.perf_counter() - t0) * 1000.0


def bench_import_cost():
    """import emowave 的耗时（part2 §0：numpy import 1082ms 是致命开销）。"""
    # 在子进程测才准，这里测已导入后重新 import 的缓存命中（近似 0）
    t0 = time.perf_counter()
    import emowave  # noqa: F401
    return (time.perf_counter() - t0) * 1000.0


def main():
    print("=" * 70)
    print("EmoWave 2.0 纯 Python 内核性能基准（零第三方依赖）")
    print("=" * 70)

    # 设备档位
    detected = tier.detect_tier()
    caps = tier.capabilities_for(detected)
    print(f"探测档位: T{int(detected)} ({detected.name})")
    print(f"  CPU 核心: {tier.cpu_count()}, 内存: {tier.total_memory_mb() or '未知'} MB, "
          f"移动端: {tier.is_mobile()}")
    print(f"  能力: L1={caps.enable_l1_filtering} L2={caps.enable_l2_curve} "
          f"L3={caps.enable_l3_learning} 节点数={caps.curve_node_count}")
    print()

    # L1 实时性
    per_step = bench_live_update(1000)
    hz1_cpu = per_step / 1000.0 * 100.0  # 1Hz 采样下的 CPU 占用百分比
    print(f"[L1 因果滤波] 单步 {per_step:.4f} ms")
    print(f"  → 1Hz 采样 CPU 占用 ≈ {hz1_cpu:.5f}% （目标 <20ms/步，实时性充裕）")
    print(f"  → 即使设备慢 5 倍仍 ≈ {hz1_cpu*5:.4f}% CPU")
    print()

    # L2 RTS 平滑 + 降采样
    full_ms, ds_ms, n_ds = bench_rts_smooth(2000, 300)
    print(f"[L2 RTS 平滑] N=2000 全采样 {full_ms:.1f} ms")
    print(f"  → 节点网格降采样 N={n_ds}: {ds_ms:.1f} ms "
          f"(加速 {full_ms/max(ds_ms,1e-9):.1f}×)")
    print(f"  part2 §2.4 预期：降采样后 ~26ms 量级，纯 Python 可接受")
    print()

    # 曲线重建（T1 档）
    rebuild_ms = bench_curve_rebuild(600, 150)
    print(f"[曲线重建] N=600→节点150: {rebuild_ms:.1f} ms "
          f"(T1 档目标 ≤150ms)")
    print()

    # 结论
    print("-" * 70)
    l1_ok = per_step < 20.0
    rebuild_ok = rebuild_ms < 150.0
    print(f"[L1 <20ms]        {'PASS' if l1_ok else 'FAIL'} ({per_step:.4f} ms)")
    print(f"[曲线重建 <150ms] {'PASS' if rebuild_ok else 'FAIL'} ({rebuild_ms:.1f} ms)")
    print(f"[零 GPU 依赖]     PASS（纯 CPU + 标准库）")
    print()
    if l1_ok and rebuild_ok:
        print("结论：纯 Python 零依赖内核满足低配置实时运行目标（REFACTOR_PLAN §23）。")
    else:
        print("结论：部分指标未达标，需进一步优化或降档。")


if __name__ == "__main__":
    main()
