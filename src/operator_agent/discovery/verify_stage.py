# -*- coding: utf-8 -*-
"""N4 — data-analysis (verify) agent.

Reuses the *existing* execution chain (read-only, never modifies planner /
coder / solvers) to turn one :class:`~operator_agent.hypothesis.Hypothesis`
into a reproducible :class:`~operator_agent.discovery.types.VerifyResult`:

    plan_pipeline(task, df, skip_hypothesis=True)        # operator selection
        -> build_pipeline_from_spec(spec)                # materialise steps
        -> execute_pipeline(steps, in_csv, out_root, ...) # run them

It then best-effort-extracts the credibility numbers (effect / p / CI / n)
from the executed step manifests + output CSVs, stamps provenance
(``dataset_hash`` / ``seed`` / ``operator_versions`` / ``artifact_paths``),
writes the result to the lane's private blackboard and emits the right
control signal.

§3.1 self-report contract
--------------------------
This function **never raises**.  Any failure (planning / build / execute /
unexpected) is caught, packed into an ``error_codes``-shaped dict, surfaced as
``status="error"`` on the returned :class:`VerifyResult`, and announced via
``bus.emit_error("verify_stage", ...)``.

Number-extraction heuristic (documented per plan §11)
-----------------------------------------------------
After execution we scan, in order, every executed step's:

1. scalar ``outputs`` values (``{key: number}``),
2. ``outputs_schema`` preview rows (header + first rows of each CSV),
3. the output CSV files themselves (read with pandas).

For each source we match column/key names (case-insensitive, separators
normalised) against fixed keyword sets:

- ``p``      ← ``p_value`` / ``p`` / ``pval`` / ``p_adj`` / ``padj`` / …
- ``effect`` ← ``effect`` / ``beta`` / ``coef`` / ``estimate`` / ``ate`` /
               ``or`` / ``odds_ratio`` / ``hr`` / ``r`` / ``correlation`` / …
- ``ci``     ← (``ci_low`` | ``conf_low`` | ``lower`` | ``lcl`` | …) paired
               with (``ci_high`` | ``conf_upper`` | ``upper`` | ``ucl`` | …)
- ``n``      ← ``n`` / ``n_obs`` / ``sample_size`` / ``n_samples`` / …

The **first** usable (non-NaN) value wins for each field; every raw hit is
stashed in ``VerifyResult.extra["raw_findings"]`` so nothing is silently
dropped.  ``effect_type`` is inferred from which effect keyword matched.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from operator_pipeline import llm_client

from operator_agent.hypothesis import Hypothesis
from operator_agent.discovery.blackboard import (
    PrivateBlackboard,
    PublicBlackboard,
)
from operator_agent.discovery.signals import SignalBus
from operator_agent.discovery.types import Evidence, VerifyResult
from operator_agent.discovery.verify_extractors import extract_for_step

__all__ = ["run", "PRODUCER", "PRIVATE_BB_KEY", "MIN_USABLE_N"]

PRODUCER = "verify_stage"
#: private-blackboard key written by this stage.
PRIVATE_BB_KEY = "verify_result"

#: Below this many observations a numeric result is treated as
#: underpowered → ``inconclusive`` (not an execution error).
MIN_USABLE_N = 3


# ---------------------------------------------------------------------------
# Keyword sets for the number-extraction heuristic
# ---------------------------------------------------------------------------
_P_KEYS = {
    "p", "p_value", "pvalue", "pval", "p_val", "p_adj", "padj", "adj_p",
    "adj_pval", "adj_p_value", "q_value", "qvalue",
}
# effect keyword -> canonical effect_type label
_EFFECT_KEYS: Dict[str, str] = {
    "effect": "effect",
    "effect_size": "effect_size",
    "beta": "beta",
    "coef": "beta",
    "coefficient": "beta",
    "estimate": "estimate",
    "ate": "ATE",
    "att": "ATT",
    "or": "OR",
    "odds_ratio": "OR",
    "oddsratio": "OR",
    "hr": "HR",
    "hazard_ratio": "HR",
    "rd": "risk_diff",
    "risk_difference": "risk_diff",
    "risk_diff": "risk_diff",
    "r": "correlation",
    "rho": "correlation",
    "correlation": "correlation",
    "corr": "correlation",
    "cohens_d": "cohens_d",
    "mean_diff": "mean_diff",
    "mean_difference": "mean_diff",
    "diff": "mean_diff",
    # bioinformatics — differential-expression effect sizes
    "logfc": "logFC",
    "log_fc": "logFC",
    "log2fc": "logFC",
    "log2_fc": "logFC",
    "log2foldchange": "logFC",
    "log2_fold_change": "logFC",
    "log_fold_change": "logFC",
    "logfoldchange": "logFC",
    "fold_change": "fold_change",
    "foldchange": "fold_change",
    # bioinformatics — over-representation / enrichment effect sizes
    "fold_enrichment": "fold_enrichment",
    "enrichment_ratio": "fold_enrichment",
    "enrichment_score": "enrichment_score",
    "nes": "enrichment_score",
}
_CI_LOW_KEYS = {
    "ci_low", "ci_lower", "ci_lo", "conf_low", "conf_lower", "lower",
    "lower_ci", "lcl", "ci_2.5", "ci_2_5", "ci_low_95", "ci95_low",
    "lower_bound", "ci_lower_95",
}
_CI_HIGH_KEYS = {
    "ci_high", "ci_upper", "ci_hi", "conf_high", "conf_upper", "upper",
    "upper_ci", "ucl", "ci_97.5", "ci_97_5", "ci_high_95", "ci95_high",
    "upper_bound", "ci_upper_95",
}
_N_KEYS = {
    "n", "n_obs", "nobs", "sample_size", "n_samples", "n_total",
    "count", "num_obs",
    # bioinformatics — enrichment "n" is most informatively the target set
    # size (the # of genes the test was applied to), with overlap as a
    # secondary anchor for context.  Two-group DEG tables expose per-group
    # sizes as n_a / n_b — pick the larger of the two so the resulting
    # number stays a credible "effective sample size" for the test row.
    "n_target", "n_overlap", "n_genes_in_term", "n_universe",
    "n_a", "n_b",
}


def _norm_key(k: Any) -> str:
    return str(k).strip().lower().replace("-", "_").replace(" ", "_")


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


# ---------------------------------------------------------------------------
# Error helpers (error_codes-shaped contract — see operator_pipeline.error_codes)
# ---------------------------------------------------------------------------
def _error_dict(code: str, phase: str, message: str, hint: str,
                args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "error_code": code,
        "error_phase": phase,
        "error": message,
        "next_action_hint": hint,
        "error_args": dict(args or {}),
    }


def _dataset_hash(df: pd.DataFrame) -> str:
    try:
        payload = df.to_csv(index=False).encode("utf-8")
    except Exception:
        payload = repr(df.shape).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _augment_task(task: str, hypothesis: Hypothesis) -> str:
    """Fold the hypothesis into the planning task string so the operator
    selector has the scientific context (kept simple + documented)."""
    bits: List[str] = [task.strip()]
    if hypothesis.variables:
        bits.append("Variables of interest: "
                    + ", ".join(hypothesis.variables) + ".")
    if hypothesis.primary_outcome:
        bits.append(f"Primary outcome: {hypothesis.primary_outcome}.")
    if hypothesis.expected_effect_direction:
        bits.append("Expected effect direction: "
                    f"{hypothesis.expected_effect_direction}.")
    if hypothesis.rationale:
        bits.append(f"Hypothesis rationale: {hypothesis.rationale}")
    bits.append(f"(finding_family={hypothesis.finding_family})")
    return " ".join(b for b in bits if b)


# ---------------------------------------------------------------------------
# Number extraction
# ---------------------------------------------------------------------------
def _scan_mapping(src: str, mapping: Dict[str, Any],
                  raw: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Scan one flat ``{key: value}`` mapping for credibility numbers.

    Returns a partial dict possibly containing ``effect`` / ``effect_type``
    / ``p`` / ``n`` / ``ci_low`` / ``ci_high``.  Records every hit in *raw*.
    """
    found: Dict[str, Any] = {}
    for k, v in mapping.items():
        nk = _norm_key(k)
        fv = _to_float(v)
        if fv is None:
            continue
        if nk in _P_KEYS and "p" not in found:
            found["p"] = fv
            raw.append({"source": src, "key": k, "field": "p", "value": fv})
        elif nk in _EFFECT_KEYS and "effect" not in found:
            found["effect"] = fv
            found["effect_type"] = _EFFECT_KEYS[nk]
            raw.append({"source": src, "key": k, "field": "effect",
                        "value": fv})
        elif nk in _CI_LOW_KEYS and "ci_low" not in found:
            found["ci_low"] = fv
            raw.append({"source": src, "key": k, "field": "ci_low",
                        "value": fv})
        elif nk in _CI_HIGH_KEYS and "ci_high" not in found:
            found["ci_high"] = fv
            raw.append({"source": src, "key": k, "field": "ci_high",
                        "value": fv})
        elif nk in _N_KEYS and "n" not in found:
            found["n"] = fv
            raw.append({"source": src, "key": k, "field": "n", "value": fv})
    return found


