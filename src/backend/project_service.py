# backend/project_service.py
# 项目管理业务逻辑

import datetime
import os
from typing import Any, Dict, List, Optional, Tuple

from db.project_schema import (
    DEFAULT_PROJECT_DISPLAY_NAME,
    DEFAULT_PROJECT_INTERNAL_NAME,
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_ARCHIVED,
    PROJECT_SUBDIRS,
    RESERVED_PROJECT_NAMES,
)
from db.project_store import ProjectStore
from db.session_store import SessionStore
from utils.workspace_manager import build_workspace_tree, init_project_workspace, resolve_project_root


def _row_to_response(row: Dict[str, Any]) -> dict:
    item = dict(row)
    for key in ("created_at", "updated_at"):
        if key in item and isinstance(item[key], (datetime.datetime, datetime.date)):
            item[key] = item[key].isoformat()
    is_default = str(item.get("name") or "") == DEFAULT_PROJECT_INTERNAL_NAME
    item["is_default"] = is_default
    if is_default:
        item["name"] = DEFAULT_PROJECT_DISPLAY_NAME
    return item


def _enrich_project_access(item: dict, user_id: int, project_row: Dict[str, Any]) -> dict:
    from backend.permission_service import get_effective_project_permissions
    from db.rbac_schema import PROJECT_ROLE_MANAGER
    from db.rbac_store import RbacStore

    perms, access_type, err = get_effective_project_permissions(
        int(item.get("id") or 0),
        user_id,
        project_row,
    )
    if err:
        return item
    access = access_type
    if access_type == "member":
        member, _ = RbacStore.get_member(int(item.get("id") or 0), user_id)
        role = str((member or {}).get("role") or "member").strip().lower()
        if role == PROJECT_ROLE_MANAGER:
            access = "project_manager"
        else:
            access = "member"
    item["access"] = access
    item["permissions"] = sorted(perms)
    item["is_shared"] = access not in ("owner", "admin")
    return item


