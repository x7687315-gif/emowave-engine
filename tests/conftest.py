import os
import sys

import pytest

# 确保能 import 应用层模块（main_app / windows / presenters / db / session）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 无头模式运行 Qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    """提供 QApplication 实例，并注入与生产一致的字体/主题。"""
    from PyQt5.QtWidgets import QApplication
    from theme import app_font, build_app_qss

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setFont(app_font(10))
    app.setStyleSheet(build_app_qss())
    yield app
