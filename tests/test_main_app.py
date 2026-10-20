"""tests/test_main_app.py — v3.1 主窗口集成测试

=== 与 v3 的差异 ===
v3.1 小窗化（400×680）换了信息架构：
   · 外壳 = 页头（44px）+ 顶部 Tab 栏（40px），**无菜单栏、无左侧图标列**
   · 页 0 曲线（4 区块）/ 页 1 模型（基线主权 + 个人模型）/ 页 2 历史（回顾 + 记录）
   · v3 的右侧 380px 抽屉**已删除**（它比小窗本身还宽，物理上无法并排）
   · 纠正 = 底部上滑框（模态，测试走 ingest_corrections 绕开 exec_）

因此本文件按新接口重写，而不是改断言凑通过。
"""
import time

import pytest
from PyQt5.QtWidgets import QLabel


def _make_window(tmp_path, clock=None):
    import main_app
    from db import DatabaseManager

    db = DatabaseManager(str(tmp_path / "test.db"))
    win = main_app.MainWindow(db, clock=clock)
    return win, db


def test_main_window_creates_with_v3_console(qapp, tmp_path):
    """MainWindow 挂载 v3.1 控制台：4 区块 + 三页，且不再有 v2 的锚点分区"""
    win, db = _make_window(tmp_path)

    console = win.console
    assert console is not None

    # 4 区块
    for attr in ["status", "readout", "curve", "input_panel"]:
        assert hasattr(console, attr), f"缺少区块 {attr}"

    # 三页 + 默认停在曲线页
    assert win.tabs is not None
    assert win.tabs.current_index() == 0
    assert console.stack.currentIndex() == 0
    assert console.page_curve is not None
    assert console.page_model is not None
    assert console.page_history is not None

    # v2 的接口已移除
    assert not hasattr(console, "sections")
    assert not hasattr(console, "collapsibles")
    db.close()


def test_window_defaults_to_phone_proportions(qapp, tmp_path):
    """小窗化：默认 400×680，最小 360×600（约 9:15.5 竖屏）。"""
    win, db = _make_window(tmp_path)

    assert win.size().width() == 400 and win.size().height() == 680
    assert win.minimumSize().width() == 360 and win.minimumSize().height() == 600
    db.close()


def test_shell_has_no_rail_and_has_toptabs(qapp, tmp_path):
    """v3 的 56px 图标列已删除，换成顶部 Tab 栏；菜单栏仍为空。"""
    from theme import METRICS

    win, db = _make_window(tmp_path)

    # 菜单栏已删除（v3.1 用顶部 Tab + 页头图标代替）
    assert win.menuBar().actions() == []
    # 左侧图标列已删除
    assert not hasattr(win, "btn_curve")
    assert not hasattr(win, "btn_drawer")
    assert 'rail_w' not in METRICS
    assert 'drawer_w' not in METRICS
    # 顶部 Tab 栏就位
    assert METRICS['tabbar_h'] == 40
    assert win.tabs.height() == 40
    db.close()


def test_tabs_switch_pages(qapp, tmp_path):
    """顶部 Tab：默认曲线页 → 切模型页 → 切历史页 → 回曲线页。"""
    win, db = _make_window(tmp_path)
    console = win.console

    assert console.stack.currentIndex() == 0
    win.tabs.set_current(1)
    assert console.stack.currentIndex() == 1
    win.tabs.set_current(2)
    assert console.stack.currentIndex() == 2
    win.tabs.set_current(0)
    assert console.stack.currentIndex() == 0
    db.close()


# ----------------------------------------------------------------
# 回归：v3 曾用「抽屉实际宽度 0↔380」防住两个真实 bug ——
#   ① setFixedWidth 把 minimumWidth 钉成 380，maximumWidth 动画收不回 0，
#      抽屉永远关不上（点抽屉按钮"点不进去"）；
#   ② 情绪曲线按钮根本没接 clicked（死按钮）。
# 抽屉随小窗化删除后，等价约束改为「**页面真的切换**」与「**按钮真的被接上**」。
# ----------------------------------------------------------------
def _settle(app, ms=320):
    """跑一段事件循环，让 QPropertyAnimation（时间驱动）真正走完。"""
    from PyQt5.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()
    app.processEvents()


