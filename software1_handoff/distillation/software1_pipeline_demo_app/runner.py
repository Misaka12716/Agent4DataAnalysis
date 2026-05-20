"""Per-step orchestrator that calls our mapping engine before each solver.

This wraps ``distillation.software1_solver.Pipeline``'s execution loop
without modifying it: we resolve a complete ``ColumnMapping`` for every
step using :mod:`mapping_engine` (LLM + enhanced rules + user override)
and pass it to the underlying ``PipelineStep`` as
``mapping_override``.

中文说明
========
demo / agent 共用的「真正干活」层：每一步先 ``resolve_mapping``，再跑算子。
对 ``*_csv`` / ``*_path`` 类 PARAMS 会先 ``_autowire_path_params``，把上一步
产物接到当前步，避免 LLM 填 ``"gene_matrix_csv"`` 这种占位串当路径。
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from distillation.software1_solver import PipelineStep
from distillation.software1_solver.profiler import profile_df

from distillation.software1_pipeline_demo_app import mapping_engine


def _pick_default_csv_key(outputs: Dict[str, Any]):
    for k, v in outputs.items():
        if isinstance(v, str) and v.endswith(".csv"):
            return k
    for k, v in outputs.items():
        if isinstance(v, (str, Path)):
            return k
    return None


def _looks_like_real_path(val: Any, role_key: str) -> bool:
    """Heuristic: does this value look like an actual file path the
    runtime can use, vs. a placeholder echoed by the LLM?

    Treated as NOT a real path:
      - non-string
      - empty string
      - equals the role key (placeholder echo)
      - matches a known role-name pattern (gene_matrix_csv, deg_table_csv, ...)
      - has no path separator AND does not exist on disk

    中文：区分「真路径」与规划模型复读字段名；假路径交给 autowire 或
    solver 默认（如打包的 GMT），避免 ``FileNotFoundError: 'gene_matrix_csv'``。
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
    # If it has a separator OR ends in .csv/.tsv/.gmt/.txt AND exists, accept.
    has_sep = ("/" in s) or ("\\" in s)
    if has_sep or Path(s).is_file():
        return True
    # Bare filename without separator AND not on disk → treat as placeholder.
    return False


def _autowire_path_params(contract, input_csv: Path,
                            outputs_by_step: Dict[str, Dict[str, Any]],
                            user_override: Optional[Dict[str, Any]],
                            ) -> Dict[str, Any]:
    """For ``PARAMS`` roles whose key ends in ``_csv`` or ``_path``,
    auto-wire the value from prior step outputs.  Resolution order
    (highest priority first):

      1. **Previous step's same-named output** wins.  Pipelines exist
         to chain solver outputs to next solver inputs; if a prior step
         published a value for this key, that fresh value is what the
         downstream solver should consume — even if the planner LLM
         pre-filled an older / unrelated path.
      2. user_override is honored if it looks like a real file path
         AND no prior step published the same key.
      3. For ``*_csv`` keys with neither (1) nor (2), fall back to
         ``input_csv`` (the runner-resolved primary previous CSV).
      4. ``*_path`` keys (e.g. ``gene_set_db_path``) are NOT
         backfilled from input_csv — solvers should use their built-in
         defaults (e.g. bundled GMT).

    Returns a *new* override dict that replaces the caller's
    user_override before handing off to ``mapping_engine``.

    中文（优先级摘要）
    ------------------
    1. 上一步若已产出同名 output key（如 ``gene_matrix_csv``），**必须**用它，
       防止 LLM/user_override 里仍是 probe 矩阵而下游需要 gene 矩阵。
    2. user_override 里看似路径且磁盘可解析时才保留。
    3. ``*_csv`` 仍缺则退回当前步的 ``input_csv``；``*_path`` 不强行填输入表，
       以便算子用内置默认库文件。
    """
    from distillation.software1_solver.contract import Role
    auto: Dict[str, Any] = {}
    invalidated_user: List[str] = []
    for k, spec in contract.roles.items():
        if spec.role != Role.PARAMS:
            continue
        is_csv = k.endswith("_csv")
        is_path = k.endswith("_path")
        if not (is_csv or is_path):
            continue

        # Priority 1: a prior step published the same key — that wins.
        upstream: Optional[str] = None
        for step_name, outs in outputs_by_step.items():
            if k in outs and isinstance(outs[k], (str, Path)):
                v = str(outs[k])
                if _looks_like_real_path(v, k):
                    upstream = v
        if upstream is not None:
            auto[k] = upstream
            # if the planner's user_override differs, mark it as
            # invalidated so we don't merge a stale value back in.
            if user_override and k in user_override:
                if str(user_override[k]) != upstream:
                    invalidated_user.append(k)
            continue

        # Priority 2: user_override (if it looks real)
        if user_override and k in user_override:
            uv = user_override[k]
            if _looks_like_real_path(uv, k):
                continue   # leave merge to take it
            invalidated_user.append(k)

        # Priority 3: fall back to current input_csv for *_csv keys
        if is_csv and input_csv is not None and Path(input_csv).is_file():
            auto[k] = str(input_csv)
        # *_path keys with no prior output and no real user_override:
        # leave the role unset so the solver uses its bundled default.

    merged: Dict[str, Any] = dict(auto)
    if user_override:
        merged.update({k: v for k, v in user_override.items()
                        if v not in (None, "") and k not in invalidated_user})
    # auto values always win over the (filtered) user values:
    merged.update(auto)
    import os
    if os.environ.get("S1_DEBUG_AUTOWIRE"):
        print(f"[autowire] solver={contract.name}")
        print(f"[autowire]   user_override keys={list((user_override or {}).keys())}")
        print(f"[autowire]   invalidated_user_paths={invalidated_user}")
        print(f"[autowire]   outputs_by_step keys={list(outputs_by_step.keys())}")
        for sn, outs in outputs_by_step.items():
            print(f"[autowire]     {sn} → {list(outs.keys())}")
        print(f"[autowire]   auto={list(auto.keys())}")
        print(f"[autowire]   merged={merged}")
    return merged


