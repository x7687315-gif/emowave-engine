"""tests/test_forecast_eval.py — v5 Phase 6：预测日志与评估底座"""
import json

import pytest


def _fc(target, horizon, v, a):
    return {"target_ts": target, "horizon_sec": horizon, "valence": v, "arousal": a}


def test_evaluate_metrics_by_horizon():
    """按 horizon 分组：MAE 是绝对误差均值，RMSE 是平方误差均值的平方根。"""
    from tools.forecast_eval import evaluate

    observations = [(1000.0, 0.5, 0.4), (1600.0, 0.7, 0.6)]
    forecasts = [
        _fc(1600.0, 300.0, 0.5, 0.5),      # V -0.2, A -0.1
        _fc(1600.0, 300.0, 0.9, 0.7),      # V +0.2, A +0.1
        _fc(1600.0, 3600.0, 0.6, 0.5),     # V -0.1, A -0.1
    ]
    result = evaluate(forecasts, observations, tolerance_sec=10)

    assert result["matched"] == 3
    h300 = result["horizons"][300.0]
    assert h300["n"] == 2
    assert h300["mae_valence"] == pytest.approx(0.2)
    assert h300["rmse_valence"] == pytest.approx(0.2)
    assert h300["mae_arousal"] == pytest.approx(0.1)

    h3600 = result["horizons"][3600.0]
    assert h3600["n"] == 1
    assert h3600["mae_valence"] == pytest.approx(0.1)
    assert result["overall"]["n"] == 3


def test_evaluate_rejects_targets_outside_tolerance():
    from tools.forecast_eval import evaluate

    result = evaluate([_fc(2000.0, 300.0, 0.5, 0.5)],
                      [(1000.0, 0.5, 0.5)], tolerance_sec=10)
    assert result["matched"] == 0
    assert result["overall"]["n"] == 0
    assert result["overall"]["mae_valence"] is None


def test_load_observations_and_forecasts(tmp_path):
    from db import DatabaseManager
    from tools.forecast_eval import load_forecasts, load_observations

    db = DatabaseManager(str(tmp_path / "eval.db"))
    db.save_event({
        "event_id": "e1",
        "start_time": 1000.0, "end_time": 1001.0,
        "peak_valence": 0.6, "peak_arousal": 0.4, "peak_intensity": 0.1,
        "sample_count": 2,
        "raw_data": json.dumps([[1000.0, 0.6, 0.4], [1001.0, 0.6, 0.4]]),
    })
    db.set_state("active_wave", json.dumps(
        {"samples": [[1002.0, 0.7, 0.5]]}))
    db.save_forecasts([
        (1000.0, 1300.0, 300.0, 0.6, 0.4, 0.01, 0.01, "sample"),
    ])

    observations = load_observations(db.conn)
    forecasts = load_forecasts(db.conn)

    assert [o[0] for o in observations] == [1000.0, 1001.0, 1002.0]
    assert len(forecasts) == 1
    assert forecasts[0]["target_ts"] == 1300.0
    assert forecasts[0]["horizon_sec"] == 300.0
    db.close()


def test_forecast_logger_logs_causal_predictions(tmp_path):
    from db import DatabaseManager
    from presenters.console_presenter import ConsolePresenter
    from tests.helpers import FakeClock

    db = DatabaseManager(str(tmp_path / "log.db"))
    clock = FakeClock()
    p = ConsolePresenter(db=db, clock=clock)
    p.toggle_recording(True)
    clock.advance(1)
    p.sample(0.80, 0.60)
    p.toggle_recording(False)

    rows = db.get_forecast_rows()
    assert len(rows) == 6
    assert {r["horizon_sec"] for r in rows} == {
        300.0, 900.0, 1800.0, 3600.0, 14400.0, 86400.0}
    base = rows[0]["base_ts"]
    assert all(r["base_ts"] == pytest.approx(base) for r in rows)
    assert all(r["target_ts"] == pytest.approx(base + r["horizon_sec"])
               for r in rows)
    assert len(p.observations) == 1          # 预测没有变成观察
    db.close()


def test_forecast_logger_throttles_decay(tmp_path):
    from db import DatabaseManager
    from presenters.console_presenter import ConsolePresenter
    from tests.helpers import FakeClock

    db = DatabaseManager(str(tmp_path / "throttle.db"))
    clock = FakeClock()
    p = ConsolePresenter(db=db, clock=clock)
    p.toggle_recording(True)
    clock.advance(1)
    p.sample(0.80, 0.60)
    p.toggle_recording(False)
    assert len(db.get_forecast_rows()) == 6      # 新观察留了一份

    clock.advance(10)
    p.decay_step()
    assert len(db.get_forecast_rows()) == 6      # 10s < 60s → 不重复写

    clock.advance(60)
    p.decay_step()
    assert len(db.get_forecast_rows()) == 12     # 满 60s → 再留一份
    db.close()
