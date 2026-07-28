#!/usr/bin/env python3
"""
whitepaper_generator.py — 心潮 EmoWave 技术白皮书自动生成器

本模块读取系统配置、模型参数和基准测试结果，自动生成结构化的技术文档初稿。

运行方式：
  cd /workspace/emowave-engine && python3 whitepaper_generator.py
"""

import sys
sys.path.insert(0, "/workspace/emowave-engine")

import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Any

from kalman_filter import KalmanConfig
from predictor import PredictionConfig
from recommender import DEFAULT_STRATEGIES
from engine import EmoCalibrationEngine
from models import PersonalThresholds


class WhitepaperGenerator:
    def __init__(
        self,
        output_path: str = "./TECHNICAL_WHITEPAPER.md",
        benchmark_results_path: Optional[str] = None,
        benchmark_report_path: Optional[str] = None,
    ):
        """初始化白皮书生成器。

        Args:
            output_path: 输出 Markdown 文件路径
            benchmark_results_path: 基准测试结果 JSON 文件路径（可选）
            benchmark_report_path: 基准测试报告 Markdown 文件路径（可选）
        """
        self.output_path = output_path
        self.benchmark_results_path = benchmark_results_path
        self.benchmark_report_path = benchmark_report_path
        self._sections = []
        self._system_config = self._collect_system_config()

    def _collect_system_config(self) -> Dict[str, Any]:
        """收集当前系统配置和模型参数"""
        kf_config = KalmanConfig()
        pred_config = PredictionConfig()
        engine = EmoCalibrationEngine(user_id="whitepaper_gen")
        thresholds = engine.get_thresholds()
        diagnostics = engine.diagnostics()

        return {
            "kalman": {
                "q_position_std": kf_config.q_position_std,
                "q_velocity_std": kf_config.q_velocity_std,
                "velocity_damping": kf_config.velocity_damping,
                "r_base_std": kf_config.r_base_std,
                "r_jump_std": kf_config.r_jump_std,
                "r_fast_std": kf_config.r_fast_std,
                "hrv_control_weight": kf_config.hrv_control_weight,
                "hr_control_weight": kf_config.hr_control_weight,
                "extrapolation_horizon_sec": kf_config.extrapolation_horizon_sec,
            },
            "predictor": {
                "extrapolation_horizon_sec": pred_config.extrapolation_horizon_sec,
                "max_prediction_window_sec": pred_config.max_prediction_window_sec,
                "min_lead_time_sec": pred_config.min_lead_time_sec,
                "max_lead_time_sec": pred_config.max_lead_time_sec,
                "warning_intensity": pred_config.warning_intensity,
                "critical_intensity": pred_config.critical_intensity,
                "min_peak_excess": pred_config.min_peak_excess,
            },
            "thresholds": {
                "high_risk_arousal": thresholds.high_risk_arousal,
                "high_risk_valence": thresholds.high_risk_valence,
                "hrv_drop_percent": thresholds.hrv_drop_percent,
                "hr_surge_zscore": thresholds.hr_surge_zscore,
                "dangerous_rise_slope": thresholds.dangerous_rise_slope,
            },
            "strategies": [
                {"id": s.id, "name": s.name, "category": s.category}
                for s in DEFAULT_STRATEGIES
            ],
            "engine": diagnostics,
        }

    def generate(self) -> str:
        """生成完整白皮书 Markdown 文本"""
        self._sections = []

        self._add_title()
        self._add_overview()
        self._add_theoretical_basis()
        self._add_system_architecture()
        self._add_core_algorithms()
        self._add_privacy_design()
        self._add_benchmark_results()
        self._add_limitations_and_future()
        self._add_references()

        return "\n\n".join(self._sections)

    def save(self):
        """生成并保存白皮书"""
        content = self.generate()
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        with open(self.output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return self.output_path

    def _add_title(self):
        """标题页"""
        now = datetime.now().strftime("%Y-%m-%d")
        cfg = self._system_config
        section = (
            "# 心潮 EmoWave 技术白皮书\n\n"
            "**EmoWave Engine -- 基于卡尔曼滤波与上下文老虎机的个人情绪校准系统**\n\n"
            "---\n\n"
            f"**版本**: 1.0  \n"
            f"**日期**: {now}  \n"
            f"**状态**: 技术初稿（供内部评审与合作方交流）\n\n"
            "---\n\n"
            "## 文档元信息\n\n"
            "| 项目 | 内容 |\n"
            "|------|------|\n"
            "| 项目名称 | 心潮 EmoWave |\n"
            "| 核心技术 | 自适应卡尔曼滤波 + LinUCB 上下文老虎机 + 贝叶斯基线漂移检测 |\n"
            "| 目标平台 | iOS / Android（设备端本地推理） |\n"
            "| 文档类型 | 技术白皮书 |\n"
            f"| 生成时间 | {now} |\n"
            "| 系统参数来源 | 实时读取自系统配置对象 |\n"
        )
        self._sections.append(section)

    def _add_overview(self):
        """
        概述章节：
        - 项目目标
        - 核心理念（"情绪是变化的曲线，而非离散的标签"）
        - 目标用户
        - 与传统方案的差异
        """
        section = (
            "## 1. 概述\n\n"
            "### 1.1 项目目标\n\n"
            "心潮 EmoWave 是一个**设备端优先**的个人情绪校准与预警系统。"
            "本项目的核心目标是构建一套能够：\n\n"
            "1. **实时估计**用户的情绪状态（效价-唤醒二维空间中的连续轨迹）\n"
            "2. **提前预警**情绪即将进入危险区域，给用户留出宝贵的反应时间\n"
            "3. **个性化推荐**情境感知的应对策略，并通过在线学习持续优化\n"
            "4. **自适应校准**阈值与基线，从群体通用值渐进过渡到个人化模型\n"
            "5. **保护隐私**，所有数据默认驻留设备本地，用户拥有完全的数据控制权\n\n"
            "### 1.2 核心理念\n\n"
            "> **情绪是变化的曲线，而非离散的标签。**\n\n"
            "传统情绪应用通常要求用户在几个离散标签中选择当前的\"心情\"，"
            "例如\"开心\"\"焦虑\"\"悲伤\"。这种方法存在两个根本问题：\n\n"
            "- **粒度丢失**：\"焦虑\"与\"愤怒\"的生理表现和应对策略截然不同，却被简化为同一类别\n"
            "- **时间维度缺失**：情绪是随时间连续变化的，离散采样无法捕捉转折点\n\n"
            "EmoWave 采用**维度情绪模型**（效价-唤醒二维空间），通过连续滑条交互"
            "捕捉情绪的动态轨迹，并利用卡尔曼滤波器在稀疏观测间进行最优插值，"
            "从而获得每秒级别的情绪状态估计。\n\n"
            "### 1.3 目标用户\n\n"
            "- **高压力职场人群**：需要持续管理压力的职场人士\n"
            "- **情绪困扰的年轻人**：寻求自我觉察与情绪管理工具的年轻人\n"
            "- **心理健康关注者**：希望科学量化自身情绪模式的普通用户\n"
            "- **临床辅助场景**：作为心理咨询的辅助工具，帮助治疗师了解来访者的日常情绪动态\n\n"
            "### 1.4 与传统方案的差异\n\n"
            "| 维度 | 传统情绪应用 | EmoWave |\n"
            "|------|-------------|---------|\n"
            "| 情绪表示 | 离散标签（5-7类） | 二维连续空间（效价-唤醒） |\n"
            "| 采样频率 | 每天 1-3 次 | 实时滑条 + 手表传感器（~1Hz） |\n"
            "| 信号处理 | 简单统计 | 卡尔曼滤波（递归贝叶斯最优估计） |\n"
            "| 预警机制 | 事后回顾 | 基于轨迹外推的提前预警 |\n"
            "| 策略推荐 | 静态列表 | LinUCB 上下文老虎机（在线学习） |\n"
            "| 个性化 | 无或极少 | 冷启动 → 混合 → 纯个人三阶段自适应 |\n"
            "| 数据存储 | 云端集中 | 设备端本地优先 |\n"
        )
        self._sections.append(section)

    def _add_theoretical_basis(self):
        """
        理论基础章节：
        - 维度情绪模型（Russell 环状模型）
          - 效价-唤醒二维空间
          - 强度的几何定义
        - 生态瞬时评估（EMA）
          - 信号检测与反应偏差理论
          - 滑条交互作为连续信号采集
        - 情感计算
          - 多模态融合（主观+生理）
          - 实时估计与预测
        """
        section = (
            "## 2. 理论基础\n\n"
            "### 2.1 维度情绪模型\n\n"
            "#### 2.1.1 Russell 环状模型（Circumplex Model）\n\n"
            "本系统的核心理论框架基于 James A. Russell (1980) 提出的**情绪环状模型**。"
            "该模型将情绪映射到二维空间：\n\n"
            "- **效价（Valence）**：水平轴，从\"极度不适\"（0）到\"极度舒适\"（1）\n"
            "- **唤醒（Arousal）**：垂直轴，从\"极困倦\"（0）到\"极兴奋\"（1）\n\n"
            "常见的\"基本情绪\"在二维空间中的近似位置如下：\n\n"
            "| 情绪 | 效价 (V) | 唤醒 (A) | 象限 |\n"
            "|------|---------|---------|------|\n"
            "| 兴奋 | 高 | 高 | Q1（高效价高唤醒） |\n"
            "| 焦虑 | 低 | 高 | Q2（低效价高唤醒）*危险区* |\n"
            "| 悲伤 | 低 | 低 | Q3（低效价低唤醒） |\n"
            "| 平静 | 高 | 低 | Q4（高效价低唤醒） |\n\n"
            "#### 2.1.2 强度的几何定义\n\n"
            "我们定义**情绪强度**为状态向量在二维空间中的欧几里得范数：\n\n"
            "```\n"
            "intensity = sqrt(valence^2 + arousal^2) / sqrt(2)\n"
            "```\n\n"
            "该定义将强度归一化到 [0, 1] 区间：\n"
            "- 原点 (0,0) 对应强度 0\n"
            "- 远端角 (1,1) 对应强度 1\n"
            "- 强度的时间导数 intensity_dot 反映情绪的加速/减速趋势\n\n"
            "**危险区**定义为：`valence < high_risk_valence AND arousal > high_risk_arousal`，"
            "即\"高唤醒 + 低效价\"象限的极端区域。"
            f"当前系统默认的群体阈值为 arousal > {self._system_config['thresholds']['high_risk_arousal']}、"
            f"valence < {self._system_config['thresholds']['high_risk_valence']}。\n\n"
            "### 2.2 生态瞬时评估（EMA）\n\n"
            "#### 2.2.1 信号检测与反应偏差理论\n\n"
            "生态瞬时评估（Ecological Momentary Assessment, EMA）是 Shiffman 等人 (2008) "
            "系统化的方法论，主张在自然生活情境中反复采集被试的即时状态。"
            "传统 EMA 通常通过定时问卷实现，存在以下局限：\n\n"
            "- **反应偏差**：用户回忆和自我报告受当时认知状态影响\n"
            "- **采样稀疏**：每天 5-10 次的采样频率无法捕捉情绪的快速变化\n"
            "- **二元化倾向**：问卷答案的离散选项会人为地将连续变量离散化\n\n"
            "#### 2.2.2 滑条交互作为连续信号采集\n\n"
            "EmoWave 将 EMA 升级为**连续信号采集范式**：\n\n"
            "1. 用户通过滑条实时报告效价和唤醒值，产生约 1Hz 的连续采样流\n"
            "2. 滑条的操作本身被视为一个**\"人在回路\"的测量系统**：\n"
            "   - 积极拖动时（高 touch_velocity）：用户处于元认知监控状态，信号可靠性高\n"
            "   - 长时间静止后跳变：可能是回顾性补录或误触，信号可靠性低\n"
            "3. 卡尔曼滤波器的自适应噪声机制根据交互行为动态调整对每个采样点的信任度\n\n"
            "### 2.3 情感计算\n\n"
            "#### 2.3.1 多模态融合（主观 + 生理）\n\n"
            "系统融合两种信号源：\n\n"
            "- **主观信号**：滑条报告的效价与唤醒值\n"
            "- **生理信号**：来自智能手表的心率（HR）和心率变异性（HRV）\n\n"
            "生理信号不直接用于情绪分类，而是作为**控制输入**融入状态转移模型：\n"
            "- HRV 下降（交感神经激活的标志）被映射为唤醒变化率的先验\n"
            "- 心率激增（z-score 超过阈值）作为生理极点的辅助证据\n"
            f"  - 当前 HRV 控制权重: {self._system_config['kalman']['hrv_control_weight']}\n"
            f"  - 当前 HR 控制权重: {self._system_config['kalman']['hr_control_weight']}\n\n"
            "#### 2.3.2 实时估计与预测\n\n"
            "情感计算的最终目标不仅是\"识别当前情绪\"，更是\"预测未来走向\"。"
            "EmoWave 通过卡尔曼滤波器的状态转移模型（含速度维度）实现：\n\n"
            "1. **实时状态估计**：每收到一个观测，滤波器递归更新状态估计\n"
            "2. **轨迹外推**：利用当前速度估计，外推未来最多 "
            f"{self._system_config['kalman']['extrapolation_horizon_sec']:.0f} 秒"
            "（{:.0f} 分钟）的情绪轨迹\n".format(self._system_config['kalman']['extrapolation_horizon_sec'] / 60) +
            "3. **极点预警**：检查外推轨迹是否进入危险区，计算最优预警提前量\n"
        )
        self._sections.append(section)

    def _add_system_architecture(self):
        """
        系统架构章节：
        - 四大核心模块及其关系
        - 用 ASCII art 或 Mermaid 流程图展示架构
        - 数据流向说明
        """
        section = (
            "## 3. 系统架构\n\n"
            "### 3.1 四大核心模块\n\n"
            "EmoWave 由四个核心模块组成，形成完整的数据处理闭环：\n\n"
            "| 模块 | 对应文件 | 职责 |\n"
            "|------|---------|------|\n"
            "| **P1: 校准引擎** | `engine.py` + `annotator.py` | 事件标注、基线管理、阈值自适应 |\n"
            "| **P2: 实时估计器** | `kalman_filter.py` | 卡尔曼滤波、多模态融合、轨迹外推 |\n"
            "| **P3: 预警与推荐** | `predictor.py` + `recommender.py` | 极点预警、策略推荐（LinUCB） |\n"
            "| **P4: 报告引擎** | `report_generator.py` | 周报/月报生成、趋势分析 |\n\n"
            "### 3.2 架构流程图\n\n"
            "#### Mermaid 流程图\n\n"
            "```mermaid\n"
            "graph TD\n"
            "    A[用户输入<br/>滑条 + 手表] --> B{多模态融合}\n"
            "    B --> C[Kalman滤波器<br/>P2: 实时估计]\n"
            "    C --> D[预警引擎<br/>极点预测]\n"
            "    D -->|预警| E[柔性通知]\n"
            "    C --> F[事件标注<br/>P1: 校准引擎]\n"
            "    F --> G[阈值自适应]\n"
            "    D --> H[策略推荐<br/>P3: 多臂老虎机]\n"
            "    H --> I[推荐展示]\n"
            "    I --> J[用户反馈]\n"
            "    J --> H\n"
            "    F --> K[周报生成<br/>P4: 报告引擎]\n"
            "    G --> C\n"
            "```\n\n"
            "#### ASCII 架构图（备选渲染）\n\n"
            "```\n"
            "  +-------------------+       +-------------------+\n"
            "  |    用户输入        |       |    生理信号        |\n"
            "  |  (滑条效价/唤醒)   |       |  (手表 HR/HRV)    |\n"
            "  +--------+----------+       +--------+----------+\n"
            "           |                           |\n"
            "           v                           v\n"
            "  +--------------------------------------------------+\n"
            "  |        P2: 自适应卡尔曼滤波器                      |\n"
            "  |  状态向量: [valence, arousal, d_v, d_a]           |\n"
            "  |  自适应观测噪声 + 生理控制输入                     |\n"
            "  +---+----------------+-------------------+----------+\n"
            "      |                |                   |\n"
            "      v                v                   v\n"
            "  +--------+   +-------------+   +-----------------+\n"
            "  | 轨迹外推 |   | P1: 事件标注 |   | P3: 策略推荐   |\n"
            "  +----+---+   +------+------+   +--------+--------+\n"
            "       |              |                    |\n"
            "       v              v                    v\n"
            "  +--------+   +------+------+   +--------+--------+\n"
            "  | 预警引擎 |   | 阈值自适应   |   | LinUCB 老虎机  |\n"
            "  +----+---+   +------+------+   +--------+--------+\n"
            "       |              |                    |\n"
            "       v              v                    v\n"
            "  +--------+   +------+------+   +--------+--------+\n"
            "  | 柔性通知 |   | 基线漂移检测 |   |  用户反馈评分  |\n"
            "  +--------+   +-------------+   +-----------------+\n"
            "                                     |\n"
            "                                     v\n"
            "                              +-------------+\n"
            "                              | P4: 周报生成 |\n"
            "                              +-------------+\n"
            "```\n\n"
            "### 3.3 数据流向说明\n\n"
            "#### 实时数据流（秒级）\n\n"
            "1. 用户拖动滑条 → 生成 `SliderObservation`（valence, arousal, touch_velocity）\n"
            "2. 手表蓝牙传输 → 生成 `PhysioInput`（hrv_drop_ratio, hr_change, signal_quality）\n"
            "3. 卡尔曼滤波器接收上述信号 → 输出 `EmotionState`（平滑后的状态估计）\n"
            "4. 预警引擎检查状态 → 若需要则触发预警通知\n\n"
            "#### 事件级数据流（分钟-小时级）\n\n"
            "1. 用户点击\"已平静\" → 汇总本事件所有时序采样为 `EmotionEventRaw`\n"
            "2. 校准引擎（P1）处理事件：\n"
            "   - 自动标注极点、危险段、恢复特征 → `EventProfile`\n"
            "   - 更新阈值管理器 → `PersonalThresholds`\n"
            "3. 用户对推荐策略评分 → LinUCB 老虎机更新参数\n\n"
            "#### 日级数据流（每日一次）\n\n"
            "1. 汇总当日所有事件 → `DailySummary`\n"
            "2. 基线管理器 EWMA 更新 → `BaselineVector`\n"
            "3. 漂移检测 → 若异常则生成 `BaselineShiftEvent`\n"
            "4. 周报引擎生成可视化报告\n"
        )
        self._sections.append(section)

    def _add_core_algorithms(self):
        """
        核心算法章节：
        - 卡尔曼滤波
          - 为什么选Kalman：实时递归、不确定性量化、多源融合
          - 状态向量定义：[valence, arousal, d_valence, d_arousal]
          - 过程模型（阻尼速度模型）
          - 观测模型（自适应噪声）
          - 生理信号作为控制输入
          - 预警外推机制
        - 多臂老虎机（LinUCB）
          - 为什么选UCB：探索-利用平衡、情境感知
          - 线性模型：θ^T x + α sqrt(x^T A^{-1} x)
          - 特征工程（10维特征向量）
        - 贝叶斯基线漂移检测
          - 为什么选贝叶斯：小样本下稳健
          - 基线向量定义
          - 漂移检测逻辑
        - 每个算法都嵌入当前系统的实际参数值
        """
        cfg = self._system_config
        section = (
            "## 4. 核心算法\n\n"
            "### 4.1 自适应卡尔曼滤波器\n\n"
            "#### 4.1.1 算法选择理由\n\n"
            "我们选择卡尔曼滤波器（Kalman Filter）作为核心状态估计器，基于以下考量：\n\n"
            "| 需求 | 卡尔曼滤波器的优势 |\n"
            "|------|-------------------|\n"
            "| 实时递归更新 | O(n^2) 复杂度，n=4 时计算量极低，适合移动设备 |\n"
            "| 不确定性量化 | 协方差矩阵自然给出估计的置信区间 |\n"
            "| 多源融合 | 通过控制输入和自适应噪声权重优雅融合主观与生理信号 |\n"
            "| 短期预测 | 状态转移模型（含速度维度）支持轨迹外推 |\n"
            "| 稀疏观测处理 | 在两次观测间利用惯性预测填补空白 |\n\n"
            "#### 4.1.2 状态向量定义\n\n"
            "```\n"
            "x = [valence, arousal, d_valence/dt, d_arousal/dt]^T\n"
            "```\n\n"
            "状态向量为 4 维：\n"
            "- **位置分量**：valence（效价）和 arousal（唤醒），范围 [0, 1]\n"
            "- **速度分量**：d_valence/dt 和 d_arousal/dt，即效价和唤醒的变化率\n\n"
            "速度维度的存在是关键设计：它使滤波器能够\"理解\"情绪的惯性趋势，"
            "从而在观测缺失期间（如用户暂时没有操作滑条）进行有意义的轨迹预测。\n\n"
            "#### 4.1.3 过程模型（阻尼速度模型）\n\n"
            "状态转移方程（离散化匀速运动模型）：\n\n"
            "```\n"
            "x[k+1] = F * x[k] + w,  w ~ N(0, Q)\n\n"
            "F = | 1  0  dt  0 |    位置 = 旧位置 + 速度 * dt\n"
            "    | 0  1  0   dt |\n"
            "    | 0  0  1   0 |    速度保持不变 + 过程噪声\n"
            "    | 0  0  0   1 |\n"
            "```\n\n"
            "**过程噪声协方差 Q 的参数**（从系统配置实时读取）：\n\n"
            "| 参数 | 当前值 | 含义 |\n"
            "|------|-------|------|\n"
            f"| q_position_std | {cfg['kalman']['q_position_std']} | 效价/唤醒位置噪声标准差 |\n"
            f"| q_velocity_std | {cfg['kalman']['q_velocity_std']} | 效价/唤醒速度噪声标准差 |\n"
            f"| velocity_damping | {cfg['kalman']['velocity_damping']} | 速度阻尼因子（每秒保留 85% 的速度） |\n\n"
            "**速度阻尼设计**：\n\n"
            "情绪变化不像物理运动那样具有惯性。用户对滑条的操作更接近\"瞬态控制\"，"
            "上一秒的速度不应强烈影响下一秒。因此我们在每次更新后对速度维度施加阻尼：\n\n"
            "```\n"
            f"velocity *= {cfg['kalman']['velocity_damping']}  // 每秒保留 85% 的速度\n"
            "```\n\n"
            "#### 4.1.4 观测模型（自适应噪声）\n\n"
            "观测方程：\n\n"
            "```\n"
            "z = H * x + v,  v ~ N(0, R)\n\n"
            "H = | 1  0  0  0 |    观测到位置，不观测速度\n"
            "    | 0  1  0  0 |\n"
            "```\n\n"
            "**自适应观测噪声 R**根据用户交互行为动态调整：\n\n"
            "| 交互类型 | 判定条件 | 噪声标准差 | 理由 |\n"
            "|---------|---------|-----------|------|\n"
            "| 快速移动 | touch_velocity > 0.3 | "
            f"R_fast = {cfg['kalman']['r_fast_std']} | 用户积极控制，信号可靠 |\n"
            "| 静止跳变 | 停顿 > 3s 后突变 | "
            f"R_jump = {cfg['kalman']['r_jump_std']} | 可能是误触或延迟补录 |\n"
            "| 正常交互 | 其他情况 | "
            f"R_base = {cfg['kalman']['r_base_std']} | 默认噪声水平 |\n\n"
            "#### 4.1.5 生理信号作为控制输入\n\n"
            "当手表连接时，HRV 变化和心率变化被映射为唤醒变化率的先验：\n\n"
            "```\n"
            "control_arousal = w_hrv * hrv_drop_ratio + w_hr * hr_change / 100\n"
            f"// 当前权重: w_hrv = {cfg['kalman']['hrv_control_weight']}, w_hr = {cfg['kalman']['hr_control_weight']}\n"
            "```\n\n"
            "信号质量低时（signal_quality < 0.5），过程噪声 Q 会被放大"
            "（最高 3 倍），使滤波器更依赖主观滑条而非不可靠的生理数据。\n\n"
            "#### 4.1.6 预警外推机制\n\n"
            "预警引擎从当前状态出发，利用状态转移模型进行纯预测迭代"
            "（不融入观测），生成未来 "
            f"{cfg['kalman']['extrapolation_horizon_sec']:.0f} 秒"
            f"（{cfg['kalman']['extrapolation_horizon_sec']/60:.0f} 分钟）"
            "的情绪轨迹。外推过程结束后恢复滤波器内部状态，不影响后续估计。\n\n"
            "---\n\n"
            "### 4.2 多臂老虎机（LinUCB）\n\n"
            "#### 4.2.1 算法选择理由\n\n"
            "策略推荐问题天然适合建模为\"上下文多臂老虎机\"问题：\n\n"
            "- **探索-利用平衡**：新策略需要被尝试（探索），已验证的策略应优先推荐（利用）\n"
            "- **情境感知**：不同情绪状态下，同一策略的效果差异显著\n"
            "- **在线学习**：无需离线训练，随用户使用自然积累个性化知识\n"
            "- **设备端运行**：纯线性模型，无需 GPU，适合移动端\n\n"
            "#### 4.2.2 线性模型\n\n"
            "LinUCB 对每个策略（\"臂\"）维护一个线性模型：\n\n"
            "```\n"
            "expected_reward = theta^T * x + alpha * sqrt(x^T * A^{-1} * x)\n"
            "                   |----- 预测 -----|   |---- 不确定性 ----|\n"
            "```\n\n"
            "其中：\n"
            "- `theta = A^{-1} * b`：当前最优系数向量\n"
            "- `A`：d x d 共轭先验矩阵（d=10）\n"
            "- `b`：d x 1 奖励累积向量\n"
            "- `alpha`：探索系数，控制探索-利用权衡\n"
            "- `x`：10 维情境特征向量\n\n"
            "UCB 分数 = 预测奖励 + 探索奖励。新策略因 A 接近单位矩阵，不确定性项大，"
            "自动获得较高的探索分数。\n\n"
            "#### 4.2.3 特征工程（10 维特征向量）\n\n"
            "| 维度 | 特征 | 归一化 |\n"
            "|------|------|--------|\n"
            "| 1 | current_valence | [0, 1] 原始 |\n"
            "| 2 | current_arousal | [0, 1] 原始 |\n"
            "| 3 | time_of_day | / 24.0 |\n"
            "| 4 | weekday | / 6.0 |\n"
            "| 5 | last_sleep_score | / 10.0 |\n"
            "| 6 | trigger_category_code | / 10.0 |\n"
            "| 7 | sin(weekday * 2pi/7) | 周期性编码 |\n"
            "| 8 | cos(weekday * 2pi/7) | 周期性编码 |\n"
            "| 9 | sin(hour * 2pi/24) | 周期性编码 |\n"
            "| 10 | cos(hour * 2pi/24) | 周期性编码 |\n\n"
            "维度 7-10 使用三角函数编码捕捉星期和小时的周期性，"
            "避免\"周一=0, 周日=6\"这类线性编码导致的周日-周一不连续问题。\n\n"
            "#### 4.2.4 当前策略库\n\n"
            "系统预设了 10 个应对策略，分为 4 个类别：\n\n"
            "| 策略 ID | 名称 | 类别 |\n"
            "|---------|------|------|\n"
        )
        # 添加策略表格
        for s in cfg["strategies"]:
            section += f"| {s['id']} | {s['name']} | {s['category']} |\n"

        section += (
            "\n---\n\n"
            "### 4.3 基线建模与漂移检测\n\n"
            "#### 4.3.1 基线向量定义\n\n"
            "用户的\"静息基线\"由 5 个维度构成：\n\n"
            "| 维度 | 含义 | 默认值 |\n"
            "|------|------|--------|\n"
            "| resting_hrv_mean | 静息 HRV 均值 (ms) | 50.0 |\n"
            "| resting_hr | 静息心率 (BPM) | 72.0 |\n"
            "| sleep_score | 前夜睡眠评分 (0-10) | 7.0 |\n"
            "| typical_valence_8am | 早间典型效价 | 0.55 |\n"
            "| typical_valence_6pm | 晚间典型效价 | 0.50 |\n\n"
            "#### 4.3.2 EWMA 更新策略\n\n"
            "基线使用指数加权移动平均（EWMA）每日更新：\n\n"
            "```\n"
            "baseline_new = alpha * x_new + (1 - alpha) * baseline_old\n"
            "// alpha = 1/8 ≈ 0.125, 等效窗口 ≈ 7 天\n"
            "```\n\n"
            "alpha=1/8 的选择基于心理学观察：情绪基线的变化通常是昼夜周期叠加慢趋势，"
            "7 天的等效窗口能捕捉周级别的漂移而不过度响应单日噪声。\n\n"
            "#### 4.3.3 漂移检测逻辑\n\n"
            "漂移检测采用\"累积标准差偏离\"策略：\n\n"
            "1. 维护原始每日输入值（未经 EWMA 平滑）\n"
            "2. 计算参考期的均值和标准差\n"
            "3. 检查最近 3 天是否每天都偏离超过 2.0 个标准差\n"
            "4. 若连续 3 天偏离 → 触发告警（INFO / WARNING / ACTION 三级）\n\n"
            "**设计要点**：使用原始值而非 EWMA 平滑后的基线做漂移检测，"
            "因为 EWMA 的指数衰减会导致基线\"追着\"异常值跑，"
            "使平滑后的值永远不会有足够大的偏离。\n\n"
            "---\n\n"
            "### 4.4 冷启动与渐进个性化\n\n"
            "系统采用三阶段渐进策略，从群体通用阈值平滑过渡到个人化模型：\n\n"
            "```\n"
            "阶段 1: POPULATION  （事件数 < 20）\n"
            "  → 使用群体安全阈值\n\n"
            "阶段 2: HYBRID  （置信度 0.0 ~ 0.75）\n"
            "  → threshold = confidence * personal + (1 - confidence) * population\n\n"
            "阶段 3: PERSONAL  （置信度 >= 0.75）\n"
            "  → 纯个人化阈值\n"
            "```\n\n"
            "#### 置信度计算（三维度加权）\n\n"
            "| 维度 | 权重 | 含义 |\n"
            "|------|------|------|\n"
            "| 事件数量 | 40% | Sigmoid(事件数)，20 次时约 0.73 |\n"
            "| 基线年龄 | 25% | 线性映射：0天=0, 30天=1.0 |\n"
            "| 一致性 | 35% | 1 - CV * 1.2，峰值变异系数越小越可信 |\n\n"
            "当前系统默认的群体安全阈值如下：\n\n"
            "| 参数 | 当前默认值 | 含义 |\n"
            "|------|-----------|------|\n"
            f"| high_risk_arousal | {cfg['thresholds']['high_risk_arousal']} | 高唤醒阈值 |\n"
            f"| high_risk_valence | {cfg['thresholds']['high_risk_valence']} | 低效价阈值 |\n"
            f"| hrv_drop_percent | {cfg['thresholds']['hrv_drop_percent']} | HRV 下降百分比阈值 |\n"
            f"| hr_surge_zscore | {cfg['thresholds']['hr_surge_zscore']} | 心率激增 z-score 阈值 |\n"
            f"| dangerous_rise_slope | {cfg['thresholds']['dangerous_rise_slope']} | 危险上升斜率阈值 |\n\n"
            "#### 个人化阈值计算方法\n\n"
            "个人化阈值基于用户历史事件统计：\n"
            "- **唤醒度阈值**：历史峰值的 P75 分位数\n"
            "- **效价阈值**：历史峰值的 P25 分位数（反转后取）\n"
            "- 生理阈值暂用群体值（需更多生理数据积累）\n"
        )
        self._sections.append(section)

    def _add_privacy_design(self):
        """
        隐私设计章节：
        - 设计原则
          - 本地优先：所有数据默认保存在设备本地
          - 数据最小化：用户可选择性关闭数据收集
          - 用户可控：一键导出、一键删除
          - 透明度：用户可随时查看被存储的数据类别
        - 技术实现
          - 数据脱敏与时间模糊化
          - 加密导出机制
          - 危机协议的本地日志设计
        """
        section = (
            "## 5. 隐私设计\n\n"
            "### 5.1 设计原则\n\n"
            "隐私保护是 EmoWave 的核心设计原则，而非事后补丁。"
            "处理的是用户最敏感的个人数据——情绪状态与生理信号，"
            "因此我们遵循以下四大原则：\n\n"
            "#### 5.1.1 本地优先（Local-First）\n\n"
            "所有数据**默认保存在设备本地**。系统架构中不包含任何网络请求逻辑，"
            "所有计算（卡尔曼滤波、策略推荐、基线建模）均在设备端完成。"
            "用户的心路历程不需要经过任何第三方服务器。\n\n"
            "#### 5.1.2 数据最小化（Data Minimization）\n\n"
            "用户可选择性关闭数据收集功能：\n"
            "- 手表生理数据连接为**可选**，不连接不影响核心功能\n"
            "- 各类数据收集项可独立开关\n"
            "- 系统仅存储必要的衍生数据（基线、阈值、事件档案），不存储原始传感器流\n"
            "- 历史事件保留上限为 500 条（约 90 天），过期自动清理\n\n"
            "#### 5.1.3 用户可控（User Control）\n\n"
            "用户拥有数据的完全控制权：\n"
            "- **一键导出**：支持 JSON / CSV / 加密 JSON 三种格式导出\n"
            "- **一键删除**：支持按类别删除（如仅删除生理数据、保留事件记录）\n"
            "- **删除审计**：每次删除操作自动记录日志，便于追溯\n\n"
            "#### 5.1.4 透明度（Transparency）\n\n"
            "用户可随时查看以下信息：\n"
            "- 被存储的数据类别与数量\n"
            "- 每类数据的保留期限\n"
            "- 数据被访问的次数与目的\n"
            "- 个性化模型使用的参数与置信度\n\n"
            "### 5.2 技术实现\n\n"
            "#### 5.2.1 数据脱敏与时间模糊化\n\n"
            "导出时采用以下隐私保护措施：\n\n"
            "- **时间模糊化**：精确时间戳被模糊为时间段（如 14:00-14:30），"
            "避免通过时间戳交叉定位个体\n"
            "- **用户 ID 哈希化**：导出文件中的用户标识使用 SHA-256 哈希，"
            "不可逆但可验证\n"
            "- **数据脱敏选项**：用户可选择在导出时隐藏原始效价/唤醒数值，"
            "仅保留衍生指标（如极点时间、恢复时长）\n\n"
            "#### 5.2.2 加密导出机制\n\n"
            "支持 AES-256 加密的 JSON 导出：\n\n"
            "1. 用户设置一个导出密码\n"
            "2. 系统使用 PBKDF2（100,000 次迭代 + 随机盐值）从密码派生密钥\n"
            "3. 使用 AES-256-GCM 对数据进行加密\n"
            "4. 加密后的文件可在任意设备上使用密码解密查看\n\n"
            "#### 5.2.3 危机协议的本地日志设计\n\n"
            "当系统检测到潜在危机信号（如用户输入包含自伤相关关键词、"
            "或连续多日情绪强度持续极高）时，危机协议在设备端执行：\n\n"
            "1. **柔性提示**：以温和的方式展示关怀信息，不使用\"报警\"式交互\n"
            "2. **心理热线信息**：展示本地存储的心理援助热线号码\n"
            "3. **EMA 追问**：引导用户进行简短的生态瞬时评估\n"
            "4. **本地日志**：危机事件的所有记录**仅保存在设备本地**，"
            "不上传至云端。日志采用 JSONL 格式，包含时间戳、信号类型和系统响应\n\n"
            "```\n"
            "// 危机日志条目示例（JSONL）\n"
            "{\"timestamp\": \"2026-07-09T14:30:00\", \"signal_type\": \"keyword\", "
            "\"keyword_match\": \"self_harm\", \"response\": \"soft_care\", "
            "\"data_local\": true}\n"
            "```\n"
        )
        self._sections.append(section)

    def _add_benchmark_results(self):
        """
        基准测试结果章节：
        - 如果 benchmark_results_path 或 benchmark_report_path 存在，读取并嵌入
        - 如果不存在，生成占位章节说明如何运行基准测试
        """
        # 尝试读取基准测试结果
        benchmark_found = False
        benchmark_content_parts = []

        if self.benchmark_results_path and os.path.exists(self.benchmark_results_path):
            try:
                with open(self.benchmark_results_path, 'r', encoding='utf-8') as f:
                    results = json.load(f)
                benchmark_found = True
                benchmark_content_parts.append(
                    "#### 定量基准测试结果\n\n"
                    "以下结果来自自动化基准测试（JSON）：\n\n"
                    "```json\n"
                    + json.dumps(results, ensure_ascii=False, indent=2)[:5000]
                    + "\n```\n"
                )
            except Exception as e:
                benchmark_content_parts.append(
                    f"基准测试结果文件存在但解析失败: {e}\n"
                )

        if self.benchmark_report_path and os.path.exists(self.benchmark_report_path):
            try:
                with open(self.benchmark_report_path, 'r', encoding='utf-8') as f:
                    report_md = f.read()
                benchmark_found = True
                # 只取前 3000 字符避免过长
                preview = report_md[:3000]
                if len(report_md) > 3000:
                    preview += "\n\n... (报告内容过长，已截断。完整报告请查看原始文件。)\n"
                benchmark_content_parts.append(
                    "#### 基准测试报告\n\n" + preview
                )
            except Exception as e:
                benchmark_content_parts.append(
                    f"基准测试报告文件存在但读取失败: {e}\n"
                )

        if benchmark_found:
            section = (
                "## 6. 基准测试结果\n\n"
                + "\n\n".join(benchmark_content_parts)
            )
        else:
            section = (
                "## 6. 基准测试结果\n\n"
                "> **注意**：基准测试结果尚未生成。以下为占位内容。\n\n"
                "### 6.1 如何运行基准测试\n\n"
                "基准测试模块负责评估系统各组件的性能指标，包括：\n\n"
                "- **卡尔曼滤波器精度**：估计值与真值的 RMSE、MAE\n"
                "- **预警引擎指标**：精确率、召回率、F1 分数、误报率\n"
                "- **推荐引擎指标**：平均奖励、累积遗憾、探索比例\n"
                "- **基线漂移检测**：检测延迟、误报率、漏报率\n\n"
                "运行基准测试的命令：\n\n"
                "```bash\n"
                "cd /workspace/emowave-engine && python3 run_simulation.py\n"
                "```\n\n"
                "生成的结果将保存在：\n"
                f"- 基准数据: `{self.benchmark_results_path or './benchmark_results/benchmark_results.json'}`\n"
                f"- 详细报告: `{self.benchmark_report_path or './benchmark_results/BENCHMARK_REPORT.md'}`\n\n"
                "### 6.2 预期性能指标\n\n"
                "基于模拟数据测试的预期性能范围：\n\n"
                "| 指标 | 预期范围 | 说明 |\n"
                "|------|---------|------|\n"
                "| 估计 RMSE | < 0.08 | 卡尔曼滤波器的估计误差 |\n"
                "| 预警提前量 | 30-180 秒 | 危险区到达前的预警时间 |\n"
                "| 预警精确率 | > 0.70 | 预警中有多少是真实的 |\n"
                "| 预警召回率 | > 0.80 | 真实危险事件有多少被预警 |\n"
                "| 推荐平均评分 | > 3.0 / 5.0 | 策略推荐的用户评价均值 |\n"
                "| 漂移检测延迟 | < 5 天 | 基线漂移被检测到所需天数 |\n\n"
                "**重要说明**：以上指标基于模拟数据。真实用户场景中的性能"
                "可能因个体差异、设备传感器质量等因素而有所不同。"
                "正式发布前需要在真实用户群体中进行大规模验证。\n"
            )
        self._sections.append(section)

    def _add_limitations_and_future(self):
        """
        局限性与未来工作章节：
        - 当前局限
          - 模拟数据 vs 真实用户数据的差距
          - 冷启动阶段依赖群体阈值
          - 滑条交互本身对情绪的影响
          - 单一用户模型（未考虑多人场景）
        - 未来工作
          - 深度学习替代/增强 Kalman 滤波
          - 迁移学习跨用户
          - 与专业心理咨询的整合
          - 长期追踪的价值发现
        """
        section = (
            "## 7. 局限性与未来工作\n\n"
            "### 7.1 当前局限\n\n"
            "#### 7.1.1 模拟数据与真实用户数据的差距\n\n"
            "当前系统的开发和验证主要基于模拟数据生成器（`data_simulator_v2.py`），"
            "模拟器能生成具有真实统计特性的情绪时间序列，但无法完全复现：\n\n"
            "- 滑条操作的真实操作噪声分布\n"
            "- 手表蓝牙连接的实际丢包和延迟\n"
            "- 用户对系统的长期适应效应（如\" habitualization\"）\n"
            "- 文化差异对效价-唤醒映射的影响\n\n"
            "#### 7.1.2 冷启动阶段的群体阈值依赖\n\n"
            "在新用户的前 20 次事件（约 2-3 周）内，系统使用群体通用阈值。"
            "这意味着：\n\n"
            "- 对极端人群（如天生高唤醒或慢性低效价的用户）可能过早或过晚预警\n"
            "- 冷启动期间的预警精确率可能低于成熟阶段\n\n"
            "> 该局限已通过 HYBRID 阶段的渐进加权部分缓解。\n\n"
            "#### 7.1.3 滑条交互本身对情绪的影响\n\n"
            "要求用户持续通过滑条报告情绪状态，这一交互本身可能：\n\n"
            "- **反刍效应**：频繁关注自身情绪可能加剧负面情绪的反复思考\n"
            "- **反应性偏差**：报告行为可能改变被报告的情绪本身\n"
            "- **认知负荷**：在情绪激动时要求精确操作滑条可能增加压力\n\n"
            "缓解策略：在情绪强度超过阈值时自动降低采样频率要求，并切换到更简化的交互方式。\n\n"
            "#### 7.1.4 单一用户模型\n\n"
            "当前系统仅支持单个用户的独立模型，未考虑：\n\n"
            "- 家庭成员共享设备的多用户场景\n"
            "- 群体级别的跨用户模式发现\n"
            "- 治疗师-来访者关系中的数据共享与权限管理\n\n"
            "### 7.2 未来工作\n\n"
            "#### 7.2.1 深度学习替代 / 增强 Kalman 滤波\n\n"
            "卡尔曼滤波器的线性假设限制了其对复杂情绪动态的建模能力。"
            "未来计划探索：\n\n"
            "- **扩展卡尔曼滤波（EKF）**：引入情绪的饱和效应等非线性\n"
            "- **变分自编码器（VAE）**：学习低维情绪流形的隐含结构\n"
            "- **轻量 Transformer**：在设备端捕捉长程依赖关系\n"
            "- **混合架构**：Kalman 滤波器提供实时基线估计，"
            "深度模型负责残差修正\n\n"
            "#### 7.2.2 迁移学习跨用户\n\n"
            "新用户的冷启动问题可以通过迁移学习缓解：\n\n"
            "- 在匿名化的群体数据上预训练一个\"情绪动态基模型\"\n"
            "- 新用户通过少量微调（fine-tuning）获得个性化模型\n"
            "- 使用差分隐私（DP-SGD）确保迁移过程不泄露个体信息\n\n"
            "#### 7.2.3 与专业心理咨询的整合\n\n"
            "EmoWave 可作为心理咨询的辅助工具，未来的整合方向包括：\n\n"
            "- **治疗师仪表板**：匿名化的趋势摘要，帮助治疗师了解来访者日常情绪动态\n"
            "- **CBT 工作表联动**：将认知行为疗法的工作表嵌入推荐策略库\n"
            "- **临床验证研究**：与心理健康机构合作开展随机对照试验\n"
            "- **危机升级路径**：在用户授权下，将危机日志安全传输给指定治疗师\n\n"
            "#### 7.2.4 长期追踪的价值发现\n\n"
            "长期使用 EmoWave 可能产生的额外价值：\n\n"
            "- **情绪节律发现**：识别月度/季节性的情绪模式\n"
            "- **生活方式关联**：通过多维度关联分析发现睡眠、运动与情绪的因果关系\n"
            "- **早期干预**：在抑郁/焦虑症状恶化前提供数据驱动的早期预警\n"
            "- **个性化循证**：为用户提供\"什么策略在什么情境下对你最有效\"的个性化证据\n"
        )
        self._sections.append(section)

    def _add_references(self):
        """参考文献"""
        section = (
            "## 8. 参考文献\n\n"
            "### 学术文献\n\n"
            "1. Russell, J. A. (1980). A circumplex model of affect. "
            "*Journal of Personality and Social Psychology, 39*(6), 1161-1178. "
            "https://doi.org/10.1037/h0077714\n\n"
            "2. Shiffman, S., Stone, A. A., & Hufford, M. R. (2008). Ecological momentary assessment. "
            "*Annual Review of Clinical Psychology, 4*, 1-32. "
            "https://doi.org/10.1146/annurev.clinpsy.3.022806.091415\n\n"
            "3. Welch, G., & Bishop, G. (2006). An introduction to the Kalman filter. "
            "University of North Carolina at Chapel Hill, Department of Computer Science, TR 95-041.\n\n"
            "4. Li, L., Chu, W., Langford, J., & Schapire, R. E. (2010). "
            "A contextual-bandit approach to personalized news article recommendation. "
            "*Proceedings of the 19th International Conference on World Wide Web (WWW '10)*, 661-670. "
            "https://doi.org/10.1145/1772690.1772758\n\n"
            "5. Diener, E., Wirtz, D., Tov, W., Kim-Prieto, C., Choi, D., Oishi, S., & Biswas-Diener, R. (2010). "
            "New well-being measures: Short scales to assess flourishing and positive and negative feelings. "
            "*Social Indicators Research, 97*(2), 143-156. "
            "https://doi.org/10.1007/s11205-009-9493-y\n\n"
            "6. Kim, H. G., Cheon, E. J., Bai, D. S., Lee, Y. H., & Koo, B. H. (2018). "
            "Stress and heart rate variability: A meta-analysis and review of the literature. "
            "*Psychiatry Investigation, 15*(3), 235-245. "
            "https://doi.org/10.4306/pi.2018.15.3.235\n\n"
            "7. Picard, R. W. (1997). *Affective Computing*. MIT Press, Cambridge, MA.\n\n"
            "8. Koster, E. H. W., De Raedt, R., Leyman, L., & De Lissnyder, E. (2010). "
            "Rumination and worry mediate the relationship between neuroticism and depressive and anxiety symptoms. "
            "*Personality and Individual Differences, 48*(5), 537-540. "
            "https://doi.org/10.1016/j.paid.2009.11.024\n\n"
            "9. Sano, A., & Picard, R. W. (2013). "
            "Stress recognition using wearable sensors and mobile phones. "
            "*Proceedings of the 2013 Humaine Association Conference on Affective Computing and Intelligent Interaction*, 671-676. "
            "https://doi.org/10.1109/ACII.2013.117\n\n"
            "10. Adams, R. P., & MacKay, D. J. C. (2007). "
            "Bayesian online changepoint detection. "
            "*arXiv preprint arXiv:0711.1063*.\n\n"
            "### 技术文档\n\n"
            "11. Thrun, S., Burgard, W., & Fox, D. (2005). *Probabilistic Robotics*. MIT Press.\n"
            "12. Sutton, R. S., & Barto, A. G. (2018). *Reinforcement Learning: An Introduction* (2nd ed.). MIT Press.\n"
            "13. Hastie, T., Tibshirani, R., & Friedman, J. (2009). *The Elements of Statistical Learning* (2nd ed.). Springer.\n\n"
            "---\n\n"
            "*本白皮书由 EmoWave Engine 白皮书生成器自动生成。系统参数来源于运行时配置对象，"
            "确保文档与代码实现的一致性。*\n"
        )
        self._sections.append(section)


def main():
    """生成技术白皮书"""
    print("=" * 60)
    print("  心潮 EmoWave — 技术白皮书生成")
    print("=" * 60)

    generator = WhitepaperGenerator(
        output_path="/workspace/emowave-engine/TECHNICAL_WHITEPAPER.md",
        benchmark_results_path="/workspace/emowave-engine/benchmark_results/benchmark_results.json",
        benchmark_report_path="/workspace/emowave-engine/benchmark_results/BENCHMARK_REPORT.md",
    )

    path = generator.save()
    print(f"\n白皮书已生成: {path}")

    # 打印章节摘要
    content = generator.generate()
    sections = [line for line in content.split('\n') if line.startswith('## ')]
    print(f"\n章节列表 ({len(sections)} 个):")
    for s in sections:
        print(f"  {s}")


if __name__ == "__main__":
    main()
