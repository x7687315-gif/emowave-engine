"""SQLiteStorage 持久化适配器单元测试。

覆盖 REFACTOR_PLAN.md §13 数据存储原则：
  - 七表 schema + 版本化
  - 原始数据不可变（observations/corrections append-only，触发器强制）
  - 模型结果可重算（emotion_states 允许清空重建）
  - 用户修正永久保留
  - 批量写入
  - §22 Migration Test
"""

import json
import sqlite3
import time

import pytest

from emowave.adapters.storage.sqlite import SQLiteStorage, SCHEMA_VERSION
from emowave.core.domain.baseline import Baseline, BaselineShiftEvent, BaselineSource
from emowave.core.domain.correction import (
    CorrectionDimension,
    CorrectionSource,
    UserCorrection,
)
from emowave.core.domain.emotion_state import EmotionState, Trend
from emowave.core.domain.events import StateEvent, StateEventType
from emowave.core.domain.model_parameters import ModelParameters
from emowave.core.domain.observation import Observation, ObservationSource


@pytest.fixture
def store():
    """内存数据库 fixture（每个测试独立）。"""
    s = SQLiteStorage(":memory:")
    yield s
    s.close()


# ============================================================
# Schema 与版本
# ============================================================


def test_schema_version_recorded(store):
    assert store.get_schema_version() == SCHEMA_VERSION


def test_all_seven_tables_created(store):
    """§13 建议的七张表都应创建。"""
    rows = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    expected = {
        "observations", "emotion_states", "user_corrections", "baselines",
        "baseline_events", "model_parameters", "state_events", "schema_meta",
    }
    assert expected.issubset(names)


def test_persistent_file_schema(tmp_path):
    """文件数据库也能正常建表。"""
    db_path = str(tmp_path / "sub" / "test.db")
    s = SQLiteStorage(db_path)
    assert s.get_schema_version() == SCHEMA_VERSION
    s.append_observation(Observation(timestamp=1.0, valence=0.5))
    assert s.count_observations() == 1
    s.close()
    # 重新打开，数据仍在
    s2 = SQLiteStorage(db_path)
    assert s2.count_observations() == 1
    s2.close()


# ============================================================
# Observations（append-only）
# ============================================================


def test_append_and_get_observation(store):
    obs = Observation(timestamp=1000.0, valence=0.6, arousal=0.4,
                      source=ObservationSource.SENSOR, hr=80.0, hrv=45.0)
    store.append_observation(obs)
    result = store.get_observations()
    assert len(result) == 1
    assert result[0].timestamp == 1000.0
    assert result[0].valence == pytest.approx(0.6)
    assert result[0].source == ObservationSource.SENSOR
    assert result[0].hr == 80.0


def test_observation_roundtrip_preserves_all_fields(store):
    obs = Observation(timestamp=1000.0, valence=0.32, arousal=0.77, hr=88.0,
                      hrv=32.0, sleep=6.5, activity=0.4,
                      source=ObservationSource.AGENT, confidence=0.85,
                      meta={"device": "watch", "session": "s1"})
    store.append_observation(obs)
    restored = store.get_observations()[0]
    assert restored.valence == pytest.approx(0.32)
    assert restored.arousal == pytest.approx(0.77)
    assert restored.sleep == 6.5
    assert restored.activity == 0.4
    assert restored.source == ObservationSource.AGENT
    assert restored.confidence == pytest.approx(0.85)
    assert restored.meta == {"device": "watch", "session": "s1"}


def test_append_observations_batch(store):
    """批量写入（单事务，§23）。"""
    obs_list = [Observation(timestamp=1000.0 + i, valence=0.5, arousal=0.5)
                for i in range(100)]
    n = store.append_observations(obs_list)
    assert n == 100
    assert store.count_observations() == 100


def test_append_observations_empty(store):
    assert store.append_observations([]) == 0


def test_get_observations_time_window(store):
    for i in range(10):
        store.append_observation(Observation(timestamp=1000.0 + i, valence=0.5))
    window = store.get_observations(start_ts=1003.0, end_ts=1006.0)
    assert [o.timestamp for o in window] == [1003.0, 1004.0, 1005.0, 1006.0]


def test_get_observations_ordered_by_timestamp(store):
    # 乱序写入
    for ts in [1005.0, 1001.0, 1003.0]:
        store.append_observation(Observation(timestamp=ts, valence=0.5))
    result = store.get_observations()
    assert [o.timestamp for o in result] == [1001.0, 1003.0, 1005.0]


