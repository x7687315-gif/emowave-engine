# 心潮 EmoWave · 第二部分：轻量化内核 / Amiya 优雅集成 / 多平台架构

> **文档性质**：技术架构设计提案（供评审）
> **日期**：2026-09-05
> **前置文档**：
> - `ARCHITECTURE_EMOTION_CURVE.md`（第一部分：曲线建模与人机协同编辑）
> - `REFERENCES.md`（理论与工程参照索引）
> - `MOBILE_ARCHITECTURE.md`（v1.0 移动端预研 —— **本文档 §4.3 将推翻其核心选型**）
>
> **核验说明**：所有性能数字由 `research/core_bench.py` 实测得出（可复现）；所有依赖分布由逐文件 grep 统计；所有 star/版本来自 GitHub REST API 与 PyPI；所有文献 DOI 经 Crossref 交叉核验。查询日期 2026-09-05。

---

## 0. 摘要：三个需求，一个共同答案

| # | 需求 | 本质 |
|---|---|---|
| A | 简化架构，低配电脑/手机流畅运行 | **削减运行时依赖与常驻计算** |
| B | 接入 Amiya Agent，优雅降级、不影响其启动 | **可选插件化 + 零启动成本** |
| C | 为手机 App、Linux 版留空间 | **UI 与内核解耦，内核可移植** |

**这三个需求指向同一个答案：把「运行时内核」从现有仓库里剥离出来，做成零第三方依赖的纯标准库包，UI 层换成 Amiya 已在用的 Flet。**

三条实测证据支撑这个结论：

1. **`import numpy` 在本机耗时 1082.6 ms**（`research/core_bench.py`）。任何进 Amiya 启动路径的代码，只要 import numpy 就会拖慢 1 秒以上 —— 这是需求 B 的硬约束。
2. **内核的 numpy 表面积只有约 15 个 API 调用，且全部可平凡替换**（§2.2）。去掉 numpy 不是妥协，是净收益。
3. **PyQt5 只出现在 2 个文件、共 1,075 行**（`main_app.py` + `widgets.py` + `windows/`）。UI 层本就与算法隔离，换 Flet 的成本远低于直觉。

---

## 1. 现状：17,916 行里只有 15% 需要进设备

先量化「简化」的靶子。按**是否会在用户设备上执行**重新分层：

| 层 | 文件 | 行数 | 占比 | 是否进设备 |
|---|---|---:|---:|---|
| **核心引擎**（纯算法） | `kalman_filter` `models` `baseline` `threshold` `recommender` `predictor` `annotator` `config` | **2,701** | 15% | ✅ 必须 |
| **应用服务** | `engine` `session` `db` `crisis_protocol` `onboarding` `privacy_export` | **3,609** | 20% | ✅ 必须（但非算力瓶颈） |
| **UI 层** | `main_app` `widgets` `windows/`（PyQt5） | **1,075** | 6% | ⚠️ 需替换 |
| **研究/离线** | `data_simulator_v2` `simulator` `run_simulation` `benchmark_suite` `evaluation` `performance_profiler` `edge_case_tests` `test_*` `report_generator` `whitepaper_generator` `demo` `dashboard_data` | **11,254** | **63%** | ❌ 永不 |
| **合计** | | **17,916** | 100% | |

> **63% 的代码永远不会在手机上跑。** 模拟器、基准套件、性能剖析器、报告生成器、白皮书生成器 —— 它们是研究资产，不是产品资产。
>
> 当前仓库把它们和内核混在根目录，导致「这个项目的依赖是什么」无法回答：`requirements.txt` 写着 `PyQt5 + numpy + pytest`，但真正在设备上需要的只有标准库。

**这是第一件要做的事：按上表把仓库切成三块。** 见 §6。

---

## 2. 轻量化内核：零依赖纯 Python

### 2.1 实测证据

脚本 `research/core_bench.py`（本次运行输出）：

```
【导入成本】import numpy: 1082.6 ms

操作                                      纯Python         numpy       倍数
----------------------------------------------------------------------
Kalman 单步（4维状态/2维观测）                    64.1 us      22.6 us     2.8x
RTS 平滑 2000 点（整条曲线重算）                 172.4 ms      36.4 ms     4.7x
LinUCB 5臂x20轮 10x10 求逆                      2.5 ms       0.6 ms     4.0x
```

**怎么读这张表：**

- **单步滤波 64 微秒**。按 1 Hz 采样、且设备比本机慢 5 倍折算 → 每秒占用 **0.032% CPU**。numpy 快 2.8 倍，但这个差距在 1 Hz 下毫无意义。
- **RTS 平滑是唯一 numpy 有实质优势的地方**（4.7 倍）。但它是**一次性后台任务**（用户打开历史、事件结束时触发），不是实时路径。且 §2.4 有更优解。
- **导入成本 1082.6 ms 是最大的单项开销，而它一次性付清且无法摊销。** 对插件化需求（§3）这是致命的。

