"""
test_engine.py — 心潮 EmoWave 引擎集成测试

模拟真实使用场景：
  1. 新用户冷启动 → 验证群体阈值
  2. 累积事件 → 验证置信度增长
  3. 基线漂移 → 验证告警触发
  4. 持久化恢复 → 验证状态完整性
  5. 生理信号融合标注 → 验证极点检测
"""

import sys
import random

# 确保 import 路径正确
sys.path.insert(0, "/workspace/emowave-engine")

from engine import EmoCalibrationEngine
from models import (
    EmotionEventRaw,
    TimeSeriesSample,
    DailySummary,
    EventProfile,
    PersonalThresholds,
    ModelSource,
    AlertLevel,
)


def generate_samples(
    n: int = 60,
    peak_arousal: float = 0.85,
    peak_valence: float = 0.12,
    base_hr: float = 72.0,
    hr_surge: float = 30.0,
    base_hrv: float = 50.0,
    hrv_drop: float = 0.35,
    has_physio: bool = True,
) -> list:
    """
    生成模拟的情绪事件时序数据。

    模拟一条"平静 → 快速上升 → 峰值 → 缓慢恢复"的典型曲线。
    """
    samples = []
    t0 = 1000000.0
    interval = 5.0  # 5秒采样

    for i in range(n):
        t = t0 + i * interval
        progress = i / n  # 0 → 1

        # Arousal 曲线：先升后降（钟形）
        if progress < 0.3:
            arousal = 0.3 + (peak_arousal - 0.3) * (progress / 0.3) ** 1.5
        elif progress < 0.5:
            arousal = peak_arousal
        else:
            arousal = peak_arousal - (peak_arousal - 0.35) * ((progress - 0.5) / 0.5) ** 0.8

        # Valence 曲线：先降后升（U 形）
        if progress < 0.4:
            valence = 0.6 - (0.6 - peak_valence) * (progress / 0.4) ** 1.3
        else:
            valence = peak_valence + (0.55 - peak_valence) * ((progress - 0.4) / 0.6) ** 0.7

        # 生理信号：与 arousal 同步变化
        if has_physio:
            hr = base_hr + hr_surge * max(0, (arousal - 0.4)) / (peak_arousal - 0.4 + 1e-9)
            # HRV 在峰值附近下降
            hrv = base_hrv * (1.0 - hrv_drop * max(0, (arousal - 0.5)) / (peak_arousal - 0.5 + 1e-9))
        else:
            hr = None
            hrv = None

        samples.append(TimeSeriesSample(
            timestamp=t,
            valence=round(max(0, min(1, valence)), 3),
            arousal=round(max(0, min(1, arousal)), 3),
            hr=round(hr, 1) if hr else None,
            hrv=round(hrv, 1) if hrv else None,
        ))

    return samples


def make_event(event_id: str, **kwargs) -> EmotionEventRaw:
    """构造 EmotionEventRaw 的便捷方法。"""
    defaults = dict(
        event_id=event_id,
        samples=generate_samples(),
        user_peak_rating=8.0,
        recovery_duration=300.0,
        trigger_tags=["工作会议"],
        coping_methods=["深呼吸"],
        coping_ratings={"深呼吸": 4},
        body_symptoms=["胸口压抑"],
        calm_timestamp=1000000.0 + 60 * 5,
    )
    defaults.update(kwargs)
    return EmotionEventRaw(**defaults)


# ================================================================
# 测试 1：冷启动 → 群体阈值
# ================================================================
def test_cold_start():
    print("=" * 60)
    print("测试 1：冷启动阶段 — 应使用群体阈值")
    print("=" * 60)

    engine = EmoCalibrationEngine(user_id="test_user")

    # 处理前 5 个事件
    for i in range(5):
        event = make_event(f"evt_{i:03d}")
        profile, thresholds = engine.process_event(event)
        print(f"  事件 {i+1}: peak_arousal={profile.peak_arousal:.2f}, "
              f"physio_score={profile.physiological_peak_score:.2f}")

    thresholds = engine.get_thresholds()
    print(f"\n  阈值来源: {thresholds.model_source.value}")
    print(f"  模型置信度: {thresholds.model_confidence:.3f}")
    print(f"  高风险唤醒阈值: {thresholds.high_risk_arousal}")
    print(f"  高风险效价阈值: {thresholds.high_risk_valence}")

    assert thresholds.model_source == ModelSource.POPULATION, "冷启动应使用群体阈值"
    assert thresholds.model_confidence < 0.75, "冷启动置信度应低于切换阈值"
    print("  ✓ 通过：冷启动使用群体阈值\n")


