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

### Phase 2 — 实时状态估计（已完成 ✅）

**目标**：重构 Kalman / state estimator，实现 `Observation → Estimator → EmotionState` 闭环。完成标准（REFACTOR_PLAN.md §28 Phase 2）：**输入连续 Observation，可以持续输出 EmotionState**。

**做了什么**：

```
emowave/core/
├── linalg.py                  纯标准库矩阵运算（零依赖，替换 numpy）
└── estimator/
    ├── __init__.py
    ├── matern.py              Matérn ν=3/2 状态空间闭式解
    └── estimator.py           StateEstimator + EstimatorConfig + compute_observation_noise

emowave/tests/
├── test_linalg.py             33 tests
├── test_estimator_matern.py   24 tests
└── test_estimator.py          43 tests

research/
└── bench_matern_vs_legacy.py  新旧估计器 RMSE 对照基准（永不进设备）
```

**怎么完成的（关键设计决策）**：

1. **零依赖 linalg**（LIGHTWEIGHT part2 §2.2 映射表）：用 `list[list[float]]` 实现矩阵加/减/数乘/乘法/矩阵-向量乘/转置/迹/对角阵，以及 **n×n 求逆（高斯-约当消元 + 部分主元选取）**。2×2 走闭式解（Kalman 新息协方差 S 常为 2×2，更快更稳）。验证 4×4（Kalman）与 10×10（LinUCB）求逆 `A·A⁻¹=I` 误差 < 1e-8，奇异矩阵抛 ValueError，部分主元处理对角元接近零的病态情况。

2. **Matérn ν=3/2 状态空间闭式解**（part1 §2.4，Hartikainen & Särkkä 2010）：
   - 转移矩阵 `F(Δt)=e^(-λΔt)·[[1+λΔt, Δt], [-λ²Δt, 1-λΔt]]`，λ=√3/ℓ
   - 稳态协方差 `P∞=σ²·[[1,0],[0,λ²]]`
   - 过程噪声 `Q(Δt)=P∞-F·P∞·Fᵀ`（自洽性：Δt→0 时 Q→0，Δt→∞ 时 Q→P∞）
   - 双通道 4×4 块对角（状态排序 `[v, v̇, a, ȧ]`，part1 §2.3 与现有向量同构）
   - **数学验证**：测试用数值微分确认 `Δt→0 时 F→I` 且 `dF/dΔt|₀=A=[[0,1],[-λ²,-2λ]]`（part1 §2.4 声称的两条性质）

3. **取代旧版手写 F 与 velocity_damping**：旧版 `F[0][2]=dt`（匀速运动）+ `velocity_damping=0.85` 魔法常数被 Matérn 闭式解取代。阻尼已内含在 `e^(-λΔt)` 中，一个有心理学含义的参数 ℓ（情绪惯性，Kuppens 2010）取代了一个拍脑袋常数。测试 `test_estimator_uses_matern_not_handwritten_F` 验证 `_predict` 后状态符合 Matérn 而非匀速模型。

4. **保留旧版两个好设计**（part1 §3.1 明确"不要丢"）：
   - **自适应观测噪声** `compute_observation_noise`：滑条交互质量 → 噪声（快速拖动噪声低、静止后跳变噪声高）。重新设计为**相对因子乘以学习到的 sigma_noise**（个人化基线），而非旧版的绝对值取 max——这样 sigma_noise 可被 Phase 4 校准学习，交互质量作为相对调制。
   - **生理控制输入**：HR/HRV 作为唤醒速度先验，`control_arousal = w_hrv·hrv_drop + w_hr·hr_change/100`，signal_quality<0.3 时门控归零。支持从 meta 显式提供或从 hr/hrv 与 baseline 自动推导。

5. **新增完整协方差输出**（part1 §3.1）：`EmotionState.variance_valence/arousal` 取自 P 对角元，供 L2 回顾层与置信带渲染。`covariance` property 返回完整 4×4（拷贝），不只是旧版的 trace。

