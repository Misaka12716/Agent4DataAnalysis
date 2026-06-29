import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

from runtime.factory import clear_runtime_cache, ensure_runtime
from runtime.local.runtime import LocalRuntime
from runtime.sandbox_adapter import SandboxRuntimeAdapter
from utils.workspace_manager import init_workspace


@pytest.fixture
def session_id(isolated_workspaces, monkeypatch):
    sid = str(uuid.uuid4())
    user_id = 1
    root = init_workspace(user_id, sid)
    clear_runtime_cache(sid)

    def mock_get_workspace_path(session_id: str):
        return root if session_id == sid else None

    def mock_get_session_user(session_id: str):
        if session_id == sid:
            return {"user_id": user_id, "session_id": sid, "workspace_abs_path": root}, None
        return None, None

    monkeypatch.setattr(
        "db.session_store.SessionStore.get_workspace_path",
        staticmethod(mock_get_workspace_path),
    )
    monkeypatch.setattr(
        "db.session_store.SessionStore.get_session_user",
        staticmethod(mock_get_session_user),
    )

    yield sid
    clear_runtime_cache(sid)


def test_factory_defaults_to_local(session_id, monkeypatch):
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "0")
    clear_runtime_cache(session_id)
    rt = ensure_runtime(session_id)
    assert isinstance(rt, LocalRuntime)
    assert rt.backend == "local"


def test_factory_uses_sandbox_when_enabled(session_id, monkeypatch):
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "1")
    clear_runtime_cache(session_id)
    mock_adapter = MagicMock(spec=SandboxRuntimeAdapter)
    mock_adapter.backend = "sandbox"

    with patch(
        "runtime.factory.SandboxRuntimeAdapter.bind",
        return_value=mock_adapter,
    ) as bind_mock:
        rt = ensure_runtime(session_id)

    bind_mock.assert_called_once_with(session_id)
    assert rt is mock_adapter


def test_factory_falls_back_when_sandbox_unavailable(session_id, monkeypatch):
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "1")
    clear_runtime_cache(session_id)

    with patch("runtime.factory.SandboxRuntimeAdapter.bind", return_value=None):
        rt = ensure_runtime(session_id)

    assert isinstance(rt, LocalRuntime)


def test_factory_caches_runtime(session_id, monkeypatch):
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "0")
    clear_runtime_cache(session_id)
    first = ensure_runtime(session_id)
    second = ensure_runtime(session_id)
    assert first is second