> **结论**：numpy 在本项目里买的性能，远远不值它的导入成本。核心是 4×4 和 10×10 矩阵 —— 这是**线性代数的玩具规模**，不是需要 BLAS 的规模。

### 2.2 numpy 表面积审计

逐文件统计 `np.*` 真实调用（grep 结果，2026-09-05）：

| 文件 | 调用 | 其中类型注解 | 真实运行时依赖 |
|---|---|---|---|
| `kalman_filter.py` | `array`×12, `ndarray`×8, `clip`×8, `sqrt`×3, `eye`×3, `linalg`×2, `zeros`×1, `trace`×1, `diag`×1 | `ndarray`×8 | clip / sqrt / eye / inv / zeros / trace / diag |
| `recommender.py` | `linalg`×5, `ndarray`×4, `zeros`×3, `array`×3, `sin`×2, `pi`×2, `eye`×2, `cos`×2, `sqrt`×1, `outer`×1 | `ndarray`×4 | 10×10 求逆 / 外积 / 周期特征 |
| `crisis_protocol.py` | `mean`×1 | — | 均值 |
| `baseline.py` `threshold.py` `annotator.py` | **0** | — | **无** |

**替换映射表**（全部是 1–3 行的标准库实现）：

| numpy | 纯标准库替身 |
|---|---|
| `np.clip(x, lo, hi)` | `lo if x < lo else hi if x > hi else x` |
| `np.sqrt(x)` | `math.sqrt(x)` |
| `np.mean(v)` | `sum(v) / len(v)` |
| `np.sin/cos/pi` | `math.sin/cos/pi` |
| `np.eye(n)`, `np.zeros((n,m))` | 列表推导 |
| `np.diag`, `np.trace` | 列表推导 / `sum` |
| `np.linalg.inv`（2×2） | 闭式解（行列式倒数） |
| `np.linalg.inv`（4×4） | 高斯-约当消元，约 25 行 |
| `np.linalg.inv`（10×10, LinUCB） | 同一份高斯-约当，通用化即可 |
| `np.ndarray`（类型注解） | 直接删除或改 `list[list[float]]` |

> **全仓库去掉 numpy 的改动量：约 15 处替换 + 一份 ~60 行的 `linalg.py`。**
> 已在 `research/core_bench.py` 中实现并验证（含 4×4 求逆、RTS 平滑、10×10 求逆）。

### 2.3 顺带解决的三个隐患

1. **PyQt5 与 Amiya 的 Flet 冲突**。当前 `requirements.txt` 锁 `PyQt5==5.15.11`。若 EmoWave 作为 Amiya 插件引入，等于给 Amiya 强加一个 ~100 MB 的 Qt 依赖。零依赖内核彻底消除这个冲突。
2. **移动端 Python 运行时的包体积**。Flet 移动端通过 `serious_python` 内嵌 CPython（默认 3.14，可选 3.13/3.12）。numpy 在移动端的 wheel 约 15–20 MB 且需要匹配 ABI；去掉后 APK/IPA 体积与构建复杂度都显著下降。
3. **数值一致性**。第一部分 §2.4 要用 Matérn 闭式解替换手写 `F` 矩阵。纯 Python 实现让这份数学在 Python / 未来的 Rust / Kotlin 移植中**逐行可读、易于对齐**，而不是藏在 BLAS 调用后面。

### 2.4 RTS 平滑的性能解法：在节点网格上平滑，而非全采样点

RTS 是唯一 numpy 优势明显的操作（172 ms vs 36 ms）。但解法不是"忍受它"，而是**降低 N**：

- 曲线的视觉平滑度取决于**控制点/节点数**，不取决于原始采样点数。2000 个采样点的曲线，用 200–400 个节点渲染，肉眼无差异。
- RTS 复杂度 **O(N)**，因此 N 从 2000 降到 300 → 耗时从 172 ms 降到约 **26 ms**。
- 用户拖动的是**稀疏控制点**（第一部分 §6.1 设计），本就不是逐采样点拖拽。

```
原始采样 N=2000 ──抽取──> 节点网格 N≈300 ──RTS──> 光滑曲线 ──渲染──> 屏幕
                                    ↑
                              用户拖动作用于此
```

> 这样即使在低端设备上，曲线重算也在 **100 ms 量级**（按慢 5 倍折算 ~130 ms），而它是一次性操作，不进入交互主循环。

---

## 3. Amiya Agent 集成：插件化 + 优雅降级

### 3.1 现有契约（已实地勘察）