6. **派生量计算**：
   - `confidence = 1 - exp(-reduction/confidence_scale)`，reduction = 后验位置方差相对平稳方差的缩减（未观测→0，多次观测→1），带 min_confidence 下限避免冷启动 UI 全虚化
   - `stability = exp(-speed/stability_scale)`，speed=√(v̇²+ȧ²)（速度越大越不稳定）
   - `trend`：强度变化率 `intensity_dot=((v-0.5)v̇+(a-0.5)ȧ)/(√0.5·dist)` 的符号 → RISING/FALLING/STABLE，中性点附近返回 UNKNOWN
   - `intensity`：由 EmotionState property 按 Russell 环状模型计算（Phase 1 已修正旧 bug）

7. **防御性设计**：乱序观察（时间戳倒退）抛 ValueError；长时间无观察 Δt 钳到 600s 上限避免外推发散；部分观察（只有 valence 或只有 arousal）构造对应降维 H/R；纯生理观察只走预测+控制输入不做观测更新；extrapolate 先保存再恢复内部状态（外推不污染滤波器）。

**验证结果**：

| 测试集 | 通过 | 失败 | 耗时 |
|---|---:|---:|---:|
| `emowave/tests/`（Phase 1+2 累计） | **257** | 0 | 0.38s |
| 其中 Phase 2 新增（linalg+matern+estimator） | 100 | 0 | — |
| 旧套件 `tests/`+`test_engine.py`（回归） | 30 | 0 | 0.81s |

**RMSE 对照基准**（`research/bench_matern_vs_legacy.py`，part1 §8 阶段 1 验证要求）：

合成 Matérn ν=3/2 轨迹（ℓ=300s，600 步 1Hz）作 ground truth，加 σ=0.08 观测噪声 + 30% 随机 gap，新旧估计器在完全相同观测上对照，5 个随机种子平均：

| 指标 | 旧版 Kalman | 新版 Matérn | 结论 |
|---|---:|---:|---|
| 全局 RMSE | 0.0645 | **0.0530** | ✅ 新版更优（不劣于旧版） |
| gap 区域 RMSE | 0.0759 | **0.0552** | ✅ 新版改善 **27.2%** |

gap 区域的显著改善直接验证了 part1 §1.2 "记忆问题"的修复：旧版 ℓ≈11-22s 在观测缺失时无法维持状态，新版 ℓ=300s 凭惯性平滑外推。

**过程中修正的 3 处问题**（均为测试期望/设计问题，非实现 bug）：
- `compute_observation_noise` 设计缺陷：`max(sigma, sigma_noise)` 地板使交互因子失效 → 重设计为相对因子乘以 sigma_noise
- `test_trend_falling`：arousal 在末段已 plateau 到中性点，trend 反映平台期而非下降段 → 改为末步仍在下降的斜坡
- `test_longer_ell`：原断言"长 ℓ 对尖峰响应更弱"依赖速度-位置协方差耦合（经数值诊断确认是 Matérn 临界阻尼均值回复的真实行为，非 bug）→ 改为检验 ℓ 的核心语义"情绪惯性"（外推时速度按 e^(-λΔt) 衰减，长 ℓ 漂移持续更久）

**Phase 2 完成标准核对**（REFACTOR_PLAN.md §28）：

- [x] 重构 Kalman / state estimator（Matérn 状态空间化）
- [x] 实现统一输入 Observation
- [x] 输出 EmotionState
- [x] 增加 confidence
- [x] 增加 uncertainty（variance_valence/arousal + 完整协方差）
- [x] 增加 trend（rising/falling/stable/unknown）
- [x] 增加实时 event stream（Phase 1 已建 EventStream，估计器输出可挂接）
- [x] **输入连续 Observation，可以持续输出 EmotionState**（闭环测试验证）

**下一步（Phase 3）**：情绪曲线（可编辑曲线是新版第一核心交互，part1 §3.2 L2 回顾层）。
- 新建 `emowave/core/curve/smoother.py`：RTS（Rauch-Tung-Striebel）平滑器，O(N) 非因果全局平滑，产出均值曲线 μ(t) + 置信带 σ(t)。
- 新建 `emowave/core/curve/curve.py`：EmotionCurve 数据结构 + 节点网格降采样（part2 §2.4，N=2000→300 使纯 Python RTS 从 172ms 降到 ~26ms）。
- 三条曲线：Raw Observation / Model Estimate / User Corrected State（REFACTOR_PLAN.md §6.3）。
- 控制点 + 拖拽语义（CurveEdit append-only，峰终加权，part1 §3.2.1 / §4.2）。
- 验证：平滑后曲线 RMSE 优于滤波（平滑利用未来信息必然更优），注入合成用户编辑确认模型不发散。

