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
# 必须先保存真实 connect，再 patch（供 tests/analysis/functional 恢复）
from _pymysql_sentinel import REAL_PYMYSQL_CONNECT  # noqa: E402
import pymysql as _pymysql_mod

_pymysql_patch = patch("pymysql.connect", return_value=MagicMock())
_pymysql_patch.start()


def enable_real_pymysql() -> None:
    """功能集成测：停掉 mock，恢复真实 pymysql.connect。"""
    from unittest import mock as _mock

    try:
        _pymysql_patch.stop()
    except RuntimeError:
        pass
    for patcher in list(getattr(_mock.patch, "_active_patches", []) or []):
        if getattr(patcher, "attribute", None) == "connect":
            try:
                patcher.stop()
            except RuntimeError:
                pass
    _pymysql_mod.connect = REAL_PYMYSQL_CONNECT


def restore_mock_pymysql() -> None:
    """功能集成测结束后恢复 mock，避免污染其它用例。"""
    try:
        _pymysql_patch.start()
    except RuntimeError:
        pass


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
