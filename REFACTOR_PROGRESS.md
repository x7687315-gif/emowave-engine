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

### Phase 1 — Core Domain 重构（已完成 ✅）

**目标**：摆脱旧的 Event-centric 设计，建立新版"连续情绪状态建模"的领域模型骨架。完成标准（REFACTOR_PLAN.md §28 Phase 1）：**Core 可以不依赖 UI 独立运行**。

**做了什么**：

新建 `emowave/` 可移植内核包（纯标准库，零第三方依赖），目录结构遵循 REFACTOR_PLAN.md §11 与 §29：

```
emowave/
├── __init__.py                    包入口，统一导出 + 版本号
├── core/
│   ├── __init__.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── observation.py         Observation + ObservationSource
│   │   ├── emotion_state.py       EmotionState + Trend + compute_intensity
│   │   ├── correction.py          UserCorrection + 峰终加权公式
│   │   ├── baseline.py            Baseline + BaselineShiftEvent + select_active_baseline
│   │   ├── model_parameters.py    ModelParameters + LearningStage + Matérn 派生量
│   │   └── events.py              StateEvent + StateEventType + EventStream
│   └── protocol/
│       ├── __init__.py
│       └── schemas.py             Envelope + AmiyaHandshake + EmotionStateOutput + AmiyaInput
└── tests/
    ├── conftest.py
    ├── test_domain_observation.py        15 tests
    ├── test_domain_emotion_state.py      20 tests
    ├── test_domain_correction.py         20 tests
    ├── test_domain_baseline.py           24 tests
    ├── test_domain_model_parameters.py   25 tests
    ├── test_domain_events.py             22 tests
    └── test_protocol_schemas.py          42 tests
```

**怎么完成的（关键设计决策）**：

1. **六个核心领域模型**（REFACTOR_PLAN.md §4）全部落地为 `@dataclass(frozen=True)`：
   - `Observation`：系统观察到的事实，append-only 不可变。所有情绪字段可选，支持纯生理观察。
   - `EmotionState`：模型估计，含 `confidence` + `variance_valence/arousal`（置信带渲染）+ `trend` + `baseline_id`（支持重算历史）。
   - `UserCorrection`：用户纠正，是个人在线学习的监督信号。
   - `Baseline`：可被用户重新定义的"正常状态"，带 `baseline_id`/`version`/`parent_id`/`regime_id` 版本链。
   - `ModelParameters`：个人化超参数（ℓ、σ、σ_noise），含 Matérn ν=3/2 派生量与层次先验收缩。
   - `StateEvent` + `EventStream`：所有状态变化以事件流记录，支撑可解释性。

2. **frozen=True 作为"原始数据不可变"的代码级保证**（REFACTOR_PLAN.md §13 / ARCHITECTURE part1 §3.2.1）：任何试图修改已存 Observation / Correction 的代码都会抛 `FrozenInstanceError`。构造时的归一化用 `object.__setattr__` 写回（dataclass 标准手法）。

3. **修正旧版 intensity 定义 bug**（ARCHITECTURE part1 §1.3）：旧版 `sqrt(v²+a²)/sqrt(2)` 把中性点当成 (0,0)，导致 (v=0,a=0) 的抑郁性迟滞被算成 0 强度。新版 `compute_intensity` 改用 Russell 环状模型的正确中性点 (0.5,0.5)，`(0,0)` 现在正确算出强度 1.0。实现为 `EmotionState.intensity` property 而非存储字段，避免"存了旧值但 v/a 已变"的不一致。

4. **峰终加权公式落地**（ARCHITECTURE part1 §4.2）：`compute_reliability_weight` 实现 `w = ω_source · ω_recency · ω_salience · ω_current_mood · ω_end`，依据 Kahneman 峰终定律（1993）、过程忽视、Faul & LaBar 心境一致性（2023）。`UserCorrection` 构造时若未显式传入权重则自动计算，快速拖动（drag_velocity 大）额外降权。