def test_default_page_is_curve_and_others_hidden(qapp, tmp_path):
    """回归①等价约束：启动必须真的停在曲线页，模型/历史页不可见。"""
    win, db = _make_window(tmp_path)
    win.show()
    _settle(qapp)

    console = win.console
    assert console.stack.currentIndex() == 0
    assert console.page_curve.isVisible()
    assert not console.page_model.isVisible()
    assert not console.page_history.isVisible()
    db.close()


def test_tab_click_actually_switches_page(qapp, tmp_path):
    """回归①等价约束：点 Tab 必须真的换页（可见性跟着变），而非只翻标志位。"""
    win, db = _make_window(tmp_path)
    win.show()
    _settle(qapp)
    console = win.console

    win.tabs.button(1).click()
    _settle(qapp)
    assert console.stack.currentIndex() == 1
    assert console.page_model.isVisible()
    assert not console.page_curve.isVisible()

    win.tabs.button(0).click()
    _settle(qapp)
    assert console.stack.currentIndex() == 0
    assert console.page_curve.isVisible()
    assert not console.page_model.isVisible()
    db.close()


def test_curve_tab_is_wired(qapp, tmp_path):
    """回归②等价约束：从别的页点「曲线」Tab 必须回到曲线页（过去是死按钮）。"""
    win, db = _make_window(tmp_path)
    win.show()
    _settle(qapp)
    console = win.console

    win.tabs.set_current(2)          # 先去历史页
    _settle(qapp)
    assert console.stack.currentIndex() == 2

    win.tabs.button(0).click()       # 点曲线 Tab → 回曲线页
    _settle(qapp)
    assert console.stack.currentIndex() == 0
    assert console.page_curve.isVisible()
    db.close()


def test_tab_active_state_follows_page(qapp, tmp_path):
    """当前视图高亮：曲线页=曲线亮、其他灭；切页后随之切换。"""
    win, db = _make_window(tmp_path)
    win.show()
    _settle(qapp)

    assert win.tabs.button(0).isChecked() is True
    assert win.tabs.button(1).isChecked() is False
    win.tabs.set_current(1); _settle(qapp)
    assert win.tabs.button(1).isChecked() is True
    assert win.tabs.button(0).isChecked() is False
    win.tabs.set_current(0); _settle(qapp)
    assert win.tabs.button(0).isChecked() is True
    assert win.tabs.button(1).isChecked() is False
    db.close()


def test_secondary_pages_are_scrollable(qapp, tmp_path):
    """回归：次要页内容可滚动，靠下的「事件回顾/历史记录」才够得着。"""
    from PyQt5.QtWidgets import QScrollArea
    win, db = _make_window(tmp_path)
    console = win.console
    assert console.page_model.findChild(QScrollArea) is not None
    assert console.page_history.findChild(QScrollArea) is not None
    db.close()


def test_secondary_cards_live_in_their_pages(qapp, tmp_path):
    """抽屉三段的归属：基线/模型 → 模型页；回顾历史 → 历史页。"""
    win, db = _make_window(tmp_path)
    console = win.console

    assert console.baseline_card is not None and console.legacy is not None
    assert console.page_model.findChild(type(console.baseline_card)) is not None
    assert console.page_history.findChild(type(console.legacy)) is not None
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


