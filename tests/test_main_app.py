"""tests/test_main_app.py — MainWindow 主窗口集成测试"""
import pytest
import time
from models import TimeSeriesSample


def test_main_window_creates_with_all_pages(qapp, tmp_path):
    """MainWindow 集成了所有四个功能页面"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)

    assert win.stack.count() == 4  # 四个页面
    assert win.dashboard_page is not None
    assert win.surfing_page is not None
    assert win.summary_page is not None
    assert win.history_page is not None
    db.close()


def test_main_window_navigates_between_pages(qapp, tmp_path):
    """侧边栏按钮可以在四个页面之间切换"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)

    # 初始在仪表盘
    assert win.stack.currentIndex() == 0

    # 切换到情绪冲浪
    win._switch_to(1)
    assert win.stack.currentIndex() == 1

    # 切换到事件回顾
    win._switch_to(2)
    assert win.stack.currentIndex() == 2

    # 切换到历史记录
    win._switch_to(3)
    assert win.stack.currentIndex() == 3
    db.close()


def test_sidebar_toggle_collapses_and_expands(qapp, tmp_path):
    """侧边栏可折叠：展开 168px ↔ 收起 54px，状态位同步翻转"""
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
    assert win.sidebar.width() == main_app.SIDEBAR_W
    db.close()


def test_switch_to_updates_header_title(qapp, tmp_path):
    """统一页头标题随页面切换更新"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)

    win._switch_to(1)
    assert win.page_title.text() == "情绪冲浪"
    win._switch_to(3)
    assert win.page_title.text() == "历史记录"
    db.close()


def test_surfing_finish_navigates_to_summary(qapp, tmp_path):
    """完成情绪记录后自动跳转到事件回顾页面"""
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)

    # 切换到冲浪页面
    win._switch_to(1)
    surfing = win.surfing_page

    # 模拟开始记录
    surfing._toggle_recording()
    assert surfing.recording is True

    # 模拟采样
    now = time.time()
    for i in range(5):
        surfing.samples.append(
            TimeSeriesSample(timestamp=now + i, valence=0.5 - i*0.05, arousal=0.3 + i*0.1)
        )

    # 完成记录
    surfing._finish_recording()

    # 应该跳转到事件回顾页面
    assert win.stack.currentIndex() == 2
    db.close()
