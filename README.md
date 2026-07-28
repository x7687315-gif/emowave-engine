# 心潮 EmoWave · 桌面情绪追踪应用

## 快速开始

```bash
pip install -r requirements.txt
python main_app.py
```

## 功能

- **今日仪表盘**: 情绪风险环、基线数据（心率/HRV/睡眠）、最近事件列表
- **情绪冲浪**: 实时效价-唤醒滑条记录，2D 轨迹可视化，标签选择
- **事件回顾**: 单次事件的峰值数据、情绪曲线、躯体症状展示
- **历史记录**: 日历视图、事件表格、JSON 数据导出

## 架构

三层架构，每层职责清晰、可独立测试：

```
UI 层 (PyQt5 窗口)
  ├── windows/dashboard_window.py    今日仪表盘
  ├── windows/surfing_window.py      情绪冲浪记录
  ├── windows/event_summary_window.py 事件回顾
  └── windows/history_window.py      历史记录与报告
         ↕
控制层 (session.py)    ← 桥接 UI 与引擎，管理状态持久化
         ↕
持久层 (db.py)         ← SQLite 本地存储
         ↕
引擎层 (已有模块)
  ├── models.py        核心数据结构
  ├── kalman_filter.py  卡尔曼滤波器
  ├── predictor.py      极点预警
  ├── recommender.py    策略推荐 (LinUCB)
  └── engine.py         主编排器
```

## 文件结构

```
emowave-engine/
├── main_app.py                  # 入口 + MainWindow 集成 + 菜单栏
├── db.py                        # SQLite 持久化层
├── session.py                   # 会话控制器
├── widgets.py                   # 共享自定义控件 (RiskRing, EmotionCanvas, CardFrame)
├── windows/
│   ├── dashboard_window.py
│   ├── surfing_window.py
│   ├── event_summary_window.py
│   └── history_window.py
├── tests/
│   ├── conftest.py              # pytest fixtures (qapp)
│   ├── test_db.py               # 4 tests
│   ├── test_session.py          # 4 tests
│   ├── test_widgets.py          # 3 tests
│   ├── test_dashboard_window.py # 4 tests
│   ├── test_surfing_window.py   # 1 test
│   ├── test_event_summary_window.py # 2 tests
│   ├── test_history_window.py   # 2 tests
│   └── test_main_app.py         # 3 tests
└── requirements.txt
```

## 测试

```bash
python -m pytest tests/ -v
```

共 23 个测试，覆盖所有模块的核心行为。采用 TDD 流程开发：每个功能先写测试 → 看失败 → 写最小实现 → 看通过。

## 隐私设计

所有数据仅存储在设备本地（`~/.emowave/emowave.db`），不上传任何个人信息。
