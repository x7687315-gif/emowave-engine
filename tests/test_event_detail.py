"""tests/test_event_detail.py — 事件补全/修正窗口 + db.update_event 回归。"""
import json
import time

from db import DatabaseManager


def _seed(db, event_id="e1"):
    """写一条"未记录"细节的事件（触发/躯体/自评皆空）。"""
    now = time.time()
    db.save_event({
        "event_id": event_id, "start_time": now - 10, "end_time": now,
        "peak_valence": 0.4, "peak_arousal": 0.7, "peak_intensity": 0.5,
        "sample_count": 5, "trigger_tags": [], "coping_methods": [],
        "coping_ratings": {}, "body_symptoms": [], "user_peak_rating": None,
        "raw_data": "[]",
    })
    return event_id


def test_update_event_preserves_created_at_and_peaks(tmp_path):
    db = DatabaseManager(str(tmp_path / "t.db"))
    eid = _seed(db)
    created = db.get_event(eid)["created_at"]
    n = db.update_event(eid, {"trigger_tags": ["工作压力"],
                              "body_symptoms": ["心慌"], "user_peak_rating": 8.0})
    after = db.get_event(eid)
    assert n == 3
    assert json.loads(after["trigger_tags"]) == ["工作压力"]
    assert json.loads(after["body_symptoms"]) == ["心慌"]
    assert after["user_peak_rating"] == 8.0
    assert after["created_at"] == created          # 补全不改时间（否则历史日期会跳）
    assert after["peak_arousal"] == 0.7            # 峰值等原字段不动
    db.close()


def test_update_event_ignores_non_editable_keys(tmp_path):
    db = DatabaseManager(str(tmp_path / "t.db"))
    eid = _seed(db)
    assert db.update_event(eid, {"peak_arousal": 0.99, "evil": "x"}) == 0
    assert db.get_event(eid)["peak_arousal"] == 0.7
    db.close()


def test_event_detail_dialog_prefill_collect_save(qapp, tmp_path):
    from windows.event_detail_dialog import EventDetailDialog
    db = DatabaseManager(str(tmp_path / "t.db"))
    eid = _seed(db)
    dlg = EventDetailDialog(db.get_event(eid), db)
    # 初始未勾选（事件里是空的）
    assert dlg.collect()["trigger_tags"] == []
    dlg.trigger_grid._boxes[0].setChecked(True)     # 工作压力
    dlg.body_grid._boxes[0].setChecked(True)        # 心慌
    dlg.rating_slider.setValue(9)
    fields = dlg.collect()
    assert fields["trigger_tags"] == ["工作压力"]
    assert fields["body_symptoms"] == ["心慌"]
    assert fields["user_peak_rating"] == 9.0
    assert dlg.save() is True
    assert json.loads(db.get_event(eid)["trigger_tags"]) == ["工作压力"]
    db.close()


def test_event_detail_dialog_prefills_existing_tags(qapp, tmp_path):
    from windows.event_detail_dialog import EventDetailDialog
    db = DatabaseManager(str(tmp_path / "t.db"))
    eid = _seed(db)
    db.update_event(eid, {"trigger_tags": ["人际冲突"], "user_peak_rating": 6.0})
    dlg = EventDetailDialog(db.get_event(eid), db)
    assert dlg.collect()["trigger_tags"] == ["人际冲突"]
    assert dlg.rating_slider.value() == 6
    db.close()


def test_summary_edit_button_gating(qapp, tmp_path):
    from session import SessionController
    from windows.event_summary_window import EventSummaryWindow
    db = DatabaseManager(str(tmp_path / "t.db"))
    win = EventSummaryWindow(SessionController(db))
    assert hasattr(win, "edit_btn")
    assert win.edit_btn.isEnabled() is False        # 无事件 → 禁用
    eid = _seed(db)
    win.show_event(eid)
    assert win.edit_btn.isEnabled() is True         # 有事件 + 有库 → 可补全
    db.close()