class ProjectService:
    @staticmethod
    def is_default_project(row: Optional[dict]) -> bool:
        if not row:
            return False
        return str(row.get("name") or "") in RESERVED_PROJECT_NAMES or bool(row.get("is_default"))

    @staticmethod
    def ensure_default_project(user_id: int) -> Tuple[Optional[dict], Optional[str]]:
        """确保用户拥有「个人默认」项目，并将无 project_id 的历史会话归属到该项目。"""
        if user_id <= 0:
            return None, "user_id 必须为正整数"
        ok, err = ProjectStore.ensure_schema()
        if not ok:
            return None, err

        row, err = ProjectStore.get_default_project(user_id)
        if err:
            return None, err
        if not row:
            placeholder = os.path.abspath(os.path.join("tmp", "workspaces", str(user_id), "pending"))
            project_id, err = ProjectStore.create_project(
                user_id, DEFAULT_PROJECT_INTERNAL_NAME, placeholder
            )
            if err or not project_id:
                return None, err or "创建个人默认项目失败"
            workspace_abs = init_project_workspace(user_id, project_id)
            ok, err = ProjectStore.set_workspace_path(project_id, workspace_abs)
            if err or not ok:
                return None, err or "更新个人默认项目工作区失败"
            row, err = ProjectStore.get_project(project_id)
            if err or not row:
                return None, err or "创建后查询个人默认项目失败"

        project_id = int(row.get("id") or 0)
        return _row_to_response(row), None

    @staticmethod
    def bootstrap_user_projects(user_id: int) -> Tuple[Optional[dict], Optional[str]]:
        """初始化/迁移脚本用：确保个人默认项目并将无 project_id 的历史会话归属到该项目。"""
        default_row, err = ProjectService.ensure_default_project(user_id)
        if err or not default_row:
            return None, err or "无法创建个人默认项目"
        project_id = int(default_row.get("id") or 0)
        if project_id > 0:
            _, err = ProjectStore.assign_orphan_sessions(user_id, project_id)
            if err:
                return None, err
        return default_row, None

    @staticmethod
    def resolve_project_id(user_id: int, project_id: Optional[int]) -> Tuple[int, Optional[str]]:
        """project_id 为空或 <=0 时解析为个人默认项目 ID。"""
        if project_id and int(project_id) > 0:
            return int(project_id), None
        default_row, err = ProjectService.ensure_default_project(user_id)
        if err or not default_row:
            return 0, err or "无法解析个人默认项目"
        return int(default_row.get("id") or 0), None

    @staticmethod
    def create_project(user_id: int, name: str) -> Tuple[Optional[dict], Optional[str]]:
        clean_name = (name or "").strip()
        if not clean_name:
            return None, "name 不能为空"
        if clean_name in RESERVED_PROJECT_NAMES:
            return None, f"项目名「{DEFAULT_PROJECT_DISPLAY_NAME}」为系统保留，请换一个名称"
        if user_id <= 0:
            return None, "user_id 必须为正整数"
        exists, err = SessionStore.user_exists(user_id)
        if err:
            return None, f"查询用户失败: {err}"
        if not exists:
            return None, "user_id 不存在，请先登录或注册"

        ok, err = ProjectStore.ensure_schema()
        if not ok:
            return None, err

        placeholder = os.path.abspath(os.path.join("tmp", "workspaces", str(user_id), "pending"))
        project_id, err = ProjectStore.create_project(user_id, clean_name, placeholder)
        if err or not project_id:
            return None, err or "创建项目失败"

        workspace_abs = init_project_workspace(user_id, project_id)
        ok, err = ProjectStore.set_workspace_path(project_id, workspace_abs)
        if err or not ok:
            return None, err or "更新项目工作区路径失败"

        row, err = ProjectStore.get_project(project_id)
        if err or not row:
            return None, err or "创建后查询项目失败"
        return _row_to_response(row), None

    @staticmethod
    def list_projects(user_id: int) -> Tuple[List[dict], Optional[str]]:
        from db.rbac_store import RbacStore

        _, err = ProjectService.ensure_default_project(user_id)
        if err:
            return [], err
        rows, err = RbacStore.list_projects_for_user(user_id)
        if err:
            return [], err
        result = []
        default_item = None
        for row in rows:
            item = _row_to_response(row)
            item = _enrich_project_access(item, user_id, row)
            cnt, _ = ProjectStore.count_sessions_by_project(int(item.get("id") or 0))
            item["session_count"] = cnt
            if item.get("is_default"):
                default_item = item
            else:
                result.append(item)
        if default_item:
            result.insert(0, default_item)
        return result, None

    @staticmethod
    def get_project_detail(project_id: int, viewer_user_id: Optional[int] = None) -> Tuple[Optional[dict], Optional[str]]:
        row, err = ProjectStore.get_project(project_id)
        if err:
            return None, err
        if not row:
            return None, "项目不存在"
        item = _row_to_response(row)
        root = resolve_project_root(project_id) or str(row.get("workspace_abs_path") or "")
        subdirs = {}
        for sub in PROJECT_SUBDIRS:
            subdirs[sub] = os.path.isdir(os.path.join(root, sub)) if root else False
        cnt, _ = ProjectStore.count_sessions_by_project(project_id)
        item["session_count"] = cnt
        item["subdirs"] = subdirs
        if viewer_user_id and viewer_user_id > 0:
            item = _enrich_project_access(item, viewer_user_id, row)
        return item, None

    @staticmethod
    def archive_project(project_id: int) -> Tuple[Optional[dict], Optional[str]]:
        row, err = ProjectStore.get_project(project_id)
        if err:
            return None, err
        if row and str(row.get("name") or "") == DEFAULT_PROJECT_INTERNAL_NAME:
            return None, "个人默认项目不可归档"
        snapshot_path = ""
        try:
            from backend.project_lifecycle import snapshot_project_on_archive

            snapshot_path = snapshot_project_on_archive(project_id)
        except Exception:
            pass
        ok, err = ProjectStore.set_status(project_id, PROJECT_STATUS_ARCHIVED)
        if err or not ok:
            return None, err or "归档失败"
        detail, err = ProjectService.get_project_detail(project_id)
        if detail and snapshot_path:
            detail["archive_snapshot_path"] = snapshot_path
        return detail, err

    @staticmethod
    def rename_project(project_id: int, name: str) -> Tuple[Optional[dict], Optional[str]]:
        clean_name = (name or "").strip()
        if not clean_name:
            return None, "name 不能为空"
        if clean_name in RESERVED_PROJECT_NAMES:
            return None, f"项目名「{DEFAULT_PROJECT_DISPLAY_NAME}」为系统保留，请换一个名称"
        row, err = ProjectStore.get_project(project_id)
        if err:
            return None, err
        if not row:
            return None, "项目不存在"
        if str(row.get("name") or "") == DEFAULT_PROJECT_INTERNAL_NAME:
            return None, "个人默认项目不可重命名"
        ok, err = ProjectStore.update_name(project_id, clean_name)
        if err or not ok:
            return None, err or "重命名失败"
        return ProjectService.get_project_detail(project_id)

    @staticmethod
    def get_project_tree(project_id: int) -> Tuple[Optional[dict], Optional[str]]:
        row, err = ProjectStore.get_project(project_id)
        if err:
            return None, err
        if not row:
            return None, "项目不存在"
        root = resolve_project_root(project_id) or str(row.get("workspace_abs_path") or "")
        if not root or not os.path.isdir(root):
            return None, "项目工作区不存在"
        trees = {}
        for sub in ("raw", "outputs", "archive"):
            sub_path = os.path.join(root, sub)
            if os.path.isdir(sub_path):
                trees[sub] = build_workspace_tree(sub_path)
            else:
                trees[sub] = {"name": sub, "type": "directory", "relative_path": sub, "children": []}
        return {"project_id": project_id, "trees": trees}, None

    @staticmethod
    def restore_project(project_id: int) -> Tuple[Optional[dict], Optional[str]]:
        ok, err = ProjectStore.set_status(project_id, PROJECT_STATUS_ACTIVE)
        if err or not ok:
            return None, err or "恢复失败"
        return ProjectService.get_project_detail(project_id)

    @staticmethod
    def list_assets(project_id: int) -> Tuple[List[dict], Optional[str]]:
        rows, err = ProjectStore.list_assets(project_id)
        if err:
            return [], err
        result = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("created_at"), (datetime.datetime, datetime.date)):
                item["created_at"] = item["created_at"].isoformat()
            result.append(item)
        return result, None

    @staticmethod
    def list_project_sessions(project_id: int) -> Tuple[List[dict], Optional[str]]:
        row, err = ProjectStore.get_project(project_id)
        if err:
            return [], err
        sessions, err = ProjectStore.list_sessions_by_project(project_id)
        return sessions, err
