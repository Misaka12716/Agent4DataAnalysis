import sys
from pathlib import Path

import pytest

# 使 tests 可从仓库根目录运行：pytest tests/
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def disable_sandbox_by_default(monkeypatch):
    """默认关闭沙箱，避免现有单测依赖本地 subprocess。"""
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "0")


@pytest.fixture
def enable_sandbox(monkeypatch):
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("CUBE_TEMPLATE_ID", "tpl-test")
    monkeypatch.setenv("E2B_API_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("E2B_API_KEY", "e2b_test")
