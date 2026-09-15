# 开工前经验报告 · EmoWave UI 重做（视觉传达 / UI 设计方向）

> 生成时间：2026-09-15
> 调研触发：上一版 AI 生成的 UI 效果不满意，推倒重来
> 调研维度：① 开源仓库/代码样例 ② Issues/踩坑 ③ 论文/文档/awesome

---

## 1. 项目简报（复述确认）

| 项 | 内容 |
|---|---|
| **要构建** | 心潮 EmoWave 桌面端 UI 重做。个人情绪状态引擎，核心视觉物是一条时间序列情绪曲线（模型线 + 置信带 + 基线 + 原始点 + 当前点） |
| **现有形态** | PyQt5 单页控制台：侧栏锚点导航（7 项）+ 滚动单页，含实时状态 / 情绪曲线 / 实时调节 / 基线主权 / 个人模型 / 纠正 / 回顾历史 |
| **已定设计语言** | "精密情绪科学仪器"（Linear × Raycast × macOS × Vercel × Arc）：暖白底 / 近黑字 / 极细边 / 克制蓝 / 曲线为中心 / 无卡片堆 / 无渐变玻璃大圆角 emoji |
| **技术约束** | PyQt5，纯 Python；核心 `emowave/` 零依赖（不引入 numpy 到内核） |
| **非功能约束** | 情绪数据属敏感个人数据，本地存储；低配设备可用（已有 Tier 分档） |
| **目标用户** | 单人、自用为主的自追踪（personal informatics）场景 |

---

## 2. 现成轮子清单

| 名称 | 类型 | 为什么考虑 / 为什么不选 | 可信度 | 结论 |
|---|---|---|---|---|
| **Qt Style Sheets (QSS)** | 官方机制 | 已是项目在用的方案，问题不在机制而在用法（见 §4） | ★★★ 官方 | ✅ 保留，但重构用法 |
| **QPainter + QPainterPath** | 官方绘图 | 曲线/环/平面已是自定义 QPainter。**对"精密仪器"美学最可控**——发丝线宽、字距、抗锯齿全部自己掌握 | ★★★ 官方 | ✅ 保留，但必须修实现 |
| **pyqtgraph** | 第三方库 | 实时性能强（宣称 50-60fps vs matplotlib 5-15fps），但**官方博客自述 "focuses on performance over aesthetics / limited styling options"**，默认观感是"科学家默认样式"，与"精密仪器"美学打架；且引入 numpy/pyqtgraph 依赖 | ★ 厂商博客（有利益关系，见 §5 三角验证结论） | ⚠️ 可选，但**不推荐**当前阶段引入 |
| **matplotlib** | 第三方库 | 出版级静态图，实时更新差（200ms+ 延迟），GUI 集成不顺畅 | ★★ 社区 | ❌ 不选 |
| **QtCharts** | 官方模块 | 免费版可用，但样式定制同样受限，且需额外安装 PyQtChart | ★★★ 官方 | ⚠️ 备选 |
| **PyQt-Fluent-Widgets (QFluentWidgets)** | 第三方组件库 | Fluent/WinUI 风格组件齐全、亮暗主题成熟 **——但这是微软消费级圆角审美，与"精密仪器"方向正好相反** | ★★ 社区高星 | ❌ 明确不用（方向性错误） |
| **QInstrument** | 参考仓库 | PyQt 仪器界面框架，可参考其"仪器面板"信息组织方式 | ★ 个人仓库（低活跃） | 📖 仅作参考 |
| **Mu2Rphy/linear-design-system** | 参考仓库 | 从 linear.app 提取的前端视觉 spec，可直接抄 spacing/color/type 体系 | ★ 个人仓库 | 📖 仅作参考 |

---

## 3. 推荐技术栈 / 架构参考

### 3.1 架构建议：三件事分开