def test_recording_stop_persists_one_event(qapp, tmp_path):
    """回归：一次「开始→停止记录」必须把该段观察聚合成一条 emotion_events 落库。
    v3 此前从不写事件表，导致抽屉「事件回顾/历史记录」永远空。"""
    win, db = _make_window(tmp_path)
    console = win.console

    assert db.get_recent_events(limit=10) == []      # 起始无事件
    console._toggle_recording(True)
    for _ in range(4):
        console._sample()
    console._toggle_recording(False)

    events = db.get_recent_events(limit=10)
    assert len(events) == 1
    ev = events[0]
    assert ev["sample_count"] == 4
    assert 0.0 <= ev["peak_arousal"] <= 1.0
    assert 0.0 <= ev["peak_valence"] <= 1.0
    assert 0.0 <= ev["peak_intensity"] <= 1.0 + 1e-9
    db.close()


def test_legacy_panels_populate_after_recording(qapp, tmp_path):
    """回归：db 注入后，历史页的 HistoryWindow 在 refresh 时按今天日期填充事件表。"""
    win, db = _make_window(tmp_path)
    console = win.console

    console._toggle_recording(True)
    for _ in range(3):
        console._sample()
    console._toggle_recording(False)

    console.legacy.refresh()                          # 打开抽屉/落库后都会调
    assert console.legacy.history.events_table.rowCount() >= 1
    db.close()


def test_recording_without_db_degrades_safely(qapp):
    """回归：无 db 时记录/停止/刷新都不该抛异常（主界面仍可脱库启动）。"""
    from windows.main_console import MainConsole
    c = MainConsole(db=None)
    c._toggle_recording(True)
    c._sample()
    c._sample()
    c._toggle_recording(False)                         # db=None → 落库安全跳过
    c.legacy.refresh()                                 # db=None → 安全跳过
    assert c.recording is False
    assert c.db is None


# ----------------------------------------------------------------
# 精力人群先验（A/B）+ 改点即时重拟合显示曲线（C）
# ----------------------------------------------------------------
def test_console_seeds_from_high_archetype(qapp):
    """回归：选高精力 → 用高精力先验 seed（ℓ 更短、基线唤醒更高）。"""
    from windows.main_console import MainConsole
    c = MainConsole(db=None, archetype_key="high")
    assert c.archetype_key == "high"
    assert c.params.ell_arousal == 180.0 and c.params.ell_valence == 240.0
    assert c.baseline_ctrl.current.arousal == 0.60
    assert c.estimator.params is c.params


def test_console_default_is_medium_population_prior(qapp):
    """回归：不选人群 = 中精力 = 群体先验（与旧冷启动一致）。"""
    from windows.main_console import MainConsole
    from emowave import ModelParameters
    c = MainConsole(db=None)
    pop = ModelParameters.population_prior()
    assert c.archetype_key == "medium"
    assert (c.params.ell_valence, c.params.ell_arousal) == (pop.ell_valence, pop.ell_arousal)
    assert c.baseline_ctrl.current.arousal == 0.42


def test_set_archetype_reseeds_and_clears_session(qapp):
    """回归：切到低精力 → 重 seed 参数/基线并清空当前会话曲线。"""
    from windows.main_console import MainConsole
    c = MainConsole(db=None, archetype_key="high")
    c._toggle_recording(True)
    c._sample(); c._sample()
    assert len(c.observations) == 2
    c.set_archetype("low")
    assert c.params.ell_arousal == 320.0
    assert c.baseline_ctrl.current.arousal == 0.25
    assert c.observations == [] and c.states == [] and c._edits == []


def test_personalization_anchor_follows_archetype(qapp):
    """回归：个性化学习收缩的锚 = 所选精力人群先验（非单一全局先验），换人群即随之变。"""
    from windows.main_console import MainConsole
    c = MainConsole(db=None, archetype_key="high")
    assert c.calibrator._population.ell_arousal == 180.0   # 高精力先验作锚
    c.set_archetype("low")
    assert c.calibrator._population.ell_arousal == 320.0   # 低精力先验作锚


