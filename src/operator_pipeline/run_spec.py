"""Parse HTTP JSON specs into ``PipelineStep`` lists.

中文说明
========
把规划器 / UI 返回的 JSON（``steps`` 数组）实例化为 ``PipelineStep``
列表：对每步 ``make_solver(sid, params)``，并把 ``mapping`` 塞进 step
供 runner 在真正执行前再解析。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from operator_library import PipelineStep

from operator_pipeline.registry import make_solver


def _step_mapping(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if "mapping" not in step:
        return None
    m = step["mapping"]
    if m is None:
        return None
    if isinstance(m, dict) and not m:
        return None
    return dict(m)


def _promote_static_params(sid: str,
                            params: Dict[str, Any],
                            mapping_obj: Optional[Dict[str, Any]]
                            ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Move solver static_params keys out of ``mapping`` and into ``params``.

    Why
    ---
    Planner LLMs frequently emit JSON like::

        {"solver": "data_imputation",
         "mapping": {"numeric_columns": [...], "method": "drop_row"},
         "params":  {}}

    or::

        {"solver": "distribution_histogram",
         "mapping": {"numeric_columns": ["height"], "n_bins": 1,
                     "bin_range": [180, 185], "weight_col": "num_of_adults"}}

    Here ``method`` / ``n_bins`` / ``bin_range`` are the solver's
    ``static_params`` (constructor kwargs), NOT ColumnMapping roles.  The
    mapping engine silently forwards them with a ``(not in contract roles)``
    rationale, but the solver instance was already built from the *empty*
    ``params`` dict and uses defaults — so the planner's intent is lost
    (e.g. Q17 ended up using ``method=median`` despite the planner asking
    for ``method=drop_row``; Q10 fell back to 20 default bins ignoring
    the explicit n_bins=1 / bin_range=[180,185]).

    Fix: inspect the solver's ``contract.static_params``, harvest any
    matching keys from ``mapping`` into ``params`` (without overriding
    keys the planner already put in ``params``), then drop them from
    ``mapping`` so the downstream ColumnMapping stays clean.

    Returns ``(promoted_params, cleaned_mapping)``.
    """
    if not mapping_obj:
        return dict(params), mapping_obj
    # Probe the solver to inspect its contract.  ``params`` may be empty
    # here; the probe is cheap and we'll rebuild with the real params.
    try:
        probe = make_solver(sid, dict(params))
    except Exception:
        return dict(params), mapping_obj
    contract = getattr(probe, "contract", None)
    if contract is None:
        return dict(params), mapping_obj
    static_keys = set((getattr(contract, "static_params", {}) or {}).keys())
    if not static_keys:
        return dict(params), mapping_obj
    out_params = dict(params)
    out_mapping = dict(mapping_obj)
    moved: List[str] = []
    for k in list(out_mapping.keys()):
        if k not in static_keys:
            continue
        # params wins on conflict (caller is explicit there).  Even
        # when params already has the key, we still drop it from
        # mapping so the downstream ColumnMapping doesn't carry a
        # stale value that confuses solver.run().
        if k not in out_params:
            out_params[k] = out_mapping[k]
        out_mapping.pop(k, None)
        moved.append(k)
    if moved and not out_mapping:
        out_mapping = None  # type: ignore[assignment]
    return out_params, out_mapping


def build_pipeline_from_spec(spec: Dict[str, Any]) -> Tuple[List[PipelineStep], List[str]]:
    if not isinstance(spec, dict) or "steps" not in spec:
        raise ValueError("spec must be a JSON object with a 'steps' array")
    raw_steps = spec["steps"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("'steps' must be a non-empty array")

    names: List[str] = []
    for i, s in enumerate(raw_steps):
        if not isinstance(s, dict) or "solver" not in s:
            raise ValueError(f"steps[{i}] must contain a 'solver' string")
        sid = str(s["solver"]).strip()
        names.append(str(s.get("name") or f"{i+1:02d}_{sid}"))

    out: List[PipelineStep] = []
    for i, s in enumerate(raw_steps):
        sid = str(s["solver"]).strip()
        params = s.get("params") if isinstance(s.get("params"), dict) else {}
        mo = _step_mapping(s)#是否有用户对齐的列名称
        # Promote planner-misplaced static_params from mapping into params
        # (Q17 method='drop_row', Q10 n_bins=1/bin_range, etc.).
        params, mo = _promote_static_params(sid, params, mo)
        solver = make_solver(sid, params)

        src = s.get("from", "previous")#从哪里拿文件
        if src in (None, "previous", "auto"):
            input_from = None
        elif src in ("initial", "__initial__"):
            input_from = "__initial__"
        elif src in ("step", "__step__"):
            idx = s.get("step_index")
            if idx is None:
                raise ValueError(f"steps[{i}] uses from=step but step_index missing")
            idx = int(idx)
            if idx < 0 or idx >= len(names):
                raise ValueError(f"steps[{i}] step_index {idx} out of range")
            # Defensive guard: LLM planners frequently emit
            #   input_source="step:N" where N == this step's own index
            # (self-reference) or N == a future step (forward-reference),
            # which breaks the dependency graph.  Quietly downgrade:
            #   - first step → read from __initial__
            #   - later step → fall through to "previous"
            # The Planner's mistake gets silently fixed and the pipeline
            # can still execute.
            if idx >= i:
                input_from = "__initial__" if i == 0 else None
            else:
                input_from = names[idx]
        else:
            raise ValueError(f"steps[{i}] invalid 'from': {src!r}")

        csv_key = s.get("csv_key", "auto")
        if csv_key is None:
            csv_key = "auto"
        csv_key = str(csv_key)

        out.append(PipelineStep(
            name=names[i],
            solver=solver,
            mapping_override=mo,
            input_from=input_from,
            input_output_key=csv_key,
        ))

    return out, names
