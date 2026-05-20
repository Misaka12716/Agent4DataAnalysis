"""Robust per-step mapping resolver for the pipeline demo.

For each pipeline step we need to produce a ``ColumnMapping`` whose
``mapping`` dict is **complete enough** that the solver's ``run`` will
not raise ``KeyError``.  That means every required role declared in the
solver's ``SolverContract.roles`` must appear in the dict, with a value
that:

  - for column roles: actually exists in the input DataFrame columns
  - for ``Role.NUMERIC_LIST`` / ``Role.ITEM_GROUP``: a list of strings
    drawn from the columns
  - for ``Role.PARAMS``: a JSON-shaped object that fits the solver's
    expectations (e.g. ``reference_ranges = {col: {"low":..,"high":..}}``)

Strategy (in order):

  1. **User override** wins for any role it specifies.
  2. **LLM** (if configured + enabled) is asked to fill the *remaining*
     required roles, given the DataFrame profile and the contract.
  3. **Enhanced rule-based** mapper (with looser categorical heuristics)
     fills anything still missing.
  4. We **validate** the final mapping; columns not present in df are
     dropped, and a structured ``rationale`` records every decision.

中文说明
========
**三层解析**：用户/规划 JSON 的 override →（可选）映射 LLM → 规则兜底。
``Role.PARAMS`` 且键名以 ``_csv`` / ``_path`` 结尾时，若值为占位符字符串，
在 ``_coerce_value`` 里置空，让下游用 autowire 或 solver 默认路径。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from distillation.software1_solver.contract import (
    ColumnMapping,
    Role,
    RoleSpec,
    SolverContract,
)
from distillation.software1_solver.profiler import profile_to_text

from distillation.software1_pipeline_demo_app import llm_client


# ---------------------------------------------------------------------------
# Enhanced rule-based fallback
# ---------------------------------------------------------------------------

_ID_HINTS = ("id", "patient", "subject", "uid", "uuid", "case", "record")
_DATETIME_HINTS = ("date", "time", "timestamp")
_TARGET_HINTS = ("target", "label", "y_true", "outcome", "event")


def _name_score(name: str, hints) -> int:
    n = str(name).lower()
    return sum(1 for h in hints if h in n)


def _is_numeric_dtype(dtype: str) -> bool:
    return any(k in dtype for k in ("int", "float", "uint"))


def _is_binary(c) -> bool:
    if c["n_unique"] != 2:
        return False
    if not _is_numeric_dtype(c["dtype"]):
        return False
    if "top_values" in c:
        try:
            vs = sorted([v[0] for v in c["top_values"]])
            return vs in ([0, 1], [0.0, 1.0])
        except Exception:
            return True
    return True


def _categorical_candidates(cols, used) -> List[Dict[str, Any]]:
    """Return columns ranked by 'how categorical' they look.

    Strategy: prefer non-numeric with low cardinality; otherwise numeric
    with very low cardinality.  Excludes already-used columns.
    """
    avail = [c for c in cols if c["name"] not in used]
    non_num = [c for c in avail
               if not _is_numeric_dtype(c["dtype"])
               and c["n_unique"] >= 2 and c["n_unique"] <= 30]
    non_num.sort(key=lambda c: c["n_unique"])
    num_low = [c for c in avail
               if _is_numeric_dtype(c["dtype"])
               and 2 <= c["n_unique"] <= 10
               and c not in non_num]
    num_low.sort(key=lambda c: c["n_unique"])
    return non_num + num_low


def _numeric_columns(cols, used) -> List[Dict[str, Any]]:
    return [c for c in cols
            if c["name"] not in used and _is_numeric_dtype(c["dtype"])]


def _id_column(cols, used) -> Optional[str]:
    for c in cols:
        if c["name"] in used:
            continue
        if c.get("looks_like_id") or _name_score(c["name"], _ID_HINTS) > 0:
            return c["name"]
    # fallback: first non-numeric column
    for c in cols:
        if c["name"] in used:
            continue
        if not _is_numeric_dtype(c["dtype"]):
            return c["name"]
    return None


def _datetime_column(cols, used) -> Optional[str]:
    for c in cols:
        if c["name"] in used:
            continue
        if "datetime" in c["dtype"]:
            return c["name"]
        if _name_score(c["name"], _DATETIME_HINTS) > 0:
            return c["name"]
    return None


def _binary_target(cols, used, hints=_TARGET_HINTS) -> Optional[str]:
    cand = [c for c in cols if c["name"] not in used and _is_binary(c)]
    cand.sort(key=lambda c: -_name_score(c["name"], hints))
    return cand[0]["name"] if cand else None


def _ttoe(cols, used) -> Optional[str]:
    for c in cols:
        if c["name"] in used:
            continue
        if (_is_numeric_dtype(c["dtype"]) and
                _name_score(c["name"], ("time", "duration", "tte", "days",
                                         "weeks", "months")) > 0):
            return c["name"]
    return None


def _event_indicator(cols, used) -> Optional[str]:
    for c in cols:
        if c["name"] in used:
            continue
        if (_is_binary(c) and _name_score(c["name"], ("event", "death",
                                                        "censor")) > 0):
            return c["name"]
    return None


def _p_value(cols, used) -> Optional[str]:
    for c in cols:
        if c["name"] in used:
            continue
        if (_is_numeric_dtype(c["dtype"]) and
                c.get("min", 0) is not None and c.get("min") >= 0 and
                c.get("max", 1) is not None and c.get("max") <= 1):
            return c["name"]
    return None


def _resolve_role_rule_based(role: Role, spec: RoleSpec,
                              cols: List[Dict[str, Any]],
                              used: set) -> Any:
    if role == Role.ID:
        return _id_column(cols, used)
    if role == Role.DATETIME:
        return _datetime_column(cols, used)
    if role == Role.BINARY_TARGET:
        return _binary_target(cols, used)
    if role == Role.NUMERIC_TARGET:
        nums = _numeric_columns(cols, used)
        nums = [c for c in nums if _name_score(c["name"], _TARGET_HINTS) > 0]
        return nums[0]["name"] if nums else None
    if role == Role.TIME_TO_EVENT:
        return _ttoe(cols, used)
    if role == Role.EVENT_INDICATOR:
        return _event_indicator(cols, used)
    if role == Role.P_VALUE:
        return _p_value(cols, used)
    if role == Role.NUMERIC:
        nums = _numeric_columns(cols, used)
        nums = [c for c in nums if c["n_unique"] > 5]
        return nums[0]["name"] if nums else None
    if role == Role.CATEGORICAL:
        cand = _categorical_candidates(cols, used)
        return cand[0]["name"] if cand else None
    if role == Role.ORDINAL:
        nums = _numeric_columns(cols, used)
        ord_cand = [c for c in nums if 2 < c["n_unique"] <= 15]
        return ord_cand[0]["name"] if ord_cand else None
    if role == Role.TEXT:
        for c in cols:
            if c["name"] in used:
                continue
            if (not _is_numeric_dtype(c["dtype"]) and c["n_unique"] > 5):
                return c["name"]
        return None
    if role in (Role.NUMERIC_LIST, Role.ITEM_GROUP):
        nums = _numeric_columns(cols, used)
        return [c["name"] for c in nums]
    if role == Role.PARAMS:
        return None
    return None


# ---------------------------------------------------------------------------
# LLM-driven planner
# ---------------------------------------------------------------------------

LLM_SYSTEM = (
    "You are a clinical-data-science assistant that maps a solver's "
    "abstract column-role contract to actual columns in a user's CSV. "
    "You always respond with a single JSON object — no markdown fences, "
    "no extra commentary — whose top-level keys exactly match the role "
    "keys requested by the user.  Use only column names that appear in "
    "the supplied DataFrame profile.  When a role asks for a list of "
    "columns, return a JSON array of column names.  When a role is of "
    "type 'params', return a JSON object that fits the solver's static "
    "parameter shape."
)


def _params_schema_hint(solver_name: str) -> str:
    """Per-solver hints for PARAMS-typed roles, in JSON Schema-ish prose."""
    if solver_name == "reference_range_flag":
        return (
            'reference_ranges: object {"<lab_col>": '
            '{"low": <number>, "high": <number>}, ...} covering only '
            "columns that look like clinical lab values; use reasonable "
            "adult reference intervals when known.  Examples: "
            'WBC ×10^9/L 4-10, Hemoglobin g/L 130-175 male / 115-150 '
            "female (use 115-175 if mixed), Platelet ×10^9/L 100-300, "
            "ALT U/L 7-40, Creatinine μmol/L 60-110, Sodium mmol/L "
            "135-145, Potassium mmol/L 3.5-5.0, Glucose fasting mmol/L "
            "3.9-6.1.  Match keys against ACTUAL column names from the "
            "profile (case + suffix sensitive)."
        )
    if solver_name == "consistency_check":
        return (
            "regex_rules: object {col: regex}; range_rules: object "
            '{col: [low, high]}; allowed_values: object {col: [v1, v2]}. '
            "All optional — return {} for any rule type you cannot "
            "confidently propose from the profile alone."
        )
    return ""


def _per_solver_role_hints(solver_name: str) -> str:
    if solver_name == "chi_square_independence":
        return ("This test needs TWO categorical columns.  Choose columns "
                "with low cardinality (n_unique typically ≤ 10).  "
                "Continuous numeric columns are not appropriate; if no "
                "reasonable categorical pair exists, return null for "
                "row_col / col_col so the runner can flag it instead of "
                "computing nonsense.")
    if solver_name in ("welch_t_test", "mann_whitney_u_test"):
        return ("group_col MUST be a binary 0/1 (or two-level) column.  "
                "value_col is the continuous outcome.")
    if solver_name in ("oneway_anova", "kruskal_wallis"):
        return ("group_col is categorical (≥2 levels), value_col is "
                "continuous numeric.")
    if solver_name == "cox_regression":
        return ("event_col MUST be 0/1; time_col is positive numeric "
                "duration; covariates are numeric.")
    if solver_name == "panss_factor_score":
        return ("positive_items: P1..P7 columns; negative_items: "
                "N1..N7; general_items: G1..G16.  Use the EXACT column "
                "names visible in the profile.")
    return ""


def _build_user_prompt(profile: Dict[str, Any], contract: SolverContract,
                        already: Dict[str, Any]) -> str:
    role_lines = []
    example: Dict[str, Any] = {}
    for k, spec in contract.roles.items():
        line = (f"  - {k} (role={spec.role.value}"
                + (", optional" if spec.optional else ", REQUIRED")
                + f"): {spec.description}")
        if spec.group_hint:
            line += f"  [hint: {spec.group_hint}]"
        role_lines.append(line)
        if spec.role in (Role.NUMERIC_LIST, Role.ITEM_GROUP):
            example[k] = ["col_a", "col_b"]
        elif spec.role == Role.PARAMS:
            example[k] = {}
        else:
            example[k] = "<one column name>"

    parts = [
        f"## Solver `{contract.name}` ({contract.capability})",
        contract.description,
        "",
        "## Required roles",
        *role_lines,
    ]
    role_hint = _per_solver_role_hints(contract.name)
    if role_hint:
        parts += ["", "## Solver-specific role guidance", role_hint]
    schema_hint = _params_schema_hint(contract.name)
    if schema_hint:
        parts += ["", "## PARAMS shape hint", schema_hint]
    if contract.static_params:
        parts += [
            "",
            "## Default static parameters (do NOT echo unless overriding)",
            json.dumps(contract.static_params, ensure_ascii=False, indent=2),
        ]
    if already:
        parts += [
            "",
            "## Already-resolved roles (keep these values; only fill the rest)",
            json.dumps(already, ensure_ascii=False, indent=2),
        ]
    parts += [
        "",
        "## DataFrame profile",
        profile_to_text(profile),
        "",
        "## Output rules",
        "- Return ONE JSON object whose keys are exactly the role keys "
        "above.  No commentary.  No markdown fences.",
        "- Use only column names that actually appear in the profile.",
        "- For roles you cannot confidently fill from this data, return "
        "null for that key (the runner will then mark the step as "
        "unrunnable rather than crash).",
        "- For NUMERIC_LIST / ITEM_GROUP roles, return a JSON array "
        "(possibly empty if not applicable).",
        "",
        "## Output template (placeholders are illustrative)",
        json.dumps(example, ensure_ascii=False, indent=2),
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _looks_like_real_path(val: Any, role_key: str) -> bool:
    """Heuristic: does this value look like an actual file path?

    Mirrors ``runner._looks_like_real_path``.  Used for ``PARAMS`` roles
    ending in ``_csv`` / ``_path`` to reject placeholder echoes such as
    ``"gene_matrix_csv"``, ``"bundled MSigDB Hallmark 2020"``, etc.

    中文：与 runner 侧逻辑对齐，避免映射 LLM 把「说明文字」当路径塞进 solver。
    """
    if not isinstance(val, str) or not val.strip():
        return False
    s = val.strip()
    if s == role_key:
        return False
    placeholders = {"gene_matrix_csv", "expression_matrix_csv",
                     "sample_groups_csv", "annotation_csv",
                     "deg_table_csv", "linkage_csv",
                     "cluster_assignments_csv", "pca_scores_csv",
                     "pca_loadings_csv", "pca_variance_csv",
                     "enrichment_csv", "gene_set_db_path",
                     "<path>", "<file>", "..."}
    if s in placeholders:
        return False
    has_sep = ("/" in s) or ("\\" in s)
    if has_sep:
        return True
    from pathlib import Path as _P
    if _P(s).is_file():
        return True
    return False


def _coerce_value(role: Role, val: Any, role_key: str = "") -> Any:
    if role in (Role.NUMERIC_LIST, Role.ITEM_GROUP):
        if isinstance(val, str):
            return [val]
        if isinstance(val, list):
            return [str(x) for x in val if x is not None]
        return None
    if role == Role.PARAMS:
        # PARAMS may be any JSON-shaped value: dict, list, str (e.g. a
        # file path), number, bool.  We only reject None.
        if val is None:
            return None
        # Special-case path-shaped roles: reject placeholder strings so
        # the solver falls back to its built-in default (e.g. bundled
        # GMT).  Detected by suffix of role_key.
        if isinstance(val, str) and role_key.endswith(("_csv", "_path")):
            if not _looks_like_real_path(val, role_key):
                return None
        return val
    if val is None:
        return None
    return str(val)


def _validate_value(role: Role, val: Any, df_cols: List[str],
                     role_key: str = "") -> bool:
    if val is None:
        return False
    if role == Role.PARAMS:
        # Path-shaped PARAMS: must look like a real path
        if isinstance(val, str) and role_key.endswith(("_csv", "_path")):
            return _looks_like_real_path(val, role_key)
        # Any other JSON-shaped value is accepted (dict, list, number, bool)
        return True
    if role in (Role.NUMERIC_LIST, Role.ITEM_GROUP):
        return isinstance(val, list) and len(val) > 0 and all(
            isinstance(c, str) and c in df_cols for c in val)
    return isinstance(val, str) and val in df_cols


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class ResolveResult:
    mapping: Dict[str, Any]
    rationale: List[str] = field(default_factory=list)
    source: str = "rule_based"            # rule_based | llm | manual | mixed
    llm_attempted: bool = False
    llm_ok: bool = False
    llm_error: Optional[str] = None
    missing_required: List[str] = field(default_factory=list)


def resolve_mapping(df: pd.DataFrame,
                     profile: Dict[str, Any],
                     contract: SolverContract,
                     user_override: Optional[Dict[str, Any]] = None,
                     use_llm: bool = False) -> ResolveResult:
    df_cols: List[str] = [str(c) for c in df.columns]
    out: Dict[str, Any] = {}
    rationale: List[str] = []
    sources: set = set()

    # Step 1 — user override (already cast / validated by caller; we
    # still validate to surface obvious typos).
    if user_override:
        for k, v in user_override.items():
            spec = contract.roles.get(k)
            if spec is None:
                # forward through anyway — solver may use it, but warn.
                out[k] = v
                rationale.append(f"[manual] {k}={v!r} (not in contract roles)")
                sources.add("manual")
                continue
            cv = _coerce_value(spec.role, v, role_key=k)
            if cv is None:
                rationale.append(f"[manual] {k}: dropped invalid value {v!r}")
                continue
            if _validate_value(spec.role, cv, df_cols, role_key=k):
                out[k] = cv
                rationale.append(f"[manual] {k}={cv!r}")
                sources.add("manual")
            else:
                rationale.append(
                    f"[manual] {k}: value {cv!r} not present in DataFrame "
                    "columns; ignoring")

    needed = [k for k, spec in contract.roles.items() if k not in out]

    llm_attempted = False
    llm_ok = False
    llm_error: Optional[str] = None

    # Step 2 — LLM fills required + optional roles still missing.
    if use_llm and needed and llm_client.is_available():
        llm_attempted = True
        try:
            prompt = _build_user_prompt(profile, contract, already=out)
            llm_dict = llm_client.chat_json(LLM_SYSTEM, prompt,
                                             max_tokens=1400,
                                             temperature=0.0)
            for k in needed:
                if k not in llm_dict:
                    continue
                spec = contract.roles[k]
                cv = _coerce_value(spec.role, llm_dict[k], role_key=k)
                if cv is None:
                    rationale.append(f"[llm] {k}: invalid shape from LLM "
                                      f"({llm_dict[k]!r})")
                    continue
                if _validate_value(spec.role, cv, df_cols, role_key=k):
                    out[k] = cv
                    rationale.append(f"[llm] {k}={cv!r}")
                    sources.add("llm")
                else:
                    rationale.append(
                        f"[llm] {k}: value {cv!r} not in DataFrame; rejected")
            llm_ok = True
        except llm_client.LLMError as e:
            llm_error = str(e)
            rationale.append(f"[llm] failed: {e}")

    # Step 3 — rule-based fallback for whatever LLM didn't fill.
    used = set()
    for v in out.values():
        if isinstance(v, str):
            used.add(v)
        elif isinstance(v, list):
            used.update(x for x in v if isinstance(x, str))

    for k in needed:
        if k in out:
            continue
        spec = contract.roles[k]
        try:
            picked = _resolve_role_rule_based(spec.role, spec,
                                               profile["columns"], used)
        except Exception as e:
            picked = None
            rationale.append(f"[rule_based] {k}: error {e!r}")
        if picked is None:
            if not spec.optional:
                rationale.append(
                    f"[rule_based] {k} ({spec.role.value}): NO MATCH "
                    f"(required, will likely error in solver)")
            else:
                rationale.append(
                    f"[rule_based] {k}: no match (optional, skipping)")
            continue
        if _validate_value(spec.role, picked, df_cols, role_key=k):
            out[k] = picked
            rationale.append(f"[rule_based] {k}={picked!r}")
            sources.add("rule_based")
            if isinstance(picked, str):
                used.add(picked)
            elif isinstance(picked, list):
                used.update(picked)
        else:
            rationale.append(
                f"[rule_based] {k}: candidate {picked!r} failed validation")

    missing_required = [
        k for k, spec in contract.roles.items()
        if not spec.optional and k not in out
    ]

    if "manual" in sources and (sources - {"manual"}):
        source = "mixed"
    elif "llm" in sources and (sources - {"llm"}):
        source = "mixed"
    elif sources:
        source = next(iter(sources))
    else:
        source = "manual" if user_override else "rule_based"

    return ResolveResult(
        mapping=out,
        rationale=rationale,
        source=source,
        llm_attempted=llm_attempted,
        llm_ok=llm_ok,
        llm_error=llm_error,
        missing_required=missing_required,
    )


def to_column_mapping(res: ResolveResult) -> ColumnMapping:
    return ColumnMapping(
        mapping=dict(res.mapping),
        rationale="\n".join(res.rationale),
        source=res.source,
    )
