"""
最简前端：用于测试 会话上传 / 快照 / 流式分析 接口。
运行方式（在 src 目录下）: streamlit run frontend/frontend.py
"""
import streamlit as st
import httpx
import uuid
import json

# 后端地址（与 backend/server 默认端口一致）
API_BASE = "http://localhost:52716"

st.set_page_config(page_title="Excel 分析智能体测试", layout="wide")
st.title("Excel 分析智能体 - 测试前端")

# 侧栏：会话与接口地址（用独立 key 存会话 ID，避免与 widget 绑定 key 冲突）
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
# 若上一步点了「生成新会话 ID」，在创建 widget 前先同步到输入框的 key，避免 widget 创建后再改 key 报错
if "_reset_session_id" in st.session_state:
    st.session_state["session_id_input"] = st.session_state.pop("_reset_session_id")

with st.sidebar:
    st.subheader("配置")
    api_base = st.text_input("后端 API 地址", value=API_BASE, key="api_base")
    session_id_input = st.text_input(
        "会话 ID (Session ID)",
        value=st.session_state["session_id"],
        key="session_id_input",
        help="同一会话内上传与分析共用此 ID",
    )
    session_id = session_id_input or st.session_state["session_id"]
    if st.button("生成新会话 ID"):
        new_id = str(uuid.uuid4())
        st.session_state["session_id"] = new_id
        st.session_state["_reset_session_id"] = new_id  # 下一轮在创建 widget 前会同步到 session_id_input
        st.rerun()
    # 后端健康检查
    try:
        r = httpx.get(f"{api_base.rstrip('/')}/health", timeout=2.0)
        if r.status_code == 200:
            st.success("后端已连接")
        else:
            st.warning(f"后端返回 {r.status_code}")
    except Exception as e:
        st.error(f"后端未连接: {e}")

# 三个测试区块
tab1, tab2, tab3 = st.tabs(["1. 上传 Excel", "2. 会话快照", "3. 流式分析"])

with tab1:
    st.subheader("上传 Excel 到会话工作区")
    st.caption("调用 POST /session/upload-excel，文件会保存到该会话工作区的 input/ 目录。")
    uploaded = st.file_uploader("选择 Excel 文件", type=["xlsx", "xls"], key="upload_excel")
    if uploaded and session_id:
        if st.button("上传", key="btn_upload"):
            with st.spinner("上传中..."):
                try:
                    uploaded.seek(0)
                    r = httpx.post(
                        f"{api_base.rstrip('/')}/session/upload-excel",
                        files={"file": (uploaded.name, uploaded.read(), uploaded.type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                        data={"session_id": session_id, "user_id": "0"},
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

with tab2:
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

with tab3:
    st.subheader("流式分析任务")
    st.caption("调用 POST /run-analysis，绑定当前会话，执行 Planner→Coder→Worker→Reporter，实时显示 SSE 推送。")
    input_data = st.text_area(
        "分析需求 (input_data)",
        value="请对工作区 input 目录下的 Excel 做简单描述性统计，并给出结论。",
        height=80,
        key="input_data",
    )
    if st.button("开始流式分析", key="btn_run_analysis"):
        if not session_id:
            st.warning("请先填写会话 ID")
        else:
            stream_placeholder = st.empty()
            log_events = []
            report_parts = []
            try:
                with httpx.stream(
                    "POST",
                    f"{api_base.rstrip('/')}/run-analysis",
                    json={"session_id": session_id, "input_data": input_data},
                    timeout=300.0,
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        try:
                            payload = json.loads(line[5:].strip())
                            log_events.append(payload)
                            if payload.get("type") == "report_chunk":
                                report_parts.append(payload.get("content", ""))
                            # 实时更新占位：事件数 + 已收报告长度
                            with stream_placeholder.container():
                                st.caption(f"已接收 {len(log_events)} 条事件，报告片段 {len(''.join(report_parts))} 字")
                                if report_parts:
                                    st.markdown("---\n**报告内容（流式）**\n")
                                    st.text("".join(report_parts))
                        except json.JSONDecodeError:
                            log_events.append({"raw": line[:200]})
            except httpx.HTTPStatusError as e:
                st.error(f"请求失败 {e.response.status_code}: {e.response.text}")
            except Exception as e:
                st.error(str(e))
            # 结束后展示完整报告与事件列表
            if report_parts:
                st.subheader("最终报告")
                st.markdown("".join(report_parts))
            if log_events:
                with st.expander("查看全部 SSE 事件"):
                    st.json(log_events)

st.sidebar.caption("确保后端已启动: uvicorn backend.server:app --port 52716")
