"""CLI — EmoWave 命令行入口。

REFACTOR_PLAN.md §11 推荐架构含 cli/。CLI 是 T0 档位的最小可用接口，
证明 Core 可完全脱离 UI 运行（Phase 1 完成标准"Core 可不依赖 UI 独立运行"
的端到端体现），也是调试/演示/自动化的入口。

子命令：
    demo      跑一段合成观察流，打印实时状态估计（Observation→Estimator→EmotionState）
    detect    用 EmotionBridge 把文本/(v,a) 映射为 Amiya 4 状态
    curve     对合成观察做 RTS 平滑，打印曲线节点 + 置信带
    version   打印版本与协议 schema

用法：
    python -m emowave.cli demo
    python -m emowave.cli detect --text "我好焦虑"
    python -m emowave.cli detect --valence 0.2 --arousal 0.85
    python -m emowave.cli curve --points 60
    python -m emowave.cli version

零第三方依赖（argparse + Core）。
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import List, Optional

from emowave import __version__
from emowave.core.domain.observation import Observation, ObservationSource
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.estimator.estimator import StateEstimator
from emowave.core.curve.smoother import RTSSmoother
from emowave.core.curve.curve import build_node_grid
from emowave.core.protocol.schemas import (
    MODEL_VERSION,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
)


# 合成观察点数上限（防止 --points 误输入超大值导致内存/时间爆炸，SEC-003 参数有效性）
MAX_SYNTH_POINTS = 200_000


def _synthetic_observations(n: int, start_ts: float = 1000.0) -> List[Observation]:
    """生成一段合成情绪观察（慢正弦 + 一个应激峰），用于 demo/curve。"""
    # 参数有效性防护：钳到 [1, MAX_SYNTH_POINTS]
    n = max(1, min(int(n), MAX_SYNTH_POINTS))
    obs = []
    for i in range(n):
        # 基础慢波 + 中段一个应激峰
        base_v = 0.55 + 0.15 * math.sin(2 * math.pi * i / max(n, 1) * 2)
        base_a = 0.4 + 0.1 * math.cos(2 * math.pi * i / max(n, 1) * 2)
        if n // 3 <= i < n // 3 + max(3, n // 10):
            # 应激峰：唤醒骤升、效价骤降
            base_a = min(1.0, base_a + 0.45)
            base_v = max(0.0, base_v - 0.35)
        obs.append(
            Observation(
                timestamp=start_ts + i,
                valence=max(0.0, min(1.0, base_v)),
                arousal=max(0.0, min(1.0, base_a)),
                source=ObservationSource.USER,
            )
        )
    return obs


def cmd_demo(args: argparse.Namespace) -> int:
    """跑合成观察流，打印实时状态估计。"""
    n = args.points
    obs = _synthetic_observations(n)
    params = ModelParameters()
    est = StateEstimator(params=params)
    est.initialize(timestamp=obs[0].timestamp, valence=obs[0].valence, arousal=obs[0].arousal)

    print(f"EmoWave demo — {n} 条合成观察 → 实时状态估计（Matérn Kalman）")
    print(f"参数: ℓ_v={params.ell_valence:.0f}s ℓ_a={params.ell_arousal:.0f}s "
          f"σ_v={params.sigma_valence} σ_noise={params.sigma_noise}")
    print("-" * 72)
    print(f"{'t':>6} {'valence':>8} {'arousal':>8} {'intens':>7} {'conf':>6} "
          f"{'trend':>8} {'±1σ_v':>12}")
    print("-" * 72)
    step = max(1, n // 20)  # 最多打印 20 行
    last = None
    for i, o in enumerate(obs):
        st = est.update(o)
        last = st
        if i % step == 0 or i == n - 1:
            lo, hi = st.confidence_interval("valence", z=1.0)
            print(f"{o.timestamp - obs[0].timestamp:>6.0f} {st.valence:>8.3f} "
                  f"{st.arousal:>8.3f} {st.intensity:>7.3f} {st.confidence:>6.3f} "
                  f"{st.trend.value:>8} [{lo:.2f},{hi:.2f}]".rjust(12))
    print("-" * 72)
    print(f"最终状态: valence={last.valence:.3f} arousal={last.arousal:.3f} "
          f"intensity={last.intensity:.3f} confidence={last.confidence:.3f}")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    """用 EmotionBridge 把文本/(v,a) 映射为 Amiya 4 状态。"""
    from emowave.adapters.agent.amiya import EmotionBridge, map_state_to_amiya_key

    if args.valence is not None and args.arousal is not None:
        key = map_state_to_amiya_key(args.valence, args.arousal)
        print(f"输入: valence={args.valence} arousal={args.arousal}")
        print(f"Amiya 状态: {key}")
        return 0

    text = args.text or ""
    bridge = EmotionBridge(enabled=True)
    key = bridge.detect(text, valence=args.valence, arousal=args.arousal)
    print(f"输入文本: {text!r}")
    print(f"Amiya 状态: {key}")
    if bridge.last_state is not None:
        st = bridge.last_state
        print(f"连续状态: valence={st.valence:.3f} arousal={st.arousal:.3f} "
              f"intensity={st.intensity:.3f} confidence={st.confidence:.3f}")
    return 0


def cmd_curve(args: argparse.Namespace) -> int:
    """对合成观察做 RTS 平滑，打印曲线节点 + 置信带。"""
    n = args.points
    obs = _synthetic_observations(n)
    smoother = RTSSmoother()
    traj = smoother.smooth(obs)

    ts = [o.timestamp for o in obs]
    grid = build_node_grid(ts, args.nodes)

    print(f"EmoWave curve — RTS 平滑 {n} 点 → {len(grid)} 节点网格（part2 §2.4 降采样）")
    print("-" * 72)
    print(f"{'node':>5} {'t':>6} {'valence':>8} {'±1σ':>14} {'arousal':>8}")
    print("-" * 72)
    for k, idx in enumerate(grid):
        mv = traj.mean_valence[idx]
        sv = math.sqrt(max(0.0, traj.var_valence[idx]))
        ma = traj.mean_arousal[idx]
        t_off = traj.timestamps[idx] - traj.timestamps[0]
        band = f"[{max(0,mv-sv):.2f},{min(1,mv+sv):.2f}]"
        print(f"{k:>5} {t_off:>6.0f} {mv:>8.3f} {band:>14} {ma:>8.3f}")
    print("-" * 72)
    print(f"平滑完成：{len(traj)} 点 → {len(grid)} 节点（非因果全局平滑，part1 §3.2）")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(f"EmoWave / 心潮 — Personal Emotion State Engine")
    print(f"  package version : {__version__}")
    print(f"  schema_version  : {SCHEMA_VERSION}")
    print(f"  protocol_version: {PROTOCOL_VERSION}")
    print(f"  model_version   : {MODEL_VERSION}")
    print(f"  python          : {sys.version.split()[0]}")
    try:
        from emowave.core.tier import detect_tier, capabilities_for
        t = detect_tier()
        caps = capabilities_for(t)
        print(f"  detected tier   : T{int(t)} ({t.name}) "
              f"L1={caps.enable_l1_filtering} L2={caps.enable_l2_curve} "
              f"L3={caps.enable_l3_learning}")
    except Exception as e:  # noqa: BLE001
        print(f"  detected tier   : (探测失败 {e})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emowave",
        description="EmoWave / 心潮 — 个人情绪状态引擎 CLI（零第三方依赖）",
    )
    sub = parser.add_subparsers(dest="command")

    p_demo = sub.add_parser("demo", help="跑合成观察流，打印实时状态估计")
    p_demo.add_argument("--points", type=int, default=60, help="观察点数（默认 60）")
    p_demo.set_defaults(func=cmd_demo)

    p_detect = sub.add_parser("detect", help="文本/(v,a) → Amiya 4 状态")
    p_detect.add_argument("--text", type=str, default=None, help="输入文本")
    p_detect.add_argument("--valence", type=float, default=None, help="效价 [0,1]")
    p_detect.add_argument("--arousal", type=float, default=None, help="唤醒 [0,1]")
    p_detect.set_defaults(func=cmd_detect)

    p_curve = sub.add_parser("curve", help="RTS 平滑曲线 + 置信带")
    p_curve.add_argument("--points", type=int, default=60, help="观察点数（默认 60）")
    p_curve.add_argument("--nodes", type=int, default=15, help="节点网格数（默认 15）")
    p_curve.set_defaults(func=cmd_curve)

    p_version = sub.add_parser("version", help="打印版本与协议 schema")
    p_version.set_defaults(func=cmd_version)

    return parser


def _ensure_utf8_stdout() -> None:
    """强制 stdout/stderr 用 UTF-8，避免 Windows 控制台 GBK 编码崩溃。

    CLI 输出含 ℓ(U+2113)、·、→ 等非 GBK 字符，Windows 默认代码页打印会抛
    UnicodeEncodeError。reconfigure 到 UTF-8 一次性解决全部子命令的输出编码。
    对不可 reconfigure（被重定向 / 旧版本）的流静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