def test_get_observations_limit(store):
    for i in range(10):
        store.append_observation(Observation(timestamp=1000.0 + i, valence=0.5))
    assert len(store.get_observations(limit=3)) == 3


def test_observations_immutable_no_update(store):
    """§13 原始数据不可变：observations 表 UPDATE 被触发器拒绝。"""
    store.append_observation(Observation(timestamp=1000.0, valence=0.5))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE observations SET valence=0.9 WHERE timestamp=1000.0")


def test_observations_immutable_no_delete(store):
    """§13 原始数据不可变：observations 表 DELETE 被触发器拒绝。"""
    store.append_observation(Observation(timestamp=1000.0, valence=0.5))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM observations WHERE timestamp=1000.0")


def test_immutable_can_be_disabled():
    """enforce_immutable=False 时不装触发器（用于数据迁移等特殊场景）。"""
    s = SQLiteStorage(":memory:", enforce_immutable=False)
    s.append_observation(Observation(timestamp=1000.0, valence=0.5))
    # 无触发器，UPDATE 不报错
    s.conn.execute("UPDATE observations SET valence=0.9 WHERE timestamp=1000.0")
    s.conn.commit()
    assert s.get_observations()[0].valence == pytest.approx(0.9)
    s.close()


# ============================================================
# EmotionStates（可重算）
# ============================================================


def test_save_and_get_emotion_state(store):
    st = EmotionState(timestamp=1000.0, valence=0.6, arousal=0.4,
                      stability=0.7, confidence=0.85, variance_valence=0.01,
                      variance_arousal=0.02, trend=Trend.RISING,
                      baseline_id="bl_1", model_version="2.0.0")
    store.save_emotion_state(st)
    result = store.get_emotion_states()
    assert len(result) == 1
    assert result[0].valence == pytest.approx(0.6)
    assert result[0].trend == Trend.RISING
    assert result[0].baseline_id == "bl_1"


def test_emotion_states_can_be_cleared(store):
    """§13 模型结果可重算：emotion_states 允许清空（派生产物可重建）。"""
    store.save_emotion_state(EmotionState(timestamp=1000.0, valence=0.6, arousal=0.4))
    store.save_emotion_state(EmotionState(timestamp=1001.0, valence=0.6, arousal=0.4))
    assert len(store.get_emotion_states()) == 2
    cleared = store.clear_emotion_states()
    assert cleared == 2
    assert len(store.get_emotion_states()) == 0
    # 但 observations 不受影响（原始数据不可变）
    store.append_observation(Observation(timestamp=1000.0, valence=0.5))
    assert store.count_observations() == 1


def test_emotion_state_time_window(store):
    for i in range(5):
        store.save_emotion_state(EmotionState(timestamp=1000.0 + i, valence=0.5, arousal=0.5))
    window = store.get_emotion_states(start_ts=1001.0, end_ts=1003.0)
    assert len(window) == 3


# ============================================================
# UserCorrections（append-only，永久保留）
# ============================================================


def test_append_and_get_correction(store):
    now = time.time()
    c = UserCorrection(timestamp=now - 10, dimension=CorrectionDimension.VALENCE,
                       predicted_value=0.4, corrected_value=0.7,
                       source=CorrectionSource.SLIDER, reason="其实更正面",
                       salience=0.8, created_at=now)
    store.append_correction(c)
    result = store.get_corrections()
    assert len(result) == 1
    assert result[0].dimension == CorrectionDimension.VALENCE
    assert result[0].predicted_value == pytest.approx(0.4)
    assert result[0].corrected_value == pytest.approx(0.7)
    assert result[0].reason == "其实更正面"
    assert result[0].source == CorrectionSource.SLIDER


def test_corrections_immutable_no_delete(store):
    """§13 用户修正永久保留：corrections 表 DELETE 被拒绝。"""
    now = time.time()
    c = UserCorrection(timestamp=now - 10, dimension=CorrectionDimension.VALENCE,
                       predicted_value=0.4, corrected_value=0.7, created_at=now)
    store.append_correction(c)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM user_corrections")


def test_corrections_immutable_no_update(store):
    now = time.time()
    c = UserCorrection(timestamp=now - 10, dimension=CorrectionDimension.VALENCE,
                       predicted_value=0.4, corrected_value=0.7, created_at=now)
    store.append_correction(c)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE user_corrections SET corrected_value=0.99")


def test_correction_count(store):
    now = time.time()
    for i in range(5):
        c = UserCorrection(timestamp=now - 10 + i, dimension=CorrectionDimension.VALENCE,
                           predicted_value=0.4, corrected_value=0.5, created_at=now + i)
        store.append_correction(c)
    assert store.count_corrections() == 5


