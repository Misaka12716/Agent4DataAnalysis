# backend/permission_service.py
# 权限解析与校验逻辑

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from db.project_schema import DEFAULT_PROJECT_INTERNAL_NAME, RESERVED_PROJECT_NAMES
from db.rbac_schema import (
    ALL_PERMISSIONS,
    PERM_MEMBER_MANAGE,
    PLATFORM_ROLE_ADMIN,
    PROJECT_ROLE_MANAGER,
)
from db.rbac_store import RbacStore


def is_user_blocked(user_row: dict[str, Any]) -> bool:
    blocked = user_row.get("is_blocked")
    if blocked is not None and bool(blocked):
        return True
    status = str(user_row.get("status", "")).strip().lower()
    return status in {"blocked", "disabled", "inactive"}


def all_project_permissions() -> Set[str]:
    return set(ALL_PERMISSIONS)


def resolve_member_permissions(role: str, permissions: List[str]) -> Set[str]:
    if str(role).strip().lower() == PROJECT_ROLE_MANAGER:
        return all_project_permissions()
    return {p for p in permissions if p in ALL_PERMISSIONS}


def get_user_platform_role(user_id: int) -> Tuple[str, Optional[str]]:
    user, err = RbacStore.get_user(user_id)
    if err:
        return "user", err
    if not user:
        return "user", None
    return str(user.get("platform_role") or "user").strip().lower(), None


def is_platform_admin(user_id: int) -> Tuple[bool, Optional[str]]:
    role, err = get_user_platform_role(user_id)
    if err:
        return False, err
    return role == PLATFORM_ROLE_ADMIN, None


def is_project_owner(project_row: dict[str, Any], user_id: int) -> bool:
    return int(project_row.get("user_id") or 0) == user_id


def is_default_project(project_row: dict[str, Any]) -> bool:
    name = str(project_row.get("name") or "")
    return name in RESERVED_PROJECT_NAMES or name == DEFAULT_PROJECT_INTERNAL_NAME


def get_effective_project_permissions(
    project_id: int,
    user_id: int,
    project_row: Optional[dict[str, Any]] = None,
) -> Tuple[Set[str], str, Optional[str]]:
    """
    返回 (permissions_set, access_type, error)。
    access_type: admin | owner | member | none
    """
    admin, err = is_platform_admin(user_id)
    if err:
        return set(), "none", err
    if admin:
        return all_project_permissions(), "admin", None

    if project_row is None:
        from db.project_store import ProjectStore

        project_row, err = ProjectStore.get_project(project_id)
        if err:
            return set(), "none", err
        if not project_row:
            return set(), "none", None

    if is_project_owner(project_row, user_id):
        return all_project_permissions(), "owner", None

    member, err = RbacStore.get_member(project_id, user_id)
    if err:
        return set(), "none", err
    if not member:
        return set(), "none", None

    perms = resolve_member_permissions(
        str(member.get("role") or ""),
        list(member.get("permissions") or []),
    )
    return perms, "member", None


def has_project_permission(
    project_id: int,
    user_id: int,
    permission: Optional[str],
    project_row: Optional[dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    perms, access, err = get_effective_project_permissions(project_id, user_id, project_row)
    if err:
        return False, err
    if access == "none":
        return False, None
    if permission is None:
        return True, None
    return permission in perms, None


def can_manage_project(
    project_id: int,
    user_id: int,
    project_row: Optional[dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """项目生命周期管理：admin / 所有者 / project_manager。"""
    admin, err = is_platform_admin(user_id)
    if err:
        return False, err
    if admin:
        return True, None
    if project_row is None:
        from db.project_store import ProjectStore

        project_row, err = ProjectStore.get_project(project_id)
        if err:
            return False, err
        if not project_row:
            return False, None
    if is_project_owner(project_row, user_id):
        return True, None
    member, err = RbacStore.get_member(project_id, user_id)
    if err:
        return False, err
    if not member:
        return False, None
    return str(member.get("role") or "").strip().lower() == PROJECT_ROLE_MANAGER, None


def can_manage_members(
    project_id: int,
    user_id: int,
    project_row: Optional[dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    admin, err = is_platform_admin(user_id)
    if err:
        return False, err
    if admin:
        return True, None
    if project_row is None:
        from db.project_store import ProjectStore

        project_row, err = ProjectStore.get_project(project_id)
        if err:
            return False, err
        if not project_row:
            return False, None
    if is_project_owner(project_row, user_id):
        return True, None
    member, err = RbacStore.get_member(project_id, user_id)
    if err:
        return False, err
    if not member:
        return False, None
    if str(member.get("role") or "").strip().lower() == PROJECT_ROLE_MANAGER:
        return True, None
    perms = resolve_member_permissions(
        str(member.get("role") or ""),
        list(member.get("permissions") or []),
    )
    return PERM_MEMBER_MANAGE in perms, None


def get_user_permissions_summary(user_id: int) -> Tuple[Dict[str, Any], Optional[str]]:
    admin, err = is_platform_admin(user_id)
    if err:
        return {}, err
    if admin:
        return {"is_admin": True, "projects": {}}, None

    from db.project_store import ProjectStore

    projects: Dict[str, Any] = {}
    owned, err = ProjectStore.list_by_user(user_id)
    if err:
        return {}, err
    for p in owned:
        pid = str(p.get("id"))
        projects[pid] = {
            "access": "owner",
            "permissions": sorted(all_project_permissions()),
        }

    member_ids, err = RbacStore.list_member_project_ids(user_id)
    if err:
        return {}, err
    for pid in member_ids:
        if str(pid) in projects:
            continue
        member, err = RbacStore.get_member(pid, user_id)
        if err or not member:
            continue
        perms = resolve_member_permissions(
            str(member.get("role") or ""),
            list(member.get("permissions") or []),
        )
        projects[str(pid)] = {
            "access": str(member.get("role") or "member"),
            "permissions": sorted(perms),
        }

    return {"is_admin": False, "projects": projects}, None
