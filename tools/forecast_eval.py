"""tools/forecast_eval.py — 预测日志评估（v5 Phase 6）

用法：
    python -m tools.forecast_eval --db ~/.emowave/emowave.db
    python -m tools.forecast_eval --tolerance-min 15

做法（对应 ROADMAP §4.4 / Phase 6 文档）：
  1. 从 emotion_events.raw_data + app_state.active_wave 还原真实观察；
  2. 从 forecast_log 读取**当时**的因果预测（不是 RTS 平滑曲线）；
  3. 对每条预测，找 target_ts 附近最近的观察（默认 ±10 分钟），
     按 horizon 分组计算效价 / 唤醒的 MAE、RMSE。

注意：用户自评本身有噪声。这里的指标衡量“模型与后续自评的吻合度”，
不是对人类真实情绪的绝对测量精度。
"""
import argparse
import bisect
import json
import math
import os
import sqlite3
import sys


def _valid_sample(item):
    try:
        ts, v, a = float(item[0]), float(item[1]), float(item[2])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if ts <= 0 or not (0.0 <= v <= 1.0) or not (0.0 <= a <= 1.0):
        return None
    return ts, v, a


def _collect(items, samples):
    for item in samples or []:
        clean = _valid_sample(item)
        if clean is not None:
            items.append(clean)


def load_observations(conn):
    """从事件 raw_data 与 active_wave 快照收集真实观察，按时间排序并去重。"""
    items = []
    try:
        rows = conn.execute("SELECT raw_data FROM emotion_events").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        try:
            _collect(items, json.loads(row[0] or "[]"))
        except (TypeError, ValueError):
            continue

    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key='active_wave'").fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        try:
            _collect(items, json.loads(row[0] or "{}").get("samples", []))
        except (TypeError, ValueError, AttributeError):
            pass

    unique = {}
    for ts, v, a in items:
        unique[(round(ts, 3), round(v, 6), round(a, 6))] = (ts, v, a)
    return sorted(unique.values(), key=lambda x: x[0])


def load_forecasts(conn):
    """读取 forecast_log；表不存在时返回空表（老库 / 还没用过应用）。"""
    try:
        rows = conn.execute(
            "SELECT base_ts,target_ts,horizon_sec,valence,arousal "
            "FROM forecast_log ORDER BY target_ts").fetchall()
    except sqlite3.OperationalError:
        return []
    return [{
        "base_ts": float(r[0]),
        "target_ts": float(r[1]),
        "horizon_sec": float(r[2]),
        "valence": float(r[3]),
        "arousal": float(r[4]),
    } for r in rows]


def _bucket():
    return {"n": 0, "ae_v": 0.0, "se_v": 0.0, "ae_a": 0.0, "se_a": 0.0}


def _finish(bucket):
    n = bucket["n"]
    if not n:
        return {"n": 0, "mae_valence": None, "rmse_valence": None,
                "mae_arousal": None, "rmse_arousal": None}
    return {
        "n": n,
        "mae_valence": bucket["ae_v"] / n,
        "rmse_valence": math.sqrt(bucket["se_v"] / n),
        "mae_arousal": bucket["ae_a"] / n,
        "rmse_arousal": math.sqrt(bucket["se_a"] / n),
    }


def evaluate(forecasts, observations, tolerance_sec=600.0):
    """按 horizon 分组计算 MAE/RMSE；只匹配 target_ts ±tolerance 内的观察。"""
    times = [o[0] for o in observations]
    overall = _bucket()
    horizons = {}
    matched = 0

    for forecast in forecasts:
        target = float(forecast["target_ts"])
        if not times:
            continue
        i = bisect.bisect_left(times, target)
        candidates = [j for j in (i - 1, i) if 0 <= j < len(observations)]
        if not candidates:
            continue
        j = min(candidates, key=lambda k: abs(times[k] - target))
        if abs(times[j] - target) > tolerance_sec:
            continue

        obs = observations[j]
        err_v = float(forecast["valence"]) - obs[1]
        err_a = float(forecast["arousal"]) - obs[2]
        horizon = float(forecast["horizon_sec"])
        bucket = horizons.setdefault(horizon, _bucket())
        for b in (bucket, overall):
            b["n"] += 1
            b["ae_v"] += abs(err_v)
            b["se_v"] += err_v * err_v
            b["ae_a"] += abs(err_a)
            b["se_a"] += err_a * err_a
        matched += 1

    return {
        "horizons": {h: _finish(horizons[h]) for h in sorted(horizons)},
        "overall": _finish(overall),
        "matched": matched,
        "forecasts": len(forecasts),
        "observations": len(observations),
    }


def format_horizon(seconds):
    seconds = float(seconds)
    if seconds < 3600:
        return "%dm" % int(round(seconds / 60))
    if seconds < 86400:
        return "%dh" % int(round(seconds / 3600))
    return "%dd" % int(round(seconds / 86400))


def _fmt(value):
    return "—" if value is None else "%.3f" % value


def main(argv=None):
    parser = argparse.ArgumentParser(description="EmoWave 预测日志评估")
    parser.add_argument(
        "--db", default=os.path.join(os.path.expanduser("~"), ".emowave", "emowave.db"),
        help="SQLite 数据库路径")
    parser.add_argument(
        "--tolerance-min", type=float, default=10.0,
        help="预测目标时刻与观察的最大匹配间隔（分钟，默认 10）")
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print("数据库不存在：%s" % args.db)
        return 2
    conn = sqlite3.connect(args.db)
    try:
        observations = load_observations(conn)
        forecasts = load_forecasts(conn)
        result = evaluate(forecasts, observations, args.tolerance_min * 60.0)
    finally:
        conn.close()

    print("观察 %d 条 · 预测 %d 条 · 匹配 %d 条（±%.0f 分钟）" % (
        result["observations"], result["forecasts"], result["matched"],
        args.tolerance_min))
    if not result["matched"]:
        print("没有可匹配的预测/观察。先使用应用积累数据，再重新运行。")
        return 1

    print("%-8s %5s %10s %10s %10s %10s" % (
        "horizon", "n", "V MAE", "V RMSE", "A MAE", "A RMSE"))
    for horizon, row in result["horizons"].items():
        print("%-8s %5d %10s %10s %10s %10s" % (
            format_horizon(horizon), row["n"],
            _fmt(row["mae_valence"]), _fmt(row["rmse_valence"]),
            _fmt(row["mae_arousal"]), _fmt(row["rmse_arousal"])))
    overall = result["overall"]
    print("%-8s %5d %10s %10s %10s %10s" % (
        "overall", overall["n"],
        _fmt(overall["mae_valence"]), _fmt(overall["rmse_valence"]),
        _fmt(overall["mae_arousal"]), _fmt(overall["rmse_arousal"])))
    print("提示：用户自评也有噪声；这些指标衡量模型与后续自评的吻合度，"
          "不是绝对测量精度。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
