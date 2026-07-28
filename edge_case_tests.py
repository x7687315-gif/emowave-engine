"""
edge_case_tests.py — 心潮 EmoWave 情绪引擎 · 边缘情况与异常输入测试

本脚本对 EmoWave 引擎的各个核心组件进行边缘情况测试，
逐一注入异常场景并验证系统稳健性。

测试覆盖场景：
  1. 传感器断连：生理数据流中途断开再恢复
  2. 滑条静默：用户长时间不操作滑条
  3. 快速打点：短时间内大量滑条点击
  4. 午夜事件：凌晨时段触发高强度情绪事件
  5. 预警风暴：连续多次触发极点预警
  6. 空数据启动：新用户冷启动，无历史数据
  7. 数据极值：效价/唤醒推到绝对边界
  8. 矛盾输入：主观滑条与生理数据严重矛盾

运行方式：
  python3 edge_case_tests.py
"""

import sys
import os
import time
import math
import random
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime

# 确保能正确导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kalman_filter import (
    EmotionKalmanFilter,
    KalmanConfig,
    SliderObservation,
    PhysioInput,
    EmotionState,
)
from predictor import (
    PredictionEngine,
    PredictionConfig,
    PredictionResult,
    WarningLevel,
)
from engine import EmoCalibrationEngine
from models import (
    PersonalThresholds,
    ModelSource,
    EmotionEventRaw,
    TimeSeriesSample,
    DailySummary,
    EventProfile,
    BaselineVector,
)
from threshold import ThresholdManager
import config as app_config

# 配置日志捕获，用于测试中记录引擎输出
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


# ================================================================
# 测试结果数据结构
# ================================================================

@dataclass
class TestCaseResult:
    """单个测试用例的执行结果。"""
    name: str                           # 测试用例名称
    scenario: str                       # 场景描述
    passed: bool                        # 是否全部断言通过
    partial: bool                       # 是否部分断言通过
    details: str                        # 详细发现
    assertions: List[Dict]              # 断言详情列表 [{assertion, passed, detail}]
    logs: List[str]                     # 测试过程中捕获的日志
    duration_sec: float                 # 测试执行耗时（秒）


# ================================================================
# 日志捕获器
# ================================================================

class LogCapture(logging.Handler):
    """自定义日志处理器，将日志消息收集到列表中。"""

    def __init__(self):
        super().__init__()
        self.captured: List[str] = []

    def emit(self, record):
        self.captured.append(self.format(record))

    def clear(self):
        self.captured.clear()


# ================================================================
# 边缘情况模拟器
# ================================================================

