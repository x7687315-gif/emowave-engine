# 设计走查报告 · EmoWave Instrument（精密仪器方向）

**走查目标**：windows/console_window.py / widgets.py / main_app.py（PyQt 单页控制台）
**设计源**：用户简报（Linear × Raycast × macOS × Vercel × Arc；暖白/近黑/极细边/克制蓝/曲线为中心/无重卡片无渐变无emoji）
**走查模式**：Mode A 自动走查（读代码）+ 对照设计源
**生成时间**：2026-09-07T22:19:41.300979

## 总览

| 严重度 | 数量 | 已修复 |
| --- | --- | --- |
| Blocker | 0 | — |
| Major | 3 | 3/3 |
| Minor | 5 | 6/7 |

## Findings（按严重度）

### Major（全部已修复）
1. [accessibility] 极小标签对比度不足 → 小字改 ink_soft（约7:1）。✅已修
2. [feedback] 基线 reset 不可逆无确认 → 加二次确认。✅已修
3. [feedback] 无记录时提交纠正静默 → 显示可行动提示。✅已修

### Minor（6/7 已修）
4. [ia] 侧栏 8 锚点 >7 → 合并为 7。✅
5. [components] 硬编码蓝 hex → token 化。✅
6. [visual-hierarchy] 同屏双 primary → 提交纠正改 outline。✅
7. [edge-states] 纠正列表无空状态 → 增加。✅
8. [feedback] 折叠展开无过渡 → 列为机会项（emil：低频操作可不动画）。⏳机会项

## 与设计源一致性（定向核对）
| 设计源要求 | 实现 | 判定 |
| --- | --- | --- |
| 暖白底/近黑字/极细边 | bg #FAFAF9 / ink #171716 / rule #E6E6E3 | ✅ |
| 克制蓝主色、颜色只表状态 | accent #1E40AF 仅模型线/当前点/控件；状态色 warn/danger/ok | ✅ |
| 曲线为视觉中心、非重卡片 | 曲线独立白面板(1px rule)为页面主角；状态条裸读数+发丝线 | ✅ |
| 强字阶 | 等宽大数值(Cascadia/Consolas) + 极小大写标签 | ✅ |
| 无渐变/玻璃/大圆角/emoji/彩虹 | 全部未使用 | ✅ |
| 精密仪器感、非 wellness/AI-dashboard | 工具感控件/低存在侧栏/mono 读数 | ✅ |

## 修复优先级
- 必须修复：3 major（已修）
- 建议修复：6 minor（已修）
- 可延后：1 minor（折叠过渡，机会项）
