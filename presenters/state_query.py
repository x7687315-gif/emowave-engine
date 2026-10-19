"""presenters/state_query.py — "某个时刻 / 某段时间"的统一查询入口

=== 为什么需要它 ===
v4 之前，想拿"现在"的状态只有两条路：
  · 等下一次 `update(obs)`（必须伪造一条观察）
  · 直接读 `states[-1]`（那是**最后一次观察时**的状态，不是现在）

而"某段时间的曲线"则散落在 presenter 的 `_emit_curve` 里，视图各自拼 payload。
本类把两件事收成一个入口：主窗、挂件、未来的 5 分钟刷新，都问同一个对象。

=== 两层 ===
  query_state(ts)   推进模型时间到 ts，返回 EmotionState（不产生观察）
  query_curve(...)  给定窗口与显示点数，返回渲染 payload（RTS 平滑 + 降采样）

查询本身无状态；缓存放在 presenter（它才知道数据何时变了）。
"""

from .engine import RTSSmoother

#: 曲线的默认显示点数上限（主窗 340px 高、400px 宽，再多也画不出来）
DEFAULT_RESOLUTION = 600


def _decimate(count: int, cap: int):
    """等间隔抽稀，返回**恰好**不超过 cap 个索引（首尾必保留）。"""
    cap = max(2, int(cap))
    if count <= cap:
        return list(range(count))
    # 留出首尾两格，中间等分，保证结果长度 == cap（不会多出一个）
    step = (count - 1) / float(cap - 1)
    return sorted({int(round(i * step)) for i in range(cap)})


class StateQuery:
    """按需查询指定时刻的状态、指定窗口的曲线。"""

    def __init__(self, estimator_provider, clock, baseline_provider=None):
        """
        Args:
            estimator_provider: 返回当前 StateEstimator 的可调用对象
                （presenter 换人群时会重建 estimator，所以必须是 provider 而非对象引用）
            clock: 返回当前时间戳的可调用对象（来自 WaveScheduler.now）
            baseline_provider: 返回当前 Baseline 的可调用对象（画基线用）
        """
        self._estimator = estimator_provider
        self._clock = clock
        self._baseline = baseline_provider or (lambda: None)

    # ------------------------------------------------------------
    # 单点
    # ------------------------------------------------------------
    def query_state(self, target_ts: float = None):
        """推进到目标时刻并返回 EmotionState；未初始化返回 None。

        容错：目标时刻早于模型当前时间时**不报错**，直接返回当前状态。
        查询是只读语义，不该因为毫秒级竞态把调用方打断。
        """
        est = self._estimator()
        if est is None or not est.is_initialized:
            return None
        target = self._clock() if target_ts is None else float(target_ts)
        last = getattr(est, "last_timestamp", None)
        if last is not None and target <= last:
            return est.current_state()
        return est.advance_to(target)

    # ------------------------------------------------------------
    # 一段曲线
    # ------------------------------------------------------------
    def query_curve(self, observations, params, edits=None,
                    start_ts=None, end_ts=None, resolution=None):
        """返回窗口内的平滑曲线 payload；数据不足返回 None。

        Args:
            observations: 真实观察（append-only，按时间正序）
            params: 当前个人参数（决定 Matérn 的 ℓ / σ）
            edits: 用户纠正产生的伪观察
            start_ts / end_ts: 窗口；None 表示不裁剪
            resolution: 显示点数上限（降采样，不影响模型精度）

        返回的 dict 与 `curve_changed` 信号同构：
            {ts, mv, ma, vv, va, baseline_v, raw}
        """
        if not observations or len(observations) < 2:
            return None

        traj = RTSSmoother(params).smooth(observations, edits or [])
        ts_all = list(traj.timestamps)

        # 窗口裁剪
        lo = float("-inf") if start_ts is None else float(start_ts)
        hi = float("inf") if end_ts is None else float(end_ts)
        idx = [i for i, t in enumerate(ts_all) if lo <= t <= hi]
        if len(idx) < 2:
            return None

        # 降采样（只影响画多少点，不影响上面算出的平滑结果）
        cap = DEFAULT_RESOLUTION if resolution is None else resolution
        if len(idx) > cap:
            picked = _decimate(len(idx), cap)
            idx = [idx[i] for i in picked]

        baseline = self._baseline()
        return {
            "ts": [ts_all[i] for i in idx],
            "mv": [traj.mean_valence[i] for i in idx],
            "ma": [traj.mean_arousal[i] for i in idx],
            "vv": [traj.var_valence[i] for i in idx],
            "va": [traj.var_arousal[i] for i in idx],
            "baseline_v": baseline.valence if baseline is not None else 0.5,
            "raw": [(o.timestamp, o.valence) for o in observations
                    if lo <= o.timestamp <= hi],
        }
