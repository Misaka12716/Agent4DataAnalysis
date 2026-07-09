# db/rbac_store.py
# 角色与权限数据访问层

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

from db.models import TABLE_USERS
from db.project_schema import TABLE_PROJECTS
from db.rbac_schema import (
    DEFAULT_MEMBER_PERMISSIONS,
    PLATFORM_ROLE_ADMIN,
    PROJECT_MEMBERS_TABLE_DDL,
    PROJECT_TASKS_TABLE_DDL,
    TABLE_PROJECT_MEMBERS,
    TABLE_PROJECT_TASKS,
    TASK_STATUS_PENDING,
    USERS_ADD_PLATFORM_ROLE_DDL,
    USERS_ADD_STATUS_DDL,
    ProjectMemberRow,
    ProjectTaskRow,
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
        return False


def _parse_permissions(raw: Any) -> List[str]:
    if raw is None:
        return list(DEFAULT_MEMBER_PERMISSIONS)
    if isinstance(raw, list):
        return [str(p) for p in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(p) for p in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    return list(DEFAULT_MEMBER_PERMISSIONS)


def _json_safe_row(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    for key in ("created_at", "updated_at"):
        val = item.get(key)
        if isinstance(val, (datetime.datetime, datetime.date)):
            item[key] = val.isoformat()
    return item


def _ensure_rbac_schema() -> Tuple[bool, Optional[str]]:
    """确保 RBAC 相关表与 users 扩展列存在。"""
    try:
        if mysql_handler._check_table_exists(TABLE_USERS):
            if not _check_column_exists(TABLE_USERS, "platform_role"):
                _, err = mysql_handler.execute(USERS_ADD_PLATFORM_ROLE_DDL)
                if err and "Duplicate column" not in str(err):
                    return False, f"为 {TABLE_USERS} 添加 platform_role 失败: {err}"
            if not _check_column_exists(TABLE_USERS, "status"):
                _, err = mysql_handler.execute(USERS_ADD_STATUS_DDL)
                if err and "Duplicate column" not in str(err):
                    return False, f"为 {TABLE_USERS} 添加 status 失败: {err}"

        if not mysql_handler._check_table_exists(TABLE_PROJECT_MEMBERS):
            _, err = mysql_handler.execute(PROJECT_MEMBERS_TABLE_DDL)
            if err:
                return False, f"创建表 {TABLE_PROJECT_MEMBERS} 失败: {err}"

        if not mysql_handler._check_table_exists(TABLE_PROJECT_TASKS):
            _, err = mysql_handler.execute(PROJECT_TASKS_TABLE_DDL)
            if err:
                return False, f"创建表 {TABLE_PROJECT_TASKS} 失败: {err}"

        return True, None
    except Exception as e:
        return False, str(e)


class RbacStore:
    @staticmethod
    def ensure_schema() -> Tuple[bool, Optional[str]]:
        return _ensure_rbac_schema()

    # ---------- 用户 ----------

    @staticmethod
    def get_user(user_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return None, err
        rows, err = mysql_handler.query(
            "SELECT * FROM users WHERE id = %s LIMIT 1",
            (user_id,),
        )
        if err:
            return None, err
        if not rows:
            return None, None
        return rows[0], None

    @staticmethod
    def get_user_by_phone(phone: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return None, err
        rows, err = mysql_handler.query(
            "SELECT * FROM users WHERE phone = %s LIMIT 1",
            (phone,),
        )
        if err:
            return None, err
        if not rows:
            return None, None
        return rows[0], None

    @staticmethod
    def is_platform_admin(user_id: int) -> Tuple[bool, Optional[str]]:
        user, err = RbacStore.get_user(user_id)
        if err:
            return False, err
        if not user:
            return False, None
        role = str(user.get("platform_role") or "user").strip().lower()
        return role == PLATFORM_ROLE_ADMIN, None

    @staticmethod
    def list_users(
        offset: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int, Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return [], 0, err
        count_rows, err = mysql_handler.query("SELECT COUNT(*) AS cnt FROM users", ())
        if err:
            return [], 0, err
        total = int((count_rows[0] if count_rows else {}).get("cnt") or 0)
        rows, err = mysql_handler.query(
            "SELECT id, username, phone, email, platform_role, status, created_at, updated_at "
            "FROM users ORDER BY id DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        if err:
            return [], 0, err
        return rows or [], total, None

    @staticmethod
    def create_user(
        username: str,
        phone: str,
        platform_role: str = "user",
        status: str = "active",
        password_hash: Optional[str] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return None, err
        import hashlib

        if not password_hash:
            password_hash = hashlib.sha256(f"admin-created:{phone}".encode("utf-8")).hexdigest()
        data = {
            "username": username.strip(),
            "phone": phone.strip(),
            "password_hash": password_hash,
            "platform_role": platform_role,
            "status": status,
        }
        _, user_id, err = mysql_handler.insert(TABLE_USERS, data)
        if err:
            return None, err
        return int(user_id or 0), None

    @staticmethod
    def update_user(user_id: int, fields: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return False, err
        allowed = {"username", "phone", "email", "platform_role", "status"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return True, None
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values()) + [user_id]
        sql = f"UPDATE {TABLE_USERS} SET {set_clause} WHERE id = %s"
        _, err = mysql_handler.execute(sql, tuple(values))
        return (err is None, err)

    @staticmethod
    def promote_initial_admin(phone: str) -> Tuple[bool, Optional[str]]:
        """若环境变量 INITIAL_ADMIN_PHONE 匹配，将用户提升为 admin。"""
        import os

        initial = os.getenv("INITIAL_ADMIN_PHONE", "").strip()
        if not initial or initial != phone.strip():
            return False, None
        user, err = RbacStore.get_user_by_phone(phone)
        if err or not user:
            return False, err
        current = str(user.get("platform_role") or "user").strip().lower()
        if current == PLATFORM_ROLE_ADMIN:
            return True, None
        uid = int(user.get("id") or 0)
        return RbacStore.update_user(uid, {"platform_role": PLATFORM_ROLE_ADMIN})

    # ---------- 项目成员 ----------

    @staticmethod
    def get_member(
        project_id: int, user_id: int
    ) -> Tuple[Optional[ProjectMemberRow], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return None, err
        sql = (
            f"SELECT id, project_id, user_id, role, permissions, created_at, updated_at "
            f"FROM {TABLE_PROJECT_MEMBERS} WHERE project_id = %s AND user_id = %s LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (project_id, user_id))
        if err:
            return None, err
        if not rows:
            return None, None
        row = dict(rows[0])
        row["permissions"] = _parse_permissions(row.get("permissions"))
        return _json_safe_row(row), None

    @staticmethod
    def list_members(project_id: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return [], err
        sql = (
            f"SELECT pm.id, pm.project_id, pm.user_id, pm.role, pm.permissions, "
            f"pm.created_at, pm.updated_at, u.username, u.phone "
            f"FROM {TABLE_PROJECT_MEMBERS} pm "
            f"LEFT JOIN {TABLE_USERS} u ON u.id = pm.user_id "
            f"WHERE pm.project_id = %s ORDER BY pm.id ASC"
        )
        rows, err = mysql_handler.query(sql, (project_id,))
        if err:
            return [], err
        result = []
        for row in rows or []:
            item = dict(row)
            item["permissions"] = _parse_permissions(item.get("permissions"))
            result.append(_json_safe_row(item))
        return result, None

    @staticmethod
    def list_member_project_ids(user_id: int) -> Tuple[List[int], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return [], err
        sql = f"SELECT project_id FROM {TABLE_PROJECT_MEMBERS} WHERE user_id = %s"
        rows, err = mysql_handler.query(sql, (user_id,))
        if err:
            return [], err
        return [int(r["project_id"]) for r in (rows or []) if r.get("project_id")], None

    @staticmethod
    def add_member(
        project_id: int,
        user_id: int,
        role: str,
        permissions: List[str],
    ) -> Tuple[Optional[int], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return None, err
        existing, err = RbacStore.get_member(project_id, user_id)
        if err:
            return None, err
        if existing:
            return None, "成员已存在"
        perms_json = json.dumps(permissions, ensure_ascii=False)
        data = {
            "project_id": project_id,
            "user_id": user_id,
            "role": role,
            "permissions": perms_json,
        }
        _, member_id, err = mysql_handler.insert(TABLE_PROJECT_MEMBERS, data)
        if err:
            return None, err
        return int(member_id or 0), None

    @staticmethod
    def update_member(
        project_id: int,
        user_id: int,
        role: Optional[str] = None,
        permissions: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return False, err
        updates: Dict[str, Any] = {}
        if role is not None:
            updates["role"] = role
        if permissions is not None:
            updates["permissions"] = json.dumps(permissions, ensure_ascii=False)
        if not updates:
            return True, None
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values()) + [project_id, user_id]
        sql = f"UPDATE {TABLE_PROJECT_MEMBERS} SET {set_clause} WHERE project_id = %s AND user_id = %s"
        _, err = mysql_handler.execute(sql, tuple(values))
        return (err is None, err)

    @staticmethod
    def remove_member(project_id: int, user_id: int) -> Tuple[bool, Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return False, err
        sql = f"DELETE FROM {TABLE_PROJECT_MEMBERS} WHERE project_id = %s AND user_id = %s"
        _, err = mysql_handler.execute(sql, (project_id, user_id))
        return (err is None, err)

    @staticmethod
    def list_projects_for_user(user_id: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """合并 owned + member 项目（不含重复）；平台 admin 返回全部项目。"""
        from db.project_store import ProjectStore

        user, err = RbacStore.get_user(user_id)
        if err:
            return [], err
        if user and str(user.get("platform_role") or "").strip().lower() == PLATFORM_ROLE_ADMIN:
            return ProjectStore.list_all()

        owned, err = ProjectStore.list_by_user(user_id)
        if err:
            return [], err
        member_ids, err = RbacStore.list_member_project_ids(user_id)
        if err:
            return [], err
        owned_ids = {int(p.get("id") or 0) for p in owned}
        extra: List[Dict[str, Any]] = []
        for pid in member_ids:
            if pid in owned_ids:
                continue
            row, err = ProjectStore.get_project(pid)
            if err:
                return [], err
            if row:
                extra.append(row)
        return owned + extra, None

    # ---------- 任务 ----------

    @staticmethod
    def create_task(
        project_id: int,
        task_type: str,
        created_by: int,
        session_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return None, err
        data: Dict[str, Any] = {
            "project_id": project_id,
            "task_type": task_type,
            "status": TASK_STATUS_PENDING,
            "created_by": created_by,
        }
        if session_id:
            data["session_id"] = session_id
        if payload is not None:
            data["payload"] = json.dumps(payload, ensure_ascii=False)
        _, task_id, err = mysql_handler.insert(TABLE_PROJECT_TASKS, data)
        if err:
            return None, err
        return int(task_id or 0), None

    @staticmethod
    def get_task(task_id: int) -> Tuple[Optional[ProjectTaskRow], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return None, err
        sql = (
            f"SELECT id, project_id, session_id, task_type, status, payload, created_by, created_at "
            f"FROM {TABLE_PROJECT_TASKS} WHERE id = %s LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (task_id,))
        if err:
            return None, err
        if not rows:
            return None, None
        return _json_safe_row(dict(rows[0])), None

    @staticmethod
    def list_tasks(project_id: int) -> Tuple[List[ProjectTaskRow], Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return [], err
        sql = (
            f"SELECT id, project_id, session_id, task_type, status, payload, created_by, created_at "
            f"FROM {TABLE_PROJECT_TASKS} WHERE project_id = %s ORDER BY id DESC"
        )
        rows, err = mysql_handler.query(sql, (project_id,))
        if err:
            return [], err
        return [_json_safe_row(dict(r)) for r in (rows or [])], None

    @staticmethod
    def delete_asset(project_id: int, relative_path: str) -> Tuple[bool, Optional[str]]:
        ok, err = _ensure_rbac_schema()
        if not ok:
            return False, err
        from db.project_schema import TABLE_PROJECT_ASSETS

        sql = f"DELETE FROM {TABLE_PROJECT_ASSETS} WHERE project_id = %s AND relative_path = %s"
        _, err = mysql_handler.execute(sql, (project_id, relative_path))
        return (err is None, err)