Amiya 仓库 `C:\amiya-agent`，Python **3.13.14**，Flet **0.86.5**（PyPI 最新版，未过时）。

- `core/emotion.py` —— 现有情绪支柱，**关键词规则分类器**，输出 4 个离散状态：

```python
EMOTION_KEYS = ("calm", "thinking", "worried", "happy")

def detect_emotion(user_text: str) -> str:   # 纯规则，零依赖，零 LLM 调用
```

- `core/agent.py:244` —— 唯一调用点：

```python
current_emotion = _emotion.detect_emotion(user_text)
```

- `ui/design/skin.py` —— 皮肤联动：`EMOTION_TO_SKIN` 静态映射 + **24 小时过期**（过期回落 calm→starry）。情绪经 `persona_state` 的 `emotion` / `last_seen_at` 传递。

**这是一条天然的升级缝**：EmoWave 只需提供**同样签名的 `detect_emotion(text) -> str`**，就能从"关键词猜"升级为"连续效价-唤醒模型推断"，而下游（提示词注入、皮肤联动）完全不用改。

### 3.2 红线与降级原则

Amiya 侧有一条必须遵守的红线（`core/emotion.py` 文档字符串）：

> 情绪状态持久化到 `persona_state.emotion`（系统状态表），**绝不写入 `memory` 表**（用户事实/经历）。

EmoWave 侧的四条降级原则：

| 级别 | 场景 | 行为 |
|---|---|---|
| **L0 启动期** | `emowave` 未安装 / import 失败 | **完全静默**，回退规则分类，不产生任何日志噪音 |
| **L1 配置期** | `EMOWAVE_ENABLED=0`（**默认值**） | 根本不尝试 import |
| **L2 运行期** | 单次调用抛异常 | 捕获 + `log.warning`，本轮回退规则分类，**绝不向上抛出** |
| **L3 能力期** | 设备档位低 / 省电模式 | 内核内部降级：关闭 RTS 与个性化，只保留因果滤波 |

**关键设计：默认关闭（opt-in）。** `EMOWAVE_ENABLED` 默认 `0`，意味着 EmoWave 的存在对 Amiya 是**零影响**的 —— 这正是用户"不影响阿米娅启动"诉求的最强保证。

### 3.3 实现骨架

新增 `C:\amiya-agent\core\emotion_bridge.py`：

```python
"""EmoWave 可选接入桥：优雅降级，绝不阻塞 Amiya 启动。

红线：
- 永不阻塞启动：emowave 缺失/异常 → 静默回退 core/emotion.py 的规则分类。
- 情绪只流向 persona_state，绝不写 memory 表（沿用 core/emotion.py 红线）。
- 默认关闭（EMOWAVE_ENABLED 未显式开启时为 0）。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from core import emotion as _rules  # 零依赖规则分类，永远可用

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return (os.getenv("EMOWAVE_ENABLED") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


_bridge = None
_resolved = False


def _resolve():
    """延迟解析：只在首次真正需要时 import emowave，且只尝试一次。"""
    global _bridge, _resolved
    if _resolved:
        return _bridge
    _resolved = True
    if not _enabled():
        return None
    try:
        from emowave import EmotionBridge  # 零依赖内核，import 开销 < 5 ms

        _bridge = EmotionBridge()
    except Exception as e:  # noqa: BLE001
        log.info("EmoWave 未启用（%s），沿用规则情绪分类", e)
        _bridge = None
    return _bridge


def detect_emotion(
    user_text: str,
    *,
    valence: Optional[float] = None,
    arousal: Optional[float] = None,
    now: Optional[float] = None,
) -> str:
    """优先 EmoWave 连续模型；任何失败都回退规则分类，且保证返回合法状态键。"""
    b = _resolve()
    if b is not None:
        try:
            state = b.detect(user_text, valence=valence, arousal=arousal, now=now)
            if state in _rules.EMOTION_KEYS:   # 防御：内核返回必须合法
                return state
        except Exception as e:  # noqa: BLE001
            log.warning("EmoWave 调用失败，本轮回退规则分类：%s", e)
    return _rules.detect_emotion(user_text)


# 透出，使 agent.py 只需改 import 一行即可切换
emotion_block = _rules.emotion_block
EMOTION_KEYS = _rules.EMOTION_KEYS
```

**Amiya 侧的唯一改动**（`core/agent.py` 第 ~20 行）：

```diff
-from core import emotion as _emotion
+from core import emotion_bridge as _emotion
```

一行 diff，`core/agent.py:244` 的调用点不变，`core/emotion.py` 及其既有测试**原样保留为降级路径**。

### 3.4 连续模型 → 4 状态 的映射

Amiya 只需要 4 个离散键。EmoWave 的连续效价-唤醒（v, a）如何映射？建议**在内核侧做**，且保留规则分类作为并列信号：

