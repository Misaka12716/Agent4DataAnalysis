"""
最简前端：用于测试 会话上传 / 快照 / 流式分析 接口。
运行方式（在 src 目录下）: streamlit run frontend/frontend.py
"""
import streamlit as st
import httpx
import json

# 后端地址（与 backend/server 默认端口一致）
API_BASE = "http://localhost:52716"

st.set_page_config(page_title="Excel 分析智能体测试", layout="wide")
st.title("Excel 分析智能体 - 测试前端")

if "current_user_id" not in st.session_state:
    st.session_state["current_user_id"] = 0
if "current_username" not in st.session_state:
    st.session_state["current_username"] = ""
if "last_user_sessions" not in st.session_state:
    st.session_state["last_user_sessions"] = []

# 侧栏：会话与接口地址（会话 ID 由后端创建）
if "session_id" not in st.session_state:
    st.session_state["session_id"] = ""
if "session_id_input" not in st.session_state:
    st.session_state["session_id_input"] = st.session_state["session_id"]
# 若上一步点了「创建会话」，在创建 widget 前先同步到输入框的 key，避免 widget 创建后再改 key 报错
if "_reset_session_id" in st.session_state:
    st.session_state["session_id_input"] = st.session_state.pop("_reset_session_id")

with st.sidebar:
    st.subheader("配置")
    api_base = st.text_input("后端 API 地址", value=API_BASE, key="api_base")
    session_id_input = st.text_input(
        "会话 ID (Session ID)",
        key="session_id_input",
        help="同一会话内上传与分析共用此 ID",
    )
    session_id = session_id_input or st.session_state["session_id"]
    current_user_id = int(st.session_state.get("current_user_id", 0) or 0)
    current_username = st.session_state.get("current_username", "")
    if current_user_id > 0:
        st.caption(f"当前登录用户: {current_username} (id={current_user_id})")
        if st.button("创建新会话（后端生成）"):
            try:
                r = httpx.post(
                    f"{api_base.rstrip('/')}/session/create",
                    json={"user_id": current_user_id},
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
                st.error(f"创建会话失败 {e.response.status_code}: {e.response.text}")
            except Exception as e:
                st.error(str(e))
    else:
        st.caption("当前登录用户: 未登录（请先登录后创建会话）")
    # 后端健康检查
    try:
        r = httpx.get(f"{api_base.rstrip('/')}/health", timeout=2.0)
        if r.status_code == 200:
            st.success("后端已连接")
        else:
            st.warning(f"后端返回 {r.status_code}")
    except Exception as e:
        st.error(f"后端未连接: {e}")

# 五个测试区块（按逻辑先后：登录/会话 -> 上传数据 -> 工作区目录与文件 -> 流式分析 -> 快照校验）
tab_login, tab_upload, tab_workspace, tab_analysis, tab_snapshot = st.tabs(
    ["1. 用户登录与会话", "2. 上传 Excel", "3. 工作区目录与文件", "4. 流式分析", "5. 会话快照"]
)

with tab_upload:
    st.subheader("上传 Excel 到会话工作区")
    st.caption("调用 POST /session/upload-excel，文件会保存到该会话工作区根目录。")
    uploaded = st.file_uploader("选择 Excel 文件", type=["xlsx", "xls","csv"], key="upload_excel")
    if uploaded and session_id:
        if st.button("上传", key="btn_upload"):
            with st.spinner("上传中..."):
                try:
                    uploaded.seek(0)
                    r = httpx.post(
                        f"{api_base.rstrip('/')}/session/upload-excel",
                        files={"file": (uploaded.name, uploaded.read(), uploaded.type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                        data={"session_id": session_id},
                        timeout=60.0,
                    )
                    r.raise_for_status()
                    data = r.json()
                    st.success(f"上传成功：{data.get('relative_path', '')}")
                    st.json(data)
                except httpx.HTTPStatusError as e:
                    st.error(f"请求失败 {e.response.status_code}: {e.response.text}")
                except Exception as e:
                    st.error(str(e))

with tab_snapshot:
    st.subheader("会话快照（断线重连）")
    st.caption("调用 GET /session/snapshot，获取该会话的完整累计内容与版本号。")
    if st.button("拉取快照", key="btn_snapshot"):
        if not session_id:
            st.warning("请先填写会话 ID")
        else:
            with st.spinner("拉取中..."):
                try:
                    r = httpx.get(
                        f"{api_base.rstrip('/')}/session/snapshot",
                        params={"session_id": session_id},
                        timeout=10.0,
                    )
                    r.raise_for_status()
                    data = r.json()
                    version = data.get("version", 0)
                    content = data.get("content", "")
                    st.metric("当前版本号", version)
                    st.text_area("完整累计内容", value=content or "(空)", height=200, key="snapshot_content")
                except httpx.HTTPStatusError as e:
                    st.error(f"请求失败 {e.response.status_code}: {e.response.text}")
                except Exception as e:
                    st.error(str(e))

with tab_workspace:
    st.subheader("工作区目录树与实际文件")
    st.caption("调用 GET /session/workspace-tree，返回目录树（tree）与实际文件数据（files）。")
    if st.button("拉取工作区目录与文件", key="btn_workspace_tree"):
        if not session_id:
            st.warning("请先填写会话 ID")
        else:
            with st.spinner("拉取中..."):
                try:
                    r = httpx.get(
                        f"{api_base.rstrip('/')}/session/workspace-tree",
                        params={"session_id": session_id},
                        timeout=30.0,
                    )
                    r.raise_for_status()
                    data = r.json()
                    payload = data.get("data") or {}
                    tree = payload.get("tree") or {}
                    files = payload.get("files") or []

                    st.success(f"拉取成功，文件数: {len(files)}")
                    st.markdown("#### 目录树（tree）")
                    st.json(tree)

                    st.markdown("#### 实际文件（files）")
                    st.json(files)
                except httpx.HTTPStatusError as e:
                    st.error(f"请求失败 {e.response.status_code}: {e.response.text}")
                except Exception as e:
                    st.error(str(e))

with tab_analysis:
    st.subheader("流式分析任务")
    st.caption("调用 POST /run-analysis（开始）或 POST /run-analysis/reconnect（断线恢复），实时显示 SSE 推送。")
    input_data = st.text_area(
        "分析需求 (input_data)",
        value="请对工作区目录下的 Excel 做简单描述性统计，并给出结论。",
        height=80,
        key="input_data",
    )
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
                        # 实时更新占位：事件数 + 快照版本 + 已收报告长度
                        with stream_placeholder.container():
                            st.caption(
                                f"已接收 {len(log_events)} 条事件，快照版本 {snapshot_version}，报告片段 {len(''.join(report_parts))} 字"
                            )
                            if snapshot_content:
                                st.markdown("---\n**快照锁存内容（重连首帧）**\n")
                                st.text_area(
                                    "snapshot_content_view",
                                    value=snapshot_content,
                                    height=180,
                                    key=f"snapshot_content_view_{endpoint}",
                                )
                            if report_parts:
                                st.markdown("---\n**报告内容（流式）**\n")
                                st.markdown("".join(report_parts))
                    except json.JSONDecodeError:
                        log_events.append({"raw": line[:200]})
        except httpx.HTTPStatusError as e:
            st.error(f"请求失败 {e.response.status_code}: {e.response.text}")
        except Exception as e:
            st.error(str(e))

        if log_events:
            with st.expander("查看全部 SSE 事件"):
                st.json(log_events)

    if st.button("开始流式分析", key="btn_run_analysis"):
        if not session_id:
            st.warning("请先填写会话 ID")
        else:
            _run_sse(
                "/run-analysis",
                {"session_id": session_id, "input_data": input_data},
                timeout_seconds=300.0,
            )

    if st.button("断线恢复（reconnect）", key="btn_run_analysis_reconnect"):
        if not session_id:
            st.warning("请先填写会话 ID")
        else:
            _run_sse(
                "/run-analysis/reconnect",
                {"session_id": session_id},
                timeout_seconds=300.0,
            )

with tab_login:
    st.subheader("用户登录接口测试")
    st.caption("用于测试发送短信验证码、短信登录（登录/注册一体）与用户会话列表查询接口。")
    phone = st.text_input(
        "手机号",
        value="18395299120",
        key="auth_phone",
        help="按后端当前规则需为 11 位中国大陆手机号，例如 18395299120",
    ).strip()
    sms_code = st.text_input(
        "短信验证码",
        value="",
        key="auth_sms_code",
        help="输入收到的 6 位短信验证码",
    ).strip()
    logged_user_id = int(st.session_state.get("current_user_id", 0) or 0)

    def _render_sessions_picker(sessions: list, key_prefix: str) -> None:
        if not sessions:
            st.info("当前没有可展示的会话。")
            return
        st.markdown("#### 会话列表（含标题）")
        for idx, item in enumerate(sessions):
            sid = str((item or {}).get("session_id") or "").strip()
            title = str((item or {}).get("title") or "").strip() or "(未命名)"
            if not sid:
                continue
            c1, c2 = st.columns([6, 1])
            with c1:
                st.text(f"{idx + 1}. {title} | {sid}")
            with c2:
                if st.button("使用", key=f"{key_prefix}_use_session_{idx}_{sid}"):
                    st.session_state["session_id"] = sid
                    st.session_state["_reset_session_id"] = sid
                    st.success(f"已回填会话 ID: {sid}")
                    st.rerun()

    if logged_user_id > 0:
        st.caption(f"已登录用户: {st.session_state.get('current_username', '')} (id={logged_user_id})")
        if st.button("创建会话（后端生成）", key="btn_create_session_in_tab4"):
            try:
                r = httpx.post(
                    f"{api_base.rstrip('/')}/session/create",
                    json={"user_id": logged_user_id},
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
                st.error(f"创建会话失败 {e.response.status_code}: {e.response.text}")
            except Exception as e:
                st.error(str(e))
        if st.button("查询该用户全部会话", key="btn_list_user_sessions"):
            try:
                r = httpx.get(
                    f"{api_base.rstrip('/')}/session/list",
                    params={"user_id": logged_user_id},
                    timeout=10.0,
                )
                r.raise_for_status()
                data = r.json()
                sessions = ((data.get("data") or {}).get("sessions")) or []
                st.session_state["last_user_sessions"] = sessions
                st.success(f"查询成功，共 {len(sessions)} 个会话")
                st.json(data)
                _render_sessions_picker(sessions, key_prefix=f"login_{logged_user_id}")
                # 便于继续联调：默认选中最新会话回填到当前会话 ID
                if sessions:
                    latest_session_id = str((sessions[0] or {}).get("session_id") or "").strip()
                    if latest_session_id:
                        st.session_state["session_id"] = latest_session_id
                        st.session_state["_reset_session_id"] = latest_session_id
                        st.info(f"已将最新会话回填到当前会话 ID: {latest_session_id}")
            except httpx.HTTPStatusError as e:
                st.error(f"查询会话失败 {e.response.status_code}: {e.response.text}")
            except Exception as e:
                st.error(str(e))

    col1, col2 = st.columns(2)
    with col1:
        if st.button("发送短信验证码", key="btn_send_sms_code"):
            if not phone:
                st.warning("请先输入手机号")
            else:
                with st.spinner("发送中..."):
                    try:
                        r = httpx.post(
                            f"{api_base.rstrip('/')}/auth/send-sms-code",
                            json={"phone": phone},
                            timeout=10.0,
                        )
                        r.raise_for_status()
                        data = r.json()
                        st.success("发送成功")
                        st.json(data)
                    except httpx.HTTPStatusError as e:
                        st.error(f"请求失败 {e.response.status_code}: {e.response.text}")
                    except Exception as e:
                        st.error(str(e))

    with col2:
        if st.button("短信登录 / 自动注册", key="btn_login_with_sms"):
            if not phone:
                st.warning("请先输入手机号")
            elif not sms_code:
                st.warning("请先输入短信验证码")
            else:
                with st.spinner("登录中..."):
                    try:
                        r = httpx.post(
                            f"{api_base.rstrip('/')}/auth/login-with-sms",
                            json={"phone": phone, "code": sms_code},
                            timeout=10.0,
                        )
                        r.raise_for_status()
                        data = r.json()
                        login_data = data.get("data") or {}
                        st.session_state["current_user_id"] = int(login_data.get("user_id") or 0)
                        st.session_state["current_username"] = str(login_data.get("username") or "")
                        st.success("登录成功")
                        st.json(data)
                        st.rerun()
                    except httpx.HTTPStatusError as e:
                        st.error(f"请求失败 {e.response.status_code}: {e.response.text}")
                    except Exception as e:
                        st.error(str(e))

    st.markdown("---")
    st.caption("未登录也可手动输入 user_id 测试 GET /session/list（返回 sessions）。")
    manual_user_id = st.number_input(
        "手动测试 user_id",
        min_value=1,
        step=1,
        value=1,
        key="manual_user_id_for_session_list",
    )
    if st.button("按 user_id 查询会话列表（手动）", key="btn_list_user_sessions_manual"):
        try:
            r = httpx.get(
                f"{api_base.rstrip('/')}/session/list",
                params={"user_id": int(manual_user_id)},
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
            sessions = ((data.get("data") or {}).get("sessions")) or []
            st.session_state["last_user_sessions"] = sessions
            st.success(f"查询成功，共 {len(sessions)} 个会话")
            st.json(data)
            _render_sessions_picker(sessions, key_prefix=f"manual_{int(manual_user_id)}")
        except httpx.HTTPStatusError as e:
            st.error(f"查询会话失败 {e.response.status_code}: {e.response.text}")
        except Exception as e:
            st.error(str(e))

    cached_sessions = st.session_state.get("last_user_sessions") or []
    if cached_sessions:
        st.markdown("---")
        st.caption("最近一次查询结果（可直接点“使用”回填会话 ID）")
        _render_sessions_picker(cached_sessions, key_prefix="cached")

st.sidebar.caption("确保后端已启动: uvicorn backend.server:app --port 52716")
