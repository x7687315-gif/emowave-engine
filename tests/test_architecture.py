"""tests/test_architecture.py — 架构护栏

瘦身只能做一次，**防止尾巴再长**要靠测试。这里把三条纪律固化成可执行的断言：

1. 零依赖内核：`emowave/core/**` 不许 import 任何第三方包（含 PyQt5 / numpy）。
2. 无孤儿模块：仓库里每个非测试模块都必须从「出货入口」可达；只有测试可达的
   模块必须显式登记在 `PUBLIC_API_ONLY`（例如内核对外发布的存储适配器）。
3. 入口可 import：`main_app` / `presenters` / `windows` 在无显示环境下可加载。

三者都是纯标准库实现，不给项目增加任何运行/开发依赖——这也是没有引入
import-linter 的原因：它的能力用 60 行 AST 就能覆盖本项目需要的部分。
"""
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 出货入口：桌面应用 + pip 包 CLI（`python -m emowave.cli`）
ENTRYPOINTS = ("main_app", "emowave.cli", "emowave.cli.__main__")

# 只有测试可达、但属于对外发布 API 的模块（pip 包消费者会用）
PUBLIC_API_ONLY = {
    "emowave.adapters.storage",
    "emowave.adapters.storage.sqlite",
}

# 不参与「必须可达」判定的目录
SKIP_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".workbuddy-ai",
    ".idea",
    "docs",
    "archive",
    "test_data",
}

TEST_MODULE_PREFIXES = ("tests.", "emowave.tests.")


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _iter_project_modules():
    for path in ROOT.rglob("*.py"):
        rel_parts = path.relative_to(ROOT).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        name = _module_name(path)
        if name.startswith(TEST_MODULE_PREFIXES) or name in ("tests", "emowave.tests"):
            continue
        if path.name == "conftest.py":
            continue
        yield name, path


