"""windows/surfing_window.py — 心潮 EmoWave 情绪冲浪记录窗口（禅意紧凑版）

提供情绪冲浪记录界面：
  - 滑条行式布局（标签同行，纵向更省）
  - 触发标签 3 列网格
  - 开始/结束记录，定时采样并绘制情绪轨迹
  - 结束时调用 SessionController 处理事件并通知父窗口
"""
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QCheckBox,
    QPushButton, QGridLayout,
)

from widgets import EmotionCanvas, CardFrame, COLORS
from models import TimeSeriesSample


# 触发标签候选（与 UI 复选框一一对应）
TRIGGER_TAGS = [
    '工作压力', '人际冲突', '健康担忧',
    '财务问题', '回忆触发', '未知',
]

_SLIDER_QSS = (
    f"QSlider::groove:horizontal {{ height: 3px; background: {COLORS['rule']};"
    f" border-radius: 1px; }}"
    f"QSlider::handle:horizontal {{ width: 14px; margin: -6px 0;"
    f" border-radius: 7px; background: {COLORS['accent']}; }}"
    f"QSlider::sub-page:horizontal {{ background: {COLORS['accent']};"
    f" border-radius: 1px; }}"
)


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
        self.setObjectName('SurfingPage')
        self.setStyleSheet("QWidget#SurfingPage { background-color: {COLORS['bg']}; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # 情绪画布（限高，紧凑）
        self.canvas = EmotionCanvas()
        self.canvas.setMinimumHeight(210)
        layout.addWidget(self.canvas, 1)

        # 实时调节卡片：标签与滑条同行，两行搞定
        adjust_card = CardFrame(title="实时调节")

        row_v = QHBoxLayout()
        row_v.setSpacing(10)
        self.valence_label = QLabel("效价 60")
        self.valence_label.setFixedWidth(58)
        self.valence_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 12px;"
        )
        self.valence_slider = QSlider(Qt.Horizontal)
        self.valence_slider.setRange(0, 100)
        self.valence_slider.setValue(60)
        self.valence_slider.setStyleSheet(_SLIDER_QSS)
        self.valence_slider.valueChanged.connect(
            lambda v: self.valence_label.setText(f"效价 {v}")
        )
        row_v.addWidget(self.valence_label)
        row_v.addWidget(self.valence_slider, 1)
        adjust_card._content_layout.addLayout(row_v)

        row_a = QHBoxLayout()
        row_a.setSpacing(10)
        self.arousal_label = QLabel("唤醒 30")
        self.arousal_label.setFixedWidth(58)
        self.arousal_label.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 12px;"
        )
        self.arousal_slider = QSlider(Qt.Horizontal)
        self.arousal_slider.setRange(0, 100)
        self.arousal_slider.setValue(30)
        self.arousal_slider.setStyleSheet(_SLIDER_QSS)
        self.arousal_slider.valueChanged.connect(
            lambda v: self.arousal_label.setText(f"唤醒 {v}")
        )
        row_a.addWidget(self.arousal_label)
        row_a.addWidget(self.arousal_slider, 1)
        adjust_card._content_layout.addLayout(row_a)

        layout.addWidget(adjust_card)

        # 标签卡片：3 列网格，纵向省一半
        tag_card = CardFrame(title="标签（可多选）")
        grid = QGridLayout()
        grid.setContentsMargins(2, 0, 2, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self.trigger_checks = []
        for i, tag in enumerate(TRIGGER_TAGS):
            cb = QCheckBox(tag)
            cb.setStyleSheet(f"QCheckBox {{ color: {COLORS['ink_soft']}; font-size: 12px; }}")
            self.trigger_checks.append(cb)
            grid.addWidget(cb, i // 3, i % 3)
        tag_card._content_layout.addLayout(grid)
        layout.addWidget(tag_card)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.start_btn = QPushButton("开始记录")
        self.start_btn.setCursor(self.cursor())
        self.start_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['accent']}; color: #FFFFFF;"
            f" border: none; border-radius: 8px; padding: 8px 18px;"
            f" font-size: 13px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {COLORS['accent_hover']}; }}"
        )
        self.start_btn.clicked.connect(self._toggle_recording)
        btn_row.addWidget(self.start_btn)

        self.finish_btn = QPushButton("已平静")
        self.finish_btn.setEnabled(False)
        self.finish_btn.setCursor(self.cursor())
        self.finish_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {COLORS['warn']};"
            f" border: 1px solid {COLORS['warn']}; border-radius: 8px;"
            f" padding: 8px 18px; font-size: 13px; font-weight: 600; }}"
            f"QPushButton:disabled {{ color: {COLORS['muted']};"
            f" border-color: {COLORS['rule']}; }}"
            f"QPushButton:hover:!disabled {{ background-color: #F4EAD8; }}"
        )
        self.finish_btn.clicked.connect(self._finish_recording)
        btn_row.addWidget(self.finish_btn)
        btn_row.addStretch(1)

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
