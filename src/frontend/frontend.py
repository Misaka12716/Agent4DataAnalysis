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
import json

from reader.file_types import guess_upload_mime, upload_allowed_extensions

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
                        login_data = data.get("data") or {}
                        st.session_state["current_user_id"] = int(login_data.get("user_id") or 0)
                        st.session_state["current_username"] = str(login_data.get("username") or "")
                        st.session_state["access_token"] = str(login_data.get("access_token") or "")
                        st.session_state["logged_in_phone"] = phone
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
            st.session_state["logged_in_phone"] = ""
            st.session_state["last_user_sessions"] = []
            st.rerun()


if "current_user_id" not in st.session_state:
    st.session_state["current_user_id"] = 0
if "access_token" not in st.session_state:
    st.session_state["access_token"] = ""
if "current_username" not in st.session_state:
    st.session_state["current_username"] = ""
if "last_user_sessions" not in st.session_state:
    st.session_state["last_user_sessions"] = []
if "logged_in_phone" not in st.session_state:
    st.session_state["logged_in_phone"] = ""

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
    st.markdown("### 会话")
    session_id_input = st.text_input(
        "会话 ID (Session ID)",
        key="session_id_input",
        help="同一会话内上传与分析共用此 ID",
    )
    session_id = session_id_input or st.session_state["session_id"]
    current_user_id = int(st.session_state.get("current_user_id", 0) or 0)
    current_username = st.session_state.get("current_username", "")
    if current_user_id > 0:
        if st.button("创建新会话（后端生成）", key="sidebar_btn_create_session"):
            try:
                r = httpx.post(
                    f"{api_base.rstrip('/')}/session/create",
                    headers=_auth_headers(),
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
        sessions, err = _fetch_user_sessions_list(api_base)
        if err:
            st.session_state["last_user_sessions"] = []
            st.error(err)
        else:
            st.session_state["last_user_sessions"] = sessions
        st.markdown("##### 我的会话")
        st.caption("点击一条可切换当前会话（用于上传、工作区与流式分析）。")
        _render_session_list_buttons(st.session_state.get("last_user_sessions") or [], key_prefix="me")
    else:
        st.caption("登录后可查看并切换您的会话列表。")

# 主区顶部
sid_for_display = session_id.strip() or st.session_state.get("session_id", "") or ""
st.title("数据科学多智能体平台")
st.markdown("大语言模型驱动的多智能体协作 · 会话与工作区 · 流式推理 — **联调与演示控制台**")
if sid_for_display:
    st.caption(f"完整会话 ID: `{sid_for_display}`")

col_stream, col_tools = st.columns([2.05, 1.0], gap="large")

with col_stream:
    st.subheader("流式分析")
    st.markdown("中间主区域用于 SSE 流式输出：报告片段与流内快照帧。")
    input_data = st.text_area(
        "分析需求 (input_data)",
        value="请结合工作区中的数据与文件，做简要理解与总结，并给出可继续深入的分析方向。",
        height=80,
        key="input_data",
    )
    with st.container(border=True):
        st.markdown("**实时输出**")
        stream_placeholder = st.empty()

    def _run_sse(endpoint: str, req_json: dict, timeout_seconds: float = 300.0):
        log_events = []
        report_parts = []
        snapshot_content = ""
        snapshot_version = 0
        try:
            with httpx.stream(
                "POST",
                f"{api_base.rstrip('/')}{endpoint}",
                json=req_json,
                headers=_auth_headers(),
                timeout=timeout_seconds,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        payload = json.loads(line[5:].strip())
                        log_events.append(payload)
                        event_type = str(payload.get("type") or "")
                        if event_type == "snapshot":
                            snapshot_content = str(payload.get("content") or "")
                            snapshot_version = int(payload.get("version") or 0)
                        elif event_type == "report_chunk":
                            report_parts.append(str(payload.get("content") or ""))
                        with stream_placeholder.container():
                            st.caption(
                                f"已接收 {len(log_events)} 条事件 · 快照版本 {snapshot_version} · 报告 {len(''.join(report_parts))} 字"
                            )
                            if snapshot_content:
                                st.markdown("**快照锁存（重连首帧）**")
                                st.text_area(
                                    "snapshot_content_view",
                                    value=snapshot_content,
                                    height=180,
                                    key=f"snapshot_content_view_{endpoint}",
                                    label_visibility="collapsed",
                                )
                            if report_parts:
                                st.markdown("**报告（流式）**")
                                st.markdown("".join(report_parts))
                    except json.JSONDecodeError:
                        log_events.append({"raw": line[:200]})
        except httpx.HTTPStatusError as e:
            st.error(f"请求失败 {e.response.status_code}: {_short_text(e.response.text, 200)}")
        except Exception as e:
            st.error(_short_exc(e))

        if log_events:
            with st.expander("查看全部 SSE 事件", expanded=False):
                st.json(log_events)

    b_run, b_rec = st.columns(2)
    with b_run:
        if st.button("开始流式分析", key="btn_run_analysis"):
            if not session_id:
                st.warning("请先在侧栏填写会话 ID")
            else:
                _run_sse(
                    "/run-analysis",
                    {"session_id": session_id, "input_data": input_data},
                    timeout_seconds=300.0,
                )
    with b_rec:
        if st.button("断线恢复（reconnect）", key="btn_run_analysis_reconnect"):
            if not session_id:
                st.warning("请先在侧栏填写会话 ID")
            else:
                _run_sse(
                    "/run-analysis/reconnect",
                    {"session_id": session_id},
                    timeout_seconds=300.0,
                )

with col_tools:
    with st.container(border=True):
        st.markdown("### 上传文件")
        st.caption(
            "支持表格（xlsx / xls / csv / tsv）、图片（png / jpg / jpeg / gif / webp / bmp）、"
            "文本（txt / md / json / yaml / yml / log / xml / html / htm）。可多选批量上传。"
        )
        uploaded_list = st.file_uploader(
            "选择文件（可多选）",
            type=upload_allowed_extensions(),
            accept_multiple_files=True,
            key="upload_session_data",
        )
        if uploaded_list and session_id:
            names = [getattr(f, "name", "?") for f in uploaded_list]
            st.caption(f"已选 **{len(uploaded_list)}** 个文件：" + "、".join(names[:8]) + (" …" if len(names) > 8 else ""))
            if st.button("全部上传", key="btn_upload"):
                ok_paths: list[str] = []
                ok_details: list = []
                err_msgs: list[str] = []
                with st.spinner(f"上传中（{len(uploaded_list)} 个文件）…"):
                    for uf in uploaded_list:
                        fname = getattr(uf, "name", "file")
                        try:
                            uf.seek(0)
                            raw = uf.read()
                            mime = guess_upload_mime(fname, getattr(uf, "type", None))
                            r = httpx.post(
                                f"{api_base.rstrip('/')}/session/upload-excel",
                                files={"file": (fname, raw, mime)},
                                data={"session_id": session_id},
                                headers=_auth_headers(),
                                timeout=120.0,
                            )
                            r.raise_for_status()
                            resp_body = r.json()
                            rp = str(resp_body.get("relative_path") or "").strip()
                            ok_paths.append(rp or fname)
                            ok_details.append({"file": fname, "response": resp_body})
                        except httpx.HTTPStatusError as e:
                            err_msgs.append(f"{fname}: HTTP {e.response.status_code} {_short_text(e.response.text, 120)}")
                        except Exception as e:
                            err_msgs.append(f"{fname}: {_short_exc(e)}")
                if ok_paths:
                    st.success(f"成功 **{len(ok_paths)}** 个：" + "；".join(ok_paths[:12]) + (" …" if len(ok_paths) > 12 else ""))
                    with st.expander("各文件响应 JSON", expanded=False):
                        st.json(ok_details)
                for msg in err_msgs:
                    st.error(msg)
        elif not session_id:
            st.caption("请先在侧栏填写或选择 Session ID。")

        st.divider()
        st.markdown("### 工作区目录与文件")
        st.caption("拉取目录树与已落盘文件元数据。")
        if st.button("拉取工作区", key="btn_workspace_tree"):
            if not session_id:
                st.warning("请先在侧栏填写会话 ID")
            else:
                with st.spinner("拉取中..."):
                    try:
                        r = httpx.get(
                            f"{api_base.rstrip('/')}/session/workspace-tree",
                            params={"session_id": session_id},
                            headers=_auth_headers(),
                            timeout=30.0,
                        )
                        r.raise_for_status()
                        data = r.json()
                        payload = data.get("data") or {}
                        tree = payload.get("tree") or {}
                        files = payload.get("files") or []

                        st.success(f"文件数: **{len(files)}**")
                        with st.expander("目录树（tree）", expanded=False):
                            st.json(tree)
                        with st.expander("实际文件（files）", expanded=False):
                            st.json(files)
                    except httpx.HTTPStatusError as e:
                        st.error(f"请求失败 {e.response.status_code}: {_short_text(e.response.text, 200)}")
                    except Exception as e:
                        st.error(_short_exc(e))