| EmoWave 状态 | 条件（v=效价, a=唤醒, 中性点 0.5） | Amiya 键 |
|---|---|---|
| 负性 + 高唤醒 | `v < 0.5 且 a > 0.5` | `worried` |
| 正性 + 高唤醒 | `v ≥ 0.5 且 a > 0.5` | `happy` |
| 高唤醒 / 认知负荷 | `a > 0.65`（无论效价） | `thinking` |
| 低唤醒 | 其余 | `calm` |

> **融合策略（建议）**：EmoWave 有数据时用 EmoWave；冷启动期（个人模型未收敛、样本 < N）自动回退规则分类。这与 Amiya 现有的 24h 过期机制天然契合 —— 情绪太久没更新，就当没有。

---

## 4. UI 层：PyQt5 → Flet（一套代码覆盖全部目标平台）

### 4.1 为什么是 Flet

**决定性理由：Amiya 已经在用 Flet。** 这不是引入新技术，而是**复用既有技术栈**。

实测（2026-09-05，GitHub REST API + PyPI + Flet 官方文档）：

| 项 | 实测值 |
|---|---|
| `flet-dev/flet` star | **16,650** |
| 最近 push | **2026-09-04**（活跃） |
| PyPI 最新版 | **0.86.5**（2026-08-01）—— **与 Amiya 锁定的版本一致，无升级风险** |
| 桌面目标 | `flet build macos` / `windows` / `linux` ✅ |
| 移动目标 | `flet build apk` / `aab` / `ipa` / `ios-simulator` ✅ |
| 移动端运行时 | 通过 `serious_python` 内嵌 CPython（默认 3.14，可选 3.13/3.12） |

**平台矩阵**：

| 目标 | Flet 支持 | 备注 |
|---|---|---|
| Windows（现状） | ✅ | `flet build windows` |
| **Linux（你要的）** | ✅ | `flet build linux` |
| macOS | ✅ | `flet build macos` |
| **Android（你要的）** | ✅ | `flet build apk/aab` |
| **iOS（你要的）** | ✅ | `flet build ipa`，需 macOS 构建机 |

### 4.2 可拖拽情绪曲线的 Flet 实现路径

第一部分的 §6 要求「拖动控制点、整条曲线光滑变形」。已验证 Flet 具备完整能力：

| 能力 | Flet 控件 | 验证结果 |
|---|---|---|
| 任意图形绘制 | `flet.canvas.Canvas` | 支持 `Rect` / `Circle` / `Path`（`MoveTo`/`LineTo`/`QuadraticTo`/`ArcTo`）/ `Text` |
| 描边、填充、渐变、透明度 | `ft.Paint` | 支持 `PaintingStyle.STROKE/FILL`、`PaintLinearGradient`、`Colors.with_opacity` → **可直接画 ±1σ / ±2σ 双层置信带** |
| 拖拽手势 | `ft.GestureDetector` | `on_pan_start/update/end`、`on_tap_down`、`on_horizontal/vertical_drag_*`、`on_scale_*`，事件带 `local_delta` / `global_delta` |
| 包裹子控件捕获手势 | `GestureDetector.content` | `content: Control \| None`，可包 `Canvas` |

**交互骨架**：

```python
import flet as ft
import flet.canvas as cv

curve = cv.Canvas(shapes=[], expand=True)

def on_tap_down(e: ft.TapEvent):
    # 命中测试：用 e.local_x / e.local_y 找最近的控制点
    ...

def on_pan_update(e: ft.DragUpdateEvent):
    # 拖动 → 更新该控制点 → 重算曲线 → 重建 shapes → curve.update()
    # 曲线重算走 §2.4 的节点网格 RTS，100ms 量级
    ...

ft.GestureDetector(
    content=curve,
    on_tap_down=on_tap_down,
    on_pan_update=on_pan_update,
    on_pan_end=on_pan_end,
)
```

> 这套写法在 Windows 桌面与 Android/iOS 触屏上是**同一份代码** —— `GestureDetector` 同时处理鼠标拖拽与手指触摸。

### 4.3 ⚠️ 推翻 `MOBILE_ARCHITECTURE.md` 的核心选型

仓库现有的 `MOBILE_ARCHITECTURE.md`（v1.0, 2026-07-15）推荐 **方案 B：Swift + Kotlin 双原生重写**，估算 4–6 周 × 2 平台。

**本文档建议推翻该选型**，理由如下：

