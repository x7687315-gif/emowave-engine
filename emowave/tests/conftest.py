"""pytest 共享 fixtures（emowave/tests）。"""

import sys
import os

# 把项目根目录加入 sys.path，使 `import emowave` 在 pytest 中可用。
# 这样即使没有 pip install -e . 也能跑测试（重构期常见场景）。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
