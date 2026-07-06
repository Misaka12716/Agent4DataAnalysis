# backend/project_lifecycle.py
# 项目生命周期：成果沉淀（outputs/）与归档快照（archive/）

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from typing import List, Optional

from db.project_schema import ASSET_TYPE_ANALYSIS_OUTPUT
from db.project_store import ProjectStore
from db.session_store import SessionStore
from reader.file_types import classify_file
from utils.workspace_manager import resolve_project_root, resolve_workspace_root

_PROMOTE_EXTENSIONS = {
    ".md",
    ".xlsx",
    ".xls",
    ".csv",
    ".tsv",
    ".json",
    ".html",
    ".htm",
    ".png",
    ".jpg",
    ".jpeg",
}
_SKIP_FILENAMES = {"SESSION_MEMORY.md"}


def _should_promote_file(file_name: str) -> bool:
    if file_name in _SKIP_FILENAMES:
        return False
    ext = os.path.splitext(file_name)[1].lower()
    return ext in _PROMOTE_EXTENSIONS


def promote_session_outputs(session_id: str) -> None:
    """将会话工作区关键产出复制到项目 outputs/<session_id>/ 并登记资产。"""
    try:
        row, err = SessionStore.get_session_user(session_id)
        if err or not row or not row.get("project_id"):
            return
        project_id = int(row["project_id"])
        project_root = resolve_project_root(project_id)
        session_root = resolve_workspace_root(session_id)
        if not project_root or not session_root:
            return

        outputs_dir = os.path.join(project_root, "outputs", session_id)
        os.makedirs(outputs_dir, exist_ok=True)

        for current_root, _dir_names, file_names in os.walk(session_root):
            for file_name in file_names:
                if not _should_promote_file(file_name):
                    continue
                src_path = os.path.join(current_root, file_name)
                inner_rel = os.path.relpath(src_path, session_root).replace(os.sep, "/")
                dest_path = os.path.join(outputs_dir, inner_rel)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                try:
                    shutil.copy2(src_path, dest_path)
                except OSError:
                    continue
                rel_path = f"outputs/{session_id}/{inner_rel}"
                ProjectStore.create_asset(
                    project_id=project_id,
                    asset_type=ASSET_TYPE_ANALYSIS_OUTPUT,
                    relative_path=rel_path,
                    session_id=session_id,
                    original_filename=file_name,
                    file_category=classify_file(file_name),
                )
    except Exception:
        pass


def snapshot_project_on_archive(project_id: int) -> str:
    """归档前将 raw/、outputs/ 快照到 archive/<timestamp>/，返回相对项目根的路径。"""
    project_root = resolve_project_root(project_id)
    if not project_root:
        return ""

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snap_rel = f"archive/{timestamp}"
    snap_root = os.path.join(project_root, snap_rel)
    manifest_files: List[str] = []

    for sub in ("raw", "outputs"):
        src = os.path.join(project_root, sub)
        if not os.path.isdir(src):
            continue
        for current_root, _dir_names, file_names in os.walk(src):
            for file_name in file_names:
                src_path = os.path.join(current_root, file_name)
                inner_rel = os.path.relpath(src_path, src).replace(os.sep, "/")
                dest_path = os.path.join(snap_root, sub, inner_rel)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                try:
                    shutil.copy2(src_path, dest_path)
                    manifest_files.append(f"{sub}/{inner_rel}")
                except OSError:
                    continue

    os.makedirs(snap_root, exist_ok=True)
    manifest = {
        "project_id": project_id,
        "archived_at": datetime.now().isoformat(),
        "files": manifest_files,
    }
    try:
        with open(os.path.join(snap_root, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return snap_rel
