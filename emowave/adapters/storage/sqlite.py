"""sqlite — Schema 版本化的本地持久化适配器。

REFACTOR_PLAN.md §13 数据存储原则：

  推荐继续以 SQLite 为基础，但重构 Schema。建议至少包含：
    observations / emotion_states / user_corrections / baselines /
    baseline_events / model_parameters / state_events

  原则：
    - 原始数据不可变：Observation 不允许因模型变化而被覆盖（append-only）
    - 模型结果可重算：EmotionState 可基于历史 Observation + ModelParameters 重新生成
    - 用户修正永久保留：Correction 是训练个人模型的监督信号，不能丢弃
    - Schema 版本化：所有数据迁移必须带版本号，为未来 Mobile/Rust Core 迁移留空间

本适配器是 Core 与 SQLite 之间的桥（§11.1：Core 不知道 SQLite 细节，
adapter 可以）。用标准库 sqlite3，零第三方依赖。

不可变性的代码级保证：
  - observations / user_corrections / state_events / baseline_events 表
    **只提供 append（INSERT），不提供 update/delete 方法**
  - 额外用 SQLite 触发器（BEFORE UPDATE/DELETE → RAISE）在数据库层强制 append-only，
    即使有人绕过适配器直接写 SQL 也无法篡改原始事实
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from emowave.core.domain.baseline import Baseline, BaselineShiftEvent
from emowave.core.domain.correction import UserCorrection
from emowave.core.domain.emotion_state import EmotionState
from emowave.core.domain.events import StateEvent
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation

# 当前 Schema 版本（§13/§27：所有迁移带版本号）
SCHEMA_VERSION = 1


class SQLiteStorage:
    """EmoWave 本地 SQLite 持久化适配器。

    使用方式：
        store = SQLiteStorage("~/.emowave/emowave.db")
        store.append_observation(obs)            # append-only
        store.append_observations([obs1, obs2])  # 批量（单事务）
        obs_list = store.get_observations(start_ts, end_ts)
        store.save_emotion_state(state)          # 可重算，允许覆盖
        store.close()

    七张表（§13）：
        observations      原始观察（append-only，触发器强制）
        emotion_states    模型估计（可重算）
        user_corrections  用户纠正（append-only，永久保留）
        baselines         基线版本（append-only，版本链）
        baseline_events   基线变更事件（append-only）
        model_parameters  个人参数版本（append-only，版本链）
        state_events      状态事件流（append-only）
        schema_meta       schema 版本（迁移用）
    """

    def __init__(self, path: Optional[str] = None, enforce_immutable: bool = True) -> None:
        """
        Args:
            path: 数据库文件路径。None → ~/.emowave/emowave.db。
                  ":memory:" → 内存数据库（测试用）。
            enforce_immutable: 是否安装 append-only 触发器（默认 True）。
        """
        if path is None:
            path = os.path.join(os.path.expanduser("~"), ".emowave", "emowave.db")
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL 模式提升并发读写（异步持久化的基础）
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass  # 内存库或不支持 WAL 时忽略
        self._create_schema()
        if enforce_immutable:
            self._install_immutable_triggers()
        self._ensure_schema_version()

    # ============================================================
    # Schema
    # ============================================================

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                valence REAL,
                arousal REAL,
                hr REAL,
                hrv REAL,
                sleep REAL,
                activity REAL,
                source TEXT NOT NULL DEFAULT 'user',
                confidence REAL NOT NULL DEFAULT 1.0,
                meta TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(timestamp);

            CREATE TABLE IF NOT EXISTS emotion_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                valence REAL NOT NULL,
                arousal REAL NOT NULL,
                stability REAL,
                confidence REAL,
                variance_valence REAL,
                variance_arousal REAL,
                trend TEXT,
                baseline_id TEXT,
                model_version TEXT,
                meta TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_state_ts ON emotion_states(timestamp);

            CREATE TABLE IF NOT EXISTS user_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                correction_id TEXT UNIQUE,
                timestamp REAL NOT NULL,
                dimension TEXT NOT NULL,
                predicted_value REAL NOT NULL,
                corrected_value REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'drag',
                reason TEXT,
                edit_session_mood REAL,
                edit_latency_sec REAL,
                drag_velocity REAL,
                salience REAL,
                seconds_before_event_end REAL,
                reliability_weight REAL,
                created_at REAL NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_corr_ts ON user_corrections(timestamp);

            CREATE TABLE IF NOT EXISTS baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                baseline_id TEXT UNIQUE,
                valence REAL, arousal REAL, intensity REAL, stability REAL,
                resting_hr REAL, resting_hrv_mean REAL, sleep_score REAL,
                source TEXT, effective_from REAL, effective_to REAL,
                regime_id TEXT, version INTEGER, parent_id TEXT,
                confidence REAL, meta TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS baseline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                old_baseline_id TEXT,
                new_baseline_id TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                reason TEXT,
                deltas TEXT NOT NULL DEFAULT '{}',
                user_confirmed INTEGER,
                detector_confidence REAL
            );

            CREATE TABLE IF NOT EXISTS model_parameters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                ell_valence REAL, ell_arousal REAL,
                sigma_valence REAL, sigma_arousal REAL, sigma_noise REAL,
                n_events_fitted INTEGER, log_likelihood REAL,
                shrinkage_alpha REAL, stage TEXT,
                parent_version INTEGER, updated_at REAL,
                regime_id TEXT, extra TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS state_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                source TEXT,
                causality_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_evt_ts ON state_events(timestamp);
            """
        )
        self.conn.commit()

    def _install_immutable_triggers(self) -> None:
        """在数据库层强制 append-only（§13 原始数据不可变）。

        对 observations / user_corrections / baseline_events / state_events
        安装 BEFORE UPDATE / BEFORE DELETE 触发器，任何修改/删除尝试都 RAISE ABORT。
        即使绕过适配器直接写 SQL 也无法篡改原始事实。
        """
        immutable_tables = [
            "observations",
            "user_corrections",
            "baseline_events",
            "state_events",
        ]
        for tbl in immutable_tables:
            self.conn.executescript(
                f"""
                CREATE TRIGGER IF NOT EXISTS {tbl}_no_update
                BEFORE UPDATE ON {tbl}
                BEGIN
                    SELECT RAISE(ABORT, '{tbl} is append-only: UPDATE forbidden');
                END;

                CREATE TRIGGER IF NOT EXISTS {tbl}_no_delete
                BEFORE DELETE ON {tbl}
                BEGIN
                    SELECT RAISE(ABORT, '{tbl} is append-only: DELETE forbidden');
                END;
                """
            )
        self.conn.commit()

    def _ensure_schema_version(self) -> None:
        """写入/迁移 schema 版本（§13 Schema 版本化）。"""
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self.conn.commit()
        else:
            current = int(row["value"])
            if current < SCHEMA_VERSION:
                self._migrate(current, SCHEMA_VERSION)

    def _migrate(self, from_version: int, to_version: int) -> None:
        """Schema 迁移（带版本号，§13）。

        当前 from=to=1 无实际迁移；未来版本在此按 v1→v2→... 逐步升级，
        为 Mobile / Rust Core 迁移留空间（§13/§25）。
        """
        # 示例迁移骨架（未来版本填充）：
        # if from_version < 2:
        #     self.conn.execute("ALTER TABLE observations ADD COLUMN xxx")
        #     from_version = 2
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(to_version),),
        )
        self.conn.commit()

    def get_schema_version(self) -> int:
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    # ============================================================
    # Observations（append-only）
    # ============================================================

    def append_observation(self, obs: Observation) -> int:
        """追加一条观察（永不覆盖，§13）。返回 row id。"""
        cur = self.conn.execute(
            """INSERT INTO observations
               (timestamp, valence, arousal, hr, hrv, sleep, activity,
                source, confidence, meta, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                obs.timestamp, obs.valence, obs.arousal, obs.hr, obs.hrv,
                obs.sleep, obs.activity, obs.source.value, obs.confidence,
                json.dumps(obs.meta, ensure_ascii=False), time.time(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def append_observations(self, observations: List[Observation]) -> int:
        """批量追加（单事务，§23 异步/批量写入）。返回写入条数。"""
        if not observations:
            return 0
        now = time.time()
        rows = [
            (
                o.timestamp, o.valence, o.arousal, o.hr, o.hrv, o.sleep,
                o.activity, o.source.value, o.confidence,
                json.dumps(o.meta, ensure_ascii=False), now,
            )
            for o in observations
        ]
        self.conn.executemany(
            """INSERT INTO observations
               (timestamp, valence, arousal, hr, hrv, sleep, activity,
                source, confidence, meta, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_observations(
        self,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[Observation]:
        """按时间窗口读取观察（正序）。"""
        sql = "SELECT * FROM observations"
        clauses = []
        params: List[Any] = []
        if start_ts is not None:
            clauses.append("timestamp >= ?")
            params.append(start_ts)
        if end_ts is not None:
            clauses.append("timestamp <= ?")
            params.append(end_ts)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_observation(r) for r in rows]

    def count_observations(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS c FROM observations").fetchone()["c"]

    def _row_to_observation(self, r: sqlite3.Row) -> Observation:
        return Observation(
            timestamp=r["timestamp"],
            valence=r["valence"],
            arousal=r["arousal"],
            hr=r["hr"],
            hrv=r["hrv"],
            sleep=r["sleep"],
            activity=r["activity"],
            source=r["source"],
            confidence=r["confidence"],
            meta=json.loads(r["meta"]) if r["meta"] else {},
        )

    # ============================================================
    # EmotionStates（可重算，允许覆盖）
    # ============================================================

    def save_emotion_state(self, state: EmotionState) -> int:
        """保存模型估计（§13：可重算，非不可变）。"""
        cur = self.conn.execute(
            """INSERT INTO emotion_states
               (timestamp, valence, arousal, stability, confidence,
                variance_valence, variance_arousal, trend, baseline_id,
                model_version, meta, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                state.timestamp, state.valence, state.arousal, state.stability,
                state.confidence, state.variance_valence, state.variance_arousal,
                state.trend.value, state.baseline_id, state.model_version,
                json.dumps(state.meta, ensure_ascii=False), time.time(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_emotion_states(
        self, start_ts: Optional[float] = None, end_ts: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[EmotionState]:
        sql = "SELECT * FROM emotion_states"
        clauses, params = [], []
        if start_ts is not None:
            clauses.append("timestamp >= ?"); params.append(start_ts)
        if end_ts is not None:
            clauses.append("timestamp <= ?"); params.append(end_ts)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"; params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            out.append(EmotionState(
                timestamp=r["timestamp"], valence=r["valence"], arousal=r["arousal"],
                stability=r["stability"] if r["stability"] is not None else 0.5,
                confidence=r["confidence"] if r["confidence"] is not None else 0.5,
                variance_valence=r["variance_valence"] or 0.0,
                variance_arousal=r["variance_arousal"] or 0.0,
                trend=r["trend"] or "unknown",
                baseline_id=r["baseline_id"],
                model_version=r["model_version"] or "",
                meta=json.loads(r["meta"]) if r["meta"] else {},
            ))
        return out

    def clear_emotion_states(self) -> int:
        """清空模型估计（§13：模型结果可重算，故允许清空后重建）。

        注意：这是唯一允许"删除"的表，因为 EmotionState 是派生产物，
        可由 (observations + model_parameters) 完全重建。
        原始 observations / corrections 永不删除。
        """
        cur = self.conn.execute("DELETE FROM emotion_states")
        self.conn.commit()
        return cur.rowcount

    # ============================================================
    # UserCorrections（append-only，永久保留）
    # ============================================================

    def append_correction(self, c: UserCorrection) -> int:
        """追加用户纠正（§13：永久保留，是个人模型的监督信号）。"""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO user_corrections
               (correction_id, timestamp, dimension, predicted_value, corrected_value,
                source, reason, edit_session_mood, edit_latency_sec, drag_velocity,
                salience, seconds_before_event_end, reliability_weight, created_at, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                c.correction_id, c.timestamp, c.dimension.value, c.predicted_value,
                c.corrected_value, c.source.value, c.reason, c.edit_session_mood,
                c.edit_latency_sec, c.drag_velocity, c.salience,
                c.seconds_before_event_end, c.reliability_weight, c.created_at,
                json.dumps(c.meta, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_corrections(
        self, start_ts: Optional[float] = None, end_ts: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[UserCorrection]:
        sql = "SELECT * FROM user_corrections"
        clauses, params = [], []
        if start_ts is not None:
            clauses.append("timestamp >= ?"); params.append(start_ts)
        if end_ts is not None:
            clauses.append("timestamp <= ?"); params.append(end_ts)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"; params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            out.append(UserCorrection(
                timestamp=r["timestamp"], dimension=r["dimension"],
                predicted_value=r["predicted_value"], corrected_value=r["corrected_value"],
                source=r["source"], reason=r["reason"],
                edit_session_mood=r["edit_session_mood"] or 0.0,
                edit_latency_sec=r["edit_latency_sec"] or 0.0,
                drag_velocity=r["drag_velocity"] or 0.0,
                salience=r["salience"] if r["salience"] is not None else 0.5,
                seconds_before_event_end=r["seconds_before_event_end"],
                reliability_weight=r["reliability_weight"],
                created_at=r["created_at"], correction_id=r["correction_id"],
                meta=json.loads(r["meta"]) if r["meta"] else {},
            ))
        return out

    def count_corrections(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS c FROM user_corrections").fetchone()["c"]

    # ============================================================
    # Baselines + BaselineEvents（append-only 版本链）
    # ============================================================

    def save_baseline(self, b: Baseline) -> int:
        """保存基线版本（INSERT OR REPLACE by baseline_id，版本链 append-only）。"""
        cur = self.conn.execute(
            """INSERT OR REPLACE INTO baselines
               (baseline_id, valence, arousal, intensity, stability, resting_hr,
                resting_hrv_mean, sleep_score, source, effective_from, effective_to,
                regime_id, version, parent_id, confidence, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                b.baseline_id, b.valence, b.arousal, b.intensity, b.stability,
                b.resting_hr, b.resting_hrv_mean, b.sleep_score, b.source.value,
                b.effective_from, b.effective_to, b.regime_id, b.version,
                b.parent_id, b.confidence, json.dumps(b.meta, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_baselines(self) -> List[Baseline]:
        rows = self.conn.execute(
            "SELECT * FROM baselines ORDER BY effective_from ASC, version ASC"
        ).fetchall()
        out = []
        for r in rows:
            out.append(Baseline(
                valence=r["valence"], arousal=r["arousal"], intensity=r["intensity"],
                stability=r["stability"], resting_hr=r["resting_hr"],
                resting_hrv_mean=r["resting_hrv_mean"], sleep_score=r["sleep_score"],
                baseline_id=r["baseline_id"], source=r["source"],
                effective_from=r["effective_from"], effective_to=r["effective_to"],
                regime_id=r["regime_id"], version=r["version"], parent_id=r["parent_id"],
                confidence=r["confidence"], meta=json.loads(r["meta"]) if r["meta"] else {},
            ))
        return out

    def append_baseline_event(self, e: BaselineShiftEvent) -> int:
        cur = self.conn.execute(
            """INSERT INTO baseline_events
               (timestamp, old_baseline_id, new_baseline_id, shift_type, reason,
                deltas, user_confirmed, detector_confidence)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                e.timestamp, e.old_baseline_id, e.new_baseline_id, e.shift_type.value,
                e.reason, json.dumps(e.deltas, ensure_ascii=False),
                1 if e.user_confirmed else 0, e.detector_confidence,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_baseline_events(self) -> List[BaselineShiftEvent]:
        rows = self.conn.execute(
            "SELECT * FROM baseline_events ORDER BY timestamp ASC"
        ).fetchall()
        return [
            BaselineShiftEvent(
                timestamp=r["timestamp"], old_baseline_id=r["old_baseline_id"],
                new_baseline_id=r["new_baseline_id"], shift_type=r["shift_type"],
                reason=r["reason"] or "",
                deltas=json.loads(r["deltas"]) if r["deltas"] else {},
                user_confirmed=bool(r["user_confirmed"]),
                detector_confidence=r["detector_confidence"] or 0.0,
            )
            for r in rows
        ]

    # ============================================================
    # ModelParameters（append-only 版本链）
    # ============================================================

    def save_model_parameters(self, p: ModelParameters) -> int:
        cur = self.conn.execute(
            """INSERT INTO model_parameters
               (version, ell_valence, ell_arousal, sigma_valence, sigma_arousal,
                sigma_noise, n_events_fitted, log_likelihood, shrinkage_alpha, stage,
                parent_version, updated_at, regime_id, extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                p.version, p.ell_valence, p.ell_arousal, p.sigma_valence,
                p.sigma_arousal, p.sigma_noise, p.n_events_fitted, p.log_likelihood,
                p.shrinkage_alpha, p.stage.value, p.parent_version, p.updated_at,
                p.regime_id, json.dumps(p.extra, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_latest_model_parameters(self) -> Optional[ModelParameters]:
        row = self.conn.execute(
            "SELECT * FROM model_parameters ORDER BY version DESC, updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self._row_to_params(row)

    def get_model_parameters_history(self) -> List[ModelParameters]:
        rows = self.conn.execute(
            "SELECT * FROM model_parameters ORDER BY version ASC"
        ).fetchall()
        return [self._row_to_params(r) for r in rows]

    def _row_to_params(self, r: sqlite3.Row) -> ModelParameters:
        return ModelParameters(
            ell_valence=r["ell_valence"], ell_arousal=r["ell_arousal"],
            sigma_valence=r["sigma_valence"], sigma_arousal=r["sigma_arousal"],
            sigma_noise=r["sigma_noise"], n_events_fitted=r["n_events_fitted"],
            log_likelihood=r["log_likelihood"], shrinkage_alpha=r["shrinkage_alpha"],
            stage=r["stage"], version=r["version"], parent_version=r["parent_version"],
            updated_at=r["updated_at"], regime_id=r["regime_id"],
            extra=json.loads(r["extra"]) if r["extra"] else {},
        )

    # ============================================================
    # StateEvents（append-only）
    # ============================================================

    def append_state_event(self, e: StateEvent) -> int:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO state_events
               (event_id, timestamp, event_type, payload, source, causality_id)
               VALUES (?,?,?,?,?,?)""",
            (
                e.event_id, e.timestamp, e.event_type.value,
                json.dumps(e.payload, ensure_ascii=False), e.source, e.causality_id,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_state_events(
        self, start_ts: Optional[float] = None, end_ts: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[StateEvent]:
        sql = "SELECT * FROM state_events"
        clauses, params = [], []
        if start_ts is not None:
            clauses.append("timestamp >= ?"); params.append(start_ts)
        if end_ts is not None:
            clauses.append("timestamp <= ?"); params.append(end_ts)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"; params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            StateEvent(
                timestamp=r["timestamp"], event_type=r["event_type"],
                payload=json.loads(r["payload"]) if r["payload"] else {},
                source=r["source"] or "", event_id=r["event_id"],
                causality_id=r["causality_id"],
            )
            for r in rows
        ]

    # ============================================================
    # 生命周期
    # ============================================================

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "SQLiteStorage":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