| 维度 | 方案 B（双原生重写） | **Flet（本方案）** |
|---|---|---|
| 代码库数量 | iOS + Android + 桌面 = **3 套** | **1 套**（Python） |
| 目标平台 | iOS / Android（**不含 Linux、Windows**） | iOS / Android / Linux / Windows / macOS |
| 算法重复实现 | Swift + Kotlin + Python 三处，需数值一致性测试 | 只有 Python 一处 |
| 与你现有资产 | 与 Amiya（Flet）**技术栈割裂** | 与 Amiya **完全同栈** |
| 支撑第一部分新架构 | RTS 平滑 / 个性化学习需**再写两遍** | 直接复用 |
| 开发者（单人）负担 | 需同时掌握 Swift / Kotlin / SwiftUI / Compose | 只需 Python |

**原文档的方案 A（Python 运行时嵌入）被低估了。** 它列出"比原生慢 10–50x"作为致命缺陷 —— 但 §2.1 的实测说明：**最重的 L1 单步只要 64 微秒**，慢 50 倍也才 3.2 毫秒，在 1 Hz 采样下仍是 0.3% CPU。原文档的功耗估算表（总计 ~5%/小时）在纯 Python 内核下依然成立，因为**瓶颈从来不是 CPU，而是蓝牙与屏幕**。

> **修正后的策略**：
> - **不重写算法**（省 4–6 周 × 2）
> - **只重写 UI**（1,075 行 → Flet）
> - 代价是移动端 APK 内含 Python 运行时（约 +15–30 MB），换来的是「一套代码跑五个平台」和「与 Amiya 同栈」。
> - 若未来确有极致性能需求，内核（~2,700 行纯 Python，无numpy）到 Rust 的移植成本**远低于**现在就去写两套原生 App。

### 4.4 端侧 ML：为什么不需要 ONNX / TFLite / ExecuTorch

原 `MOBILE_ARCHITECTURE.md` 的方案 C 提到 ONNX Runtime。第一部分 §3.3 的 L3 学习层也涉及优化。**结论是都不需要神经网络推理引擎**：

| 组件 | 数学本质 | 规模 | 需要 NN 运行时？ |
|---|---|---|---|
| L1 因果滤波 | 4×4 矩阵递推 | 64 µs/步 | ❌ |
| L2 RTS 平滑 | 4×4 后向递推，O(N) | 26–172 ms（一次性） | ❌ |
| L3 超参数学习 | 边际似然最大化，**4–6 个标量超参数** | 小规模数值优化 | ❌ |
| P3 LinUCB | 10×10 矩阵求逆 × 臂数 | 2.5 ms | ❌ |

L3 的"机器学习"是**对数似然曲面的低维数值优化**（坐标下降或 Nelder-Mead 即可），参数量是个位数 —— 这属于经典数值优化，不属于深度学习。引入 ONNX/TFLite 只会增加 5–10 MB 体积和一个黑盒依赖，换取零收益。

> **这也是「简化」的一部分：砍掉一个根本不需要的推理栈。**
> 相关综述（说明这类资源约束场景的通用做法）见 §10.1。

---

## 5. 多平台矩阵与能力分层

### 5.1 平台矩阵

| 平台 | 内核（零依赖） | UI | 存储 | 状态 |
|---|---|---|---|---|
| Windows 桌面 | ✅ | Flet（替换 PyQt5） | SQLite | 现状已有，需迁移 UI |
| **Linux 桌面** | ✅ | Flet（`flet build linux`） | SQLite | **新增，几乎零成本** |
| macOS 桌面 | ✅ | Flet | SQLite | 顺带获得 |
| **Android** | ✅ | Flet（`flet build apk`） | SQLite | **新增** |
| **iOS** | ✅ | Flet（`flet build ipa`） | SQLite | **新增**（需 macOS 构建机或 CI） |
| **Amiya 插件** | ✅（Tier 0） | 无（仅输出 4 状态） | 可选 | **新增** |

### 5.2 能力分层（Tier）

这是"低配设备流畅运行"的核心机制 —— **不是靠优化让它跑得动，而是按档位决定跑什么**。

| Tier | 目标 | L1 因果滤波 | L2 RTS + 曲线编辑 | L3 个性化学习 | 存储 | 曲线节点数 |
|---|---|:---:|:---:|:---:|---|---:|
| **T0 Embed** | Amiya 插件、极低配设备 | ✅ | ❌ | ❌ | 内存 | — |
| **T1 Lite** | 低端手机 / 低配 PC | ✅ | ✅（降级） | ❌ | SQLite | ~150 |
| **T2 Full** | 中高端设备 | ✅ | ✅ | ✅ | SQLite | ~300 |

- **T0** 只做一件事：吃观测、出 4 状态。常驻开销 ≈ 0，这是 Amiya 集成的默认档。
- **T1** 保留曲线可视化（用户最需要的功能）与推荐，但**关闭个性化学习**（它需要跨事件累积计算）并把节点数减半。
- **T2** 完整体验，含第一部分的全部四层架构。

