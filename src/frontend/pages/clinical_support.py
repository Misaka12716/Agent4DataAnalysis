# frontend/pages/clinical_support.py
# Clinical support UI — Excel-like filters, patient pickers, rich visualizations

import sys
from pathlib import Path as P

_SRC = P(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import io
import json
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

from frontend.page_utils import API_BASE, api_get, api_post, auth_headers, render_auth_sidebar, require_session
from frontend.clinical_ui_helpers import (
    DIAG_EN,
    auto_cache_summary,
    build_filter_tree,
    cache_report_section,
    cohort_pair_picker,
    detect_feature_and_target_columns,
    detect_primary_diagnosis,
    ensure_patient_catalog,
    invalidate_patient_catalog,
    parse_uploaded_table,
    patient_multiselect,
    plot_followup_trend,
    plot_heatmap_payload,
    plot_matrix_heatmap,
    plot_network_graph,
    render_batch_abnormality,
    render_batch_risk_results,
    render_cluster_result,
    render_correlation_result,
    render_followup_compare,
    render_model_evaluation,
    render_partial_correlation_result,
    render_reference_compare,
    render_spectrum_result,
    summarize_analysis_for_report,
    summarize_followup_compare,
    summarize_followup_trend,
    summarize_reference_compare,
    zh_diagnosis,
)

FIXTURE_DIR = _SRC.parent / "tests" / "fixtures"


def _load_fixture_bytes(name: str) -> bytes | None:
    path = FIXTURE_DIR / name
    return path.read_bytes() if path.exists() else None


def _render_data_status():
    resp = api_get("/clinical/data/status")
    data = (resp.get("data") or {}) if "error" not in resp else {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("患者", data.get("patients", 0))
    c2.metric("随访记录", data.get("followups", 0))
    c3.metric("参考区间", data.get("reference_ranges", 0))
    ready = data.get("ready", False)
    c4.metric("可分析", "是" if ready else "否")
    if not ready:
        st.warning("请先在「数据导入」上传患者、随访与参考区间 CSV/Excel，无需依赖 seed 脚本。")
    return data


FIELD_LABELS_ZH = {
    "patient_id": "患者编号",
    "age": "年龄",
    "gender": "性别",
    "diagnosis": "诊断",
    "admission_date": "入院日期",
    "discharge_date": "出院日期",
    "HAMD_total": "HAMD 总分",
    "HAMA_total": "HAMA 总分",
    "PHQ9_total": "PHQ-9 总分",
    "disease_duration_years": "病程（年）",
    "medication": "用药",
    "outcome": "结局",
    "relapse": "是否复发",
    "visit_date": "随访日期",
    "visit_type": "访视类型",
    "medication_dose_mg": "用药剂量(mg)",
    "notes": "备注",
    "indicator": "指标名",
    "lower_bound": "参考下限",
    "upper_bound": "参考上限",
    "age_range_lower": "适用年龄下限",
    "age_range_upper": "适用年龄上限",
    "unit": "单位",
    "source": "来源",
}


def _mapping_state_key(prefix: str) -> str:
    return f"clinical_mapping_{prefix}"


def _suggest_mapping(dataset_type: str, rows: list, user_override: dict | None = None) -> dict:
    payload = {
        "dataset_type": dataset_type,
        "rows": rows[:50],
        "use_llm": True,
    }
    if user_override:
        payload["column_mapping"] = user_override
    return api_post("/clinical/import/suggest-mapping", payload)


def _render_mapping_editor(prefix: str, mapping_payload: dict) -> dict:
    """Show editable canonical_field -> source_column mapping; return user mapping."""
    source_columns = [""] + list(mapping_payload.get("source_columns") or [])
    required_fields = set(mapping_payload.get("required_fields") or [])
    optional_fields = mapping_payload.get("optional_fields") or []
    all_fields = list(mapping_payload.get("required_fields") or []) + list(optional_fields)
    current = dict(mapping_payload.get("column_mapping") or {})

    st.markdown("##### 列映射（可修改）")
    st.caption("左侧为标准字段，右侧为文件中的列名。标 * 为必填。")

    edited: dict[str, str] = {}
    cols = st.columns(2)
    half = (len(all_fields) + 1) // 2
    for idx, field in enumerate(all_fields):
        with cols[0 if idx < half else 1]:
            label = FIELD_LABELS_ZH.get(field, field)
            if field in required_fields:
                label += " *"
            default_src = current.get(field, "")
            try:
                default_idx = source_columns.index(default_src) if default_src in source_columns else 0
            except ValueError:
                default_idx = 0
            picked = st.selectbox(
                label,
                source_columns,
                index=default_idx,
                format_func=lambda x: "（不映射）" if not x else x,
                key=f"{prefix}_map_{field}",
            )
            if picked:
                edited[field] = picked

    warnings = list(mapping_payload.get("warnings") or [])
    missing = [f for f in required_fields if f not in edited]
    if missing:
        st.error("以下必填字段尚未映射：" + "，".join(FIELD_LABELS_ZH.get(f, f) for f in missing))
    for w in warnings:
        st.warning(w)

    unmapped = mapping_payload.get("unmapped_source_columns") or []
    if unmapped:
        st.caption("文件中未映射的列：" + "，".join(unmapped[:12]) + (" …" if len(unmapped) > 12 else ""))

    rationale = mapping_payload.get("rationale") or []
    if rationale:
        with st.expander("映射说明（LLM/规则）", expanded=False):
            for line in rationale[:20]:
                st.text(line)

    preview_rows = mapping_payload.get("preview_rows") or []
    if preview_rows:
        with st.expander("映射后预览（前 5 行）", expanded=False):
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)

    return edited


def _run_import_with_mapping(
    dataset_type: str,
    endpoint: str,
    uploaded,
    mode_label: str,
    *,
    state_prefix: str,
    import_btn_label: str = "确认导入",
):
    if not uploaded:
        st.warning("请先选择文件")
        return

    state_key = _mapping_state_key(state_prefix)
    try:
        rows = parse_uploaded_table(uploaded)
        if not rows:
            st.warning("文件为空或无法解析")
            return

        file_sig = f"{uploaded.name}:{len(rows)}"
        cached = st.session_state.get(state_key) or {}
        if cached.get("file_sig") != file_sig:
            suggest = _suggest_mapping(dataset_type, rows)
            if "error" in suggest:
                st.error(suggest["error"])
                return
            cached = suggest.get("data") or {}
            cached["file_sig"] = file_sig
            cached["raw_rows"] = rows
            st.session_state[state_key] = cached

        mapping_payload = st.session_state[state_key]
        edited_mapping = _render_mapping_editor(state_prefix, mapping_payload)

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("重新推断映射", key=f"{state_prefix}_remap"):
                suggest = _suggest_mapping(dataset_type, rows, user_override=edited_mapping or None)
                if "error" not in suggest:
                    fresh = suggest.get("data") or {}
                    fresh["file_sig"] = file_sig
                    fresh["raw_rows"] = rows
                    st.session_state[state_key] = fresh
                st.rerun()
        with col_b:
            do_import = st.button(import_btn_label, key=f"{state_prefix}_import", type="primary")

        if do_import:
            required = set(mapping_payload.get("required_fields") or [])
            missing = [f for f in required if f not in edited_mapping]
            if missing:
                st.error("请先完成必填字段映射再导入")
                return
            result = api_post(endpoint, {
                "rows": mapping_payload.get("raw_rows") or rows,
                "column_mapping": edited_mapping,
                "mode": "append_only" if "append" in mode_label else "upsert",
            })
            if "error" not in result:
                d = result.get("data", {})
                st.success(
                    f"导入完成：新增 {d.get('inserted')}，更新 {d.get('updated')}，跳过 {d.get('skipped')}"
                )
                if d.get("errors"):
                    st.caption("部分行失败：" + "；".join(d["errors"][:5]))
                if d.get("mapping_errors"):
                    st.caption("映射警告：" + "；".join(d["mapping_errors"][:5]))
                st.session_state.pop(state_key, None)
                invalidate_patient_catalog()
                st.rerun()
            else:
                st.error(result["error"])
    except Exception as e:
        st.error(f"解析或导入失败: {e}")


def _run_import(endpoint: str, uploaded, mode_label: str, rows_key: str = "rows"):
    """Legacy direct import without mapping UI (kept for compatibility)."""
    if not uploaded:
        st.warning("请先选择文件")
        return
    try:
        rows = parse_uploaded_table(uploaded)
        result = api_post(endpoint, {
            rows_key: rows,
            "mode": "append_only" if "append" in mode_label else "upsert",
        })
        if "error" not in result:
            d = result.get("data", {})
            st.success(
                f"导入完成：新增 {d.get('inserted')}，更新 {d.get('updated')}，跳过 {d.get('skipped')}"
            )
            if d.get("errors"):
                st.caption("部分行失败：" + "；".join(d["errors"][:5]))
            invalidate_patient_catalog()
            st.rerun()
        else:
            st.error(result["error"])
    except Exception as e:
        st.error(f"解析或导入失败: {e}")


def render_patient_query():
    st.subheader("患者检索与纳排")
    st.caption("正式使用请通过 **数据导入** 上传 CSV/Excel 写入临床库；各分析模块均读取库内数据，不绑定本地 seed 文件。")

    status = _render_data_status()
    df = ensure_patient_catalog(api_post)
    tab_import, tab_filter, tab_saved = st.tabs(["数据导入", "列筛选查询", "已保存条件"])

    with tab_import:
        with st.expander("① 患者主表（必填）", expanded=True):
            st.caption("上传后系统会先用 LLM/规则推断列映射，请核对后再导入。必填：patient_id")
            tpl = _load_fixture_bytes("patient_import_template.csv") or _load_fixture_bytes("risk_training_sample.csv")
            if tpl:
                st.download_button("下载患者模板", tpl, file_name="patient_import_template.csv", mime="text/csv", key="dl_patient_tpl")
            up_p = st.file_uploader("患者 CSV/Excel", type=["csv", "xlsx"], key="patient_import_file")
            mode_p = st.radio("患者导入模式", ["upsert（存在则更新）", "append_only（仅新增）"], horizontal=True, key="patient_import_mode")
            if up_p is not None:
                _run_import_with_mapping("patient", "/patient/import", up_p, mode_p, state_prefix="patient", import_btn_label="确认导入患者")

        with st.expander("② 随访记录（随访分析需要）", expanded=not status.get("followups")):
            st.caption("必填：patient_id, visit_date。上传后可编辑列映射。")
            tpl_fu = _load_fixture_bytes("followup_import_template.csv")
            if tpl_fu:
                st.download_button("下载随访模板", tpl_fu, file_name="followup_import_template.csv", mime="text/csv", key="dl_followup_tpl")
            up_fu = st.file_uploader("随访 CSV/Excel", type=["csv", "xlsx"], key="followup_import_file")
            mode_fu = st.radio("随访导入模式", ["upsert（存在则更新）", "append_only（仅新增）"], horizontal=True, key="followup_import_mode")
            if up_fu is not None:
                _run_import_with_mapping("followup", "/followup/import", up_fu, mode_fu, state_prefix="followup", import_btn_label="确认导入随访")

        with st.expander("③ 参考区间（异常评估需要）", expanded=not status.get("reference_ranges")):
            st.caption("必填：indicator, lower_bound, upper_bound。上传后可编辑列映射。")
            tpl_ref = _load_fixture_bytes("reference_import_template.csv")
            if tpl_ref:
                st.download_button("下载参考区间模板", tpl_ref, file_name="reference_import_template.csv", mime="text/csv", key="dl_ref_tpl")
            up_ref = st.file_uploader("参考区间 CSV/Excel", type=["csv", "xlsx"], key="reference_import_file")
            mode_ref = st.radio("参考区间导入模式", ["upsert（追加写入）", "append_only（仅新增）"], horizontal=True, key="reference_import_mode")
            if up_ref is not None:
                _run_import_with_mapping("reference", "/reference/import", up_ref, mode_ref, state_prefix="reference", import_btn_label="确认导入参考区间")

    with tab_filter:
        if df.empty:
            st.warning("患者目录为空，请先在「数据导入」上传患者主表。")
        if not df.empty:
            st.markdown("##### 列筛选（AND 组合）")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                diagnosis = st.multiselect(
                    "诊断",
                    options=sorted(df["diagnosis"].dropna().unique().tolist()),
                    format_func=zh_diagnosis,
                    key="pq_diag",
                )
            with c2:
                gender = st.multiselect("性别", ["female", "male"], key="pq_gender")
            with c3:
                age_min = st.number_input("年龄 ≥", 0, 99, 0, key="pq_age_lo")
                age_max = st.number_input("年龄 ≤", 0, 99, 99, key="pq_age_hi")
            with c4:
                hamd_min = st.number_input("HAMD ≥", 0.0, 60.0, 0.0, key="pq_hamd")
                hama_min = st.number_input("HAMA ≥", 0.0, 60.0, 0.0, key="pq_hama")
                phq9_min = st.number_input("PHQ9 ≥", 0.0, 30.0, 0.0, key="pq_phq9")
            
            c5, c6, c7 = st.columns(3)
            with c5:
                exclude_diag = st.multiselect(
                    "排除诊断",
                    options=sorted(df["diagnosis"].dropna().unique().tolist()),
                    format_func=zh_diagnosis,
                    key="pq_exc",
                )
            with c6:
                relapse_only = st.checkbox("仅复发患者", key="pq_relapse")
            with c7:
                page_size = st.number_input("每页条数", 5, 100, 20, key="pq_ps")
            
            tree = build_filter_tree(
                diagnosis, exclude_diag, gender, int(age_min), int(age_max),
                float(hamd_min), float(hama_min), float(phq9_min), relapse_only,
            )
            st.session_state["last_query_tree"] = tree
            
            active_filters = []
            if diagnosis:
                active_filters.append(f"诊断∈{','.join(zh_diagnosis(d) for d in diagnosis)}")
            if gender:
                active_filters.append(f"性别∈{','.join(gender)}")
            if age_min > 0 or age_max < 99:
                active_filters.append(f"年龄 {age_min}-{age_max}")
            if hamd_min > 0:
                active_filters.append(f"HAMD≥{hamd_min}")
            if relapse_only:
                active_filters.append("仅复发")
            st.info("当前筛选: " + ("；".join(active_filters) if active_filters else "全部患者"))
            
            b1, b2, b3 = st.columns(3)
            with b1:
                do_query = st.button("应用筛选并查询", key="btn_pq", type="primary")
            with b2:
                qname = st.text_input("保存名称", "我的筛选条件", key="pq_save_name")
                if st.button("保存条件", key="btn_save_query"):
                    r = api_post("/patient/query/save", {"query_name": qname, "condition_tree": tree})
                    if "error" not in r:
                        st.success(f"已保存 query_id={r.get('data', {}).get('query_id')}")
                    else:
                        st.error(r["error"])
            with b3:
                if st.button("导出 CSV", key="btn_export_query"):
                    try:
                        resp = httpx.post(
                            f"{API_BASE}/patient/query/export",
                            json={"condition_tree": tree, "format": "csv"},
                            headers=auth_headers(),
                            timeout=60,
                        )
                        if resp.status_code == 200:
                            st.download_button("下载 CSV", resp.content, file_name="patients_export.csv", mime="text/csv")
                        else:
                            st.error(resp.json().get("detail", f"HTTP {resp.status_code}"))
                    except Exception as e:
                        st.error(str(e))
            
            if do_query:
                result = api_post("/patient/query", {"condition_tree": tree, "page": 1, "page_size": int(page_size)})
                if "error" not in result:
                    patients = result.get("data", {}).get("patients", [])
                    total = result.get("data", {}).get("total", 0)
                    st.session_state["last_query_result"] = patients
                    st.session_state["clinical_cohort_ids"] = [p.get("patient_id") for p in patients if p.get("patient_id")]
                    if patients:
                        show = pd.DataFrame(patients)
                        if "diagnosis" in show.columns:
                            show["diagnosis"] = show["diagnosis"].map(zh_diagnosis)
                        st.dataframe(show, use_container_width=True, hide_index=True)
                    st.metric("匹配患者数", total)
                    diags = {}
                    for p in patients:
                        d = zh_diagnosis(p.get("diagnosis", ""))
                        diags[d] = diags.get(d, 0) + 1
                    summary = f"纳入 {total} 例；诊断分布 {diags}"
                    cache_report_section("研究对象基本信息", summary)
                    auto_cache_summary(summary)
                else:
                    st.error(result["error"])
            
            if st.session_state.get("last_query_result"):
                st.caption("提示：在下方其他模块可直接从患者目录多选，无需手打 ID。")
            
    with tab_saved:
        saved = api_get("/patient/query/list")
        rows = saved.get("data") or [] if "error" not in saved else []
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            qid = st.number_input("加载 query_id", min_value=1, value=int(rows[0]["id"]), key="pq_load_id")
            if st.button("加载并查询", key="btn_load_query"):
                detail = api_get(f"/patient/query/{int(qid)}")
                if "error" not in detail:
                    ct = (detail.get("data") or {}).get("condition_tree")
                    if ct:
                        result = api_post("/patient/query", {"condition_tree": ct, "page": 1, "page_size": 20})
                        if "error" not in result:
                            st.dataframe(pd.DataFrame(result.get("data", {}).get("patients", [])), use_container_width=True)
                else:
                    st.error(detail["error"])
        else:
            st.info("暂无已保存条件")


def render_abnormality():
    st.subheader("异常评估")
    tab_single, tab_batch, tab_compare = st.tabs(["单例评估", "批量评估", "人群对比"])

    with tab_single:
        with st.form("abnormality_form"):
            col1, col2, col3 = st.columns(3)
            with col1:
                hamd = st.number_input("HAMD", value=22.0)
                hama = st.number_input("HAMA", value=16.0)
            with col2:
                gender = st.selectbox("性别", ["female", "male", "all"])
                age = st.number_input("年龄", value=35)
            with col3:
                diag = st.selectbox("诊断", list(DIAG_EN.keys()), format_func=zh_diagnosis)
            if st.form_submit_button("评估"):
                body = {"indicators": {"HAMD_total": hamd, "HAMA_total": hama}, "age": age, "diagnosis": diag}
                if gender != "all":
                    body["gender"] = gender
                result = api_post("/reference/evaluate", body)
                if "error" not in result:
                    rows = []
                    for item in result.get("data", []):
                        rows.append({
                            "indicator": item.get("indicator"),
                            "value": item.get("value"),
                            "lower": item.get("lower"),
                            "upper": item.get("upper"),
                            "deviation_pct": item.get("deviation_pct"),
                            "abnormal": item.get("is_abnormal"),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    txt = "; ".join(f"{r['indicator']}={r['value']} abnormal={r['abnormal']}" for r in rows)
                    cache_report_section("HAMA量表分析", txt)
                    cache_report_section("数据质控结果", txt)
                else:
                    st.error(result["error"])

    with tab_batch:
        st.caption("从患者目录多选进行批量异常率统计（空选=全库抽样）。")
        pids = patient_multiselect(
            "ref_batch_ids",
            "选择患者（可多选，可不选）",
            default_ids=["P001", "P002", "P003", "P004", "P005"],
            api_post=api_post,
        )
        if st.button("批量异常率", key="btn_ref_batch", type="primary"):
            result = api_post("/reference/batch_evaluate", {
                "patient_ids": pids,
                "indicators": ["HAMD_total", "HAMA_total", "PHQ9_total"],
            })
            if "error" not in result:
                data = result.get("data", {})
                render_batch_abnormality(data)
                summary = str(data.get("summary") or data.get("abnormal_rates") or "")[:800]
                cache_report_section("数据质控结果", summary)
            else:
                st.error(result["error"])

    with tab_compare:
        st.caption("分别从目录选择两个队列进行横向对比。")
        cohort_a, cohort_b = cohort_pair_picker(api_post, "ref_cmp")
        indicator = st.selectbox("对比指标", ["HAMD_total", "HAMA_total", "PHQ9_total"], key="ref_cmp_ind")
        if st.button("横向对比", key="btn_ref_compare", type="primary"):
            if not cohort_a or not cohort_b:
                st.warning("请为队列 A 和队列 B 各选择至少一名患者")
            else:
                result = api_post("/reference/compare", {
                    "cohort_patient_ids": cohort_a,
                    "reference_cohort_ids": cohort_b,
                    "indicator": indicator,
                })
                if "error" not in result:
                    data = result.get("data", {})
                    render_reference_compare(data)
                    cache_report_section("治疗反应分析", summarize_reference_compare(data))
                else:
                    st.error(result["error"])


def render_followup():
    st.subheader("随访分析")
    tab_trend, tab_compare = st.tabs(["趋势曲线", "组间对比"])

    with tab_trend:
        pids = patient_multiselect(
            "fu_pids", "选择患者", default_ids=["P001", "P002"], api_post=api_post,
        )
        indicators = st.multiselect(
            "指标", ["HAMD_total", "HAMA_total", "PHQ9_total"],
            default=["HAMD_total", "HAMA_total"], key="fu_inds",
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            time_window = st.selectbox("时间窗", ["all", "baseline", "week4", "week8", "week12", "custom"], key="fu_tw")
        with c2:
            start_d = st.text_input("自定义起始", "2024-01-01", key="fu_start")
        with c3:
            end_d = st.text_input("自定义结束", "2024-12-31", key="fu_end")
        per_patient = st.checkbox("按患者分线展示", value=True, key="fu_per_patient")

        if st.button("查询趋势", key="btn_followup", type="primary"):
            if not pids:
                st.warning("请至少选择一名患者")
            else:
                body = {"patient_ids": pids, "indicators": indicators, "per_patient": per_patient}
                if time_window != "all":
                    body["time_window"] = time_window
                if time_window == "custom":
                    body["time_range"] = [start_d, end_d]
                result = api_post("/followup/trend", body)
                if "error" not in result:
                    data = result.get("data", {})
                    chart_data = data.get("chart") or {}
                    plot_followup_trend(chart_data, indicators)
                    trend_table = data.get("trend_table", [])
                    if trend_table:
                        st.dataframe(pd.DataFrame(trend_table), use_container_width=True, hide_index=True)
                    cache_report_section("治疗反应分析", summarize_followup_trend(data))
                else:
                    st.error(result["error"])

    with tab_compare:
        ga, gb = cohort_pair_picker(api_post, "fu_cmp")
        ind_c = st.multiselect("对比指标", ["HAMD_total", "PHQ9_total"], default=["HAMD_total"], key="fu_cmp_ind")
        if st.button("组间对比", key="btn_fu_compare", type="primary"):
            if not ga or not gb:
                st.warning("请为两组各选择患者")
            else:
                result = api_post("/followup/compare", {
                    "group_a": ga, "group_b": gb, "indicators": ind_c,
                })
                if "error" not in result:
                    render_followup_compare(result.get("data", {}))
                    cache_report_section("治疗反应分析", summarize_followup_compare(result.get("data", {})))
                else:
                    st.error(result["error"])


def render_risk_prediction():
    st.subheader("风险预测")
    sample_csv = _load_fixture_bytes("risk_training_sample.csv")
    if sample_csv:
        st.download_button(
            "下载示例训练 CSV",
            sample_csv,
            file_name="risk_training_sample.csv",
            mime="text/csv",
            key="dl_risk_sample",
        )

    tab1, tab2, tab3, tab4 = st.tabs(["训练模型", "单人预测", "批量预测", "模型评估"])

    models_resp = api_get("/risk/models")
    model_rows = (models_resp.get("data") or []) if "error" not in models_resp else []
    default_mid = int(model_rows[0]["id"]) if model_rows else 1
    model_options = {
        f"ID {m.get('id')} · {m.get('model_name', 'model')} · {m.get('task_type', '')}": int(m["id"])
        for m in model_rows if m.get("id") is not None
    }

    with tab1:
        with st.form("train_form"):
            task = st.selectbox("任务类型", ["relapse", "self_harm", "adverse_reaction"],
                                format_func=lambda x: {"relapse": "复发/再入院", "self_harm": "危机/自伤", "adverse_reaction": "用药不良反应"}[x])
            model_type = st.selectbox("模型类型", ["LogisticRegression", "RandomForest"])
            uploaded = st.file_uploader("上传训练数据 (xlsx/csv)", type=["xlsx", "csv"], key="risk_train_upload")
            features = st.text_input("特征列 (自动识别，可修改)", "", key="risk_features")
            target = st.text_input("目标列 (自动识别，可修改)", "", key="risk_target")
            if st.form_submit_button("训练"):
                if not uploaded:
                    st.warning("请上传训练数据文件")
                else:
                    try:
                        if uploaded.name.endswith(".csv"):
                            train_df = pd.read_csv(io.BytesIO(uploaded.getvalue()))
                        else:
                            train_df = pd.read_excel(io.BytesIO(uploaded.getvalue()))
                        auto_feat, auto_tgt = detect_feature_and_target_columns(train_df)
                        features_list = [f.strip() for f in (features or ",".join(auto_feat)).split(",") if f.strip()]
                        target_col = (target or auto_tgt).strip()
                        if not features_list:
                            st.error("未能识别特征列，请手动填写")
                        elif not target_col or target_col not in train_df.columns:
                            st.error(f"目标列 {target_col or '(空)'} 不在数据中")
                        else:
                            y = (train_df[target_col].astype(str).str.lower().isin(["1", "yes", "true", "y", "relapse"])).astype(int)
                            train_df = train_df.copy()
                            train_df[target_col] = y.values
                            body = {
                                "task_type": task,
                                "model_type": model_type,
                                "features": features_list,
                                "label": target_col,
                                "training_data": train_df[features_list + [target_col]].to_dict(orient="records"),
                            }
                            result = api_post("/risk/train", body)
                            if "error" not in result:
                                d = result.get("data", {})
                                st.success(f"训练完成! 模型ID: {d.get('model_id', 'N/A')}")
                                metrics = d.get("metrics", {})
                                if metrics:
                                    ca, cb, cc = st.columns(3)
                                    ca.metric("Accuracy", f"{metrics.get('accuracy', 0):.3f}" if metrics.get("accuracy") is not None else "N/A")
                                    cb.metric("AUC", f"{metrics.get('auc', 0):.3f}" if metrics.get("auc") is not None else "N/A")
                                    cc.metric("F1", f"{metrics.get('f1', 0):.3f}" if metrics.get("f1") is not None else "N/A")
                                cache_report_section("结论与建议", f"风险模型 ID {d.get('model_id')}, metrics={metrics}")
                            else:
                                st.error(result["error"])
                    except Exception as e:
                        st.error(f"训练失败: {e}")

    with tab2:
        st.markdown("#### 单人预测")
        df = ensure_patient_catalog(api_post)
        with st.form("predict_form"):
            if model_options:
                sel = st.selectbox("选择模型", list(model_options.keys()), key="risk_pred_model_sel")
                model_id = model_options[sel]
            else:
                model_id = st.number_input("模型ID", min_value=1, value=default_mid)
            if not df.empty:
                pid_options = df["patient_id"].tolist()
                patient_id = st.selectbox("选择患者", pid_options, index=0)
                row = df[df["patient_id"] == patient_id].iloc[0].to_dict()
                hamd = st.number_input("HAMD", value=float(row.get("HAMD_total") or 22))
                hama = st.number_input("HAMA", value=float(row.get("HAMA_total") or 15))
                phq9 = st.number_input("PHQ9", value=float(row.get("PHQ9_total") or 12))
                age = st.number_input("年龄", value=int(row.get("age") or 35))
            else:
                patient_id = st.text_input("患者ID", "P001")
                hamd = st.number_input("HAMD", value=22.0)
                hama = st.number_input("HAMA", value=15.0)
                phq9 = st.number_input("PHQ9", value=12.0)
                age = st.number_input("年龄", value=35)
            if st.form_submit_button("预测"):
                pdata = {"patient_id": patient_id, "HAMD_total": hamd, "HAMA_total": hama, "PHQ9_total": phq9, "age": age}
                result = api_post("/risk/predict", {"model_id": int(model_id), "patient_data": pdata})
                if "error" not in result:
                    d = result.get("data", {})
                    c1, c2 = st.columns(2)
                    c1.metric("Risk Score", f"{d.get('risk_score', 0):.3f}" if isinstance(d.get("risk_score"), (int, float)) else str(d.get("risk_score")))
                    c2.metric("Risk Level", str(d.get("risk_level", "")))
                    cache_report_section("结论与建议", f"患者 {patient_id} 风险评分 {d.get('risk_score')} 等级 {d.get('risk_level')}")
                else:
                    st.error(result["error"])

    with tab3:
        st.markdown("#### 批量预测")
        st.caption("对选定患者队列一次性计算风险评分与等级分布。")
        if model_options:
            sel_b = st.selectbox("选择模型", list(model_options.keys()), key="risk_batch_model_sel")
            batch_model_id = model_options[sel_b]
        else:
            batch_model_id = st.number_input("模型ID", min_value=1, value=default_mid, key="risk_batch_mid")
        batch_pids = patient_multiselect(
            "risk_batch_pids",
            "选择患者队列",
            default_ids=[f"P{i:03d}" for i in range(1, 11)],
            api_post=api_post,
        )
        if st.button("批量预测", key="btn_risk_batch", type="primary"):
            if not batch_pids:
                st.warning("请至少选择一名患者")
            else:
                result = api_post("/risk/batch_predict", {
                    "model_id": int(batch_model_id),
                    "cohort_patient_ids": batch_pids,
                })
                if "error" not in result:
                    data = result.get("data", {})
                    render_batch_risk_results(data)
                    summary = data.get("summary") or {}
                    cache_report_section(
                        "结论与建议",
                        f"批量风险预测 n={summary.get('total')} 分布={summary.get('risk_distribution')}",
                    )
                else:
                    st.error(result["error"])

    with tab4:
        st.markdown("#### 模型评估")
        st.caption("查看已训练模型的准确率、AUC、F1 等指标。")
        if model_options:
            sel_e = st.selectbox("选择模型", list(model_options.keys()), key="risk_eval_model_sel")
            eval_model_id = model_options[sel_e]
        else:
            eval_model_id = st.number_input("模型ID", min_value=1, value=default_mid, key="risk_eval_mid")
        if st.button("查看模型评估", key="btn_risk_eval", type="primary"):
            result = api_get(f"/risk/model/{int(eval_model_id)}/evaluate")
            if "error" not in result:
                data = result.get("data") or {}
                render_model_evaluation(data)
                metrics = data.get("stored_metrics") or {}
                m_txt = "、".join(
                    f"{k}={float(v):.3f}" if isinstance(v, (int, float)) else f"{k}={v}"
                    for k, v in metrics.items()
                )
                cache_report_section("结论与建议", f"模型 {eval_model_id} 评估：{m_txt or '已完成'}")
            else:
                st.error(result["error"])
        if model_rows:
            st.dataframe(
                pd.DataFrame([{
                    "id": m.get("id"),
                    "name": m.get("model_name"),
                    "task": m.get("task_type"),
                    "type": m.get("model_type"),
                    "created": m.get("created_at"),
                } for m in model_rows]),
                use_container_width=True,
                hide_index=True,
            )


def render_comorbidity():
    st.subheader("共病分析")
    st.caption("请从患者目录多选（建议至少 10 人、含多种诊断，如 P001–P020）。")

    default_ids = [f"P{i:03d}" for i in range(1, 21)]
    ids = patient_multiselect("comorb_ids", "选择患者队列", default_ids=default_ids, api_post=api_post)
    analysis_type = st.radio(
        "分析类型",
        ["共病矩阵", "谱系分析", "聚类", "热图", "网络图"],
        horizontal=True,
        key="comorb_type",
    )
    primary_diag = detect_primary_diagnosis(ensure_patient_catalog(api_post), ids)
    if analysis_type == "谱系分析":
        df_c = ensure_patient_catalog(api_post)
        diag_options = sorted(df_c["diagnosis"].dropna().unique().tolist()) if not df_c.empty else list(DIAG_EN.keys())
        default_idx = diag_options.index(primary_diag) if primary_diag in diag_options else 0
        primary_diag = st.selectbox(
            "主诊断（谱系分析锚点，默认取所选队列众数）",
            diag_options,
            format_func=zh_diagnosis,
            index=default_idx,
            key="comorb_primary_diag",
        )
        st.caption("谱系分析会结合主诊断字段与量表阈值推断的伴发症状信号（如抑郁+HAMA≥15→焦虑信号）。")

    if st.button("计算", key="btn_comorbidity", type="primary"):
        if not ids:
            st.warning("请至少选择一名患者")
        elif analysis_type == "聚类" and len(ids) < 10:
            st.warning("聚类至少需要 10 名患者，请扩大选择范围（如 P001–P020）")
        else:
            endpoint_map = {
                "共病矩阵": "/comorbidity/matrix",
                "谱系分析": "/comorbidity/spectrum",
                "聚类": "/comorbidity/cluster",
                "热图": "/comorbidity/heatmap",
                "网络图": "/comorbidity/network",
            }
            body = {"cohort_ids": ids}
            if analysis_type == "谱系分析":
                body["primary_diagnosis"] = primary_diag
            result = api_post(endpoint_map[analysis_type], body)
            if "error" not in result:
                data = result.get("data", {})
                if analysis_type in ("共病矩阵",):
                    matrix = data.get("matrix") or data.get("frequency_matrix")
                    if matrix:
                        plot_matrix_heatmap(matrix)
                    else:
                        st.dataframe(pd.DataFrame(data), use_container_width=True)
                elif analysis_type == "热图":
                    if data.get("data"):
                        plot_heatmap_payload(data)
                    else:
                        matrix = data.get("matrix") or data.get("frequency_matrix")
                        if matrix:
                            plot_matrix_heatmap(matrix, "Comorbidity Heatmap")
                elif analysis_type == "网络图":
                    plot_network_graph(data)
                elif analysis_type == "谱系分析":
                    render_spectrum_result(data)
                elif analysis_type == "聚类":
                    render_cluster_result(data)
                cache_report_section("抑郁-焦虑共病分析", summarize_analysis_for_report(analysis_type, data))
            else:
                st.error(result["error"])


def render_report():
    st.subheader("图文报告")
    session_id = require_session()
    if not session_id:
        return

    templates_resp = api_get("/template/list")
    templates = (templates_resp.get("data") or []) if "error" not in templates_resp else []
    template_options = {
        f"{t.get('template_name', '未命名模板')} · ID {t.get('id')}": int(t.get("id"))
        for t in templates if t.get("id") is not None
    }

    cache = st.session_state.get("clinical_report_cache") or {}
    if cache:
        with st.expander(f"已缓存 {len(cache)} 段临床分析结果（将写入报告）", expanded=False):
            for k, v in cache.items():
                st.markdown(f"**{k}**")
                st.caption(str(v)[:300])

    with st.form("report_form"):
        if template_options:
            selected_template = st.selectbox("报告模板", list(template_options.keys()))
            template_id = template_options[selected_template]
        else:
            template_id = st.number_input("模板ID", min_value=1, value=1)
        auto = st.checkbox("自动汇总临床模块数据", value=True)
        use_llm = st.checkbox("大模型润色报告正文（需配置 API_KEY）", value=True)
        st.caption("在仓库根目录 `.env` 配置 API_KEY、OPENAI_COMPATIBLE_API_BASE、CLINICAL_REPORT_MODEL；未配置时使用规则模板摘要。")
        if st.form_submit_button("生成报告"):
            payload = {
                "session_id": session_id,
                "template_id": int(template_id),
                "auto_aggregate": auto,
                "use_llm": use_llm,
                "cohort_patient_ids": st.session_state.get("clinical_cohort_ids") or [],
                "analysis_results": dict(cache),
            }
            result = api_post("/report/build", payload)
            if "error" not in result:
                d = result.get("data", {})
                st.session_state["last_report"] = d
                st.success(f"报告已生成: {d.get('report_name', 'N/A')}")
            else:
                st.error(result["error"])

    d = st.session_state.get("last_report")
    if d:
        html_content = d.get("html_content")
        if html_content:
            st.components.v1.html(html_content, height=600, scrolling=True)
        rid = d.get("report_id")
        if rid:
            for fmt in ("html", "pdf"):
                if st.button(f"下载 {fmt.upper()}", key=f"dl_report_{fmt}"):
                    resp = httpx.get(
                        f"{API_BASE}/report/{rid}/export",
                        params={"format": fmt},
                        headers=auth_headers(),
                        timeout=60,
                    )
                    if resp.status_code == 200:
                        mime = "application/pdf" if fmt == "pdf" else "text/html"
                        st.download_button(
                            f"保存 {fmt.upper()}",
                            resp.content,
                            file_name=f"report_{rid}.{fmt}",
                            mime=mime,
                            key=f"save_report_{fmt}",
                        )
                    else:
                        st.error(resp.text[:200])


def render_correlation():
    st.subheader("相关性分析")
    sample_csv = _load_fixture_bytes("correlation_clinical_sample.csv")
    if sample_csv:
        st.download_button(
            "下载示例相关分析 CSV",
            sample_csv,
            file_name="correlation_clinical_sample.csv",
            mime="text/csv",
            key="dl_corr_sample",
        )

    tab_select, tab_partial, tab_file = st.tabs(["从患者库选取", "偏相关（控制变量）", "上传文件"])

    with tab_select:
        df = ensure_patient_catalog(api_post)
        if df.empty:
            st.warning("患者目录为空")
        else:
            pids = patient_multiselect(
                "corr_pids", "选择患者（建议 ≥5 人）",
                default_ids=[f"P{i:03d}" for i in range(1, 11)],
                api_post=api_post,
            )
            columns = st.multiselect(
                "指标列",
                ["HAMD_total", "HAMA_total", "PHQ9_total", "age"],
                default=["HAMD_total", "HAMA_total", "PHQ9_total"],
                key="corr_cols",
            )
            method = st.selectbox("方法", ["pearson", "spearman", "kendall"], key="corr_method")
            if st.button("计算相关性", key="btn_corr_select", type="primary"):
                if len(pids) < 3:
                    st.warning("至少选择 3 名患者")
                elif len(columns) < 2:
                    st.warning("至少选择 2 个指标")
                else:
                    sub = df[df["patient_id"].isin(pids)][["patient_id"] + columns].dropna()
                    data_rows = sub[columns].to_dict(orient="records")
                    result = api_post("/correlation/compute", {"data": data_rows, "columns": columns, "method": method})
                    if "error" not in result:
                        render_correlation_result(result.get("data", {}))
                        cache_report_section("抑郁-焦虑共病分析", f"相关分析 {method} n={len(data_rows)}")
                    else:
                        st.error(result["error"])

    with tab_partial:
        df = ensure_patient_catalog(api_post)
        if df.empty:
            st.warning("患者目录为空")
        else:
            pids_p = patient_multiselect(
                "corr_partial_pids", "选择患者（≥5 人）",
                default_ids=[f"P{i:03d}" for i in range(1, 11)],
                api_post=api_post,
            )
            avail = ["HAMD_total", "HAMA_total", "PHQ9_total", "age"]
            cols_p = st.multiselect("分析指标（≥2）", avail, default=["HAMD_total", "PHQ9_total"], key="corr_partial_cols")
            ctrl_p = st.multiselect("控制变量（≥1）", avail, default=["age"], key="corr_partial_ctrl")
            st.caption("偏相关：在控制混杂变量（如年龄）后，看两指标是否仍相关。")
            if st.button("计算偏相关", key="btn_corr_partial", type="primary"):
                if len(pids_p) < 5:
                    st.warning("偏相关至少需要 5 名患者")
                elif len(cols_p) < 2:
                    st.warning("至少选择 2 个分析指标")
                elif len(ctrl_p) < 1:
                    st.warning("至少选择 1 个控制变量")
                elif set(ctrl_p) & set(cols_p):
                    st.warning("控制变量不能与分析指标重复")
                else:
                    use_cols = list(dict.fromkeys(cols_p + ctrl_p))
                    sub = df[df["patient_id"].isin(pids_p)][["patient_id"] + use_cols].dropna()
                    data_rows = sub[use_cols].to_dict(orient="records")
                    result = api_post("/correlation/partial", {
                        "data": data_rows,
                        "columns": cols_p,
                        "control_vars": ctrl_p,
                    })
                    if "error" not in result:
                        render_partial_correlation_result(result.get("data", {}))
                        cache_report_section(
                            "抑郁-焦虑共病分析",
                            f"偏相关 控制={ctrl_p} n={len(data_rows)}",
                        )
                    else:
                        st.error(result["error"])

    with tab_file:
        uploaded = st.file_uploader("上传数据文件 (xlsx/csv)", type=["xlsx", "csv"], key="corr_upload")
        method_f = st.selectbox("方法", ["pearson", "spearman", "kendall"], key="corr_method_f")
        if st.button("从文件计算", key="btn_corr_file", type="primary"):
            if not uploaded:
                st.warning("请上传数据文件")
            else:
                try:
                    if uploaded.name.endswith(".csv"):
                        file_df = pd.read_csv(io.BytesIO(uploaded.getvalue()))
                    else:
                        file_df = pd.read_excel(io.BytesIO(uploaded.getvalue()))
                    num_cols = file_df.select_dtypes(include=["number"]).columns.tolist()[:8]
                    if len(num_cols) < 2:
                        st.error("文件中数值列不足")
                    else:
                        data_rows = file_df[num_cols].dropna().to_dict(orient="records")
                        result = api_post("/correlation/compute", {"data": data_rows, "columns": num_cols, "method": method_f})
                        if "error" not in result:
                            render_correlation_result(result.get("data", {}))
                        else:
                            st.error(result["error"])
                except Exception as e:
                    st.error(f"处理文件失败: {e}")


def render_clinical_support_page() -> None:
    st.title("临床支持模块")
    tab_names = ["检索纳排", "异常评估", "随访分析", "风险预测", "共病分析", "图文报告", "相关性分析"]
    tabs = st.tabs(tab_names)
    with tabs[0]:
        render_patient_query()
    with tabs[1]:
        render_abnormality()
    with tabs[2]:
        render_followup()
    with tabs[3]:
        render_risk_prediction()
    with tabs[4]:
        render_comorbidity()
    with tabs[5]:
        render_report()
    with tabs[6]:
        render_correlation()


if __name__ == "__main__":
    st.set_page_config(page_title="临床支持", page_icon="🏥", layout="wide")
    render_auth_sidebar()
    st.title("临床支持模块")
    tab_names = ["检索纳排", "异常评估", "随访分析", "风险预测", "共病分析", "图文报告", "相关性分析"]
    tabs = st.tabs(tab_names)
    with tabs[0]:
        render_patient_query()
    with tabs[1]:
        render_abnormality()
    with tabs[2]:
        render_followup()
    with tabs[3]:
        render_risk_prediction()
    with tabs[4]:
        render_comorbidity()
    with tabs[5]:
        render_report()
    with tabs[6]:
        render_correlation()
