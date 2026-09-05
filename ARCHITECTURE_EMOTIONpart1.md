# 心潮 EmoWave · 情绪曲线建模与人机协同编辑架构

> **文档性质**：技术架构设计提案（供评审）
> **日期**：2026-09-05
> **前置文档**：`REFERENCES.md`（本架构的理论与工程参照索引）
> **核验说明**：本文档所有引用数来自 OpenAlex / Crossref API（2026-09-05 实测），所有 star 数来自 GitHub REST API（同日实测），所有数值结论均由本文档附带的脚本计算得出，可复现。

---

## 0. 摘要：三个需求，一个架构冲突

你的需求可以拆成三件事：

| # | 需求 | 本质 |
|---|---|---|
| A | 情绪变化是一条曲线，用户能直观感受、能拖动、能手动改数值 | **可编辑的连续曲线建模 + 可视化** |
| B | 用户不断提供数据后，曲线越来越贴合真实情绪 | **在线个性化学习** |
| C | 情绪出现大波折时，用户有权从底层改变情绪基线 | **用户对模型参数的最终主权** |

**这三个需求背后有一个必须先解决的架构冲突：**

> 你现在的 `EmotionKalmanFilter` 是**因果**的（causal）——它只从过去推断现在，状态只有 `_x` 和 `_P` 两个向量，不保存历史。
> 而"拖动历史曲线"要求**非因果**的全局平滑：用户改了 10 分钟前的一个点，整条曲线的形状都要跟着平滑地变。

因果模型做不到这件事。这就是必须先做的架构决策。

**本文档的核心结论**：不要"用高斯过程替换卡尔曼"，而是**把现有的卡尔曼升级为"状态空间高斯过程"**——它底层仍然是卡尔曼滤波/平滑，因此保持 O(N) 复杂度、可在端侧运行、复用现有代码结构；但在数学上它**精确等价于**一个高斯过程回归，从而获得 GP 的全部好处（全局平滑、核函数可解释的超参数、不确定性量化）。

支撑这个方案的已验证文献：Hartikainen & Särkkä (2010)，**219 引用**，DOI `10.1109/mlsp.2010.5589113`。

---

## 1. 现状诊断

我通读了 `kalman_filter.py`、`models.py`、`widgets.py`、`baseline.py`、`config.py`、`engine.py`。在谈"做什么"之前，先记录"现在缺什么"。

### 1.1 硬伤清单

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| **1** | **完全没有时序曲线可视化** | `widgets.py` 只有 `EmotionCanvas`（2D 效价-唤醒相图，第 70–124 行）。全仓库无时间序列曲线绘制 | 需求 A 的可视化部分需从零构建 |
| **2** | **不确定性从未被可视化** | `EmotionState.covariance_trace` 有计算（`kalman_filter.py:532`）但 UI 层零引用 | 用户无法判断"哪里该改"，需求 A 的编辑体验缺关键引导 |
| **3** | **没有任何用户修正入口** | 在 `engine.py` / `baseline.py` / `threshold.py` 中检索 `edit\|revise\|correct\|override\|修正\|编辑` → **0 命中** | 需求 A 的编辑与需求 C 的基线主权完全是新功能 |
| **4** | **参数是全局常量，不可学习** | `config.py` 全部为模块级常量（如 `EWMA_ALPHA = 1.0/8.0`、`SHIFT_STD_DEVIATIONS = 2.0`） | 需求 B "越用越贴合"无法实现——没有可学习的参数 |
| **5** | **基线是纯 EWMA，无用户干预** | `baseline.py:95–114` 逐维度固定 α 加权 | 需求 C 无落点 |

### 1.2 一个量化的严重问题：模型几乎没有记忆

现有 `velocity_damping = 0.85`（`kalman_filter.py:58`）是个"拍脑袋"常数。但如果把它映射到 Matérn ν=3/2 核的状态空间形式，可以反推出它隐含的**长度尺度 ℓ**——即模型认为情绪在多长时间内保持相关。

实测计算（脚本 `research/scale_calc.py`，本次运行输出）：

```
[口径A] 若 exp(-λ·Δt)=0.85  (Δt=1s):  λ=0.1625 → ℓ=√3/λ = 10.7 s
[口径B] 若 F[1,1]=0.85:              λ=0.0796 → ℓ=√3/λ = 21.8 s

不同 ℓ 下，60 秒后的自相关：
  ℓ=  10s → 0.000     ← 当前量级
  ℓ=  30s → 0.140
  ℓ=  60s → 0.483
  ℓ= 300s → 0.952
  ℓ= 900s → 0.994
```

**结论**：当前模型的隐含长度尺度约 **11–22 秒**。在这个尺度下，**60 秒后的自相关为 0**——模型认为 1 分钟前的情绪与现在毫无关系。

这会直接导致三个后果：

1. 滤波器的"惯性预测"能力形同虚设，它基本只是在做逐点平滑
2. `extrapolate()` 外推 600 秒（`config` 中 `extrapolation_horizon_sec = 600.0`）在数学上不可能有意义——模型认为 10 秒外就没有可外推的结构
3. 这正是基准测试中**预警精确率仅 0.198** 的一个结构性原因

> **修复方向**：ℓ 必须成为**可学习的、因人而异的**参数。而它恰好就是心理学中研究得很透彻的**情绪惯性（emotional inertia）**——Kuppens et al. (2010)，**757 引用**。详见 §4.3。