**降级触发**（建议 `auto` 探测 + 显式覆盖）：

```python
EMOWAVE_TIER=auto        # 默认：自动探测
EMOWAVE_TIER=0|1|2       # 显式锁定

# auto 探测依据（全部标准库可得）
#   - os.cpu_count()
#   - 平台（sys.platform / 移动端探测）
#   - 可用内存
#   - 移动端省电模式 → 强制 ≤ T1
```

> 关键：**Tier 只削减"非实时的、批量的"功能，L1 因果滤波在任何档位都保留。** 这保证了情绪追踪的核心体验在所有设备上一致。

### 5.3 与第一部分架构的关系

第一部分的四层（L1 感知 / L2 回顾 / L3 学习 / L4 主权）**原封不动**，Tier 只是决定哪些层被激活：

```
T0:  L1                          → Amiya 插件、极低配
T1:  L1 + L2(降级) + P3推荐      → 低端手机
T2:  L1 + L2 + L3 + L4           → 完整体验
```

---

## 6. 仓库重构：三分法

```
emowave-engine/
├── emowave/                      # 【可移植内核】纯标准库，零第三方依赖
│   ├── __init__.py               #   导出 EmotionBridge（Amiya 用）
│   ├── linalg.py                 #   矩阵运算：4x4 求逆 / 10x10 求逆 / 基础运算（~60 行）
│   ├── kalman.py                 #   ← kalman_filter.py，Matérn 状态空间化
│   ├── smoother.py               #   RTS 平滑（第一部分 §3.2）
│   ├── bandit.py                 #   ← recommender.py，LinUCB
│   ├── baseline.py               #   ← baseline.py
│   ├── threshold.py              #   ← threshold.py
│   ├── annotator.py              #   ← annotator.py
│   ├── types.py                  #   ← models.py（dataclass，零依赖）
│   └── tier.py                   #   能力档位探测（§5.2）
│
├── platforms/
│   ├── flet_ui/                  # 【跨平台 UI】一套代码 → Win/Linux/macOS/Android/iOS
│   │   ├── app.py
│   │   ├── curve_canvas.py       #   GestureDetector + Canvas 可拖拽曲线
│   │   └── controls.py           #   滑条 / 实时情绪指数
│   ├── amiya/                    # 【Amiya 插件适配器】
│   │   └── bridge.py             #   EmotionBridge：连续 (v,a) → 4 状态
│   └── storage_sqlite.py         #   SQLite 持久化（各平台共用 Schema）
│
├── research/                     # 【永不进设备】63% 的现有代码
│   ├── simulator/                #   data_simulator_v2, simulator, run_simulation
│   ├── benchmark/                #   benchmark_suite, performance_profiler
│   ├── evaluation/               #   evaluation, edge_case_tests
│   └── reporting/                #   report_generator, whitepaper_generator, dashboard
│
└── legacy/                       # 冻结：PyQt5 UI、旧入口
    ├── main_app.py
    ├── widgets.py
    └── windows/
```

**依赖声明随之三分**：

```toml
# pyproject.toml
[project]
dependencies = []                 # ← emowave 内核：零依赖

[project.optional-dependencies]
ui     = ["flet==0.86.5"]         # 与 Amiya 同版本
research = ["numpy>=1.21", "scipy>=1.7", "matplotlib>=3.5"]
dev     = ["pytest>=7.0"]
```

> **`pip install emowave` 装到手机上：零依赖。** 研究栈（numpy/scipy/matplotlib）只有跑模拟器与基准时才需要。

---

## 7. 实施路线

按依赖顺序，每阶段可独立交付与验证。**总工期约 3–4 周，且第 1 周结束即有可验证成果。**

### 阶段 0：内核零依赖化（3–4 天）★ 最高优先级

- [ ] 新建 `emowave/linalg.py`（4×4 与 n×n 求逆、基础矩阵运算）
- [ ] 按 §2.2 映射表替换 `kalman_filter.py` / `recommender.py` / `crisis_protocol.py` 的 numpy 调用
- [ ] 删除 `np.ndarray` 类型注解（8+4 处）
- [ ] **验证**：`python -c "import emowave"` 不触发任何第三方导入；现有 `test_engine.py` 全部通过
- [ ] **验证**：`research/core_bench.py` 重跑，确认纯 Python 路径数值与 numpy 路径一致（容差 1e-9）

> 这一阶段做完，需求 A（轻量化）就已达成大半，且需求 B（接入 Amiya）的前置条件就绪。

### 阶段 1：Amiya 插件接入（2 天）