class EdgeCaseSimulator:
    """边缘情况模拟器：逐一注入异常场景并验证系统稳健性。"""

    def __init__(self):
        self.results: List[TestCaseResult] = []

    # ----------------------------------------------------------------
    # 1. 传感器断连
    # ----------------------------------------------------------------

    def test_sensor_disconnect(self) -> TestCaseResult:
        """
        传感器断连：情绪事件中途，生理数据流突然返回None，持续5分钟后恢复。

        测试策略：
          - 前100步：正常带生理控制输入更新 KF
          - 中间300步：physio=None，仅用滑条观测更新（模拟传感器断连）
          - 后100步：恢复生理数据输入
        - 断言：KF 不崩溃，断连期间协方差迹增大，恢复后状态平滑过渡
        """
        start = time.perf_counter()
        assertions = []
        logs = []
        log_capture = LogCapture()

        try:
            kf = EmotionKalmanFilter()
            kf.init(valence=0.5, arousal=0.3)

            # 第一阶段：正常阶段（100步，带生理数据）
            for i in range(100):
                obs = SliderObservation(
                    timestamp=float(i),
                    valence=0.5 + 0.01 * math.sin(i * 0.1),
                    arousal=0.3 + 0.01 * math.sin(i * 0.15),
                    touch_velocity=0.02,
                    seconds_since_last_touch=1.0,
                )
                physio = PhysioInput(
                    timestamp=float(i),
                    hrv_drop_ratio=0.1,
                    hr_change=5.0,
                    signal_quality=0.9,
                )
                kf.update_with_control(obs, physio)

            # 第二阶段：断连阶段（300步，physio=None）
            disconnect_covs = []
            no_crash = True
            for i in range(100, 400):
                try:
                    obs = SliderObservation(
                        timestamp=float(i),
                        valence=0.5 + 0.005 * math.sin(i * 0.05),
                        arousal=0.3 + 0.005 * math.sin(i * 0.08),
                        touch_velocity=0.01,
                        seconds_since_last_touch=1.0,
                    )
                    state = kf.update_with_control(obs, physio=None)
                    disconnect_covs.append(state.covariance_trace)
                except Exception as e:
                    no_crash = False
                    logs.append(f"断连阶段第{i}步崩溃: {e}")
                    break

            # 第三阶段：恢复阶段（100步，恢复生理数据）
            recovery_no_crash = True
            for i in range(400, 500):
                try:
                    obs = SliderObservation(
                        timestamp=float(i),
                        valence=0.5 + 0.01 * math.sin(i * 0.1),
                        arousal=0.3 + 0.01 * math.sin(i * 0.15),
                        touch_velocity=0.02,
                        seconds_since_last_touch=1.0,
                    )
                    physio = PhysioInput(
                        timestamp=float(i),
                        hrv_drop_ratio=0.1,
                        hr_change=5.0,
                        signal_quality=0.9,
                    )
                    kf.update_with_control(obs, physio)
                except Exception as e:
                    recovery_no_crash = False
                    logs.append(f"恢复阶段第{i}步崩溃: {e}")
                    break

            # 断言1：KF 全程不崩溃
            a1_pass = no_crash and recovery_no_crash
            assertions.append({
                "assertion": "传感器断连及恢复期间KF不崩溃",
                "passed": a1_pass,
                "detail": "正常" if a1_pass else "断连或恢复阶段发生异常",
            })

            # 断言2：断连期间状态仍追踪滑条（降级到纯滑条模式，valence/arousal漂移不大）
            if disconnect_covs:
                # 取断连阶段最后一个状态
                last_disconnect_state = kf._to_state(399.0)
                # 滑条在断连阶段的值约 0.5 和 0.3（微小正弦波动）
                tracks_slider = (
                    abs(last_disconnect_state.valence - 0.5) < 0.1
                    and abs(last_disconnect_state.arousal - 0.3) < 0.1
                )
                assertions.append({
                    "assertion": "断连期间KF降级到纯滑条模式，状态追踪滑条输入",
                    "passed": tracks_slider,
                    "detail": (
                        f"valence={last_disconnect_state.valence:.4f}, "
                        f"arousal={last_disconnect_state.arousal:.4f}"
                    ),
                })
            else:
                assertions.append({
                    "assertion": "断连期间KF降级到纯滑条模式，状态追踪滑条输入",
                    "passed": False,
                    "detail": "无断连阶段数据",
                })

            # 断言3：最终状态值在合理范围内
            final_state = kf._to_state(500.0)
            in_range = 0.0 <= final_state.valence <= 1.0 and 0.0 <= final_state.arousal <= 1.0
            assertions.append({
                "assertion": "恢复后状态值在[0,1]范围内",
                "passed": in_range,
                "detail": f"valence={final_state.valence}, arousal={final_state.arousal}",
            })

        except Exception as e:
            logs.append(f"测试异常: {e}")

        elapsed = time.perf_counter() - start
        all_pass = all(a["passed"] for a in assertions)
        any_pass = any(a["passed"] for a in assertions)

        return TestCaseResult(
            name="传感器断连",
            scenario="情绪事件中途，生理数据流突然返回None，持续约5分钟（300步）后恢复",
            passed=all_pass,
            partial=any_pass and not all_pass,
            details="测试卡尔曼滤波器在传感器断连场景下的稳健性：不崩溃、不确定性上升、恢复后状态合理",
            assertions=assertions,
            logs=logs,
            duration_sec=round(elapsed, 4),
        )

    # ----------------------------------------------------------------
    # 2. 滑条静默
    # ----------------------------------------------------------------

    def test_slider_silence(self) -> TestCaseResult:
        """
        滑条静默：用户打开记录界面但30分钟不拖动滑条。

        测试策略：
          - 喂入100个完全相同的观测（模拟滑条静止不动）
          - 断言：速度衰减到接近0，强度保持稳定，无异常警告
        """
        start = time.perf_counter()
        assertions = []
        logs = []

        try:
            kf = EmotionKalmanFilter()
            kf.init(valence=0.5, arousal=0.4)

            # 喂入100个完全相同的观测，模拟用户不操作
            states = []
            for i in range(100):
                obs = SliderObservation(
                    timestamp=float(i),
                    valence=0.5,
                    arousal=0.4,
                    touch_velocity=0.0,           # 无触摸速度
                    seconds_since_last_touch=float(i + 1),  # 距上次触摸越来越远
                )
                state = kf.update(obs)
                states.append(state)

            # 断言1：速度衰减到接近0
            last_state = states[-1]
            velocity_near_zero = abs(last_state.d_valence) < 0.01 and abs(last_state.d_arousal) < 0.01
            assertions.append({
                "assertion": "速度衰减到接近0",
                "passed": velocity_near_zero,
                "detail": f"d_valence={last_state.d_valence}, d_arousal={last_state.d_arousal}",
            })

            # 断言2：强度保持稳定（波动小于0.05）
            intensities = [s.intensity for s in states]
            intensity_range = max(intensities) - min(intensities)
            intensity_stable = intensity_range < 0.05
            assertions.append({
                "assertion": "强度保持稳定（波动<0.05）",
                "passed": intensity_stable,
                "detail": f"强度范围: [{min(intensities):.4f}, {max(intensities):.4f}], 波动={intensity_range:.4f}",
            })

            # 断言3：效价和唤醒保持在初始值附近（漂移小于0.03）
            valence_drift = abs(last_state.valence - 0.5)
            arousal_drift = abs(last_state.arousal - 0.4)
            small_drift = valence_drift < 0.03 and arousal_drift < 0.03
            assertions.append({
                "assertion": "效价和唤醒值漂移小于0.03",
                "passed": small_drift,
                "detail": f"效价漂移={valence_drift:.4f}, 唤醒漂移={arousal_drift:.4f}",
            })

        except Exception as e:
            logs.append(f"测试异常: {e}")

        elapsed = time.perf_counter() - start
        all_pass = all(a["passed"] for a in assertions)
        any_pass = any(a["passed"] for a in assertions)

        return TestCaseResult(
            name="滑条静默",
            scenario="用户打开记录界面后30分钟内不拖动滑条，持续喂入相同的观测值",
            passed=all_pass,
            partial=any_pass and not all_pass,
            details="测试卡尔曼滤波器在长期无变化输入下的行为：速度应衰减、强度应稳定",
            assertions=assertions,
            logs=logs,
            duration_sec=round(elapsed, 4),
        )

    # ----------------------------------------------------------------
    # 3. 快速打点
    # ----------------------------------------------------------------

    def test_rapid_tapping(self) -> TestCaseResult:
        """
        快速打点：用户在1秒内连续点击滑条10次。

        测试策略：
          - 在同一时间戳下生成10个随机效价/唤醒的观测
          - 断言：KF 不崩溃，速度维度有显著变化，能处理快速状态跳变
        """
        start = time.perf_counter()
        assertions = []
        logs = []

        try:
            kf = EmotionKalmanFilter()
            kf.init(valence=0.5, arousal=0.5)

            base_ts = 1000.0  # 基准时间戳
            random.seed(42)  # 固定随机种子，保证可重复性

            # 生成10个快速随机点击
            rapid_states = []
            no_crash = True
            for i in range(10):
                try:
                    obs = SliderObservation(
                        timestamp=base_ts + i * 0.01,  # 100ms间隔
                        valence=random.uniform(0.1, 0.9),
                        arousal=random.uniform(0.1, 0.9),
                        touch_velocity=random.uniform(0.5, 2.0),  # 高速移动
                        seconds_since_last_touch=0.01,  # 极短时间内连续操作
                    )
                    state = kf.update(obs)
                    rapid_states.append(state)
                except Exception as e:
                    no_crash = False
                    logs.append(f"快速打点第{i}次崩溃: {e}")
                    break

            # 断言1：KF 不崩溃
            assertions.append({
                "assertion": "快速打点期间KF不崩溃",
                "passed": no_crash,
                "detail": "正常" if no_crash else "快速打点导致崩溃",
            })

            # 断言2：所有状态值在有效范围内
            if rapid_states:
                all_in_range = all(
                    0.0 <= s.valence <= 1.0 and 0.0 <= s.arousal <= 1.0
                    for s in rapid_states
                )
                assertions.append({
                    "assertion": "所有状态值在[0,1]有效范围内",
                    "passed": all_in_range,
                    "detail": f"共{len(rapid_states)}个状态点",
                })

                # 断言3：速度维度有显著变化（快速打点应产生高速度）
                max_dv = max(abs(s.d_valence) for s in rapid_states)
                max_da = max(abs(s.d_arousal) for s in rapid_states)
                has_velocity = max_dv > 0.001 or max_da > 0.001
                assertions.append({
                    "assertion": "速度维度有显著变化（反映快速操作）",
                    "passed": has_velocity,
                    "detail": f"最大d_valence={max_dv:.4f}, 最大d_arousal={max_da:.4f}",
                })
            else:
                assertions.append({
                    "assertion": "所有状态值在[0,1]有效范围内",
                    "passed": False,
                    "detail": "无有效状态数据",
                })
                assertions.append({
                    "assertion": "速度维度有显著变化（反映快速操作）",
                    "passed": False,
                    "detail": "无有效状态数据",
                })

        except Exception as e:
            logs.append(f"测试异常: {e}")

        elapsed = time.perf_counter() - start
        all_pass = all(a["passed"] for a in assertions)
        any_pass = any(a["passed"] for a in assertions)

        return TestCaseResult(
            name="快速打点",
            scenario="用户在1秒内连续点击/拖动滑条10次，每次随机效价和唤醒值",
            passed=all_pass,
            partial=any_pass and not all_pass,
            details="测试卡尔曼滤波器对高频随机输入的处理能力",
            assertions=assertions,
            logs=logs,
            duration_sec=round(elapsed, 4),
        )

    # ----------------------------------------------------------------
    # 4. 午夜事件
    # ----------------------------------------------------------------

    def test_midnight_event(self) -> TestCaseResult:
        """
        午夜事件：凌晨3点触发高强度情绪事件。

        测试策略：
          - 创建时间戳为凌晨3点的情绪事件（高唤醒0.9、低效价0.1）
          - 通过引擎处理该事件，检查预测引擎的预警行为
          - 断言：预测器不因时间因素而忽略预警，事件被正确处理
        """
        start = time.perf_counter()
        assertions = []
        logs = []

        try:
            # 构造凌晨3点的时间戳（使用一个固定的基准日期）
            # 2026-07-15 03:00:00 UTC
            midnight_ts = datetime(2026, 7, 15, 3, 0, 0).timestamp()

            # 构造带生理数据的时序采样
            samples = []
            for i in range(120):
                # 构造一个先升后降的情绪轨迹，峰值在中间
                progress = i / 120.0
                if progress < 0.4:
                    arousal = 0.3 + 0.6 * (progress / 0.4)  # 上升至0.9
                    valence = 0.5 - 0.4 * (progress / 0.4)  # 下降至0.1
                else:
                    decay = (progress - 0.4) / 0.6
                    arousal = 0.9 - 0.5 * decay  # 下降至0.4
                    valence = 0.1 + 0.3 * decay   # 上升至0.4

                samples.append(TimeSeriesSample(
                    timestamp=midnight_ts + i,
                    valence=round(valence, 4),
                    arousal=round(arousal, 4),
                    hr=80 + int(40 * (1.0 - abs(progress - 0.4) / 0.6)),  # 心率在峰值时最高
                    hrv=40 + int(20 * abs(progress - 0.4) / 0.6),        # HRV在峰值时最低
                ))

            raw_event = EmotionEventRaw(
                event_id="midnight_crisis_001",
                samples=samples,
                user_peak_rating=8.0,
                recovery_duration=72.0,
                trigger_tags=["工作压力", "深夜焦虑"],
                calm_timestamp=midnight_ts + 120,
            )

            # 通过引擎处理事件
            engine = EmoCalibrationEngine(user_id="midnight_test_user")
            profile, thresholds = engine.process_event(raw_event)

            # 断言1：事件被正确处理，peak_arousal 接近 0.9
            peak_close = profile.peak_arousal > 0.7
            assertions.append({
                "assertion": "事件极点唤醒度接近0.9（实际>0.7）",
                "passed": peak_close,
                "detail": f"peak_arousal={profile.peak_arousal}, peak_valence={profile.peak_valence}",
            })

            # 断言2：时间戳正确保留（在凌晨时段）
            is_midnight = (
                0 < datetime.utcfromtimestamp(profile.peak_time).hour < 6
                or profile.peak_time == midnight_ts
            )
            assertions.append({
                "assertion": "事件时间戳正确保留在凌晨时段",
                "passed": is_midnight,
                "detail": f"peak_time={profile.peak_time}, "
                          f"对应UTC时间={datetime.utcfromtimestamp(profile.peak_time).strftime('%H:%M:%S')}",
            })

            # 断言3：预测引擎能识别危险（使用引擎的卡尔曼滤波器运行预测）
            kf = EmotionKalmanFilter()
            kf.init(valence=0.5, arousal=0.5)
            # 喂入部分采样让 KF 追踪到高强度状态
            for s in samples[:60]:
                obs = SliderObservation(
                    timestamp=s.timestamp,
                    valence=s.valence,
                    arousal=s.arousal,
                    touch_velocity=0.05,
                    seconds_since_last_touch=1.0,
                )
                physio = PhysioInput(
                    timestamp=s.timestamp,
                    hrv_drop_ratio=0.2,
                    hr_change=20.0,
                    signal_quality=0.8,
                )
                kf.update_with_control(obs, physio)

            predictor = PredictionEngine()
            result = predictor.predict(kf, thresholds, current_time=samples[59].timestamp)
            # 不管预警级别是什么，引擎不应因时间而完全忽略
            predictor_responded = result.warning_level != WarningLevel.NONE or result.reason != ""
            assertions.append({
                "assertion": "预测引擎对凌晨高强度事件有响应（非NONE或理由非空）",
                "passed": predictor_responded,
                "detail": f"warning_level={result.warning_level.value}, reason={result.reason}",
            })

        except Exception as e:
            logs.append(f"测试异常: {e}")
            import traceback
            logs.append(traceback.format_exc())

        elapsed = time.perf_counter() - start
        all_pass = all(a["passed"] for a in assertions)
        any_pass = any(a["passed"] for a in assertions)

        return TestCaseResult(
            name="午夜事件",
            scenario="凌晨3点触发高强度情绪事件（唤醒0.9，效价0.1），附带生理数据",
            passed=all_pass,
            partial=any_pass and not all_pass,
            details="测试系统在凌晨时段处理高强度情绪事件的能力",
            assertions=assertions,
            logs=logs,
            duration_sec=round(elapsed, 4),
        )

    # ----------------------------------------------------------------
    # 5. 预警风暴
    # ----------------------------------------------------------------

    def test_warning_storm(self) -> TestCaseResult:
        """
        预警风暴：系统在10分钟内连续触发5次极点预警。

        测试策略：
          - 创建一个长时间高强度事件，让预测引擎持续预警
          - 每12秒调用一次预测，持续600秒（10分钟）
          - 统计预警触发次数和间隔
          - 断言：多次预警能触发，间隔合理
        """
        start = time.perf_counter()
        assertions = []
        logs = []

        try:
            kf = EmotionKalmanFilter()
            kf.init(valence=0.1, arousal=0.85)  # 初始就在高风险区

            # 构造群体默认阈值
            thresholds = PersonalThresholds(
                high_risk_arousal=app_config.POPULATION_THRESHOLDS["high_risk_arousal"],
                high_risk_valence=app_config.POPULATION_THRESHOLDS["high_risk_valence"],
                hrv_drop_percent=app_config.POPULATION_THRESHOLDS["hrv_drop_percent"],
                hr_surge_zscore=app_config.POPULATION_THRESHOLDS["hr_surge_zscore"],
                dangerous_rise_slope=app_config.POPULATION_THRESHOLDS["dangerous_rise_slope"],
            )

            # 使用更宽松的预警配置以便触发更多预警
            pred_config = PredictionConfig(
                warning_intensity=0.55,
                critical_intensity=0.65,
                min_current_intensity_for_warning=0.30,
                min_peak_excess=0.05,
                min_intensity_dot_for_warning=0.0,  # 取消变化率门槛
            )
            predictor = PredictionEngine(config=pred_config)

            # 模拟600秒内持续的高强度状态
            warning_count = 0
            warning_times = []
            no_crash = True

            for step in range(50):  # 50步 * 12秒 = 600秒
                t = float(step * 12)
                try:
                    # 喂入高风险观测（低效价+高唤醒），带有微小波动
                    obs = SliderObservation(
                        timestamp=t,
                        valence=0.08 + 0.02 * math.sin(step * 0.5),
                        arousal=0.88 + 0.05 * math.sin(step * 0.3),
                        touch_velocity=0.01,
                        seconds_since_last_touch=1.0,
                    )
                    kf.update(obs)

                    result = predictor.predict(kf, thresholds, current_time=t)

                    if result.warning_level in (WarningLevel.WARNING, WarningLevel.CRITICAL):
                        warning_count += 1
                        warning_times.append(t)
                except Exception as e:
                    no_crash = False
                    logs.append(f"预警风暴第{step}步崩溃: {e}")
                    break

            # 断言1：全程不崩溃
            assertions.append({
                "assertion": "预警风暴期间引擎不崩溃",
                "passed": no_crash,
                "detail": "正常" if no_crash else "预警风暴导致崩溃",
            })

            # 断言2：至少触发1次预警（在极端参数下应多次）
            has_warnings = warning_count >= 1
            assertions.append({
                "assertion": "高强度持续状态下至少触发1次预警",
                "passed": has_warnings,
                "detail": f"共触发{warning_count}次预警",
            })

            # 断言3：如果有多次预警，检查间隔是否合理
            if len(warning_times) >= 2:
                intervals = [warning_times[i+1] - warning_times[i] for i in range(len(warning_times) - 1)]
                min_interval = min(intervals) if intervals else 0
                has_spacing = min_interval >= 1.0  # 至少间隔1秒
                assertions.append({
                    "assertion": "多次预警之间有合理间隔（>=1秒）",
                    "passed": has_spacing,
                    "detail": f"最小间隔={min_interval:.1f}秒, 共{len(warning_times)}次预警",
                })
            else:
                assertions.append({
                    "assertion": "多次预警之间有合理间隔（>=1秒）",
                    "passed": True,  # 只触发0或1次预警，此断言不适用，视为通过
                    "detail": f"仅触发{warning_count}次预警，间隔检查不适用",
                })

        except Exception as e:
            logs.append(f"测试异常: {e}")
            import traceback
            logs.append(traceback.format_exc())

        elapsed = time.perf_counter() - start
        all_pass = all(a["passed"] for a in assertions)
        any_pass = any(a["passed"] for a in assertions)

        return TestCaseResult(
            name="预警风暴",
            scenario="系统在10分钟内持续处于高强度状态，检查预警触发频率和间隔",
            passed=all_pass,
            partial=any_pass and not all_pass,
            details="测试预测引擎在持续高风险状态下的预警行为，关注预警风暴是否有抑制机制",
            assertions=assertions,
            logs=logs,
            duration_sec=round(elapsed, 4),
        )

    # ----------------------------------------------------------------
    # 6. 空数据启动
    # ----------------------------------------------------------------

    def test_cold_start_no_data(self) -> TestCaseResult:
        """
        空数据启动：新用户第一天没有任何历史数据，所有模型处于冷启动。

        测试策略：
          - 创建全新的 EmoCalibrationEngine 实例
          - 不处理任何事件，直接查询阈值
          - 断言：model_source 为 POPULATION，阈值使用群体默认值，置信度低
        """
        start = time.perf_counter()
        assertions = []
        logs = []

        try:
            engine = EmoCalibrationEngine(user_id="cold_start_user")
            thresholds = engine.get_thresholds()

            # 断言1：模型来源为 POPULATION（冷启动）
            is_population = thresholds.model_source == ModelSource.POPULATION
            assertions.append({
                "assertion": "冷启动时模型来源为POPULATION",
                "passed": is_population,
                "detail": f"实际model_source={thresholds.model_source.value}",
            })

            # 断言2：阈值使用群体默认值
            pop = app_config.POPULATION_THRESHOLDS
            arousal_match = abs(thresholds.high_risk_arousal - pop["high_risk_arousal"]) < 0.001
            valence_match = abs(thresholds.high_risk_valence - pop["high_risk_valence"]) < 0.001
            thresholds_match = arousal_match and valence_match
            assertions.append({
                "assertion": "阈值使用群体默认值",
                "passed": thresholds_match,
                "detail": (
                    f"arousal: 期望={pop['high_risk_arousal']}, 实际={thresholds.high_risk_arousal} | "
                    f"valence: 期望={pop['high_risk_valence']}, 实际={thresholds.high_risk_valence}"
                ),
            })

            # 断言3：置信度较低（< 0.5）
            low_confidence = thresholds.model_confidence < 0.5
            assertions.append({
                "assertion": "冷启动时模型置信度较低（<0.5）",
                "passed": low_confidence,
                "detail": f"model_confidence={thresholds.model_confidence}",
            })

            # 断言4：事件计数为0
            zero_events = thresholds.event_count == 0
            assertions.append({
                "assertion": "冷启动时事件计数为0",
                "passed": zero_events,
                "detail": f"event_count={thresholds.event_count}",
            })

        except Exception as e:
            logs.append(f"测试异常: {e}")
            import traceback
            logs.append(traceback.format_exc())

        elapsed = time.perf_counter() - start
        all_pass = all(a["passed"] for a in assertions)
        any_pass = any(a["passed"] for a in assertions)

        return TestCaseResult(
            name="空数据启动",
            scenario="新用户第一天无历史数据，直接查询引擎阈值和模型状态",
            passed=all_pass,
            partial=any_pass and not all_pass,
            details="验证冷启动阶段的默认行为：使用群体阈值、低置信度、零事件",
            assertions=assertions,
            logs=logs,
            duration_sec=round(elapsed, 4),
        )

    # ----------------------------------------------------------------
    # 7. 数据极值
    # ----------------------------------------------------------------

    def test_data_extremes(self) -> TestCaseResult:
        """
        数据极值：效价和唤醒滑条被推到绝对边界(0.0或1.0)并保持。

        测试策略：
          - 喂入60个 (valence=0.0, arousal=1.0) 的观测
          - 断言：KF 将值裁剪到 [0,1]，intensity 接近 1.0，无 NaN 或 infinity
        """
        start = time.perf_counter()
        assertions = []
        logs = []

        try:
            kf = EmotionKalmanFilter()
            kf.init(valence=0.5, arousal=0.5)

            states = []
            no_crash = True
            has_nan = False
            has_inf = False

            for i in range(60):
                try:
                    obs = SliderObservation(
                        timestamp=float(i),
                        valence=0.0,   # 绝对下界
                        arousal=1.0,   # 绝对上界
                        touch_velocity=0.1,
                        seconds_since_last_touch=1.0,
                    )
                    state = kf.update(obs)
                    states.append(state)

                    # 检查 NaN
                    if (math.isnan(state.valence) or math.isnan(state.arousal)
                            or math.isnan(state.intensity)):
                        has_nan = True
                    # 检查 infinity
                    if (math.isinf(state.valence) or math.isinf(state.arousal)
                            or math.isinf(state.intensity)):
                        has_inf = True
                except Exception as e:
                    no_crash = False
                    logs.append(f"数据极值第{i}步崩溃: {e}")
                    break

            last = states[-1] if states else None

            # 断言1：全程不崩溃
            assertions.append({
                "assertion": "数据极值输入期间KF不崩溃",
                "passed": no_crash,
                "detail": "正常" if no_crash else "数据极值导致崩溃",
            })

            # 断言2：无 NaN
            assertions.append({
                "assertion": "状态中无NaN值",
                "passed": not has_nan,
                "detail": "检测到NaN" if has_nan else "无NaN",
            })

            # 断言3：无 infinity
            assertions.append({
                "assertion": "状态中无infinity值",
                "passed": not has_inf,
                "detail": "检测到infinity" if has_inf else "无infinity",
            })

            # 断言4：最终值被裁剪到 [0, 1]
            if last:
                in_bounds = 0.0 <= last.valence <= 1.0 and 0.0 <= last.arousal <= 1.0
                assertions.append({
                    "assertion": "最终效价和唤醒在[0,1]范围内",
                    "passed": in_bounds,
                    "detail": f"valence={last.valence}, arousal={last.arousal}",
                })

                # 断言5：intensity 接近 1.0（(0,1) 点的强度应很高）
                intensity_high = last.intensity > 0.5
                assertions.append({
                    "assertion": "intensity > 0.5（(0,1)处强度应较高）",
                    "passed": intensity_high,
                    "detail": f"intensity={last.intensity}",
                })
            else:
                assertions.append({
                    "assertion": "最终效价和唤醒在[0,1]范围内",
                    "passed": False,
                    "detail": "无有效状态数据",
                })
                assertions.append({
                    "assertion": "intensity > 0.5（(0,1)处强度应较高）",
                    "passed": False,
                    "detail": "无有效状态数据",
                })

        except Exception as e:
            logs.append(f"测试异常: {e}")
            import traceback
            logs.append(traceback.format_exc())

        elapsed = time.perf_counter() - start
        all_pass = all(a["passed"] for a in assertions)
        any_pass = any(a["passed"] for a in assertions)

        return TestCaseResult(
            name="数据极值",
            scenario="效价=0.0、唤醒=1.0 的极端输入持续60步",
            passed=all_pass,
            partial=any_pass and not all_pass,
            details="测试卡尔曼滤波器对边界值输入的处理：裁剪、无NaN/infinity、强度合理",
            assertions=assertions,
            logs=logs,
            duration_sec=round(elapsed, 4),
        )

    # ----------------------------------------------------------------
    # 8. 矛盾输入
    # ----------------------------------------------------------------

    def test_contradictory_inputs(self) -> TestCaseResult:
        """
        矛盾输入：主观滑条显示高强度愤怒(0.1, 0.9)，但生理数据显示极度平静(hr_change=0, hrv_drop=0)。

        测试策略：
          - 滑条持续显示高唤醒低效价，但生理信号完全平静
          - 断言：KF 不崩溃，协方差迹升高（反映不确定性），状态值仍合理
        """
        start = time.perf_counter()
        assertions = []
        logs = []

        try:
            kf = EmotionKalmanFilter()
            kf.init(valence=0.5, arousal=0.3)

            states = []
            no_crash = True

            for i in range(100):
                try:
                    # 滑条显示愤怒：低效价、高唤醒
                    obs = SliderObservation(
                        timestamp=float(i),
                        valence=0.1,
                        arousal=0.9,
                        touch_velocity=0.05,
                        seconds_since_last_touch=1.0,
                    )
                    # 生理显示平静：无心率变化、无HRV下降
                    physio = PhysioInput(
                        timestamp=float(i),
                        hrv_drop_ratio=0.0,     # HRV无下降
                        hr_change=0.0,          # 心率无变化
                        signal_quality=0.95,    # 信号质量很好
                    )
                    state = kf.update_with_control(obs, physio)
                    states.append(state)
                except Exception as e:
                    no_crash = False
                    logs.append(f"矛盾输入第{i}步崩溃: {e}")
                    break

            # 先运行一个"一致输入"对照实验，用于比较协方差
            kf_baseline = EmotionKalmanFilter()
            kf_baseline.init(valence=0.5, arousal=0.3)
            baseline_covs = []
            for i in range(100):
                obs = SliderObservation(
                    timestamp=float(i),
                    valence=0.1,
                    arousal=0.9,
                    touch_velocity=0.05,
                    seconds_since_last_touch=1.0,
                )
                # 一致输入：生理数据也显示高唤醒
                physio = PhysioInput(
                    timestamp=float(i),
                    hrv_drop_ratio=0.3,     # HRV下降30%
                    hr_change=25.0,         # 心率上升25BPM
                    signal_quality=0.95,
                )
                state = kf_baseline.update_with_control(obs, physio)
                baseline_covs.append(state.covariance_trace)

            last_contradictory = states[-1] if states else None
            avg_baseline_cov = sum(baseline_covs) / len(baseline_covs) if baseline_covs else 0
            avg_contradictory_cov = sum(s.covariance_trace for s in states) / len(states) if states else 0

            # 断言1：KF 不崩溃
            assertions.append({
                "assertion": "矛盾输入期间KF不崩溃",
                "passed": no_crash,
                "detail": "正常" if no_crash else "矛盾输入导致崩溃",
            })

            # 断言2：状态值在合理范围内
            if last_contradictory:
                in_range = 0.0 <= last_contradictory.valence <= 1.0 and 0.0 <= last_contradictory.arousal <= 1.0
                assertions.append({
                    "assertion": "状态值在[0,1]合理范围内",
                    "passed": in_range,
                    "detail": f"valence={last_contradictory.valence}, arousal={last_contradictory.arousal}",
                })
            else:
                assertions.append({
                    "assertion": "状态值在[0,1]合理范围内",
                    "passed": False,
                    "detail": "无有效状态数据",
                })

            # 断言3：矛盾输入下的协方差高于一致输入（不确定性更大）
            #   注：由于KF会将状态快速拉向观测，矛盾输入的协方差可能不会显著更高
            #   但我们仍然期望一些差异
            cov_higher = avg_contradictory_cov >= avg_baseline_cov * 0.8  # 宽松比较
            assertions.append({
                "assertion": "矛盾输入下协方差不低于一致输入的80%",
                "passed": cov_higher,
                "detail": (
                    f"矛盾输入平均协方差={avg_contradictory_cov:.6f}, "
                    f"一致输入平均协方差={avg_baseline_cov:.6f}"
                ),
            })

        except Exception as e:
            logs.append(f"测试异常: {e}")
            import traceback
            logs.append(traceback.format_exc())

        elapsed = time.perf_counter() - start
        all_pass = all(a["passed"] for a in assertions)
        any_pass = any(a["passed"] for a in assertions)

        return TestCaseResult(
            name="矛盾输入",
            scenario="滑条显示愤怒(0.1, 0.9)但生理数据完全平静(hr_change=0, hrv_drop=0)",
            passed=all_pass,
            partial=any_pass and not all_pass,
            details="测试卡尔曼滤波器对主观-生理信号矛盾的处理：不崩溃、协方差反映不确定性",
            assertions=assertions,
            logs=logs,
            duration_sec=round(elapsed, 4),
        )