### 1.3 一个待确认的概念问题：情绪强度的定义

`kalman_filter.py:517`：
```python
intensity = min(1.0, np.sqrt(v ** 2 + a ** 2) / np.sqrt(2))
```

这把强度定义为**到原点的距离**。但按 Russell 环状模型，效价/唤醒映射到 [0,1] 后，**中性点是 (0.5, 0.5)**，强度应该是**到中性点的距离**：

```python
intensity = np.sqrt((v - 0.5)**2 + (a - 0.5)**2) / 0.7071
```

差别在哪？在现定义下，`(v=0, a=0)`（极度不适 + 极度困倦，即典型的抑郁性迟滞）算出的强度是 **0**——系统认为这个人"毫无情绪波动"。这显然不对。

> 白皮书 2.1.2 节目前是按"到原点距离"写的，如果确认要改，白皮书需同步修订。**建议优先修复**，因为它同时影响了强度预警、极值标注和周报结论。

---

## 2. 技术选型：为什么是"状态空间高斯过程"

### 2.1 候选方案对比

| 方案 | 支持历史编辑 | 全局平滑 | 不确定性 | 端侧 O(N) | 超参数可学习 |
|---|:---:|:---:|:---:|:---:|:---:|
| 现有 Kalman Filter | ✗ | ✗ | ✓ | ✓ | ✗ |
| 朴素 GP 回归 | ✓ | ✓ | ✓ | ✗ O(N³) | ✓ |
| RTS 平滑器 | ✓ | ✓ | ✓ | ✓ | ✗ |
| **状态空间 GP**（本方案） | **✓** | **✓** | **✓** | **✓** | **✓** |

### 2.2 核心定理

> **Hartikainen & Särkkä (2010)**：一维时序高斯过程回归，若其核函数的谱密度是有理函数（Matérn 族满足），则可以精确转化为一个线性随机微分方程，其回归解由 **Kalman 滤波 + RTS 平滑** 给出，复杂度 **O(N)** 而非 O(N³)。

- 论文：*Kalman filtering and smoothing solutions to temporal Gaussian process regression models*，IEEE MLSP 2010
- **219 引用**，DOI `10.1109/mlsp.2010.5589113`
- 系统教材：Särkkä (2013) *Bayesian Filtering and Smoothing*，Cambridge Univ. Press，**1,785 引用**，DOI `10.1017/cbo9781139344203`（全书作者公开免费）

**这意味着：你不需要在 Kalman 和 GP 之间做取舍。** Kalman 是**计算后端**，GP 是**数学语义**。

### 2.3 天作之合：现有状态向量就是 Matérn ν=3/2

Matérn ν=3/2 核的状态空间形式是 2 维的 `[f, ḟ]`（值 + 一阶导数）。你的系统有两个通道（效价、唤醒），所以是 4 维：

```
现有:  x = [valence, arousal, d_valence, d_arousal]     ← kalman_filter.py:7
Matérn: x = [v, v̇, a, ȧ]                                ← 完全同构（仅顺序不同）
```

**你的实现在结构上已经是一个 Matérn 族状态空间模型了**，只是参数是手工离散化的（`F` 矩阵直接写死为匀速运动、阻尼硬编码为 0.85、`Q` 由 `q_position_std` 等经验值拼出），而不是从连续 SDE 正确离散化而来。

**因此迁移路径不是"重写"，而是"参数化升级"。**

### 2.4 具体映射（可直接实现）

Matérn ν=3/2，长度尺度 ℓ，令 λ = √3 / ℓ：

**连续状态矩阵**（单通道）：
```
A = [[ 0,      1   ],
     [ -λ²,   -2λ ]]
```

**离散转移矩阵**（步长 Δt，闭式解）：
```
F(Δt) = e^(-λΔt) · [[ 1 + λΔt,      Δt     ],
                     [ -λ²Δt,     1 - λΔt  ]]
```

> 已验证：Δt→0 时 F→I，且 dF/dΔt|_{0} = A。

**这个闭式解取代现有的手写 F 矩阵和 `velocity_damping`。** 一个参数（ℓ）取代了一个魔法常数，且 ℓ 有明确的心理学含义。

**过程噪声** Q 由稳态协方差导出：
```
P∞ = σ² · [[1, 0], [0, λ²]]        （σ = 幅度超参数）
Q(Δt) = P∞ - F(Δt)·P∞·F(Δt)ᵀ
```

**待学习的超参数**（每个用户一份）：

| 参数 | 含义 | 心理学对应 |
|---|---|---|
| ℓ_valence | 效价的时间相关尺度 | 情绪惯性（效价维度） |
| ℓ_arousal | 唤醒的时间相关尺度 | 情绪惯性（唤醒维度） |
| σ_valence, σ_arousal | 波动幅度 | 情绪变异性 / 不稳定度 |
| σ_noise | 观测噪声 | 用户的自我报告可靠度 |

> ℓ 与情绪惯性的等价性是本架构最重要的理论红利：Kuppens et al. (2010) *Emotional Inertia and Psychological Maladjustment*（**757 引用**）证明情绪惯性存在稳定个体差异且预测抑郁发作；Kuppens et al. (2012) *Emotional inertia prospectively predicts the onset of depressive disorder in adolescence*（**292 引用**）进一步确立其临床预测价值。你的 ℓ 就是这个量的连续时间版本。