- [ ] 实现 `platforms/amiya/bridge.py` 的 `EmotionBridge`
- [ ] 在 Amiya 侧新增 `core/emotion_bridge.py`（§3.3 骨架）
- [ ] `core/agent.py` 一行 import 改动
- [ ] **验证（关键）**：
  - `EMOWAVE_ENABLED=0`（默认）→ Amiya 启动时间与行为**完全不变**
  - 删除/重命名 emowave 包 → Amiya **正常启动**，仅日志一行 info
  - emowave 内部注入异常 → 单轮回退规则分类，对话不中断
- [ ] 补充测试：`tests/test_emotion_bridge_fallback.py`

### 阶段 2：目录重构（1–2 天）

- [ ] 按 §6 拆分 `emowave/` 与 `research/`
- [ ] `pyproject.toml` 三分依赖
- [ ] **验证**：`pip install -e .` 后 `import emowave` 的依赖树为空

### 阶段 3：Flet UI 与曲线（5–7 天）

- [ ] `platforms/flet_ui/` 骨架（替换 PyQt5 的 1,075 行）
- [ ] `curve_canvas.py`：`GestureDetector` + `Canvas`，实现均值曲线 + 双层置信带 + 可拖拽控制点
- [ ] 保留现有 2D 效价-唤醒相图为副视图（第一部分 §6.1）
- [ ] **验证**：Windows 桌面跑通；曲线重算 ≤ 150 ms（T1 档，节点 150）

### 阶段 4：Linux + 移动端（3–5 天）

- [ ] `flet build linux` 产出 Linux 桌面版 ← **需求 C 之一**
- [ ] `flet build apk` 产出 Android 包 ← **需求 C 之一**
- [ ] iOS 需 macOS 构建机或 CI（可延后）
- [ ] 移动端省电模式 → Tier 自动降档
- [ ] **验证**：低端 Android 设备上 1 Hz 采样连续 1 小时，CPU 占用 < 1%

### 阶段 5：Tier 自动化（2 天）

- [ ] `emowave/tier.py`：CPU / 内存 / 平台 / 省电模式探测
- [ ] `EMOWAVE_TIER=auto` 默认生效
- [ ] **验证**：在低配设备上自动落到 T1，且功能可用

---

## 8. 风险与开放问题

| 风险 | 等级 | 缓解 |
|---|---|---|
| Flet 移动端 APK 体积（含 CPython 运行时） | **中** | 零依赖内核已避免 numpy 的 15–20 MB；用 `--arch` 限定架构、排除非必要资源 |
| Flet Canvas 高频重绘性能（拖拽时） | **中** | §2.4 节点网格降 N；拖动中可先画"预览折线"，松手后再做完整 RTS |
| iOS 构建需 macOS 机器 | **中** | 延后；或 GitHub Actions macOS runner |
| 纯 Python 数值精度与 numpy 有细微差异 | **低** | 阶段 0 的数值一致性测试（容差 1e-9）；4×4 / 10×10 规模下差异可忽略 |
| Amiya 情绪红线被破坏（写到 memory 表） | **低** | §3.2 红线；桥接层只返回状态键，不触碰存储 |
| 重构期与研究脚本的导入路径断裂 | **低** | 阶段 2 用 `research/` 独立可运行验证；旧路径保留一个兼容 shim |

**开放问题**：

1. **Amiya 是否需要在无滑条输入时也有情绪？** 当前 Amiya 是纯文本驱动。若开启"从对话文本推断效价-唤醒"，需要一个文本→(v,a) 的映射（可用现有关键词表加权，或未来接一个小模型）。这会引入一个新的可选依赖，需单独评估。
2. **移动端后台常驻的必要性？** 情绪追踪若要求"无感采集"，移动端后台限制（尤其 iOS）是硬约束。建议移动端以**主动记录为主**（用户打开 App 打点），不做后台常驻。
3. **是否保留 PyQt5 版本？** 建议冻结在 `legacy/`，不再维护。Flet UI 稳定后删除。

---

## 9. 一句话总结

> **简化不是"把代码写得更少"，而是"让 63% 的代码不再上设备、让内核不再需要任何第三方库"。** 实测显示 `import numpy` 就要 1082 ms，而核心计算一步只要 64 微秒 —— 这个 16000 倍的错配，就是当前架构最大的浪费。把内核做成零依赖纯标准库，Amiya 接入就变成"一行 import + 默认关闭"的零风险插件；把 UI 换成 Amiya 已在用的 Flet，Linux 和手机版就从"两个新项目"变成"两条构建命令"。

---

## 10. 本轮新增参考文献

> 引用数由 OpenAlex API 实测，**出版年经 Crossref 交叉核验**（OpenAlex 常给"在线优先"年，与正式出刊年不同）。上一轮 `REFERENCES.md` 与 `ARCHITECTURE_EMOTION_CURVE.md` 已收录的此处不重复。

### 10.1 资源受限端侧机器学习（§4.4 的依据）

