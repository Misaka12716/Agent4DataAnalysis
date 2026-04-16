# db/session_store.py
# 基于 MySQLHandler 的会话内容与工作区路径读写（使用 db.models 中的表名与字段）

from typing import List, Optional, Tuple
from utils.mysql_utils import mysql_handler
from db.models import (
    TABLE_SESSION_USER,
    TABLE_SESSION_CONTENT,
    TABLE_USERS,
    SessionUserRow,
    SessionContentRow,
)


class SessionStore:
    """会话与内容存储：仅做 Session-User 映射和 Session 内容/版本读写。"""

    @staticmethod
    def get_workspace_path(session_id: str) -> Optional[str]:
        """根据 session_id 查询工作区绝对路径。"""
        sql = (
            f"SELECT workspace_abs_path FROM {TABLE_SESSION_USER} "
            "WHERE session_id = %s LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (session_id,))
        if err or not rows:
            return None
        return rows[0].get("workspace_abs_path")

    @staticmethod
    def get_session_user(session_id: str) -> Tuple[Optional[SessionUserRow], Optional[str]]:
        """根据 session_id 获取会话-用户映射记录。"""
        sql = (
            f"SELECT id, session_id, user_id, workspace_abs_path FROM {TABLE_SESSION_USER} "
            "WHERE session_id = %s LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (session_id,))
        if err:
            return None, err
        if not rows:
            return None, None
        return rows[0], None

    @staticmethod
    def create_session(session_id: str, user_id: int, workspace_abs_path: str) -> Tuple[bool, Optional[str]]:
        """创建会话映射；若 session_id 已存在则返回失败。"""
        exists, err = SessionStore.get_session_user(session_id)
        if err:
            return False, err
        if exists:
            return False, "session_id already exists"
        data = {
            "session_id": session_id,
            "user_id": user_id,
            "workspace_abs_path": workspace_abs_path,
        }
        _, _, err = mysql_handler.insert(TABLE_SESSION_USER, data)
        return (err is None, err)

    @staticmethod
    def user_exists(user_id: int) -> Tuple[bool, Optional[str]]:
        """校验 users 表中是否存在该 user_id。"""
        sql = f"SELECT id FROM {TABLE_USERS} WHERE id = %s LIMIT 1"
        rows, err = mysql_handler.query(sql, (user_id,))
        if err:
            return False, err
        return bool(rows), None

    @staticmethod
    def get_session_ids_by_user_id(user_id: int) -> Tuple[List[str], Optional[str]]:
        """根据 user_id 查询该用户的全部 session_id（按创建顺序倒序）。"""
        sql = (
            f"SELECT session_id FROM {TABLE_SESSION_USER} "
            "WHERE user_id = %s ORDER BY id DESC"
        )
        rows, err = mysql_handler.query(sql, (user_id,))
        if err:
            return [], err
        session_ids = [str(row.get("session_id")) for row in rows if row.get("session_id")]
        return session_ids, None

    @staticmethod
    def set_workspace_path(session_id: str, user_id: int, workspace_abs_path: str) -> Tuple[bool, Optional[str]]:
        """创建或更新会话记录并设置工作区路径。返回 (成功, 错误信息)。"""
        data = {
            "session_id": session_id,
            "user_id": user_id,
            "workspace_abs_path": workspace_abs_path,
        }
        # 若已存在则更新
        check_sql = f"SELECT id FROM {TABLE_SESSION_USER} WHERE session_id = %s LIMIT 1"
        exists, err = mysql_handler.query(check_sql, (session_id,))
        if err:
            return False, err
        if exists:
            up_sql = (
                f"UPDATE {TABLE_SESSION_USER} "
                "SET user_id = %s, workspace_abs_path = %s WHERE session_id = %s"
            )
            _, err = mysql_handler.execute(
                up_sql, (user_id, workspace_abs_path, session_id)
            )
            return (err is None, err)
        _, _, err = mysql_handler.insert(TABLE_SESSION_USER, data)
        return (err is None, err)

    @staticmethod
    def get_latest_content(session_id: str) -> Tuple[Optional[str], int]:
        """获取会话当前「完整累计内容」和「版本号」。无记录时返回 (None, 0)。"""
        sql = (
            f"SELECT content, version FROM {TABLE_SESSION_CONTENT} "
            "WHERE session_id = %s ORDER BY version DESC LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (session_id,))
        if err or not rows:
            return None, 0
        row = rows[0]
        return row.get("content"), int(row.get("version", 0))

    @staticmethod
    def append_content(session_id: str, new_content: str) -> Tuple[bool, int, Optional[str]]:
        """
        追加内容：在最新版本基础上生成新版本，写入「完整累计内容」。
        返回 (成功, 新版本号, 错误信息)。
        """
        prev_content, prev_version = SessionStore.get_latest_content(session_id)
        full_content = (prev_content or "") + new_content
        next_version = prev_version + 1
        data = {
            "session_id": session_id,
            "version": next_version,
            "content": full_content,
        }
        _, _, err = mysql_handler.insert(TABLE_SESSION_CONTENT, data)
        if err:
            return False, prev_version, err
        return True, next_version, None

    @staticmethod
    def set_full_content(session_id: str, full_content: str, version: int) -> Tuple[bool, Optional[str]]:
        """直接写入指定版本的完整内容（用于快照覆盖或初始化）。"""
        data = {
            "session_id": session_id,
            "version": version,
            "content": full_content,
        }
        _, _, err = mysql_handler.insert(TABLE_SESSION_CONTENT, data)
        return (err is None, err)