def _merge_found(acc: Dict[str, Any], part: Dict[str, Any]) -> None:
    for key in ("effect", "effect_type", "p", "n", "ci_low", "ci_high"):
        if key in part and key not in acc:
            acc[key] = part[key]


def _scan_csv(path: Path, raw: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Read a result CSV and scan its columns (first non-null per column)
    plus the common ``metric``/``value`` long-format layout."""
    acc: Dict[str, Any] = {}
    try:
        df = pd.read_csv(path, nrows=200)
    except Exception:
        return acc
    if df.empty:
        return acc
    src = path.name
    # Wide layout: one column per statistic.
    col_map: Dict[str, Any] = {}
    for col in df.columns:
        series = df[col].dropna()
        if series.empty:
            continue
        col_map[col] = series.iloc[0]
    _merge_found(acc, _scan_mapping(src, col_map, raw))
    # Long layout: a name column + a value column.
    lower_cols = {_norm_key(c): c for c in df.columns}
    name_col = next((lower_cols[c] for c in
                     ("metric", "statistic", "stat", "name", "parameter",
                      "term", "variable") if c in lower_cols), None)
    val_col = next((lower_cols[c] for c in
                    ("value", "val", "estimate", "result") if c in lower_cols),
                   None)
    if name_col is not None and val_col is not None:
        long_map = {}
        for _, row in df.iterrows():
            long_map[str(row[name_col])] = row[val_col]
        _merge_found(acc, _scan_mapping(src + ":long", long_map, raw))
    return acc


def _extract_numbers(steps: List[Dict[str, Any]],
                     run_dir: Optional[Path]
                     ) -> Tuple[Dict[str, Any], List[Dict[str, Any]],
                                List[str]]:
    """Best-effort scan over executed steps for credibility numbers.

    Returns ``(found, raw_findings, artifact_paths)``.

    Note: this function preserves the *flat-merge* legacy behaviour for
    the convenience ``found`` dict (which feeds the legacy single-value
    ``effect``/``p``/``n`` fields).  ADR-0008's per-operator evidence is
    produced separately by :func:`_extract_evidence_per_operator` so the
    legacy call sites continue to work bit-for-bit.
    """
    acc: Dict[str, Any] = {}
    raw: List[Dict[str, Any]] = []
    artifacts: List[str] = []
    csv_paths: List[Path] = []

    for st in steps or []:
        name = st.get("name", "?")
        outputs = st.get("outputs") or {}
        # (1) scalar outputs
        scalars = {k: v for k, v in outputs.items()
                   if isinstance(v, (int, float))}
        if scalars:
            _merge_found(acc, _scan_mapping(f"{name}.outputs", scalars, raw))
        # collect artifact file paths + queue CSVs for scanning
        for v in outputs.values():
            if isinstance(v, str) and v:
                p = Path(v)
                try:
                    is_file = p.is_file()
                except Exception:
                    is_file = False
                if is_file:
                    artifacts.append(str(p))
                    if p.suffix.lower() == ".csv":
                        csv_paths.append(p)
        # (2) outputs_schema preview rows (header + first rows)
        schema = st.get("outputs_schema") or {}
        for entry in schema.values():
            for row in (entry.get("preview_rows") or []):
                if isinstance(row, dict):
                    _merge_found(acc, _scan_mapping(f"{name}.preview", row,
                                                    raw))

    # (3) read the output CSVs themselves
    for p in csv_paths:
        _merge_found(acc, _scan_csv(p, raw))

    # Also sweep the run_dir tree for any artifact files we missed.
    if run_dir is not None:
        try:
            for p in sorted(run_dir.rglob("*")):
                if p.is_file():
                    sp = str(p)
                    if sp not in artifacts:
                        artifacts.append(sp)
        except Exception:
            pass

    return acc, raw, artifacts


def _extract_evidence_per_operator(steps: List[Dict[str, Any]]
                                   ) -> List[Evidence]:
    """Produce one :class:`Evidence` per executed pipeline step.

    ADR-0008 — uses the per-operator extractor table for known bio
    operators; falls back to a single-operator flat scan for unknown
    operators (still preserving the per-operator boundary).  Critically,
    no values are merged across operator boundaries — that was the
    cross-operator semantic-mixing bug this ADR fixes.
    """
    out: List[Evidence] = []
    for st in steps or []:
        try:
            ev = extract_for_step(
                st,
                fallback_p_keys=_P_KEYS,
                fallback_effect_keys=_EFFECT_KEYS,
                fallback_n_keys=_N_KEYS,
            )
        except Exception:
            ev = Evidence(source_operator=str(st.get("solver") or "?"))
        out.append(ev)
    return out


def _pick_primary(evidence: List[Evidence],
                  steps: List[Dict[str, Any]]) -> Optional[Evidence]:
    """ADR-0008 §3 — pick the primary operator's Evidence for the
    single-value ``effect``/``p``/``n`` fields.

    Order: (1) operator marked ``primary=True`` in its step spec;
    (2) first Evidence with all three of effect/p/n populated;
    (3) first Evidence with at least p populated; (4) None.
    """
    if not evidence:
        return None
    primary_id: Optional[str] = None
    for st in (steps or []):
        if st.get("primary"):
            primary_id = str(st.get("solver"))
            break
    if primary_id is not None:
        for ev in evidence:
            if ev.source_operator == primary_id:
                return ev
    for ev in evidence:
        if (ev.effect is not None and ev.p is not None
                and ev.n is not None):
            return ev
    for ev in evidence:
        if ev.p is not None:
            return ev
    return None


def _operator_versions(steps: List[Dict[str, Any]]) -> Dict[str, str]:
    """Best-effort operator provenance: each executed solver id → version.

    Real per-solver versions aren't tracked in the registry yet, so we
    record ``"unknown"`` (honest placeholder).  The set of ids is the
    credibility-relevant part (which operators produced the numbers).
    """
    versions: Dict[str, str] = {}
    for st in steps or []:
        sid = st.get("solver")
        if sid and sid not in versions:
            versions[str(sid)] = "unknown"
    return versions


_CODER_SYSTEM = (
    "You are a verification coder inside a scientific discovery pipeline.\n"
    "Write ONE Python script that reads input.csv in the current directory, "
    "tests the provided hypothesis as directly as possible, and writes "
    "verify_metrics.json in the current directory.\n"
    "The JSON MUST contain numeric keys when available: effect, effect_type, "
    "p, n, ci_low, ci_high, plus a short method string. Use pandas/numpy/"
    "scipy/statsmodels only if installed; otherwise use standard-library "
    "fallbacks. Do not read files outside the current directory. Do not call "
    "the network. Return STRICT JSON: {\"code\": \"...python...\"}."
)


def _json_to_metrics_csv(json_path: Path, csv_path: Path) -> None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    row = data if isinstance(data, dict) else {}
    pd.DataFrame([row]).to_csv(csv_path, index=False)


def _run_coder_verify(plan_task: str,
                      hypothesis: Hypothesis,
                      in_csv: Path,
                      work_dir: Path,
                      coder_steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate and execute a constrained verification script.

    Returns a runner-shaped dict with ``steps`` so the existing extraction
    machinery can consume the produced metrics CSV.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    local_csv = work_dir / "input.csv"
    if local_csv.resolve() != in_csv.resolve():
        local_csv.write_bytes(in_csv.read_bytes())

    if not llm_client.is_available():
        return {
            "ok": False,
            "n_steps_ok": 0,
            "n_steps_failed": 1,
            "failed_steps": [{
                "error_code": "CODER_LLM_UNAVAILABLE",
                "error_phase": "planning",
                "error": "LLM is not configured for __coder__ verify fallback",
                "next_action_hint": "configure the LLM or choose an operator-supported verification.",
            }],
            "steps": [],
        }

    hint_bits = []
    for st in coder_steps or []:
        hint = st.get("coder_hint") or st.get("description") or ""
        if hint:
            hint_bits.append(str(hint))
    user = json.dumps({
        "task": plan_task,
        "hypothesis": hypothesis.to_dict(),
        "input_csv": "input.csv",
        "data_columns": list(pd.read_csv(in_csv, nrows=5).columns),
        "coder_hints": hint_bits,
        "required_output": "verify_metrics.json",
    }, ensure_ascii=False, indent=2)

    try:
        raw = llm_client.chat_json(
            _CODER_SYSTEM, user,
            max_tokens=1800, temperature=0.0, stage="verify_coder")
        code = str((raw or {}).get("code") or "").strip()
    except Exception as exc:
        return {
            "ok": False,
            "n_steps_ok": 0,
            "n_steps_failed": 1,
            "failed_steps": [{
                "error_code": "CODER_LLM_FAILED",
                "error_phase": "planning",
                "error": f"coder LLM failed: {type(exc).__name__}: {exc}",
                "next_action_hint": "retry or use an operator-supported task.",
            }],
            "steps": [],
        }

    if code.startswith("```"):
        code = code.strip("`")
        if code.lower().startswith("python"):
            code = code[6:].lstrip()
    script = work_dir / "verify_coder.py"
    script.write_text(code, encoding="utf-8")
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "n_steps_ok": 0,
            "n_steps_failed": 1,
            "failed_steps": [{
                "error_code": "CODER_TIMEOUT",
                "error_phase": "computation",
                "error": "verify coder script timed out",
                "next_action_hint": "simplify the verification code or use a bounded operator.",
            }],
            "steps": [],
        }

    metrics_json = work_dir / "verify_metrics.json"
    metrics_csv = work_dir / "verify_metrics.csv"
    if metrics_json.is_file():
        _json_to_metrics_csv(metrics_json, metrics_csv)

    outputs: Dict[str, Any] = {
        "script": str(script),
        "stdout_txt": proc.stdout,
        "stderr_txt": proc.stderr,
    }
    if metrics_json.is_file():
        outputs["verify_metrics_json"] = str(metrics_json)
    if metrics_csv.is_file():
        outputs["verify_metrics_csv"] = str(metrics_csv)

    step = {
        "name": "01___coder__",
        "solver": "__coder__",
        "primary": True,
        "outputs": outputs,
        "returncode": proc.returncode,
    }
    ok = proc.returncode == 0 and metrics_json.is_file()
    return {
        "ok": ok,
        "n_steps_ok": 1 if ok else 0,
        "n_steps_failed": 0 if ok else 1,
        "failed_steps": ([] if ok else [{
            "error_code": "CODER_OUTPUT_MISSING",
            "error_phase": "computation",
            "error": "verify coder did not produce verify_metrics.json",
            "next_action_hint": "inspect verify_coder.py stdout/stderr and retry.",
        }]),
        "steps": [step],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run(task: str,
        df: pd.DataFrame,
        hypothesis: Hypothesis,
        public_bb: PublicBlackboard,
        private_bb: PrivateBlackboard,
        *,
        run_dir: Optional[Path] = None,
        bus: Optional[SignalBus] = None,
        seed: Optional[int] = None,
        use_llm: bool = True) -> VerifyResult:
    """Verify one hypothesis by reusing the operator pipeline.

    Never raises — always returns a populated :class:`VerifyResult` whose
    ``status`` is ``"ok"`` / ``"inconclusive"`` / ``"error"``.  The result is
    also written to ``private_bb`` under :data:`PRIVATE_BB_KEY` and produced
    artifacts are registered.
    """
    dataset_hash = _dataset_hash(df)
    base = VerifyResult(
        hypothesis_id=getattr(hypothesis, "id", None),
        seed=seed,
        dataset_hash=dataset_hash,
    )

    def _finish(result: VerifyResult) -> VerifyResult:
        """Persist to the private blackboard + register artifacts (best
        effort; persistence failure must not turn a good run into a crash)."""
        try:
            private_bb.put(PRIVATE_BB_KEY, result.to_dict(),
                           producer=PRODUCER, seed=seed)
        except Exception:
            pass
        for ap in result.artifact_paths:
            try:
                private_bb.register_artifact(ap, f"verify_stage artifact")
            except Exception:
                pass
        return result

    try:
        # --- materialise the input CSV (like solve_task does) ---------
        if run_dir is not None:
            run_dir = Path(run_dir)
            artifacts_dir = run_dir / "artifacts"
            out_root = run_dir / "pipeline_output"
            owns_tmp = False
        else:
            run_dir = Path(tempfile.mkdtemp(prefix="verify_stage_"))
            artifacts_dir = run_dir / "artifacts"
            out_root = run_dir / "pipeline_output"
            owns_tmp = True
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        in_csv = artifacts_dir / "input.csv"
        try:
            df.to_csv(in_csv, index=False)
        except Exception as e:
            base.status = "error"
            base.error = _error_dict(
                "INPUT_CSV_WRITE_FAILED", "input_resolution",
                f"failed to persist input dataframe: {type(e).__name__}: {e}",
                "ensure the dataframe is serialisable to CSV.")
            if bus is not None:
                bus.emit_error(PRODUCER, **base.error,
                               hypothesis_id=base.hypothesis_id)
            return _finish(base)

        # --- plan (Stage 3 operator selection only) -------------------
        # Local import keeps the discovery package importable even if the
        # heavy planner import chain is unavailable in some environments.
        from operator_agent.planner import plan_pipeline
        from operator_pipeline.run_spec import build_pipeline_from_spec
        from operator_pipeline.runner import execute_pipeline

        plan_task = _augment_task(task, hypothesis)
        try:
            plan = plan_pipeline(plan_task, df, skip_hypothesis=True)
        except Exception as e:
            base.status = "error"
            base.error = _error_dict(
                "PLAN_RAISED", "planning",
                f"plan_pipeline raised: {type(e).__name__}: {e}",
                "inspect the planner; verify_stage degrades to error.")
            if bus is not None:
                bus.emit_error(PRODUCER, **base.error,
                               hypothesis_id=base.hypothesis_id)
            return _finish(base)

        if not plan.ok:
            base.status = "error"
            base.error = _error_dict(
                "PLAN_FAILED", "planning",
                plan.error or "planner produced no executable steps",
                "LLM may be unavailable, or no operator matched the task; "
                "supervisor should report + suggest a fix.",
                {"invalid_solver_ids": list(plan.invalid_solver_ids)})
            if bus is not None:
                bus.emit_error(PRODUCER, **base.error,
                               hypothesis_id=base.hypothesis_id)
            return _finish(base)

        # --- build steps ----------------------------------------------
        # PlanResult.spec["steps"] is the raw (operator + coder) list as
        # emitted by the planner.  build_pipeline_from_spec() can only
        # materialise *operator* steps — it raises KeyError when it sees
        # ``__coder__`` (since that is not a registry id).  The planner
        # already separated them for us, so feed it only the operator
        # steps.  If the planner picked a pure-coder plan, there is no
        # operator path to extract verify numbers from → inconclusive
        # rather than a hard error.
        op_steps_raw = list(plan.operator_steps or [])
        if not op_steps_raw:
            run_result = _run_coder_verify(
                plan_task, hypothesis, in_csv, run_dir / "coder_verify",
                list(plan.coder_steps or []))
            run_steps = run_result.get("steps", []) or []
            names = [str(s.get("solver") or s.get("name") or "__coder__")
                     for s in run_steps]
            base.operator_versions = {"__coder__": "llm_generated"}
            found, raw_findings, artifacts = _extract_numbers(run_steps, run_dir)
            base.artifact_paths = artifacts
            base.effect = found.get("effect")
            base.effect_type = found.get("effect_type")
            base.p = found.get("p")
            n_val = found.get("n")
            base.n = int(n_val) if n_val is not None else None
            ci_low = found.get("ci_low")
            ci_high = found.get("ci_high")
            if ci_low is not None and ci_high is not None:
                base.ci = [ci_low, ci_high]
            base.extra = {
                "raw_findings": raw_findings,
                "n_steps_ok": run_result.get("n_steps_ok"),
                "n_steps_failed": run_result.get("n_steps_failed"),
                "step_order": names,
                "pipeline_ok": run_result.get("ok"),
                "primary_operator": "__coder__",
                "coder_fallback": True,
            }
            usable = (base.p is not None) or (base.effect is not None)
            failed = run_result.get("failed_steps") or []
            if not usable:
                if failed and not run_result.get("ok", False):
                    first = failed[0]
                    base.status = "error"
                    base.error = _error_dict(
                        first.get("error_code") or "CODER_FAILED",
                        first.get("error_phase") or "computation",
                        first.get("error") or "coder verification failed",
                        first.get("next_action_hint")
                        or "inspect the generated verify_coder.py.")
                    if bus is not None:
                        bus.emit_error(PRODUCER, **base.error,
                                       hypothesis_id=base.hypothesis_id)
                    return _finish(base)
                base.status = "inconclusive"
                base.extra["inconclusive_reason"] = (
                    "coder fallback ran but no usable effect/p was extracted")
                if bus is not None:
                    bus.emit_done(PRODUCER, status="inconclusive",
                                  hypothesis_id=base.hypothesis_id)
                return _finish(base)
            base.status = "ok"
            if bus is not None:
                bus.emit_done(PRODUCER, status="ok", effect=base.effect,
                              p=base.p, n=base.n,
                              hypothesis_id=base.hypothesis_id,
                              coder_fallback=True)
            return _finish(base)

        try:
            steps_objs, names = build_pipeline_from_spec(
                {"steps": op_steps_raw})
        except Exception as e:
            base.status = "error"
            base.error = _error_dict(
                "BUILD_FAILED", "build",
                f"failed to materialize plan: {type(e).__name__}: {e}",
                "the plan spec was not executable; supervisor should "
                "report + suggest a fix.")
            if bus is not None:
                bus.emit_error(PRODUCER, **base.error,
                               hypothesis_id=base.hypothesis_id)
            return _finish(base)

        # --- execute --------------------------------------------------
        try:
            run_result = execute_pipeline(steps_objs, in_csv, out_root,
                                          use_llm=use_llm,
                                          task_description=plan_task)
        except Exception as e:
            base.status = "error"
            base.error = _error_dict(
                "EXECUTE_RAISED", "computation",
                f"execute_pipeline raised: {type(e).__name__}: {e}",
                "an operator/coder crashed; supervisor should report + "
                "suggest a fix.")
            if bus is not None:
                bus.emit_error(PRODUCER, **base.error,
                               hypothesis_id=base.hypothesis_id)
            return _finish(base)

        run_steps = run_result.get("steps", []) or []
        base.operator_versions = _operator_versions(run_steps)

        # --- extract numbers ------------------------------------------
        found, raw_findings, artifacts = _extract_numbers(run_steps, run_dir)
        base.artifact_paths = artifacts

        # ADR-0008 — per-operator extraction.  Populated unconditionally;
        # it never crosses operator boundaries even when the legacy
        # flat-scan above would.
        evidence = _extract_evidence_per_operator(run_steps)
        base.evidence_per_operator = evidence
        primary = _pick_primary(evidence, run_steps)

        # Single-value legacy fields:
        # - if a primary Evidence is identified, use it (ADR-0008 §3);
        # - else fall back to the legacy flat-merge (preserves prior
        #   behaviour for non-bio workflows / operators not in the
        #   extractor table).
        if primary is not None:
            base.effect = primary.effect
            base.effect_type = primary.effect_kind
            base.p = primary.p
            base.n = primary.n
        else:
            base.effect = found.get("effect")
            base.effect_type = found.get("effect_type")
            base.p = found.get("p")
            n_val = found.get("n")
            base.n = int(n_val) if n_val is not None else None

        ci_low = found.get("ci_low")
        ci_high = found.get("ci_high")
        if ci_low is not None and ci_high is not None:
            base.ci = [ci_low, ci_high]

        base.extra = {
            "raw_findings": raw_findings,
            "n_steps_ok": run_result.get("n_steps_ok"),
            "n_steps_failed": run_result.get("n_steps_failed"),
            "step_order": names,
            "pipeline_ok": run_result.get("ok"),
            "primary_operator": (primary.source_operator
                                 if primary is not None else None),
        }

        usable = (base.p is not None) or (base.effect is not None)
        failed = run_result.get("failed_steps") or []

        if not usable:
            # Ran but produced nothing usable.  If the pipeline failed
            # outright (operator/coder error), that's an EXECUTION error
            # per §3.1; otherwise it merely ran weak → inconclusive.
            if failed and not run_result.get("ok", False):
                first = failed[0]
                base.status = "error"
                base.error = _error_dict(
                    first.get("error_code") or "OPERATOR_FAILED",
                    first.get("error_phase") or "computation",
                    first.get("error") or "operator execution failed",
                    first.get("next_action_hint")
                    or "supervisor should report + suggest a fix.",
                    {"failed_steps": failed})
                if bus is not None:
                    bus.emit_error(PRODUCER, **base.error,
                                   hypothesis_id=base.hypothesis_id)
                return _finish(base)
            base.status = "inconclusive"
            base.extra["inconclusive_reason"] = (
                "no usable effect/p extracted from executed outputs")
            if bus is not None:
                bus.emit_done(PRODUCER, status="inconclusive",
                              hypothesis_id=base.hypothesis_id)
            return _finish(base)

        # Usable numbers but underpowered → inconclusive (not an error).
        if base.n is not None and base.n < MIN_USABLE_N:
            base.status = "inconclusive"
            base.extra["inconclusive_reason"] = (
                f"n={base.n} below MIN_USABLE_N={MIN_USABLE_N}")
            if bus is not None:
                bus.emit_done(PRODUCER, status="inconclusive",
                              hypothesis_id=base.hypothesis_id)
            return _finish(base)

        base.status = "ok"
        if bus is not None:
            bus.emit_done(PRODUCER, status="ok", effect=base.effect,
                          p=base.p, n=base.n,
                          hypothesis_id=base.hypothesis_id)
        return _finish(base)

    except Exception as e:  # last-resort guard: never raise (§3.1)
        base.status = "error"
        base.error = _error_dict(
            "UNCAUGHT_EXCEPTION", "computation",
            f"verify_stage unexpected failure: {type(e).__name__}: {e}",
            "this is a bug in verify_stage; inspect the traceback.")
        if bus is not None:
            try:
                bus.emit_error(PRODUCER, **base.error,
                               hypothesis_id=base.hypothesis_id)
            except Exception:
                pass
        return _finish(base)