def test_correction_refits_displayed_curve(qapp, tmp_path):
    """回归（C 核心）：提交一次效价纠正 → 显示曲线（RTS 平滑）随之重拟合。"""
    win, db = _make_window(tmp_path)
    c = win.console
    c._toggle_recording(True)
    for _ in range(6):
        c._sample()
    c._toggle_recording(False)

    before = list(c.curve.model_v)                    # 默认滑块 v=0.5 → 平滑峰 ~0.5
    ts = c.states[3][0]
    n = c.ingest_corrections([(ts, 0.95, c.states[3][1].arousal)])
    after = list(c.curve.model_v)

    assert n >= 1
    assert after != before                            # 曲线确实变了
    assert max(after) > max(before)                   # 被拉到 0.95 处隆起
    db.close()


def test_mainwindow_reads_saved_archetype(qapp, tmp_path):
    """回归：db 里存过 archetype=high → 重启 MainWindow 直接按高精力 seed。"""
    import main_app
    from db import DatabaseManager
    db = DatabaseManager(str(tmp_path / "a.db"))
    db.set_state("archetype", "high")
    win = main_app.MainWindow(db)
    assert win.console.archetype_key == "high"
    assert win.console.params.ell_arousal == 180.0
    db.close()


def test_time_axis_formatting_adapts_to_span(qapp):
    """回归：时间轴按可见跨度自适应精度（短记录显示到秒，别再三个刻度都同分）。"""
    import time
    from curve_widget import _fmt_time
    t0 = time.time()
    assert _fmt_time(t0, span=30).count(":") == 2        # HH:MM:SS
    assert _fmt_time(t0, span=600).count(":") == 1      # HH:MM
    assert "-" in _fmt_time(t0, span=90000)              # 跨天带日期


def test_header_date_label_is_locale_safe(qapp, tmp_path):
    """回归：页头日期不能依赖系统 locale 代码页编码中文。

    v3/v3.1 曾写 `today.strftime("%m月%d日")`——strftime 会把格式串里的中文字面量
    按**系统 ANSI 代码页**编码。本机是 936(GBK) 所以看不出问题，而 en-US(1252)
    的 CI runner 上直接 UnicodeEncodeError，连带 29 条测试全红。
    现在中文由 f-string 拼接，strftime 只做纯 ASCII 的 %m/%d。
    """
    win, db = _make_window(tmp_path)
    win.show()
    _settle(qapp)

    header = win.centralWidget().layout().itemAt(0).widget()
    labels = header.findChildren(QLabel)
    date_texts = [l.text() for l in labels if "月" in l.text() or "周" in l.text()]
    assert date_texts, "页头应显示含 月/日/周 的日期"
    assert any("月" in t and "日" in t and "周" in t for t in date_texts)

    # 源码层面：所有 strftime 调用的格式串必须是纯 ASCII
    # （不能出现"月"这类中文字面量，否则依赖系统 ANSI 代码页）
    import ast
    import main_app
    src = open(main_app.__file__, encoding="utf-8").read()
    bad = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "strftime" and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if not arg.value.isascii():
                    bad.append((arg.lineno, arg.value))
    assert not bad, f"strftime 格式串含非 ASCII（会被 locale 代码页编码失败）: {bad}"
    db.close()


def test_state_head_merges_status_and_readout(qapp, tmp_path):
    """阶段 2：状态卡与读数条合并成一条状态头，v3 的属性名全部保留。"""
    win, db = _make_window(tmp_path)
    console = win.console

    # 四读数 + 状态名 + 纠正入口都还在（v3 契约）
    for attr in ("state_label", "state_hint", "corr_btn",
                 "v_lbl", "a_lbl", "c_lbl", "ci_lbl"):
        assert hasattr(console.status, attr), f"状态头缺少 {attr}"
    # readout 是兼容别名，指向同一个状态头
    assert console.readout is console.status
    # update_values / update_state 两个 v3 入口都可用
    console.readout.update_values(0.62, 0.44, 0.91, "58 – 66")
    assert console.status.v_lbl.value_label.text() == "62"
    assert console.status.ci_lbl.value_label.text() == "58 – 66"
    console.status.update_state(0.62, 0.44)
    assert console.status.state_label.text() == "平静 · 偏积极"
    db.close()


