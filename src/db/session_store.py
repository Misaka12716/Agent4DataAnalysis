# db/session_store.py
# 基于 MySQLHandler 的会话内容与工作区路径读写（使用 db.models 中的表名与字段）

import json
from typing import Any, Dict, List, Optional, Tuple
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
            f"SELECT id, session_id, user_id, project_id, title, workspace_abs_path FROM {TABLE_SESSION_USER} "
            "WHERE session_id = %s LIMIT 1"
        )
        rows, err = mysql_handler.query(sql, (session_id,))
        if err:
            return None, err
        if not rows:
            return None, None
        return rows[0], None

    @staticmethod
    def create_session(
        session_id: str,
        user_id: int,
        workspace_abs_path: str,
        project_id: Optional[int] = None,
    ) -> Tuple[bool, Optional[str]]:
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
        if project_id is not None and int(project_id) > 0:
            data["project_id"] = int(project_id)
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
    def get_sessions_by_user_id(user_id: int) -> Tuple[List[Dict[str, Optional[str]]], Optional[str]]:
        """根据 user_id 查询会话列表，包含 session_id 和标题（按创建顺序倒序）。"""
        sql = (
            f"SELECT session_id, title FROM {TABLE_SESSION_USER} "
            "WHERE user_id = %s ORDER BY id DESC"
        )
        rows, err = mysql_handler.query(sql, (user_id,))
        if err:
            return [], err
        sessions = [
            {
                "session_id": str(row.get("session_id")),
                "title": (row.get("title") or None),
            }
            for row in rows
            if row.get("session_id")
        ]
        return sessions, None

    @staticmethod
    def get_accessible_sessions(user_id: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        返回当前用户可访问的会话列表（自己创建的 + 可访问项目内的共享会话）。
        字段：session_id, title, project_id（可选）, access（owner | shared）
        """
        from db.rbac_store import RbacStore
        from db.project_store import ProjectStore

        own_sessions, err = SessionStore.get_sessions_by_user_id(user_id)
        if err:
            return [], err

        seen: set[str] = set()
        result: List[Dict[str, Any]] = []

        for s in own_sessions:
            sid = str(s.get("session_id") or "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            result.append({
                "session_id": sid,
                "title": s.get("title"),
                "access": "owner",
            })

        projects, err = RbacStore.list_projects_for_user(user_id)
        if err:
            return [], err

        for proj in projects or []:
            pid = int(proj.get("id") or 0)
            if pid <= 0:
                continue
            proj_sessions, err = ProjectStore.list_sessions_by_project(pid)
            if err:
                return [], err
            for s in proj_sessions or []:
                sid = str(s.get("session_id") or "")
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                result.append({
                    "session_id": sid,
                    "title": s.get("title"),
                    "project_id": pid,
                    "access": "shared",
                })

        return result, None

    @staticmethod
    def save_session_title_if_absent(session_id: str, title: str) -> Tuple[bool, bool, Optional[str]]:
        """
        会话标题首次写入：如果已存在非空标题则不覆盖。
        返回 (成功, 是否已写入, 错误信息)。
        """
        clean_title = title.strip()
        if not clean_title:
            return False, False, "title is empty"

        sql = f"SELECT title FROM {TABLE_SESSION_USER} WHERE session_id = %s LIMIT 1"
        rows, err = mysql_handler.query(sql, (session_id,))
        if err:
            return False, False, err
        if not rows:
            return False, False, "session_id not found"

        current_title = (rows[0].get("title") or "").strip()
        if current_title:
            return True, False, None

        update_sql = (
            f"UPDATE {TABLE_SESSION_USER} "
            "SET title = %s WHERE session_id = %s AND (title IS NULL OR title = '')"
        )
        _, err = mysql_handler.execute(update_sql, (clean_title, session_id))
        if err:
            return False, False, err
        return True, True, None

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
        full_content = SessionStore._normalize_full_content(prev_content or "", new_content)
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

    @staticmethod
    def _normalize_full_content(prev_content: str, new_content: str) -> str:
        """
        归一化会话累计内容，约束 llm_chunk 存储形态：
        1) 连续 llm_chunk 合并成一条；
        2) 当出现 llm_complete 时，移除其前面连续的 llm_chunk 分片，仅保留 llm_complete。
        """
        incoming_line = (new_content or "").strip()
        if not incoming_line:
            return prev_content

        incoming_event = SessionStore._parse_json_line(incoming_line)
        # 非 JSON 行或无 type 字段，保持原有追加语义，避免破坏兼容性。
        if not incoming_event or "type" not in incoming_event:
            return prev_content + new_content

        events = SessionStore._parse_event_lines(prev_content)
        # 历史内容若存在非 JSON 行，回退到原始追加策略，避免意外丢数据。
        if events is None:
            return prev_content + new_content
        incoming_type = str(incoming_event.get("type") or "")

        if incoming_type == "llm_chunk":
            chunk_text = str(incoming_event.get("content") or "")
            if events and str(events[-1].get("type") or "") == "llm_chunk":
                # 连续 chunk 合并，避免数据库中出现多条连续 llm_chunk。
                merged = dict(events[-1])
                merged["content"] = str(merged.get("content") or "") + chunk_text
                events[-1] = merged
            else:
                events.append(incoming_event)
        elif incoming_type == "llm_complete":
            # 完整结果出现后，回收紧邻的 llm_chunk 分片，只保留 llm_complete。
            while events and str(events[-1].get("type") or "") == "llm_chunk":
                events.pop()
            events.append(incoming_event)
        else:
            events.append(incoming_event)

        return SessionStore._dump_event_lines(events)

    @staticmethod
    def _parse_event_lines(content: str) -> Optional[List[Dict[str, Any]]]:
        events: List[Dict[str, Any]] = []
        for raw in (content or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            evt = SessionStore._parse_json_line(line)
            if evt is None:
                return None
            events.append(evt)
        return events

    @staticmethod
    def _parse_json_line(line: str) -> Optional[Dict[str, Any]]:
        try:
            obj = json.loads(line)
        except Exception:
            return None
        if isinstance(obj, dict):
            return obj
        return None

    @staticmethod
    def _dump_event_lines(events: List[Dict[str, Any]]) -> str:
        if not events:
            return ""
        return "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
