# EmoWave 2.0 重构进度追踪

> **本文件是重构执行的实时日志。** 每完成一个阶段就在此追加一条记录，包含：做了什么、怎么做的、验证结果、下一步计划。
>
> **权威依据**：
> - `EMOWAVE_REFACTOR_PLAN.md`（用户提供的顶层计划，34 章）
> - `ARCHITECTURE_EMOTIONpart1.md`（曲线建模与人机协同编辑架构）
> - `ARCHITECTURE_LIGHTWEIGHTpart2.md`（轻量化内核 / Amiya 集成 / 多平台）
> - `IMPLEMENTATION_PLAN.md`（旧版桌面应用 TDD 实现计划，已完成，作为基线参考）
>
> **重构分支**：`refactor/v2-core`
> **稳定基线 tag**：`v1.0-stable`（旧版最后一版可回滚锚点）

---

## 执行原则

1. **TDD 铁律**：每个新功能先写测试，再看失败，再最小实现，再看通过。
2. **原始数据不可变**：Observation append-only，永不覆盖；用户修正独立记录。
3. **Core 硬边界**：Core 不知道 PyQt / Flet / SQLite / Amiya / LLM 的存在，只认识自己的领域模型和算法。
4. **零依赖内核优先**：新增 `emowave/core/**` 只用 Python 标准库（`math`、`dataclasses`、`typing`），不 import numpy / scipy / PyQt5。
5. **每步一提交**：完成一个阶段就 commit + push，保证任意时刻可回滚。
6. **可回滚锚点**：Phase 0 打 tag `v1.0-stable`，之后任何一步失败都能 `git reset --hard v1.0-stable`。

---

## 时间线

### Phase 0 — 冻结当前版本（进行中）

**目标**：保证旧项目可回滚，建立新分支，跑通基线测试，锁定 tag。

**动作**：

1. 建立新分支 `refactor/v2-core`
2. 在当前 `main` HEAD 打 tag `v1.0-stable`（旧版可回滚锚点）
3. 把三份未追踪的架构文档纳入版本管理：
   - `ARCHITECTURE_EMOTIONpart1.md`
   - `ARCHITECTURE_LIGHTWEIGHTpart2.md`
   - `REFERENCES.md`
4. 创建本追踪文档 `REFACTOR_PROGRESS.md`
5. 跑全量测试，保存 benchmark baseline

**测试基线**（Python 3.9.13，pytest 8.x，2026-09-05 实测）：

| 测试集 | 通过 | 失败 | 错误 | 耗时 | 备注 |
|---|---:|---:|---:|---:|---|
| `tests/`（桌面应用主套件） | 23 | 0 | 0 | 1.21s | ✅ 全部通过 |
| `test_engine.py` | 5 | 0 | 0 | — | ✅ 全部通过 |
| `test_recommender.py` | 2 | 0 | 1 | — | ⚠️ `test_serialization` 缺 `bandit` fixture（旧问题，非本次引入） |
| **合计** | **30** | **0** | **1** | **~2s** | 基线锁定 |

**已完成清单**：

- [x] 环境准备：Python 3.9.13 + pytest（通过清华源安装）
- [x] 现有测试全部跑通并记录基线
- [x] 建立 `refactor/v2-core` 分支
- [x] 打 tag `v1.0-stable`
- [x] 三份架构文档纳入 git
- [x] 本追踪文档创建

**下一步（Phase 1）**：Core Domain 重构。新建 `emowave/core/domain/` 目录，实现 6 个新领域模型 + protocol schemas，配套单元测试。

---
