import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.workspace_manager import init_workspace
from worker.workspace_worker import run_python_in_workspace


def _mock_session_store(monkeypatch, session_id: str, user_id: int, root: str) -> None:
    def mock_get_workspace_path(sid: str):
        return root if sid == session_id else None

    def mock_get_session_user(sid: str):
        if sid == session_id:
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


@pytest.fixture
def sandbox_session(enable_sandbox, isolated_workspaces, monkeypatch):
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "1")
    session_id = "test-sandbox-session"
    user_id = 1
    root = init_workspace(user_id, session_id)
    _mock_session_store(monkeypatch, session_id, user_id, root)

    mock_sb = MagicMock()
    mock_sb.sandbox_id = "sbx-123"
    mock_result = MagicMock()
    mock_result.stdout = "hello\n"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_sb.commands.run.return_value = mock_result
    mock_sb.files.write.return_value = None
    mock_sb.files.read.return_value = b"print('x')"
    mock_sb.files.list.return_value = []
    mock_sb.files.exists.return_value = True

    with patch("runtime.sandbox_adapter.try_ensure_sandbox", return_value=True), patch(
        "runtime.sandbox_adapter.ensure_sandbox"
    ), patch("runtime.sandbox_adapter.get_sandbox", return_value=mock_sb), patch(
        "runtime.sandbox_adapter.pause_sandbox"
    ), patch("sandbox.files.sync_to_local", return_value=root), patch(
        "sandbox.session_manager._save_meta"
    ), patch("sandbox.session_manager._load_meta", return_value=None):
        from runtime.factory import clear_runtime_cache

        clear_runtime_cache(session_id)
        yield session_id, mock_sb, root
        clear_runtime_cache(session_id)


def test_run_python_in_sandbox_invokes_commands(sandbox_session):
    from utils.workspace_file_ops import write_file

    session_id, mock_sb, root = sandbox_session
    write_file(session_id, "main.py", "print('hello')")
    # 沙箱 write 经 mock；Worker 校验脚本需本地镜像存在（与 sync_to_local 行为一致）
    with open(os.path.join(root, "main.py"), "w", encoding="utf-8") as f:
        f.write("print('hello')\n")
    out = run_python_in_workspace(session_id, "main.py", timeout=120)
    assert out["success"] is True
    assert out["stdout"] == "hello\n"
    mock_sb.commands.run.assert_called_once()
    cmd = mock_sb.commands.run.call_args[0][0]
    assert "python3" in cmd
    assert "main.py" in cmd


def test_write_file_uses_sandbox(sandbox_session):
    from utils.workspace_file_ops import write_file, read_file

    session_id, mock_sb, _root = sandbox_session
    ok = write_file(session_id, "main.py", "print(1)")
    assert ok is True
    mock_sb.files.write.assert_called()
    mock_sb.files.read.return_value = b"print(1)"
    text = read_file(session_id, "main.py")
    assert text == "print(1)"


def test_write_bytes_file_sandbox(sandbox_session):
    from utils.workspace_file_ops import write_bytes_file

    session_id, mock_sb, _root = sandbox_session
    ok = write_bytes_file(session_id, "data.xlsx", b"\x00\x01")
    assert ok is True
    mock_sb.files.write.assert_called()


@pytest.mark.integration
def test_live_sandbox_roundtrip(tmp_path, monkeypatch):
    if os.getenv("RUN_CUBE_SANDBOX_INTEGRATION") != "1":
        pytest.skip("set RUN_CUBE_SANDBOX_INTEGRATION=1 to run live sandbox test")
    if not os.getenv("CUBE_TEMPLATE_ID"):
        pytest.skip("CUBE_TEMPLATE_ID required")

    monkeypatch.setenv("TEMP_FOLDER", str(tmp_path) + os.sep)
    session_id = "integration-sandbox-session"
    user_id = 1
    init_workspace(user_id, session_id)
    from sandbox.session_manager import ensure_sandbox, pause_sandbox
    from sandbox.files import write_text, sync_to_local
    from sandbox.worker import run_python_in_sandbox

    sb = ensure_sandbox(session_id)
    assert sb.sandbox_id
    write_text(session_id, "hello.py", "print('cube_ok')")
    sync_to_local(session_id)
    result = run_python_in_sandbox(session_id, "hello.py", timeout=120)
    assert result["success"], result.get("stderr")
    assert "cube_ok" in result["stdout"]
    pause_sandbox(session_id)
