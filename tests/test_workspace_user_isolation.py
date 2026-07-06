import os
import uuid

import pytest

from utils.workspace_manager import (
    get_workspace_session_id_from_abs_path,
    init_project_workspace,
    init_session_in_project,
    init_workspace,
    resolve_workspace_root,
    workspace_path_for,
)


def test_user_session_path_layout(isolated_workspaces):
    sid = str(uuid.uuid4())
    path = init_workspace(user_id=42, session_id=sid)
    assert path == str(isolated_workspaces / "42" / sid)
    assert os.path.isdir(path)


def test_users_are_isolated(isolated_workspaces):
    sid1 = str(uuid.uuid4())
    sid2 = str(uuid.uuid4())
    p1 = init_workspace(user_id=1, session_id=sid1)
    p2 = init_workspace(user_id=2, session_id=sid2)
    assert p1 != p2
    assert os.path.basename(os.path.dirname(p1)) == "1"
    assert os.path.basename(os.path.dirname(p2)) == "2"


def test_resolve_prefers_db_path(isolated_workspaces, monkeypatch):
    sid = str(uuid.uuid4())
    on_disk = init_workspace(user_id=3, session_id=sid)
    alt = str(isolated_workspaces / "3" / "other-session")
    os.makedirs(alt, exist_ok=True)

    def mock_get_workspace_path(session_id: str):
        return alt if session_id == sid else None

    def mock_get_session_user(session_id: str):
        if session_id == sid:
            return {"user_id": 3, "session_id": sid, "workspace_abs_path": alt}, None
        return None, None

    monkeypatch.setattr(
        "db.session_store.SessionStore.get_workspace_path",
        staticmethod(mock_get_workspace_path),
    )
    monkeypatch.setattr(
        "db.session_store.SessionStore.get_session_user",
        staticmethod(mock_get_session_user),
    )

    assert resolve_workspace_root(sid) == os.path.abspath(alt)
    assert resolve_workspace_root(sid) != on_disk


def test_session_id_from_abs_path(isolated_workspaces):
    sid = str(uuid.uuid4())
    abs_path = init_workspace(user_id=7, session_id=sid)
    nested = os.path.join(abs_path, "data.xlsx")
    assert get_workspace_session_id_from_abs_path(nested) == sid


def test_workspace_path_for(isolated_workspaces):
    p = workspace_path_for(99, "abc-123")
    assert p.endswith(os.path.join("99", "abc-123"))


def test_project_workspace_layout(isolated_workspaces):
    project_path = init_project_workspace(user_id=10, project_id=5)
    assert project_path == str(isolated_workspaces / "10" / "5")
    for sub in ("raw", "processed", "outputs", "archive", "sessions"):
        assert os.path.isdir(os.path.join(project_path, sub))


def test_session_in_project_layout(isolated_workspaces):
    sid = str(uuid.uuid4())
    session_path = init_session_in_project(user_id=10, project_id=5, session_id=sid)
    expected = os.path.join(str(isolated_workspaces / "10" / "5" / "sessions" / sid))
    assert session_path == os.path.abspath(expected)
    assert os.path.isdir(session_path)


def test_init_workspace_with_project_id(isolated_workspaces):
    sid = str(uuid.uuid4())
    path = init_workspace(user_id=10, session_id=sid, project_id=5)
    assert path.endswith(os.path.join("5", "sessions", sid))


def test_session_id_from_project_layout_path(isolated_workspaces):
    sid = str(uuid.uuid4())
    abs_path = init_session_in_project(user_id=10, project_id=5, session_id=sid)
    nested = os.path.join(abs_path, "data.xlsx")
    assert get_workspace_session_id_from_abs_path(nested) == sid
