# docs · 文档索引

> 根目录只保留 `README.md`；其余文档按用途分层放在这里。

## 先读这个

| 文档 | 内容 |
|---|---|
| [`交接文档.md`](交接文档.md) | **接手必读**：现在能做什么、改了什么、每一步怎么做的、遗留问题、未来规划、实地测试指南 |

## 架构（当前有效）

| 文档 | 内容 |
|---|---|
| [`architecture/emotion-model.md`](architecture/emotion-model.md) | 情绪曲线建模、RTS 平滑、人机协同编辑（原 `ARCHITECTURE_EMOTIONpart1.md`） |
| [`architecture/lightweight-core.md`](architecture/lightweight-core.md) | 轻量化内核、Amiya 集成、多平台与 Tier 降级（原 `ARCHITECTURE_LIGHTWEIGHTpart2.md`） |
| [`architecture/mobile.md`](architecture/mobile.md) | 移动端预研（v1.0；核心选型已被 lightweight-core §4.3 推翻） |
| [`whitepaper.md`](whitepaper.md) | 技术白皮书 |
| [`references.md`](references.md) | 理论与工程参照索引 |

## 本轮瘦身（2026-10）

| 文档 | 内容 |
|---|---|
| [`slim-refactor/10-阶段0-取证与调研.md`](slim-refactor/10-阶段0-取证与调研.md) | 「尾巴」从哪来：AST 可达图 + 检索台账 |
| [`slim-refactor/11-阶段1-WaveA-清除研究评估死簇.md`](slim-refactor/11-阶段1-WaveA-清除研究评估死簇.md) | 研究/评估脚手架退场（−9,883 行） |
| [`slim-refactor/12-阶段2-WaveB-引擎栈退役.md`](slim-refactor/12-阶段2-WaveB-引擎栈退役.md) | 1.x 引擎栈退役 + 三处代码收敛（−9,509 行） |
| [`slim-refactor/13-阶段3-架构护栏.md`](slim-refactor/13-阶段3-架构护栏.md) | 让尾巴无法再长回来（4 条护栏） |
| [`slim-refactor/14-阶段4-文档收敛与多轮验证.md`](slim-refactor/14-阶段4-文档收敛与多轮验证.md) | 文档收敛 + 多轮验证报告 |
| [`slim-refactor/15-阶段5-符号清理与磁盘收尾.md`](slim-refactor/15-阶段5-符号清理与磁盘收尾.md) | 未使用 import / 死方法 / 磁盘 22MB→7.9MB / 体积预算护栏 |

## v5 数据契约 + UI 重构（Phase 0–5 完成）

| 文档 | 内容 |
|---|---|
| [`refactor-v5/ROADMAP.md`](refactor-v5/ROADMAP.md) | **阶段交接文档**：目标、基线、决策、数据契约、停止条件、验收标准 |
| [`refactor-v5/00-阶段0-后端正确性审计.md`](refactor-v5/00-阶段0-后端正确性审计.md) | Phase 0：只读审计——恢复重复推进、状态与曲线不同步、重复定义（含假阳性排除） |
| [`refactor-v5/01-阶段1-UI与接口审计.md`](refactor-v5/01-阶段1-UI与接口审计.md) | Phase 1：UI 现状、14 项接口对照（A4/B4/C1/D5）、三层边界越界清单、纠正未持久化的纠错 |
| [`refactor-v5/02-阶段2-后端修复.md`](refactor-v5/02-阶段2-后端修复.md) | Phase 2：单轨时间（恢复不平移、读数跟随曲线）、纠正持久化、去重 24 处、无重复定义护栏 |
| [`refactor-v5/03-阶段3-主窗口重构.md`](refactor-v5/03-阶段3-主窗口重构.md) | Phase 3：首屏 meta 行（距上次自评/估计时刻/基线差）、修正标记、correction_context、时间范围为何不做 |
| [`refactor-v5/04-阶段4-悬浮窗重构.md`](refactor-v5/04-阶段4-悬浮窗重构.md) | Phase 4：收起/展开/钉住、距上次自评、打开主窗、双窗一致性测试 |
| [`refactor-v5/05-阶段5-视觉系统与交互验收.md`](refactor-v5/05-阶段5-视觉系统与交互验收.md) | Phase 5：字体层级修复、柔和圆角、空态/错误态、360×600、悬停、接口收口、15 轮验证 |

## v4 时间语义重构（已完成）

| 文档 | 内容 |
|---|---|
| [`refactor-v4/00-阶段0-审计与基线.md`](refactor-v4/00-阶段0-审计与基线.md) | Phase 0：现状审计、六项实测取证、复用清单、13 个待修问题、4 个待拍板决策 |
| [`refactor-v4/01-阶段1-修改计划.md`](refactor-v4/01-阶段1-修改计划.md) | Phase 1 计划：观察/时间语义、TDD 顺序、改动清单、验收标准 |
| [`refactor-v4/02-阶段1-实施记录.md`](refactor-v4/02-阶段1-实施记录.md) | Phase 1 实施：心跳不再造观察、墙钟推进、状态常驻、预测步回基线修复 |
| [`refactor-v4/03-阶段2-统一查询.md`](refactor-v4/03-阶段2-统一查询.md) | Phase 2：query_curve（窗口/分辨率/缓存），主窗与挂件共用一份 payload |
| [`refactor-v4/04-阶段3-增量衰减.md`](refactor-v4/04-阶段3-增量衰减.md) | Phase 3：内核 predict_step 原语 + 衰减尾巴增量推进（15.62ms→0.30ms） |
| [`refactor-v4/05-阶段4-生命周期与刷新.md`](refactor-v4/05-阶段4-生命周期与刷新.md) | Phase 4：关主窗≠退出、挂件常驻、5 分钟刷新、显式退出入口 |
| [`refactor-v4/06-阶段5-曲线显示与性能.md`](refactor-v4/06-阶段5-曲线显示与性能.md) | Phase 5：静态层缓存真正生效、预测段虚线、widgets.py 清死控件 |
| [`refactor-v4/07-阶段6-持久化加固.md`](refactor-v4/07-阶段6-持久化加固.md) | Phase 6：快照校验、恢复幂等、长间隔分段、重启上下文恢复 |
| [`refactor-v4/08-阶段7-验收.md`](refactor-v4/08-阶段7-验收.md) | Phase 7：13 项验收对照、五条核对、三处未做到、总账与下一步 |

## UI 重构（历史）

| 文档 | 内容 |
|---|---|
| [`ui-compact-refactor/`](ui-compact-refactor/) | 小窗化重构阶段 0～7（调研 / 方案 / 逐阶段记录 / 瘦身前两波） |
| [`UI_PREFLIGHT_视觉传达与UI设计调研.md`](UI_PREFLIGHT_视觉传达与UI设计调研.md) | 视觉传达与 UI 设计调研 |
| [`screenshots/`](screenshots/) | 主界面 / 模型页 / 历史页 / 纠正框 / 迷你窗实拍 |
| `screenshots/v5-*.png` | v5 视觉验收：400×680 / 360×600 / 模型 / 历史 / 悬浮窗 / 空态 / 错误态 / 悬停 |

## 归档（仅作历史参考）

| 文档 | 说明 |
|---|---|
| [`history/refactor-progress.md`](history/refactor-progress.md) | v2 内核重构执行日志（Phase 0–9） |
| [`history/implementation-plan-1x.md`](history/implementation-plan-1x.md) | 1.x 桌面应用 TDD 计划（所述模块已退役） |
| [`测试报告/`](测试报告/) | P3C 代码质量检查等审查留档 |