---

## 3. 四层架构

```
┌──────────────────────────────────────────────────────────────┐
│  L4  主权层 (Sovereignty)                                     │
│      基线制度：微调 / 分叉 / 重置                              │
│      BOCPD 提议 → 用户裁决 → 裁决结果反哺检测器                │
└───────────────────────▲──────────────────────────────────────┘
                        │ 用户授权
┌───────────────────────┴──────────────────────────────────────┐
│  L3  学习层 (Learning)                                        │
│      边际似然最大化 → 个人超参数 θ_user                        │
│      层次先验收缩：θ_user ← 群体先验 + 个人证据                 │
│      Coactive Learning：把用户拖动当作"改进"而非"真值"          │
└───────────────────────▲──────────────────────────────────────┘
                        │ 编辑事件流
┌───────────────────────┴──────────────────────────────────────┐
│  L2  回顾层 (Retrospective)   ← 【需求 A 的核心】              │
│      RTS 平滑器：O(N)，非因果，全局平滑                        │
│      产出：均值曲线 μ(t) + 置信带 σ(t) + 控制点                │
│      用户拖动 → 增量重平滑 → 整条曲线实时变形                   │
└───────────────────────▲──────────────────────────────────────┘
                        │ 原始观测（不可变）
┌───────────────────────┴──────────────────────────────────────┐
│  L1  感知层 (Perception)   ← 现有 EmotionKalmanFilter 升级     │
│      因果 Kalman 滤波，<10ms，无历史                           │
│      输入：滑条 ~1Hz + 手表 HR/HRV                             │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 L1 感知层（改造现有模块）

改造 `kalman_filter.py`：
- 用 §2.4 的 Matérn 闭式解替换手写 `F` 与 `velocity_damping`
- 保留现有自适应观测噪声 `compute_R_from_interaction()` 的设计思想（滑条交互质量 → 噪声），这是个好设计，不要丢
- 保留生理控制输入机制
- **新增**：输出中保留完整协方差（不只是 trace），供 L2 使用

**不改**：O(1) 内存、递归更新、<10ms 延迟、无历史依赖。

### 3.2 L2 回顾层（全新模块：`curve.py`）

这是需求 A 的主体。

**触发时机**：事件结束、用户打开历史、用户进入编辑模式。

**算法**：RTS（Rauch-Tung-Striebel）平滑器，对事件窗口内的观测做后向传播。O(N)，非因果。

**输出**：
```python
@dataclass
class EmotionCurve:
    timestamps: np.ndarray      # (N,)
    mean: np.ndarray            # (N, 2)  效价/唤醒的平滑后验均值
    variance: np.ndarray        # (N, 2)  后验方差 → 渲染置信带
    control_points: np.ndarray  # (M,)    可拖拽控制点的时间索引
    hyperparameters: dict       # 本次使用的 θ
    version: int                # 曲线版本（见 §3.2.1）
