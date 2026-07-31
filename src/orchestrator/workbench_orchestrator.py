# orchestrator/workbench_orchestrator.py — 2.2.10 数据分析工作台编排

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

import pandas as pd

from configs.config import TEMP_FOLDER

_LOG = logging.getLogger(__name__)

RUNS_ROOT = Path(TEMP_FOLDER) / "workbench_runs"


@dataclass
class WorkbenchRunState:
    run_id: str
    session_id: str
    user_id: int
    task: str
    route: str = "workbench"
    status: str = "pending"
    current_stage: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    charts: List[Dict[str, Any]] = field(default_factory=list)
    suggestions: List[Dict[str, Any]] = field(default_factory=list)
    profile: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    evaluation: Dict[str, Any] = field(default_factory=dict)
    manifest_path: str = ""
    run_dir: str = ""
    error: Optional[str] = None
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # 用户可控出图：有 chart_specs 时优先按规格出图；否则 auto_charts=True 才自动出图
    auto_charts: bool = False
    chart_specs: List[Dict[str, Any]] = field(default_factory=list)

    def emit(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        entry = {"ts": time.time(), "type": event_type, **payload}
        self.timeline.append(entry)
        return entry


def _suitable_group_cols(df: pd.DataFrame) -> list:
    """Columns usable as binary / low-cardinality grouping factors."""
    skip_names = {
        "patient_id", "sample_id", "subject_id", "id", "row_id", "__row_id__",
        "uuid", "index",
    }
    found = []
    for c in df.columns:
        name = str(c)
        if name.startswith("__") or name.lower() in skip_names:
            continue
        try:
            nu = int(df[c].nunique(dropna=True))
        except Exception:
            continue
        if nu == 2:
            found.append(name)
        elif (not pd.api.types.is_numeric_dtype(df[c])) and 2 <= nu <= 12:
            found.append(name)
    return found


def _default_eda_pipeline(df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Fallback ≥10-step pipeline when LLM planner unavailable.

    Group comparison solvers are included only when the data has a usable
    grouping column; otherwise welch/mann_whitney fail with missing group_col.
    """
    steps: List[Dict[str, Any]] = [
        {"solver": "missing_summary", "from": "initial", "params": {}, "mapping": {}},
        {"solver": "describe_full", "from": "initial", "params": {}, "mapping": {}},
        {"solver": "distribution_histogram", "from": "initial", "params": {"n_bins": 20}, "mapping": {}},
        {"solver": "outlier_iqr_flag", "from": "initial", "params": {}, "mapping": {}},
        {"solver": "normality_test", "from": "initial", "params": {}, "mapping": {}},
        {"solver": "data_imputation", "from": "initial", "params": {"method": "median"}, "mapping": {}},
        {"solver": "encode_categorical", "from": "step", "step_index": 5, "csv_key": "imputed_csv", "params": {}, "mapping": {}},
        {"solver": "pearson_correlation", "from": "step", "step_index": 6, "csv_key": "encoded_csv", "params": {}, "mapping": {}},
        {"solver": "spearman_correlation", "from": "step", "step_index": 6, "csv_key": "encoded_csv", "params": {}, "mapping": {}},
    ]
    include_group = True if df is None else bool(_suitable_group_cols(df))
    if include_group:
        steps.extend([
            {"solver": "groupby_stat", "from": "step", "step_index": 6, "csv_key": "encoded_csv", "params": {}, "mapping": {}},
            {"solver": "welch_t_test", "from": "step", "step_index": 6, "csv_key": "encoded_csv", "params": {}, "mapping": {}},
            {"solver": "mann_whitney_u_test", "from": "step", "step_index": 6, "csv_key": "encoded_csv", "params": {}, "mapping": {}},
        ])
    steps.extend([
        {"solver": "linear_regression", "from": "step", "step_index": 6, "csv_key": "encoded_csv", "params": {}, "mapping": {}},
        # 多重校正需要「含 p 值列」的表；接 normality_test（shapiro_p/ks_p），
        # 勿接相关矩阵（step 7/8），否则会报 missing p_value_col。
        {"solver": "multiple_correction", "from": "step", "step_index": 4, "csv_key": "auto", "params": {}, "mapping": {}},
    ])
    rationale = "Default comprehensive EDA + statistical testing pipeline (no LLM)."
    if df is not None and not include_group:
        rationale += " Skipped group comparisons (no binary/low-cardinality group column)."
    return {"rationale": rationale, "steps": steps}


def _augment_llm_plan_for_task(
    plan_spec: Dict[str, Any],
    task: str,
    df: pd.DataFrame,
) -> tuple[Dict[str, Any], List[str]]:
    """Honor named analyses without turning every LLM plan into default EDA.

    A deliberately short, ordered request remains short.  A request for
    "complete EDA" is different: it asks for a data-adaptive core analysis,
    so silently omitting missingness, distribution, or outlier checks is not
    acceptable.  Group comparisons are added only when a usable group field
    exists; otherwise the rationale records why they were not planned.
    """
    raw = task or ""
    lower = raw.lower()
    comprehensive = any(
        marker in lower
        for marker in ("完整 eda", "完整eda", "全面 eda", "全面eda", "full eda", "complete eda")
    ) or ("完整" in raw and ("探索" in raw or "eda" in lower))
    has_group_request = any(
        marker in lower
        for marker in ("组间", "分组比较", "t检验", "t 检验", "mann-whitney", "非参数检验")
    )

    requested: List[str] = []
    if comprehensive:
        requested.extend([
            "metadata_parser",
            "missing_summary",
            "describe_full",
            "distribution_histogram",
            "outlier_iqr_flag",
            "normality_test",
            "pearson_correlation",
            "spearman_correlation",
        ])
    aliases = [
        (("缺失检查", "缺失率", "missing"), "missing_summary"),
        (("描述统计", "描述性统计", "describe"), "describe_full"),
        (("直方图", "分布", "histogram"), "distribution_histogram"),
        (("异常值", "outlier"), "outlier_iqr_flag"),
        (("正态", "normality"), "normality_test"),
        (("pearson",), "pearson_correlation"),
        (("spearman",), "spearman_correlation"),
        (("相关分析", "相关性", "correlation"), "pearson_correlation"),
        (("多重校正", "fdr", "bonferroni"), "multiple_correction"),
    ]
    for markers, solver in aliases:
        if any(marker in lower for marker in markers):
            requested.append(solver)

    notes: List[str] = []
    group_cols = _suitable_group_cols(df)
    if has_group_request:
        if group_cols:
            requested.extend(["groupby_stat", "welch_t_test"])
        else:
            notes.append("未加入组间比较：数据中没有可用的二分类或低基数分组列。")

    existing = {
        str(step.get("solver") or ""): dict(step)
        for step in (plan_spec.get("steps") or [])
        if isinstance(step, dict) and step.get("solver")
    }
    order = list(dict.fromkeys(requested + list(existing)))
    steps: List[Dict[str, Any]] = []
    for solver in order:
        step = existing.get(solver)
        if step is None:
            step = {"solver": solver, "from": "initial", "params": {}, "mapping": {}}
        steps.append(step)

    out = dict(plan_spec)
    out["steps"] = steps
    if notes:
        rationale = str(out.get("rationale") or "").strip()
        out["rationale"] = " ".join(part for part in (rationale, *notes) if part)
    return out, notes


def _sanitize_plan_spec(
    plan_spec: Dict[str, Any],
    df: Optional[pd.DataFrame] = None,
    allow_short: bool = False,
) -> Dict[str, Any]:
    """Drop unknown solvers; map legacy ids; prune impossible group tests.

    allow_short=True keeps intentionally short plans (resume slices).
    """
    from operator_pipeline.registry import list_solvers
    valid = {sid for sid, _ in list_solvers()}
    alias = {
        "hypothesis_tests": "welch_t_test",
        "correlation": "pearson_correlation",
        "histogram": "distribution_histogram",
    }
    steps = []
    for s in plan_spec.get("steps") or []:
        sid = str(s.get("solver", "")).strip()
        sid = alias.get(sid, sid)
        if sid in valid:
            s = dict(s)
            s["solver"] = sid
            steps.append(s)
    if df is not None and not _suitable_group_cols(df):
        drop = {"welch_t_test", "mann_whitney_u_test", "groupby_stat"}
        steps = [s for s in steps if s.get("solver") not in drop]
    if len(steps) < 10 and not allow_short:
        return _default_eda_pipeline(df)
    out = dict(plan_spec)
    out["steps"] = steps
    return out


def _load_csv(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path)


def _generate_suggestions(df: pd.DataFrame, task: str) -> List[Dict[str, Any]]:
    from orchestrator.workbench_insights import is_deg_like

    num = df.select_dtypes(include="number").columns.tolist()
    cat = df.select_dtypes(exclude="number").columns.tolist()
    miss = df.isnull().mean()
    suggestions = []

    if is_deg_like(df):
        suggestions.append({
            "title": "差异表达解读",
            "reason": "检测到 logFC 与 p 值列，适合火山图、显著性筛选与效应量复核",
            "pipeline_hint": ["describe_full", "multiple_correction", "pearson_correlation"],
        })

    if (miss > 0).any():
        top_miss = miss[miss > 0].sort_values(ascending=False).head(5)
        suggestions.append({
            "title": "数据治理",
            "reason": f"检测到 {int((miss > 0).sum())} 列存在缺失值，最高缺失率 {top_miss.iloc[0]:.1%}",
            "pipeline_hint": ["missing_summary", "data_imputation", "data_quality_check"],
        })

    if len(num) >= 2 and not is_deg_like(df):
        suggestions.append({
            "title": "相关与组间比较",
            "reason": f"数值变量 {len(num)} 个，可进行相关分析与组间检验",
            "pipeline_hint": ["pearson_correlation", "welch_t_test", "groupby_stat"],
        })
    elif len(num) >= 2 and is_deg_like(df):
        suggestions.append({
            "title": "指标相关结构",
            "reason": "可对 logFC / AveExpr / t / p 等连续指标做相关与分布检查",
            "pipeline_hint": ["pearson_correlation", "spearman_correlation", "distribution_histogram"],
        })

    if len(num) >= 3:
        suggestions.append({
            "title": "特征与回归建模",
            "reason": "变量足够支撑回归/特征选择分析",
            "pipeline_hint": ["feature_selection", "linear_regression", "lasso_cv_select"],
        })

    if cat and num:
        suggestions.append({
            "title": "分组可视化",
            "reason": f"分类变量 {cat[0]} 可用于分组比较",
            "pipeline_hint": ["groupby_stat", "welch_t_test", "distribution_histogram"],
        })

    if "发" in task or "论文" in task or "novel" in task.lower():
        suggestions.append({
            "title": "科学发现模式",
            "reason": "任务含发表/发现意图，建议启用 Discovery 路径做假设验证与新颖性评估",
            "pipeline_hint": ["discovery_route"],
        })

    if not suggestions:
        suggestions.append({
            "title": "基础 EDA",
            "reason": "先跑完整描述统计与分布检查",
            "pipeline_hint": ["describe_full", "distribution_histogram", "normality_test"],
        })
    return suggestions


def _evaluate_results(
    manifest: Dict[str, Any],
    suggestions: List[Dict[str, Any]],
    facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from orchestrator.workbench_insights import evaluate_results

    return evaluate_results(manifest, suggestions, facts)


def _template_explain(
    task: str,
    manifest: Dict[str, Any],
    facts: Optional[Dict[str, Any]] = None,
) -> str:
    from orchestrator.workbench_insights import template_explain

    return template_explain(task, facts)


def _llm_explain(
    task: str,
    manifest: Dict[str, Any],
    profile_text: str,
    facts: Optional[Dict[str, Any]] = None,
) -> str:
    from orchestrator.workbench_insights import explain_results

    return explain_results(task, manifest, profile_text, facts)


class WorkbenchOrchestrator:
    """NL → profile → suggest → plan → execute → chart → explain → evaluate."""

    def __init__(self):
        RUNS_ROOT.mkdir(parents=True, exist_ok=True)

    def create_run_dir(self, session_id: str) -> tuple[str, Path]:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
        run_dir = RUNS_ROOT / session_id / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_id, run_dir

    def run_sync(
        self,
        task: str,
        csv_path: Path,
        session_id: str,
        user_id: int,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        state: Optional[WorkbenchRunState] = None,
    ) -> WorkbenchRunState:
        for ev in self.run_events(task, csv_path, session_id, user_id, state=state):
            if on_event:
                on_event(ev)
        assert state is not None
        return state

    def run_events(
        self,
        task: str,
        csv_path: Path,
        session_id: str,
        user_id: int,
        state: Optional[WorkbenchRunState] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        # Reuse caller-provided run_id/run_dir so progress polling and artifacts stay consistent.
        if state is not None and state.run_id and state.run_dir:
            run_id = state.run_id
            run_dir = Path(state.run_dir)
            run_dir.mkdir(parents=True, exist_ok=True)
        else:
            run_id, run_dir = self.create_run_dir(session_id)
            if state is None:
                state = WorkbenchRunState(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    task=task,
                    run_dir=str(run_dir),
                )
            else:
                state.run_id = run_id
                state.run_dir = str(run_dir)

        def _yield(ev: Dict[str, Any]):
            yield ev

        try:
            # --- Route (keyword-only; avoid importing operator_agent package init) ---
            discovery_markers = (
                "发论文", "发表", "假设", "显著", "科研发现", "publish",
                "hypothesis", "significant", "novelty", "p-value", "p值",
            )
            task_l = (task or "").lower()
            is_discovery = any(m.lower() in task_l or m in (task or "") for m in discovery_markers)
            state.route = "discovery" if is_discovery else "general"
            ev = state.emit("route", {"route": state.route, "reason": "keyword"})
            yield ev

            if state.route == "discovery":
                yield from self._run_discovery_path(task, csv_path, state)
                return

            state.status = "running"
            input_copy = run_dir / "input.csv"
            df = _load_csv(csv_path)
            if csv_path.suffix.lower() != ".csv":
                df.to_csv(input_copy, index=False)
            else:
                input_copy.write_bytes(csv_path.read_bytes())

            # --- Profile ---
            state.current_stage = "profile"
            from operator_library.profiler import profile_df, profile_to_text
            prof = profile_df(df)
            profile_text = profile_to_text(prof)
            state.profile = {"rows": len(df), "cols": len(df.columns), "text": profile_text[:3000]}
            (run_dir / "profile.txt").write_text(profile_text, encoding="utf-8")
            yield state.emit("profile", {"rows": len(df), "cols": list(df.columns)[:20]})

            if state.cancel_event.is_set():
                state.status = "cancelled"
                yield state.emit("cancelled", {})
                return

            # --- Suggestions ---
            state.current_stage = "suggest"
            state.suggestions = _generate_suggestions(df, task)
            (run_dir / "suggestions.json").write_text(
                json.dumps(state.suggestions, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            yield state.emit("suggestions", {"items": state.suggestions})

            # --- Plan ---
            # Prefer LLM planning so用户可用中文点名步骤与顺序；失败再回退默认 EDA 链。
            # WORKBENCH_USE_LLM_PLAN=0 可强制只用默认链。
            state.current_stage = "plan"
            plan_spec: Dict[str, Any] = _default_eda_pipeline(df)
            plan_source = "default_eda"
            import os
            use_llm_plan = os.getenv("WORKBENCH_USE_LLM_PLAN", "1").strip().lower() not in (
                "0", "false", "no",
            )
            if use_llm_plan:
                try:
                    from operator_agent.planner import plan_pipeline
                    plan_task = (
                        "【编排约束】用户用自然语言描述要用的分析及顺序时，必须严格按用户顺序编排对应算子；"
                        "用户可用中文名（如缺失检查、描述统计、正态性检验、相关分析、t检验、非参数检验、多重校正），"
                        "不必写算子 id。不要擅自改成与用户要求无关的默认全流程；"
                        "若用户只说「完整 EDA」而未指定顺序，再使用合理的探索性分析顺序。\n"
                        f"用户任务：{task}"
                    )
                    plan_result = plan_pipeline(task=plan_task, df=df, skip_hypothesis=True)
                    if plan_result.ok and plan_result.spec.get("steps"):
                        plan_spec = plan_result.spec
                        plan_source = "llm"
                except Exception as exc:
                    _LOG.warning("planner fallback: %s", exc)
                    plan_spec = _default_eda_pipeline(df)

            if plan_source == "llm":
                llm_step_count = len(plan_spec.get("steps") or [])
                plan_spec, additions = _augment_llm_plan_for_task(plan_spec, task, df)
                if additions or len(plan_spec.get("steps") or []) != llm_step_count:
                    plan_source = "llm_augmented"

            # Explicit LLM plans can intentionally be short (e.g. “先描述统计，
            # 再相关，最后 t 检验”); only the default/fallback route requires
            # the historic comprehensive-EDA minimum length.
            plan_spec = _sanitize_plan_spec(
                plan_spec,
                df,
                allow_short=plan_source.startswith("llm"),
            )
            plan_txt = json.dumps(plan_spec, ensure_ascii=False, indent=2)
            (run_dir / "plan.json").write_text(plan_txt, encoding="utf-8")
            (run_dir / "plan_full.json").write_text(plan_txt, encoding="utf-8")
            yield state.emit("plan", {
                "step_count": len(plan_spec.get("steps", [])),
                "rationale": plan_spec.get("rationale", ""),
                "source": plan_source,
            })

            if state.cancel_event.is_set():
                state.status = "cancelled"
                yield state.emit("cancelled", {})
                return

            # --- Execute ---
            state.current_stage = "execute"
            yield from self._execute_and_finalize(
                state, run_dir, input_copy, plan_spec, task, profile_text,
                resume_from=0, kept_steps=None,
            )

        except Exception as exc:
            _LOG.exception("workbench run failed")
            state.status = "error"
            state.error = str(exc)
            yield state.emit("error", {"message": str(exc)})

    def _execute_and_finalize(
        self,
        state: WorkbenchRunState,
        run_dir: Path,
        input_copy: Path,
        plan_spec: Dict[str, Any],
        task: str,
        profile_text: str,
        resume_from: int = 0,
        kept_steps: Optional[List[Dict[str, Any]]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        from operator_pipeline.run_spec import build_pipeline_from_spec
        from operator_pipeline.runner import execute_pipeline

        steps, step_names = build_pipeline_from_spec(plan_spec)
        out_root = run_dir / "pipeline_output"
        out_root.mkdir(parents=True, exist_ok=True)

        state.steps = list(kept_steps or [])
        for rec in state.steps:
            yield state.emit("step_update", {**rec, "kept": True})

        # When kept_steps provided, plan is already sliced; run all of it.
        # Otherwise honor resume_from for partial execute of a full plan.
        if kept_steps:
            run_steps, run_names = steps, step_names
            yield state.emit("resume", {
                "kept": len(kept_steps),
                "remaining": len(run_steps),
            })
        else:
            run_steps = steps[resume_from:]
            run_names = step_names[resume_from:]
            if resume_from > 0:
                yield state.emit("resume", {
                    "from_step": resume_from,
                    "kept": resume_from,
                    "remaining": len(run_steps),
                })

        run_result = execute_pipeline(
            run_steps, input_copy, out_root, use_llm=False, task_description=task
        )

        base_idx = len(state.steps)
        for i, step_info in enumerate(run_result.get("steps") or []):
            rec = {
                "index": base_idx + i,
                "name": run_names[i] if i < len(run_names) else step_info.get("solver"),
                "solver": step_info.get("solver"),
                "status": step_info.get("status"),
                "output_dir": step_info.get("output_dir"),
            }
            state.steps.append(rec)
            yield state.emit("step_update", rec)

        new_steps = list(run_result.get("steps") or [])
        if kept_steps:
            manifest = dict(run_result) if isinstance(run_result, dict) else {"steps": []}
            manifest["steps"] = [
                {**s, "status": s.get("status") or "ok", "kept": True}
                for s in kept_steps
            ] + new_steps
            manifest["resumed"] = True
        else:
            manifest = run_result if isinstance(run_result, dict) else {"steps": state.steps}

        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        state.manifest_path = str(manifest_path)

        state.current_stage = "charts"
        from viz.chart_renderer import render_charts_from_run, render_charts_from_specs

        specs = list(state.chart_specs or [])
        if specs:
            # 用户指定了图：只画这些，不被自动图淹没
            state.charts = render_charts_from_specs(run_dir, input_copy, specs)
            chart_mode = "user_specs"
        elif state.auto_charts:
            state.charts = render_charts_from_run(run_dir, input_copy)
            chart_mode = "auto"
        else:
            # 未指定任何图且未开自动：回退自动，避免结果区空白
            state.charts = render_charts_from_run(run_dir, input_copy)
            chart_mode = "auto_fallback"

        # Per-chart LLM/rule captions for UI under each figure
        try:
            from orchestrator.workbench_chart_captions import annotate_charts_with_analysis
            df_for_cap = None
            try:
                df_for_cap = _load_csv(input_copy)
            except Exception:
                df_for_cap = None
            state.charts = annotate_charts_with_analysis(
                state.charts, df=df_for_cap, task=task, profile_text=profile_text
            )
            slim = [{k: v for k, v in c.items() if k != "base64"} for c in state.charts]
            (run_dir / "charts.json").write_text(
                json.dumps({"charts": slim, "mode": chart_mode, "count": len(slim)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            _LOG.warning("chart captions failed: %s", exc)

        yield state.emit("charts", {
            "count": len(state.charts),
            "mode": chart_mode,
            "charts": [
                {k: v for k, v in c.items() if k != "base64"} for c in state.charts[:12]
            ],
        })

        # Persist full plan once so resume never re-inflates a short slice.
        full_plan_path = run_dir / "plan_full.json"
        if not full_plan_path.is_file():
            full_plan_path.write_text(
                json.dumps(plan_spec, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        from orchestrator.workbench_insights import collect_data_facts

        facts = collect_data_facts(run_dir, input_copy, state.charts, profile_text)

        state.current_stage = "explain"
        state.summary = _llm_explain(task, manifest, profile_text, facts)
        (run_dir / "summary.md").write_text(state.summary, encoding="utf-8")
        yield state.emit("summary", {"text": state.summary})

        state.current_stage = "evaluate"
        state.evaluation = _evaluate_results(manifest, state.suggestions, facts)
        try:
            from backend.workbench_kg_client import enrich_evaluation_with_kg
            state.evaluation = enrich_evaluation_with_kg(task, state.summary, state.evaluation)
        except Exception as exc:
            _LOG.debug("kg enrich skipped: %s", exc)
        (run_dir / "evaluation.json").write_text(
            json.dumps(state.evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        yield state.emit("evaluation", state.evaluation)

        state.status = "completed"
        yield state.emit("done", {"run_id": state.run_id, "status": "completed"})

    def run_resume_events(
        self,
        parent_run_dir: Path,
        from_step: int,
        session_id: str,
        user_id: int,
        state: WorkbenchRunState,
        task_override: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """从父 run 的某一步起续跑，保留之前步骤产物。"""
        import shutil

        from operator_pipeline.run_spec import build_pipeline_from_spec
        from orchestrator.workbench_insights import pick_resume_seed_csv, slice_plan_for_resume

        parent_run_dir = Path(parent_run_dir)
        run_dir = Path(state.run_dir)
        # Prefer original full plan (never the previously sliced resume plan)
        plan_path = parent_run_dir / "plan_full.json"
        if not plan_path.is_file():
            plan_path = parent_run_dir / "plan.json"
        if not plan_path.is_file():
            raise FileNotFoundError(f"父 run 无 plan.json: {parent_run_dir}")

        input_copy = run_dir / "input.csv"
        parent_input = parent_run_dir / "input.csv"
        if parent_input.is_file():
            shutil.copy2(parent_input, input_copy)
        else:
            raise FileNotFoundError("父 run 缺少 input.csv")

        try:
            df_resume = _load_csv(input_copy)
        except Exception:
            df_resume = None

        raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        # Critical: allow_short so a short parent slice is NOT re-inflated to
        # the 14-step default (which breaks step_index wiring on resume).
        plan_spec = _sanitize_plan_spec(raw_plan, df=df_resume, allow_short=True)
        (run_dir / "plan_full.json").write_text(
            json.dumps(plan_spec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "parent_run.json").write_text(
            json.dumps({
                "parent_run_dir": str(parent_run_dir),
                "from_step": from_step,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        parent_pipe = parent_run_dir / "pipeline_output"
        out_root = run_dir / "pipeline_output"
        out_root.mkdir(parents=True, exist_ok=True)
        steps, step_names = build_pipeline_from_spec(plan_spec)
        if from_step < 0 or from_step >= len(steps):
            raise ValueError(
                f"from_step 超出范围: {from_step} / {len(steps)}。"
                "请点击步骤列表中的步骤号（须小于总算子数）后再续跑。"
            )

        kept_steps: List[Dict[str, Any]] = []
        for i in range(from_step):
            name = step_names[i] if i < len(step_names) else f"step_{i}"
            src = parent_pipe / name
            if src.is_dir():
                dst = out_root / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            kept_steps.append({
                "index": i,
                "name": name,
                "solver": (plan_spec.get("steps") or [{}])[i].get("solver")
                if i < len(plan_spec.get("steps") or []) else name,
                "status": "ok",
                "output_dir": str(out_root / name),
                "kept": True,
            })

        # Seed with encoded/imputed table so remapped solvers don't ask pearson for encoded_csv
        seed_csv = pick_resume_seed_csv(out_root, step_names, from_step, input_copy)
        if seed_csv.is_file() and seed_csv.resolve() != input_copy.resolve():
            shutil.copy2(seed_csv, input_copy)

        rem_steps = slice_plan_for_resume(plan_spec.get("steps") or [], from_step)
        if not rem_steps:
            raise ValueError("没有可续跑的剩余步骤")
        sliced_plan = dict(plan_spec)
        sliced_plan["steps"] = rem_steps
        sliced_plan["rationale"] = (plan_spec.get("rationale") or "") + f" (resume from step {from_step})"
        (run_dir / "plan.json").write_text(
            json.dumps(sliced_plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        task = task_override or state.task
        state.task = task
        state.status = "running"
        state.route = "workbench"
        state.current_stage = "resume"
        profile_text = ""
        prof_path = parent_run_dir / "profile.txt"
        if prof_path.is_file():
            profile_text = prof_path.read_text(encoding="utf-8")
            (run_dir / "profile.txt").write_text(profile_text, encoding="utf-8")
        sug_path = parent_run_dir / "suggestions.json"
        if sug_path.is_file():
            try:
                state.suggestions = json.loads(sug_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        yield state.emit("resume_start", {"from_step": from_step, "parent": str(parent_run_dir)})
        # Execute sliced plan from 0; kept_steps only for timeline display
        yield from self._execute_and_finalize(
            state, run_dir, seed_csv, sliced_plan, task, profile_text,
            resume_from=0, kept_steps=kept_steps,
        )

    def _run_discovery_path(self, task: str, csv_path: Path, state: WorkbenchRunState) -> Generator[Dict[str, Any], None, None]:
        """Delegate to scientific discovery TopAgent."""
        state.current_stage = "discovery"
        state.status = "running"
        yield state.emit("discovery_start", {"message": "启动科学发现 Agent..."})
        try:
            from operator_agent.discovery.top_agent import TopAgent
            agent = TopAgent()
            session = agent.start(task=task, csv=str(csv_path), max_hypotheses=2)
            while not session.is_done():
                if state.cancel_event.is_set():
                    session.cancel()
                    state.status = "cancelled"
                    yield state.emit("cancelled", {})
                    return
                prog = session.progress()
                yield state.emit("discovery_progress", prog)
                time.sleep(1.0)
            result = session.result(timeout=0.0)
            state.status = result.status if hasattr(result, "status") else "completed"
            state.summary = getattr(result, "summary", str(result))
            findings_path = getattr(result, "findings_path", None) or getattr(result, "findings_yaml", None)
            if findings_path:
                state.manifest_path = str(findings_path)
            yield state.emit("done", {"run_id": state.run_id, "status": state.status, "route": "discovery"})
        except Exception as exc:
            state.status = "error"
            state.error = str(exc)
            yield state.emit("error", {"message": str(exc)})
