"""
kalman_filter.py — 心潮 EmoWave 实时情绪状态估计器 · 自适应卡尔曼滤波器

本模块实现一个融合主观滑条与可选手表数据的实时情绪状态估计器。

核心设计：
  - 状态向量：[valence, arousal, d_valence/dt, d_arousal/dt]
    前 2 维是位置（效价、唤醒），后 2 维是变化率（速度）
  - 状态转移模型：匀速运动 + 随机游走加速度
    即当前帧的 valence ≈ 上一帧的 valence + d_valence * dt + 噪声
  - 观测模型：直接观测 (valence, arousal)，带自适应噪声
  - 控制输入（可选）：手表数据作为唤醒变化率的先验

算法选择理由：
  卡尔曼滤波器在以下场景中是最优线性估计器：
    1. 观测数据稀疏且带噪声（滑条每秒~1次，用户操作不稳定）
    2. 存在辅助连续信号（手表心率/HRV）
    3. 需要在线实时更新（O(n²) 复杂度，n=4，非常轻量）
  相比简单的指数平滑，卡尔曼滤波器能：
    - 利用速度维度的惯性来预测短暂观测缺失期间的轨迹
    - 通过自适应噪声协方差动态平衡主观 vs 生理信号的权重

替换指南：
  - 若需非线性模型（如情绪的饱和效应），可替换为 Extended KF 或 UKF
  - 若需更鲁棒的异常值处理，可在观测更新前加一层 Mahalanobis 距离门控
  - config.py 中的所有参数均可调，预留了 A/B 测试和 RL 调参接口
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Tuple


# ================================================================
# 配置参数（与 config.py 分离，便于此模块独立运行）
# ================================================================

@dataclass
class KalmanConfig:
    """
    卡尔曼滤波器的可调参数。
    所有参数均可通过 A/B 测试框架或 RL 调参器在线修改。
    """

    # --- 过程噪声 Q ---
    #   控制模型对"状态可以自由变化多大"的假设。
    #   Q 越大，滤波器越信任观测而非模型预测；Q 越小，滤波器越平滑。
    #   位置维度的过程噪声：表示 valence/arousal 的随机加速度标准差
    q_position_std: float = 0.02       # 效价/唤醒位置噪声标准差
    #   速度维度的过程噪声：表示 d_valence/d_arousal 的随机变化标准差
    q_velocity_std: float = 0.06       # 效价/唤醒速度噪声标准差

    # --- 速度阻尼 ---
    #   每次观测更新后，对速度维度施加阻尼。
    #   理由：情绪变化不像物理运动那样有惯性——用户的滑条操作
    #   更像是"瞬态控制"，上一秒的速度不应强烈影响下一秒。
    #   阻尼因子 0.85 = 每秒保留 85% 的速度，衰减较快
    velocity_damping: float = 0.85
    #   位置-速度交叉耦合（通常设为 0，除非有理论依据）
    q_cross: float = 0.0

    # --- 基础观测噪声 R ---
    #   滑条观测的基础噪声标准差
    r_base_std: float = 0.08           # 基础观测噪声（滑条缓慢移动时）
    r_jump_std: float = 0.25           # 跳变噪声（滑条长时间静止后突然跳变）
    r_fast_std: float = 0.04           # 快速拖动噪声（用户积极控制时噪声更低）

    # --- 判定阈值 ---
    #   用于 compute_R_from_interaction() 中的交互行为分类
    jump_velocity_threshold: float = 0.3    # 速度超过此值视为"快速移动"
    stillness_threshold_sec: float = 3.0    # 停顿超过此秒数视为"静止跳变"
    fast_touch_window_sec: float = 2.0      # 此窗口内的触摸视为"最近触摸"

    # --- 生理信号质量控制 ---
    #   HRV 信号质量降低时的过程噪声放大系数
    hrv_poor_quality_factor: float = 3.0     # 信号质量差时 Q 放大倍数
    hrv_good_quality_rssi: float = -60.0    # 手表 RSSI > 此值视为信号好

    # --- 预测外推参数 ---
    extrapolation_horizon_sec: float = 600.0  # 最大外推时间（10 分钟）
    prediction_dt_sec: float = 1.0           # 外推步长（秒）

    # --- 预警提前量约束 ---
    min_lead_time_sec: float = 30.0           # 最小预警提前量（给用户反应时间）
    max_lead_time_sec: float = 180.0          # 最大预警提前量（避免过早打扰）

    # --- 生理控制输入权重 ---
    #   用于将 HRV 变化率作为唤醒速度的先验
    hrv_control_weight: float = 0.3          # HRV 控制输入的权重
    hr_control_weight: float = 0.2           # HR 控制输入的权重

    # --- A/B 测试 & RL 接口预留 ---
    #   将来可通过这些字段标识不同的参数配置
    ab_test_variant: str = "default"
    rl_param_version: int = 0

    @property
    def Q_matrix(self) -> np.ndarray:
        """构建 4x4 过程噪声协方差矩阵。"""
        q_p = self.q_position_std ** 2
        q_v = self.q_velocity_std ** 2
        q_c = self.q_cross
        return np.array([
            [q_p, 0,   q_c, 0  ],
            [0,   q_p, 0,   q_c],
            [q_c, 0,   q_v, 0  ],
            [0,   q_c, 0,   q_v],
        ]) * 0.5  # 0.5 是 dt=1s 时的离散化缩放

    @property
    def R_base(self) -> np.ndarray:
        """构建 2x2 基础观测噪声矩阵。"""
        r = self.r_base_std ** 2
        return np.array([
            [r, 0],
            [0, r],
        ])


# ================================================================
# 数据结构
# ================================================================

@dataclass
class SliderObservation:
    """
    一次滑条观测数据。
    由 UI 层的触摸事件产生。
    """
    timestamp: float         # Unix 时间戳（秒）
    valence: float           # 效价（0-1）
    arousal: float           # 唤醒（0-1）
    touch_velocity: float = 0.0   # 滑条移动速度（单位/秒），由 UI 层计算
    seconds_since_last_touch: float = 0.0  # 距上次触摸的间隔（秒）


@dataclass
class PhysioInput:
    """
    生理信号控制输入（可选）。
    由手表蓝牙连接提供。
    """
    timestamp: float
    hrv_drop_ratio: float = 0.0      # HRV 相对于基线的下降比例（0 = 无变化，正值 = 下降）
    hr_change: float = 0.0           # 心率相对于基线的变化（BPM，正 = 上升）
    signal_quality: float = 1.0      # 信号质量（0-1，1 = 完美）


@dataclass
class EmotionState:
    """
    滤波器输出的情绪状态估计。
    """
    timestamp: float
    valence: float           # 平滑后的效价
    arousal: float           # 平滑后的唤醒
    d_valence: float         # 效价变化率（单位/秒）
    d_arousal: float         # 唤醒变化率（单位/秒）
    intensity: float         # 情绪强度 = sqrt(v² + a²)，已归一化到 [0, 1]
    intensity_dot: float     # 强度变化率（单位/秒）
    covariance_trace: float  # 协方差矩阵的迹，反映估计不确定性


# ================================================================
# 自适应噪声计算
# ================================================================

def compute_R_from_interaction(
    obs: SliderObservation,
    config: KalmanConfig,
) -> np.ndarray:
    """
    根据用户交互行为动态计算观测噪声矩阵 R。

    策略：
      - 快速移动中（touch_velocity 高）→ 噪声低（用户在积极控制）
      - 长时间静止后突然跳变 → 噪声高（可能是误触或延迟补录）
      - 正常交互 → 使用基础噪声

    设计理由：
      滑条的本质是一个"人在回路"的测量系统。
      当用户积极拖动时，每个采样点都携带真实意图信息，噪声低；
      当滑条静止许久后突然跳到一个远处的位置，这个点很可能是
      用户在回顾性补录，或者手滑误触，噪声应显著增大。
    """
    velocity = obs.touch_velocity
    stillness = obs.seconds_since_last_touch

    if velocity > config.jump_velocity_threshold:
        # 快速移动：用户在积极控制，噪声降低
        sigma = config.r_fast_std
    elif stillness > config.stillness_threshold_sec:
        # 长时间静止后跳变：噪声增大
        # 跳变幅度越大，噪声越高
        jump_penalty = min(2.0, stillness / config.stillness_threshold_sec)
        sigma = config.r_jump_std * jump_penalty
    else:
        # 正常交互
        sigma = config.r_base_std

    r = sigma ** 2
    return np.array([
        [r, 0],
        [0, r],
    ])


def compute_Q_from_physio_quality(
    base_Q: np.ndarray,
    physio_quality: float,
    config: KalmanConfig,
) -> np.ndarray:
    """
    根据生理信号质量调节过程噪声 Q。

    当手表信号质量差（接触不良、RSSI 低）时，增大过程噪声，
    使滤波器更依赖主观滑条而非不可靠的生理先验。

    Args:
        base_Q: 基础过程噪声矩阵
        physio_quality: 信号质量（0-1）
        config: 配置参数

    Returns:
        调整后的过程噪声矩阵
    """
    if physio_quality < 0.5:
        # 信号质量差，放大 Q
        factor = 1.0 + (config.hrv_poor_quality_factor - 1.0) * (1.0 - physio_quality * 2)
        return base_Q * max(1.0, factor)
    return base_Q


# ================================================================
# 卡尔曼滤波器
# ================================================================

class EmotionKalmanFilter:
    """
    情绪状态卡尔曼滤波器。

    状态向量 x = [valence, arousal, d_valence, d_arousal]^T
    观测向量 z = [valence, arousal]^T

    使用方式：
      kf = EmotionKalmanFilter(config)
      state = kf.init(valence=0.5, arousal=0.3)

      # 每收到一个滑条观测，更新滤波器
      state = kf.update(obs)

      # 可选：融入生理控制输入
      state = kf.update_with_control(obs, physio)
    """

    def __init__(self, config: Optional[KalmanConfig] = None):
        self.config = config or KalmanConfig()
        self._x = np.zeros(4)      # 状态向量
        self._P = np.eye(4) * 0.1  # 状态协方差矩阵

    @property
    def state_vector(self) -> np.ndarray:
        return self._x.copy()

    @property
    def covariance(self) -> np.ndarray:
        return self._P.copy()

    # ============================================================
    # 初始化
    # ============================================================

    def init(self, valence: float = 0.5, arousal: float = 0.3) -> EmotionState:
        """
        用初始状态初始化滤波器。

        Args:
            valence: 初始效价（默认中性偏正）
            arousal: 初始唤醒（默认中等偏低）
        """
        self._x = np.array([valence, arousal, 0.0, 0.0], dtype=float)
        self._P = np.diag([0.05, 0.05, 0.02, 0.02])
        return self._to_state(0.0)

    # ============================================================
    # 预测步骤（时间更新）
    # ============================================================

    def _predict(self, dt: float = 1.0, Q: Optional[np.ndarray] = None) -> None:
        """
        卡尔曼滤波器预测步骤。

        状态转移模型（匀速运动 + 离散化）：
          x[k+1] = F * x[k] + w,  w ~ N(0, Q)

          F = [1  0  dt  0 ]    （位置 = 旧位置 + 速度 * dt）
              [0  1  0   dt]
              [0  0  1   0 ]    （速度保持不变 + 过程噪声）
              [0  0  0   1 ]

        Args:
            dt: 时间步长（秒）
            Q: 过程噪声矩阵（可选，默认使用 config 中的值）
        """
        F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0,  dt],
            [0, 0, 1,  0],
            [0, 0, 0,  1],
        ])

        if Q is None:
            Q = self.config.Q_matrix * dt  # Q 按 dt 缩放

        # 状态预测
        self._x = F @ self._x
        # 协方差预测
        self._P = F @ self._P @ F.T + Q

        # 约束状态范围（valence/arousal 必须在 [0, 1]）
        self._x[0] = np.clip(self._x[0], 0, 1)
        self._x[1] = np.clip(self._x[1], 0, 1)

    # ============================================================
    # 更新步骤（观测更新）
    # ============================================================

    def update(self, obs: SliderObservation) -> EmotionState:
        """
        融入滑条观测数据，更新状态估计。

        观测模型：
          z = H * x + v,  v ~ N(0, R)
          H = [1  0  0  0]    （观测到位置，不观测速度）
              [0  1  0  0]

        Args:
            obs: 滑条观测数据

        Returns:
            更新后的 EmotionState
        """
        H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ])

        # 预测
        dt = 1.0  # 假设每个观测间隔约 1 秒
        self._predict(dt)

        # 自适应观测噪声
        R = compute_R_from_interaction(obs, self.config)

        # 卡尔曼增益
        S = H @ self._P @ H.T + R  # 新息协方差
        K = self._P @ H.T @ np.linalg.inv(S)

        # 新息（观测 - 预测的观测）
        z = np.array([obs.valence, obs.arousal])
        y = z - H @ self._x

        # 状态更新
        self._x = self._x + K @ y
        # 协方差更新（Joseph 形式，数值更稳定）
        I_KH = np.eye(4) - K @ H
        self._P = I_KH @ self._P @ I_KH.T + K @ R @ K.T

        # 约束
        self._x[0] = np.clip(self._x[0], 0, 1)
        self._x[1] = np.clip(self._x[1], 0, 1)

        # 速度阻尼：衰减速度维度，抑制惯性过冲
        self._x[2] *= self.config.velocity_damping
        self._x[3] *= self.config.velocity_damping

        return self._to_state(obs.timestamp)

    def update_with_control(
        self,
        obs: SliderObservation,
        physio: Optional[PhysioInput] = None,
    ) -> EmotionState:
        """
        融入滑条观测 + 生理控制输入。

        控制输入模型：
          将 HRV 下降和心率变化作为唤醒变化率的先验，
          通过调整状态转移中的速度分量来融入。

          u = [0, 0, 0, control_arousal]^T
          x[k+1] = F * x[k] + B * u + w

          control_arousal = w_hrv * hrv_drop + w_hr * hr_change

        Args:
            obs: 滑条观测
            physio: 生理信号输入（可选）

        Returns:
            更新后的 EmotionState
        """
        dt = 1.0
        Q = self.config.Q_matrix * dt

        # 根据生理信号质量调整过程噪声
        if physio is not None:
            Q = compute_Q_from_physio_quality(Q, physio.signal_quality, self.config)

        # 控制输入
        B = np.array([
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 1],  # 控制输入影响 d_arousal
        ])

        if physio is not None and physio.signal_quality > 0.3:
            control_arousal = (
                self.config.hrv_control_weight * physio.hrv_drop_ratio
                + self.config.hr_control_weight * physio.hr_change / 100.0
            )
        else:
            control_arousal = 0.0

        u = np.array([0, 0, 0, control_arousal])

        # 预测（含控制输入）
        F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0,  dt],
            [0, 0, 1,  0],
            [0, 0, 0,  1],
        ])

        self._x = F @ self._x + B @ u
        self._P = F @ self._P @ F.T + Q

        self._x[0] = np.clip(self._x[0], 0, 1)
        self._x[1] = np.clip(self._x[1], 0, 1)

        # 观测更新
        H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ])
        R = compute_R_from_interaction(obs, self.config)
        S = H @ self._P @ H.T + R
        K = self._P @ H.T @ np.linalg.inv(S)
        z = np.array([obs.valence, obs.arousal])
        y = z - H @ self._x
        self._x = self._x + K @ y
        I_KH = np.eye(4) - K @ H
        self._P = I_KH @ self._P @ I_KH.T + K @ R @ K.T

        self._x[0] = np.clip(self._x[0], 0, 1)
        self._x[1] = np.clip(self._x[1], 0, 1)

        # 速度阻尼
        self._x[2] *= self.config.velocity_damping
        self._x[3] *= self.config.velocity_damping

        return self._to_state(obs.timestamp)

    # ============================================================
    # 外推（用于预警）
    # ============================================================

    def extrapolate(self, horizon_sec: float, dt: float = 1.0) -> List[EmotionState]:
        """
        从当前状态进行短期外推（不做观测更新）。

        仅使用状态转移模型，模拟未来情绪轨迹。
        用于预警引擎判断是否会进入危险区。

        Args:
            horizon_sec: 外推时长（秒）
            dt: 外推步长（秒）

        Returns:
            外推轨迹上的状态点列表
        """
        x_save = self._x.copy()
        P_save = self._P.copy()

        Q = self.config.Q_matrix * dt
        trajectory = []
        steps = int(horizon_sec / dt)

        t = 0.0
        for _ in range(steps):
            self._predict(dt)
            t += dt
            trajectory.append(self._to_state(t))

        # 恢复状态（外推不应改变滤波器内部状态）
        self._x = x_save
        self._P = P_save

        return trajectory

    # ============================================================
    # 内部工具
    # ============================================================

    def _to_state(self, timestamp: float) -> EmotionState:
        """
        将内部状态向量转换为 EmotionState。
        """
        v = float(self._x[0])
        a = float(self._x[1])
        dv = float(self._x[2])
        da = float(self._x[3])

        # 情绪强度：使用极坐标映射
        #   intensity = sqrt(v² + a²) / sqrt(2)，归一化到 [0, 1]
        intensity = min(1.0, np.sqrt(v ** 2 + a ** 2) / np.sqrt(2))

        # 强度变化率（链式法则）
        #   d(intensity)/dt = (v*dv + a*da) / (sqrt(2) * intensity)
        denom = np.sqrt(2) * intensity + 1e-9
        intensity_dot = (v * dv + a * da) / denom

        return EmotionState(
            timestamp=timestamp,
            valence=round(v, 4),
            arousal=round(a, 4),
            d_valence=round(dv, 4),
            d_arousal=round(da, 4),
            intensity=round(intensity, 4),
            intensity_dot=round(intensity_dot, 4),
            covariance_trace=round(float(np.trace(self._P)), 6),
        )
