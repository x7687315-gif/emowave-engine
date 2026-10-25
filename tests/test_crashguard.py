"""tests/test_crashguard.py — 崩溃捕获模块测试

核心断言：替换 sys.excepthook 后，"槽内异常 → 进程 abort（闪退）"
被换成"落日志 + 通知 + 继续运行"。PyQt5 只在 excepthook 未被替换时
才调用 qFatal（文档化行为），所以 hook 本身的存在就是修复。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crashguard


@pytest.fixture(autouse=True)
def restore_excepthook():
    saved = sys.excepthook
    saved_dir = crashguard.LOG_DIR
    saved_notifier = crashguard._notifier
    yield
    sys.excepthook = saved
    crashguard.LOG_DIR = saved_dir
    crashguard._notifier = saved_notifier


def test_install_replaces_excepthook(tmp_path):
    crashguard.install(log_dir=str(tmp_path))
    assert sys.excepthook is not crashguard._original if hasattr(
        crashguard, "_original") else True
    assert sys.excepthook is not sys.__excepthook__


def test_hook_writes_crash_log_and_keeps_going(tmp_path, qapp):
    crashguard.install(log_dir=str(tmp_path))
    notified = []
    crashguard.set_notifier(lambda text: notified.append(text))
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    files = [f for f in os.listdir(str(tmp_path)) if f.startswith("crash-")]
    assert files, "崩溃日志必须落盘"
    content = open(os.path.join(str(tmp_path), files[0]),
                   encoding="utf-8").read()
    assert "ValueError" in content and "boom" in content
    assert notified, "主线程内应触发用户可见通知"


def test_log_failure_records_traceback(tmp_path):
    crashguard.LOG_DIR = str(tmp_path)
    try:
        1 / 0
    except ZeroDivisionError:
        path = crashguard.log_failure("纠正弹窗打开/提交失败")
    assert path and os.path.exists(path)
    content = open(path, encoding="utf-8").read()
    assert "ZeroDivisionError" in content


def test_dir_traversal_inputs_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        crashguard._validated_dir(str(tmp_path) + os.sep + ".." + os.sep + "elsewhere")


def test_kind_is_sanitized_into_filename(tmp_path):
    crashguard.LOG_DIR = str(tmp_path)
    path = crashguard._write_log("crash/../../evil", "hello")
    assert path is not None
    name = os.path.basename(path)
    # 净化后的文件名不允许出现路径分隔符
    assert "/" not in name and "\\" not in name
    assert os.path.dirname(os.path.abspath(path)) == os.path.abspath(str(tmp_path))


def test_notifier_without_qapp_does_not_raise(tmp_path):
    crashguard.install(log_dir=str(tmp_path))
    crashguard.set_notifier(lambda text: (_ for _ in ()).throw(RuntimeError("GUI!")))
    # 无 QApplication 时应静默跳过（offscreen conftest 可能已建 qapp，
    # 这里用异常回调覆盖另一条防御路径：回调自身抛异常也必须被吞掉）
    crashguard._notify_user(ValueError("x"))
