import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 使 tests 可从仓库根目录运行：pytest tests/
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# 避免 import db/session_store 时连接真实 MySQL
_pymysql_patch = patch("pymysql.connect", return_value=MagicMock())
_pymysql_patch.start()


@pytest.fixture(autouse=True)
def disable_sandbox_by_default(monkeypatch):
    """默认关闭沙箱，避免现有单测依赖本地 subprocess。"""
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "0")


@pytest.fixture
def isolated_workspaces(tmp_path, monkeypatch):
    """将工作区根目录隔离到 pytest tmp_path。"""
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEMP_FOLDER", str(tmp_path) + os.sep)
    monkeypatch.setattr("utils.workspace_manager.WORKSPACES_ROOT", str(ws_root))
    return ws_root


@pytest.fixture
def enable_sandbox(monkeypatch):
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("CUBE_TEMPLATE_ID", "tpl-test")
    monkeypatch.setenv("E2B_API_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("E2B_API_KEY", "e2b_test")