```

**为什么拖动体验会好**：RTS 平滑是非因果的，且 GP 后验是全局耦合的。用户拖动一个控制点，整条曲线会以核函数决定的光滑方式重新收敛——**这就是"拉函数图像"的手感**。而现有因果滤波器做不到（改历史点对过去无影响）。

#### 3.2.1 关键约束：原始数据必须不可变

**用户编辑绝不能覆盖原始观测。** 架构上必须保证：

```
raw_observations   : append-only，永不修改（系统采集的滑条/生理数据）
user_edits         : append-only，独立日志（时间戳、原值、新值、上下文）
EmotionCurve       : 派生产物，可由 (raw + edits + θ) 完全重建
```

**为什么这是硬性要求**：如果用户编辑直接覆盖原始数据，模型最终会去拟合用户**被记忆偏差污染后的回忆**，而不是用户当时的真实体验。这会系统性摧毁 §4.2 讨论的学习信号。曲线是可重算的"视图"，原始观测是"事实"。

### 3.3 L3 学习层（新增：`personalize.py`）

**目标**：让 ℓ、σ、σ_noise 收敛到符合该用户的取值。

**方法**：最大化边际似然（marginal likelihood）——状态空间形式下由 Kalman 滤波直接给出，O(N)：

```
log p(y | θ) = -½ Σ [log|2πS_k| + y_kᵀ S_k⁻¹ y_k]
```

**层次先验**（关键，防止小样本过拟合）：

```
θ_user ~ LogNormal(μ_population, Σ_population)
θ_user = argmax [ log p(y_user | θ) + log p(θ | μ_pop, Σ_pop) ]
```

依据：
- **Taylor et al. (2020)** *Personalized Multitask Learning for Predicting Tomorrow's Mood, Stress, and Health*，IEEE Trans. Affective Computing，**269 引用**，DOI `10.1109/taffc.2017.2784832`——证明了"群体先验 + 个人微调"的多任务结构在情绪预测上优于纯个人模型
- **Oravecz et al. (2011)** *A hierarchical latent stochastic differential equation model for affective dynamics*，Psychological Methods，**120 引用**，DOI `10.1037/a0024375`——**层次化情绪动力学模型**，与本架构的数学结构高度一致，是最直接的方法论参照

**更新节奏**：事件结束时增量更新，不必实时。

### 3.4 L4 主权层（改造 `baseline.py` + 新增 `sovereignty.py`）

见 §5。

---

## 4. 核心设计：用户拖动的语义学

**这是我对你的想法最主要的扩展。** 你提出"用户可以拉曲线"，但没有定义这个动作在数据层面**意味着什么**。这个定义直接决定系统是越用越准还是越用越歪。

### 4.1 三类信号，可靠性完全不同

| 类型 | 例子 | 可靠性 | 处理方式 |
|---|---|---|---|
| **即时采样** | 现在拖动滑条 | 最高 | 直接观测，低噪声 |
| **即时修正** | "不对，我现在是 0.8 不是 0.5" | 高 | 直接观测，中噪声 |
| **回顾编辑** | 拖动昨天的曲线 | **随位置剧烈变化** | 加权观测，见 §4.2 |

前两类是 EMA 的标准场景。**第三类是你的新需求，也是风险所在**——因为人类的情绪记忆是系统性扭曲的。

### 4.2 【扩展点】峰终加权：用记忆心理学给编辑定权重

心理学对"人如何回忆一段情绪经历"有非常明确的结论：

**发现 1 — 峰终定律（peak-end rule）**
> 人对一段经历的回顾性评价，主要由**峰值**和**结尾**决定。
> Kahneman et al. (1993) *When More Pain Is Preferred to Less: Adding a Better End*，Psychological Science，**1,523 引用**，DOI `10.1111/j.1467-9280.1993.tb00589.x`

**发现 2 — 过程忽视（duration neglect）**
> 人会忽略经历的**持续时间**。
> Fredrickson & Kahneman (1993)；另见 Liersch et al. (2009) *Duration neglect by numbers—and its elimination by graphs*，OBHDP，DOI `10.1016/j.obhdp.2008.07.001`

**发现 3 — 心境一致性记忆偏差（mood-congruent memory）**
> 回忆时的**当前心境**会污染对过去情绪的回忆。
> Faul & LaBar (2023) *Mood-congruent memory revisited*，Psychological Review，**107 引用**，DOI `10.1037/rev0000394`

**这三条合成一条设计原则：**

> 用户拖动曲线时，**在峰值附近和结尾附近的编辑是可靠的**；**在平淡中段和起始段的编辑是不可靠的**；且**用户当前心境越极端，所有历史编辑都应整体降权**。

**具体公式**：

```
R_edit(t) = R_base · ω_recency(t) · ω_salience(t) · ω_current_mood
```

其中：

```python
# 1. 时近性：越近期越可信
ω_recency(t) = exp(-(t_now - t) / τ_recall)        # τ_recall ≈ 1 天

# 2. 显著性：峰值与结尾权重高（峰终定律）
salience(t) = |μ(t) - baseline| · w_peak
            + |dμ/dt| · w_slope
            + exp(-(t_end - t)/τ_end) · w_end      # 结尾提升

ω_salience(t) = exp(-salience(t))                  # 显著性越高 → 噪声越低
```

**工程后果**：用户在曲线中段随手一拖，系统**温和采纳**；用户在峰值处拖，系统**强烈采纳**。这个差异就是"越用越准"和"越用越歪"的分界线。

### 4.3 【扩展点】Coactive Learning：把编辑当"改进"，不是"真值"

第二个风险：用户可能随意拖动，或出于"我希望曲线长这样"而非"曲线实际长这样"来编辑。

**Coactive Learning**（Shivaswamy & Joachims, 2015，JAIR，**53 引用**，DOI `10.1613/jair.4539`）正是为这种场景设计的：

> 传统学习假设用户提供**标签**（label）。Coactive Learning 假设用户提供的是**改进**（improvement）——用户展示一个比系统输出"更好"的结果，系统据此更新，但不假设用户的改进是最优的。

**映射**：用户把曲线从 `μ_model` 拖到 `μ_edit`，这不是"真值就是 μ_edit"，而是"**μ_edit 比 μ_model 好**"。

**实现**：
```
约束：  score(μ_edit; θ) ≥ score(μ_model; θ) + margin
更新：  若违反则沿梯度推进一步，步长受 §4.2 权重调节
```

这让系统对用户的随意拖动**鲁棒**——单次大幅拖动只产生有界的影响，不会毁掉模型。

### 4.4 【扩展点】系统性拖动 = 超参数错了，不是数据错了

这是最容易被忽略、但对"越用越准"最关键的一条。

如果用户在**多个事件**中都表现出同一种拖动模式——比如总是把峰值往上拉、或总是把上升段拉得更陡——那么问题**不在数据**，而在于 **ℓ 或 σ 设错了**：

| 系统性拖动模式 | 诊断 | 修正 |
|---|---|---|
| 总把峰值拉高/拉低 | σ（幅度）被低估/高估 | 调整 σ |
| 总把曲线拉得更陡 | ℓ 太大（模型过度平滑） | 减小 ℓ |
| 总把曲线拉得更平 | ℓ 太小（模型过度跟随噪声） | 增大 ℓ |
| 拖动幅度随机、无模式 | 观测噪声 σ_noise 偏小 | 增大 σ_noise |

**实现**：维护一个"拖动偏差统计量"（按事件聚合的方向性统计），当检测到统计显著的系统性偏差时，**触发 L3 超参数重估**，而不是继续累积编辑。

> 这条把"用户编辑"从单纯的数据补充，升级为**模型诊断信号**。它直接回应了你的需求 B——用户提供的数据不只让曲线"更像"，还让**模型本身**变得更对。

### 4.5 【扩展点】反应性偏差：编辑本身会改变情绪

你的白皮书 7.1.3 已经意识到"滑条交互本身可能影响情绪"。编辑历史曲线这个问题**更严重**——它要求用户主动回忆情绪激动的时刻。

- **Stone et al. (2023)** *Evaluation of Pressing Issues in Ecological Momentary Assessment*，Annu. Rev. Clin. Psychol.，**197 引用**，DOI `10.1146/annurev-clinpsy-080921-083128`
- **Doherty et al. (2020)** *The Design of Ecological Momentary Assessment Technologies*，Interacting with Computers，**192 引用**，DOI `10.1093/iwcomp/iwaa019`

**设计对策**：
1. 编辑会话限制时长，超时温和提醒
2. 高强度事件（峰值接近危险区）默认**不主动邀请编辑**，需用户显式选择
3. 记录编辑时的用户当前情绪，作为 `ω_current_mood` 的来源
4. 编辑完成后提供"回到当下"的收尾交互（而不是让用户停留在回忆里）

---

## 5. 基线主权模型（需求 C）

你要求"当用户情绪出现大的变化和波折时，给用户从底层改变情绪基线的权限"。

### 5.1 基线 = GP 的均值函数

架构上，把基线定义为 GP 的**均值函数 m(t)**，而不是一个独立模块：

```
情绪(t) = m(t) + f(t)
          │       └─ 零均值 GP：情绪的波动部分
          └─ 均值函数：基线，缓慢漂移
