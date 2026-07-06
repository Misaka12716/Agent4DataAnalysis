"""
联调前端：面向数据科学场景的多智能体后端，用于测试会话上传、工作区与流式分析等接口。
运行方式（在 src 目录下）: streamlit run frontend/frontend.py
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st
import httpx

from frontend.page_utils import (
    apply_login_payload,
    render_acceptance_login_button,
    refresh_user_profile,
    resolve_project_id_for_permission,
)

# 后端地址（与 backend/server 默认端口一致）
API_BASE = "http://localhost:52716"


def _short_text(s: str, max_len: int = 160) -> str:
    s = s.strip() if isinstance(s, str) else str(s)
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _short_exc(e: BaseException, max_len: int = 120) -> str:
    return _short_text(str(e), max_len)


def _mask_phone(phone: str) -> str:
    p = (phone or "").strip()
    if len(p) >= 11:
        return f"{p[:3]}****{p[-4:]}"
    if len(p) >= 7:
        return f"{p[:2]}****{p[-2:]}"
    return p or "—"


def _render_session_list_buttons(sessions: list, key_prefix: str) -> None:
    """侧栏：每条会话一行可点按钮，切换当前 Session ID。"""
    if not sessions:
        st.caption("暂无会话记录。")
        return
    for idx, item in enumerate(sessions):
        sid = str((item or {}).get("session_id") or "").strip()
        title = str((item or {}).get("title") or "").strip() or "(未命名)"
        if not sid:
            continue
        label = f"{idx + 1}. {title}"
        if str((item or {}).get("access") or "") == "shared":
            label += " (共享)"
        safe_key = f"{key_prefix}_{idx}_{sid.replace('-', '_')}"
        if st.button(label, key=safe_key, use_container_width=True, help=sid):
            st.session_state["session_id"] = sid
            st.session_state["_reset_session_id"] = sid
            st.rerun()
        st.caption(sid)


def _auth_headers() -> dict[str, str]:
    token = str(st.session_state.get("access_token") or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _fetch_user_sessions_list(api_base: str):
    """返回 (sessions, error_msg)。error_msg 非空表示请求失败。"""
    try:
        r = httpx.get(
            f"{api_base.rstrip('/')}/session/list",
            headers=_auth_headers(),
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        sessions = ((data.get("data") or {}).get("sessions")) or []
        return sessions, None
    except httpx.HTTPStatusError as e:
        return [], f"列表失败 {e.response.status_code}: {_short_text(e.response.text, 200)}"
    except Exception as e:
        return [], _short_exc(e)


def _fetch_project_list(api_base: str):
    try:
        r = httpx.get(
            f"{api_base.rstrip('/')}/project/list",
            headers=_auth_headers(),
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        projects = ((data.get("data") or {}).get("projects")) or []
        return projects, None
    except httpx.HTTPStatusError as e:
        return [], f"项目列表失败 {e.response.status_code}: {_short_text(e.response.text, 200)}"
    except Exception as e:
        return [], _short_exc(e)


def _fetch_project_sessions(api_base: str, project_id: int):
    try:
        r = httpx.get(
            f"{api_base.rstrip('/')}/project/{project_id}/sessions",
            headers=_auth_headers(),
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        sessions = ((data.get("data") or {}).get("sessions")) or []
        return sessions, None
    except httpx.HTTPStatusError as e:
        return [], f"项目会话列表失败 {e.response.status_code}: {_short_text(e.response.text, 200)}"
    except Exception as e:
        return [], _short_exc(e)


def _render_project_list_buttons(projects: list, key_prefix: str) -> None:
    if not projects:
        st.caption("暂无项目。")
        return
    for idx, item in enumerate(projects):
        pid = int((item or {}).get("id") or 0)
        name = str((item or {}).get("name") or "").strip() or f"项目 {pid}"
        status = str((item or {}).get("status") or "active").strip()
        if pid <= 0:
            continue
        label = f"{idx + 1}. {name}"
        if (item or {}).get("is_default"):
            label = f"{idx + 1}. {name} ★"
        if status == "archived":
            label += " [已归档]"
        safe_key = f"{key_prefix}_{idx}_{pid}"
        if st.button(label, key=safe_key, use_container_width=True):
            st.session_state["current_project_id"] = pid
            st.session_state["session_id"] = ""
            st.session_state["_reset_session_id"] = ""
            st.rerun()


st.set_page_config(
    page_title="数据科学多智能体 · 联调控制台",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    #MainMenu {visibility: hidden;}
    header[data-testid="stHeader"] {background: transparent;}
    .block-container {padding-top: 1.25rem; max-width: 1200px;}
    h1 {letter-spacing: -0.02em;}
    div[data-testid="stTabs"] button {font-weight: 500;}
    [data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {border-radius: 12px;}
</style>
""",
    unsafe_allow_html=True,
)


