"""tests/test_main_app.py — MainWindow 单页控制台集成测试"""
import pytest


def test_main_window_creates_with_console(qapp, tmp_path):
    """MainWindow 创建单页控制台，包含全部八个分区锚点"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)

    assert win.console is not None
    for key in ["state", "curve", "adjust", "baseline",
                "model", "correction", "summary", "history"]:
        assert key in win.console.sections
    db.close()


def test_sidebar_toggle_collapses_and_expands(qapp, tmp_path):
    """侧边栏可折叠：展开 150px ↔ 收起 54px"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)

    assert win.sidebar_collapsed is False
    assert win.sidebar.width() == main_app.SIDEBAR_W

    win.toggle_sidebar()
    assert win.sidebar_collapsed is True
    assert win.sidebar.width() == main_app.SIDEBAR_W_COLLAPSED

    win.toggle_sidebar()
    assert win.sidebar_collapsed is False
    db.close()


def test_jump_updates_header_title(qapp, tmp_path):
    """锚点导航更新页头标题"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)

    win._jump(3)
    assert win.page_title.text() == "基线主权"
    win._jump(7)
    assert win.page_title.text() == "历史记录"
    db.close()


def test_console_recording_samples_state(qapp, tmp_path):
    """控制台记录模式：采样喂给 2.0 估计器并更新状态/曲线"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)
    console = win.console

    console._toggle_recording()
    assert console.recording is True
    for _ in range(3):
        console._sample()
    console._toggle_recording()
    assert console.recording is False

    assert len(console.observations) == 3
    assert len(console.states) == 3
    db.close()


def test_console_baseline_nudge_and_fork(qapp, tmp_path):
    """基线主权：nudge 改变基线，fork 产生新 regime"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)
    console = win.console

    before = console.baseline_ctrl.current.valence
    console._nudge(+0.05)
    assert console.baseline_ctrl.current.valence == pytest.approx(before + 0.05)

    n_regimes = len(console.baseline_ctrl.regimes)
    console._fork()
    assert len(console.baseline_ctrl.regimes) == n_regimes + 1
    db.close()


def test_console_correction_feeds_calibrator(qapp, tmp_path):
    """纠正：提交后进入 calibrator 数据集"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)
    console = win.console

    console._sample()
    console.cv_slider.setValue(80)
    console._submit_correction()

    assert len(console.calibrator.dataset) == 1
    db.close()
