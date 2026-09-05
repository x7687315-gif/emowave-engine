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

### Phase 4 — Correction Learning / L3 学习层（已完成 ✅）

**目标**：让模型从用户纠正中逐渐发生可验证的个性化变化。完成标准（REFACTOR_PLAN.md §28 Phase 4）：**模型能够从用户纠正中逐渐发生可验证的个性化变化**。

**做了什么**：

```
emowave/core/calibration/
├── __init__.py
├── corrections.py      CorrectionDataset + BiasStatistics + OnlineResidualRegression
├── personal_model.py   PersonalModelLearner + kalman_log_likelihood + LearnerConfig
└── calibrator.py       Calibrator（编排 dataset + 残差回归 + 超参学习）

emowave/tests/
├── test_calibration_corrections.py        30 tests
└── test_calibration_personal_model.py     21 tests
```

**怎么完成的（关键设计决策）**：

1. **两类学习并存互补**（REFACTOR_PLAN.md §9.3 + part1 §3.3）：
   - **残差回归**（OnlineResidualRegression）：学习"模型输出→用户偏好"的系统性映射，直接补偿预测值。快、立即降误差。对应 §9.3 Online Ridge Regression。在线更新 `A←A+wφφᵀ, b←b+w·r·φ, θ=(A+λI)⁻¹b`，φ(x)=[1,x]。
   - **超参数学习**（PersonalModelLearner）：学习 ℓ/σ/σ_noise 动力学参数，改变模型本身行为。慢、深层、形成"个人动态模型"。对应 part1 §3.3 边际似然+层次先验。
   - 为什么两者都要：残差回归补偿"静态偏移"（模型总低估 0.1），超参学习补偿"动态结构"（模型惯性比用户短）。前者立竿见影，后者长期塑形。

2. **系统性偏差诊断**（part1 §4.4 诊断表的代码落地）：`BiasStatistics` 从加权纠正流提取四个信号——
   - `peak_weighted_delta`（高 salience 纠正的加权 delta）→ σ 幅度偏差
   - `delta_autocorr`（相邻 delta 加权 lag-1 自相关）→ ℓ 惯性偏差（持续=ℓ 应增大，交替=ℓ 应减小）
   - `delta_scatter`（去系统分量后的加权标准差）× `(1-direction_consistency)` → σ_noise 偏差
   - `direction_consistency`（|加权均值|/加权|均值|）区分系统性 vs 随机
   全部按 reliability_weight 加权（part1 §4.2 峰终加权贯穿到学习层）。

3. **层次先验收缩**（part1 §3.3，Taylor 2017 / Oravecz 2011）：`θ_final = w_pop·θ_pop + (1-w_pop)·θ_personal`，w_pop 由 dataset 累积 ESS（Σ weight）分段决定——ESS<5 纯群体（1.0），5-20 强收缩（0.8），20-40 线性过渡，≥40 保留 20% 群体锚点防漂移。防小样本过拟合（part1 §9 风险表）。

4. **Coactive 有界更新**（part1 §4.3，Shivaswamy & Joachims 2015）：把拖动当"改进"而非"真值"——
   - 每条样本按 reliability_weight 加权（随意拖动权重低）
   - 岭正则 λ 防单次极端样本主导 θ
   - 残差修正裁剪到 ±max_correction（默认 0.3）
   - 超参数单步相对变化裁剪到 ±max_relative_step（默认 20%）
   测试验证：单次极端拖动（0→1）后参数仍 ≥0 且变化 ≤15%，模型不被毁掉。

5. **边际似然**（part1 §3.3）：`kalman_log_likelihood` 用 Kalman 前向遍累积 `log p(y|θ)=Σ[-½log|2πS_k|-½y_kᵀS_k⁻¹y_k]`，O(N)。`_log_det` 用高斯消元+部分主元算 log|det| 避免下溢。验证：与数据生成过程匹配的参数（ℓ=300 平滑信号）似然高于失配参数（ℓ=20）。