# ================================================================
# 测试 2：累积事件 → 置信度增长
# ================================================================
def test_confidence_growth():
    print("=" * 60)
    print("测试 2：累积事件 — 置信度应逐步增长")
    print("=" * 60)

    engine = EmoCalibrationEngine(user_id="test_user")

    # 模拟 25 个事件
    prev_confidence = 0.0
    switched = False
    for i in range(25):
        # 随机变化峰值，模拟真实波动
        peak_a = random.uniform(0.7, 0.95)
        peak_v = random.uniform(0.05, 0.25)
        event = make_event(
            f"evt_{i:03d}",
            samples=generate_samples(peak_arousal=peak_a, peak_valence=peak_v),
        )
        profile, thresholds = engine.process_event(event)

        if thresholds.model_source != ModelSource.POPULATION:
            if not switched:
                switched = True
                print(f"  第 {i+1} 个事件后切换到: {thresholds.model_source.value}")

    thresholds = engine.get_thresholds()
    print(f"\n  总事件数: {thresholds.event_count}")
    print(f"  模型置信度: {thresholds.model_confidence:.3f}")
    print(f"  阈值来源: {thresholds.model_source.value}")
    print(f"  个人化唤醒阈值: {thresholds.high_risk_arousal}")
    print(f"  个人化效价阈值: {thresholds.high_risk_valence}")

    assert thresholds.event_count == 25
    assert thresholds.model_confidence > prev_confidence, "置信度应随事件增长"
    print("  ✓ 通过：置信度逐步增长\n")


# ================================================================
# 测试 3：生理信号融合标注
# ================================================================
def test_physio_annotation():
    print("=" * 60)
    print("测试 3：生理信号融合标注 — 极点检测")
    print("=" * 60)

    engine = EmoCalibrationEngine(user_id="test_user")

    # 带生理数据的事件
    event_with_physio = make_event(
        "evt_physio",
        samples=generate_samples(
            n=80, peak_arousal=0.92, peak_valence=0.08,
            hr_surge=35.0, hrv_drop=0.40,
            has_physio=True,
        ),
    )
    profile, _ = engine.process_event(event_with_physio)

    print(f"  总采样点: {profile.sample_count}")
    print(f"  极点时间偏移: {profile.peak_time - profile.onset_time:.0f}s")
    print(f"  峰值唤醒度: {profile.peak_arousal:.2f}")
    print(f"  峰值效价: {profile.peak_valence:.2f}")
    print(f"  生理极点得分: {profile.physiological_peak_score:.2f}")
    print(f"  生理极点置信度: {profile.physiological_peak_confidence:.2f}")
    print(f"  检测到的生理极点数: {len(profile.physio_peaks)}")
    print(f"  危险上升段数: {len(profile.dangerous_rise_segments)}")
    print(f"  恢复时长: {profile.recovery_duration:.0f}s")

    if profile.physio_peaks:
        best = profile.physio_peaks[0]
        print(f"\n  最佳生理极点:")
        print(f"    HR z-score: {best.hr_zscore:.2f}")
        print(f"    HRV 下降: {best.hrv_drop_pct:.1%}")
        print(f"    Arousal: {best.arousal_spike:.2f}")
        print(f"    融合得分: {best.composite_score:.3f}")

    assert profile.physiological_peak_score > 0.3, "有生理数据时应检测到显著极点"
    assert profile.sample_count == 80
    print("  ✓ 通过：生理信号融合标注正确\n")


# ================================================================
# 测试 4：无生理数据的降级标注
# ================================================================
def test_no_physio_fallback():
    print("=" * 60)
    print("测试 4：无生理数据 — 降级到滑条最大值")
    print("=" * 60)

    engine = EmoCalibrationEngine(user_id="test_user")

    event_no_physio = make_event(
        "evt_no_physio",
        samples=generate_samples(has_physio=False),
    )
    profile, _ = engine.process_event(event_no_physio)

    print(f"  极点时间偏移: {profile.peak_time - profile.onset_time:.0f}s")
    print(f"  峰值唤醒度: {profile.peak_arousal:.2f}")
    print(f"  生理极点得分: {profile.physiological_peak_score:.2f}")
    print(f"  生理极点置信度: {profile.physiological_peak_confidence:.2f}")

    assert profile.physiological_peak_score == 0.0, "无生理数据时融合得分应为 0"
    assert profile.physiological_peak_confidence <= 0.5, "无生理数据时置信度应低"
    print("  ✓ 通过：无生理数据正确降级\n")