---

### Phase 3 — 情绪曲线 / L2 回顾层（已完成 ✅）

**目标**：实现可编辑情绪曲线——新版第一核心交互。完成标准（REFACTOR_PLAN.md §28 Phase 3）：**用户可以直接通过曲线修改当前情绪状态**。

**做了什么**：

```
emowave/core/curve/
├── __init__.py
├── smoother.py     RTSSmoother + SmoothedTrajectory（O(N) 非因果全局平滑）
└── curve.py        EmotionCurve + CurveEdit + CurveEditor + build_node_grid

emowave/tests/
├── test_curve_smoother.py   24 tests
└── test_curve.py            30 tests
```

**怎么完成的（关键设计决策）**：

1. **RTS 平滑器解决"因果 vs 非因果"架构冲突**（part1 §0 摘要的核心论断）：旧版 Kalman 是因果的（只从过去推断现在，不保存历史），做不到"拖动 10 分钟前的点，整条曲线平滑跟着变"。RTS（Rauch-Tung-Striebel）两遍算法——前向 Kalman 滤波保存每步 (x_pred,P_pred,x_filt,P_filt,F)，后向用 `G_k=P_filt_k·F_{k+1}ᵀ·P_pred_{k+1}⁻¹` 回传平滑——复杂度 O(N)（非朴素 GP 的 O(N³)），且数学上精确等价于状态空间高斯过程回归（Hartikainen & Särkkä 2010）。与 L1 的 StateEstimator 共用同一套 Matérn 参数化。

2. **平滑优于滤波的验证**（part1 §8 阶段 2）：`test_smoothing_beats_filtering_rmse` 用与模型时间尺度匹配的慢变真值（周期 400s 正弦 + 漂移，ℓ=300s 可追踪）+ 白噪声，对照 RTS 平滑与因果滤波，平滑 RMSE 更低（用过去+未来平均掉噪声）；`test_smoothed_variance_le_filtered_variance` 验证平滑后验方差 ≤ 滤波后验方差。

3. **三条曲线**（REFACTOR_PLAN.md §6.3）：EmotionCurve 同时携带 `raw_valence/raw_arousal`（原始观察，不可变）、`mean_valence/mean_arousal`（模型估计/用户修正后）、`var_valence/var_arousal`（置信带）。`confidence_band(i, channel, z)` 支持 ±1σ/±2σ 双层置信带渲染（part1 §6.2）。

4. **节点网格降采样**（part2 §2.4）：`build_node_grid` 按时间等距抽取节点（二分查找最近采样索引），N=2000→300 使纯 Python RTS 从 172ms 降到 ~26ms。`test_smooth_2000_points_completes` 验证 N=2000 全分辨率平滑在 3s 内完成（纯 Python，无 numpy）。控制点从节点网格再稀疏抽取（约 1/3），用户拖动作用于稀疏控制点而非逐采样点。

5. **CurveEdit append-only + 原始数据不可变**（part1 §3.2.1）：编辑独立记录，绝不覆盖原始观察。`test_edit_does_not_mutate_raw_observations` / `test_drag_edit_does_not_change_raw_curve` 验证编辑后 raw 曲线不变。CurveEdit 是 frozen dataclass，携带峰终加权全部上下文（edit_session_mood/edit_latency_sec/drag_velocity/salience/seconds_before_event_end）并自动计算 reliability_weight。

6. **编辑作为加权伪观察 + 精度提升**（part1 §4.2 峰终加权的工程落地）：编辑经 `_merge_edits` 转为伪观察插入时间序列，与原始观察共同参与平滑。关键设计——`compute_observation_noise` 对 `is_user_edit` 伪观察施加精度提升 `edit_factor=0.35-0.25×weight`：weight=1→100× 精度（强烈采纳），weight=0→8× 精度（仍可见，温和采纳）。这平衡了两个目标：UX 上拖动必须"实时变形"可见（part1 §3.2），学习上权重保留"温和 vs 强烈"单调区分（part1 §4.2）；reliability_weight 完整保存在 CurveEdit 中供 Phase 4 学习按权重加权。