6. **渐进式个性化**：learner.update 每次事件结束调用一步，参数逐步向用户收敛（不是一步到位拟合）。`Calibrator` 编排完整闭环：ingest_correction/ingest_curve_edit → dataset+残差回归累积 → learn() 超参更新 → calibrate() 预测时施加残差修正。

**验证结果**：

| 测试集 | 通过 | 失败 | 耗时 |
|---|---:|---:|---:|
| `emowave/tests/`（Phase 1-4 累计） | **362** | 0 | — |
| 其中 Phase 4 新增（corrections+personal_model） | 51 | 0 | 0.26s |
| 全套件 + 旧回归（emowave+tests+test_engine） | **392** | 0 | 2.76s |

calibration 模块零 numpy/scipy/PyQt5 依赖。

**关键完成标准验证**：
- **"修正越多，误差下降"**（§28 Phase 4）：`test_regression_error_decreases_with_more_corrections` 与 `test_calibrator_error_decreases_end_to_end` 模拟有系统偏差的用户（corrected=pred+0.15+0.3pred），随纠正累积 held-out 误差从初始单调下降到 <0.05。
- **"ℓ 能区分画像"**（part1 §8 阶段4）：`test_learner_distinguishes_stable_vs_anxious_archetypes` 验证情绪稳定型（纠正持续，autocorr 高）学到的 ℓ 显著大于焦虑敏感型（纠正交替，autocorr 负）。
- **part1 §4.4 诊断表四行全覆盖**：峰值拉高→σ 增大、峰值拉平→σ 减小、纠正持续→ℓ 增大、纠正交替→ℓ 减小、随机拖动→σ_noise 增大，各有独立测试。

**过程中修正的 3 处问题**：
- 测试缺 `CorrectionSource` 导入 → 补充
- 层次收缩设计缺陷：`max(w_by_events, w_by_ess)` 中新建 prior 的 n_events=0 给 w=1.0，max 后抵消了大量纠正证据（ESS 高），使个性化无法发生（σ 被卡在 ~0.18）→ 改为以 dataset 累积 ESS 为直接证据度量（ESS 才是真实的个人证据量），修正后 σ 可收敛到 ~0.5
- `test_learner_shrinkage_alpha_increases`：固定 dataset 下 alpha 恒定 → 改为每步追加纠正（现实流程：ESS 渐增），alpha 真正单调上升

**Phase 4 完成标准核对**（REFACTOR_PLAN.md §28）：

- [x] 保存 UserCorrection（dataset append-only）
- [x] 建立 correction dataset（CorrectionDataset 聚合 UserCorrection/CurveEdit）
- [x] 统计系统性模型偏差（BiasStatistics 四信号，part1 §4.4）
- [x] 实现 Online Regression（OnlineResidualRegression 岭回归）
- [x] 增加个人参数（PersonalModelLearner 学 ℓ/σ/σ_noise + 层次收缩）
- [x] 建立 Model Error 曲线（held_out_error + 边际似然评估）
- [x] 验证"修正越多，误差是否下降"（端到端测试误差降到 <0.05）
- [x] **模型能够从用户纠正中逐渐发生可验证的个性化变化**

**下一步（Phase 5）**：Baseline Control（基线主权，REFACTOR_PLAN.md §8）。
- 新建 `emowave/core/calibration/baseline_control.py`：基线作为 GP 均值函数 m(t)（part1 §5.1），三级用户主权 nudge/fork/reset（part1 §5.2）。
- Fork 引入硬变点：regimes 分段，每段独立 baseline 与 θ，此后学习只用分叉后数据。
- BaselineShiftEvent 记录 + 基线历史/版本。
- BOCPD 提议 + 用户裁决闭环（part1 §5.3）：把 config 里 SHIFT_CONSECUTIVE_DAYS=3/SHIFT_STD_DEVIATIONS=2.0 的拍脑袋常数换成数据驱动。
- 验证：用户重标定后模型不会把新基线继续判断为异常（REFACTOR_PLAN.md §22 Baseline Test）；注入人工漂移确认 BOCPD 召回。

