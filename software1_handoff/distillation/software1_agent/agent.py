"""Software 1 agent: NL task + CSV → planned pipeline → executed run.

程序 API 示例::

    from distillation.software1_agent import solve_task
    result = solve_task(task="...", csv_path="data.csv",
                       output_dir="runs/exp1")
    print(result.ok, result.manifest_path)

与网页 demo 共用同一套编排（registry → mapping_engine → runner），
区别仅在于「谁决定管线」：这里由 **规划 LLM** 产出 JSON 流水线，再执行。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from distillation.software1_pipeline_demo_app import llm_client
from distillation.software1_pipeline_demo_app.run_spec import (
    build_pipeline_from_spec,
)
from distillation.software1_pipeline_demo_app.runner import execute_pipeline

from distillation.software1_agent.planner import PlanResult, plan_pipeline


@dataclass
class SolveResult:
    ok: bool
    run_dir: Path
    manifest_path: Path
    plan: PlanResult
    steps: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


def solve_task(task: str,
                csv_path: str | Path,
                output_dir: str | Path,
                use_llm_mapping: bool = True,
                planner_max_tokens: int = 1800,
                run_id: Optional[str] = None,
                step_overrides: Optional[List[Dict[str, Any]]] = None,
                ) -> SolveResult:
    """对 ``csv_path`` 执行「规划 + 落盘 + 执行」全流程。

    落盘结构::

        <output_dir>/<run_id>/
            input.csv               # 副本，保证可复现
            plan.json               # 规划 LLM 的 JSON + rationale
            pipeline_output/<step_name>/...  # 每步产物
            manifest.json           # 汇总

    参数
    ----
    step_overrides
        按**步骤下标**与规划结果对齐的 mapping 补丁列表；某步若不需要
        可传 ``None`` / ``{}``。合并规则：**调用方键值覆盖 planner**，
        专门防小模型漏写 ``group_a``、文件路径等导致管线失败；高层
        仍由 LLM 选算子与顺序。
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)

    run_id = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "_" + uuid.uuid4().hex[:8])
    run_dir = Path(output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    in_path = run_dir / "input.csv"
    in_path.write_bytes(csv_path.read_bytes())

    try:
        df = pd.read_csv(in_path)
    except Exception as e:
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "ok": False,
            "error": f"failed to read csv: {type(e).__name__}: {e}",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return SolveResult(
            ok=False, run_dir=run_dir, manifest_path=manifest_path,
            plan=PlanResult(spec={"steps": []}, rationale="", raw={},
                            error="csv unreadable"),
            error="csv unreadable",
        )

    plan = plan_pipeline(task=task, df=df,
                          max_tokens=planner_max_tokens,
                          temperature=0.0)
    # 将 step_overrides 并入各步 mapping：小模型常漏写 mapping，由
    # 宿主脚本把关键参数「钉死」，仍以 planner 的 steps 顺序为准。
    if step_overrides:
        steps_list = plan.spec.get("steps") or []
        for i, ov in enumerate(step_overrides):
            if i >= len(steps_list) or not ov:
                continue
            step = steps_list[i]
            existing = step.get("mapping") or {}
            merged = dict(existing)
            merged.update(ov)   # overrides win
            step["mapping"] = merged
        plan.spec["steps"] = steps_list

    (run_dir / "plan.json").write_text(
        json.dumps({
            "task": task,
            "rationale": plan.rationale,
            "spec": plan.spec,
            "valid_solver_ids": plan.valid_solver_ids,
            "invalid_solver_ids": plan.invalid_solver_ids,
            "error": plan.error,
            "raw": plan.raw,
            "llm": {
                "model": (llm_client.get_config().model
                          if llm_client.is_available() else None),
                "use_llm_mapping": use_llm_mapping,
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not plan.ok:
        manifest = {
            "run_id": run_id, "ok": False,
            "task": task, "rationale": plan.rationale,
            "error": plan.error or "planner produced no executable steps",
            "spec": plan.spec, "steps": [],
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False,
                                             indent=2),
                                  encoding="utf-8")
        return SolveResult(ok=False, run_dir=run_dir,
                            manifest_path=manifest_path, plan=plan,
                            steps=[], error=plan.error)

    try:
        steps_objs, names = build_pipeline_from_spec(plan.spec)
    except Exception as e:
        manifest = {
            "run_id": run_id, "ok": False, "task": task,
            "rationale": plan.rationale,
            "error": (f"failed to materialize plan: "
                      f"{type(e).__name__}: {e}"),
            "spec": plan.spec, "steps": [],
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False,
                                             indent=2),
                                  encoding="utf-8")
        return SolveResult(ok=False, run_dir=run_dir,
                            manifest_path=manifest_path, plan=plan,
                            steps=[], error=str(e))

    out_root = run_dir / "pipeline_output"
    run_result = execute_pipeline(steps_objs, in_path, out_root,
                                    use_llm=use_llm_mapping)

    files: List[Dict[str, Any]] = [{"path": "input.csv",
                                      "size": in_path.stat().st_size}]
    if out_root.exists():
        for p in sorted(out_root.rglob("*")):
            if p.is_file():
                files.append({
                    "path": str(p.relative_to(run_dir)).replace("\\", "/"),
                    "size": p.stat().st_size,
                })

    manifest = {
        "run_id": run_id,
        "ok": run_result.get("ok", False),
        "task": task,
        "rationale": plan.rationale,
        "spec": plan.spec,
        "steps": run_result.get("steps", []),
        "files": files,
        "step_order": names,
        "use_llm_mapping": use_llm_mapping,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False,
                                         indent=2, default=str),
                              encoding="utf-8")
    return SolveResult(
        ok=run_result.get("ok", False),
        run_dir=run_dir,
        manifest_path=manifest_path,
        plan=plan,
        steps=run_result.get("steps", []),
        error=run_result.get("error"),
    )
