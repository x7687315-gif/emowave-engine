"""tests/test_main_app.py — v3 主窗口集成测试

=== 与 v2 的差异 ===
v2 测的是「8 分区锚点 + 可折叠侧栏 + 隐藏式分区」；v3 信息架构换了：
  · 外壳 = 56px 图标列 + 极简页头（无菜单栏）
  · 主界面 = MainConsole 4 区块（状态 / 读数 / 曲线 / 输入台）
  · 次要区（基线主权 / 个人模型 / 回顾历史）收进右侧抽屉
  · 纠正 = 底部上滑框（模态，测试走 ingest_corrections 绕开 exec_）

因此本文件按新接口重写，而不是改断言凑通过。
"""
import time

import pytest


def _make_window(tmp_path):
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db)
    return win, db


def test_main_window_creates_with_v3_console(qapp, tmp_path):
    """MainWindow 挂载 v3 控制台：4 区块 + 抽屉，且不再有 v2 的锚点分区"""
    win, db = _make_window(tmp_path)

    console = win.console
    assert console is not None

    # 4 区块
    for attr in ["status", "readout", "curve", "input_panel"]:
        assert hasattr(console, attr), f"缺少区块 {attr}"

    # 抽屉（基线 / 模型 / 回顾历史）
    assert console.drawer is not None
    assert console.drawer.is_open() is False

    # v2 的接口已移除
    assert not hasattr(console, "sections")
    assert not hasattr(console, "collapsibles")
    db.close()


def test_rail_is_56px_icon_column(qapp, tmp_path):
    """外壳：56px 图标列 + 无菜单栏"""
    import main_app
    from theme import METRICS

    win, db = _make_window(tmp_path)

    # 菜单栏已删除（v3 用页头图标按钮代替）
    assert win.menuBar().actions() == []
    assert METRICS['rail_w'] == 56
    db.close()


def test_drawer_toggles_open_and_closed(qapp, tmp_path):
    """抽屉：默认关闭 → toggle 打开 → toggle 关闭"""
    win, db = _make_window(tmp_path)
    console = win.console

    assert console.drawer.is_open() is False
    console.toggle_drawer()
    assert console.drawer.is_open() is True
    console.toggle_drawer()
    assert console.drawer.is_open() is False
    db.close()


def test_console_recording_samples_state(qapp, tmp_path):
    """记录模式：无参 _toggle_recording 切换状态，采样喂给 2.0 估计器"""
    win, db = _make_window(tmp_path)
    console = win.console

    assert console.recording is False
    console._toggle_recording()               # 无参 → 取反
    assert console.recording is True

    for _ in range(3):
        console._sample()

    console._toggle_recording()
    assert console.recording is False

    assert len(console.observations) == 3
    assert len(console.states) == 3
    db.close()


def test_input_panel_button_label_follows_state(qapp, tmp_path):
    """回归：按钮文案必须跟随状态翻转（v3 初版传的是翻转前的旧值，永不变化）"""
    win, db = _make_window(tmp_path)
    ip = win.console.input_panel

    assert ip.btn_record.text().startswith("●")     # 初始：开始记录
    win.console._toggle_recording(True)
    assert ip.btn_record.text().startswith("■")     # 记录中：停止记录
    win.console._toggle_recording(False)
    assert ip.btn_record.text().startswith("●")
    db.close()


def test_baseline_nudge_fork_and_reset(qapp, tmp_path):
    """基线主权：nudge 改变基线，fork 产生新 regime，reset 回到群体先验

    注意 reset 的目标是**群体先验**（Baseline() 默认 0.55/0.42），不是 0.50。
    """
    from emowave.core.domain.baseline import Baseline, BaselineSource

    win, db = _make_window(tmp_path)
    console = win.console
    population = Baseline()

    before = console.baseline_ctrl.current.valence
    console._nudge(+0.05)
    assert console.baseline_ctrl.current.valence == pytest.approx(before + 0.05)
    # 同步进估计器（否则曲线基线画的是旧值）
    assert console.estimator.baseline.valence == pytest.approx(before + 0.05)

    n_regimes = len(console.baseline_ctrl.regimes)
    console._fork()
    assert len(console.baseline_ctrl.regimes) == n_regimes + 1

    console._reset_baseline()
    assert console.baseline_ctrl.current.valence == pytest.approx(population.valence)
    assert console.baseline_ctrl.current.source == BaselineSource.USER_RESET
    db.close()