def _imported_names(path: Path, module_name: str):
    """返回该文件 import 到的候选模块名（含相对 import 展开后的绝对名）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:                                  # 语法错误的文件交给别的测试去报
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                # `from .x import y`：先回到当前包，再按 level 上跳
                package = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
                for _ in range(node.level - 1):
                    package = package.rpartition(".")[0]
                base = "%s.%s" % (package, base) if base else package
            if not base:
                continue
            names.add(base)
            for alias in node.names:
                names.add("%s.%s" % (base, alias.name))
    return names


def _resolve(name: str, modules) -> str:
    """把 import 名收敛到项目内实际存在的模块（逐级截断）。"""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in modules:
            return candidate
    return ""


def _with_ancestors(name: str, modules) -> set:
    """导入 `a.b.c` 时，`a` 与 `a.b` 也一并被加载，必须算作可达。"""
    out = set()
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        candidate = ".".join(parts[:i])
        if candidate in modules:
            out.add(candidate)
    return out


def _reachable_from(entrypoints, modules):
    seen = set()
    stack = [e for e in entrypoints if e in modules]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for name in _imported_names(modules[current], current):
            target = _resolve(name, modules)
            if not target:
                continue
            for module in _with_ancestors(target, modules):
                if module not in seen:
                    stack.append(module)
    return seen


def _is_stdlib(module_name: str) -> bool:
    if module_name in sys.builtin_module_names:
        return True
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return False
    if spec is None:
        return False
    origin = spec.origin or ""
    return "site-packages" not in origin and "dist-packages" not in origin


def test_no_orphan_modules():
    """每个非测试模块都必须从出货入口可达，否则就是"尾巴"。"""
    modules = dict(_iter_project_modules())
    reachable = _reachable_from(ENTRYPOINTS, modules)
    orphans = sorted(set(modules) - reachable - PUBLIC_API_ONLY)

    assert not orphans, (
        "以下模块从 main_app / emowave.cli 不可达，属于孤儿代码：\n  "
        + "\n  ".join(orphans)
        + "\n如果它是对外发布的 API，请登记到 PUBLIC_API_ONLY；"
        "否则删除它（参考 docs/slim-refactor/）。"
    )


def test_public_api_allowlist_is_still_real():
    """白名单不能变成"僵尸名单"：登记的模块必须真实存在。"""
    modules = set(dict(_iter_project_modules()))
    missing = sorted(PUBLIC_API_ONLY - modules)
    assert not missing, "PUBLIC_API_ONLY 里登记了已不存在的模块：%s" % missing


def test_core_has_no_third_party_imports():
    """`emowave/core/**` 必须保持零第三方依赖（可 pip 安装、可移植）。"""
    offenders = []
    for name, path in _iter_project_modules():
        if not name.startswith("emowave.core"):
            continue
        for imported in _imported_names(path, name):
            top = imported.split(".")[0]
            if top == "emowave":
                continue
            if not _is_stdlib(top):
                offenders.append("%s -> %s" % (name, imported))

    assert not offenders, "内核引入了第三方依赖：\n  " + "\n  ".join(sorted(offenders))


def test_entrypoints_import_without_display():
    """无显示环境下，出货入口必须能 import（解耦边界的运行时证据）。"""
    pytest.importorskip("PyQt5")

    import main_app                                        # noqa: F401
    import presenters.console_presenter                    # noqa: F401
    import windows.legacy_embed                            # noqa: F401
    import windows.mini_window                             # noqa: F401
    from emowave import __version__                        # noqa: F401


# 代码体积预算（棘轮）：21.9k 行是 2026-10 瘦身后的水位。
# 要加功能就删等价代码；确实需要上调时，连同本注释一起改，并在 commit 里写清理由。
#
# 上调记录：
#   2026-10-19  23,000 → 24,000  v4 Phase1 时间/观察语义（含 16 条测试）
#                               + Phase2 统一曲线查询（含 5 条测试）
#                               当前 22,973，余量约 1,000（≈4%）
#   2026-10-20  24,000 → 25,000  v5 Phase2 净减 56（23,760）
#                               + Phase3 首屏 meta 行 / 修正标记 / correction_context
#                               + 7 条测试，净 +221 → 23,981
#                               上调理由：这一波是**真实功能增量**（UI 能回答
#                               "这数是几点的"），不是维护性膨胀。余量留给
#                               Phase4 悬浮窗与 Phase5 视觉；若 Phase5 还要涨，
#                               必须回头删等价代码，不许再默默加预算。
LOC_BUDGET = 25000


def test_python_line_budget():
    """总量预算：防止"每次只加一点点"把尾巴慢慢养回来。"""
    total = 0
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        total += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())

    assert total <= LOC_BUDGET, (
        "Python 代码 %d 行，超出预算 %d 行（+%d）。\n"
        "要么删掉等价代码，要么在 tests/test_architecture.py 上调 LOC_BUDGET 并说明理由。"
        % (total, LOC_BUDGET, total - LOC_BUDGET)
    )


# ================================================================
# 第 4 条纪律：无重复定义（v5 Phase 2）
# ================================================================

#: 允许重名的装饰器——`@property` 与 `@x.setter` 共享函数名是 Python 正常写法
_PROPERTY_DECORATORS = {"property", "setter", "getter", "deleter"}


def _decorator_names(fn):
    names = set()
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Name):
            names.add(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.add(dec.attr)
    return names


def test_no_duplicate_definitions():
    """同一作用域里同名函数只能定义一次。

    为什么值得一道护栏：Python 后者静默覆盖前者，**不报错**。
    2026-10 的 v4 就因此攒下 `showEvent` / `_tail_points` /
    `restore_recent_context` 各两份（同体副本，白占 230 行），
    以及 19 个重复测试函数（前一份永不执行）。这种债肉眼看不出来，
    只能靠 AST 数。

    property / setter 共享名字是正常写法，跳过。
    """
    problems = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        src = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:                 # 语法错由 compileall 负责
            problems.append("%s: 无法解析（%s）" % (path.relative_to(ROOT), exc))
            continue

        scopes = [("<module>", tree.body)]
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                scopes.append((node.name, node.body))

        for scope_name, body in scopes:
            seen = {}
            for sub in body:
                if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                seen.setdefault(sub.name, []).append(sub)
            for name, defs in seen.items():
                if len(defs) < 2:
                    continue
                all_props = all(
                    _decorator_names(d) & _PROPERTY_DECORATORS for d in defs
                )
                if all_props:
                    continue
                problems.append(
                    "%s :: %s.%s 定义了 %d 次（行 %s）"
                    % (path.relative_to(ROOT), scope_name, name, len(defs),
                       [d.lineno for d in defs])
                )

    assert not problems, "存在重复定义（后一份会静默覆盖前一份）：\n  " + "\n  ".join(problems)
