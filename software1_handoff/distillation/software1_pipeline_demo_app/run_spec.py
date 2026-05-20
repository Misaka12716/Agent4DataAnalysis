"""Parse HTTP JSON specs into ``PipelineStep`` lists.

中文说明
========
把规划器 / UI 返回的 JSON（``steps`` 数组）实例化为 ``PipelineStep``
列表：对每步 ``make_solver(sid, params)``，并把 ``mapping`` 塞进 step
供 runner 在真正执行前再解析。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from distillation.software1_solver import PipelineStep

from distillation.software1_pipeline_demo_app.registry import make_solver


def _step_mapping(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if "mapping" not in step:
        return None
    m = step["mapping"]
    if m is None:
        return None
    if isinstance(m, dict) and not m:
        return None
    return dict(m)


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
        solver = make_solver(sid, params)

        mo = _step_mapping(s)#是否有用户对齐的列名称

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