def test_correction_feeds_calibrator(qapp, tmp_path):
    """纠正：上滑框提交的 (ts, v, a) 写进 calibrator 数据集

    上滑框是模态的（exec_ 阻塞），所以直接测 ingest_corrections——
    它就是把 applied 落到 calibrator 的那一步。

    UserCorrection 是**单维度**的，所以同时改效价+唤醒 = 2 条记录。
    """
    win, db = _make_window(tmp_path)
    console = win.console

    console._sample()
    ts = console.states[-1][0]
    pred = console.states[-1][1]

    n = console.ingest_corrections([(ts, 0.8, 0.3)])
    assert n == 2                                  # 效价 + 唤醒 各一条
    assert len(console.calibrator.dataset) == 2

    recs = console.calibrator.dataset.records
    dims = {r['dimension']: r for r in recs}
    assert set(dims) == {"valence", "arousal"}
    assert dims['valence']['corrected'] == pytest.approx(0.8)
    assert dims['valence']['predicted'] == pytest.approx(pred.valence)
    assert dims['arousal']['corrected'] == pytest.approx(0.3)
    db.close()


def test_correction_skips_untouched_dimension(qapp, tmp_path):
    """纠正：只拖了效价时，不该给唤醒也造一条 delta=0 的假纠正"""
    win, db = _make_window(tmp_path)
    console = win.console

    console._sample()
    ts = console.states[-1][0]
    pred = console.states[-1][1]

    # 唤醒保持模型原值 → 只应产生 1 条效价纠正
    n = console.ingest_corrections([(ts, 0.8, pred.arousal)])
    assert n == 1
    recs = console.calibrator.dataset.records
    assert recs[0]['dimension'] == "valence"
    db.close()


def test_correction_ignores_unknown_timestamp(qapp, tmp_path):
    """纠正：对不上任何状态的时刻应被丢弃，而不是塞进数据集"""
    win, db = _make_window(tmp_path)
    console = win.console

    console._sample()
    n = console.ingest_corrections([(time.time() + 9999, 0.8, 0.3)])
    assert n == 0
    assert len(console.calibrator.dataset) == 0
    db.close()


def test_app_qss_is_injected_once(qapp, tmp_path):
    """主题：一份 app 级 QSS（修 QSS 不继承 font/color），覆盖主要控件类型"""
    from theme import build_app_qss, COLORS

    qss = build_app_qss()
    assert len(qss) > 1000

    # QSS 不继承 → 必须逐类型声明 font/color，抽样几个关键类型
    for sel in ["QWidget", "QLabel", "QPushButton", "QSlider"]:
        assert sel in qss, f"{sel} 未在全局 QSS 中声明"

    # 暖色板 token 必须在（v3 不再是 Linear 蓝）
    assert COLORS['accent'].upper() == "#BE5E38"
    assert COLORS['paper'].upper() == "#F2ECE1"
    db = None


def test_curve_widget_renders_both_series(qapp, tmp_path):
    """曲线：效价 + 唤醒双序列都能画（v2 只画效价，唤醒从未可视化）"""
    from curve_widget import EmotionCurveWidget

    w = EmotionCurveWidget(series='both')
    n = 60
    ts = [1000.0 + i for i in range(n)]
    mv = [0.5 + 0.1 * (i / n) for i in range(n)]
    ma = [0.5 - 0.1 * (i / n) for i in range(n)]
    w.set_data(ts, mv, ma,
               [0.01] * n, [0.01] * n,
               baseline_v=0.5,
               raw=[(t, v) for t, v in zip(ts, mv)])
    assert len(w.timestamps) == n
    assert len(w.model_v) == n
    assert len(w.model_a) == n


def test_curve_widget_render_performance(qapp, tmp_path):
    """性能：n=20000 平滑数据单次渲染应远低于 149ms 目标"""
    from PyQt5.QtGui import QPixmap
    from curve_widget import EmotionCurveWidget

    w = EmotionCurveWidget(series='valence')
    w.resize(1000, 320)
    n = 20000
    ts = [1000.0 + i * 0.5 for i in range(n)]
    mv = [0.5 + 0.2 * (i / n) for i in range(n)]
    w.set_data(ts, mv, [0.5] * n, [0.01] * n, [0.01] * n,
               baseline_v=0.5, raw=[])

    pm = QPixmap(w.size())
    t0 = time.perf_counter()
    w.render(pm)
    el = (time.perf_counter() - t0) * 1000
    assert el < 149, f"渲染 {el:.1f}ms 超过 149ms 目标"