7. **CurveEditor 控制器 + Undo/Redo**（REFACTOR_PLAN.md §28 Phase 3）：持有不可变 raw、append-only edit 日志、undo/redo 双栈。`drag_edit`（拖动）/`manual_edit`（手动输入，salience 先验更高）/`undo`/`redo`/`rebuild`（由 raw+edits+θ 完全重建，验证派生产物可重建性）。新编辑清空 redo 栈（标准分支语义）。

8. **不发散保证**（part1 §8 阶段 3）：`test_extreme_edits_do_not_diverge`（交替拖到 0/1 边界 + 高权重）与 `test_biased_edits_stay_bounded`（系统性上拖）验证曲线始终在 [0,1]、无 NaN/Inf、有界不爆炸。

**验证结果**：

| 测试集 | 通过 | 失败 | 耗时 |
|---|---:|---:|---:|
| `emowave/tests/`（Phase 1+2+3 累计） | **311** | 0 | 1.15s |
| 其中 Phase 3 新增（smoother+curve） | 54 | 0 | — |
| 旧套件 `tests/`+`test_engine.py`（回归） | 30 | 0 | 0.67s |

curve 模块零 numpy/scipy/PyQt5 依赖（LIGHTWEIGHT part2 §2 持续满足）。

**过程中修正的 5 处问题**：
- 编辑采纳不足（4 个测试）：单条编辑与原始观察同级（R 相同），在 30 条原始观察中无法产生可见形变 → 在 `compute_observation_noise` 增加 `is_user_edit` 精度提升分支（weight=1→100×，weight=0→8×）
- RMSE 对照信号失配：原用周期 80s 正弦远快于 ℓ=300s，模型失配导致平滑/滤波都滞后、差异被淹没 → 改用周期 400s 慢信号（与模型同尺度）+ 避开首尾边界效应
- 拖拽测试假时间戳陷阱：`drag_edit` 按 `time.time()-timestamp` 算延迟，测试用假时间戳 1000.0 导致延迟 ~17 亿秒、时近性权重 exp(-Δt/τ)→0、编辑被完全忽略 → 给 `drag_edit`/`manual_edit` 增加 `edit_latency_sec` 显式覆盖参数（生产环境 UI 传真实延迟，测试传 5.0s），修正后实测拖拽使曲线移动 +0.154（可见形变）

**Phase 3 完成标准核对**（REFACTOR_PLAN.md §28）：

- [x] 实现时间序列曲线（RTS 平滑轨迹）
- [x] Model Estimate curve（mean_valence/mean_arousal）
- [x] Raw Observation curve（raw_valence/raw_arousal，不可变）
- [x] Confidence band（var_* + confidence_band(±1σ/±2σ)）
- [x] Anchor points（control_point_indices 稀疏控制点）
- [x] 曲线拖拽（drag_edit → 增量重平滑 → 曲线实时变形）
- [x] 用户手动输入（manual_edit）
- [x] Undo / Redo（双栈，新编辑清空 redo）
- [x] **用户可以直接通过曲线修改当前情绪状态**（拖拽使曲线移动 +0.154 验证）

**下一步（Phase 4）**：Correction Learning（个人在线学习）。
- 新建 `emowave/core/calibration/`：correction dataset（从 CurveEdit/UserCorrection 聚合）、系统性偏差统计（part1 §4.4：总把峰值拉高→σ 被低估；总拉陡→ℓ 太大）。
- 实现边际似然最大化 + 层次先验收缩（part1 §3.3，Taylor 2017）学习个人 ℓ/σ/σ_noise。
- Coactive Learning 有界更新（part1 §4.3，Shivaswamy & Joachims 2015）：把拖动当"改进"而非"真值"，单次大幅拖动只产生有界影响。
- 验证：在 data_simulator_v2 的 4 类画像上确认学到的 ℓ 能区分画像（情绪稳定型 ℓ > 焦虑敏感型 ℓ）；"修正越多，误差是否下降"。

---