def test_params_explain_moved_to_model_page(qapp, tmp_path):
    """阶段 2：220px 参数说明折叠面板移出状态头，常驻「模型」Tab。"""
    win, db = _make_window(tmp_path)
    console = win.console

    assert console.params_card is not None
    assert console.page_model.findChild(type(console.params_card)) is not None
    # 刷新一次读数后四格有值（不是占位符）
    console._toggle_recording(True)
    console._sample()
    for cell in console.params_card._cells:
        assert cell[0].text() not in ("", "—")
    db.close()


def test_curve_min_height_restored(qapp, tmp_path):
    """阶段 2：状态头合并后曲线下限回到 340px（阶段 1 曾压到 220）。"""
    win, db = _make_window(tmp_path)
    assert win.console.curve.minimumHeight() == 340
    db.close()


def test_widgets_palette_unified(qapp, tmp_path):
    """阶段 2：widgets.py 复用 theme.COLORS，抽屉遗留窗口不再用冷色板。"""
    win, db = _make_window(tmp_path)

    import widgets
    from theme import COLORS

    assert widgets.COLORS is COLORS
    # 遗留窗口的键在 theme 里都有（不会 KeyError）
    for key in ("accent", "bg", "cyan", "ink_soft", "muted", "rule", "surface",
                "accent_soft", "ink", "ink_2"):
        assert key in widgets.COLORS
    db.close()


def test_mini_window_shares_presenter(qapp, tmp_path):
    """阶段 4：迷你挂件与主窗共用同一个 presenter（解耦的验收点）。"""
    win, db = _make_window(tmp_path)

    assert win.mini is not None
    assert win.mini.presenter is win.console.presenter
    # 默认隐藏（用户主动唤起）
    assert win.mini.isVisible() is False
    # 页头有唤起按钮且能切换显隐
    assert win.b_mini is not None
    win._toggle_mini()
    assert win.mini.isVisible() is True
    win._toggle_mini()
    assert win.mini.isVisible() is False
    db.close()


def test_mini_window_mirrors_main_view(qapp, tmp_path):
    """阶段 4：主窗采样后，挂件的读数/状态名/按钮三态必须实时跟上。"""
    win, db = _make_window(tmp_path)
    mini = win.mini
    mini.show()
    _settle(qapp)

    # 主窗采样（ presenter 驱动）
    win.console._toggle_recording(True)
    for _ in range(3):
        win.console._sample()
    _settle(qapp)

    assert mini.v_cell.value_label.text() != "—"
    assert mini.a_cell.value_label.text() != "—"
    assert mini.state_label.text() != "—"
    assert mini.btn_record.text().startswith("■")     # 记录中

    # 挂件自己的滑杆也能作为输入源（共用同一 presenter）
    mini.a_slider['slider'].setValue(70)
    _settle(qapp)
    assert win.console.presenter._input_a == pytest.approx(0.70)

    # 推演中按钮禁用态
    win.console._toggle_recording(False)
    _settle(qapp)
    assert mini.btn_record.text().startswith("◌")
    assert mini.btn_record.isEnabled() is False
    db.close()


def test_model_card_pulls_latest_params(qapp, tmp_path):
    """阶段 5：ModelCard 用 provider 拉取，换人群/学习一步后不再显示旧值。"""
    win, db = _make_window(tmp_path)
    card = win.console.model_card
    console = win.console

    # ① 换人群 → ℓ 应变成低精力先验的 380（v3 这里会停在旧的 240）
    console.set_archetype("low")
    card.refresh()
    assert card._lbl_ell.value_label.text() == "380"
    assert card._lbl_stage.value_label.text() == "cold_start"

    # ② 学习一步（换人群会清空会话，所以先重新记录 + 做一次纠正）
    console._toggle_recording(True)
    for _ in range(3):
        console._sample()
    console._toggle_recording(False)
    ts = console.states[-1][0]
    console.ingest_corrections([(ts, 0.9, 0.5)])
    console._learn()
    card.refresh()

    # 有纠正数据集 → 走 warm 路径，n_events_fitted +1（v3 会停在 0）
    assert card._lbl_n.value_label.text() != "0"
    # ℓ 必须与 presenter 当前 params 一致（不是学习前的旧值）
    assert int(card._lbl_ell.value_label.text()) == int(round(
        console.presenter.params.ell_valence))
    db.close()


