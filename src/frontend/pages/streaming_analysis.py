# frontend/pages/streaming_analysis.py — LLM 流式分析联调页

from __future__ import annotations

import json

import httpx
import streamlit as st

from reader.file_types import guess_upload_mime, upload_allowed_extensions

from frontend.page_utils import can_analyze, can_upload, resolve_project_id_for_permission


def _short_text(s: str, max_len: int = 160) -> str:
    s = s.strip() if isinstance(s, str) else str(s)
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _short_exc(e: BaseException, max_len: int = 120) -> str:
    return _short_text(str(e), max_len)


def _auth_headers() -> dict[str, str]:
    token = str(st.session_state.get("access_token") or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def render_streaming_analysis_page(api_base: str, session_id: str) -> None:
    current_user_id = int(st.session_state.get("current_user_id", 0) or 0)
    sid_for_display = session_id.strip() or st.session_state.get("session_id", "") or ""

    st.title("流式分析")
    st.markdown("大语言模型驱动的多智能体协作 · SSE 流式推理")
    if sid_for_display:
        st.caption(f"当前会话: `{sid_for_display}`")

    col_stream, col_tools = st.columns([2.05, 1.0], gap="large")

    with col_stream:
        st.subheader("分析需求")
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
                                    f"已接收 {len(log_events)} 条事件 · 快照版本 {snapshot_version} · "
                                    f"报告 {len(''.join(report_parts))} 字"
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
        perm_pid = resolve_project_id_for_permission(st.session_state.get("last_user_projects"))
        allow_analyze = can_analyze(perm_pid) if current_user_id > 0 else False
        if current_user_id > 0 and not allow_analyze:
            st.warning("权限不足：需要「统计分析任务创建」权限才能发起分析。")
        with b_run:
            if st.button("开始流式分析", key="btn_run_analysis", disabled=not allow_analyze):
                if not session_id:
                    st.warning("请先在侧栏填写会话 ID")
                else:
                    _run_sse(
                        "/run-analysis",
                        {"session_id": session_id, "input_data": input_data},
                        timeout_seconds=300.0,
                    )
        with b_rec:
            if st.button("断线恢复（reconnect）", key="btn_run_analysis_reconnect", disabled=not allow_analyze):
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
                "支持表格（xlsx / xls / csv / tsv）、图片、文本等。可多选批量上传。"
            )
            uploaded_list = st.file_uploader(
                "选择文件（可多选）",
                type=upload_allowed_extensions(),
                accept_multiple_files=True,
                key="upload_session_data",
            )
            if uploaded_list and session_id:
                names = [getattr(f, "name", "?") for f in uploaded_list]
                st.caption(
                    f"已选 **{len(uploaded_list)}** 个文件："
                    + "、".join(names[:8])
                    + (" …" if len(names) > 8 else "")
                )
                allow_upload = can_upload(perm_pid) if current_user_id > 0 else False
                if current_user_id > 0 and not allow_upload:
                    st.warning("权限不足：需要「数据上传」权限。")
                if st.button("全部上传", key="btn_upload", disabled=not allow_upload):
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
                                orig = str(resp_body.get("original_filename") or fname).strip()
                                display = orig if orig else (rp or fname)
                                if resp_body.get("renamed") and rp and rp != orig:
                                    display = f"{orig} → {rp}"
                                ok_paths.append(display)
                                ok_details.append({"file": fname, "response": resp_body})
                            except httpx.HTTPStatusError as e:
                                err_msgs.append(
                                    f"{fname}: HTTP {e.response.status_code} "
                                    f"{_short_text(e.response.text, 120)}"
                                )
                            except Exception as e:
                                err_msgs.append(f"{fname}: {_short_exc(e)}")
                    if ok_paths:
                        st.success(
                            f"成功 **{len(ok_paths)}** 个："
                            + "；".join(ok_paths[:12])
                            + (" …" if len(ok_paths) > 12 else "")
                        )
                        with st.expander("各文件响应 JSON", expanded=False):
                            st.json(ok_details)
                    for msg in err_msgs:
                        st.error(msg)
            elif not session_id:
                st.caption("请先在侧栏填写或选择 Session ID。")

            st.divider()
            st.markdown("### 工作区目录与文件")
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
                            st.error(
                                f"请求失败 {e.response.status_code}: "
                                f"{_short_text(e.response.text, 200)}"
                            )
                        except Exception as e:
                            st.error(_short_exc(e))

    if current_user_id > 0:
        with st.expander("项目生命周期管理（raw / 资产 / 归档）", expanded=False):
            from frontend.pages.project_workspace import render_project_lifecycle

            render_project_lifecycle(api_base)