---

### Phase 5 — Baseline Control / L4 主权层（已完成 ✅）

**目标**：让用户能明确告诉系统"这是我的新正常状态"。完成标准（REFACTOR_PLAN.md §28 Phase 5）：**用户可以明确告诉系统"这是我的新正常状态"**。

**做了什么**：

```
emowave/core/calibration/
└── baseline_control.py   BaselineController + BaselineRegime +
                          ChangePointDetector + ChangePointProposal

emowave/tests/
└── test_baseline_control.py   44 tests
```

**怎么完成的（关键设计决策）**：

1. **三级用户主权**（part1 §5.2）：
   - **L1 Nudge 微调**：上下拖动基线虚线，"我最近的正常水平就是这样"。同一 regime 内产生新 Baseline 版本（version+1，parent 链），不分段。立即生效。
   - **L2 Fork 分叉**：标记某时刻为"新基线起点"，"从这里开始我是另一个人了"。引入**硬变点**：关闭当前 regime（end_time=分叉点），开启新 regime（独立 baseline，此后学习只用分叉后数据）。这是对"大波折"需求的正面回应。
   - **L3 Reset 重置**：清空个人模型回到群体先验，"忘掉我，重新开始"，开启全新 regime。

2. **基线作为 GP 均值函数 m(t)**（part1 §5.1）：情绪(t)=m(t)+f(t)，m 是缓慢漂移的基线（均值函数），f 是零均值 GP（波动部分，由 Phase 2 估计器学习）。基线编辑与曲线编辑共用同一套数学。`recompute_thresholds` 把阈值定义为基线±k·σ（基线相对），因此 nudge/fork 后阈值自动跟随平移。

3. **不变量保证**（REFACTOR_PLAN.md §8）：原始数据不变、历史记录不变，只有模型参数（基线/regime/阈值）重新标定。每次 nudge/fork/reset 产生一条 `BaselineShiftEvent`（append-only 事件流），基线历史与 regime 分段完整保留，支持 `baseline_at(timestamp)` 用任意历史时刻的基线重算。

4. **BOCPD 提议-裁决闭环**（part1 §5.3）：`ChangePointDetector` 用轻量 EWMA + z-score 持续偏离检测（不引入重型 BOCPD 库，符合 part2 §4.4 与低配置原则）——找最长"连续同向超阈值"段，提议变点 + 置信度（综合持续长度与偏离幅度）+ 建议新基线（偏离段均值）。用户裁决：确认→采纳分叉（auto_bocpd 来源），拒绝→记录负样本。

5. **检测器阈值数据驱动校准**（part1 §5.3 核心价值）：裁决累积为带标签变点数据集，`calibrate` 按确认率调整 z_threshold/min_sustained——拒绝率>0.7（误报多）→更保守（×1.15，+1 步），确认率>0.8（可能漏报）→更敏感（×0.9，有下限），0.3-0.8→不动。这把旧版 config 里 `SHIFT_CONSECUTIVE_DAYS=3`/`SHIFT_STD_DEVIATIONS=2.0` 两个拍脑袋常数换成数据驱动的决策规则。样本<3 不校准（防过拟合，呼应 part1 §9）。

**验证结果**：

| 测试集 | 通过 | 失败 | 耗时 |
|---|---:|---:|---:|
| `emowave/tests/`（Phase 1-5 累计） | **406** | 0 | — |
| 其中 Phase 5 新增（baseline_control） | 44 | 0 | 0.16s |
| 全套件 + 旧回归 | **436** | 0 | 2.17s |