def test_mini_window_syncs_state_on_late_attach(qapp, tmp_path):
    """回归：挂件创建晚于 resume_active_wave，必须初始同步 presenter 现状。

    场景：上次未走完的波 → 主窗构造时就进入 decaying，而迷你窗那时还不存在。
    不同步的话：主窗显示"推演中/禁用"，挂件却显示"开始/可用"，
    用户会在挂件上点"开始"去打断一场正在进行的衰减。
    """
    from windows.main_console import MainConsole
    from db import DatabaseManager
    db = DatabaseManager(str(tmp_path / "s.db"))
    c = MainConsole(db=db, archetype_key="medium")
    c._toggle_recording(True)
    for _ in range(5):
        c._sample()
    c._toggle_recording(False)
    assert c._wave_active is True

    import main_app
    win = main_app.MainWindow(db)
    win.mini._sync_from_presenter()          # 显式同步（构造时也会调一次）
    _settle(qapp)

    assert win.mini.btn_record.text().startswith("◌"), "挂件应显示推演中"
    assert win.mini.btn_record.isEnabled() is False
    assert win.mini.v_cell.value_label.text() != "—"
    db.close()


def test_input_mirrors_between_views(qapp, tmp_path):
    """回归：两个视图的输入不能互相覆盖，且变更要镜像到另一侧滑杆。"""
    win, db = _make_window(tmp_path)
    mini = win.mini
    mini.show()
    _settle(qapp)

    # 挂件只改唤醒 → 主窗唤醒滑杆应镜像过来，且主窗效价不受影响
    mini.a_slider['slider'].setValue(90)
    _settle(qapp)
    assert win.console.presenter._input_a == pytest.approx(0.90)
    assert win.console.input_panel.a_row['slider'].value() == 90
    assert win.console.input_panel.v_row['slider'].value() == 50   # 效价没被动过

    # 主窗只改效价 → 挂件唤醒应保持 0.90，不被整体覆盖打回 0.40
    win.console.input_panel.v_row['slider'].setValue(70)
    _settle(qapp)
    assert win.console.presenter._input_v == pytest.approx(0.70)
    assert win.console.presenter._input_a == pytest.approx(0.90), \
        "主窗碰效价不应把挂件调的唤醒打回原值"
    assert mini.v_slider['slider'].value() == 70
    assert mini.a_slider['slider'].value() == 90
    db.close()


def test_stop_with_data_enters_decay(qapp, tmp_path):
    """回归：有数据时停止记录 → 进入"衰减回基线"，不清空、按钮转推演中。"""
    win, db = _make_window(tmp_path)
    c = win.console
    c._toggle_recording(True)
    for _ in range(5):
        c._sample()
    c._toggle_recording(False)
    assert c.recording is False
    assert c._wave_active is True
    assert len(c.states) == 5
    assert c.input_panel.btn_record.text().startswith("◌")   # 推演中
    db.close()


def test_decay_settles_back_to_baseline_and_clears(qapp, tmp_path):
    """回归：衰减外推回基线后曲线消失、回到"开始记录"。

    v4 修订：衰减按墙钟推进，用假时钟让 60 秒过去（旧写法连调 60 次
    `_decay_step()` 隐含"tick 次数 = 秒数"，正是本次要修的错误语义）。
    """
    from tests.helpers import FakeClock
    clock = FakeClock()
    win, db = _make_window(tmp_path, clock=clock)
    c = win.console
    c._toggle_recording(True)
    for _ in range(4):
        clock.advance(1)
        c._sample()
    c._toggle_recording(False)
    assert c._wave_active is True
    clock.advance(60)
    c._decay_step()
    assert c._wave_active is False
    assert c.curve.timestamps == []                          # 曲线消失
    assert c.input_panel.btn_record.text().startswith("●")   # 回到开始记录
    db.close()