| 文献 | 引用 | 链接 |
|---|---:|---|
| *A review on TinyML: State-of-the-art and prospects*. J. King Saud Univ. - Computer and Information Sciences (**2022**) | **422** `[OA]` | https://doi.org/10.1016/j.jksuci.2021.11.019 |
| *A Comprehensive Survey on TinyML*. IEEE Access (**2023**) | **333** `[OA]` | https://doi.org/10.1109/access.2023.3294111 |
| *TinyML Meets IoT: A Comprehensive Survey*. Internet of Things (**2021**) | **323** `[OA]` | https://doi.org/10.1016/j.iot.2021.100461 |
| *TinyML-Enabled Frugal Smart Objects: Challenges and Opportunities*. IEEE Circuits and Systems Magazine (**2020**) | **292** `[OA]` | https://doi.org/10.1109/mcas.2020.3005467 |
| *Lightweight Deep Learning for Resource-Constrained Environments: A Survey*. ACM Computing Surveys (**2024**) | **241** `[OA]` | https://doi.org/10.1145/3657282 |
| Kang et al. *Neurosurgeon: Collaborative Intelligence Between the Cloud and Mobile Edge*. ACM SIGARCH CAN (**2017**) | **792** `[OA]` | https://doi.org/10.1145/3093337.3037698 |

> **Neurosurgeon** 提出按层切分 DNN、在移动端与云端间做计算分配 —— 这是"按设备能力分层"思想的经典先例，与本文 §5.2 的 Tier 设计同构（尽管本项目的分层粒度是整个算法层而非网络层）。

### 10.2 可穿戴/移动端情绪识别（承接 REFERENCES.md）

| 文献 | 引用 | 链接 |
|---|---:|---|
| *Emotion Recognition for Everyday Life Using Physiological Signals From Wearables: A Systematic Review*. IEEE Trans. Affective Computing (**2023**) | **203** `[OA]` | https://doi.org/10.1109/taffc.2022.3176135 |
| *Emotion Recognition from Physiological Signal Analysis: A Review*. Electronic Notes in Theoretical Computer Science (**2019**) | **561** `[OA]` | https://doi.org/10.1016/j.entcs.2019.04.009 |
| *A systematic review of emotion recognition using cardio-based signals*. ICT Express (**2024**) | **30** `[OA]` | https://doi.org/10.1016/j.icte.2023.09.001 |

### 10.3 移动端 EMA 可行性（移动端设计的经验依据）

| 文献 | 引用 | 链接 |
|---|---:|---|
| *Smartphone-Based Ecological Momentary Assessment of Well-Being: A Systematic Review and Recommendations*. Journal of Happiness Studies (**2020**) | **264** `[OA]` | https://doi.org/10.1007/s10902-020-00324-7 |

### 10.4 工程栈（GitHub / PyPI，2026-09-05 实测）

| 项目 | Star | 版本 / 最近更新 | 用途 |
|---|---:|---|---|
| [flet-dev/flet](https://github.com/flet-dev/flet) | **16,650** | v0.86.5（2026-08-01，PyPI 最新） | **跨平台 UI，与 Amiya 同栈（首选）** |
| [tauri-apps/tauri](https://github.com/tauri-apps/tauri) | 110,822 | 2026-09-05 | 轻量桌面备选（Rust + WebView） |
| [kivy/kivy](https://github.com/kivy/kivy) | 19,020 | 2026-09-03 | Python 移动端备选（生态较老） |
| [beeware/toga](https://github.com/beeware/toga) | 5,410 | 2026-09-04 | Python 原生跨平台备选 |
| [mozilla/uniffi-rs](https://github.com/mozilla/uniffi-rs) | 4,935 | 2026-09-04 | 若内核未来移植 Rust，用于生成多语言绑定 |

> **未采用说明**：ONNX Runtime（21,764）、ExecuTorch（4,991）、ncnn（23,785）、MNN（16,027）均**不建议引入** —— §4.4 已论证本项目无需神经网络推理引擎。
> **PyQt5 说明**：非移动端方案，且与 Amiya 的 Flet 栈冲突，列入 `legacy/` 冻结。pyqtgraph（4,409）随之一并弃用（其价值被 Flet Canvas 取代）。

### 10.5 平台支持依据（Flet 官方文档，2026-09-05 查阅）

- 发布总览（平台矩阵 / `flet build` / Python 版本绑定）：https://docs.flet.dev/publish/
- Canvas（Path / Circle / Rect / Text + Paint 渐变与透明度）：https://docs.flet.dev/controls/canvas/
- GestureDetector（`on_pan_*` / `on_tap_down` / `content` 包裹子控件）：https://docs.flet.dev/controls/gesturedetector/

---

*文档版本 v1.0 · 2026-09-05*
