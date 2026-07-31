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

from operator_library.contract import (
    ColumnMapping,
    Role,
    RoleSpec,
    SolverContract,
)
from operator_library.profiler import profile_to_text, profile_to_text_wide

from operator_pipeline import llm_client


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
    # Skip the synthetic __row_id__ during the first two passes so we
    # don't shadow a genuine primary key (e.g. patient_id) that already
    # exists in the table.
    for c in cols:
        if c["name"] in used or c["name"] == "__row_id__":
            continue
        if c.get("looks_like_id") or _name_score(c["name"], _ID_HINTS) > 0:
            return c["name"]
    # Fallback 1: first non-numeric column (textual IDs are usually
    # categoricals with high cardinality).
    for c in cols:
        if c["name"] in used or c["name"] == "__row_id__":
            continue
        if not _is_numeric_dtype(c["dtype"]):
            return c["name"]
    # Fallback 2: the runner injects a universal ``__row_id__`` column
    # right after read_csv, so we can always cite it as a last-resort
    # identifier (0..n-1 row index).  Avoids spurious id_col-missing
    # mapping failures on tabular benchmarks like QRData / RDAB that
    # contain no natural primary key column.  Only chosen when neither
    # a hint-matching nor a textual column is available.
    for c in cols:
        if c["name"] == "__row_id__":
            return "__row_id__"
    # Last resort: even if the runner failed to inject it (e.g. older
    # codepaths or testing harnesses), report __row_id__ so downstream
    # solvers receive a stable sentinel; they should themselves be
    # tolerant of "__row_id__" → fallback to df.index.
    return "__row_id__"


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
                              used: set,
                              task_description: str = "") -> Any:
    if role == Role.ID:
        return _id_column(cols, used)
    if role == Role.DATETIME:
        return _datetime_column(cols, used)
    if role == Role.BINARY_TARGET:
        # Tier 1 — generic name hints + binarity check.
        picked = _binary_target(cols, used)
        if picked is not None:
            return picked
        # Tier 2 — task-text grounding: when the prompt mentions a
        # specific binary column (e.g. "compare event rates between
        # treated and control" / "risk of death"), pick that column
        # if it's binary in the table.  Same idea as NUMERIC_TARGET
        # tier-2 above; rescues risk_difference_ci / logistic
        # regression bindings on tables whose binary cols don't match
        # _TARGET_HINTS (e.g. ``death``, ``stroke``, ``hospitalized``,
        # ``infected``, ``churn``, ``defaulted``).
        text = (task_description or "").lower()
        if text:
            cand = [c for c in cols
                    if c["name"] not in used
                    and _is_binary(c)
                    and c["name"] and c["name"].lower() in text]
            if cand:
                return cand[0]["name"]
        return None
    if role == Role.NUMERIC_TARGET:
        nums = _numeric_columns(cols, used)
        # Tier 1 — generic name hints (target/label/y_true/outcome/event).
        hinted = [c for c in nums
                  if _name_score(c["name"], _TARGET_HINTS) > 0]
        if hinted:
            return hinted[0]["name"]
        # Tier 2 — task-text grounding.  When the prompt mentions a
        # specific column name (e.g. "predict house price" or "model
        # the loan amount"), pick that column as the regression target
        # even though the generic _TARGET_HINTS list doesn't cover the
        # domain word.  This rescues RDAB/RADAR-style tasks where the
        # outcome column is named e.g. ``price``, ``charges``, ``mpg``
        # — none of which would match a generic hint list.
        text = (task_description or "").lower()
        if text and nums:
            cand = [c for c in nums
                    if c["name"] and c["name"].lower() in text]
            if cand:
                # Prefer the highest-cardinality numeric column when
                # multiple names appear (heuristic: the ``y`` is rarely
                # the indicator/dummy and tends to be high-cardinality).
                cand.sort(key=lambda c: c.get("n_unique", 0), reverse=True)
                return cand[0]["name"]
        # Tier 3 — last-resort: use the highest-cardinality numeric
        # column (well-defined, deterministic; logged in rationale).
        if nums:
            nums_sorted = sorted(nums,
                                  key=lambda c: c.get("n_unique", 0),
                                  reverse=True)
            return nums_sorted[0]["name"]
        return None
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
                        already: Dict[str, Any],
                        task_text: str = "") -> str:
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
    if task_text:
        parts += [
            "",
            "## Task description (REFERENCE ONLY for column semantics)",
            "The task description below is provided ONLY to help you understand what the columns mean.",
            "Do NOT try to complete the task; your ONLY job is to map column names to solver slots.",
            "",
            task_text[:2000] if len(task_text) > 2000 else task_text,
        ]
    parts += [
        "",
        "## DataFrame profile",
        profile_to_text_wide(profile),
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
        "- If the solver asks for an ID role (e.g. ``id_col``, "
        "``subject_id``, ``patient_id``) but the dataset has NO natural "
        "identifier column, return the literal string ``\"__row_id__\"`` "
        "for that key.  The runner injects an integer 0..n-1 row index "
        "column with that exact name into every loaded csv, so it is "
        "guaranteed to exist and to uniquely identify rows.  This is "
        "always preferable to returning null when the only obstacle is "
        "a missing PK; null should be reserved for roles whose meaning "
        "(e.g. time_col, event_col) genuinely cannot be inferred.",
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
                     use_llm: bool = False,
                     task_description: str = "") -> ResolveResult:
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

    # Wide-table guard: when the input table has many columns, pre-fill
    # list-type roles (NUMERIC_LIST / ITEM_GROUP) via the rule-based mapper
    # BEFORE the LLM call.  Otherwise the mapping LLM is asked to enumerate
    # hundreds/thousands of column names for that role, blows past
    # max_tokens, and returns a truncated JSON → "unexpected end of JSON
    # input".  Rule-based returns "all numeric columns", which is exactly
    # the intended semantics (the consuming solver caps the list itself).
    if len(df_cols) > 60:
        used_pre: set = set()
        for v in out.values():
            if isinstance(v, str):
                used_pre.add(v)
            elif isinstance(v, list):
                used_pre.update(x for x in v if isinstance(x, str))
        for k in list(needed):
            spec = contract.roles[k]
            if spec.role not in (Role.NUMERIC_LIST, Role.ITEM_GROUP):
                continue
            try:
                picked = _resolve_role_rule_based(
                    spec.role, spec, profile["columns"], used_pre,
                    task_description=task_description)
            except Exception:
                picked = None
            if picked and _validate_value(spec.role, picked, df_cols, role_key=k):
                out[k] = picked
                rationale.append(
                    f"[rule_based:wide] {k}=<{len(picked)} numeric cols> "
                    "(pre-filled on wide table to avoid LLM column enumeration)")
                sources.add("rule_based")
        needed = [k for k in needed if k not in out]

    llm_attempted = False
    llm_ok = False
    llm_error: Optional[str] = None

    # V8 Phase 2 §3.5: roles for which the LLM EXPLICITLY returned null
    # (i.e. "I considered this optional role and decided it does not
    # apply").  rule-based fallback below skips these so we do not
    # over-bind an optional role like ``column_stat.weight_col`` to a
    # type-matching but semantically-wrong column (e.g. binding
    # ``weight_col`` to "TOTAL B" just because it is numeric).
    llm_explicit_null: set = set()

    # Step 2 — LLM fills required + optional roles still missing.
    if use_llm and needed and llm_client.is_available():
        llm_attempted = True
        try:
            prompt = _build_user_prompt(profile, contract, already=out, task_text=task_description)
            llm_dict = llm_client.chat_json(LLM_SYSTEM, prompt,
                                             max_tokens=1400,
                                             temperature=0.0,
                                             stage="mapping")
            for k in needed:
                if k not in llm_dict:
                    continue
                spec = contract.roles[k]
                raw_val = llm_dict[k]
                cv = _coerce_value(spec.role, raw_val, role_key=k)
                if cv is None:
                    rationale.append(f"[llm] {k}: invalid shape from LLM "
                                      f"({raw_val!r})")
                    # Only treat as "LLM explicitly said no" when the
                    # raw value really was a JSON null (None) AND the
                    # role is optional.  Required-but-null is a real
                    # error and SHOULD still trigger rule-based fallback.
                    if raw_val is None and spec.optional:
                        llm_explicit_null.add(k)
                        rationale.append(
                            f"[llm] {k} explicitly null → "
                            f"skipping rule_based fallback (optional role)"
                        )
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
        if k in llm_explicit_null:
            # LLM 主动判定该 optional 角色 = null；不再 rule-based 兜底，
            # 避免把语义不该绑的列硬绑上去（典型场景：column_stat 的
            # weight_col 在非频数表数据上应保持空）。
            continue
        spec = contract.roles[k]
        try:
            picked = _resolve_role_rule_based(
                spec.role, spec, profile["columns"], used,
                task_description=task_description)
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
