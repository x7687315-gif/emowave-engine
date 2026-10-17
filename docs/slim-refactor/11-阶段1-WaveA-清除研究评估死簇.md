# 阶段 1 · Wave A：清除 1.x 研究/评估死簇

> 日期：2026-10-17 · 基于 `9502f8c`（阶段 0）· 回滚锚点 `archive-v1-legacy-2026-10`

---

## 1. 做了什么

删除**对出货路径零引用**的 1.x 研究/评估脚手架，共 **22 个文件 / 9,883 行**：

| 文件 | 行数 | 为什么删 |
|---|---:|---|
| `data_simulator_v2.py` | 1455 | 合成数据生成器，只有同簇脚本用 |
| `performance_profiler.py` | 1146 | 性能剖析，产物在 `profiling_output/` |
| `benchmark_suite.py` | 1128 | 基准套件 |
| `evaluation.py` | 1005 | 评估脚本 |
| `onboarding.py` | 849 | 1.x 引导流程，2.0 已由 `archetype_dialog` 取代 |
| `whitepaper_generator.py` | 845 | 白皮书生成器（白皮书本身是静态 MD，不需要生成器） |
| `run_simulation.py` | 839 | 仿真驱动 |
| `crisis_protocol.py` | 641 | 1.x 危机协议（2.0 未接线） |
| `dashboard/`（py+html+css+js+json） | 2301 | 1.x Web 仪表盘，桌面端已独立 |
| `windows/console_window.py` | 550 | 被 `windows/main_console.py` 取代 |
| `demo.py` / `simulator.py` / `dashboard_data.py` | 786 | 1.x 演示与仪表盘数据层 |
| `research/bench_*.py` | 392 | 一次性基准 |
| `docs/shot.py` / `docs/shot_app.py` | 178 | 一次性截图脚本 |

## 2. 怎么做的（双证据 + 门禁）

1. **结构证据**：AST 可达图，入口只取 `main_app` 与 `emowave.cli`，确认上述模块**均不在可达集**。
2. **文本证据**：对每个模块名做全仓 grep（`*.py/*.yml/*.bat/*.md`），确认所有引用都落在**死簇内部**（互相引用）或**历史文档**里，出货代码 0 命中。
3. **唯一例外复核**：`main_console.py` 里出现 `console_window`，逐行确认只是 docstring 中的"与原 console_window.py 的差异"说明，不是 import。
4. **删除方式**：`git rm`（保留历史，可用 tag 取回），不手工 `rm`。
5. **绿测门禁**：删除前后均 `687 passed`。

## 3. 验证

| 项 | 结果 |
|---|---|
| 全量测试 | **687 passed, 6 warnings**（与删除前完全一致） |
| 无 GUI import 冒烟 | `import main_app, presenters.console_presenter, windows.mini_window` → OK |
| Python 行数 | 41,086 → **31,203**（−9,883 / −24%） |
| 受版本控制文件 | 170 → 149 |

> 说明：本轮删除**不影响** `tests/` 与 `emowave/tests/` 的收集——被删模块从未被任何活测试 import（`edge_case_tests.py` 因文件名不匹配 `test_*` 规则，pytest 从未收集它）。

## 4. 事故记录（重要）

执行 `git rm -r` 期间，**`docs/` 与 `windows/` 目录曾被沙箱的批量删除保护误清空**（工作树文件消失，索引未变）。已用 `git checkout -- docs windows` 完整恢复，并复核 `git status` 只剩预期的 22 个删除项。

**后续纪律**：不再对目录用 `git rm -r`，改为显式列出文件；每条破坏性命令后立即 `git status` 复核。此事不影响提交内容（索引自始至终正确），但记录在此以免误判。

## 5. 下一步

Wave B：1.x 引擎栈（`engine` / `kalman_filter` / `predictor` / `threshold` / `baseline` / `recommender` / `annotator` / `config` / `session`）+ 僵尸模块（`privacy_export` / `report_generator` / `edge_case_tests`）+ 死窗口 + 3 个根脚本式测试；`requirements.txt` 去掉 `numpy`。