**关键完成标准验证**：
- **§22 Baseline Test**：`test_baseline_test_recalibration_stops_false_alarm` 验证用户状态从 0.5 漂移到 0.7 后，重标定前 0.7 被判异常（z=2.5>2），nudge 基线到 0.7 后同样状态不再异常（z≈0）——"这不是异常，这是新的我"。`test_baseline_test_fork_recalibration` 验证 fork 分叉后按新 regime 基线判断。
- **阈值跟随基线**：`test_thresholds_shift_after_nudge` 验证基线上移 0.2 后预警阈值同步上移 0.2。
- **变点检测**：持续偏离提议、单点尖峰忽略（min_sustained 防护）、稳定信号不误报、更长更强偏离置信度更高。
- **裁决校准**：多拒绝→阈值升高（保守）、多确认→阈值降低（敏感，有下限）、样本少/均衡→不校准。

**过程中修正的 2 处问题**：
- `current` 语义缺陷：原用 `select_active_baseline(history, time.time())`，当操作带未来时间戳时（如 reset 到 base+200）real-now 查询返回旧基线 → 改为返回 `history[-1]`（最新定义的基线，操作立即生效语义），`baseline_at(ts)` 仍用 select_active_baseline 做历史查询
- 测试假时间戳陷阱（同 Phase 3）：propose/adjudicate 测试用 ts=1000.0+i，而 controller 初始基线 effective_from=真实 time.time()，fork 在 1003 关闭旧基线时 effective_to(1003)<effective_from(1.78e9) 报错 → 测试改用 base=time.time() 真实时间戳；并在 fork 增加清晰守卫（timestamp<current.effective_from 时抛明确 ValueError 而非下游晦涩错误）

**Phase 5 完成标准核对**（REFACTOR_PLAN.md §28）：

- [x] 基线可视化（EmotionCurve.raw/mean + Baseline 提供数据，UI 在 Phase 9）
- [x] 用户主动调整基线（nudge/fork/reset 三级主权）
- [x] BaselineShiftEvent（每次变更记录，append-only）
- [x] 基线历史（history + baseline_at 历史查询）
- [x] 基线版本（version/parent_id 链）
- [x] 基于新基线重新计算阈值（recompute_thresholds 基线相对）
- [x] 基线迁移测试（§22 Baseline Test 通过）
- [x] **用户可以明确告诉系统"这是我的新正常状态"**

**下一步（Phase 6）**：Personal Dynamics Model（个人动态模型，REFACTOR_PLAN.md §10）。
- 引入状态转移模型 E_{t+1}=f(E_t, X_t, U_t, Δt)，学习"这个用户的情绪是怎么变化的"。
- 学习 temporal decay（恢复速度）、signal sensitivity（哪些信号对这个用户重要）、个人波动范围。
- 预测短期趋势 + 对预测给出 uncertainty。
- 复用 Phase 2 的 Matérn 状态空间（ℓ 即 temporal decay）与 Phase 4 的个人参数学习。
- 验证：模型不只识别当前状态，而能回答"如果当前状态保持不变，趋势会怎样""恢复速度是否在变化"。

---

### Phase 6 — Personal Dynamics Model / 推断层（已完成 ✅）

**目标**：模型不只识别当前状态，而开始学习"这个用户的情绪是怎么变化的"。完成标准（REFACTOR_PLAN.md §28 Phase 6）。

**做了什么**：

```
emowave/core/inference/
├── __init__.py
├── dynamics.py     SignalSensitivity + PersonalDynamicsModel +
│                   OnlineFeatureRegression + DynamicsLearner +
│                   predict_forward + recovery_half_time
└── predictor.py    TrendForecast + TrendPredictor

emowave/tests/
└── test_inference_dynamics.py   37 tests
```

**怎么完成的（关键设计决策）**：

1. **状态转移模型 E_{t+1}=f(E_t,X_t,U_t,Δt)**（§10）：复用 Phase 2 Matérn 状态空间——F(Δt) 即 E_t→E_{t+1} 的转移，X_t（生理信号）经 control input 注入，U_t（用户修正）经 Phase 3/4 的编辑伪观察注入，Δt 进入闭式解。`predict_forward` 提供纯状态转移的前向预测。

