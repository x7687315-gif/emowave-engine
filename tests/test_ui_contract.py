"""tests/test_ui_contract.py — UI 视觉与交互契约（v5 Phase 5）

这些断言不测"画得好不好看"（那要人看），只测**语义有没有被守住**：

1. 曲线四种元素的画法来自同一处（`theme.CURVE_STYLE`），预测必须是虚线；
2. 空态必须给一句可执行的下一步，而不是四个破折号；
3. 高频动作有键盘入口；
4. 主窗与挂件用同一套字体/颜色令牌，不各写一份。
"""


# ================================================================
# 1. 曲线语义
# ================================================================

def test_curve_style_defines_all_four_semantics():
    """四种元素缺一不可——少一种，用户就分不清那是谁说的。"""
    from theme import CURVE_STYLE

    for key in ("observation", "model", "forecast", "correction"):
        assert key in CURVE_STYLE, "缺少曲线语义：%s" % key


def test_forecast_is_dashed_and_model_is_solid():
    """预测必须是虚线。这条一旦破了，"猜的"会被读成"算的"。"""
    from theme import CURVE_STYLE

    assert CURVE_STYLE["forecast"]["dash"] is True
    assert CURVE_STYLE["model"]["dash"] is False


def test_correction_is_visually_distinct_from_observation():
    """修正点与真实观察点不能用同一个颜色——一个是我改的，一个是我说的。"""
    from theme import CURVE_STYLE

    obs = CURVE_STYLE["observation"]["color"]
    corr = CURVE_STYLE["correction"]["color"]
    assert obs != corr


def test_curve_colors_reference_theme_tokens():
    """字符串色值必须能在 COLORS 里找到，防止写死十六进制绕过主题。"""
    from theme import COLORS, CURVE_STYLE

    for name, spec in CURVE_STYLE.items():
        color = spec.get("color")
        if isinstance(color, str):
            assert color in COLORS, "%s 的颜色 %r 不在 COLORS 里" % (name, color)
        else:
            assert len(color) == 4, "%s 的 rgba 必须是 4 元组" % name


def test_curve_widget_uses_theme_observation_color():
    """控件真的在用主题色，而不是自己留了一份字面量。"""
    import curve_widget
    from theme import CURVE_STYLE

    assert curve_widget._RAW_RGBA == CURVE_STYLE["observation"]["color"]


# ================================================================
# 2. 空态
# ================================================================

def test_empty_state_tells_user_what_to_do(qapp):
    from windows.state_head import EMPTY_HINT, StateHead

    head = StateHead(on_correction_clicked=lambda: None)
    head.reset_values()

    text = head.meta_label.text()
    assert text == EMPTY_HINT
    assert ("开始记录" in text) or ("滑条" in text)


# ================================================================
# 3. 键盘入口
# ================================================================

def test_main_console_exposes_keyboard_shortcuts(qapp, tmp_path):
    """两个高频动作必须能用键盘完成（不用鼠标也能记一笔）。"""
    from PyQt5.QtWidgets import QShortcut

    from db import DatabaseManager
    import main_app

    db = DatabaseManager(str(tmp_path / "k.db"))
    win = main_app.MainWindow(db)

    keys = {s.key().toString() for s in win.console.findChildren(QShortcut)}
    assert "Ctrl+R" in keys        # 开始 / 停止记录
    assert "Ctrl+N" in keys        # 记一笔
    win.shutdown()


# ================================================================
# 4. 令牌同源
# ================================================================

def test_both_windows_use_theme_fonts():
    """主窗与挂件的字体都从 theme 来，不各写一套字号表。"""
    import windows.main_console as mc
    import windows.mini_window as mw
    import theme

    assert mc.app_font is theme.app_font
    assert mw.app_font is theme.app_font
    assert mc.COLORS is theme.COLORS
    assert mw.COLORS is theme.COLORS


def test_mini_reuses_state_head_time_formatter():
    """"多久以前"只有一套实现，挂件不复制。"""
    import windows.mini_window as mw
    import windows.state_head as sh

    assert mw._fmt_age is sh._fmt_age


# ================================================================
# 5. 柔和圆角与视图边界（v5 Phase 5）
# ================================================================

def test_theme_has_soft_radius_tokens():
    """圆角只走令牌：小控件 < 卡片 ≤ 悬浮窗。"""
    from theme import METRICS

    assert 0 < METRICS['radius_sm'] < METRICS['radius'] <= METRICS['radius_mini']


def test_global_qss_uses_radius_tokens():
    """全局 QSS 的圆角必须来自令牌；留一个 Tab 底部硬边条作为导航语义。"""
    from theme import METRICS, build_app_qss

    qss = build_app_qss()
    assert f"border-radius: {METRICS['radius_sm']}px" in qss
    assert f"border-radius: {METRICS['radius']}px" in qss
    assert qss.count("border-radius: 0px") == 1


def test_views_do_not_touch_presenter_internals():
    """生产视图不得再摸 presenter 私有成员或 latest_state（Phase 5 停止条件）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "windows"
    bad = []
    for path in root.glob("*.py"):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if ("presenter._" in line or "latest_state" in line
                    or "_readout_payload" in line):
                bad.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not bad, "视图仍在访问 presenter 内部：\n" + "\n".join(bad)
