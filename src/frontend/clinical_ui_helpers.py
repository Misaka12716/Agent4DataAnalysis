# frontend/clinical_ui_helpers.py — shared UI helpers for clinical_support page

from __future__ import annotations

import io
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# 患者主表可用于风险模型训练/批量预测的特征（不含 patient_id）
PATIENT_NUMERIC_FEATURES = [
    "age", "HAMD_total", "HAMA_total", "PHQ9_total", "disease_duration_years", "relapse",
]


def setup_matplotlib_cjk() -> None:
    """配置 matplotlib 中文字体，避免共病图/热图/网络图中文乱码。"""
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    if getattr(setup_matplotlib_cjk, "_done", False):
        return
    candidates = ["Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC", "WenQuanYi Micro Hei"]
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in installed:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            setup_matplotlib_cjk._done = True
            return
    plt.rcParams["axes.unicode_minus"] = False
    setup_matplotlib_cjk._done = True

DIAG_EN = {
    "depression": "Depression",
    "anxiety": "Anxiety",
    "schizophrenia": "Schizophrenia",
    "sleep_disorder": "Sleep Disorder",
    "child_adolescent": "Child/Adolescent",
}

DIAG_ZH = {
    "depression": "抑郁",
    "anxiety": "焦虑",
    "schizophrenia": "精神分裂症",
    "sleep_disorder": "睡眠障碍",
    "child_adolescent": "儿童青少年精神障碍",
}

GENDER_EN = {"female": "Female", "male": "Male", "F": "Female", "M": "Male"}
GENDER_ZH = {"female": "女", "male": "男", "F": "女", "M": "男"}


def zh_diagnosis(label: str) -> str:
    key = str(label or "").strip()
    return DIAG_ZH.get(key, key or "未知")


def en_diagnosis(label: str) -> str:
    return DIAG_EN.get(str(label or "").strip(), str(label or "Unknown"))


def zh_gender(label: str) -> str:
    return GENDER_ZH.get(str(label or "").strip(), str(label or ""))


def detect_primary_diagnosis(df: pd.DataFrame, patient_ids: Optional[List[str]] = None) -> str:
    """根据所选队列的主诊断字段众数推断谱系分析锚点。"""
    if df.empty:
        return "depression"
    sub = df[df["patient_id"].isin(patient_ids)] if patient_ids else df
    if sub.empty or "diagnosis" not in sub.columns:
        return "depression"
    counts = sub["diagnosis"].dropna().value_counts()
    return str(counts.index[0]) if len(counts) else "depression"


def ensure_patient_catalog(api_post) -> pd.DataFrame:
    if st.session_state.get("patient_catalog") is not None:
        return st.session_state["patient_catalog"]
    resp = api_post(
        "/patient/query",
        {
            "condition_tree": {
                "operator": "AND",
                "conditions": [{"field": "patient_id", "op": "LIKE", "value": "%"}],
            },
            "page": 1,
            "page_size": 500,
        },
    )
    rows = (resp.get("data") or {}).get("patients") or [] if "error" not in resp else []
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["patient_id"])
    if not df.empty and "diagnosis" in df.columns:
        df["diagnosis_zh"] = df["diagnosis"].map(zh_diagnosis)
    st.session_state["patient_catalog"] = df
    return df


def invalidate_patient_catalog() -> None:
    st.session_state.pop("patient_catalog", None)


