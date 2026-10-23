# UI Exploration 01 · 月光微金 × 云白深海

**设计探索目录。生产代码零改动，未提交、未推送。**

 EmoWave 的界面要从"暖纸 + 陶土"换成"黑白灰骨架 + 少量语义点缀色"。
这里放了两个候选方案的**可预览原型**与全部评估依据，供你选定一个后再讨论正式重构。

---

## 30 秒看完两套方案

```bash
# 1) 并排对比页（浏览器直接打开，不需要服务器）
start docs/ui-exploration-01/screenshots/compare.html

# 2) 真窗口上手玩：可以悬停曲线、拖纠正框的节点、按 Ctrl+R 记录
cd "C:/Users/86159/Desktop/代码/emowave-engine"
/c/Users/86159/AppData/Local/Programs/Python/Python39/python.exe \
    docs/ui-exploration-01/prototype.py a light      # A 月光微金 · 亮
#   把末尾换成  a dark / b light / b dark  看其余三组
```

> `python` 直接敲会命中 Windows Store 占位符（退出码 49），必须用上面的完整路径。
> 项目实际运行时是 Python 3.9.13 + PyQt5 5.15.11。

---

## 文件地图

| 文件 | 是什么 |
|---|---|
| `00-现状审查报告.md` | 仓库状态、真实 UI 结构、令牌收敛度、**已验证缺陷 D1–D6**、待验证假设 H1–H5 |
| `01-双主题设计简报.md` | 全部颜色令牌、文字层级、间距/线宽/圆角、状态色、亮暗映射规则 |
| `02-AB对照评估.md` | 可测事实 + 我的判断 + 需要你拍板的 5 件事 |
| `03-实施建议.md` | 哪些直接复用、哪些必须重构、**哪些现在不该碰**、工作量切分 |
| `palette_tokens.py` | A/B × 亮/暗 四组色板值 + 结构令牌（**唯一需要你改的就是这个文件**） |
| `prototype.py` | 原型入口：注入色板 → 复用真实组件 → 喂演示数据 → 抓图 |
| `contrast_report.py` | WCAG 对比度 + 灰度分离实测（纯标准库） |
| `accent_coverage.py` | 数像素：点缀色到底占了多少画面 |
| `fairness_check.py` | 断言 A/B 布局/尺寸/字体/数据完全一致 |
| `contrast-report.md` | 上面那个脚本的产出，勿手改 |
| `screenshots/` | 24 张真实组件截图 + `compare.html` + manifest |

---

## 原型是怎么做到"不碰生产代码"的

生产 UI 的颜色全部来自 `theme.COLORS` / `theme.CURVE_STYLE`。
`prototype.py` 在 **import 任何界面模块之前**把那两张字典整体换成候选值，
然后照常构造真正的 `MainWindow` / `MiniWindow` / `CorrectionSheet`。

所以截图里看到的是**真实组件、真实排版、真实曲线渲染**，只有颜色是候选值——
不是 HTML 仿制品，也不是配色表。

两个必须知道的边界：

1. **A 与 B 不能共存于同一进程**。因为 `curve_widget.py` 有 2 处、
   `main_console.py` 有 3 处在 import 期就把颜色固化成常量/类属性了。
   原型因此每个主题起一个子进程。这正是正式重构要修的债，写在 `03` §2.2。
2. **只换了值，没换结构**。字号层级、圆角、线宽、组件集合、交互流程一个都没动——
   `apply_palette()` 里有一条 `assert` 专门守这件事，`fairness_check.py` 再复核一遍。

---

## 哪些是演示数据

全部截图里的内容都是**脚本造的演示数据**，不是你真实的情绪记录：

| 内容 | 来源 |
|---|---|
| 11 次自评（09:00 起，跨约 8 小时，午后一次低谷） | `prototype.py` 的 `DEMO` 常量 |
| 2 处拖点纠正（共 4 条纠正记录） | `CORRECTIONS` 常量 |
| 基线、先验人群 | 默认 archetype + 演示数据自动学习得到 |
| 数据库 | `tempfile.mkdtemp()` 里的临时 sqlite，**进程结束即删** |

> 原型**从不读写** `~/.emowave/` 下的真实数据库与快照。这一点由
> `DatabaseManager(os.path.join(tmp, 'demo.db'))` 显式传路径保证。

时间戳取"今天 09:00"，所以截图上的时间看着像真实一天；同一天内 A/B 完全一致。

---

## 怎么比较两套（建议顺序）

1. 开 `screenshots/compare.html`，从上往下看 6 组并排图。
2. 只看**主窗正常态**，问自己：哪张图里你先看到曲线？（视觉重心）
3. 切到**暗色**，问：哪张的底还算是"黑白灰"？
   这条有实测答案——B 暗色的中性轴色偏 18.8，和旧暖纸的 19.5 同级（`02` §1.2）。
4. 打开 `*-mini-*.png` 看悬浮窗：它比主窗更克制吗，还是只是缩小版？
5. 用 `prototype.py a light` 真开一个窗口，把滑块拖动、按 Ctrl+R、点状态头「纠正」，
   亲手试交互反馈——静态图看不出hover和焦点。
6. 填 `02` §2 的判断表：不同意哪行直接说。

---

## 重新生成

```bash
cd "C:/Users/86159/Desktop/代码/emowave-engine"
PY=/c/Users/86159/AppData/Local/Programs/Python/Python39/python.exe

# 改完 palette_tokens.py 后：
PYTHONIOENCODING=utf-8 $PY docs/ui-exploration-01/contrast_report.py    # 先看有没有掉出 AA
PYTHONIOENCODING=utf-8 $PY docs/ui-exploration-01/prototype.py --all    # 重拍 24 张 + compare.html
PYTHONIOENCODING=utf-8 $PY docs/ui-exploration-01/fairness_check.py     # 公平性断言
PYTHONIOENCODING=utf-8 $PY docs/ui-exploration-01/accent_coverage.py    # 彩色占比

# 确认生产没被碰坏
PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen $PY -m pytest -q       # 应 769 passed
git status --short                                                      # 只应有本目录下的 ?? 条目
```

**截图不要用 `QT_QPA_PLATFORM=offscreen`** —— 离屏平台不渲染文字，
拍出来是"没有字的界面"。默认走 Windows 平台插件即可。

---

## 边界与许可

- 本目录是探索产物。选定方案后，正式改动落在 `theme.py` 等生产文件里，
  本目录留作决策依据（或届时归档/删除，由你定）。
- 本目录下的 `.py` 不参与架构护栏：`tests/test_architecture.py` 的 `SKIP_DIRS` 含 `docs`，
  所以不计入 LOC 预算、不算孤儿模块。加入本目录后生产测试仍 769 全绿。