@st.dialog("短信登录", width="small")
def _login_dialog() -> None:
    st.markdown("使用 **手机号 + 短信验证码** 登录；未注册账号将自动完成注册。")
    base = str(st.session_state.get("api_base", API_BASE)).rstrip("/")
    phone = st.text_input(
        "手机号",
        value="",
        placeholder="11 位手机号，例如 13800138000",
        key="dlg_auth_phone",
        help="需为 11 位中国大陆手机号",
    ).strip()
    sms_code = st.text_input(
        "短信验证码",
        value="",
        key="dlg_auth_sms",
        help="6 位数字验证码",
    ).strip()
    c_send, c_login = st.columns(2)
    with c_send:
        if st.button("发送验证码", use_container_width=True, key="dlg_btn_send_sms"):
            if not phone:
                st.warning("请先填写手机号")
            else:
                with st.spinner("发送中…"):
                    try:
                        r = httpx.post(
                            f"{base}/auth/send-sms-code",
                            json={"phone": phone},
                            timeout=10.0,
                        )
                        r.raise_for_status()
                        st.success("验证码已发送")
                        with st.expander("响应 JSON", expanded=False):
                            st.json(r.json())
                    except httpx.HTTPStatusError as e:
                        st.error(f"发送失败 {e.response.status_code}: {_short_text(e.response.text, 200)}")
                    except Exception as e:
                        st.error(_short_exc(e))
    with c_login:
        if st.button("登录", type="primary", use_container_width=True, key="dlg_btn_login"):
            if not phone:
                st.warning("请填写手机号")
            elif not sms_code:
                st.warning("请填写验证码")
            else:
                with st.spinner("登录中…"):
                    try:
                        r = httpx.post(
                            f"{base}/auth/login-with-sms",
                            json={"phone": phone, "code": sms_code},
                            timeout=10.0,
                        )
                        r.raise_for_status()
                        data = r.json()
                        apply_login_payload(data, phone)
                        refresh_user_profile()
                        st.success("登录成功")
                        st.rerun()
                    except httpx.HTTPStatusError as e:
                        st.error(f"登录失败 {e.response.status_code}: {_short_text(e.response.text, 200)}")
                    except Exception as e:
                        st.error(_short_exc(e))
    st.caption("关闭：点击遮罩外区域或按 Esc。")


@st.dialog("修改昵称", width="small")
def _edit_username_dialog() -> None:
    base = str(st.session_state.get("api_base", API_BASE)).rstrip("/")
    current_user_id = int(st.session_state.get("current_user_id", 0) or 0)
    current_username = str(st.session_state.get("current_username") or "")
    new_username = st.text_input(
        "昵称",
        value=current_username,
        placeholder="请输入新的昵称",
        key="dlg_edit_username",
        help="对应后端 users.username 字段，最长 128 字符",
    ).strip()
    if st.button("保存", type="primary", use_container_width=True, key="dlg_btn_save_username"):
        if not new_username:
            st.warning("昵称不能为空")
        elif current_user_id <= 0:
            st.warning("请先登录")
        else:
            with st.spinner("保存中…"):
                try:
                    r = httpx.post(
                        f"{base}/auth/update-username",
                        json={"username": new_username},
                        headers=_auth_headers(),
                        timeout=10.0,
                    )
                    data = r.json()
                    if r.status_code == 200 and int(data.get("code", -1)) == 0:
                        updated = (data.get("data") or {}).get("username") or new_username
                        st.session_state["current_username"] = str(updated)
                        st.success("昵称已更新")
                        st.rerun()
                    else:
                        st.error(data.get("msg") or f"更新失败 HTTP {r.status_code}")
                except httpx.HTTPStatusError as e:
                    try:
                        err_data = e.response.json()
                        st.error(err_data.get("msg") or _short_text(e.response.text, 200))
                    except Exception:
                        st.error(f"更新失败 {e.response.status_code}: {_short_text(e.response.text, 200)}")
                except Exception as e:
                    st.error(_short_exc(e))
    st.caption("关闭：点击遮罩外区域或按 Esc。")