def _sanitize_cell(value: Any) -> Any:
    """Excel/CSV 解析后的单元格转 JSON 可序列化值（尤其 visit_date 等日期列）。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def sanitize_table_rows(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        out.append({str(k): _sanitize_cell(v) for k, v in raw.items()})
    return out


def parse_uploaded_table(uploaded) -> List[dict]:
    if not uploaded:
        return []
    raw = uploaded.getvalue()
    if uploaded.name.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(raw))
    else:
        df = pd.read_excel(io.BytesIO(raw))
    return sanitize_table_rows(df.to_dict(orient="records"))


def patient_label(row: dict) -> str:
    pid = row.get("patient_id", "?")
    diag = zh_diagnosis(row.get("diagnosis", ""))
    gender = zh_gender(str(row.get("gender", "")))
    age = row.get("age", "")
    hamd = row.get("HAMD_total", "")
    return f"{pid} | {diag} | {gender} | {age}岁 | HAMD {hamd}"


def patient_multiselect(
    key: str,
    label: str,
    default_ids: Optional[List[str]] = None,
    api_post=None,
    max_selections: Optional[int] = None,
) -> List[str]:
    df = ensure_patient_catalog(api_post)
    if df.empty:
        st.warning("患者目录为空，请先在「患者检索与纳排 → 数据导入」上传患者 CSV/Excel。")
        return []
    options = {patient_label(r.to_dict()): r["patient_id"] for _, r in df.iterrows()}
    inv = {v: k for k, v in options.items()}
    defaults = [inv[pid] for pid in (default_ids or []) if pid in inv]
    picked_labels = st.multiselect(label, list(options.keys()), default=defaults, key=key)
    ids = [options[l] for l in picked_labels]
    if max_selections and len(ids) > max_selections:
        st.caption(f"已选 {len(ids)} 人（建议不超过 {max_selections}）")
    return ids


def cohort_pair_picker(api_post, key_prefix: str = "cohort") -> Tuple[List[str], List[str]]:
    df = ensure_patient_catalog(api_post)
    if df.empty:
        return [], []
    diag_counts = df["diagnosis"].dropna().value_counts() if "diagnosis" in df.columns else pd.Series(dtype=int)
    top_diags = list(diag_counts.index[:2])
    group_a_default = df[df["diagnosis"] == top_diags[0]]["patient_id"].tolist()[:5] if top_diags else df["patient_id"].tolist()[:5]
    second = top_diags[1] if len(top_diags) > 1 else None
    group_b_default = (
        df[df["diagnosis"] == second]["patient_id"].tolist()[:5]
        if second
        else df["patient_id"].tolist()[5:10]
    )
    c1, c2 = st.columns(2)
    with c1:
        cap_a = zh_diagnosis(top_diags[0]) if top_diags else "队列"
        group_a = patient_multiselect(
            f"{key_prefix}_a",
            f"队列 A（可多选，默认{cap_a}）",
            default_ids=group_a_default,
            api_post=api_post,
        )
    with c2:
        cap_b = zh_diagnosis(second) if second else "对照"
        group_b = patient_multiselect(
            f"{key_prefix}_b",
            f"队列 B（可多选，默认{cap_b}）",
            default_ids=group_b_default,
            api_post=api_post,
        )
    return group_a, group_b


def build_filter_tree(
    diagnosis: List[str],
    exclude_diag: List[str],
    gender: List[str],
    age_min: int,
    age_max: int,
    hamd_min: float,
    hama_min: float,
    phq9_min: float,
    relapse_only: bool,
) -> dict:
    conds: List[dict] = []
    if diagnosis:
        conds.append({"field": "diagnosis", "op": "IN", "value": diagnosis})
    if gender:
        conds.append({"field": "gender", "op": "IN", "value": gender})
    if age_min > 0:
        conds.append({"field": "age", "op": ">=", "value": age_min})
    if age_max < 99:
        conds.append({"field": "age", "op": "<=", "value": age_max})
    if hamd_min > 0:
        conds.append({"field": "HAMD_total", "op": ">=", "value": hamd_min})
    if hama_min > 0:
        conds.append({"field": "HAMA_total", "op": ">=", "value": hama_min})
    if phq9_min > 0:
        conds.append({"field": "PHQ9_total", "op": ">=", "value": phq9_min})
    if relapse_only:
        conds.append({"field": "relapse", "op": "=", "value": 1})
    tree = {"operator": "AND", "conditions": conds} if conds else {
        "operator": "AND",
        "conditions": [{"field": "patient_id", "op": "LIKE", "value": "%"}],
    }
    if exclude_diag:
        tree = {
            "operator": "AND",
            "conditions": [
                tree,
                {"operator": "NOT", "conditions": [{"field": "diagnosis", "op": "IN", "value": exclude_diag}]},
            ],
        }
    return tree


def cache_report_section(title: str, content: str) -> None:
    cache = st.session_state.setdefault("clinical_report_cache", {})
    cache[title] = content[:4000]


def cache_report_from_keywords(content_map: Dict[str, str]) -> None:
    for title, text in content_map.items():
        cache_report_section(title, text)


def guess_report_titles(text: str) -> List[str]:
    """Map summary text to likely template section titles."""
    keys = []
    if "患者" in text or "纳入" in text or "队列" in text:
        keys.extend(["研究对象基本信息", "研究对象信息"])
    if "异常" in text or "质控" in text:
        keys.append("数据质控结果")
    if "HAMA" in text or "HAMD" in text or "量表" in text:
        keys.append("HAMA量表分析")
    if "随访" in text or "趋势" in text or "治疗" in text:
        keys.extend(["治疗反应分析", "症状量表与评估"])
    if "共病" in text or "矩阵" in text or "聚类" in text:
        keys.append("抑郁-焦虑共病分析")
    if "风险" in text or "预测" in text:
        keys.extend(["风险评估结论", "结论与建议"])
    if "相关" in text:
        keys.append("抑郁-焦虑共病分析")
    return keys


def auto_cache_summary(summary: str) -> None:
    for title in guess_report_titles(summary):
        cache_report_section(title, summary)


def detect_feature_and_target_columns(df: pd.DataFrame) -> Tuple[List[str], str]:
    numeric = df.select_dtypes(include=["number"]).columns.tolist()
    boolish = []
    for col in df.columns:
        if col in numeric:
            continue
        sample = df[col].dropna().astype(str).str.lower().head(20)
        if len(sample) and sample.isin(["0", "1", "yes", "no", "true", "false", "y", "n"]).mean() > 0.6:
            boolish.append(col)
    target_candidates = [c for c in boolish if c.lower() in ("relapse", "outcome", "target", "label", "event")]
    if not target_candidates:
        target_candidates = [c for c in df.columns if c.lower() in ("relapse", "outcome", "target", "label")]
    target = target_candidates[0] if target_candidates else (boolish[0] if boolish else "")
    feature_pool = [c for c in numeric if c != target]
    preferred = [c for c in PATIENT_NUMERIC_FEATURES if c in feature_pool]
    features = preferred + [c for c in feature_pool if c not in preferred]
    return features[:8], target


def _normalize_abnormal_rate(value: Any) -> float:
    """后端 abnormal_rates 为 0–100 百分比；兼容旧版 0–1 小数。"""
    v = float(value)
    return v if v > 1 else v * 100


def render_batch_abnormality(data: dict) -> None:
    rates = data.get("abnormal_rates") or {}
    details = data.get("details") or []
    note = data.get("interpretation_note")
    if note:
        st.info(note)
    if rates:
        rate_df = pd.DataFrame([
            {"指标": k, "异常率": f"{_normalize_abnormal_rate(v):.1f}%"}
            for k, v in rates.items()
        ])
        st.dataframe(rate_df, use_container_width=True, hide_index=True)
        try:
            numeric_rates = {k: _normalize_abnormal_rate(v) / 100 for k, v in rates.items()}
            st.bar_chart(pd.DataFrame({"异常率": numeric_rates}))
        except Exception:
            pass
    if details:
        flat = []
        for item in details[:30]:
            pid = item.get("patient_id")
            for r in item.get("results") or []:
                flat.append({
                    "patient_id": pid,
                    "indicator": r.get("indicator"),
                    "value": r.get("value"),
                    "abnormal": r.get("is_abnormal"),
                    "lower": r.get("lower"),
                    "upper": r.get("upper"),
                })
        if flat:
            st.dataframe(pd.DataFrame(flat), use_container_width=True, hide_index=True)


def render_batch_risk_results(data: dict) -> None:
    summary = data.get("summary") or {}
    preds = data.get("predictions") or []
    warn = data.get("feature_warnings") or {}
    if warn.get("missing_in_patient_table"):
        st.warning(
            f"模型特征 {warn['missing_in_patient_table']} 不在患者主表中，"
            f"批量预测已用 {warn.get('imputed_value', 0)} 填补。{warn.get('note', '')}"
        )
    if summary:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Predicted", summary.get("total", len(preds)))
        dist = summary.get("risk_distribution") or {}
        if dist:
            c2.metric("High/Critical", dist.get("high", 0) + dist.get("critical", 0))
            try:
                st.bar_chart(pd.DataFrame({"count": dist}))
            except Exception:
                pass
    rows = []
    for item in preds:
        pred = item.get("prediction") or {}
        rows.append({
            "patient_id": item.get("patient_id"),
            "risk_score": pred.get("risk_score"),
            "risk_level": pred.get("risk_level"),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("无批量预测结果")


def render_model_evaluation(data: dict) -> None:
    metrics = data.get("stored_metrics") or {}
    if metrics:
        cols = st.columns(min(4, len(metrics)))
        for i, (k, v) in enumerate(metrics.items()):
            if isinstance(v, (int, float)):
                cols[i % len(cols)].metric(str(k), f"{float(v):.3f}")
            else:
                cols[i % len(cols)].metric(str(k), str(v))
        st.dataframe(pd.DataFrame([metrics]), use_container_width=True, hide_index=True)
    roc = data.get("roc_curve")
    if roc and roc.get("fpr") and roc.get("tpr"):
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(5, 4))
        ax.plot(roc["fpr"], roc["tpr"], marker=".", label="ROC")
        ax.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Chance")
        ax.set_xlabel("False Positive Rate")
        ax.set_ylabel("True Positive Rate")
        ax.set_title("ROC Curve")
        ax.legend()
        ax.grid(True, alpha=0.3)
        st.pyplot(fig)
        plt.close(fig)
    cm = data.get("confusion_matrix")
    if cm:
        st.markdown("**Confusion Matrix**")
        st.dataframe(pd.DataFrame(cm), use_container_width=True, hide_index=True)
    status = data.get("validation_status") or {}
    if status:
        st.caption(
            f"External validation: {status.get('external_validation')} | "
            f"Calibration reported: {status.get('calibration_reported')} | "
            f"Use: {status.get('intended_use')}"
        )


def render_partial_correlation_result(data: dict) -> None:
    controls = data.get("control_vars") or []
    if controls:
        st.caption(f"Partial correlation controlling for: {', '.join(controls)}")
    payload = {**data, "method": "partial"}
    render_correlation_result(payload)


def render_reference_compare(data: dict) -> None:
    rows = data.get("comparison") or data.get("indicators") or []
    if isinstance(rows, dict):
        rows = [rows]
    flat = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        flat.append({
            "Indicator": row.get("indicator"),
            "Cohort Mean": row.get("cohort_mean"),
            "Reference Mean": row.get("reference_mean"),
            "Cohort N": row.get("cohort_n"),
            "Reference N": row.get("reference_n"),
            "Diff": row.get("mean_difference") or row.get("diff"),
            "p-value": row.get("p_value"),
            "Significant": row.get("significant"),
            "Effect Size": row.get("effect_size") or row.get("cohens_d"),
        })
    if flat:
        st.dataframe(pd.DataFrame(flat), use_container_width=True, hide_index=True)
    else:
        st.info("无对比结果")


def render_followup_compare(data: dict) -> None:
    comp = data.get("comparison") or []
    flat = []
    for row in comp:
        tp = row.get("time_point")
        for k, v in row.items():
            if k == "time_point" or not isinstance(v, dict):
                continue
            flat.append({
                "time_point": tp,
                "indicator": k,
                "group_a_mean": v.get("group_a_mean"),
                "group_b_mean": v.get("group_b_mean"),
                "group_a_n": v.get("group_a_n"),
                "group_b_n": v.get("group_b_n"),
                "p_value": v.get("p_value"),
                "significant": v.get("significant"),
            })
    if flat:
        st.dataframe(pd.DataFrame(flat), use_container_width=True, hide_index=True)
        try:
            chart_df = pd.DataFrame(flat).pivot_table(
                index="time_point", columns="indicator", values=["group_a_mean", "group_b_mean"]
            )
            st.line_chart(chart_df, use_container_width=True)
        except Exception:
            pass
    else:
        st.info("无组间对比数据")


def plot_followup_trend(chart_data: dict, indicators: List[str]) -> None:
    import matplotlib.pyplot as plt

    setup_matplotlib_cjk()
    x_labels = chart_data.get("x") or []
    series = chart_data.get("series") or {}
    if not x_labels or not series:
        st.info("无趋势数据")
        return
    fig, ax = plt.subplots(figsize=(9, 4))
    for name, values in series.items():
        ys = [float(v) if v is not None else float("nan") for v in values]
        ax.plot(range(len(x_labels)), ys, marker="o", label=str(name))
    ax.set_xticks(range(len(x_labels)))
    ax.set_xticklabels(x_labels, rotation=30, ha="right")
    ax.set_xlabel("Visit Date")
    ax.set_ylabel("Score")
    ax.set_title("Follow-up Trend")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    st.pyplot(fig)
    plt.close(fig)


def plot_matrix_heatmap(matrix: dict, title: str = "共病矩阵") -> None:
    import matplotlib.pyplot as plt
    import seaborn as sns

    setup_matplotlib_cjk()
    labels = [zh_diagnosis(k) for k in matrix.keys()]
    raw_labels = list(matrix.keys())
    arr = [[matrix[r].get(c, 0) for c in raw_labels] for r in raw_labels]
    fig, ax = plt.subplots(figsize=(7, 5))
    sns.heatmap(arr, annot=True, fmt=".1f", xticklabels=labels, yticklabels=labels, cmap="YlOrRd", ax=ax)
    ax.set_title(title)
    st.pyplot(fig)
    plt.close(fig)


def plot_heatmap_payload(data: dict) -> None:
    labels = [zh_diagnosis(x) for x in (data.get("labels") or data.get("x_axis") or [])]
    raw = data.get("labels") or data.get("x_axis") or []
    points = data.get("data") or []
    if not labels or not points:
        st.info("热图数据为空")
        return
    n = len(labels)
    import numpy as np
    import matplotlib.pyplot as plt
    import seaborn as sns

    setup_matplotlib_cjk()
    arr = np.zeros((n, n))
    for x, y, v in points:
        if 0 <= y < n and 0 <= x < n:
            arr[y][x] = v
    fig, ax = plt.subplots(figsize=(7, 5))
    sns.heatmap(arr, annot=True, fmt=".1f", xticklabels=labels, yticklabels=labels, cmap="YlOrRd", ax=ax)
    ax.set_title("共病热图")
    st.pyplot(fig)
    plt.close(fig)


def plot_network_graph(data: dict) -> None:
    import matplotlib.pyplot as plt
    import networkx as nx

    setup_matplotlib_cjk()
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    if not nodes or not edges:
        st.info("网络边为空，请扩大患者范围（建议 P001–P020）")
        return
    G = nx.Graph()
    for node in nodes:
        nid = node.get("id") or node.get("name")
        G.add_node(nid, label=zh_diagnosis(node.get("label") or nid))
    for edge in edges:
        G.add_edge(edge.get("source"), edge.get("target"), weight=float(edge.get("weight") or 1))
    labels = {n: G.nodes[n].get("label", zh_diagnosis(n)) for n in G.nodes}
    fig, ax = plt.subplots(figsize=(8, 6))
    if len(G.nodes) <= 6:
        pos = nx.circular_layout(G)
    else:
        pos = nx.spring_layout(G, seed=42, k=1.5)
    weights = [G[u][v].get("weight", 1) * 2 for u, v in G.edges]
    nx.draw_networkx_nodes(G, pos, node_color="#A8D5FF", node_size=1200, ax=ax)
    nx.draw_networkx_edges(G, pos, width=weights, edge_color="#888888", ax=ax)
    font_family = plt.rcParams.get("font.sans-serif", ["sans-serif"])[0]
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=9, font_family=font_family, ax=ax)
    ax.set_title("共病网络")
    ax.axis("off")
    st.pyplot(fig)
    plt.close(fig)


def render_spectrum_result(data: dict) -> None:
    rels = data.get("relationships") or data.get("pairs") or data.get("spectrum_pairs") or []
    primary = data.get("primary_diagnosis", "")
    if primary:
        st.markdown(f"**主诊断：** {zh_diagnosis(primary)}")
    note = data.get("inference_note")
    if note:
        st.caption(note)

    summary = data.get("summary") or {}
    if summary:
        c1, c2, c3 = st.columns(3)
        c1.metric("主诊断人数", summary.get("primary_n", summary.get("primary_count", "—")))
        c2.metric("伴发信号类型", summary.get("comorbid_types", summary.get("comorbid_signals", len(rels))))
        c3.metric("显著关联对", summary.get("significant_pairs", "—"))

    if not rels:
        st.info("当前队列未检出与其他诊断/症状信号的共现关系，请扩大患者范围或更换主诊断。")
        return

    rows = []
    for r in rels:
        rows.append({
            "伴发信号": zh_diagnosis(r.get("comorbid_diagnosis", "")),
            "共现人数": r.get("n_with_both", r.get("co_occurring_count")),
            "相对风险 RR": r.get("relative_risk"),
            "比值比 OR": r.get("odds_ratio"),
            "p 值": r.get("p_value"),
            "显著": "是" if r.get("significant") else "否",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    try:
        rr_map = {
            zh_diagnosis(r.get("comorbid_diagnosis", "")): float(r["relative_risk"])
            for r in rels
            if r.get("relative_risk") is not None
        }
        if rr_map:
            st.bar_chart(pd.DataFrame({"RR": rr_map}))
    except Exception:
        pass


def render_cluster_result(data: dict) -> None:
    clusters = data.get("clusters") or []
    rows = []
    for c in clusters:
        dist = c.get("diagnosis_distribution") or {}
        dist_txt = "、".join(f"{zh_diagnosis(k)} {v}例" for k, v in dist.items())
        rows.append({
            "聚类": c.get("cluster_id"),
            "人数": c.get("count", len(c.get("patients") or [])),
            "HAMD 均值": c.get("mean_hamd"),
            "诊断构成": dist_txt or "—",
            "示例患者": ", ".join((c.get("patients") or [])[:5]),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("无聚类结果")


def summarize_analysis_for_report(analysis_type: str, data: dict) -> str:
    """将分析结果转为报告缓存用的自然语言摘要（非 JSON）。"""
    if analysis_type == "谱系分析":
        primary = zh_diagnosis(data.get("primary_diagnosis", ""))
        rels = data.get("relationships") or data.get("pairs") or []
        parts = [f"谱系分析（主诊断 {primary}，n={data.get('total_patients', 'N/A')}）"]
        for r in rels[:5]:
            other = zh_diagnosis(r.get("comorbid_diagnosis", ""))
            rr, pv = r.get("relative_risk"), r.get("p_value")
            if rr is not None:
                parts.append(f"与{other}共现 {r.get('n_with_both', r.get('co_occurring_count', 0))} 例，RR={rr}，p={pv}")
        return "；".join(parts)
    if analysis_type in ("共病矩阵", "热图"):
        diags = [zh_diagnosis(d) for d in (data.get("diagnoses") or [])]
        return f"共病矩阵 n={data.get('total_patients', 'N/A')}，检出 {len(diags)} 类信号（{'、'.join(diags[:4])}等）。"
    if analysis_type == "聚类":
        clusters = data.get("clusters") or []
        return f"共病聚类 {len(clusters)} 组，样本 {data.get('total_patients', 'N/A')} 例。"
    if analysis_type == "网络图":
        return f"共病网络：节点 {len(data.get('nodes') or [])}，边 {len(data.get('edges') or [])}。"
    return str(data.get("summary") or data.get("inference_note") or "临床分析已完成")[:800]


def summarize_followup_trend(data: dict) -> str:
    tbl = data.get("trend_table") or []
    lines = []
    for row in tbl[:5]:
        tp = row.get("time_point") or row.get("visit_date") or ""
        hamd = row.get("HAMD_total") or row.get("HAMD")
        if hamd is not None:
            lines.append(f"{tp} HAMD≈{hamd}")
    return "随访趋势：" + ("；".join(lines) if lines else "已生成趋势数据")[:800]


def summarize_followup_compare(data: dict) -> str:
    comp = data.get("comparison") or []
    lines = []
    for row in comp[:4]:
        tp = row.get("time_point", "")
        for ind, val in row.items():
            if ind == "time_point" or not isinstance(val, dict):
                continue
            lines.append(f"{tp} {ind} A={val.get('group_a_mean')} B={val.get('group_b_mean')} p={val.get('p_value')}")
    return "组间随访对比：" + ("；".join(lines) if lines else "已完成组间对比")[:800]


def summarize_reference_compare(data: dict) -> str:
    rows = data.get("comparison") or data.get("indicators") or []
    if isinstance(rows, dict):
        rows = [rows]
    lines = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        ind = row.get("indicator", "")
        diff = row.get("mean_difference") or row.get("diff")
        pv = row.get("p_value")
        lines.append(f"{ind} 均值差={diff}，p={pv}")
    return "参考区间人群对比：" + ("；".join(lines) if lines else "已完成对比")[:800]


def render_correlation_result(data: dict) -> None:
    import matplotlib.pyplot as plt
    import seaborn as sns

    matrix = data.get("matrix")
    labels = data.get("labels", [])
    p_values = data.get("p_values")
    method = data.get("method", "")
    st.markdown(f"**Method**: {method} | **N**: {data.get('sample_size', 'N/A')}")
    if matrix and labels:
        annot = []
        for i in range(len(labels)):
            row_a = []
            for j in range(len(labels)):
                r_val = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else None
                if i == j:
                    row_a.append("1.00")
                elif r_val is None:
                    row_a.append("N/A")
                else:
                    row_a.append(f"{float(r_val):.2f}")
            annot.append(row_a)
        fig, ax = plt.subplots(figsize=(max(5, len(labels) * 1.2), max(4, len(labels))))
        sns.heatmap(
            matrix, annot=annot, fmt="", xticklabels=labels, yticklabels=labels,
            cmap="RdBu_r", center=0, vmin=-1, vmax=1, ax=ax, linewidths=0.5,
        )
        ax.set_title(f"Correlation Matrix ({method})")
        st.pyplot(fig)
        plt.close(fig)
    if p_values and labels:
        sig_pairs = []
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                pv = p_values[i][j] if i < len(p_values) and j < len(p_values[i]) else None
                r_val = matrix[i][j] if matrix else None
                if pv is None:
                    continue
                sig = ""
                if pv < 0.001:
                    sig = "***"
                elif pv < 0.01:
                    sig = "**"
                elif pv < 0.05:
                    sig = "*"
                sig_pairs.append({
                    "var_a": labels[i],
                    "var_b": labels[j],
                    "r": round(float(r_val), 4) if r_val is not None else None,
                    "p_value": round(float(pv), 4),
                    "significance": sig or "ns",
                })
        st.dataframe(pd.DataFrame(sig_pairs), use_container_width=True, hide_index=True)