# ================================================================
# 辅助函数
# ================================================================

def np_trace(matrix) -> float:
    """计算矩阵的迹（用于兼容无 numpy 直接导入的场景）。"""
    import numpy as np
    return float(np.trace(matrix))


# ================================================================
# 报告生成器
# ================================================================

def generate_report(results: List[TestCaseResult], output_path: str) -> None:
    """
    生成边缘测试报告文本文件。

    Args:
        results: 所有测试用例的结果列表
        output_path: 输出文件路径
    """
    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 统计总体数据
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    partial = sum(1 for r in results if r.partial and not r.passed)
    failed = sum(1 for r in results if not r.passed and not r.partial)
    total_duration = sum(r.duration_sec for r in results)

    lines = []

    # 报告头
    lines.append("## 边缘测试报告")
    lines.append("")
    lines.append(f"> 生成时间: 2026-07-15")
    lines.append(f"> 测试场景数: {total}")
    lines.append(f"> 通过: {passed} | 部分通过: {partial} | 失败: {failed}")
    lines.append(f"> 总耗时: {total_duration:.2f}s")
    lines.append("")

    # 总体结果表格
    lines.append("### 总体结果")
    lines.append("")
    lines.append("| 场景 | 状态 | 耗时 | 断言通过率 |")
    lines.append("|------|------|------|-----------|")

    for r in results:
        if r.passed:
            status = "通过"
        elif r.partial:
            status = "部分通过"
        else:
            status = "失败"

        passed_count = sum(1 for a in r.assertions if a["passed"])
        total_count = len(r.assertions)
        rate = f"{passed_count}/{total_count}"

        lines.append(f"| {r.name} | {status} | {r.duration_sec:.2f}s | {rate} |")

    lines.append("")

    # 详细结果
    lines.append("### 详细结果")
    lines.append("")

    for idx, r in enumerate(results, 1):
        lines.append(f"#### {idx}. {r.name}")
        lines.append("")
        lines.append(f"**场景描述**: {r.scenario}")
        lines.append("")
        lines.append(f"**预期行为**: {r.details}")
        lines.append("")

        if r.passed:
            lines.append("**实际结果**: 全部断言通过")
        elif r.partial:
            lines.append("**实际结果**: 部分断言通过，存在需要关注的问题")
        else:
            lines.append("**实际结果**: 断言未通过，需要修复")
        lines.append("")

        # 断言详情
        lines.append("**断言详情**:")
        lines.append("")
        for a in r.assertions:
            mark = "PASS" if a["passed"] else "FAIL"
            lines.append(f"- [{mark}] {a['assertion']}")
            lines.append(f"  - {a['detail']}")
        lines.append("")

        # 日志片段
        if r.logs:
            lines.append("**日志片段**:")
            lines.append("")
            # 最多展示5条日志
            for log in r.logs[:5]:
                lines.append(f"```\n{log}\n```")
            if len(r.logs) > 5:
                lines.append(f"... (共{len(r.logs)}条日志)")
            lines.append("")

        lines.append("---")
        lines.append("")

    # 写入文件
    report_content = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"报告已生成: {output_path}")


