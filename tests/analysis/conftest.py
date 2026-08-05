"""analysis 目录共享 fixtures：供 test_psych_* 与其它 analysis 用例使用。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

# ingest / chunked_upload_finalize → reader.handlers.table 依赖 langchain_openai
if "langchain_openai" not in sys.modules:
    sys.modules["langchain_openai"] = MagicMock()

from psych_test_helpers import (  # noqa: E402
    make_auth_headers,
    make_psych_app,
    rbac_user_row,
)


@pytest.fixture
def auth_headers():
    return make_auth_headers()


@pytest.fixture
def mock_rbac_user():
    with patch("db.rbac_store.RbacStore.get_user", return_value=(rbac_user_row(), None)):
        yield rbac_user_row()


@pytest.fixture
def psych_client(mock_rbac_user):
    """轻量 FastAPI + psych 路由 + JWT 可用的 TestClient。"""
    with TestClient(make_psych_app()) as client:
        yield client
