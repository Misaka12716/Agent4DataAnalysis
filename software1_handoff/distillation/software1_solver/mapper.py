"""Column-role mapper.

Two implementations:

1. **rule_based**:  fast, offline, no LLM.  Uses dtype + name keywords +
   profile statistics.  Good for ~80% of clinical tables; explicit and
   debuggable.

2. **llm**:  pluggable LLM call (any ``Callable[[str], str]``).  Builds a
   prompt with the profile + contract, expects the LLM to return JSON.
   Use this when rule-based mapping is ambiguous (e.g. PANSS items P1..P7
   need to be partitioned into 3 factor groups).

Both return a ``ColumnMapping`` dataclass.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from .contract import ColumnMapping, Role, RoleSpec, SolverContract
from .profiler import profile_to_text


LLMCallable = Callable[[str], str]


# ---------------------------------------------------------------------------
# Rule-based
# ---------------------------------------------------------------------------
_KEYWORD_HINTS: Dict[Role, List[str]] = {
    Role.ID: ["id", "patient", "subject", "uid", "uuid", "case", "record"],
    Role.BINARY_TARGET: ["target", "label", "y_true", "y", "outcome",
                          "event", "is_", "_flag", "positive"],
    Role.NUMERIC_TARGET: ["target", "y", "outcome", "score"],
    Role.DATETIME: ["date", "time", "timestamp", "datetime"],
    Role.TIME_TO_EVENT: ["time_to", "duration", "tte", "days", "weeks", "months"],
    Role.EVENT_INDICATOR: ["event", "death", "censor", "occur", "happened"],
    Role.P_VALUE: ["p_value", "pval", "pvalue", "p_val"],
    Role.TEXT: ["text", "note", "comment", "description"],
}


def _name_score(name: str, hints: List[str]) -> int:
    nl = str(name).lower()
    return sum(1 for h in hints if h in nl)


def _is_numeric_dtype(dtype: str) -> bool:
    return any(k in dtype for k in ("int", "float", "uint"))


def _is_binary_col(col_info: Dict[str, Any]) -> bool:
    if col_info["n_unique"] != 2:
        return False
    if not _is_numeric_dtype(col_info["dtype"]):
        return False
    if "top_values" in col_info:
        vals = sorted([v[0] for v in col_info["top_values"]])
        return vals == [0, 1] or vals == [0.0, 1.0]
    return True


def _find_one_column(profile_cols: List[Dict[str, Any]],
                     spec: RoleSpec, used: set) -> Optional[str]:
    role = spec.role
    candidates = [c for c in profile_cols if c["name"] not in used]

    if role == Role.ID:
        for c in candidates:
            if c.get("looks_like_id") or _name_score(c["name"], _KEYWORD_HINTS[Role.ID]) > 0:
                return c["name"]
        return None

    if role == Role.BINARY_TARGET:
        scored = [(c, _name_score(c["name"], _KEYWORD_HINTS[Role.BINARY_TARGET]))
                  for c in candidates if _is_binary_col(c)]
        scored = [(c, s) for c, s in scored if s > 0]
        scored.sort(key=lambda x: -x[1])
        return scored[0][0]["name"] if scored else None

    if role == Role.NUMERIC_TARGET:
        scored = [(c, _name_score(c["name"], _KEYWORD_HINTS[Role.NUMERIC_TARGET]))
                  for c in candidates if _is_numeric_dtype(c["dtype"])]
        # require at least one keyword hint — otherwise we'd silently
        # grab the first numeric column and call it the target.
        scored = [(c, s) for c, s in scored if s > 0]
        scored.sort(key=lambda x: -x[1])
        return scored[0][0]["name"] if scored else None

    if role == Role.DATETIME:
        for c in candidates:
            if "datetime" in c["dtype"]:
                return c["name"]
            if _name_score(c["name"], _KEYWORD_HINTS[Role.DATETIME]) > 0:
                return c["name"]
        return None

    if role == Role.TIME_TO_EVENT:
        for c in candidates:
            if (_is_numeric_dtype(c["dtype"]) and
                    _name_score(c["name"], _KEYWORD_HINTS[Role.TIME_TO_EVENT]) > 0):
                return c["name"]
        return None

    if role == Role.EVENT_INDICATOR:
        for c in candidates:
            if (_is_binary_col(c) and
                    _name_score(c["name"], _KEYWORD_HINTS[Role.EVENT_INDICATOR]) > 0):
                return c["name"]
        return None

    if role == Role.P_VALUE:
        for c in candidates:
            if (_is_numeric_dtype(c["dtype"]) and
                    c.get("min", 0) >= 0 and c.get("max", 1) <= 1 and
                    _name_score(c["name"], _KEYWORD_HINTS[Role.P_VALUE]) > 0):
                return c["name"]
        # fallback: any numeric in [0,1]
        for c in candidates:
            if (_is_numeric_dtype(c["dtype"]) and
                    c.get("min") is not None and c["min"] >= 0 and
                    c.get("max") is not None and c["max"] <= 1):
                return c["name"]
        return None

    if role == Role.NUMERIC:
        for c in candidates:
            if _is_numeric_dtype(c["dtype"]) and c["n_unique"] > 5:
                return c["name"]
        return None

    if role == Role.CATEGORICAL:
        for c in candidates:
            if not _is_numeric_dtype(c["dtype"]) and c["n_unique"] <= 20:
                return c["name"]
        return None

    if role == Role.ORDINAL:
        for c in candidates:
            if (_is_numeric_dtype(c["dtype"]) and 2 < c["n_unique"] <= 15):
                return c["name"]
        return None

    if role == Role.TEXT:
        for c in candidates:
            if (not _is_numeric_dtype(c["dtype"]) and
                    c["n_unique"] > 0.5 * (c.get("n_unique", 0) + 1)):
                return c["name"]
        return None

    return None


def _find_list_columns(profile_cols: List[Dict[str, Any]],
                       spec: RoleSpec, used: set) -> List[str]:
    """Resolve NUMERIC_LIST / ITEM_GROUP roles."""
    out: List[str] = []
    for c in profile_cols:
        if c["name"] in used:
            continue
        if spec.role in (Role.NUMERIC_LIST, Role.ITEM_GROUP):
            if _is_numeric_dtype(c["dtype"]):
                out.append(c["name"])
    return out


def map_columns_rule_based(profile: Dict[str, Any],
                            contract: SolverContract) -> ColumnMapping:
    """Resolve role -> column-name(s) using static heuristics."""
    cols = profile["columns"]
    used: set = set()
    mapping: Dict[str, Any] = {}
    log: List[str] = []

    # Resolve scalar roles first (more specific), then list roles last.
    # Role.PARAMS is non-column config; we skip mapping it here entirely.
    param_keys = [k for k, v in contract.roles.items()
                  if v.role == Role.PARAMS]
    list_keys = [k for k, v in contract.roles.items()
                 if v.role in (Role.NUMERIC_LIST, Role.ITEM_GROUP)]
    scalar_keys = [k for k in contract.roles
                   if k not in list_keys and k not in param_keys]

    for k in param_keys:
        spec = contract.roles[k]
        if not spec.optional:
            log.append(f"REQUIRED PARAMS role {k!r} must come from "
                        f"mapping override")

    for k in scalar_keys:
        spec = contract.roles[k]
        picked = _find_one_column(cols, spec, used)
        if picked is None and not spec.optional:
            log.append(f"NO MATCH for required role {k!r} ({spec.role.value})")
        if picked is not None:
            mapping[k] = picked
            used.add(picked)
            log.append(f"{k} ({spec.role.value}) -> {picked!r}")

    for k in list_keys:
        spec = contract.roles[k]
        picked = _find_list_columns(cols, spec, used)
        if not picked and not spec.optional:
            log.append(f"NO MATCH for required role {k!r} ({spec.role.value})")
        mapping[k] = picked
        used.update(picked)
        log.append(f"{k} ({spec.role.value}) -> {picked!r}")

    return ColumnMapping(mapping=mapping,
                         rationale="\n".join(log),
                         source="rule_based")


# ---------------------------------------------------------------------------
# LLM-based
# ---------------------------------------------------------------------------
_LLM_PROMPT_TEMPLATE = """\
You are a data-science assistant.  Given a DataFrame's compact schema
profile and a solver's required column-role contract, return a JSON
object that maps each role-key to the actual column name (or list of
column names) in the DataFrame.

