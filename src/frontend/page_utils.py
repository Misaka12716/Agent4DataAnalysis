# frontend/page_utils.py — 联调前端共享 API 与权限辅助

from __future__ import annotations

import io
import os
from typing import Any, Optional, Tuple

import httpx
import pandas as pd
import streamlit as st

from db.rbac_schema import (
    PERM_ANALYSIS_CREATE,
    PERM_DATA_ANNOTATE,
    PERM_DATA_DELETE,
    PERM_DATA_DOWNLOAD,
    PERM_DATA_REVIEW,
    PERM_DATA_UPLOAD,
    PERM_MEMBER_MANAGE,
    PERM_TRAINING_CREATE,
)

API_BASE = "http://localhost:52716"
ACCEPTANCE_PHONE = "13800000000"
ACCEPTANCE_CODE = "888888"

ALL_PERMISSION_LABELS = {
    PERM_DATA_UPLOAD: "数据上传",
    PERM_DATA_DELETE: "数据删除",
    PERM_DATA_DOWNLOAD: "数据下载",
    PERM_DATA_ANNOTATE: "数据标注",
    PERM_DATA_REVIEW: "数据审核",
    PERM_ANALYSIS_CREATE: "统计分析任务创建",
    PERM_TRAINING_CREATE: "模型训练任务创建",
    PERM_MEMBER_MANAGE: "成员管理",
}


def api_base_url() -> str:
    return str(st.session_state.get("api_base") or API_BASE).rstrip("/")


def _acceptance_mode_enabled() -> bool:
    return os.getenv("ACCEPTANCE_MODE", "").strip().lower() in ("1", "true", "yes")


def apply_login_payload(data: dict, phone: str) -> None:
    login_data = data.get("data") or {}
    st.session_state["current_user_id"] = int(login_data.get("user_id") or 0)
    st.session_state["current_username"] = str(login_data.get("username") or "")
    st.session_state["access_token"] = str(login_data.get("access_token") or "")
    st.session_state["auth_token"] = st.session_state["access_token"]
    st.session_state["logged_in_phone"] = phone


def is_logged_in() -> bool:
    return bool(_access_token())


def is_platform_admin() -> bool:
    return str(st.session_state.get("platform_role") or "").strip().lower() == "admin"


def acceptance_quick_login() -> Tuple[bool, str]:
    try:
        r = httpx.post(
            f"{api_base_url()}/auth/login-with-sms",
            json={"phone": ACCEPTANCE_PHONE, "code": ACCEPTANCE_CODE},
            timeout=10.0,
        )
        r.raise_for_status()
        apply_login_payload(r.json(), ACCEPTANCE_PHONE)
        refresh_user_profile()
        return True, ""
    except httpx.HTTPStatusError as e:
        return False, f"HTTP {e.response.status_code}: {e.response.text[:120]}"
    except Exception as e:
        return False, str(e)


def refresh_user_profile() -> None:
    if not is_logged_in():
        return
    try:
        r = httpx.get(
            f"{api_base_url()}/auth/me",
            headers=auth_headers(),
            timeout=10.0,
        )
        if r.status_code == 401:
            # token 因服务重启等原因失效时清掉，避免后续请求反复 401
            st.session_state["access_token"] = ""
            st.session_state["auth_token"] = ""
            st.session_state["current_user_id"] = 0
            st.session_state["platform_role"] = ""
            st.session_state["permissions_summary"] = {}
            return
        if r.status_code != 200:
            return
        me = (r.json().get("data") or {})
        st.session_state["platform_role"] = str(me.get("platform_role") or "user")
        st.session_state["permissions_summary"] = me.get("permissions_summary") or {}
        if me.get("user_id"):
            st.session_state["current_user_id"] = int(me.get("user_id") or 0)
        if me.get("username") is not None:
            st.session_state["current_username"] = str(me.get("username") or "")
    except Exception:
        pass


def render_acceptance_login_button(key: str = "page_utils_acceptance_login") -> None:
    if not _acceptance_mode_enabled():
        return
    if st.button("验收一键登录", key=key, type="primary"):
        ok, err = acceptance_quick_login()
        if ok:
            st.rerun()
        else:
            st.error(err)
    st.caption(f"验收账号 {ACCEPTANCE_PHONE} / {ACCEPTANCE_CODE}（需 ACCEPTANCE_MODE=1）")


