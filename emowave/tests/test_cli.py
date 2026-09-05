"""CLI 单元测试。

验证 Core 可完全脱离 UI 运行（Phase 1 完成标准的端到端体现），
以及各子命令的正确性。用 capsys 捕获输出，不实际打印到控制台。
"""

import pytest

from emowave.cli import build_parser, main, _ensure_utf8_stdout


def test_ensure_utf8_stdout_is_safe_and_idempotent():
    """Windows 控制台 GBK 编码修复的守卫：helper 可调用、幂等、不抛异常。

    修复前 CLI 打印 ℓ(U+2113)/·/→ 在 GBK 控制台抛 UnicodeEncodeError 崩溃。
    """
    _ensure_utf8_stdout()
    _ensure_utf8_stdout()  # 幂等，重复调用不报错


def test_parser_has_subcommands():
    parser = build_parser()
    # 各子命令应可解析
    for cmd in ["demo", "detect", "curve", "version"]:
        args = parser.parse_args([cmd])
        assert args.command == cmd


def test_version_command(capsys):
    rc = main(["version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "schema_version" in out
    assert "protocol_version" in out
    assert "model_version" in out
    assert "detected tier" in out


def test_demo_command(capsys):
    rc = main(["demo", "--points", "30"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "valence" in out
    assert "最终状态" in out


def test_demo_command_default_points(capsys):
    rc = main(["demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "EmoWave demo" in out


def test_detect_command_with_va(capsys):
    """显式 (v,a) → 直接映射。"""
    rc = main(["detect", "--valence", "0.15", "--arousal", "0.9"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "worried" in out  # v<0.4, a>0.65 → worried


def test_detect_command_with_text(capsys):
    rc = main(["detect", "--text", "我好焦虑啊"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Amiya 状态" in out


def test_detect_command_calm(capsys):
    rc = main(["detect", "--valence", "0.6", "--arousal", "0.3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "calm" in out  # a≤0.5 → calm


def test_curve_command(capsys):
    rc = main(["curve", "--points", "40", "--nodes", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "RTS 平滑" in out
    assert "节点" in out


def test_no_command_prints_help(capsys):
    """无子命令 → 打印帮助，返回 0。"""
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower() or "EmoWave" in out


def test_cli_zero_heavy_deps():
    """CLI 模块不应 import numpy/PyQt5/flet（T0 最小接口）。"""
    import inspect
    import emowave.cli as cli_mod
    src = inspect.getsource(cli_mod)
    assert "import numpy" not in src
    assert "import PyQt5" not in src
    assert "import flet" not in src