def test_correction_roundtrip_preserves_weight(store):
    now = time.time()
    c = UserCorrection(timestamp=now - 10, dimension=CorrectionDimension.AROUSAL,
                       predicted_value=0.3, corrected_value=0.8,
                       source=CorrectionSource.DRAG, salience=0.9,
                       edit_latency_sec=10.0, drag_velocity=0.3, created_at=now)
    store.append_correction(c)
    restored = store.get_corrections()[0]
    assert restored.reliability_weight == pytest.approx(c.reliability_weight)
    assert restored.salience == pytest.approx(0.9)
    assert restored.drag_velocity == pytest.approx(0.3)


# ============================================================
# Baselines + BaselineEvents
# ============================================================


def test_save_and_get_baseline(store):
    b = Baseline(valence=0.62, arousal=0.48, source=BaselineSource.USER_NUDGE,
                 regime_id="regime_1", version=2, confidence=0.7)
    store.save_baseline(b)
    result = store.get_baselines()
    assert len(result) == 1
    assert result[0].valence == pytest.approx(0.62)
    assert result[0].source == BaselineSource.USER_NUDGE
    assert result[0].regime_id == "regime_1"
    assert result[0].version == 2


def test_baseline_version_chain(store):
    b1 = Baseline(valence=0.55, version=1)
    b2 = b1.with_updates(valence=0.60)
    b3 = b2.with_updates(valence=0.65)
    for b in [b1, b2, b3]:
        store.save_baseline(b)
    result = store.get_baselines()
    assert len(result) == 3
    versions = [b.version for b in result]
    assert versions == [1, 2, 3]


def test_append_and_get_baseline_event(store):
    now = time.time()
    e = BaselineShiftEvent(timestamp=now, old_baseline_id="bl_old",
                           new_baseline_id="bl_new", shift_type=BaselineSource.USER_FORK,
                           reason="人生阶段转变", deltas={"valence": -0.15},
                           user_confirmed=True)
    store.append_baseline_event(e)
    result = store.get_baseline_events()
    assert len(result) == 1
    assert result[0].shift_type == BaselineSource.USER_FORK
    assert result[0].deltas == {"valence": -0.15}
    assert result[0].user_confirmed is True


def test_baseline_events_immutable(store):
    now = time.time()
    e = BaselineShiftEvent(timestamp=now, old_baseline_id=None, new_baseline_id="bl_new",
                           shift_type=BaselineSource.USER_NUDGE)
    store.append_baseline_event(e)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM baseline_events")


# ============================================================
# ModelParameters（版本链）
# ============================================================


def test_save_and_get_model_parameters(store):
    p = ModelParameters(ell_valence=320.0, ell_arousal=260.0, sigma_valence=0.18,
                        n_events_fitted=25, version=3, regime_id="regime_1",
                        extra={"coupling": 0.15})
    store.save_model_parameters(p)
    latest = store.get_latest_model_parameters()
    assert latest is not None
    assert latest.ell_valence == pytest.approx(320.0)
    assert latest.n_events_fitted == 25
    assert latest.version == 3
    assert latest.extra == {"coupling": 0.15}


def test_model_parameters_history(store):
    p1 = ModelParameters(version=1, ell_valence=300.0)
    p2 = p1.with_updates(ell_valence=320.0)
    p3 = p2.with_updates(ell_valence=340.0)
    for p in [p1, p2, p3]:
        store.save_model_parameters(p)
    history = store.get_model_parameters_history()
    assert len(history) == 3
    assert [p.version for p in history] == [1, 2, 3]
    # latest 应是 version 3
    assert store.get_latest_model_parameters().version == 3


def test_get_latest_model_parameters_empty(store):
    assert store.get_latest_model_parameters() is None


def test_model_parameters_stage_roundtrip(store):
    p = ModelParameters(n_events_fitted=50)  # stage=PERSONAL
    store.save_model_parameters(p)
    restored = store.get_latest_model_parameters()
    assert restored.stage == p.stage


# ============================================================
# StateEvents（append-only）
# ============================================================


def test_append_and_get_state_event(store):
    e = StateEvent(timestamp=1000.0, event_type=StateEventType.EMOTION_ESTIMATE,
                   payload={"valence": 0.6}, source="estimator", causality_id="c1")
    store.append_state_event(e)
    result = store.get_state_events()
    assert len(result) == 1
    assert result[0].event_type == StateEventType.EMOTION_ESTIMATE
    assert result[0].payload == {"valence": 0.6}
    assert result[0].causality_id == "c1"


