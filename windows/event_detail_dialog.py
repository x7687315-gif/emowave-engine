"""windows/event_detail_dialog.py — 事件补全 / 修正窗口

事件回顾 & 历史记录此前只能看、不能填：记录产生的事件里"触发因素 / 躯体症状 /
应对方式 / 自评峰值"恒为空（未记录）。本窗口给用户一个补录的地方：勾选标签 +
拖自评峰值，保存到 emotion_events（只更新可补全字段，不动 created_at/峰值/raw）。

风格沿用 v3 精密仪器：暖色板、直角、无 emoji。
"""
import json

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QSlider, QPushButton,
    QGridLayout, QFrame,
)
from PyQt5.QtGui import QFont

from theme import COLORS, app_font

TRIGGER_TAGS = ['工作压力', '人际冲突', '健康担忧', '财务问题', '回忆触发', '未知']
BODY_SYMPTOMS = ['心慌', '胸闷', '呼吸急促', '肌肉紧张', '头痛', '肠胃不适', '乏力', '失眠']
COPING_METHODS = ['深呼吸', '正念冥想', '散步', '倾诉', '书写', '暂停休息', '运动']


def _as_list(value):
    """db 里这些字段是 JSON 字符串；也兼容已是 list。"""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


class _CheckGrid(QFrame):
    """一组可多选标签（3 列网格）。"""

    def __init__(self, options, selected, parent=None):
        super().__init__(parent)
        self._boxes = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        for i, opt in enumerate(options):
            cb = QCheckBox(opt)
            cb.setStyleSheet(f"QCheckBox {{ color: {COLORS['ink_2']}; font-size: 12px; }}")
            cb.setChecked(opt in selected)
            self._boxes.append(cb)
            grid.addWidget(cb, i // 3, i % 3)

    def selected(self):
        return [cb.text() for cb in self._boxes if cb.isChecked()]


class EventDetailDialog(QDialog):
    """补全/修正一条情绪事件的自报信息。"""

    def __init__(self, event, db, parent=None):
        super().__init__(parent)
        self._event = dict(event or {})
        self._db = db
        self.setWindowTitle("补全事件信息")
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        title = QLabel("补全 / 修正这次情绪事件")
        title.setFont(app_font(15, QFont.DemiBold))
        title.setStyleSheet(f"color: {COLORS['ink']};")
        root.addWidget(title)

        peak = self._event.get("peak_arousal")
        sub = QLabel(f"峰值唤醒 {float(peak):.2f} · 采样 {self._event.get('sample_count', 0)} 点"
                     if peak is not None else "补录触发因素 / 躯体症状 / 应对方式 / 自评峰值")
        sub.setFont(app_font(11))
        sub.setStyleSheet(f"color: {COLORS['muted']};")
        root.addWidget(sub)

        # 触发因素
        root.addWidget(self._section_label("触发因素"))
        self.trigger_grid = _CheckGrid(TRIGGER_TAGS, _as_list(self._event.get("trigger_tags")))
        root.addWidget(self.trigger_grid)

        # 躯体症状
        root.addWidget(self._section_label("躯体症状"))
        self.body_grid = _CheckGrid(BODY_SYMPTOMS, _as_list(self._event.get("body_symptoms")))
        root.addWidget(self.body_grid)

        # 应对方式
        root.addWidget(self._section_label("应对方式"))
        self.coping_grid = _CheckGrid(COPING_METHODS, _as_list(self._event.get("coping_methods")))
        root.addWidget(self.coping_grid)

        # 自评峰值（0–10）
        root.addWidget(self._section_label("自评峰值（当时最强烈的程度，0–10）"))
        rating_row = QHBoxLayout()
        rating_row.setSpacing(10)
        try:
            cur = float(self._event.get("user_peak_rating") or 0.0)
        except (ValueError, TypeError):
            cur = 0.0
        self.rating_slider = QSlider(Qt.Horizontal)
        self.rating_slider.setRange(0, 10)
        self.rating_slider.setValue(int(round(cur)))
        self.rating_value = QLabel(f"{int(round(cur))}/10")
        self.rating_value.setFont(app_font(12))
        self.rating_value.setStyleSheet(f"color: {COLORS['ink']};")
        self.rating_value.setFixedWidth(44)
        self.rating_slider.valueChanged.connect(
            lambda v: self.rating_value.setText(f"{v}/10"))
        rating_row.addWidget(self.rating_slider, 1)
        rating_row.addWidget(self.rating_value)
        root.addLayout(rating_row)

        root.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLORS['ink_2']};"
            f" border: 1px solid {COLORS['rule']}; padding: 8px 18px; font-size: 12px; }}"
        )
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; color: {COLORS['surface']};"
            f" border: none; padding: 8px 24px; font-size: 12px; font-weight: 600;"
            f" letter-spacing: 1.4px; }} QPushButton:hover {{ background: {COLORS['accent_ink']}; }}"
        )
        save.clicked.connect(self._on_save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

    @staticmethod
    def _section_label(text):
        lbl = QLabel(text)
        lbl.setFont(app_font(9, QFont.DemiBold))
        lbl.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1.4px;")
        return lbl

    def collect(self):
        """汇总当前表单为可写字段 dict（不写库，便于测试）。"""
        return {
            "trigger_tags": self.trigger_grid.selected(),
            "body_symptoms": self.body_grid.selected(),
            "coping_methods": self.coping_grid.selected(),
            "user_peak_rating": float(self.rating_slider.value()),
        }

    def save(self):
        event_id = self._event.get("event_id")
        if self._db is None or not event_id:
            return False
        self._db.update_event(event_id, self.collect())
        return True

    def _on_save(self):
        if self.save():
            self.accept()
