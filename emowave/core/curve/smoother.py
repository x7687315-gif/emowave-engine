"""smoother — RTS（Rauch-Tung-Striebel）平滑器。

ARCHITECTURE part1 §3.2 L2 回顾层的核心算法。

为什么需要平滑而非滤波（part1 §0 摘要的架构冲突）：
  现有 Kalman 是**因果**的——只从过去推断现在，状态只有 x 和 P，不保存历史。
  而"拖动历史曲线"要求**非因果**的全局平滑：用户改了 10 分钟前的一个点，
  整条曲线都要跟着平滑地变。因果模型做不到这件事。

RTS 平滑是两遍算法，复杂度 O(N)（不是朴素 GP 的 O(N³)）：
  前向遍：Kalman 滤波，保存每步的 (x_pred, P_pred, x_filt, P_filt, F)
  后向遍：
    G_k     = P_filt_k · F_{k+1}ᵀ · P_pred_{k+1}⁻¹
    x_sm_k  = x_filt_k + G_k·(x_sm_{k+1} - x_pred_{k+1})
    P_sm_k  = P_filt_k + G_k·(P_sm_{k+1} - P_pred_{k+1})·G_kᵀ

依据：Hartikainen & Särkkä (2010)，Särkkä (2013) *Bayesian Filtering and
Smoothing*（part1 §2.2）。平滑解精确等价于状态空间高斯过程回归。

拖动体验为什么好（part1 §3.2）：RTS 平滑是非因果的，且 GP 后验全局耦合。
用户拖动一个控制点，整条曲线以核函数决定的光滑方式重新收敛——这就是
"拉函数图像"的手感。

性能（part2 §2.4）：RTS 是唯一 numpy 有实质优势的操作（纯 Python 172ms vs
numpy 36ms @ N=2000）。解法是降低 N：在节点网格（N≈300）上平滑而非全采样点，
纯 Python 降到 ~26ms，肉眼与 2000 点无差异。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from emowave.core import linalg
from emowave.core.domain.observation import Observation
from emowave.core.estimator.estimator import (
    EstimatorConfig,
    compute_observation_noise,
)
from emowave.core.estimator.matern import (
    OBSERVATION_MATRIX,
    build_process_noise,
    build_transition,
)
from emowave.core.domain.model_parameters import ModelParameters

_STATE_DIM = 4
_IDX_V = 0
_IDX_DV = 1
_IDX_A = 2
_IDX_DA = 3


@dataclass
class SmoothedTrajectory:
    """RTS 平滑的输出：每个时间点的后验均值与方差。

    Attributes:
        timestamps: (N,) 时间戳
        mean_valence: (N,) 平滑后效价后验均值
        mean_arousal: (N,) 平滑后唤醒后验均值
        var_valence: (N,) 效价后验方差（渲染置信带）
        var_arousal: (N,) 唤醒后验方差
        filtered_valence: (N,) 因果滤波的效价（对照用，平滑应优于此）
        filtered_arousal: (N,) 因果滤波的唤醒
    """

    timestamps: List[float] = field(default_factory=list)
    mean_valence: List[float] = field(default_factory=list)
    mean_arousal: List[float] = field(default_factory=list)
    var_valence: List[float] = field(default_factory=list)
    var_arousal: List[float] = field(default_factory=list)
    filtered_valence: List[float] = field(default_factory=list)
    filtered_arousal: List[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.timestamps)


class RTSSmoother:
    """RTS 平滑器：对一段 Observation 做非因果全局平滑。

    使用方式：
        smoother = RTSSmoother(params, config)
        traj = smoother.smooth(observations)
        # traj.mean_valence / traj.var_valence 用于渲染曲线 + 置信带

    与 StateEstimator 的区别：
      - StateEstimator 是因果的、O(1) 内存、实时逐点更新（L1 感知层）
      - RTSSmoother 是非因果的、O(N) 内存、批量处理一段历史（L2 回顾层）
    两者共用同一套 Matérn 状态空间参数化（part1 §2.2 核心定理）。
    """

    def __init__(
        self,
        params: Optional[ModelParameters] = None,
        config: Optional[EstimatorConfig] = None,
    ) -> None:
        self.params = params or ModelParameters()
        self.config = config or EstimatorConfig()

    def smooth(
        self,
        observations: List[Observation],
        edits: Optional[List["dict"]] = None,
    ) -> SmoothedTrajectory:
        """对一段观察做 RTS 平滑。

        Args:
            observations: 按时间正序的观察列表（至少 1 条）。
            edits: 可选的用户编辑（CurveEdit 列表），作为加权伪观察注入。
                每个 edit 是 dict：{timestamp, channel, value, reliability_weight}。
                编辑按其 reliability_weight 调节观测噪声（权重越低噪声越大），
                实现 part1 §4.2 峰终加权的"温和采纳/强烈采纳"差异。

        Returns:
            SmoothedTrajectory：平滑后验均值 + 方差 + 因果滤波对照。
        """
        if not observations:
            return SmoothedTrajectory()

        # 按时间排序（防御乱序输入）
        obs_sorted = sorted(observations, key=lambda o: o.timestamp)

        # 把编辑转为伪观察，合并进观察流
        all_obs = self._merge_edits(obs_sorted, edits or [])

        # ---------- 前向遍：Kalman 滤波，保存历史 ----------
        lam_v = self.params.lambda_valence()
        lam_a = self.params.lambda_arousal()

        # 初始状态
        x_filt = [
            self.config.initial_valence,
            0.0,
            self.config.initial_arousal,
            0.0,
        ]
        # 若首条观察带情绪通道，用其初始化位置（减少冷启动偏差）
        first = all_obs[0]
        if first.valence is not None:
            x_filt[_IDX_V] = first.valence
        if first.arousal is not None:
            x_filt[_IDX_A] = first.arousal
        p_filt = linalg.diag(
            [
                self.config.initial_position_var,
                self.config.initial_velocity_var,
                self.config.initial_position_var,
                self.config.initial_velocity_var,
            ]
        )

        # 每步保存：F_k（从 k-1 到 k 的转移）、x_pred_k、P_pred_k、x_filt_k、P_filt_k
        hist_F: List[List[List[float]]] = []
        hist_x_pred: List[List[float]] = []
        hist_P_pred: List[List[List[float]]] = []
        hist_x_filt: List[List[float]] = []
        hist_P_filt: List[List[List[float]]] = []
        timestamps: List[float] = []

        prev_ts = all_obs[0].timestamp
        for k, obs in enumerate(all_obs):
            if k == 0:
                # 首步：无预测，直接把初始状态当作 filtered
                dt = 0.0
                f_k = linalg.identity(_STATE_DIM)
                x_pred = list(x_filt)
                p_pred = [row[:] for row in p_filt]
            else:
                dt = obs.timestamp - prev_ts
                if dt < 0:
                    dt = 0.0
                dt = min(dt, 600.0)
                f_k = build_transition(lam_v, lam_a, dt)
                q_k = build_process_noise(
                    self.params.sigma_valence,
                    self.params.sigma_arousal,
                    lam_v,
                    lam_a,
                    dt,
                )
                x_pred = linalg.mul_vec(f_k, x_filt)
                x_pred[_IDX_V] = _clip(x_pred[_IDX_V])
                x_pred[_IDX_A] = _clip(x_pred[_IDX_A])
                p_pred = linalg.add(
                    linalg.mul(linalg.mul(f_k, p_filt), linalg.transpose(f_k)), q_k
                )

            # 观测更新
            x_filt_new, p_filt_new = self._observation_update(obs, x_pred, p_pred)

            hist_F.append(f_k)
            hist_x_pred.append(x_pred)
            hist_P_pred.append(p_pred)
            hist_x_filt.append(x_filt_new)
            hist_P_filt.append(p_filt_new)
            timestamps.append(obs.timestamp)

            x_filt = x_filt_new
            p_filt = p_filt_new
            prev_ts = obs.timestamp

        n = len(all_obs)

        # ---------- 后向遍：RTS 平滑 ----------
        x_smooth: List[List[float]] = [None] * n  # type: ignore
        p_smooth: List[List[List[float]]] = [None] * n  # type: ignore
        x_smooth[n - 1] = hist_x_filt[n - 1]
        p_smooth[n - 1] = hist_P_filt[n - 1]

        for k in range(n - 2, -1, -1):
            f_next = hist_F[k + 1]
            p_pred_next = hist_P_pred[k + 1]
            x_pred_next = hist_x_pred[k + 1]

            # G_k = P_filt_k · F_{k+1}ᵀ · P_pred_{k+1}⁻¹
            try:
                p_pred_next_inv = linalg.inv(p_pred_next)
            except ValueError:
                # P_pred 奇异（极罕见）：跳过平滑，用滤波值
                x_smooth[k] = hist_x_filt[k]
                p_smooth[k] = hist_P_filt[k]
                continue
            g_k = linalg.mul(
                linalg.mul(hist_P_filt[k], linalg.transpose(f_next)), p_pred_next_inv
            )

            # x_sm_k = x_filt_k + G_k·(x_sm_{k+1} - x_pred_{k+1})
            diff_x = linalg.vec_sub(x_smooth[k + 1], x_pred_next)
            x_smooth[k] = linalg.vec_add(hist_x_filt[k], linalg.mul_vec(g_k, diff_x))

            # P_sm_k = P_filt_k + G_k·(P_sm_{k+1} - P_pred_{k+1})·G_kᵀ
            diff_p = linalg.sub(p_smooth[k + 1], p_pred_next)
            p_smooth[k] = linalg.add(
                hist_P_filt[k],
                linalg.mul(linalg.mul(g_k, diff_p), linalg.transpose(g_k)),
            )

        # ---------- 组装输出 ----------
        traj = SmoothedTrajectory()
        traj.timestamps = timestamps
        for k in range(n):
            xs = x_smooth[k]
            ps = p_smooth[k]
            traj.mean_valence.append(_clip(xs[_IDX_V]))
            traj.mean_arousal.append(_clip(xs[_IDX_A]))
            traj.var_valence.append(max(0.0, ps[_IDX_V][_IDX_V]))
            traj.var_arousal.append(max(0.0, ps[_IDX_A][_IDX_A]))
            xf = hist_x_filt[k]
            traj.filtered_valence.append(_clip(xf[_IDX_V]))
            traj.filtered_arousal.append(_clip(xf[_IDX_A]))
        return traj

    def _observation_update(
        self,
        obs: Observation,
        x_pred: List[float],
        p_pred: List[List[float]],
    ) -> tuple:
        """单步 Kalman 观测更新（与 StateEstimator._observation_update 同逻辑）。

        支持部分观察（只有 valence 或只有 arousal）。
        """
        if not obs.has_emotion_channel():
            # 纯生理观察：不做观测更新，返回预测值
            return list(x_pred), [row[:] for row in p_pred]

        sigma_r = compute_observation_noise(obs, self.config, self.params)
        r_scalar = sigma_r * sigma_r

        rows: List[List[float]] = []
        z: List[float] = []
        r_diag: List[float] = []
        if obs.valence is not None:
            rows.append(OBSERVATION_MATRIX[0])
            z.append(obs.valence)
            r_diag.append(r_scalar)
        if obs.arousal is not None:
            rows.append(OBSERVATION_MATRIX[1])
            z.append(obs.arousal)
            r_diag.append(r_scalar)

        h = rows
        r = linalg.diag(r_diag)
        p_ht = linalg.mul(p_pred, linalg.transpose(h))
        s = linalg.add(linalg.mul(h, p_ht), r)
        s_inv = linalg.inv(s)
        k = linalg.mul(p_ht, s_inv)
        hx = linalg.mul_vec(h, x_pred)
        y = linalg.vec_sub(z, hx)
        x_new = linalg.vec_add(x_pred, linalg.mul_vec(k, y))

        kh = linalg.mul(k, h)
        i_kh = linalg.sub(linalg.identity(_STATE_DIM), kh)
        term1 = linalg.mul(linalg.mul(i_kh, p_pred), linalg.transpose(i_kh))
        term2 = linalg.mul(linalg.mul(k, r), linalg.transpose(k))
        p_new = linalg.add(term1, term2)

        x_new[_IDX_V] = _clip(x_new[_IDX_V])
        x_new[_IDX_A] = _clip(x_new[_IDX_A])
        return x_new, p_new

    def _merge_edits(
        self, observations: List[Observation], edits: List[dict]
    ) -> List[Observation]:
        """把用户编辑作为加权伪观察合并进观察流。

        part1 §4.2 峰终加权的工程实现：编辑按 reliability_weight 调节观测噪声——
        权重高（峰值/结尾/近期编辑）→ 噪声低 → 强烈采纳；
        权重低（平淡中段/久远编辑）→ 噪声高 → 温和采纳。

        编辑不覆盖原始观察（part1 §3.2.1 原始数据不可变），而是作为
        **额外的**伪观察插入时间序列，与原始观察共同参与平滑。
        """
        if not edits:
            return list(observations)

        merged = list(observations)
        for e in edits:
            ts = float(e.get("timestamp", 0.0))
            channel = e.get("channel", "valence")
            value = float(e.get("value", 0.5))
            weight = float(e.get("reliability_weight", 1.0))
            weight = max(0.0, min(1.0, weight))

            # 权重 → confidence 映射：confidence 越高噪声越低（见 compute_observation_noise）
            # weight=1 → confidence=1（σ×1.0），weight=0 → confidence=0（σ×2.0）
            meta = dict(e.get("meta", {}))
            meta["is_user_edit"] = True
            pseudo = Observation(
                timestamp=ts,
                valence=value if channel == "valence" else None,
                arousal=value if channel == "arousal" else None,
                source="user",
                confidence=weight,
                meta=meta,
            )
            merged.append(pseudo)

        merged.sort(key=lambda o: o.timestamp)
        return merged


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    x = float(x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
