# frontend/pages/template_analysis.py — 模板管理与分析执行

from __future__ import annotations

import json

import httpx
import pandas as pd
import streamlit as st

from frontend.page_utils import (
    api_base_url,
    api_get,
    api_post,
    auth_headers,
    auth_headers_upload,
    can_analyze,
    can_upload,
    is_logged_in,
    is_platform_admin,
    require_session,
    resolve_project_id_for_permission,
)

try:
    from reader.file_types import guess_upload_mime
except ImportError:

    def guess_upload_mime(name, _=None):
        return "application/octet-stream"


def _to_df(obj):
    if isinstance(obj, dict) and isinstance(obj.get("records"), list):
        return pd.DataFrame(obj["records"])
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return pd.DataFrame(obj)
    return None


def _fmt(value, spec=".3f"):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return format(value, spec)
        except (ValueError, TypeError):
            return str(value)
    return "-" if value is None else str(value)


def _fetch_templates() -> list[dict]:
    if not is_logged_in():
        return []
    result = api_get("/template/list")
    if "error" in result:
        st.error(result["error"])
        return []
    return result.get("data") or []


def render_template_management() -> None:
    st.subheader("模板管理")
    if not is_logged_in():
        st.warning("请先登录。")
        return

    is_admin = is_platform_admin()
    if not is_admin:
        st.caption("模板 CRUD 与批量导入需平台管理员权限；以下为只读列表。")

    if is_admin:
        col1, col2 = st.columns([2, 1])
        with col1:
            if st.button("刷新模板列表", key="refresh_templates"):
                st.rerun()
        with col2:
            if st.button("从 knowledge/templates 批量导入", key="import_templates"):
                r = api_post("/template/import", {})
                if "error" not in r:
                    st.success(f"导入完成: {r.get('data', {})}")
                else:
                    st.error(r["error"])

        uploaded = st.file_uploader("导入单个模板 JSON", type=["json"], key="import_template")
        if uploaded:
            try:
                data = json.load(uploaded)
                resp = httpx.post(
                    f"{api_base_url()}/template/create",
                    json=data,
                    headers=auth_headers(),
                )
                if resp.status_code in (200, 201):
                    st.success("模板导入成功")
                else:
                    st.error(resp.json().get("detail", "导入失败"))
            except Exception as e:
                st.error(f"导入失败: {e}")

    templates = _fetch_templates()
    if not templates:
        st.info("暂无模板。管理员可点击批量导入。")
        return

    rows = [
        {
            "ID": t.get("id"),
            "名称": t.get("template_name", ""),
            "类型": t.get("disease_type", ""),
            "版本": t.get("version", ""),
        }
        for t in templates
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    selected_id = st.selectbox(
        "查看详情",
        [r["ID"] for r in rows],
        format_func=lambda x: next(r["名称"] for r in rows if r["ID"] == x),
    )
    if selected_id:
        detail = api_get(f"/template/{selected_id}")
        if "error" not in detail:
            with st.expander("模板详情", expanded=False):
                st.json(detail.get("data", {}))


def render_analysis_execution() -> None:
    st.subheader("模板分析执行")
    if not is_logged_in():
        st.warning("请先登录。")
        return

    session_id = require_session()
    if not session_id:
        return

    perm_pid = resolve_project_id_for_permission(st.session_state.get("last_user_projects"))
    allow_analyze = can_analyze(perm_pid)
    allow_upload = can_upload(perm_pid)
    if not allow_analyze:
        st.warning("权限不足：需要「统计分析任务创建」权限。")

    templates = _fetch_templates()
    if not templates:
        st.info("暂无可用模板，请联系管理员导入。")
        return

    template_options = {
        f"{t.get('template_name')} (ID {t.get('id')})": int(t.get("id") or 0)
        for t in templates
    }
    template_label = st.selectbox("选择分析模板", list(template_options.keys()))
    template_id = template_options[template_label]

    col1, col2 = st.columns(2)
    with col1:
        uploaded_file = st.file_uploader("上传数据文件（可选）", type=["xlsx", "csv"], key="analysis_data")
    with col2:
        st.caption("未上传时使用工作区已有 xlsx/csv 文件。")

    if st.button("开始模板分析", type="primary", key="start_analysis", disabled=not allow_analyze):
        with st.spinner("执行模板分析..."):
            try:
                if uploaded_file:
                    if not allow_upload:
                        st.error("权限不足：需要「数据上传」权限。")
                        return
                    mime = guess_upload_mime(uploaded_file.name)
                    resp = httpx.post(
                        f"{api_base_url()}/session/upload-excel",
                        data={"session_id": session_id},
                        files={"file": (uploaded_file.name, uploaded_file.getvalue(), mime)},
                        headers=auth_headers_upload(),
                        timeout=120,
                    )
                    if resp.status_code not in (200, 201):
                        st.error(resp.json().get("detail", "上传失败"))
                        return
                run = api_post(
                    "/analysis/template-run",
                    {"session_id": session_id, "template_id": int(template_id)},
                    timeout=180,
                )
                if "error" in run:
                    st.error(run["error"])
                    return
                res_data = run.get("data", {})
                if res_data.get("is_longitudinal"):
                    st.success(
                        f"分析完成 — 纵向数据：{res_data.get('row_count', '?')} 名患者，"
                        f"共 {res_data.get('raw_row_count', '?')} 条随访记录"
                    )
                else:
                    st.success(f"分析完成 — {res_data.get('row_count', '?')} 行")
                st.session_state["analysis_result"] = res_data
            except Exception as e:
                st.error(str(e))

    result = st.session_state.get("analysis_result")
    if not result:
        return
    _render_analysis_result(result)


def _render_analysis_result(result: dict) -> None:
    st.subheader("分析结果")
    if result.get("step_results"):
        st.caption(f"执行模式: {result.get('execution_mode', 'template_steps')}")
        rows = [
            {
                "步": s.get("step"),
                "名称": s.get("name"),
                "算子": s.get("operator"),
                "方法": s.get("method"),
                "状态": s.get("status"),
                "说明": s.get("note") or s.get("error") or "",
            }
            for s in result.get("step_results", [])
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    tabs = st.tabs(
        ["步骤详情", "数据分布", "用药与结局关联", "复发/再入院分析", "有序回归", "相关与症状网络", "报告"]
    )

    with tabs[0]:
        for s in result.get("step_results", []):
            with st.expander(f"Step {s.get('step')} — {s.get('name')} ({s.get('status')})"):
                st.markdown(f"**方法:** {s.get('method')}")
                if s.get("description"):
                    st.caption(s.get("description"))
                if s.get("note"):
                    st.info(s.get("note"))
                if s.get("error"):
                    st.error(s.get("error"))
                if s.get("outputs"):
                    st.json(s.get("outputs"))

    with tabs[1]:
        dist_df = _to_df(result.get("data_distribution"))
        if dist_df is not None and not dist_df.empty:
            st.dataframe(dist_df, use_container_width=True, hide_index=True)
        else:
            st.info("本次分析未产出量表分布统计。")

    with tabs[2]:
        assoc = result.get("medication_outcome_assoc") or {}
        summary = assoc.get("summary_json") or assoc.get("summary") or {}
        if summary:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("检验方法", summary.get("test", "-"))
            c2.metric("χ² / 统计量", _fmt(summary.get("chi2")))
            c3.metric("自由度", summary.get("dof", "-"))
            c4.metric("p 值", _fmt(summary.get("p_value"), ".4f"))
        table_df = _to_df(assoc.get("table_csv"))
        if table_df is not None:
            st.dataframe(table_df, use_container_width=True, hide_index=True)
        trend_df = _to_df(assoc.get("trend_csv"))
        if trend_df is not None and "visit_type" in trend_df.columns:
            mean_cols = [c for c in trend_df.columns if c.endswith("_mean")]
            if mean_cols:
                chart_df = trend_df.set_index("visit_type")[mean_cols].rename(
                    columns={c: c.replace("_mean", "") for c in mean_cols}
                )
                st.line_chart(chart_df)
            st.dataframe(trend_df, use_container_width=True, hide_index=True)

    with tabs[3]:
        surv = result.get("relapse_analysis") or {}
        if surv:
            curve_df = _to_df(surv.get("curve_csv"))
            if curve_df is not None and {"timeline", "survival_prob"}.issubset(curve_df.columns):
                group_col = "group" if "group" in curve_df.columns else None
                pivot = (
                    curve_df.pivot_table(
                        index="timeline", columns=group_col, values="survival_prob", aggfunc="last"
                    )
                    if group_col
                    else curve_df.set_index("timeline")[["survival_prob"]]
                )
                st.line_chart(pivot.ffill())

    with tabs[4]:
        ordr = result.get("ordinal_regression") or {}
        coef_df = _to_df(ordr.get("coef_table_csv"))
        if coef_df is not None:
            st.dataframe(coef_df, use_container_width=True, hide_index=True)

    with tabs[5]:
        corr = result.get("scale_correlation") or {}
        matrix_df = _to_df(corr.get("matrix_csv"))
        if matrix_df is not None:
            label_col = matrix_df.columns[0]
            st.dataframe(matrix_df.set_index(label_col), use_container_width=True)

    with tabs[6]:
        st.markdown(result.get("report_markdown", ""))


def render_template_analysis_page() -> None:
    st.title("模板分析")
    tab1, tab2 = st.tabs(["模板管理", "分析执行"])
    with tab1:
        render_template_management()
    with tab2:
        render_analysis_execution()
