# 阶段 2 · Wave B：1.x 引擎栈退役 + 僵尸模块清除

> 日期：2026-10-17 · 基于 `6f78b50`（Wave A）· 回滚锚点 `archive-v1-legacy-2026-10`

---

## 1. 做了什么

| 类别 | 文件 | 行数 | 判据 |
|---|---|---:|---|
| 1.x 引擎栈（被 `emowave/core` 取代） | `engine` / `kalman_filter` / `predictor` / `threshold` / `baseline` / `recommender` / `annotator` / `config` / `models` | 2,854 | 出货 UI 与 CLI 零引用 |
| 从未接线的"产品级死码" | `privacy_export` / `report_generator` / `edge_case_tests` | 3,769 | 只被自己的测试续命；README/文档 0 提及 |
| 死窗口 | `windows/dashboard_window` / `windows/surfing_window` | 389 | 只被自己的测试 import |
| 脚本式测试（非 pytest 断言，只 `print`） | `test_engine` / `test_recommender` / `test_report` + 根 `conftest.py` | 1,802 | 随被测模块一起退役 |
| 随之失效的测试 | `tests/test_session` / `test_dashboard_window` / `test_surfing_window` / `test_privacy_export_crypto` | 489 | 被测对象已退役 |
| **合计** | **22 个文件** | **9,509** | — |

## 2. 三处"不是删、是改"的地方（真正的代码优化）

删除会连带打断 3 个**活测试**（它们用 1.x 引擎造数据），所以顺手做了收敛：

### 2.1 `session.py`：159 行 → 12 行

1.x 的 `SessionController` 把 `EmoCalibrationEngine + ContextualBandit + DatabaseManager`
三件套绑在一起。但 2.0 的引擎在 `emowave/core`，由 `presenters/` 驱动；视图层
（`windows/**`）**只用到 `session.db` 一个属性**（全仓 grep 证实：5 处 `.db`，
`process_event` / `get_dashboard_data` 的调用点都在已删的死窗口里）。

```python
class SessionController:
    __slots__ = ("db",)
    def __init__(self, db):
        self.db = db
```

### 2.2 `windows/legacy_embed.py`：删掉重复的 `_DbSession`

上一轮为了给旧窗口喂 db，在 `legacy_embed` 里手写了一个 `_DbSession`——和
`session.py` 里的适配器是同一个东西的两份拷贝。现在统一用 `SessionController`。

### 2.3 视图测试：不再"用引擎造数据"

`test_history_window` / `test_event_summary_window` 原来靠
`session.process_event(samples=[TimeSeriesSample(...)])` 写入事件——等于让
**视图测试依赖整套 1.x 引擎**（kalman、threshold、recommender 全被拉起来）。
现在改为 `tests/helpers.seed_event(db)`：**直接写一行库记录**。

- 语义更准：测的是"窗口如何渲染一行事件"，不是"引擎如何算出一个事件"。
- 更快更稳：不跑卡尔曼滤波，不依赖 numpy。
- 依赖方向正确：视图测试只依赖持久化层。

## 3. 依赖瘦身

`requirements.txt`：

```diff
  PyQt5==5.15.11
- numpy>=1.21
  pytest>=7.0
  pytest-qt>=4.2.0
- cryptography>=42.0.0
```

`numpy` 的唯一用途是 1.x 引擎与仿真脚本；`cryptography` 只服务 `privacy_export`。
内核 `emowave/core/**` 保持**零第三方运行时依赖**（`emowave/tests/test_cli.py`
有一条断言在守这件事）。

## 4. 验证

| 项 | 结果 |
|---|---|
| 全量测试 | **658 passed, 0 failed** |
| 差额核对 | 687 − 658 = **29**，与被删测试文件的 `def test_` 数**逐个吻合**（7+1+6+4+4+1+6） |
| 无 GUI import 冒烟 | `main_app` / `presenters.console_presenter` / `windows.mini_window` / `windows.legacy_embed` → OK |
| Python 行数 | 31,203 → **21,694**（累计 −47%） |
| 受版本控制文件 | 149 → 128 |

## 5. 事故记录（同类）

`git rm` 批量删除期间，`tests/` 与 `windows/` 目录**再次**被沙箱批量删除保护清空
（与 Wave A 同现象：工作树文件消失、索引未变）。已 `git checkout --` 恢复并重做
5 处编辑。**教训**：本环境下每条破坏性命令后必须立刻 `git status` 复核，
编辑后立刻提交，不做长时间未提交的修改。

## 6. 下一步

Wave C：把"什么算孤儿模块""内核不许依赖第三方""入口必须可 import"写成
**测试**，让尾巴无法再悄悄长回来。