```
theme.py        ← 单一来源：所有颜色/字号/间距/线宽的 token（primitive→semantic）
style.py        ← 由 token 生成的一份 app 级 QSS（只生成一次）
widgets.py      ← 自定义绘制控件，paintEvent 从 theme.py 取值，不硬编码
```

**关键改变**：停止在循环里 `btn.setStyleSheet(...)`（当前 `main_app.py:138-164` 就在循环里给每个导航按钮设样式表）。改为「一份应用级 QSS + 少量状态类」。

### 3.2 实测到的本机环境（影响设计决策）

| 检查项 | 结果 | 影响 |
|---|---|---|
| Cascadia Code 字体 | ✅ 本机存在（`CascadiaCode.ttf`） | 等宽数值可用，但**不能假设所有用户都有**，需显式 fallback 链 |
| Segoe UI | ✅ 存在（含 Light/Semibold 等 6 个字面） | 界面字体可用 |
| 微软雅黑 | ✅ 存在（`msyh.ttc`） | 中文回退可用 |
| HighDPI 属性 | ❌ **全项目零处设置** | ⚠️ 见 §4 坑 #1，这是"看起来糊/廉价"的头号嫌疑 |

---

## 4. 已知坑与 workaround（重点）

### 坑 #1 — PyQt5 未启用 HighDPI → 界面整体发虚 🔴 高优先级

**现象**：在 125%/150% 缩放的 Windows 上，PyQt5 默认由系统做位图拉伸，文字和 1px 发丝线全部变模糊。一个号称"极细边"的设计，边线糊掉就等于设计语言失效。

**workaround**：必须在 `QApplication` 创建**之前**设置（顺序错了无效）：
```python
from PyQt5.QtCore import Qt
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
# PyQt 5.14+ 建议
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
app = QApplication(sys.argv)
```
**来源**：Qt 官方文档 High DPI 章节 <https://doc.qt.io/qt-6/highdpi.html>
**状态**：当前 `main_app.py:261` 直接 `QApplication(sys.argv)`，未设任何属性。

---

### 坑 #2 — QSS 不继承 font/color，导致"补丁式"界面 🔴 高优先级

**现象（官方原文）**：
> *"By default, when using Qt Style Sheets, a widget does **not** automatically inherit its font and color setting from its parent widget."*

**为什么这会让 AI 生成的 PyQt 界面看起来廉价**：给容器设了颜色，里面没逐个设色的子控件全部回落到**系统默认色/系统字体**。结果是同一屏里字体、灰度深浅参差——这正是"AI 搓的"典型观感。

**workaround**（三选一，建议全用）：
1. 在 `QApplication` 上设一次全局：`app.setStyleSheet("QWidget { color: ...; font-family: ...; }")`
2. 设传播标志：`QCoreApplication.setAttribute(Qt.AA_UseStyleSheetPropagationInWidgetStyles, True)`
3. 选择器写全：`QGroupBox, QGroupBox * { color: red; }`（官方示例）

**来源**：<https://doc.qt.io/qt-6/stylesheet-syntax.html>（官方文档，"Font and Color Inheritance" 章节）
**状态**：当前项目在 `main_app.py` / `windows/*.py` 里逐控件 `setStyleSheet`，正是反模式。

---

### 坑 #3 — 复杂控件（QScrollBar / QComboBox / QMenu）必须整套定制

**现象（官方原文）**：
> *"With complex widgets such as QComboBox and QScrollBar, if one property or sub-control is customized, **all** the other properties or sub-controls must be customized as well."*

**后果**：`QScrollArea` 里冒出一条**原生 Windows 粗滚动条**，或者一个半定制的下拉框——一个元素就能把"精密仪器"的调性彻底破坏。当前项目滚动单页 + 多处列表，滚动条必然出现。

**workaround**：显式定制 `QScrollBar` 的 `handle` / `add-line` / `sub-line` / `add-page` / `sub-page` 全部子控件，或直接 `QScrollBar { ... }` 全套。同理处理 `QMenu`（`::item` / `::separator`）。

