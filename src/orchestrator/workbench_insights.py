# orchestrator/workbench_insights.py — 面向用户的数据/图表结论（非执行过程）

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

_LOG = logging.getLogger(__name__)


def is_deg_like(df: pd.DataFrame) -> bool:
    cols = {str(c).lower().replace(".", "_") for c in df.columns}
    has_fc = any(x in cols for x in ("logfc", "log2foldchange", "log2_fc"))
    has_p = any(x in cols for x in ("p_value", "pvalue", "p_val", "adj_p_value", "padj"))
    return has_fc and has_p


def load_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path)


def collect_data_facts(
    run_dir: Path,
    input_csv: Optional[Path],
    charts: Optional[List[Dict[str, Any]]] = None,
    profile_text: str = "",
) -> Dict[str, Any]:
    facts: Dict[str, Any] = {
        "rows": None,
        "cols": None,
        "columns": [],
        "numeric": [],
        "categorical": [],
        "missing_top": [],
        "corr_top": [],
        "deg": None,
        "chart_types": [],
        "chart_titles": [],
        "profile_text": (profile_text or "")[:2000],
    }
    df = None
    if input_csv and Path(input_csv).is_file():
        try:
            df = load_table(Path(input_csv))
        except Exception:
            df = None
    if df is not None:
        facts["rows"] = int(len(df))
        facts["cols"] = int(df.shape[1])
        facts["columns"] = [str(c) for c in df.columns]
        num = df.select_dtypes(include="number")
        cat = df.select_dtypes(exclude="number")
        facts["numeric"] = [str(c) for c in num.columns if not str(c).startswith("__")][:12]
        facts["categorical"] = [str(c) for c in cat.columns if not str(c).startswith("__")][:8]
        miss = df.isnull().mean()
        miss = miss[miss > 0].sort_values(ascending=False).head(5)
        facts["missing_top"] = [(str(i), float(v)) for i, v in miss.items()]
        if is_deg_like(df):
            pcol = next(
                (
                    c
                    for c in df.columns
                    if str(c).lower().replace(".", "_")
                    in ("p_value", "pvalue", "adj_p_value", "padj")
                ),
                None,
            )
            fcol = next(
                (
                    c
                    for c in df.columns
                    if str(c).lower().replace(".", "_") in ("logfc", "log2foldchange", "log2_fc")
                ),
                None,
            )
            sig = up = down = None
            if pcol is not None:
                sig_mask = df[pcol] < 0.05
                sig = int(sig_mask.sum())
                if fcol is not None:
                    up = int(((df[fcol] > 0) & sig_mask).sum())
                    down = int(((df[fcol] < 0) & sig_mask).sum())
            facts["deg"] = {
                "p_col": str(pcol) if pcol is not None else None,
                "fc_col": str(fcol) if fcol is not None else None,
                "significant": sig,
                "up": up,
                "down": down,
            }

    pipe = Path(run_dir) / "pipeline_output" if run_dir else None
    if pipe and pipe.is_dir():
        for pairs in pipe.rglob("*pairs*.csv"):
            try:
                pairs_df = pd.read_csv(pairs)
                cols = {c.lower(): c for c in pairs_df.columns}
                a = cols.get("var_a") or cols.get("variable_a")
                b = cols.get("var_b") or cols.get("variable_b")
                r = cols.get("r") or cols.get("rho") or cols.get("correlation")
                if a and b and r:
                    sub = pairs_df[[a, b, r]].dropna()
                    sub = sub.reindex(sub[r].abs().sort_values(ascending=False).index).head(5)
                    facts["corr_top"] = [
                        (str(row[a]), str(row[b]), float(row[r])) for _, row in sub.iterrows()
                    ]
                    break
            except Exception:
                pass

    for ch in charts or []:
        if ch.get("chart_type"):
            facts["chart_types"].append(str(ch.get("chart_type")))
        if ch.get("title"):
            facts["chart_titles"].append(str(ch.get("title")))
    facts["chart_types"] = sorted(set(facts["chart_types"]))
    facts["chart_count"] = len(charts or [])
    facts["chart_titles"] = facts["chart_titles"][:12]
    return facts