@st.dialog("退出登录", width="small")
def _logout_dialog() -> None:
    st.markdown("确定退出当前账户？退出后需重新登录才能创建会话。")
    c_cancel, c_ok = st.columns(2)
    with c_cancel:
        if st.button("取消", use_container_width=True, key="dlg_logout_cancel"):
            st.rerun()
    with c_ok:
        if st.button("确定退出", type="primary", use_container_width=True, key="dlg_logout_confirm"):
            st.session_state["current_user_id"] = 0
            st.session_state["current_username"] = ""
            st.session_state["access_token"] = ""
            st.session_state["auth_token"] = ""
            st.session_state["logged_in_phone"] = ""
            st.session_state["platform_role"] = ""
            st.session_state["permissions_summary"] = {}
            st.session_state["last_user_sessions"] = []
            st.session_state["last_user_projects"] = []
            st.session_state["current_project_id"] = 0
            st.rerun()


if "current_user_id" not in st.session_state:
    st.session_state["current_user_id"] = 0
if "access_token" not in st.session_state:
    st.session_state["access_token"] = ""
if "current_username" not in st.session_state:
    st.session_state["current_username"] = ""
if "last_user_sessions" not in st.session_state:
    st.session_state["last_user_sessions"] = []
if "last_user_projects" not in st.session_state:
    st.session_state["last_user_projects"] = []
if "current_project_id" not in st.session_state:
    st.session_state["current_project_id"] = 0
if "logged_in_phone" not in st.session_state:
    st.session_state["logged_in_phone"] = ""
if "platform_role" not in st.session_state:
    st.session_state["platform_role"] = ""
if "permissions_summary" not in st.session_state:
    st.session_state["permissions_summary"] = {}

if "session_id" not in st.session_state:
    st.session_state["session_id"] = ""
if "session_id_input" not in st.session_state:
    st.session_state["session_id_input"] = st.session_state["session_id"]
if "_reset_session_id" in st.session_state:
    st.session_state["session_id_input"] = st.session_state.pop("_reset_session_id")

