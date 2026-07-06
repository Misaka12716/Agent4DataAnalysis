"""项目生命周期：outputs 沉淀与 archive 快照。"""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.project_lifecycle import promote_session_outputs, snapshot_project_on_archive


@pytest.fixture
def lifecycle_layout(isolated_workspaces):
    user_id = 10
    project_id = 2
    session_id = "sess-promote"
    project_root = isolated_workspaces / str(user_id) / str(project_id)
    for sub in ("raw", "processed", "outputs", "archive", "sessions"):
        (project_root / sub).mkdir(parents=True, exist_ok=True)
    session_root = project_root / "sessions" / session_id
    session_root.mkdir(parents=True, exist_ok=True)
    report = session_root / "report.md"
    report.write_text("# report", encoding="utf-8")
    (project_root / "raw" / "data.csv").write_text("a\n1", encoding="utf-8")
    return {
        "user_id": user_id,
        "project_id": project_id,
        "session_id": session_id,
        "project_root": project_root,
        "session_root": session_root,
    }


def test_promote_session_outputs_copies_to_outputs(lifecycle_layout):
    layout = lifecycle_layout
    session_user = {
        "session_id": layout["session_id"],
        "user_id": layout["user_id"],
        "project_id": layout["project_id"],
        "workspace_abs_path": str(layout["session_root"]),
    }
    created = []

    def _create_asset(**kwargs):
        created.append(kwargs)
        return 1, None

    with patch(
        "backend.project_lifecycle.SessionStore.get_session_user",
        return_value=(session_user, None),
    ), patch(
        "backend.project_lifecycle.resolve_project_root",
        return_value=str(layout["project_root"]),
    ), patch(
        "backend.project_lifecycle.resolve_workspace_root",
        return_value=str(layout["session_root"]),
    ), patch("backend.project_lifecycle.ProjectStore.create_asset", side_effect=_create_asset):
        promote_session_outputs(layout["session_id"])

    dest = layout["project_root"] / "outputs" / layout["session_id"] / "report.md"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == "# report"
    assert any(a.get("relative_path", "").startswith("outputs/") for a in created)


def test_snapshot_project_on_archive(lifecycle_layout):
    layout = lifecycle_layout
    (layout["project_root"] / "outputs" / "sess-promote").mkdir(parents=True, exist_ok=True)
    (layout["project_root"] / "outputs" / "sess-promote" / "out.csv").write_text("x", encoding="utf-8")

    with patch(
        "backend.project_lifecycle.resolve_project_root",
        return_value=str(layout["project_root"]),
    ):
        snap_rel = snapshot_project_on_archive(layout["project_id"])

    assert snap_rel.startswith("archive/")
    snap_root = layout["project_root"] / snap_rel
    assert (snap_root / "raw" / "data.csv").is_file()
    assert (snap_root / "outputs" / "sess-promote" / "out.csv").is_file()
    assert (snap_root / "manifest.json").is_file()