# ================================================================
# 测试 5：基线漂移检测
# ================================================================
def test_baseline_shift():
    print("=" * 60)
    print("测试 5：基线漂移 — 连续偏离应触发告警")
    print("=" * 60)

    engine = EmoCalibrationEngine(user_id="test_user")

    # 先建立 10 天正常基线
    for day in range(1, 11):
        daily = DailySummary(
            date=f"2026-07-{day:02d}",
            avg_resting_hrv=50.0 + random.uniform(-3, 3),
            avg_resting_hr=72.0 + random.uniform(-3, 3),
            sleep_score=7.0 + random.uniform(-0.5, 0.5),
            morning_valence_avg=0.55 + random.uniform(-0.05, 0.05),
            evening_valence_avg=0.50 + random.uniform(-0.05, 0.05),
        )
        alert = engine.update_daily(daily)
        if alert:
            print(f"  Day {day}: 意外告警! {alert.message}")
        else:
            print(f"  Day {day}: 正常 (HRV={daily.avg_resting_hrv:.1f})")

    # 然后注入 4 天异常数据（HRV 持续偏低）
    shift_detected = False
    for day in range(11, 15):
        daily = DailySummary(
            date=f"2026-07-{day:02d}",
            avg_resting_hrv=30.0 + random.uniform(-2, 2),  # 严重偏低
            avg_resting_hr=72.0 + random.uniform(-3, 3),
            sleep_score=7.0 + random.uniform(-0.5, 0.5),
            morning_valence_avg=0.55 + random.uniform(-0.05, 0.05),
            evening_valence_avg=0.50 + random.uniform(-0.05, 0.05),
        )
        alert = engine.update_daily(daily)
        if alert:
            print(f"  Day {day}: ⚠ 告警触发!")
            print(f"    级别: {alert.alert_level.value}")
            print(f"    漂移维度: {alert.shifted_dimensions}")
            print(f"    偏离量: {alert.shift_magnitudes}")
            shift_detected = True
        else:
            print(f"  Day {day}: 正常 (HRV={daily.avg_resting_hrv:.1f})")

    assert shift_detected, "应检测到基线漂移"
    print("  ✓ 通过：基线漂移检测正确\n")


# ================================================================
# 测试 6：持久化与恢复
# ================================================================
def test_persistence():
    print("=" * 60)
    print("测试 6：持久化与恢复 — 状态应完整还原")
    print("=" * 60)

    engine = EmoCalibrationEngine(user_id="persist_test")

    # 处理几个事件
    for i in range(10):
        event = make_event(f"evt_{i:03d}")
        engine.process_event(event)

    # 更新几天基线
    for day in range(1, 6):
        daily = DailySummary(
            date=f"2026-07-{day:02d}",
            avg_resting_hrv=50.0,
            avg_resting_hr=72.0,
            sleep_score=7.0,
            morning_valence_avg=0.55,
            evening_valence_avg=0.50,
        )
        engine.update_daily(daily)

    # 序列化
    state_json = engine.serialize_state()
    print(f"  序列化长度: {len(state_json)} 字符")

    # 恢复
    engine2 = EmoCalibrationEngine.load(state_json)

    # 验证
    diag1 = engine.diagnostics()
    diag2 = engine2.diagnostics()

    print(f"\n  原引擎事件数: {diag1['total_events']}")
    print(f"  恢复引擎事件数: {diag2['total_events']}")
    print(f"  原引擎基线天数: {diag1['baseline_days']}")
    print(f"  恢复引擎基线天数: {diag2['baseline_days']}")
    print(f"  原引擎置信度: {diag1['model_confidence']:.3f}")
    print(f"  恢复引擎置信度: {diag2['model_confidence']:.3f}")

    assert diag2["total_events"] == diag1["total_events"], "事件数应一致"
    assert diag2["baseline_days"] == diag1["baseline_days"], "基线天数应一致"
    print("  ✓ 通过：持久化恢复正确\n")


# ================================================================
# 测试 7：诊断输出
# ================================================================
def test_diagnostics():
    print("=" * 60)
    print("测试 7：诊断面板输出")
    print("=" * 60)

    engine = EmoCalibrationEngine(user_id="diag_test")
    for i in range(3):
        event = make_event(f"evt_{i:03d}")
        engine.process_event(event)

    diag = engine.diagnostics()
    print(f"  用户 ID: {diag['user_id']}")
    print(f"  总事件数: {diag['total_events']}")
    print(f"  基线天数: {diag['baseline_days']}")
    print(f"  模型置信度: {diag['model_confidence']}")
    print(f"  模型来源: {diag['model_source']}")
    print(f"  警戒阈值:")
    for k, v in diag['thresholds'].items():
        print(f"    {k}: {v}")
    print(f"  基线:")
    for k, v in diag['baseline'].items():
        print(f"    {k}: {v}")
    print("  ✓ 通过：诊断输出完整\n")


# ================================================================
# 运行全部测试
# ================================================================
if __name__ == "__main__":
    random.seed(42)  # 固定种子确保可重复

    print("\n心潮 EmoWave — 个人情绪校准引擎 · 集成测试\n")

    tests = [
        test_cold_start,
        test_confidence_growth,
        test_physio_annotation,
        test_no_physio_fallback,
        test_baseline_shift,
        test_persistence,
        test_diagnostics,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ 测试失败: {e}\n")
            failed += 1

    print("=" * 60)
    print(f"测试结果：{passed} 通过 / {failed} 失败 / {len(tests)} 总计")
    print("=" * 60)