with st.sidebar:
    with st.container(border=True):
        st.markdown("##### 账户")
        current_user_id = int(st.session_state.get("current_user_id", 0) or 0)
        current_username = st.session_state.get("current_username", "") or ""
        login_phone = str(st.session_state.get("logged_in_phone") or "").strip()
        if current_user_id > 0:
            st.caption("状态")
            st.badge("已登录", color="green")
            st.markdown(f"**手机** `{_mask_phone(login_phone)}`")
            if login_phone:
                st.caption("号码仅存于浏览器会话，用于展示与联调。")
            st.markdown(f"**昵称** {current_username or '—'}")
            platform_role = str(st.session_state.get("platform_role") or "user")
            if platform_role == "admin":
                st.markdown("**角色** 平台管理员")
            if st.button("修改昵称", use_container_width=True, key="sidebar_btn_edit_username"):
                _edit_username_dialog()
            st.caption(f"用户 ID `{current_user_id}`")
            if st.button("退出登录", use_container_width=True, key="sidebar_btn_logout"):
                _logout_dialog()
        else:
            st.caption("状态")
            st.badge("未登录", color="gray")
            st.caption("登录后将自动加载您的会话列表，点击可切换当前会话。")
            if st.button("登录 / 注册", type="primary", use_container_width=True, key="sidebar_btn_open_login"):
                _login_dialog()
            render_acceptance_login_button(key="sidebar_acceptance_login")
    st.divider()
    st.markdown("### 后端连接状态")
    api_base = st.text_input("后端 API 地址", value=API_BASE, key="api_base")
    try:
        r = httpx.get(f"{api_base.rstrip('/')}/health", timeout=2.0)
        if r.status_code == 200:
            st.success("后端已连接")
        else:
            st.warning(f"后端返回 HTTP {r.status_code}")
    except Exception as e:
        st.error(f"未连接: {_short_exc(e)}")

    st.divider()
    st.markdown("### 项目")
    current_user_id = int(st.session_state.get("current_user_id", 0) or 0)
    current_project_id = int(st.session_state.get("current_project_id", 0) or 0)
    if current_user_id > 0:
        new_project_name = st.text_input(
            "新项目名称",
            value=f"项目-{current_user_id}",
            key="sidebar_new_project_name",
        )
        col_create, col_refresh = st.columns(2)
        with col_create:
            if st.button("创建项目", key="sidebar_btn_create_project", use_container_width=True):
                try:
                    r = httpx.post(
                        f"{api_base.rstrip('/')}/project/create",
                        headers=_auth_headers(),
                        json={"name": (new_project_name or f"项目-{current_user_id}").strip()},
                        timeout=10.0,
                    )
                    r.raise_for_status()
                    resp = r.json()
                    new_pid = int(((resp.get("data") or {}).get("id") or 0))
                    if new_pid > 0:
                        st.session_state["current_project_id"] = new_pid
                        st.success(f"项目已创建: {new_pid}")
                        st.rerun()
                except httpx.HTTPStatusError as e:
                    st.error(f"创建项目失败 {e.response.status_code}: {_short_text(e.response.text, 200)}")
                except Exception as e:
                    st.error(_short_exc(e))
        with col_refresh:
            if st.button("刷新", key="sidebar_btn_refresh_projects", use_container_width=True):
                st.rerun()
        projects, perr = _fetch_project_list(api_base)
        if perr:
            st.session_state["last_user_projects"] = []
            st.error(perr)
        else:
            st.session_state["last_user_projects"] = projects
            if current_project_id <= 0:
                for item in projects:
                    if (item or {}).get("is_default"):
                        current_project_id = int((item or {}).get("id") or 0)
                        st.session_state["current_project_id"] = current_project_id
                        break
        st.caption("点击项目切换当前工作上下文。「个人默认」包含历史无项目会话。")
        _render_project_list_buttons(st.session_state.get("last_user_projects") or [], key_prefix="proj")
        if current_project_id > 0:
            st.caption(f"当前项目 ID: `{current_project_id}`")
    else:
        st.caption("登录后可创建并切换项目。")

    st.divider()
    st.markdown("### 会话")
    session_id_input = st.text_input(
        "会话 ID (Session ID)",
        key="session_id_input",
        help="同一会话内上传与分析共用此 ID",
    )
    session_id = session_id_input or st.session_state["session_id"]
    current_username = st.session_state.get("current_username", "")
    if current_user_id > 0:
        if current_project_id <= 0:
            st.caption("将使用「个人默认」项目创建会话（含历史会话）。")
        if st.button("创建新会话（后端生成）", key="sidebar_btn_create_session"):
            try:
                payload = {}
                if current_project_id > 0:
                    payload["project_id"] = current_project_id
                r = httpx.post(
                    f"{api_base.rstrip('/')}/session/create",
                    headers=_auth_headers(),
                    json=payload,
                    timeout=10.0,
                )
                r.raise_for_status()
                resp = r.json()
                new_id = (((resp.get("data") or {}).get("session_id")) or "").strip()
                if not new_id:
                    st.error("创建会话成功但未返回 session_id")
                else:
                    st.session_state["session_id"] = new_id
                    st.session_state["_reset_session_id"] = new_id
                    st.success(f"会话已创建: {new_id}")
                    st.rerun()
            except httpx.HTTPStatusError as e:
                st.error(f"创建会话失败 {e.response.status_code}: {_short_text(e.response.text, 200)}")
            except Exception as e:
                st.error(_short_exc(e))
    else:
        st.caption("未登录时无法创建会话：请使用侧栏 **账户** 中的「登录 / 注册」。")

    if current_user_id > 0:
        if current_project_id > 0:
            sessions, err = _fetch_project_sessions(api_base, current_project_id)
            list_title = "当前项目会话"
        else:
            sessions, err = _fetch_user_sessions_list(api_base)
            list_title = "全部会话（含历史无项目）"
        if err:
            st.session_state["last_user_sessions"] = []
            st.error(err)
        else:
            st.session_state["last_user_sessions"] = sessions
        st.markdown(f"##### {list_title}")
        st.caption("点击一条可切换当前会话（用于上传、工作区与流式分析）。")
        _render_session_list_buttons(st.session_state.get("last_user_sessions") or [], key_prefix="me")
    else:
        st.caption("登录后可查看并切换您的会话列表。")

st.session_state["session_id"] = session_id

from frontend.pages.admin_users import render_admin_users_page
from frontend.pages.project_members import render_project_members_page
from frontend.pages.streaming_analysis import render_streaming_analysis_page
from frontend.pages.template_analysis import render_template_analysis_page

_nav_pages = [
    st.Page(
        lambda: render_streaming_analysis_page(api_base, session_id),
        title="流式分析",
        icon="📊",
        default=True,
    ),
    st.Page(render_template_analysis_page, title="模板分析", icon="📋"),
    st.Page(render_project_members_page, title="项目成员", icon="👥"),
]
if str(st.session_state.get("platform_role") or "") == "admin":
    _nav_pages.append(st.Page(render_admin_users_page, title="用户管理", icon="👤"))

pg = st.navigation(_nav_pages)
pg.run()
