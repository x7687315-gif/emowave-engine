"""presenters/state_query.py — "某个时刻的状态是什么"

=== 为什么需要它 ===
v4 之前，想拿"现在"的状态只有两条路：
  · 等下一次 `update(obs)`（必须伪造一条观察）
  · 直接读 `states[-1]`（那是**最后一次观察时**的状态，不是现在）

两条都不对。本类提供一个统一查询：把模型时间推进到目标时刻，返回状态，
**不产生任何 Observation**。

底层用 `StateEstimator.advance_to()`（v4 Phase 1 新增），它只做预测步。
"""


class StateQuery:
    """按需查询指定时刻的状态估计。"""

    def __init__(self, estimator_provider, clock):
        """
        Args:
            estimator_provider: 返回当前 StateEstimator 的可调用对象
                （presenter 换人群时会重建 estimator，所以必须是 provider 而非对象引用）
            clock: 返回当前时间戳的可调用对象（来自 WaveScheduler.now）
        """
        self._estimator = estimator_provider
        self._clock = clock

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
