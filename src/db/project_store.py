# db/project_store.py
# 项目与项目资产的数据访问层

from typing import Any, Dict, List, Optional, Tuple

from db.models import (
    TABLE_SESSION_USER,
    SESSION_USER_ADD_PROJECT_ID_DDL,
    SESSION_USER_ADD_PROJECT_ID_INDEX_DDL,
)
from db.project_schema import (
    TABLE_PROJECTS,
    TABLE_PROJECT_ASSETS,
    PROJECTS_TABLE_DDL,
    PROJECT_ASSETS_TABLE_DDL,
    PROJECT_STATUS_ACTIVE,
    ProjectRow,
    ProjectAssetRow,
)
from utils.mysql_utils import mysql_handler


def _check_column_exists(table_name: str, column_name: str) -> bool:
    try:
        sql = """
            SELECT COUNT(*) AS cnt
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name = %s
        """
        db_name = getattr(mysql_handler, "db", None) or "agent_platform"
        rows, err = mysql_handler.query(sql, (db_name, table_name, column_name))
        if err or not rows:
            return False
        return int(rows[0].get("cnt") or 0) > 0
    except Exception:
        try:
            sql = f"PRAGMA table_info({table_name})"
            rows, err = mysql_handler.query(sql, ())
            if err or not rows:
                return False
            return any(str(r.get("name") or r.get("Name") or "") == column_name for r in rows)
        except Exception:
            return False


def _check_index_exists(table_name: str, index_name: str) -> bool:
    try:
        sql = """
            SELECT COUNT(*) AS cnt
            FROM information_schema.statistics
            WHERE table_schema = %s AND table_name = %s AND index_name = %s
        """
        db_name = getattr(mysql_handler, "db", None) or "agent_platform"
        rows, err = mysql_handler.query(sql, (db_name, table_name, index_name))
        if err or not rows:
            return False
        return int(rows[0].get("cnt") or 0) > 0
    except Exception:
        return False


def _ensure_tables() -> Tuple[bool, Optional[str]]:
    """确保 projects / project_assets 表存在，并为 session_user 补 project_id 列。"""
    try:
        if not mysql_handler._check_table_exists(TABLE_PROJECTS):
            _, err = mysql_handler.execute(PROJECTS_TABLE_DDL)
            if err:
                return False, f"创建表 {TABLE_PROJECTS} 失败: {err}"
        if not mysql_handler._check_table_exists(TABLE_PROJECT_ASSETS):
            _, err = mysql_handler.execute(PROJECT_ASSETS_TABLE_DDL)
            if err:
                return False, f"创建表 {TABLE_PROJECT_ASSETS} 失败: {err}"
        if mysql_handler._check_table_exists(TABLE_SESSION_USER):
            if not _check_column_exists(TABLE_SESSION_USER, "project_id"):
                _, err = mysql_handler.execute(SESSION_USER_ADD_PROJECT_ID_DDL)
                if err and "Duplicate column" not in str(err):
                    return False, f"为 {TABLE_SESSION_USER} 添加 project_id 失败: {err}"
            if not _check_column_exists(TABLE_SESSION_USER, "project_id"):
                pass
            elif not _check_index_exists(TABLE_SESSION_USER, "idx_project_id"):
                _, err = mysql_handler.execute(SESSION_USER_ADD_PROJECT_ID_INDEX_DDL)
                if err and "Duplicate key name" not in str(err) and "already exists" not in str(err).lower():
                    return False, f"为 {TABLE_SESSION_USER} 添加 idx_project_id 失败: {err}"
        from db.rbac_store import _ensure_rbac_schema

        ok_rbac, err_rbac = _ensure_rbac_schema()
        if not ok_rbac:
            return False, err_rbac
        return True, None
    except Exception as e:
        return False, str(e)


