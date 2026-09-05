# 心潮 EmoWave · 个人情绪状态引擎

> **EmoWave / 心潮 — Personal Emotion State Engine**
>
> EmoWave 不替你定义"你现在是什么情绪"，而是维护一条可观察、可编辑、可校准、可学习的**个人情绪状态曲线**，并在长期数据积累后逐渐形成属于你自己的情绪动态模型。

[![tests](https://img.shields.io/badge/tests-594_passed-brightgreen)]() [![deps](https://img.shields.io/badge/runtime_deps-zero-blue)]() [![python](https://img.shields.io/badge/python-3.9%2B-blue)]()

---

## 一句话原则

> **EmoWave 不替用户定义情绪，而是帮助用户建立一个越来越理解自己的情绪模型。**
>
> **任何可选能力都必须能够失效而不摧毁核心系统。**

---

## 2.0 重构概览

本仓库正在从"情绪事件追踪桌面应用"（1.x，PyQt5 + numpy）升级为"个人情绪状态建模引擎"（2.0，零依赖可移植内核）。重构在 `refactor/v2-core` 分支进行，1.x 稳定版锚定在 tag `v1.0-stable`。

完整重构计划见 `EMOWAVE_REFACTOR_PLAN.md`，架构设计见 `ARCHITECTURE_EMOTIONpart1.md`（曲线建模与人机协同编辑）与 `ARCHITECTURE_LIGHTWEIGHTpart2.md`（轻量化内核 / Amiya 集成 / 多平台）。**逐步执行日志见 [`REFACTOR_PROGRESS.md`](REFACTOR_PROGRESS.md)**（每阶段做了什么、怎么做的、验证结果、下一步）。

核心闭环：

```
现实世界 → 观测数据 → 模型估计 → 用户查看 → 用户修正 → 模型学习 → 下一次估计更贴近用户 → 循环
```

---

## 新内核架构（emowave/，零第三方依赖）

```
emowave/
├── core/
│   ├── domain/          核心领域模型（全部 frozen dataclass，原始数据不可变）
│   │   ├── observation.py        Observation：系统观察到的事实（append-only）
│   │   ├── emotion_state.py      EmotionState：模型估计 + confidence/variance/trend
│   │   ├── correction.py         UserCorrection：用户纠正 + 峰终加权
│   │   ├── baseline.py           Baseline：可重定义的"正常状态" + 版本链
│   │   ├── model_parameters.py   ModelParameters：个人超参（Matérn ℓ/σ）+ 层次收缩
│   │   └── events.py             StateEvent/EventStream：事件流（可解释性）
│   ├── estimator/       L1 感知层：Matérn ν=3/2 状态空间 Kalman（因果，O(1)）
│   ├── curve/           L2 回顾层：RTS 平滑（非因果，O(N)）+ 可编辑曲线
│   ├── calibration/     L3 学习层：在线岭回归 + 个人超参学习 + L4 基线主权
│   ├── inference/       个人动态模型 + 短期趋势预测（含 uncertainty）
│   ├── protocol/        协议与版本 schema（Envelope / Amiya 输入输出）
│   ├── linalg.py        纯标准库矩阵运算（n×n 求逆，替换 numpy）
│   └── tier.py          能力档位探测（T0/T1/T2，低配优雅降级）
├── adapters/
│   ├── agent/amiya.py   Amiya 集成桥（四级优雅降级）
│   └── storage/sqlite.py 七表 schema 版本化 + append-only 触发器
└── cli/                 命令行入口（demo/detect/curve/version）
```

**Core 硬边界**：Core 不知道 PyQt / Flet / Android / iOS / SQLite 实现细节 / Amiya / LLM / TTS，只认识自己的领域模型和算法。所有跨边界交互通过 `adapters/` 完成。

---

## 四层架构

```
L4 主权层   基线制度（nudge/fork/reset）+ BOCPD 提议→用户裁决→反哺检测器
L3 学习层   边际似然 + 层次先验收缩 → 个人超参数；Coactive Learning 有界更新
L2 回顾层   RTS 平滑（非因果全局平滑）→ 均值曲线 + 置信带 + 可拖拽控制点
L1 感知层   因果 Kalman 滤波（Matérn ν=3/2 状态空间），<1ms，O(1) 内存
```

L1 在任何设备档位都保留（核心体验一致）；L2/L3/L4 按 Tier 降级。

---

## 关键算法

- **Matérn ν=3/2 状态空间**（Hartikainen & Särkkä 2010）：Kalman 是计算后端，GP 是数学语义。转移矩阵闭式解 `F(Δt)=e^(-λΔt)[[1+λΔt,Δt],[-λ²Δt,1-λΔt]]`，λ=√3/ℓ。ℓ 即**情绪惯性**（Kuppens 2010），修正了 1.x `velocity_damping=0.85` 隐含的 ℓ≈11-22s "记忆问题"。
- **RTS 平滑**：O(N) 非因果全局平滑，用户拖动一个控制点整条曲线光滑变形（"拉函数图像"的手感）。
- **峰终加权**（Kahneman 1993）：回顾性编辑按 `ω_recency·ω_salience·ω_current_mood` 加权，峰值/结尾的编辑强烈采纳，平淡中段温和采纳。
- **层次先验收缩**（Taylor 2017）：小样本向群体先验收缩防过拟合，证据越多越个性化。
- **Coactive Learning**（Shivaswamy & Joachims 2015）：把拖动当"改进"而非"真值"，单次大幅拖动只产生有界影响。

---

## 快速开始

```bash
# 零运行时依赖（仅标准库），Python 3.9+
python -m emowave.cli version          # 版本 + 协议 schema + 探测档位
python -m emowave.cli demo --points 60 # 合成观察流 → 实时状态估计
python -m emowave.cli detect --text "我好焦虑"   # 文本 → Amiya 4 状态
python -m emowave.cli detect --valence 0.15 --arousal 0.9  # (v,a) → worried
python -m emowave.cli curve --points 60 --nodes 15         # RTS 平滑曲线 + 置信带
```

Python API：

```python
from emowave import Observation, EmotionState, ModelParameters
from emowave.core.estimator.estimator import StateEstimator

est = StateEstimator(params=ModelParameters())
est.initialize(timestamp=1000.0, valence=0.5, arousal=0.5)
for i in range(60):
    state = est.update(Observation(timestamp=1000.0 + i, valence=0.6, arousal=0.4))
print(state.valence, state.arousal, state.intensity, state.confidence, state.trend)
```

---

## 测试

```bash
python -m pytest emowave/tests/ -v   # 564 个内核单元测试
python -m pytest tests/ test_engine.py -v  # 30 个 1.x 桌面应用回归测试
```

共 **594 个测试**，TDD 流程开发（写测试→看失败→最小实现→看通过→重构）。覆盖领域模型、Matérn 估计器、RTS 平滑、可编辑曲线、个人在线学习、基线主权、动态模型、Amiya 降级、SQLite 持久化、CLI。

性能基准（`research/bench_core_performance.py`）：L1 因果滤波单步 0.148ms（1Hz 采样 CPU 占用 0.0148%），RTS 节点网格降采样 6.1× 加速，曲线重建 149ms（T1 档目标 ≤150ms），零 GPU 依赖。

---

## 与 Amiya-Agent 的关系

```
EmoWave = State Engine（"用户现在大概处于什么状态？"）
Amiya   = Companion Agent（"我应该怎样理解、回应和陪伴？"）
```

EmoWave 通过 `EmotionBridge` 作为 Amiya 的**可选外部状态引擎**，不反向依赖 Amiya。四级优雅降级保证：EmoWave without Amiya ✅ / Amiya without EmoWave ✅ / EmoWave + Amiya ✅。Amiya 侧默认 `EMOWAVE_ENABLED=0`（opt-in），存在则升级、缺失则静默回退规则分类。

---

## 数据隐私

情绪数据属高敏感个人状态数据，默认 **Local-first**：原始数据仅本地存储（`~/.emowave/emowave.db`），Amiya 接口只获得结论性状态摘要，默认不把完整历史时间序列发给 LLM，用户拥有导出/删除/重置能力。

---

## 1.x 遗留桌面应用

tag `v1.0-stable` 保留了完整的 1.x PyQt5 桌面应用（今日仪表盘 / 情绪冲浪 / 事件回顾 / 历史记录），三层架构 UI→session→db→engine。重构期间作为 fallback 保留，Flet 跨平台 UI 稳定后将归档至 `legacy/`。运行 1.x：

```bash
pip install -r requirements.txt   # PyQt5 + numpy
python main_app.py
```

---

## 多平台路线（§24）

第一阶段 Python Core + Desktop（**Phase 1-9 已完成**：算法/数据模型/协议/存储/CLI，零依赖可移植）。第二阶段 Flet 跨平台 UI（Desktop/Linux/Android/iOS，需 `flet==0.86.5`，与 Amiya 同栈）。第三阶段可选 Rust Core（Python 作为 Reference Implementation，golden test 保证一致性）。

---

## 参考项目

- EmoWave Engine: https://github.com/x7687315-gif/emowave-engine
- Amiya-Agent: https://github.com/x7687315-gif/Amiya-Agent