5. **Matérn ν=3/2 参数化**（ARCHITECTURE part1 §2.4）：`ModelParameters` 默认 ℓ_valence=300s / ℓ_arousal=240s，修正旧版 `velocity_damping=0.85` 隐含的 ℓ≈11-22s "记忆问题"。提供 `lambda_*()` 与 `autocorrelation()` 派生方法，自相关公式 `ρ(τ)=(1+√3τ/ℓ)·exp(-√3τ/ℓ)` 经复算与 part1 §1.2 表格逐行一致。

6. **层次先验收缩**（ARCHITECTURE part1 §3.3，Taylor 2017）：`population_shrinkage_weight()` 按 n_events 分段——n<5 纯群体（1.0），5≤n<20 强收缩（0.8），20≤n<40 线性过渡到 0.2，n≥40 保留 20% 群体锚点防漂移。`LearningStage` 由 n_events 自动推导。

7. **协议与版本 schema**（REFACTOR_PLAN.md §15-§18, §27）：`SCHEMA_VERSION=1` / `PROTOCOL_VERSION=1` / `MODEL_VERSION="2.0.0-alpha.0"`。`Envelope` 通用封装带兼容性检查；`AmiyaHandshake` 支持能力发现（未知能力忽略，前向兼容）；`EmotionStateOutput` 只输出结论性状态（测试验证不泄露 Kalman 参数/数据库字段）；`AmiyaInput.to_observation_dict()` 把 Agent 输入转为 source=agent 的 Observation，不直接覆盖状态。

8. **零依赖验证**（ARCHITECTURE LIGHTWEIGHT part2 §2）：`import emowave` 新增 57 个模块全部为标准库（math/dataclasses/enum/typing/time/uuid/collections），numpy/scipy/PyQt5/flet 等重依赖**零引入**。

**验证结果**：

| 测试集 | 通过 | 失败 | 耗时 | 备注 |
|---|---:|---:|---:|---|
| `emowave/tests/`（Phase 1 新增） | **168** | 0 | 0.21s | ✅ 全部通过 |
| `tests/` + `test_engine.py`（旧套件回归） | 30 | 0 | 0.85s | ✅ 无回归 |

过程中修正了 2 处**测试期望值的算术错误**（非实现 bug）：Matérn 自相关 ρ(60;ℓ=300)=0.952、ρ(300;ℓ=300)=0.483，经独立脚本复算与 ARCHITECTURE part1 §1.2 表格完全一致后修正断言。

**Phase 1 完成标准核对**（REFACTOR_PLAN.md §28）：

- [x] 创建 `Observation`
- [x] 创建 `EmotionState`
- [x] 创建 `UserCorrection`
- [x] 创建 `Baseline`
- [x] 创建 `ModelParameters`
- [x] 创建 `StateEvent`
- [x] 定义 schema version（SCHEMA/PROTOCOL/MODEL 三版本号）
- [x] 定义统一 protocol schema（Envelope + Amiya 输入输出协议）
- [x] **Core 可以不依赖 UI 独立运行**（`import emowave` 零重依赖，168 测试无需 PyQt5）

**下一步（Phase 2）**：实时状态估计。
- 新建 `emowave/core/linalg.py`：纯标准库矩阵运算（4×4 / n×n 求逆、乘法、转置），替换 numpy（LIGHTWEIGHT part2 §2.2 映射表）。
- 新建 `emowave/core/estimator/`：Matérn ν=3/2 状态空间 Kalman 滤波器，用闭式解 `F(Δt)=e^(-λΔt)·[[1+λΔt,Δt],[-λ²Δt,1-λΔt]]` 取代旧版手写 F 矩阵与 velocity_damping（part1 §2.4）。
- 实现 `Observation → Estimator → EmotionState` 闭环，输出含 confidence / variance / trend。
- 验证：新参数化在旧 `test_data/` 上 RMSE 不劣于旧实现（part1 §8 阶段 1）。

---