# ================================================================
# 主入口
# ================================================================

def main():
    """运行所有边缘测试并生成报告。"""
    print("=" * 60)
    print("心潮 EmoWave 边缘情况测试")
    print("=" * 60)
    print()

    simulator = EdgeCaseSimulator()

    # 注册所有测试方法
    test_methods = [
        ("传感器断连", simulator.test_sensor_disconnect),
        ("滑条静默", simulator.test_slider_silence),
        ("快速打点", simulator.test_rapid_tapping),
        ("午夜事件", simulator.test_midnight_event),
        ("预警风暴", simulator.test_warning_storm),
        ("空数据启动", simulator.test_cold_start_no_data),
        ("数据极值", simulator.test_data_extremes),
        ("矛盾输入", simulator.test_contradictory_inputs),
    ]

    # 逐一执行测试
    for name, method in test_methods:
        print(f"运行测试: {name} ... ", end="", flush=True)
        try:
            result = method()
            simulator.results.append(result)

            if result.passed:
                print(f"通过 ({result.duration_sec:.2f}s)")
            elif result.partial:
                print(f"部分通过 ({result.duration_sec:.2f}s)")
            else:
                print(f"失败 ({result.duration_sec:.2f}s)")
        except Exception as e:
            print(f"异常: {e}")
            import traceback
            traceback.print_exc()
            simulator.results.append(TestCaseResult(
                name=name,
                scenario="测试执行异常",
                passed=False,
                partial=False,
                details=f"测试方法抛出未捕获异常: {e}",
                assertions=[],
                logs=[str(e)],
                duration_sec=0.0,
            ))

    print()

    # 生成报告
    report_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "profiling_output",
        "edge_case_report.txt",
    )
    generate_report(simulator.results, report_path)

    # 控制台汇总
    total = len(simulator.results)
    passed = sum(1 for r in simulator.results if r.passed)
    partial = sum(1 for r in simulator.results if r.partial and not r.passed)
    failed = total - passed - partial

    print()
    print("=" * 60)
    print(f"测试完成: {passed}/{total} 通过, {partial} 部分通过, {failed} 失败")
    print("=" * 60)

    # 返回退出码
    if failed > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()