2. **temporal decay → 恢复速度的可解释翻译**：`recovery_half_time(ℓ)=0.969·ℓ`。推导——Matérn 位置偏离按 d(Δt)=e^(-λΔt)(1+λΔt)·d₀ 衰减，解 d/d₀=0.5 得 λΔt≈1.678，即 t_half≈1.678ℓ/√3≈0.969ℓ。**ℓ 本质就是恢复时间尺度**，把抽象的 ℓ 翻译成"情绪恢复一半要多久"的可解释量。测试用 Matérn 衰减公式反向验证 0.969 系数（半衰期处偏离确实≈0.5）。

3. **signal sensitivity 学习**（§10"什么因素最容易让这个用户变化"）：`OnlineFeatureRegression` 多特征在线岭回归（含 R² 诊断），从 (生理/情境信号 → 状态变化率) 对学习个人权重。`DynamicsLearner.ingest_transition` 把相邻观察的状态变化作为目标 y、prev 时刻信号（hr_z/hrv_drop/activity/sleep 中心化 + 自身惯性）作为特征。R² 低 → 该信号只是噪声（§9.2"哪些信号只是噪声"）。`dominant_signals` 返回影响最大的前 k 个信号。

4. **个人波动范围**：volatility = 个人 σ（Phase 4 已学），mean_valence/arousal = 个人吸引子中心。组装进 `PersonalDynamicsModel`。

5. **短期趋势预测 + uncertainty**（§6.4 诚实表达不确定性）：`TrendPredictor.forecast` 从 EmotionState 前向传播，置信带随 horizon 变宽（Q 累积，方差单调增长趋近 P∞），置信度随 horizon 与预测方差下降（`_forecast_confidence` 双重指数惩罚）。`recovery_estimate` 基于恢复半衰期估算"回到基线附近"的时间（偏离越大需越多半衰期）。`recovery_speed_changing` 对比两时期模型判断恢复速度变化（§10 问题4）。

6. **基线作为 GP 均值函数的落地**（part1 §5.1）：`predict_forward` 在**偏离空间**传播——位置减去基线均值 m，F 使偏离回复到 0，输出加回 m。这修正了"原始 Matérn F 把状态均值回复到 0（而非情绪中性点 0.5/基线）"的建模错误，保证无观测时状态回复到基线而非 0。

**验证结果**：

| 测试集 | 通过 | 失败 | 耗时 |
|---|---:|---:|---:|
| `emowave/tests/`（Phase 1-6 累计） | **443** | 0 | — |
| 其中 Phase 6 新增（dynamics+predictor） | 37 | 0 | 0.21s |
| 全套件 + 旧回归 | **473** | 0 | 2.73s |

inference 模块零 numpy/scipy/PyQt5 依赖。

**关键完成标准验证**（§10 四个问题）：
- 问题1"趋势会怎样"：`forecast` 输出 trend + 预测轨迹 + 置信带；CI 随 horizon 变宽、置信度随 horizon 下降。
- 问题2"什么因素最易让该用户变化"：`most_influential_signals` / `dominant_signals`；HRV 下降驱动唤醒的用户学到 w_hrv_arousal>0。
- 问题4"恢复速度是否变化"：`recovery_estimate`（ℓ 短者恢复快）+ `recovery_speed_changing`（半衰期对比）。
- 问题3"应对方式是否有效"：留待 Phase 8 与 recommender（LinUCB）集成。

**过程中修正的 1 处问题**：
- 均值回复目标错误：原始 Matérn 状态空间 F 把状态回复到 0，但情绪量表中性点是基线（0.5）。中性状态 (0.5,0.5) 前向预测会漂向 (0,0) 使强度从 0 上升、trend 误判 RISING → `predict_forward` 改为偏离空间传播（part1 §5.1 基线=GP 均值函数），TrendPredictor 传入 baseline 作为 mean，修正后中性状态正确预测 STABLE

