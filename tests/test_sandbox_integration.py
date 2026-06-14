import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.workspace_manager import init_workspace
from worker.workspace_worker import run_python_in_workspace


@pytest.fixture
def sandbox_session(enable_sandbox, monkeypatch):
    monkeypatch.setenv("CUBE_SANDBOX_ENABLED", "1")
    session_id = "test-sandbox-session"
    init_workspace(session_id)
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

    with patch("sandbox.session_manager.ensure_sandbox", return_value=mock_sb), patch(
        "sandbox.session_manager.get_sandbox", return_value=mock_sb
    ), patch("sandbox.files.sync_to_local", return_value="/tmp/ws"), patch(
        "sandbox.session_manager._save_meta"
    ), patch("sandbox.session_manager._load_meta", return_value=None):
        yield session_id, mock_sb


def test_run_python_in_sandbox_invokes_commands(sandbox_session):
    session_id, mock_sb = sandbox_session
    out = run_python_in_workspace(session_id, "main.py", timeout=120)
    assert out["success"] is True
    assert out["stdout"] == "hello\n"
    mock_sb.commands.run.assert_called_once()
    cmd = mock_sb.commands.run.call_args[0][0]
    assert "python3" in cmd
    assert "main.py" in cmd


def test_write_file_uses_sandbox(sandbox_session):
    from utils.workspace_file_ops import write_file, read_file

    session_id, mock_sb = sandbox_session
    with patch("sandbox.files.write_text", return_value=True) as mock_write:
        ok = write_file(session_id, "main.py", "print(1)")
    assert ok is True
    mock_write.assert_called_once()
    with patch("sandbox.files.read_text", return_value="print(1)"):
        text = read_file(session_id, "main.py")
    assert text == "print(1)"


def test_write_bytes_file_sandbox(sandbox_session):
    from utils.workspace_file_ops import write_bytes_file

    session_id, mock_sb = sandbox_session
    with patch("sandbox.files.write_bytes", return_value=True) as mock_write:
        ok = write_bytes_file(session_id, "data.xlsx", b"\x00\x01")
    assert ok is True
    mock_write.assert_called_once()


@pytest.mark.integration
def test_live_sandbox_roundtrip():
    if os.getenv("RUN_CUBE_SANDBOX_INTEGRATION") != "1":
        pytest.skip("set RUN_CUBE_SANDBOX_INTEGRATION=1 to run live sandbox test")
    if not os.getenv("CUBE_TEMPLATE_ID"):
        pytest.skip("CUBE_TEMPLATE_ID required")

    session_id = "integration-sandbox-session"
    init_workspace(session_id)
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