**来源**：<https://doc.qt.io/qt-6/stylesheet-syntax.html>（官方 Note）

---

### 坑 #4 — 自定义 QWidget 子类的 QSS 背景不生效

**现象**：继承 `QWidget` 并重写 `paintEvent` 的自定义控件，QSS 里设 `background-color` 无效。

**workaround**：
- 方案 A：`widget.setAttribute(Qt.WA_StyledBackground, True)`
- 方案 B：改继承 `QFrame`（自带 styled paintEvent）
- 方案 C：`paintEvent` 里手动 `QStyleOption` + `style().drawPrimitive()`

**来源**：Qt Style Sheets 官方参考 <https://doc.qt.io/qt-6/stylesheet-reference.html>；社区实证 <https://www.cnblogs.com/ybqjymy/p/18075556>
**状态**：当前 `RiskRingWidget` / `EmotionCanvas` / `StatBlock` / `EmotionCurveWidget` 均继承 `QWidget` 并手绘 `fillRect` 绕过——能跑但与 QSS 体系割裂。

---

### 坑 #5 — 曲线逐段 drawLine + RoundCap → 接缝"串珠"且慢 🔴 影响观感

**当前实现**（`widgets.py:312-319`）：
```python
p.setPen(QPen(QColor(COLORS['accent']), 2, Qt.SolidLine, Qt.RoundCap))
for i in range(1, len(self.model_v)):
    p.drawLine(x1, y1, x2, y2)   # 每段单独画
```
**两个问题**：
1. **视觉**：每段独立描边 + 圆头端帽，在折线拐点处圆帽互相叠加，形成**不均匀的"串珠/结节"**——曲线是视觉主角，这个瑕疵最致命。
2. **性能**：N 次 drawLine 调用。项目日志已记录 **曲线重建 149ms**（`REFACTOR_PROGRESS.md` Phase 相关条目），与逐段绘制吻合。

**workaround**：改为 `QPainterPath` 一次性 `strokePath`：
```python
path = QPainterPath()
path.moveTo(x0, y0)
for ...: path.lineTo(x, y)
p.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
p.strokePath(path, p.pen())   # 单次绘制，均匀线宽，接缝由 RoundJoin 处理
```
再叠加：静态层（网格/轴/基线）缓存到 `QPixmap`，只有数据层每次重绘。

---

### 坑 #6 — Token 化没做完，硬编码颜色残留

`widgets.py` 里仍有两处绕过 `COLORS` token：
- `:302` `QColor(30, 64, 175, 26)` — 置信带
- `:308` `QColor(156, 150, 138, 120)` — 原始点

另有**注释与实现漂移**（说明是 AI 多次改写的痕迹）：
- `:311` 注释「模型估计线（灰绿）」→ 实际用 `accent`（蓝）
- `:321` 注释「当前点（朱红）」→ 实际用 `accent`（蓝）
- 说明此前存在过一套"灰绿 / 朱红"色板，被替换后注释没跟上。**重做时必须整体清掉，不留旧色板幽灵。**

---

### 坑 #7 — QSS 其他官方限制（容易踩）

| 限制 | 说明 | 来源 |
|---|---|---|
| 不支持 `!important` | `Qt currently doesn't implement !important` | 官方 stylesheet-syntax |
| 类型选择器特异性相同 | `QPushButton` 与 `QAbstractButton` 特异性一样，**后者出现的规则胜** | 同上 |
| 自身样式表绝对优先 | widget 自身 setStyleSheet 优先于祖先/QApplication，且不看特异性 | 同上 |
| 属性选择器需手动重算 | Qt 属性变更后要 unset→set 样式表才生效 | 同上 |
| `qproperty-` 只求值一次 | **不能用于 `:hover` 等伪状态** | 同上 |
| 命名空间内自定义控件 | 类型选择器要把 `::` 写成 `--` | 同上 |