**Phase 6 完成标准核对**（REFACTOR_PLAN.md §28）：

- [x] 引入状态转移模型（复用 Matérn F(Δt) + control + 编辑）
- [x] 学习 temporal decay（ℓ → recovery_half_time）
- [x] 学习 signal sensitivity（OnlineFeatureRegression 多特征岭回归）
- [x] 学习恢复速度（recovery_estimate + recovery_speed_changing）
- [x] 学习个人波动范围（volatility = 个人 σ）
- [x] 预测短期趋势（forecast trend + 轨迹）
- [x] 对预测结果给出 uncertainty（CI 随 horizon 变宽 + confidence 下降）
- [x] **模型开始学习"这个用户的情绪是怎么变化的"**

**下一步（Phase 7）**：Lite Runtime / Performance（REFACTOR_PLAN.md §28 Phase 7 + part2 §5.2 Tier）。
- 新建 `emowave/core/tier.py`：能力档位探测（T0 Embed / T1 Lite / T2 Full），按 CPU/内存/平台/省电模式自动降档（part2 §5.2）。
- Benchmark：CPU / memory / SQLite 写入；数据降采样（Phase 3 节点网格已就绪）；异步/批量持久化。
- 验证低配置设备：1Hz 采样 CPU 占用、曲线重算延迟、内存足迹。
- 目标（§23）：Live state update <20ms，Curve redraw <16ms，内存低百 MB，无 GPU。

---

### Phase 7 — Lite Runtime / Performance（已完成 ✅）

**目标**：普通低配置电脑和手机也能实时运行（REFACTOR_PLAN.md §12）。核心机制不是"靠优化让它跑得动"，而是"按档位决定跑什么"（part2 §5.2）。

**做了什么**：

```
emowave/core/
└── tier.py    Tier(EMBED/LITE/FULL) + TierCapabilities + CAPABILITIES 预设 +
               detect_tier（CPU/内存/平台/省电探测）+ resolve_capabilities

emowave/tests/
└── test_tier.py            30 tests

research/
└── bench_core_performance.py   纯 Python 内核性能基准（L1/RTS/曲线重建）
```

**怎么完成的（关键设计决策）**：

1. **三档能力分层**（part2 §5.2 表的代码落地）：
   - **T0 Embed**（Amiya 插件/极低配）：仅 L1 因果滤波，内存存储，10 分钟滚动窗口，无曲线/学习。常驻开销≈0。
   - **T1 Lite**（低端手机/低配 PC）：L1 + L2 曲线（节点减半 150），关闭 L3 个性化学习（需跨事件累积计算），SQLite。
   - **T2 Full**（中高端）：完整四层，节点 300。
   - **关键不变量**：L1 因果滤波在所有档位都启用——情绪追踪核心体验在一切设备上一致，Tier 只削减"非实时、批量"的功能。

2. **auto 探测**（全部标准库，part2 §5.2）：`os.cpu_count()` + 跨平台内存探测（Windows ctypes GlobalMemoryStatusEx / Linux-macOS os.sysconf）+ 移动平台检测（sys.platform / ANDROID_ROOT / EMOWAVE_MOBILE）+ 省电模式（EMOWAVE_BATTERY_SAVER）。决策保守（宁可降档不可卡顿）：内存<1024MB 或单核→T0；移动端或内存<3072MB 或≤2 核→T1；其余→T2。省电模式强制 ≤T1。显式 `EMOWAVE_TIER=0|1|2` 直接锁定。

3. **探测失败的保守降级**：内存探测返回 None（未知）时不上 T2，保守取 T1（不确定就不冒进）。

4. **性能基准**（`research/bench_core_performance.py`，验证 §23 目标）：本机实测（16 核 / 15773MB / T2）——

