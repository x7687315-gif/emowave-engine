"""presenters/snapshot_store.py — 会话快照的读写

=== 为什么单独抽 ===
`active_wave`（未走完的波）和 `latest_state`（当前状态）都要落盘，
但它们的读写细节（schema 版本、JSON、损坏兜底、EmotionState 反序列化）
不该塞在 presenter 里——Phase 1 一开始确实塞进去了，presenter 直接从
541 涨到 703 行，等于把"编排"又变回"什么都干"。

现在 presenter 只说"存/取"，细节归这里。Phase 6（持久化加固）也在这里改，
不用再动编排层。

=== 两个快照 ===
  active_wave  未走完的波：{schema_version, last_obs_ts, decay_started_at, samples}
               恢复时靠 last_obs_ts 算真实经过时间（v1 只有 end_ts，见 v1 兜底）
  latest_state 当前状态：EmotionState 的 JSON，波结束后仍保留，供挂件显示
"""

import json
import logging

logger = logging.getLogger(__name__)

#: active_wave 快照结构版本。v1 = {end_ts, samples}（无墙钟语义）。
WAVE_SNAPSHOT_VERSION = 2

_STATE_KEY = "latest_state"
_WAVE_KEY = "active_wave"


class SnapshotStore:
    """把 db 的 key-value 用法包成语义化接口；db 为 None 时全部降级为 no-op。"""

    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------
    # active_wave
    # ------------------------------------------------------------
    def save_wave(self, last_obs_ts, decay_started_at, samples):
        if self.db is None:
            return
        try:
            snap = {
                "schema_version": WAVE_SNAPSHOT_VERSION,
                "last_obs_ts": float(last_obs_ts),
                "decay_started_at": float(decay_started_at),
                "samples": [[o.timestamp, o.valence, o.arousal] for o in samples],
            }
            self.db.set_state(_WAVE_KEY, json.dumps(snap))
        except Exception as exc:
            logger.warning("保存活动情绪波快照失败：%s", exc)

    def load_wave(self):
        """返回 (samples, last_obs_ts)；无快照或损坏返回 None。"""
        if self.db is None:
            return None
        try:
            raw = self.db.get_state(_WAVE_KEY, "") or ""
        except Exception as exc:
            logger.debug("读取 active_wave 快照失败：%s", exc)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            samples = data.get("samples", [])
            # v1 用 end_ts，v2 用 last_obs_ts
            last_obs_ts = float(data.get("last_obs_ts", data.get("end_ts", 0.0)))
        except Exception as exc:
            logger.warning("active_wave 快照损坏，已清除：%s", exc)
            self.clear_wave()
            return None
        if not samples:
            self.clear_wave()
            return None
        return samples, last_obs_ts

    def clear_wave(self):
        if self.db is None:
            return
        try:
            self.db.set_state(_WAVE_KEY, "")
        except Exception as exc:
            logger.warning("清除活动情绪波快照失败：%s", exc)

    # ------------------------------------------------------------
    # latest_state
    # ------------------------------------------------------------
    def save_latest(self, state):
        if self.db is None or state is None:
            return
        try:
            payload = state.to_dict()
            payload["trend"] = getattr(state.trend, "value", state.trend)
            payload.pop("intensity", None)
            self.db.set_state(_STATE_KEY, json.dumps(payload))
        except Exception as exc:
            logger.warning("保存当前状态失败：%s", exc)

    def load_latest(self):
        """返回 EmotionState；无快照或损坏返回 None。"""
        if self.db is None:
            return None
        try:
            raw = self.db.get_state(_STATE_KEY, "") or ""
        except Exception as exc:
            logger.debug("读取当前状态失败：%s", exc)
            return None
        if not raw:
            return None
        try:
            from emowave import EmotionState, Trend

            data = json.loads(raw)
            data.pop("intensity", None)
            trend = data.pop("trend", None)
            state = EmotionState.from_dict(data)
            if trend is not None:
                object.__setattr__(state, "trend", Trend(trend))
            return state
        except Exception as exc:
            logger.warning("当前状态快照损坏，已忽略：%s", exc)
            return None