---

## 5. 参考论文 / 文档 / awesome（视觉传达理论）

### 5.1 数据可视化（对本项目的曲线设计直接适用）

| 来源 | 关键结论 | 对本项目的意义 |
|---|---|---|
| **Tufte《The Visual Display of Quantitative Information》(1983)** | **data-ink ratio**：最大化数据墨水，最小化装饰；"Above all else, show the data"；提出 **sparklines**、**small multiples** | 曲线应去除一切非数据元素；历史/回顾可用 sparkline |
| **Stephen Few《The Chartjunk Debate》(2011)** 🔗 <https://www.cs.rug.nl/svcg/uploads/VisualAnalytics/Few11.pdf> | Tufte 的极端立场需要修正：**"nothing that supports the chart's message in a meaningful way is junk"**——非数据元素若能①吸引注意 ②强调重点 ③增强记忆，则不是 junk | 给"克制的装饰"提供了理论许可：网格线、发丝分隔线只要服务于读数是合理的 |
| **Cleveland & McGill《Graphical Perception》(1984)** 🔗 <https://www.jstor.org/stable/2288400> | 视觉编码精确度排序：**位置（共同刻度）> 长度 > 角度/斜率 > 面积 > 体积 > 色彩饱和度** | 情绪值应用**位置/线**表达，不要依赖颜色深浅；当前 RiskRing 用环形角度+颜色，是低精度编码 |
| **Few 的仪表盘 13 个常见错误** 🔗 <https://www.bpminstitute.org/resources/articles/dashboard-design/> | **最常见错误：把过多数据塞进一屏**，把监控工具变成没人看的数据堆；仪表盘定义是"**单屏即可一览**" | ⚠️ 直接命中当前设计：7 个分区塞进一屏滚动 |
| **Preattentive processing（前注意加工）** | 人脑在 **<250ms**、意识介入前即可察觉 color/size/position/shape | 状态异常应靠前注意属性"跳出来"，而不是靠用户逐项读 |
| **红绿色盲** | 约 **7% 男性**无法有效区分红绿（红绿灯式配色无效） | RiskRing 的琥珀→砖红状态色需配形状/文字冗余编码 |

### 5.2 个人信息化（personal informatics）—— 本项目的学术根基

**Li, Dey & Forlizzi《A Stage-Based Model of Personal Informatics Systems》, CHI 2010**
🔗 <https://dl.acm.org/doi/10.1145/1753326.1753409> ｜ PDF <https://www.cs.cmu.edu/~jhm/Readings/2010-ianli-chi-stage-based-model.pdf>

五阶段模型：**Preparation → Collection → Integration → Reflection → Action**

> **核心设计启示**：自追踪类应用的失败点几乎总在 **Reflection（反思）阶段**——数据收集得很勤，但用户"看到数字却不知道意味着什么、该做什么"。因此 UI 的重心不应是"把所有数据都显示出来"，而是**降低从数据到洞察的解释成本**。

对 EmoWave 的直接推论：
- 「基线主权」「纠正」「个人模型」这些高级功能是 **Integration** 层，不应与日常 **Reflection** 层争夺首屏
- 首屏应该回答一个问题：**"我现在怎么样，和平时比如何"**
- 需要提供**对比锚点**（vs 我的基线 / vs 上周同时段），而不是孤立的绝对值

### 5.3 视觉风格参考

