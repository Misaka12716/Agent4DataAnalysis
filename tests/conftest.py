import sys
from pathlib import Path

# 使 tests 可从仓库根目录运行：pytest tests/
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
