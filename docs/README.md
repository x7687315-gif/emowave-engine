# docs · 文档索引

> 根目录只保留 `README.md`；其余文档按用途分层放在这里。

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

## UI 重构（历史）

| 文档 | 内容 |
|---|---|
| [`ui-compact-refactor/`](ui-compact-refactor/) | 小窗化重构阶段 0～7（调研 / 方案 / 逐阶段记录 / 瘦身前两波） |
| [`UI_PREFLIGHT_视觉传达与UI设计调研.md`](UI_PREFLIGHT_视觉传达与UI设计调研.md) | 视觉传达与 UI 设计调研 |
| [`screenshots/`](screenshots/) | 主界面 / 模型页 / 历史页 / 纠正框 / 迷你窗实拍 |

## 归档（仅作历史参考）

| 文档 | 说明 |
|---|---|
| [`history/refactor-progress.md`](history/refactor-progress.md) | v2 内核重构执行日志（Phase 0–9） |
| [`history/implementation-plan-1x.md`](history/implementation-plan-1x.md) | 1.x 桌面应用 TDD 计划（所述模块已退役） |
| [`测试报告/`](测试报告/) | P3C 代码质量检查等审查留档 |