| 来源 | 可提取的硬参数 |
|---|---|
| **Linear UI 改版复盘** 🔗 <https://linear.app/now/how-we-redesigned-the-linear-ui> | 主题变量从 **98 个收敛到 3 个**（base color / accent color / **contrast**）；色彩空间从 HSL 迁到 **LCH**（感知均匀：lightness 50 的红与黄看起来一样亮）；标题 Inter Display、正文 Inter；用**对齐**而非间距来控制密度。**注：未公开任何 px 值**，不能照抄间距 |
| **Designing Calm: UX Principles for Reducing Users' Anxiety** 🔗 <https://www.uxmatters.com/mt/archives/2025/05/designing-calm-ux-principles-for-reducing-users-anxiety.php> | 情绪类界面应避免制造焦虑的交互模式（紧迫感、连续中断、惩罚性反馈） |
| 项目内已有 `docs/emowave-banner.svg` | README 已是**构成主义**（红楔 + 墨黑 + 纸白 + 粗黑无衬线 El Lissitzky 式） |

⚠️ **发现的不一致**：README/品牌是**构成主义·红**，而 UI 是 **Linear·蓝**。两套品牌语言并存——重做时需要决定统一到哪一边。

### 5.4 三角验证结论

| 结论 | 来源数 | 判定 |
|---|---|---|
| QSS 不继承 font/color | Qt 官方文档 + 多处社区实证 | ✅ 确认 |
| 复杂控件必须整套定制 | Qt 官方文档 | ✅ 确认 |
| data-ink / chartjunk | Tufte 原著 + Few 2011 + BPM Institute | ✅ 确认（且 Few 给出了修正版，非教条） |
| Cleveland-McGill 编码排序 | 原著 JSTOR + 多处二手引用 | ✅ 确认 |
| pyqtgraph 50-60fps vs matplotlib 5-15fps | **仅 pyqtgraph.com 自家博客** | ⚠️ **待验证**，有厂商利益，未获独立来源交叉印证。**不建议据此做选型决定** |
| 红绿色盲 ~7% 男性 | BPM Institute（引 Few） | ⚠️ 单一来源，量级可信但需自行按 WCAG 校验 |

---

## 6. 对当前 UI 的诊断（本报告最有价值的部分）

按视觉传达原理逐条对照，回答"为什么不喜欢的可能性在哪"：

| # | 问题 | 依据 | 严重度 |
|---|---|---|---|
| 1 | **7 个分区塞进一屏滚动**——既不是"一览即懂"的仪表盘，也不是聚焦的工具页。侧栏锚点+滚动是 Web 模式硬套桌面 | Few：单屏一览；最常见错误是塞太多 | 🔴 |
| 2 | **全部用 #E6E6E3 发丝线**（对 #FAFAF9 对比度约 1.15:1）——"极细"过头变成"没结构"，整体读起来是"没做完"而非"精密" | 精密感来自**清晰的结构**，不是看不见的结构 | 🔴 |
| 3 | **曲线无刻度/无网格**：只有"效价""时间→"两个标签，没有 Y 轴数值、时间刻度、网格线。号称"精密仪器"却没有标尺 | Tufte：轴与刻度是解读数据所必需，属 data-ink | 🔴 |
| 4 | **RiskRingWidget 是环形仪表**——用角度+颜色编码单一数值，是 Cleveland-McGill 里精度最低的编码之一；且 Few 明确批评仪表盘滥用仪表盘/速度表 | Cleveland-McGill；Few | 🟠 |
| 5 | **逐段 drawLine + RoundCap** → 拐点串珠；149ms 重建 | 见 §4 坑 #5 | 🟠 |
| 6 | **侧栏 150px / 11px 字 / 28px 行高**，收起态用日式竖排二字——密度高但无层级，属技巧性装饰 | Linear：靠**对齐**而非花活控制密度 | 🟡 |
| 7 | **未设 HighDPI** → 1px 发丝线在缩放屏上糊掉，设计语言直接失效 | 见 §4 坑 #1 | 🔴 |
| 8 | **token 未收口**：硬编码 rgba 残留 + 注释/实现漂移（"灰绿""朱红"旧色板幽灵） | 见 §4 坑 #6 | 🟡 |
| 9 | **品牌不一致**：README 构成主义红 vs UI Linear 蓝 | 见 §5.3 | 🟡 |
| 10 | **状态色仅靠颜色**（琥珀/砖红），无障碍下不可辨 | 红绿色盲 ~7% | 🟡 |

