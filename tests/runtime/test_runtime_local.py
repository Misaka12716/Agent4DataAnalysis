import os
import uuid

import pytest

from runtime.factory import clear_runtime_cache, ensure_runtime
from runtime.local.runtime import LocalRuntime
from runtime.validation import ValidationError, validate_python_command
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


def test_path_traversal_rejected(session_id):
    rt = ensure_runtime(session_id)
    assert rt.files.write("../escape.txt", "x") is None
    assert rt.files.read("../escape.txt") is None
    assert not rt.files.exists("../etc/passwd")


def test_write_read_text(session_id):
    rt = ensure_runtime(session_id)
    info = rt.files.write("hello.txt", "你好\nworld")
    assert info is not None
    assert info.path == "hello.txt"
    assert rt.files.read("hello.txt", format="text") == "你好\nworld"
    assert rt.files.exists("hello.txt")


def test_run_python_success(session_id):
    rt = ensure_runtime(session_id)
    rt.files.write("main.py", 'print("runtime_ok")\n')
    result = rt.commands.run("python3 main.py", cwd=rt.workdir, timeout=30.0)
    assert result.success
    assert "runtime_ok" in result.stdout
    assert result.exit_code == 0


def test_run_python_timeout(session_id):
    rt = ensure_runtime(session_id)
    rt.files.write("slow.py", "import time\ntime.sleep(5)\n")
    result = rt.commands.run("python3 slow.py", cwd=rt.workdir, timeout=0.2)
    assert not result.success
    assert "超时" in result.stderr


def test_validate_python_command_rejects_shell_injection(session_id):
    rt = ensure_runtime(session_id)
    rt.files.write("main.py", "pass\n")
    with pytest.raises(ValidationError):
        validate_python_command("python3 main.py; rm -rf /", session_id)
    result = rt.commands.run("python3 main.py; echo pwned", cwd=rt.workdir)
    assert not result.success


def test_local_runtime_bind(session_id):
    rt = LocalRuntime.bind(session_id)
    assert rt.backend == "local"
    assert os.path.isdir(rt.workdir)