def execute_pipeline(steps: List[PipelineStep],
                      initial_input_csv: Path,
                      output_dir: Path,
                      use_llm: bool = False) -> Dict[str, Any]:
    """Run the steps sequentially, resolving each step's mapping with
    LLM + enhanced rules + the user's override (if any).

    Returns a dict shaped like the runtime ``manifest`` consumed by the
    result template.

    中文：顺序执行 ``PipelineStep``；每步读取上一步主 CSV + autowire 路径参数；
    ``use_llm`` 为真时未填角色由 ``mapping_engine`` 调用 LLM 补全。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    initial_csv = Path(initial_input_csv)
    last_csv = initial_csv
    outputs_by_step: Dict[str, Dict[str, Any]] = {}
    step_records: List[Dict[str, Any]] = []
    overall_ok = True

    for step in steps:
        # 1) resolve input csv (mirrors software1_solver.Pipeline logic).
        if step.input_from is None:
            input_csv = last_csv
        elif step.input_from == "__initial__":
            input_csv = initial_csv
        else:
            prev = outputs_by_step.get(step.input_from)
            if prev is None:
                step_records.append({
                    "name": step.name,
                    "solver": step.solver.contract.name,
                    "input_csv": "(unresolved)",
                    "mapping_source": "n/a",
                    "mapping": {},
                    "rationale": [],
                    "status": "error",
                    "error": f"input_from={step.input_from!r} not yet executed",
                    "outputs": {},
                    "missing_required": [],
                })
                overall_ok = False
                continue
            key = step.input_output_key#看有没有对应的键值对
            if key == "auto":
                key = _pick_default_csv_key(prev) or ""
            if key not in prev:
                step_records.append({
                    "name": step.name,
                    "solver": step.solver.contract.name,
                    "input_csv": "(unresolved)",
                    "mapping_source": "n/a",
                    "mapping": {},
                    "rationale": [],
                    "status": "error",
                    "error": f"csv_key {key!r} not in outputs of "
                              f"{step.input_from!r} (have: {list(prev)})",
                    "outputs": {},
                    "missing_required": [],
                })
                overall_ok = False
                continue
            input_csv = Path(prev[key])

        # 2) load + profile + resolve mapping.
        try:
            df = pd.read_csv(input_csv)
        except Exception as e:
            step_records.append({
                "name": step.name,
                "solver": step.solver.contract.name,
                "input_csv": str(input_csv),
                "mapping_source": "n/a",
                "mapping": {},
                "rationale": [],
                "status": "error",
                "error": f"failed to read csv: {type(e).__name__}: {e}",
                "outputs": {},
                "missing_required": [],
            })
            overall_ok = False
            continue

        profile = profile_df(df)#获取这份文件的基本信息
        user_override = step.mapping_override or None
        # auto-wire path-shaped PARAMS from prior step outputs / the
        # resolved input_csv, before handing off to the mapper.
        effective_override = _autowire_path_params(
            contract=step.solver.contract,
            input_csv=input_csv,
            outputs_by_step=outputs_by_step,
            user_override=user_override,
        )#保证输入输出的规范性
        resolve = mapping_engine.resolve_mapping(
            df=df,
            profile=profile,
            contract=step.solver.contract,
            user_override=effective_override or None,
            use_llm=use_llm,
        )#让用户对应列或者大模型对应列或者规则对应列
        cm = mapping_engine.to_column_mapping(resolve)#对应不了则报错，但是不崩溃

        # 3) run solver.
        step_dir = output_dir / step.name
        step_dir.mkdir(parents=True, exist_ok=True)
        outputs: Dict[str, Any] = {}
        status = "ok"
        error = None
        if resolve.missing_required:
            status = "error"
            error = ("missing required role(s): "
                     + ", ".join(resolve.missing_required))
            overall_ok = False
        else:
            try:
                outputs = step.solver.run(df=df, mapping=cm,
                                           output_dir=step_dir)#跑算子
            except BaseException as e:
                status = "error"
                error = f"{type(e).__name__}: {e}"
                outputs = {}
                overall_ok = False

        if status == "ok":
            key = _pick_default_csv_key(outputs)#结果也规范化输出
            if key is not None:
                last_csv = Path(outputs[key])

        outputs_by_step[step.name] = outputs

        step_records.append({
            "name": step.name,
            "solver": step.solver.contract.name,
            "input_csv": str(input_csv),
            "mapping_source": resolve.source,
            "mapping": resolve.mapping,
            "rationale": resolve.rationale,
            "llm_attempted": resolve.llm_attempted,
            "llm_ok": resolve.llm_ok,
            "llm_error": resolve.llm_error,
            "missing_required": resolve.missing_required,
            "status": status,
            "error": error,
            "outputs": {
                k: (str(v) if isinstance(v, Path) else v)
                for k, v in outputs.items()
                if isinstance(v, (str, int, float, dict, list, Path))
                or v is None
            },
        })

    return {"ok": overall_ok, "steps": step_records}
