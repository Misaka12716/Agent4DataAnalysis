# backend/project_asset_registry.py
# 项目资产登记薄层封装

import os
from typing import Optional

from db.project_schema import ASSET_TYPE_ANALYSIS_OUTPUT, ASSET_TYPE_UPLOAD
from db.project_store import ProjectStore
from db.session_store import SessionStore
from reader.file_types import classify_file
from utils.workspace_manager import resolve_project_root, resolve_workspace_root

_ANALYSIS_EXTENSIONS = {".py", ".md", ".xlsx", ".xls", ".csv", ".tsv", ".json", ".html", ".htm", ".png", ".jpg", ".jpeg"}
_SKIP_FILENAMES = {"SESSION_MEMORY.md"}


def relative_path_for_project_asset(
    project_root: str,
    session_root: str,
    session_id: str,
    file_abs_path: str,
) -> str:
    """计算相对项目根的路径；历史会话在 project 目录外时用 legacy/<session_id>/ 前缀。"""
    try:
        rel = os.path.relpath(file_abs_path, project_root).replace(os.sep, "/")
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    inner = os.path.relpath(file_abs_path, session_root).replace(os.sep, "/")
    return f"legacy/{session_id}/{inner}"


def register_upload(
    project_id: int,
    session_id: Optional[str],
    relative_path: str,
    original_filename: Optional[str] = None,
    file_category: Optional[str] = None,
) -> None:
    try:
        ProjectStore.create_asset(
            project_id=project_id,
            asset_type=ASSET_TYPE_UPLOAD,
            relative_path=relative_path,
            session_id=session_id,
            original_filename=original_filename,
            file_category=file_category,
        )
    except Exception:
        pass


def register_analysis_outputs(session_id: str) -> None:
    """扫描会话工作区产出并登记到 project_assets（仅当 session 关联 project 时）。"""
    try:
        row, err = SessionStore.get_session_user(session_id)
        if err or not row:
            return
        project_id = row.get("project_id")
        if not project_id:
            return
        project_id = int(project_id)
        project_root = resolve_project_root(project_id)
        session_root = resolve_workspace_root(session_id)
        if not project_root or not session_root:
            return

        for current_root, _dir_names, file_names in os.walk(session_root):
            for file_name in file_names:
                if file_name in _SKIP_FILENAMES:
                    continue
                ext = os.path.splitext(file_name)[1].lower()
                if ext not in _ANALYSIS_EXTENSIONS:
                    continue
                abs_path = os.path.join(current_root, file_name)
                rel_path = relative_path_for_project_asset(
                    project_root, session_root, session_id, abs_path
                )
                ProjectStore.create_asset(
                    project_id=project_id,
                    asset_type=ASSET_TYPE_ANALYSIS_OUTPUT,
                    relative_path=rel_path,
                    session_id=session_id,
                    original_filename=file_name,
                    file_category=classify_file(file_name),
                )
        from backend.project_lifecycle import promote_session_outputs

        promote_session_outputs(session_id)
    except Exception:
        pass


def register_template_run_outputs(session_id: str, workspace_root: str) -> None:
    """登记 template_runs 目录下的产出。"""
    try:
        row, err = SessionStore.get_session_user(session_id)
        if err or not row or not row.get("project_id"):
            return
        project_id = int(row["project_id"])
        project_root = resolve_project_root(project_id)
        if not project_root:
            return
        runs_dir = os.path.join(workspace_root, "template_runs")
        if not os.path.isdir(runs_dir):
            return
        for current_root, _dir_names, file_names in os.walk(runs_dir):
            for file_name in file_names:
                abs_path = os.path.join(current_root, file_name)
                rel_path = relative_path_for_project_asset(
                    project_root, workspace_root, session_id, abs_path
                )
                ProjectStore.create_asset(
                    project_id=project_id,
                    asset_type=ASSET_TYPE_ANALYSIS_OUTPUT,
                    relative_path=rel_path,
                    session_id=session_id,
                    original_filename=file_name,
                    file_category=classify_file(file_name),
                )
        from backend.project_lifecycle import promote_session_outputs

        promote_session_outputs(session_id)
    except Exception:
        pass