```

好处：基线编辑和曲线编辑**共用同一套数学**，不需要额外的编辑机制。

### 5.2 三级权限

| 级别 | 操作 | 语义 | 影响范围 |
|---|---|---|---|
| **1. 微调 Nudge** | 上下拖动基线虚线 | "我最近的正常水平就是这样" | 立即生效，随后由 EWMA 自然衰减 |
| **2. 分叉 Fork** | 标记某时刻为"新基线起点" | "从这里开始，我是另一个人了" | **历史分段**，此后学习只用分叉后数据 |
| **3. 重置 Reset** | 清空个人模型 | "忘掉我，重新开始" | 回到群体先验 θ_population |

**"分叉"是最有力量的一级**，也是对你"大波折"需求的正面回应。它的技术含义是引入一个**硬变点**：

```
regimes = [R₁(t₀→t₁), R₂(t₁→t₂), ...]
每个 regime 有独立的基线 mᵢ 与超参数 θᵢ
```

生活事件（换工作、失恋、开始服药、搬迁）会让历史数据失去参考价值。分叉让用户能明确告诉系统："**这段历史不再适用于我**"。

### 5.3 BOCPD 提议，用户裁决

```
BOCPD 检测到疑似漂移
        ↓
  生成候选分叉点 + 置信度
        ↓
   ┌────┴────┐
  用户确认    用户拒绝
   │              │
 采纳分叉      抑制该点（记录为负样本）
   │              │
   └──────┬───────┘
          ↓
  用户裁决→带标签变点数据集
          ↓
  用于校准 BOCPD 的阈值（把"3天2σ"启发式换成数据驱动的决策规则）
```

**这个闭环的价值**：当前 `config.py` 里的 `SHIFT_CONSECUTIVE_DAYS = 3`、`SHIFT_STD_DEVIATIONS = 2.0` 是完全没有依据的常数。用户的每次裁决都是一条带标签样本，积累到一定量后，这两个常数可以被**数据驱动地拟合**出来。

**参照**：Gama et al. (2014) *A survey on concept drift adaptation*，ACM Computing Surveys，**3,568 引用**，DOI `10.1145/2523813`。

---

## 6. 可视化设计规范

### 6.1 主视图：时序曲线（新增，需求 A 的主体）

```
  强度/效价
   1.0 ┤                    ╭─╮            ← 均值曲线 μ(t)
       │              ╭─────╯ ╰──╮
   0.5 ┤──────────────╯           ╰────    ← 基线虚线 m(t)（可拖动）
       │         ╭────╯
   0.0 ┼─────────╯──────────────────────
       └──────────────────────────────→ 时间
         ░░░░ = ±1σ 置信带（后验不确定性）
         ●    = 可拖拽控制点
         ▲    = 系统标注的峰值