def test_active_wave_resumes_on_relaunch(qapp, tmp_path):
    """回归：关掉重开（新 MainConsole 同库）→ 从 active_wave 快照恢复并继续推演。"""
    from windows.main_console import MainConsole
    from db import DatabaseManager
    db = DatabaseManager(str(tmp_path / "r.db"))
    c = MainConsole(db=db, archetype_key="medium")
    c._toggle_recording(True)
    for _ in range(6):
        c._sample()
    c._toggle_recording(False)
    assert c._wave_active is True

    c2 = MainConsole(db=db, archetype_key="medium")         # 模拟重启
    assert c2._wave_active is True
    assert len(c2.observations) == 6
    assert c2.input_panel.btn_record.text().startswith("◌")
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


# ================================================================
# v4 Phase 1：UI 接线（记一笔 / 状态常驻）
# ================================================================

def test_note_button_commits_one_observation(qapp, tmp_path):
    """「记一笔」只在记录中可用，点一次 = 一条自评（不依赖拖动）。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    win, db = _make_window(tmp_path, clock=clock)
    c = win.console
    panel = c.input_panel

    assert panel.btn_note.isEnabled() is False      # 未开始记录 → 禁用
    c._toggle_recording(True)
    assert panel.btn_note.isEnabled() is True

    panel.btn_note.click()
    assert len(c.observations) == 1
    assert panel.count_label.text() == "1 次自评"

    panel.btn_note.click()                          # 再确认一次（值没变也提交）
    assert len(c.observations) == 2
    db.close()


def test_mini_shows_state_after_wave_ends(qapp, tmp_path):
    """波结束后挂件仍显示"当前状态"，而不是回到 '—'。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    win, db = _make_window(tmp_path, clock=clock)
    c = win.console
    c._toggle_recording(True)
    clock.advance(1)
    c._sample()
    c._toggle_recording(False)
    clock.advance(60)
    c._decay_step()
    assert c._wave_active is False

    win.mini._sync_from_presenter()
    assert win.mini.v_cell.value_label.text() not in ("", "—")
    assert win.mini.state_label.text() not in ("", "—")
    db.close()


# ================================================================
# v4 Phase 4：生命周期与低频刷新
# ================================================================

def test_mini_toggle_controls_periodic_refresh(qapp, tmp_path):
    """挂件可见 → 开启 5 分钟刷新；收起 → 停止。"""
    win, db = _make_window(tmp_path)
    p = win.console.presenter
    assert p.refresh_timer.isActive() is False

    win._toggle_mini()
    assert win.mini.isVisible() is True
    assert p.refresh_timer.isActive() is True
    assert p.refresh_timer.interval() == 300000

    win._toggle_mini()
    assert win.mini.isVisible() is False
    assert p.refresh_timer.isActive() is False
    db.close()