class ProjectStore:
    """项目与项目资产存储。"""

    @staticmethod
    def ensure_schema() -> Tuple[bool, Optional[str]]:
        return _ensure_tables()

    @staticmethod
    def get_default_project(user_id: int) -> Tuple[Optional[ProjectRow], Optional[str]]:
        from db.project_schema import DEFAULT_PROJECT_INTERNAL_NAME

        ok, err = _ensure_tables()
        if not ok:
            return None, err
        sql = (
            f"SELECT id, user_id, name, status, workspace_abs_path, created_at, updated_at "
            f"FROM {TABLE_PROJECTS} WHERE user_id = %s AND name = %s LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (user_id, DEFAULT_PROJECT_INTERNAL_NAME))
        if err:
            return None, err
        if not rows:
            return None, None
        return rows[0], None

    @staticmethod
    def assign_orphan_sessions(user_id: int, project_id: int) -> Tuple[int, Optional[str]]:
        """将 user 下 project_id 为空的历史会话归属到指定项目（不移动磁盘文件）。"""
        ok, err = _ensure_tables()
        if not ok:
            return 0, err
        sql = (
            f"UPDATE {TABLE_SESSION_USER} SET project_id = %s "
            f"WHERE user_id = %s AND project_id IS NULL"
        )
        affected, err = mysql_handler.execute(sql, (project_id, user_id))
        return int(affected or 0), err

    @staticmethod
    def create_project(user_id: int, name: str, workspace_abs_path: str) -> Tuple[Optional[int], Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return None, err
        data = {
            "user_id": user_id,
            "name": name.strip(),
            "status": PROJECT_STATUS_ACTIVE,
            "workspace_abs_path": workspace_abs_path,
        }
        _, project_id, err = mysql_handler.insert(TABLE_PROJECTS, data)
        if err:
            return None, err
        return int(project_id or 0), None

    @staticmethod
    def get_project(project_id: int) -> Tuple[Optional[ProjectRow], Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return None, err
        sql = (
            f"SELECT id, user_id, name, status, workspace_abs_path, created_at, updated_at "
            f"FROM {TABLE_PROJECTS} WHERE id = %s LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (project_id,))
        if err:
            return None, err
        if not rows:
            return None, None
        return rows[0], None

    @staticmethod
    def list_by_user(user_id: int) -> Tuple[List[ProjectRow], Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return [], err
        sql = (
            f"SELECT id, user_id, name, status, workspace_abs_path, created_at, updated_at "
            f"FROM {TABLE_PROJECTS} WHERE user_id = %s ORDER BY id DESC"
        )
        rows, err = mysql_handler.query(sql, (user_id,))
        if err:
            return [], err
        return rows or [], None

    @staticmethod
    def list_all() -> Tuple[List[ProjectRow], Optional[str]]:
        """返回全部项目（按 id 倒序），供平台 admin 使用。"""
        ok, err = _ensure_tables()
        if not ok:
            return [], err
        sql = (
            f"SELECT id, user_id, name, status, workspace_abs_path, created_at, updated_at "
            f"FROM {TABLE_PROJECTS} ORDER BY id DESC"
        )
        rows, err = mysql_handler.query(sql, ())
        if err:
            return [], err
        return rows or [], None

    @staticmethod
    def set_workspace_path(project_id: int, workspace_abs_path: str) -> Tuple[bool, Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return False, err
        sql = f"UPDATE {TABLE_PROJECTS} SET workspace_abs_path = %s WHERE id = %s"
        _, err = mysql_handler.execute(sql, (workspace_abs_path, project_id))
        return (err is None, err)

    @staticmethod
    def set_status(project_id: int, status: str) -> Tuple[bool, Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return False, err
        sql = f"UPDATE {TABLE_PROJECTS} SET status = %s WHERE id = %s"
        _, err = mysql_handler.execute(sql, (status, project_id))
        return (err is None, err)

    @staticmethod
    def update_name(project_id: int, name: str) -> Tuple[bool, Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return False, err
        sql = f"UPDATE {TABLE_PROJECTS} SET name = %s WHERE id = %s"
        _, err = mysql_handler.execute(sql, (name.strip(), project_id))
        return (err is None, err)

    @staticmethod
    def get_asset_by_path(
        project_id: int, relative_path: str
    ) -> Tuple[Optional[ProjectAssetRow], Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return None, err
        sql = (
            f"SELECT id, project_id, session_id, asset_type, relative_path, "
            f"original_filename, file_category, created_at "
            f"FROM {TABLE_PROJECT_ASSETS} WHERE project_id = %s AND relative_path = %s LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (project_id, relative_path))
        if err:
            return None, err
        if not rows:
            return None, None
        return rows[0], None

    @staticmethod
    def create_asset(
        project_id: int,
        asset_type: str,
        relative_path: str,
        session_id: Optional[str] = None,
        original_filename: Optional[str] = None,
        file_category: Optional[str] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return None, err
        existing, err = ProjectStore.get_asset_by_path(project_id, relative_path)
        if err:
            return None, err
        if existing:
            return int(existing.get("id") or 0), None
        data: Dict[str, Any] = {
            "project_id": project_id,
            "asset_type": asset_type,
            "relative_path": relative_path,
        }
        if session_id:
            data["session_id"] = session_id
        if original_filename:
            data["original_filename"] = original_filename
        if file_category:
            data["file_category"] = file_category
        _, asset_id, err = mysql_handler.insert(TABLE_PROJECT_ASSETS, data)
        if err:
            return None, err
        return int(asset_id or 0), None

    @staticmethod
    def list_assets(project_id: int) -> Tuple[List[ProjectAssetRow], Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return [], err
        sql = (
            f"SELECT id, project_id, session_id, asset_type, relative_path, "
            f"original_filename, file_category, created_at "
            f"FROM {TABLE_PROJECT_ASSETS} WHERE project_id = %s ORDER BY id DESC"
        )
        rows, err = mysql_handler.query(sql, (project_id,))
        if err:
            return [], err
        return rows or [], None

    @staticmethod
    def list_sessions_by_project(project_id: int) -> Tuple[List[Dict[str, Optional[str]]], Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return [], err
        sql = (
            f"SELECT session_id, title, project_id FROM {TABLE_SESSION_USER} "
            f"WHERE project_id = %s ORDER BY id DESC"
        )
        rows, err = mysql_handler.query(sql, (project_id,))
        if err:
            return [], err
        sessions = [
            {
                "session_id": str(row.get("session_id")),
                "title": (row.get("title") or None),
                "project_id": row.get("project_id"),
            }
            for row in (rows or [])
            if row.get("session_id")
        ]
        return sessions, None

    @staticmethod
    def count_sessions_by_project(project_id: int) -> Tuple[int, Optional[str]]:
        ok, err = _ensure_tables()
        if not ok:
            return 0, err
        sql = f"SELECT COUNT(*) AS cnt FROM {TABLE_SESSION_USER} WHERE project_id = %s"
        rows, err = mysql_handler.query(sql, (project_id,))
        if err:
            return 0, err
        return int((rows[0] if rows else {}).get("cnt") or 0), None
