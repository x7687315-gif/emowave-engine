# UI Exploration 03 · 定稿方向「水位」· 低饱和深海蓝

在 02 的 B 方案上继续收敛：曲线重画、基线做出特色、当下提到最强、三页打通、加入蓝色。
**生产代码零改动、未提交。**

```bash
cd "C:/Users/86159/Desktop/代码/emowave-engine"
PY=/c/Users/86159/AppData/Local/Programs/Python/Python39/python.exe

start docs/ui-exploration-03/screenshots/system-sheet.png      # 一张图看六个画面
$PY docs/ui-exploration-03/prototype3.py --live                # 真窗口（可记录/切页/开纠正）
```

真窗口里可以做的：
- 点「● 开始记录」→ 整条控制带反相成深蓝；主屏出现唤醒滑杆
- **上下拖动那条蓝色横线**设定效价（B 的核心交互）
- 点「记一笔」提交一次自评；点「纠正」打开生产的上滑框
- 切「模型」页：`− / +` 微调常态、分叉、重置、学习一步
- 切「历史」页：按天分组的事件、水平偏差条
- 悬浮窗在 `screenshots/mini-*.png`；实时预览里它是独立窗口，可拖动记录

---

## 文件

| 文件 | 内容 |
|---|---|
| `00-设计说明.md` | 这轮的五项决定、曲线数学、基线规则、蓝色语义、三页结构、待确认 |
| `tokens.py` | 中性三级灰 + 语义蓝 + 曲线线型 |
| `softcurve.py` | 单调三次插值、置信带、常态带、水位线、时间刻度 |
| `views.py` | 主屏 / 模型页 / 历史页 / 全局记录条 |
| `mini.py` | 悬浮窗（360×134 收起 / 360×180 展开） |
| `datasource.py` | 真实 presenter + 多天演示数据（临时库） |
| `prototype3.py` | 采集、系统对照图、实时预览 |
| `screenshots/` | 9 张实拍（含 3 个悬浮窗状态）+ `system-sheet.png` + manifest |

## 演示数据

- 今天：11 次自评（09:00 起约 8 小时）+ 2 处拖点纠正，由真实 Matérn/Kalman/RTS 产出 311 个曲线点
- 前两天：各一段短会话，**走的是真实"开始记录→采样→停止"**，不是手插的假行
- 演示库里把 `created_at` 归一到 `start_time`，好让历史页按天分组
- 数据库在 `tempfile.mkdtemp()`，退出即删，绝不碰 `~/.emowave/`

## 重新生成

```bash
PYTHONIOENCODING=utf-8 $PY docs/ui-exploration-03/prototype3.py --all
PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen $PY -m pytest -q   # 生产应仍 769 passed
git status --short                                                   # 只应有 ?? docs/ui-exploration-0x/
```