def test_state_events_immutable(store):
    store.append_state_event(StateEvent(timestamp=1000.0, event_type=StateEventType.SYSTEM))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM state_events")


def test_state_events_time_window(store):
    for i in range(5):
        store.append_state_event(StateEvent(timestamp=1000.0 + i,
                                            event_type=StateEventType.OBSERVATION))
    window = store.get_state_events(start_ts=1001.0, end_ts=1003.0)
    assert len(window) == 3


# ============================================================
# §22 Migration Test + 集成
# ============================================================


def test_migration_idempotent(tmp_path):
    """§22 Migration Test：重复打开同一库不应破坏数据或版本。"""
    db_path = str(tmp_path / "migrate.db")
    s1 = SQLiteStorage(db_path)
    s1.append_observation(Observation(timestamp=1000.0, valence=0.5))
    v1 = s1.get_schema_version()
    s1.close()

    # 重新打开（触发 _ensure_schema_version / _migrate 路径）
    s2 = SQLiteStorage(db_path)
    assert s2.get_schema_version() == v1 == SCHEMA_VERSION
    assert s2.count_observations() == 1  # 数据未丢失
    s2.close()


def test_full_persistence_workflow(store):
    """端到端：观察→估计→纠正→基线→参数→事件 全链路持久化与读回。"""
    now = time.time()
    # 观察
    obs = [Observation(timestamp=now + i, valence=0.5 + 0.01 * i, arousal=0.4)
           for i in range(10)]
    store.append_observations(obs)
    # 估计
    store.save_emotion_state(EmotionState(timestamp=now + 9, valence=0.59, arousal=0.4))
    # 纠正
    store.append_correction(UserCorrection(
        timestamp=now + 5, dimension=CorrectionDimension.VALENCE,
        predicted_value=0.55, corrected_value=0.7, created_at=now + 6))
    # 基线
    b = Baseline(valence=0.58, arousal=0.42)
    store.save_baseline(b)
    store.append_baseline_event(BaselineShiftEvent(
        timestamp=now + 7, old_baseline_id=None, new_baseline_id=b.baseline_id,
        shift_type=BaselineSource.USER_NUDGE))
    # 参数
    store.save_model_parameters(ModelParameters(ell_valence=310.0, n_events_fitted=1))
    # 事件
    store.append_state_event(StateEvent(timestamp=now + 8,
                                        event_type=StateEventType.MODEL_UPDATE))

    # 全部读回
    assert store.count_observations() == 10
    assert len(store.get_emotion_states()) == 1
    assert store.count_corrections() == 1
    assert len(store.get_baselines()) == 1
    assert len(store.get_baseline_events()) == 1
    assert store.get_latest_model_parameters().ell_valence == pytest.approx(310.0)
    assert len(store.get_state_events()) == 1


def test_context_manager(tmp_path):
    """支持 with 语句。"""
    db_path = str(tmp_path / "ctx.db")
    with SQLiteStorage(db_path) as s:
        s.append_observation(Observation(timestamp=1000.0, valence=0.5))
        assert s.count_observations() == 1
    # 退出后连接已关闭
    with pytest.raises(Exception):
        s.conn.execute("SELECT 1")


def test_concurrent_writes_thread_safe(tmp_path):
    """多线程并发读写不崩溃（MEDIUM 修复：读写全部串行化 + busy_timeout）。

    Flet/Electron UI 常在后台线程回调里读写库（tier.async_persistence）。
    修复前 check_same_thread=False 的共享连接并发 execute/commit 非线程安全，
    不仅写写冲突，读写并发在同一连接上同样不安全。修复后所有公共读写方法
    经 _synchronized 串行化 + PRAGMA busy_timeout=5000。
    """
    import threading

    db_path = str(tmp_path / "concurrent.db")
    store = SQLiteStorage(db_path)
    n_writer = 6
    n_reader = 6
    per_writer = 25
    errors = []

    def writer(tid):
        try:
            for i in range(per_writer):
                store.append_observation(
                    Observation(timestamp=1000.0 + tid * 1000 + i, valence=0.5)
                )
        except Exception as e:  # noqa: BLE001
            errors.append(("write", e))

    def reader(_tid):
        try:
            for _ in range(per_writer):
                store.get_observations(limit=10)
                store.count_observations()
        except Exception as e:  # noqa: BLE001
            errors.append(("read", e))

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_writer)]
    threads += [threading.Thread(target=reader, args=(t,)) for t in range(n_reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发读写抛异常: {errors}"
    assert store.count_observations() == n_writer * per_writer
    store.close()