def evaluate_results(
    manifest: Dict[str, Any],
    suggestions: List[Dict[str, Any]],
    facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    facts = facts or {}
    flags: List[str] = []
    rows, cols = facts.get("rows"), facts.get("cols")
    if rows and cols:
        flags.append(f"数据规模：{rows} 行 × {cols} 列")
    if facts.get("missing_top"):
        top = facts["missing_top"][0]
        flags.append(f"缺失最突出：{top[0]}（{top[1]:.1%}）")
    elif rows:
        flags.append("主要变量缺失较少，数据完整度较好")
    if facts.get("corr_top"):
        a, b, r = facts["corr_top"][0]
        flags.append(f"最强相关：{a} 与 {b}（r={r:.3f}）")
    deg = facts.get("deg") or {}
    if deg.get("significant") is not None:
        flags.append(
            f"差异表达：p<0.05 约 {deg['significant']} 个"
            + (
                f"（上调 {deg.get('up')} / 下调 {deg.get('down')}）"
                if deg.get("up") is not None
                else ""
            )
        )
    if facts.get("chart_types"):
        flags.append("已生成图表类型：" + "、".join(facts["chart_types"][:8]))

    reason = "结论基于描述统计、相关/差异指标与可视化分布特征，供后续假设验证。"
    novelty = "若为差异表达/临床队列，可结合知识图谱评估生物学或临床新颖性。"
    if deg:
        novelty = "差异表达结果可结合通路/疾病知识图谱评估生物学新颖性与可解释性。"

    score = 0.7
    if rows and cols:
        score += 0.1
    if facts.get("corr_top") or deg.get("significant") is not None:
        score += 0.15
    if facts.get("chart_types"):
        score += 0.05

    return {
        "completeness_score": round(min(score, 1.0), 2),
        "step_count": len(manifest.get("steps") or []),
        "novelty_note": novelty,
        "reasonableness": reason,
        "flags": flags,
        "suggestions_used": [s.get("title") for s in suggestions[:3]],
        "data_facts": {
            "rows": rows,
            "cols": cols,
            "numeric": facts.get("numeric"),
            "deg": deg or None,
        },
    }


def template_explain(task: str, facts: Optional[Dict[str, Any]] = None) -> str:
    facts = facts or {}
    lines = ["## 数据分析结论", "", f"**分析问题**：{task}", ""]
    rows, cols = facts.get("rows"), facts.get("cols")
    if rows and cols:
        lines.append(f"**数据概况**：共 {rows} 条记录、{cols} 个变量。")
        if facts.get("numeric"):
            lines.append(f"**数值变量**：{', '.join(facts['numeric'][:8])}。")
        if facts.get("categorical"):
            lines.append(f"**分类变量**：{', '.join(facts['categorical'][:6])}。")
        lines.append("")
    if facts.get("missing_top"):
        miss_txt = "；".join(f"{n} {r:.1%}" for n, r in facts["missing_top"][:4])
        lines += [f"**缺失情况**：{miss_txt}。", ""]
    else:
        lines += ["**缺失情况**：未发现突出缺失（或已较低）。", ""]

    deg = facts.get("deg") or {}
    if deg:
        lines.append("**差异表达要点**：")
        if deg.get("significant") is not None:
            lines.append(
                f"- 在 {deg.get('p_col')} < 0.05 标准下，约有 **{deg['significant']}** 个显著条目"
                + (
                    f"，其中上调 {deg.get('up')}、下调 {deg.get('down')}"
                    if deg.get("up") is not None
                    else ""
                )
                + "。"
            )
        lines.append(
            "- 建议优先查看火山图中远离原点且显著的基因/探针，并结合效应量（logFC）复核。"
        )
        lines.append("")

    if facts.get("corr_top"):
        lines.append("**相关结构**：")
        for a, b, r in facts["corr_top"][:5]:
            lines.append(f"- {a} ↔ {b}：r = {r:.3f}")
        lines.append("")

    n_charts = int(facts.get("chart_count") or 0) or len(facts.get("chart_titles") or [])
    if n_charts:
        lines.append(
            f"**图表**：本次生成了 **{n_charts} 张** 图"
            + (
                f"（涉及类型：{', '.join((facts.get('chart_types') or [])[:8])}；"
                f"可选图种共 22 种）"
                if facts.get("chart_types")
                else ""
            )
            + "。"
        )
        lines.append(
            "请重点看：分布形态、组间差异、相关热图中的强相关块，以及（若有）火山图显著区。"
        )
        lines.append("")

    lines.append("**小结**：以上对应数据特征与图示结果；可在右侧图表与表格继续复核。")
    return "\n".join(lines)


def explain_results(
    task: str,
    manifest: Dict[str, Any],
    profile_text: str,
    facts: Optional[Dict[str, Any]] = None,
) -> str:
    import os

    facts = facts or {}
    if os.getenv("WORKBENCH_USE_LLM_EXPLAIN", "").strip() not in ("1", "true", "TRUE", "yes"):
        return template_explain(task, facts)
    try:
        from operator_pipeline import llm_client

        if not llm_client.is_available():
            raise RuntimeError("LLM unavailable")
        facts_brief = json.dumps(
            {
                k: facts.get(k)
                for k in (
                    "rows",
                    "cols",
                    "numeric",
                    "categorical",
                    "missing_top",
                    "corr_top",
                    "deg",
                    "chart_types",
                )
            },
            ensure_ascii=False,
            default=str,
        )[:2500]
        out = llm_client.chat_json(
            system=(
                "你是数据分析师。根据数据事实与图表类型，用中文写面向科研/业务用户的结论。"
                "禁止罗列算子名称或执行成败；聚焦数据特点、主要发现、图表解读、下一步建议。"
                '回复 JSON: {"summary": "markdown正文"}'
            ),
            user=f"任务: {task}\n\n数据事实:\n{facts_brief}\n\n画像:\n{(profile_text or '')[:1200]}",
            max_tokens=900,
            temperature=0.2,
            json_mode=True,
            stage="workbench_explain",
        )
        if isinstance(out, dict) and out.get("summary"):
            return str(out["summary"]).strip()
    except Exception as exc:
        _LOG.debug("LLM explain fallback: %s", exc)
    return template_explain(task, facts)


def pick_resume_seed_csv(
    out_root: Path, step_names: List[str], from_step: int, fallback: Path
) -> Path:
    for i in range(from_step - 1, -1, -1):
        if i >= len(step_names):
            continue
        step_dir = out_root / step_names[i]
        man = step_dir / "step_manifest.json"
        if man.is_file():
            try:
                m = json.loads(man.read_text(encoding="utf-8"))
            except Exception:
                m = {}
            outputs = m.get("outputs") or {}
            for key in ("encoded_csv", "imputed_csv", "primary_csv"):
                p = outputs.get(key)
                if p and Path(p).is_file():
                    return Path(p)
            pc = m.get("primary_csv")
            if pc and Path(pc).is_file():
                return Path(pc)
        if step_dir.is_dir():
            preferred = sorted(step_dir.glob("*encoded*.csv")) + sorted(
                step_dir.glob("*imputed*.csv")
            )
            if preferred:
                return preferred[0]
            csvs = sorted(step_dir.glob("*.csv"))
            if csvs:
                return csvs[0]
    return fallback


def slice_plan_for_resume(
    plan_steps: List[Dict[str, Any]], from_step: int
) -> List[Dict[str, Any]]:
    rem: List[Dict[str, Any]] = []
    for i, s in enumerate(plan_steps[from_step:]):
        ns = dict(s)
        orig_idx = ns.get("step_index")
        if ns.get("from") == "step" and isinstance(orig_idx, int):
            if orig_idx < from_step:
                ns["from"] = "initial"
                ns.pop("step_index", None)
                ns.pop("csv_key", None)
            else:
                ns["step_index"] = int(orig_idx) - from_step
        if i == 0:
            ns["from"] = "initial"
            ns.pop("step_index", None)
            ns.pop("csv_key", None)
        rem.append(ns)
    return rem