def render_auth_sidebar() -> None:
    """独立 pages 侧栏：登录状态与可选验收登录。"""
    with st.sidebar:
        st.markdown("##### 账户")
        if is_logged_in():
            st.success(f"已登录 · {st.session_state.get('current_username', '')}")
            sid = str(st.session_state.get("session_id") or "").strip()
            if sid:
                st.caption(f"会话 `{sid}`")
        else:
            st.warning("未登录 — 需鉴权的 API 将失败")
            render_acceptance_login_button()


def _access_token() -> str:
    return str(
        st.session_state.get("access_token")
        or st.session_state.get("auth_token")
        or ""
    ).strip()


def auth_headers() -> dict[str, str]:
    token = _access_token()
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def auth_headers_upload() -> dict[str, str]:
    token = _access_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _parse_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        detail = body.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("msg") or detail)
        if detail:
            return str(detail)
        return str(body.get("msg") or f"HTTP {resp.status_code}")
    except Exception:
        return f"HTTP {resp.status_code}"


def api_get(path: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
    try:
        headers = auth_headers()
        # #region agent log
        try:
            import json as _json
            import time as _time

            with open(
                "/data1/pjw/AgentPlatform/.cursor/debug-59272a.log",
                "a",
                encoding="utf-8",
            ) as _f:
                _f.write(
                    _json.dumps(
                        {
                            "sessionId": "59272a",
                            "runId": "post-fix",
                            "hypothesisId": "H1,H5",
                            "location": "page_utils.py:api_get",
                            "message": "frontend api_get request",
                            "data": {
                                "path": path,
                                "has_auth_header": "Authorization" in headers,
                                "token_len": len(
                                    (headers.get("Authorization") or "").replace(
                                        "Bearer ", ""
                                    )
                                ),
                                "logged_in": is_logged_in(),
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        resp = httpx.get(
            f"{api_base_url()}{path}",
            params=params,
            headers=headers,
            timeout=timeout,
        )
        # #region agent log
        try:
            import json as _json
            import time as _time

            with open(
                "/data1/pjw/AgentPlatform/.cursor/debug-59272a.log",
                "a",
                encoding="utf-8",
            ) as _f:
                _f.write(
                    _json.dumps(
                        {
                            "sessionId": "59272a",
                            "runId": "post-fix",
                            "hypothesisId": "H1,H5",
                            "location": "page_utils.py:api_get:response",
                            "message": "frontend api_get response",
                            "data": {
                                "path": path,
                                "status_code": resp.status_code,
                                "body_prefix": (resp.text or "")[:160],
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        if resp.status_code == 200:
            return resp.json()
        return {"error": _parse_error(resp)}
    except Exception as e:
        return {"error": str(e)}


def api_post(path: str, data: dict, timeout: float = 60) -> dict:
    try:
        resp = httpx.post(
            f"{api_base_url()}{path}",
            json=data,
            headers=auth_headers(),
            timeout=timeout,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        return {"error": _parse_error(resp)}
    except Exception as e:
        return {"error": str(e)}


def api_put(path: str, data: dict, timeout: float = 60) -> dict:
    try:
        resp = httpx.put(
            f"{api_base_url()}{path}",
            json=data,
            headers=auth_headers(),
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": _parse_error(resp)}
    except Exception as e:
        return {"error": str(e)}


def api_delete(path: str, timeout: float = 60, json_body: dict | None = None) -> dict:
    try:
        kwargs: dict = {
            "headers": auth_headers(),
            "timeout": timeout,
        }
        if json_body is not None:
            kwargs["json"] = json_body
        resp = httpx.request("DELETE", f"{api_base_url()}{path}", **kwargs)
        if resp.status_code == 200:
            return resp.json()
        return {"error": _parse_error(resp)}
    except Exception as e:
        return {"error": str(e)}


def resolve_project_id_for_permission(projects: list | None = None) -> int:
    """权限校验以会话归属项目为准；无会话上下文时回退到侧栏当前项目。"""
    session_pid = int(st.session_state.get("session_project_id", 0) or 0)
    if session_pid > 0:
        return session_pid
    pid = int(st.session_state.get("current_project_id", 0) or 0)
    if pid > 0:
        return pid
    for item in projects or []:
        if (item or {}).get("is_default"):
            return int((item or {}).get("id") or 0)
    return 0


def sync_session_project_context(session_id: str) -> None:
    """根据 session_id 拉取 meta 并同步 session_project_id / current_project_id。"""
    sid = (session_id or "").strip()
    if not sid or not is_logged_in():
        st.session_state["session_project_id"] = 0
        return
    result = api_get("/session/meta", params={"session_id": sid}, timeout=10.0)
    if "error" in result:
        st.session_state["session_project_id"] = 0
        return
    meta = result.get("data") or {}
    pid = int(meta.get("project_id") or 0)
    st.session_state["session_project_id"] = pid
    if pid > 0:
        st.session_state["current_project_id"] = pid


def apply_session_selection(session_id: str, project_id: int | None = None) -> None:
    """切换当前会话并同步项目上下文。"""
    sid = (session_id or "").strip()
    st.session_state["session_id"] = sid
    st.session_state["_reset_session_id"] = sid
    if project_id is not None and int(project_id or 0) > 0:
        pid = int(project_id)
        st.session_state["session_project_id"] = pid
        st.session_state["current_project_id"] = pid
    elif sid:
        sync_session_project_context(sid)
    else:
        st.session_state["session_project_id"] = 0


def project_has_permission(project_id: int, permission: str) -> bool:
    if is_platform_admin():
        return True
    summary = st.session_state.get("permissions_summary") or {}
    if summary.get("is_admin"):
        return True
    if project_id <= 0:
        return True
    proj = (summary.get("projects") or {}).get(str(project_id)) or {}
    perms = proj.get("permissions") or []
    if proj.get("access") == "owner":
        return True
    return permission in perms


def can_upload(project_id: int) -> bool:
    return project_has_permission(project_id, PERM_DATA_UPLOAD)


def can_analyze(project_id: int) -> bool:
    return project_has_permission(project_id, PERM_ANALYSIS_CREATE)


def can_manage_project(project_id: int) -> bool:
    if is_platform_admin():
        return True
    summary = st.session_state.get("permissions_summary") or {}
    if summary.get("is_admin"):
        return True
    if project_id <= 0:
        return True
    proj = (summary.get("projects") or {}).get(str(project_id)) or {}
    if proj.get("access") in ("owner", "project_manager", "admin"):
        return True
    return PERM_MEMBER_MANAGE in (proj.get("permissions") or [])


def can_manage_members(project_id: int) -> bool:
    if is_platform_admin():
        return True
    summary = st.session_state.get("permissions_summary") or {}
    if summary.get("is_admin"):
        return True
    proj = (summary.get("projects") or {}).get(str(project_id)) or {}
    if proj.get("access") in ("owner", "project_manager"):
        return True
    return PERM_MEMBER_MANAGE in (proj.get("permissions") or [])


def can_download(project_id: int) -> bool:
    return project_has_permission(project_id, PERM_DATA_DOWNLOAD)


def can_delete(project_id: int) -> bool:
    return project_has_permission(project_id, PERM_DATA_DELETE)


def get_project_access_info(project_id: int) -> dict:
    summary = st.session_state.get("permissions_summary") or {}
    if is_platform_admin() or summary.get("is_admin"):
        return {"access": "admin", "permissions": list(ALL_PERMISSION_LABELS.keys())}
    return (summary.get("projects") or {}).get(str(project_id)) or {}


def format_permission_labels(permissions: list | None) -> str:
    if not permissions:
        return "（无）"
    labels = [ALL_PERMISSION_LABELS.get(p, p) for p in permissions]
    return ", ".join(labels)


def require_session() -> str:
    sid = str(st.session_state.get("session_id") or st.session_state.get("session_id_input") or "").strip()
    if not sid:
        st.warning("请先在侧栏登录并创建或选择会话。")
    return sid


def read_uploaded_table(uploaded) -> Optional[list[dict]]:
    if not uploaded:
        return None
    raw = uploaded.getvalue()
    if uploaded.name.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(raw))
    else:
        df = pd.read_excel(io.BytesIO(raw))
    return df.to_dict(orient="records")
