# UI Exploration 02 · 两套结构方案（灰度）

**这一轮不是换颜色，是重排结构。** 生产代码零改动、未提交、未推送。

上一轮（`docs/ui-exploration-01/`）验证了"同一套布局只换色板"。
你的反馈很明确：**换色不够，要从结构重新想。** 于是本轮：

- 不动任何生产文件；
- 保留全部功能与数据语义；
- 但把"谁先被看见、谁该让位、靠什么建立层级"重新做了一遍；
- 两套方案在**布局与视觉层级上有实质区别**，不是同一张图换个排法。

---

## 30 秒预览

```bash
cd "C:/Users/86159/Desktop/代码/emowave-engine"

# 1) 一张并列对照图（双击打开）
start docs/ui-exploration-02/screenshots/compare-side-by-side.png

# 2) 真窗口上手：可点记录、拖滑杆/水位线、开纠正框
PY=/c/Users/86159/AppData/Local/Programs/Python/Python39/python.exe
$PY docs/ui-exploration-02/prototype2.py a --live     # 方案 A 地平线
$PY docs/ui-exploration-02/prototype2.py b --live     # 方案 B 水位
```

> `python` 直接敲会命中 Windows Store 占位符（退出码 49），必须用上面的完整路径。
> 项目运行时是 Python 3.9.13 + PyQt5 5.15.11。

预览时可以真的操作：
- 点「● 开始记录」→ 界面进入记录态（A 长出滑杆 / B 整条控制带反相）
- B 的记录态里，**上下拖动那条横线**即可设定效价（这是 B 的核心假设）
- 点「记一笔」提交一次自评；点「纠正」打开生产的上滑框
- 点页签「模型 / 历史」会看到一行说明：这两页本轮没有重做

---

## 文件地图

| 文件 | 是什么 |
|---|---|
| `00-现状诊断.md` | 当前 UI 的结构问题（10 条，附实测）、检索台账、保留清单 |
| `01-两套方案.md` | 两套的意图、逐项改动、放弃的东西、对照表 |
| `02-假设与验证.md` | 灰度底线（含实测对比度）、每套最值得验证的假设与验证协议 |
| `gray_tokens.py` | 灰度令牌（严格 R=G=B）与字号/线宽常量 |
| `curve_ink.py` | 把真实 `curve_payload` 映射到任意几何（两套共用） |
| `plan_a.py` | 方案 A「地平线」的完整界面 |
| `plan_b.py` | 方案 B「水位」的完整界面 |
| `data_source.py` | 真实 presenter + 演示数据 + 交互方法（临时库，退出即删） |
| `prototype2.py` | 原型入口：采集截图、并列对照图、实时预览 |
| `screenshots/` | 8 张实拍 + 2 份 manifest + `compare-side-by-side.png` |

---

## 哪些是演示数据

**全部界面内容都是脚本造的**，不是你真实的情绪记录：

| 内容 | 来源 |
|---|---|
| 11 次自评（09:00 起约 8 小时，午后低谷、傍晚回升） | `data_source.py` 的 `DEMO` |
| 2 处拖点纠正（共 4 条纠正记录） | `CORRECTIONS` |
| 基线 0.55、状态名、读数 | 由真实内核在演示数据上算出 |
| 曲线 311 个点 | 11 个观察 + 5 分钟外推（每秒一个点），由内核 RTS/Kalman 生成 |
| 数据库 | `tempfile.mkdtemp()` 下的临时 sqlite，**进程结束即删** |

原型**从不读写** `~/.emowave/` 下的真实数据库与快照。
时间基取"今天 09:00"（若此刻早于 09:00 则退到昨天 09:00），保证演示数据全在过去
——`UserCorrection` 校验 `created_at ≥ timestamp`，数据落在未来会直接抛错。

---

## 怎么比较两套

1. 先看 `compare-side-by-side.png` 的第一行，问自己：**眼睛先落在哪？**
   - A：那句 31px 的「平静 · 偏积极」
   - B：那条横贯画面的水位线
2. 看第二行（记录中）：哪种"状态改变"更能让你意识到"正在记录"？
   - A：底部**长出**两条滑杆（空间变化）
   - B：整条控制带**反相**成黑（明度反转）
3. 看第三行（360×600）：压缩后谁先让位、有没有裁字。
4. 真窗口里试 B 的拖线设定效价，再对比 A 的滑杆 —— 这是两套最大的交互分歧。
5. 读 `02-假设与验证.md`，按里面的 5 秒测试自测一遍。

---

## 重新生成

```bash
cd "C:/Users/86159/Desktop/代码/emowave-engine"
PY=/c/Users/86159/AppData/Local/Programs/Python/Python39/python.exe

PYTHONIOENCODING=utf-8 $PY docs/ui-exploration-02/prototype2.py --all     # 8 张图 + 对照图
PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen $PY -m pytest -q        # 生产应仍 769 passed
git status --short                                                      # 只应出现本目录的 ?? 条目
```

截图**不要**用 `QT_QPA_PLATFORM=offscreen` —— 离屏平台不渲染文字，拍出来是没有字的界面。

---

## 边界

- 本目录是探索产物，`.py` 不参与架构护栏（`tests/test_architecture.py` 的 `SKIP_DIRS` 含 `docs`）。
- 生产文件一个字节都没改；没有提交，没有推送。
- 等你选定方案后，再讨论怎么落到 `theme.py` / `windows/` 里。