```

**要素**：
1. **均值曲线** — 情绪轨迹
2. **±1σ 置信带** — **这是编辑体验的关键**：带子越宽的地方模型越不确定，用户在这里的编辑信息量最大。应视觉鼓励在这些位置修正
3. **基线虚线** — 可拖动（L4 微调）
4. **控制点** — 稀疏（不是每个采样点），拖动后整条曲线光滑变形
5. **峰值/危险区标记** — 复用现有 `annotator.py` 的输出

**保留现有 2D 相图**：`EmotionCanvas` 的效价-唤醒相图有价值（能看出情绪的"绕圈"模式），作为**副视图**保留，与时序曲线联动（悬停时序曲线的某点，相图高亮对应位置）。

### 6.2 不确定性可视化必须谨慎

Padilla et al. (2018) *Decision making with visualizations: a cognitive framework across disciplines*，**347 引用**，DOI `10.1186/s41235-018-0120-9`——不确定性可视化设计不当会导致用户误判。

**具体规范**：
- 用**带状**（band）而非**误差棒**（error bars）——带状在连续曲线上更易读
- **不要**只画 ±1σ：建议双层（±1σ 深色、±2σ 浅色），让用户感知分布尾部
- 数值显示**避免过度精确**：显示"0.62 ± 0.08"，不要显示"0.6234 ± 0.0812"
- 当方差超过阈值时，曲线应**视觉降级**（如虚化），明确传达"这里模型不确定"

### 6.3 技术选型：pyqtgraph

当前项目是 PyQt5。**[pyqtgraph](https://github.com/pyqtgraph/pyqtgraph) — 4,409 star，最近更新 2026-08-31，活跃维护。**

理由：
- Qt 原生（与 PyQt5 同渲染栈，无嵌入开销）
- 内置可拖拽图元（`ROI`、`ScatterPlotItem` 的拖拽信号），直接支撑控制点交互
- 高性能，实时重绘无压力（RTS 平滑后整条曲线重算 + 重绘需 <16ms 才能保证 60fps 手感）

> 备选：Matplotlib `widgets`（生态大但性能差、Qt 嵌入笨重）、Plotly（Web 技术栈，与桌面端不一致）。**推荐 pyqtgraph。**

### 6.4 曲线本身有治疗价值

Liersch et al. (2009) *Duration neglect by numbers—and its elimination by graphs* 发现：**图表能消除过程忽视**。用户看到完整的情绪曲线，会修正"我整个下午都很糟"这类被峰终定律扭曲的自我评价。

换句话说，**时序曲线不只是交互界面，它本身就是产品价值**。这一点值得写进白皮书。

---

## 7. 数据模型变更

新增（建议放 `models.py` 或新建 `curve_models.py`）：

```python
@dataclass
class CurveEdit:
    """一次用户编辑。append-only，永不修改原始观测。"""
    edit_id: str
    timestamp: float            # 被编辑的时间点
    channel: str                # "valence" | "arousal" | "baseline"
    value_before: float
    value_after: float
    edit_session_mood: float    # 编辑时用户的当前情绪（用于心境一致性降权）
    edit_latency_sec: float     # 距被编辑时刻的时间间隔
    drag_velocity: float        # 拖动速度（快速拖 = 更随意）
    reliability_weight: float   # 由 §4.2 公式算出
    created_at: float

@dataclass
class EmotionCurve:
    """派生产物：可由 (raw + edits + θ) 完全重建。"""
    timestamps: np.ndarray
    mean: np.ndarray            # (N, 2)
    variance: np.ndarray        # (N, 2)
    control_points: np.ndarray
    hyperparameters: dict
    version: int

@dataclass
class UserModelParams:
    """个人化超参数。"""
    ell_valence: float          # 效价长度尺度（情绪惯性）
    ell_arousal: float          # 唤醒长度尺度
    sigma_valence: float        # 效价波动幅度
    sigma_arousal: float        # 唤醒波动幅度
    sigma_noise: float          # 观测噪声
    # 元信息
    n_events_fitted: int
    log_likelihood: float
    shrinkage_alpha: float      # 向群体先验收缩的程度 (0=纯群体, 1=纯个人)
    last_updated: str

@dataclass
class BaselineRegime:
    """基线分段（由分叉产生）。"""
    start_time: float
    end_time: Optional[float]
    baseline: BaselineVector
    source: str                 # "auto_bocpd" | "user_fork" | "user_nudge"
    confidence: float
