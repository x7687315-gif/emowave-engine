"""crashguard.py — 崩溃捕获与失败日志

=== 为什么需要这个模块 ===
PyQt5 ≥5.5 的默认行为：Python 槽（按钮回调 / 定时器 / paintEvent）里冒出的
未捕获异常，在打印 traceback 之后调用 qFatal **终止整个进程**——用户看到
的就是"闪退"，而且没有任何现场可查（本应用明确不做云端上报）。

PyQt5 的文档化行为同时给了出路：**只要替换了 `sys.excepthook`，PyQt5 就把
异常交给新 hook 处理，不再 abort**。本模块做的事：

  1. 替换 `sys.excepthook`：traceback 落盘 `~/.emowave/logs/crash-*.log`，
     应用**继续运行**；
  2. 开启 `faulthandler`：原生段错误（没有 Python traceback 的那类闪退）
     也在同一目录留下现场（`native-crash.log`）；
  3. `log_failure()`：调用方**捕获了**的严重失败（如纠正弹窗异常）同样留档，
     用户提示里的"详情已写入日志"因此是实话；
  4. `set_notifier()`：主窗口注册一个回调，崩溃时给用户一条可见提示
     （只允许主线程调用 GUI，见 `_notify_user` 的线程守卫）。

只依赖标准库；PyQt5 的 import 放在函数内，保证无 GUI 环境也能 import。
"""
import faulthandler
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

#: 日志目录；install() 可覆盖（测试注入 tmp 目录用）
LOG_DIR = None

#: faulthandler 需要文件对象保持打开，这里持有引用防止被 GC 关闭
_fh_files = []
_notifier = None

#: 日志文件名只允许这些字符（kind 来自调用方，落盘前必须净化）
_SAFE_KIND = re.compile(r"[^A-Za-z0-9_-]")


def _validated_dir(candidate: str) -> str:
    """规范化并校验目录：路径分段中出现上级目录段一律拒绝。"""
    parts = str(candidate).replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError("日志目录不允许包含上级目录段：%r" % candidate)
    return os.path.abspath(candidate)


def default_log_dir() -> str:
    home = Path.home()
    return str(home / ".emowave" / "logs")


def _write_log(kind: str, text: str):
    """写一份日志文件，返回路径；任何失败都吞掉（日志不能压垮主流程）。"""
    try:
        base = Path(_validated_dir(LOG_DIR or default_log_dir()))
        safe_kind = _SAFE_KIND.sub("-", str(kind))[:40] or "log"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = "%s-%s.log" % (safe_kind, stamp)
        # 净化后的文件名只含 [A-Za-z0-9_-] 与点，不可能携带路径分段
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            return None
        target = base / name
        if target.resolve().parent != base.resolve():
            return None
        base.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return str(target)
    except Exception:
        return None


def install(log_dir: str = None) -> str:
    """安装崩溃捕获。必须在 QApplication 构造前调用一次。

    Returns:
        日志目录（测试断言用）
    """
    global LOG_DIR
    if log_dir is not None:
        LOG_DIR = _validated_dir(log_dir)

    def hook(exc_type, exc, tb):
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        _write_log("crash", detail)
        sys.__excepthook__(exc_type, exc, tb)      # 控制台照常打印（调试启动器可见）
        _notify_user(exc)

    sys.excepthook = hook

    try:
        base = Path(_validated_dir(LOG_DIR or default_log_dir()))
        base.mkdir(parents=True, exist_ok=True)
        f = open(base / "native-crash.log", "a", encoding="utf-8")
        f.write("\n==== session %s pid=%d ====\n"
                % (datetime.now().isoformat(), os.getpid()))
        faulthandler.enable(f, all_threads=True)
        _fh_files.append(f)
    except Exception:
        pass                                    # 原生捕获装不上不影响 Python 层
    return LOG_DIR if LOG_DIR is not None else default_log_dir()


def log_failure(context: str):
    """记录一个已捕获的严重失败（带当前 traceback），返回日志路径或 None。"""
    detail = "%s\n%s" % (context, "".join(traceback.format_exc()))
    return _write_log("failure", detail)


def set_notifier(fn):
    """注册用户可见的崩溃提示回调（主窗口把通知条接进来）。"""
    global _notifier
    _notifier = fn


def _notify_user(exc):
    fn = _notifier
    if fn is None:
        return
    try:
        from PyQt5.QtCore import QThread, QCoreApplication
        app = QCoreApplication.instance()
        # GUI 只能在主线程碰；工作线程的异常只落日志
        if app is not None and QThread.currentThread() is app.thread():
            fn("刚刚有个功能出了问题（%s），已继续运行；"
               "详情见 ~/.emowave/logs/。" % exc)
    except Exception:
        pass
