"""windows/surfing_window.py — 心潮 EmoWave 情绪冲浪记录窗口

提供情绪冲浪记录界面：
  - 实时滑动条调节效价 / 唤醒度
  - 多选触发标签
  - 开始/结束记录，定时采样并绘制情绪轨迹
  - 结束时调用 SessionController 处理事件并通知父窗口
"""
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QCheckBox, QPushButton,
)

from widgets import EmotionCanvas, CardFrame
from models import TimeSeriesSample


# 触发标签候选（与 UI 复选框一一对应）
TRIGGER_TAGS = [
    '工作压力', '人际冲突', '健康担忧',
    '财务问题', '回忆触发', '未知',
]


class SurfingWindow(QWidget):
    """情绪冲浪记录窗口。"""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.parent_window = parent

        # 运行时状态
        self.recording = False
        self.samples = []

        # 定时采样器（每秒一次）
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._sample)

        self._setup_ui()

    # ================================================================
    # UI 构建
    # ================================================================

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # 标题
        title = QLabel("情绪冲浪")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2d2a26;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # 情绪画布
        self.canvas = EmotionCanvas()
        self.canvas.setMinimumHeight(280)
        layout.addWidget(self.canvas)

        # 实时调节卡片
        adjust_card = CardFrame("实时调节")

        self.valence_label = QLabel("效价：60")
        self.valence_slider = QSlider(Qt.Horizontal)
        self.valence_slider.setRange(0, 100)
        self.valence_slider.setValue(60)
        self.valence_slider.valueChanged.connect(
            lambda v: self.valence_label.setText(f"效价：{v}")
        )
        adjust_card.add_widget(self.valence_label)
        adjust_card.add_widget(self.valence_slider)

        self.arousal_label = QLabel("唤醒：30")
        self.arousal_slider = QSlider(Qt.Horizontal)
        self.arousal_slider.setRange(0, 100)
        self.arousal_slider.setValue(30)
        self.arousal_slider.valueChanged.connect(
            lambda v: self.arousal_label.setText(f"唤醒：{v}")
        )
        adjust_card.add_widget(self.arousal_label)
        adjust_card.add_widget(self.arousal_slider)

        layout.addWidget(adjust_card)

        # 标签卡片
        tag_card = CardFrame("标签（可多选）")
        self.trigger_checks = []
        for tag in TRIGGER_TAGS:
            cb = QCheckBox(tag)
            self.trigger_checks.append(cb)
            tag_card.add_widget(cb)
        layout.addWidget(tag_card)

        # 操作按钮
        btn_row = QHBoxLayout()

        self.start_btn = QPushButton("开始记录")
        self.start_btn.setStyleSheet(
            "QPushButton { background-color: #2cb69a; color: white; "
            "border-radius: 8px; padding: 8px 16px; font-size: 14px; font-weight: bold; }"
        )
        self.start_btn.clicked.connect(self._toggle_recording)
        btn_row.addWidget(self.start_btn)

        self.finish_btn = QPushButton("已平静")
        self.finish_btn.setEnabled(False)
        self.finish_btn.setStyleSheet(
            "QPushButton { background-color: #e8a838; color: white; "
            "border-radius: 8px; padding: 8px 16px; font-size: 14px; font-weight: bold; }"
            "QPushButton:disabled { background-color: #d5d0c8; color: #9a958e; }"
        )
        self.finish_btn.clicked.connect(self._finish_recording)
        btn_row.addWidget(self.finish_btn)

        layout.addLayout(btn_row)

    # ================================================================
    # 记录控制
    # ================================================================

    def _toggle_recording(self):
        """切换记录状态：开始时清空并启动定时器，停止时改文字。"""
        if not self.recording:
            # 开始记录
            self.recording = True
            self.samples = []
            self.canvas.clear()

            self.start_btn.setText("停止记录")
            self.finish_btn.setEnabled(True)
            self.timer.start(1000)
        else:
            # 停止记录（仅暂停，不结束事件）
            self.recording = False
            self.timer.stop()
            self.start_btn.setText("开始记录")
            self.finish_btn.setEnabled(False)

    def _sample(self):
        """从滑条取值，构造一个 TimeSeriesSample 并追加到画布与缓存。"""
        if not self.recording:
            return

        valence = self.valence_slider.value() / 100.0
        arousal = self.arousal_slider.value() / 100.0

        sample = TimeSeriesSample(
            timestamp=time.time(),
            valence=valence,
            arousal=arousal,
        )
        self.samples.append(sample)
        self.canvas.add_point(valence, arousal)

    def _collect_trigger_tags(self):
        """收集所有被勾选的触发标签。"""
        return [cb.text() for cb in self.trigger_checks if cb.isChecked()]

    def _finish_recording(self):
        """结束本次事件：停止定时器，提交事件，通知父窗口。"""
        self.timer.stop()
        self.recording = False
        self.start_btn.setText("开始记录")
        self.finish_btn.setEnabled(False)

        trigger_tags = self._collect_trigger_tags()

        result = None
        if self.samples:
            result = self.session.process_event(self.samples, trigger_tags)

        self.samples = []

        # 通知父窗口（若存在且提供了回调）
        if self.parent_window is not None and hasattr(self.parent_window, 'on_surfing_finished'):
            self.parent_window.on_surfing_finished(result)

        return result