---

## 7. 风险与开工 checklist

- [ ] **HighDPI 三行属性**在 `QApplication` 创建前设置（坑 #1）
- [ ] 建立 `theme.py` 单一 token 源，**规则：代码里出现任何 hex/rgba 字面量即为违规**
- [ ] 改为**一份 app 级 QSS**，删除循环内 `setStyleSheet`（坑 #2）
- [ ] 显式定制 **QScrollBar / QComboBox / QMenu 全套子控件**（坑 #3）
- [ ] 曲线改 **QPainterPath 单次 strokePath**，静态层缓存 QPixmap（坑 #5）
- [ ] 曲线补 **Y 轴刻度 + 时间刻度 + 极淡网格线**（诊断 #3）
- [ ] 状态异常用**颜色 + 形状/文字**冗余编码，不靠颜色单打独斗（诊断 #10）
- [ ] **决策：首屏是否砍到 3 个分区以内**（诊断 #1）——这是最关键的信息架构决定
- [ ] **决策：品牌统一到构成主义红还是延续 Linear 蓝**（诊断 #9）
- [ ] 待验证项：pyqtgraph 性能数据（厂商来源，需自测）——**当前阶段建议不引入，先把 QPainter 路径优化到位**
- [ ] License 合规：本项目 MIT；新增第三方库需确认（当前结论是不新增）

---

## 8. 来源汇总（可追溯）

**官方文档**
- Qt Style Sheet Syntax（坑 #2/#3/#7）<https://doc.qt.io/qt-6/stylesheet-syntax.html>
- Qt Style Sheets <https://doc.qt.io/qt-6/stylesheet.html>
- Qt Style Sheets Reference（坑 #4）<https://doc.qt.io/qt-6/stylesheet-reference.html>
- Qt High DPI（坑 #1）<https://doc.qt.io/qt-6/highdpi.html>

**开源仓库**
- pyqtgraph <https://github.com/pyqtgraph/pyqtgraph> ｜ Issue #1039 多线绘制慢 <https://github.com/pyqtgraph/pyqtgraph/issues/1039>
- PyQt-Fluent-Widgets <https://github.com/zhiyiYo/PyQt-Fluent-Widgets>（已判方向不符）
- QInstrument <https://github.com/davidgrier/QInstrument>
- linear-design-system spec <https://github.com/Mu2Rphy/linear-design-system>

**论文 / 权威文档**
- Cleveland & McGill, Graphical Perception (1984) <https://www.jstor.org/stable/2288400>
- Few, The Chartjunk Debate (2011) <https://www.cs.rug.nl/svcg/uploads/VisualAnalytics/Few11.pdf>
- Li, Dey & Forlizzi, A Stage-Based Model of Personal Informatics Systems, CHI 2010 <https://dl.acm.org/doi/10.1145/1753326.1753409>
- Dashboard Design（Few 13 个错误综述）<https://www.bpminstitute.org/resources/articles/dashboard-design/>
- Graphical Perception 讲义 <https://rstudio-pubs-static.s3.amazonaws.com/342939_d79a0160031d464f8a4cad3e20bbdbc4.html>
- A Data Storyteller's Guide To Avoiding Clutter <https://www.effectivedatastorytelling.com/post/a-data-storytellers-guide-to-avoiding-clutter>

**设计参考**
- How we redesigned the Linear UI <https://linear.app/now/how-we-redesigned-the-linear-ui>
- Designing Calm: UX Principles for Reducing Users' Anxiety <https://www.uxmatters.com/mt/archives/2025/05/designing-calm-ux-principles-for-reducing-users-anxiety.php>

**低可信来源（已排除，未采信任何结论）**：CSDN / 百度文库 / runebook 等聚合站的二手 QSS 教程。