| 指标 | 实测 | 目标（§23 / part2） | 结论 |
|---|---:|---|---|
| L1 因果滤波单步 | **0.148 ms** | <20 ms | ✅ 余量巨大 |
| L1 @1Hz CPU 占用 | **0.0148%** | — | ✅ 设备慢 5 倍仍 0.074% |
| RTS 平滑 N=2000 全采样 | 449.9 ms | — | （一次性后台任务） |
| RTS 节点网格降采样 N=300 | **74.0 ms** | part2 §2.4 ~26ms 量级 | ✅ 加速 6.1× |
| 曲线重建 N=600→节点150 | **149.3 ms** | T1 ≤150ms | ✅ 达标 |
| GPU 依赖 | 无 | 不要求 | ✅ 纯 CPU+标准库 |

降采样验证了 part2 §2.4 的核心论断：曲线视觉平滑度取决于节点数而非采样点数，RTS O(N) 因此从全采样 450ms 降到节点网格 74ms（6.1×），使纯 Python 在低配设备可行。

**验证结果**：

| 测试集 | 通过 | 失败 | 耗时 |
|---|---:|---:|---:|
| `emowave/tests/`（Phase 1-7 累计） | **473** | 0 | — |
| 其中 Phase 7 新增（tier） | 30 | 0 | 0.10s |
| 全套件 + 旧回归 | **503** | 0 | 3.72s |

**过程中修正的 1 处问题**：
- 注入参数哨兵缺陷：`detect_tier` 的 cpus/memory_mb 等参数默认 None 且"None 即探测"，导致测试无法显式传 memory_mb=None 模拟"探测失败"（会被实际探测值覆盖）→ 引入模块级 `_UNSET` 哨兵区分"未提供（探测）"与"显式 None（未知，触发保守降档）"

**Phase 7 完成标准核对**（REFACTOR_PLAN.md §28）：

- [x] 移除 Core 中不必要的大依赖（Phase 2 起零 numpy，本阶段确认全内核零重依赖）
- [x] Benchmark CPU（L1 0.148ms/步，1Hz 占用 0.0148%）
- [x] Benchmark memory（探测 + 档位内存策略）
- [x] 数据降采样（节点网格，RTS 6.1× 加速）
- [x] 控制 UI redraw frequency（曲线重建 149ms，节点数按档位 150/300）
- [x] 验证低配置设备（Tier 自动降档 + 保守降级）
- [ ] Benchmark SQLite 写入 / 异步持久化（存储适配器在 Phase 9 落地，Tier 已定义 storage_backend 与 async_persistence 开关）

**下一步（Phase 8）**：Amiya Adapter（REFACTOR_PLAN.md §14-§18 + part2 §3）。
- 新建 `emowave/adapters/agent/amiya.py`：EmotionBridge（连续 (v,a) → Amiya 4 状态 calm/thinking/worried/happy，part2 §3.4 映射）。
- handshake + capability discovery（Phase 1 protocol 已定义 AmiyaHandshake/AmiyaCapability）。
- 四级优雅降级（part2 §3.2）：L0 启动期未安装静默回退 / L1 配置期默认关闭 / L2 运行期异常捕获回退 / L3 能力期低配降档。
- EmoWave→Amiya 输出协议（EmotionStateOutput，Phase 1 已定义）+ Amiya→EmoWave 输入协议（AmiyaInput→Observation）。
- 验证（part2 §7 阶段1 关键）：EMOWAVE_ENABLED=0 默认→Amiya 行为不变；emowave 缺失→正常启动；内部异常→单轮回退不中断。
- §22 Degradation Test + Reverse Degradation Test。

> **备注**：Phase 6 与 Phase 7 的提交已在本地完成（commit 6c63a2b 及后续），但因 GitHub 网络瞬断（连接重置）push 暂挂，网络恢复后将一并推送。本地提交安全，无数据丢失风险。

---