def test_periodic_refresh_advances_state_without_observation(qapp, tmp_path):
    """低频刷新把状态推到"现在"，但绝不产生新观察。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    win, db = _make_window(tmp_path, clock=clock)
    c = win.console
    p = c.presenter

    c._toggle_recording(True)
    for _ in range(3):
        clock.advance(1)
        c._sample()
    c._toggle_recording(False)

    clock.advance(700)              # 超过 MAX_DECAY_SEC → 波自然结束
    c._decay_step()
    assert p._wave_active is False
    assert len(p.observations) == 0  # 波结束后内存里的波数据清空

    clock.advance(300)
    p._on_refresh()
    assert len(p.observations) == 0                    # 仍然没有新观察
    assert p.latest_state is not None
    assert p.latest_state.timestamp == pytest.approx(clock(), abs=1.0)
    db.close()


def test_close_main_with_mini_visible_only_hides(qapp, tmp_path):
    """挂件还在时关主窗 → 只隐藏，进程继续跑，数据库仍可用。"""
    win, db = _make_window(tmp_path)
    win.show()
    win.mini.show()

    win.close()

    assert win._shut_down is False
    assert win.isVisible() is False
    assert win.mini.isVisible() is True
    assert db.get_recent_events(limit=1) == []          # 库没关
    db.close()


def test_close_main_without_mini_shuts_down(qapp, tmp_path):
    """没有可见挂件时关主窗 → 真正退出（关库 + quit）。"""
    import sqlite3

    win, db = _make_window(tmp_path)
    win.show()
    win.close()

    assert win._shut_down is True
    with pytest.raises(sqlite3.ProgrammingError):
        db.get_recent_events(limit=1)


def test_mini_close_when_main_hidden_shuts_down(qapp, tmp_path):
    """主窗藏着、挂件被 × 掉 → 没有可见窗口了 → 退出。"""
    win, db = _make_window(tmp_path)
    win.mini.show()
    win.hide()

    win.mini._on_close_clicked()

    assert win._shut_down is True


def test_mini_close_when_main_visible_keeps_running(qapp, tmp_path):
    """主窗还开着时关挂件 → 只收挂件，不退出。"""
    win, db = _make_window(tmp_path)
    win.show()
    win.mini.show()

    win.mini._on_close_clicked()

    assert win._shut_down is False
    assert win.mini.isVisible() is False
    assert db.get_recent_events(limit=1) == []
    db.close()


def test_shutdown_is_idempotent(qapp, tmp_path):
    win, db = _make_window(tmp_path)
    win.shutdown()
    win.shutdown()                   # 第二次不应抛异常
    assert win._shut_down is True


# ================================================================
# v4 Phase 4：生命周期与低频刷新
# ================================================================

def test_mini_note_button_commits_observation(qapp, tmp_path):
    """挂件单独使用时也能记一笔：按钮只在记录中出现。"""
    from tests.helpers import FakeClock
    clock = FakeClock()
    win, db = _make_window(tmp_path, clock=clock)
    c = win.console
    mini = win.mini

    assert mini.btn_note.isHidden() is True
    c._toggle_recording(True)
    assert mini.btn_note.isHidden() is False

    mini.btn_note.click()
    assert len(c.observations) == 1

    c._toggle_recording(False)
    assert mini.btn_note.isHidden() is True
    db.close()




# ================================================================
# v5 Phase 3：首屏说清「这个数是几点的」
# ================================================================

def test_state_head_meta_reports_age_and_baseline(qapp):
    """meta 行同时给出：距上次自评多久、模型估计时刻、与基线差。"""
    from windows.state_head import StateHead

    head = StateHead(on_correction_clicked=lambda: None)
    now = 1_700_000_000.0
    head.update_meta(0.62, state_ts=now, last_obs_ts=now - 720, baseline_v=0.50)

    text = head.meta_label.text()
    assert "距上次自评 12 分钟" in text
    assert "模型估计" in text
    assert "比基线 +12" in text


def test_state_head_meta_says_just_now(qapp):
    """刚自评完不显示「距上次 0 分钟」，也不重复报估计时刻。"""
    from windows.state_head import StateHead

    head = StateHead(on_correction_clicked=lambda: None)
    now = 1_700_000_000.0
    head.update_meta(0.48, state_ts=now, last_obs_ts=now - 5, baseline_v=0.50)

    text = head.meta_label.text()
    assert "刚刚自评" in text
    assert "模型估计" not in text
    assert "与基线持平" in text


def test_state_head_meta_empty_without_state(qapp):
    from windows.state_head import StateHead

    head = StateHead(on_correction_clicked=lambda: None)
    head.update_meta(0.5, state_ts=0.0)
    assert head.meta_label.text() == ""


# ================================================================
# v5 Phase 3：首屏说清「这个数是几点的」
# ================================================================