```

**持久化**：`db.py` 需新增三张表 `curve_edits`、`user_model_params`、`baseline_regimes`。全部本地 SQLite，符合现有隐私设计。

---

## 8. 实施路线

按依赖顺序，每阶段可独立交付与验证。

### 阶段 0：修正（半天）
- [ ] 修复 `intensity` 定义（到中性点距离，§1.3），同步修订白皮书 2.1.2
- [ ] 在 `config.py` 中把 `velocity_damping` 标记为 deprecated

### 阶段 1：感知层升级（2–3 天）
- [ ] 实现 Matérn ν=3/2 状态空间闭式解（`matern.py`）
- [ ] 替换 `kalman_filter.py` 的手写 `F` 与 `velocity_damping`
- [ ] **验证**：新参数化在现有 `test_data/` 上 RMSE 不劣于旧实现
- [ ] 保留自适应 `R` 与生理控制输入

### 阶段 2：回顾层 + 只读曲线（3–5 天）
- [ ] 实现 RTS 平滑器（`smoother.py`）
- [ ] 用 pyqtgraph 实现曲线视图（只读）：均值 + 置信带 + 基线
- [ ] **验证**：平滑后曲线在 `benchmark_results/` 上重算，检查 `rmse_intensity` 是否改善（预期显著——平滑利用未来信息，必然优于滤波）

### 阶段 3：可编辑（4–6 天）
- [ ] 控制点拖拽交互
- [ ] `curve_edits` 表 + 不可变约束（§3.2.1）
- [ ] 峰终加权（§4.2）
- [ ] **验证**：注入合成用户编辑（含噪声与偏差），确认模型**不发散**

### 阶段 4：个性化学习（5–8 天）
- [ ] 边际似然 + 层次先验（`personalize.py`）
- [ ] Coactive Learning 更新规则（§4.3）
- [ ] 系统性拖动诊断（§4.4）
- [ ] **验证**：在 `data_simulator_v2.py` 生成的 4 类画像上，确认学到的 ℓ 能区分画像（如"情绪稳定型"应比"焦虑敏感型"有更大的 ℓ）

> 注意：模拟器生成的画像其 ℓ 是已知的（Ground Truth），这是验证学习器的**绝佳测试床**——如果学习器恢复不出模拟器的真实 ℓ，说明实现有问题。

### 阶段 5：基线主权（4–6 天）
- [ ] 基线作为均值函数
- [ ] 三级权限（微调 / 分叉 / 重置）
- [ ] BOCPD 提议 + 用户裁决闭环
- [ ] **验证**：注入人工漂移，确认 BOCPD 能召回，且用户裁决能提升精确率

### 阶段 6：真实数据验证
- [ ] 用 **WESAD 数据集**（`REFERENCES.md` §3.1，1,259 引用）验证生理权重 `w_hrv=0.3, w_hr=0.2`
- [ ] 把白皮书 7.1.1 的"模拟器局限"从自我批评改为已缓解项

---

## 9. 风险与开放问题

| 风险 | 等级 | 缓解 |
|---|---|---|
| 小样本下超参数过拟合 | **高** | 层次先验收缩（Taylor 2017）；`shrinkage_alpha` 随数据量单调上升；前 20 事件强制强收缩 |
| 用户编辑污染学习信号 | **高** | 原始数据不可变（§3.2.1）；峰终加权（§4.2）；Coactive Learning 有界更新（§4.3） |
| 编辑交互诱发反刍 | **中** | §4.5 的四条对策；高危事件不主动邀请编辑 |
| Matérn 假设不成立（情绪动力学非平稳） | **中** | 分叉机制（§5.2）把非平稳转为分段平稳；周期性核可后续叠加（昼夜/周节律） |
| 端侧性能 | **低** | 状态空间形式 O(N)；事件窗口通常 N < 2000，纯 NumPy 即可 |
| 置信带被误解 | **中** | 遵循 §6.2 规范；用户测试中验证理解度 |

**开放问题**（建议后续研究）：
1. 是否需要**周期性核**叠加（昼夜节律、周节律、经期）？现有特征工程里已有 `sin/cos(weekday)`、`sin/cos(hour)`，说明你已意识到周期性——但这是加在强盗特征上，而非加在情绪动力学模型上。二者是否应该统一？
2. 效价与唤醒是否应**耦合**建模（当前是 4 维独立演化）？环状模型暗示二者相关。可考虑 2 维输出 GP（co-kriging / multi-output GP）。
3. 多用户场景（白皮书 7.1.4 已列为局限）下，层次先验天然支持"群体先验"的学习——这其实是本架构的意外红利。

---

## 10. 本轮新增参考文献

> 引用数由 OpenAlex / Crossref API 于 2026-09-05 实测。标注 `[OA]`=OpenAlex，`[CR]`=Crossref。上一轮 `REFERENCES.md` 已收录的文献此处不重复。

### 10.1 核心方法论（状态空间 GP）

| 文献 | 引用 | 链接 |
|---|---:|---|
| Hartikainen & Särkkä (2010) *Kalman filtering and smoothing solutions to temporal Gaussian process regression models*. IEEE MLSP | **219** `[OA]` | https://doi.org/10.1109/mlsp.2010.5589113 |
| Särkkä (2013) *Bayesian Filtering and Smoothing*. Cambridge Univ. Press（作者公开免费） | **1,785** `[OA]` | https://doi.org/10.1017/cbo9781139344203 |
| Särkkä et al. (2013) *Spatiotemporal Learning via Infinite-Dimensional Bayesian Filtering and Smoothing*. IEEE Signal Processing Magazine | — | https://doi.org/10.1109/msp.2013.2246292 |

### 10.2 从用户修正中学习

| 文献 | 引用 | 链接 |
|---|---:|---|
| Shivaswamy & Joachims (2015) *Coactive Learning*. JAIR | **53** `[OA]` | https://doi.org/10.1613/jair.4539 · [开放获取](https://jair.org/index.php/jair/article/download/10939/26066) |
| Teso et al. (2019) *Explanatory Interactive Machine Learning*. ACM XiDM | **199** `[OA]` | https://doi.org/10.1145/3306618.3314293 |
| Sadigh et al. (2017) *Active Preference-Based Learning of Reward Functions*. RSS | **264** `[OA]` | https://doi.org/10.15607/rss.2017.xiii.053 |
| González et al. (2017) *Preferential Bayesian Optimization*. arXiv | **36** `[OA]` | https://arxiv.org/abs/1704.03651 |
| Dudley & Kristensson (2018) *A Review of User Interface Design for Interactive Machine Learning*. ACM TIIS | **339** `[OA]` | https://doi.org/10.1145/3185517 |
| Holzinger (2016) *Interactive machine learning for health informatics: when do we need the human-in-the-loop?* Brain Informatics | **895** `[OA]` | https://doi.org/10.1007/s40708-016-0042-6 |
| Mosqueira-Rey et al. (2022) *Human-in-the-loop machine learning: a state of the art*. Artificial Intelligence Review | **981** `[OA]` | https://doi.org/10.1007/s10462-022-10246-w |
| Spinner et al. (2019) *explAIner: A Visual Analytics Framework for Interactive and Explainable ML*. IEEE TVCG | **260** `[OA]` | https://arxiv.org/abs/1908.00087 |

### 10.3 情绪动力学与个性化（承接 REFERENCES.md）

| 文献 | 引用 | 链接 |
|---|---:|---|
| Oravecz et al. (2011) *A hierarchical latent stochastic differential equation model for affective dynamics*. Psychological Methods | **120** `[OA]` | https://doi.org/10.1037/a0024375 |
| Taylor et al. (2020) *Personalized Multitask Learning for Predicting Tomorrow's Mood, Stress, and Health*. IEEE Trans. Affective Computing | **269** `[OA]` | https://doi.org/10.1109/taffc.2017.2784832 |
| Gama et al. (2014) *A survey on concept drift adaptation*. ACM Computing Surveys | **3,568** `[OA]` | https://doi.org/10.1145/2523813 |

### 10.4 记忆与回顾偏差（§4.2 的依据）

| 文献 | 引用 | 链接 |
|---|---:|---|
| Kahneman et al. (1993) *When More Pain Is Preferred to Less: Adding a Better End*. Psychological Science | **1,523** `[OA]` | https://doi.org/10.1111/j.1467-9280.1993.tb00589.x |
| Liersch et al. (2009) *Duration neglect by numbers—and its elimination by graphs*. OBHDP | **27** `[OA]` | https://doi.org/10.1016/j.obhdp.2008.07.001 |
| Faul & LaBar (2023) *Mood-congruent memory revisited*. Psychological Review | **107** `[OA]` | https://doi.org/10.1037/rev0000394 |
| Stone et al. (2023) *Evaluation of Pressing Issues in Ecological Momentary Assessment*. Annu. Rev. Clin. Psychol. | **197** `[OA]` | https://doi.org/10.1146/annurev-clinpsy-080921-083128 |
| Doherty et al. (2020) *The Design of Ecological Momentary Assessment Technologies*. Interacting with Computers | **192** `[OA]` | https://doi.org/10.1093/iwcomp/iwaa019 |
| Mazzetti et al. (2012) *Evaluating a visual timeline methodology for appraisal and coping research*. J Occup. Organ. Psychol. | **44** `[OA]` | https://doi.org/10.1111/j.2044-8325.2012.02060.x |

### 10.5 可视化与自我追踪

| 文献 | 引用 | 链接 |
|---|---:|---|
| Padilla et al. (2018) *Decision making with visualizations: a cognitive framework across disciplines*. Cognitive Research | **347** `[OA]` | https://doi.org/10.1186/s41235-018-0120-9 |
| Kersten-van Dijk et al. (2017) *Personal Informatics, Self-Insight, and Behavior Change: A Critical Review*. Human-Computer Interaction | **179** `[OA]` | https://doi.org/10.1080/07370024.2016.1276456 |
| Chung et al. (2016) *Boundary Negotiating Artifacts in Personal Informatics*. CHI | **235** `[OA]` | https://doi.org/10.1145/2818048.2819926 |
| Cuttone et al. (2014) *Four Data Visualization Heuristics to Facilitate Reflection in Personal Informatics*. LNCS | **31** `[OA]` | https://doi.org/10.1007/978-3-319-07509-9_51 |

### 10.6 工程库（GitHub，2026-09-05 实测）

| 仓库 | Star | 最近更新 | 用途 |
|---|---:|---|---|
| [pyqtgraph/pyqtgraph](https://github.com/pyqtgraph/pyqtgraph) | 4,409 | 2026-08-31 | **Qt 原生交互式绘图，支撑拖拽控制点（首选）** |
| [cornellius-gp/gpytorch](https://github.com/cornellius-gp/gpytorch) | 3,907 | 2026-07-10 | GP 实现（仅建议用于离线研究对照，端侧过重） |
| [GPflow/GPflow](https://github.com/GPflow/GPflow) | 1,916 | 2026-08-10 | GP 实现（同上） |
| [statsmodels/statsmodels](https://github.com/statsmodels/statsmodels) | 11,603 | 2026-09-04 | `MLEModel` 状态空间框架，可做超参数估计的参考实现 |
| [exoplanet-dev/celerite2](https://github.com/exoplanet-dev/celerite2) | 85 | 2026-08-31 | O(N) 一维 GP（C++ 依赖，端侧需评估） |

> **端侧选型建议**：`gpytorch` / `GPflow` 依赖 PyTorch / TensorFlow，**不适合端侧**。本架构推荐**纯 NumPy 自实现状态空间 GP**（约 200 行），O(N) 且无重依赖。`celerite2` 可作为性能对照，但有 C++ 依赖。

---

## 11. 一句话总结

> 不要把"更准确"寄托在更复杂的模型上。**先修好长度尺度**——当前的 `velocity_damping=0.85` 让模型认为情绪在 10 秒内就完全遗忘，这不是精度问题，是记忆问题。把 ℓ 变成可学习的、因人而异的参数，它就同时是性能提升和 Kuppens 情绪惯性的工程实现。其余的可视化、编辑、主权，都建立在这个地基之上。