# DataFrame profile
{profile_text}

# Solver: {solver_name}  ({capability})
{description}

# Roles to fill
{roles_text}

# Static parameters (already known, for context)
{params_text}

Output exactly one JSON object on a single line, no commentary, no
markdown fences.  Example: {example}
"""


def _build_llm_prompt(profile: Dict[str, Any],
                      contract: SolverContract) -> str:
    role_lines = []
    example: Dict[str, Any] = {}
    for k, spec in contract.roles.items():
        line = f"  - {k}: role={spec.role.value}"
        if spec.optional:
            line += " (optional)"
        line += f" - {spec.description}"
        if spec.group_hint:
            line += f"  [hint: {spec.group_hint}]"
        role_lines.append(line)
        if spec.role in (Role.NUMERIC_LIST, Role.ITEM_GROUP):
            example[k] = ["col1", "col2"]
        else:
            example[k] = "some_column_name"

    return _LLM_PROMPT_TEMPLATE.format(
        profile_text=profile_to_text(profile),
        solver_name=contract.name,
        capability=contract.capability,
        description=contract.description,
        roles_text="\n".join(role_lines),
        params_text=json.dumps(contract.static_params, ensure_ascii=False),
        example=json.dumps(example, ensure_ascii=False),
    )


def map_columns_llm(profile: Dict[str, Any],
                    contract: SolverContract,
                    llm: LLMCallable) -> ColumnMapping:
    """Map using an LLM callable.  ``llm(prompt: str) -> json_string``."""
    prompt = _build_llm_prompt(profile, contract)
    raw = llm(prompt)
    # tolerate fenced blocks
    raw_stripped = re.sub(r"^```(json)?|```$", "", raw.strip(),
                           flags=re.MULTILINE).strip()
    try:
        mapping_dict = json.loads(raw_stripped)
    except json.JSONDecodeError as e:
        return ColumnMapping(
            mapping={},
            rationale=f"LLM JSON parse failed: {e}\nraw: {raw[:500]}",
            source="llm",
        )
    return ColumnMapping(mapping=mapping_dict,
                         rationale=f"LLM raw response:\n{raw}",
                         source="llm")
