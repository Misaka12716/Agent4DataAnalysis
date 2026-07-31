"""Per-step orchestrator that calls our mapping engine before each solver.

This wraps ``operator_library.Pipeline``'s execution loop
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

from operator_library import PipelineStep
from operator_library.profiler import profile_df

from operator_pipeline import mapping_engine
from operator_pipeline.error_codes import OperatorInputError


def _pick_default_csv_key(outputs: Dict[str, Any]):
    for k, v in outputs.items():
        if isinstance(v, str) and v.endswith(".csv"):
            return k
    for k, v in outputs.items():
        if isinstance(v, (str, Path)):
            return k
    return None


# LLM-friendly aliases for csv_key references in operator chains.  When
# the planner emits ``csv_key='filled_csv'`` we silently rewrite it to
# the canonical key the upstream solver actually published.  All values
# are lowercase canonical identifiers; matching is case-insensitive.
#
# Add a new entry only when (a) the alias is unambiguous (one canonical
# key on the receiving side) AND (b) at least one observed planner
# failure used the alias.  Anything fuzzier should be left to the
# explicit error message so the operator surface stays self-documenting.
_CSV_KEY_ALIASES: Dict[str, str] = {
    # data_imputation publishes "imputed_csv" — planner sometimes calls
    # it "filled_csv" / "imputed" / "filled".
    "filled_csv":          "imputed_csv",
    "filled":              "imputed_csv",
    "imputed":             "imputed_csv",
    # missing_summary publishes "summary_csv" — planner sometimes
    # qualifies it as "missing_summary_csv".
    "missing_summary_csv": "summary_csv",
    "missing_csv":         "summary_csv",
    # describe_full publishes "summary_csv" — same alias works.
    "describe_csv":        "summary_csv",
    # outlier_iqr_flag publishes "flagged_csv".
    "outlier_csv":         "flagged_csv",
    "outliers_csv":        "flagged_csv",
    # encode_categorical publishes "encoded_csv".
    "encoded":             "encoded_csv",
    "categorical_encoded_csv": "encoded_csv",
    # normalize_scale publishes "scaled_csv".
    "scaled":              "scaled_csv",
    "normalized_csv":      "scaled_csv",
    "standardized_csv":    "scaled_csv",
}


def _resolve_csv_key(requested_key: str,
                      available: Dict[str, Any]) -> Optional[str]:
    """Return the canonical csv_key that lives in ``available``, or
    ``None`` when no alias maps."""
    if not requested_key:
        return None
    if requested_key in available:
        return requested_key
    canonical = _CSV_KEY_ALIASES.get(requested_key.strip().lower())
    if canonical and canonical in available:
        return canonical
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
    # 必须磁盘上确实存在；只看"长得像路径"会让 LLM 编出来的 /workspace/foo.csv 这种
    # 假路径绕过 autowire，下游 read_csv 时再炸。规则收紧：只接受 is_file()。
    if Path(s).is_file():
        return True
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
    from operator_library.contract import Role
    auto: Dict[str, Any] = {}
    invalidated_user: List[str] = []
    workspace_dir: Optional[Path] = None
    if input_csv is not None:
        try:
            workspace_dir = Path(input_csv).parent
        except Exception:
            workspace_dir = None

    def _try_resolve_in_workspace(val: str) -> Optional[str]:
        """Recover an LLM-fabricated path like '/workspace/foo.csv' by trying
        the basename inside the actual workspace dir."""
        if not isinstance(val, str) or not val.strip():
            return None
        if workspace_dir is None:
            return None
        base = Path(val).name
        if not base:
            return None
        candidate = workspace_dir / base
        if candidate.is_file():
            return str(candidate)
        return None

    # Recognize a small set of "obviously a data file" param suffixes so
    # solvers that take e.g. an .h5ad / .h5 / .parquet / .npy file can be
    # autowired from anywhere in the workspace.  Each suffix maps to the
    # set of file extensions we glob for.
    _DATA_PARAM_SUFFIXES = {
        "_h5ad":    [".h5ad"],
        "_h5":      [".h5", ".hdf5"],
        "_parquet": [".parquet"],
        "_anndata": [".h5ad"],
        "_npy":     [".npy"],
        "_npz":     [".npz"],
    }

    def _glob_workspace_for(exts: List[str]) -> Optional[str]:
        if workspace_dir is None or not workspace_dir.is_dir():
            return None
        for ext in exts:
            for p in sorted(workspace_dir.rglob(f"*{ext}")):
                if p.is_file():
                    return str(p)
        return None

    primary_fallback_used = False  # only fall back to input_csv ONCE per step
    for k, spec in contract.roles.items():
        if spec.role != Role.PARAMS:
            continue
        is_csv = k.endswith("_csv")
        is_path = k.endswith("_path")
        data_exts: Optional[List[str]] = None
        for suf, exts in _DATA_PARAM_SUFFIXES.items():
            if k.endswith(suf):
                data_exts = exts
                break
        if not (is_csv or is_path or data_exts):
            continue

        # Priority 1: a prior step published the same key.
        upstream: Optional[str] = None
        for step_name, outs in outputs_by_step.items():
            if k in outs and isinstance(outs[k], (str, Path)):
                v = str(outs[k])
                if _looks_like_real_path(v, k):
                    upstream = v
        if upstream is not None:
            auto[k] = upstream
            if user_override and k in user_override:
                if str(user_override[k]) != upstream:
                    invalidated_user.append(k)
            continue

        # Priority 2: user_override
        if user_override and k in user_override:
            uv = user_override[k]
            if _looks_like_real_path(uv, k):
                continue  # let merge keep it
            # LLM 偶尔写 /workspace/<basename>，尝试在真实 workspace 里找 basename
            recovered = _try_resolve_in_workspace(str(uv))
            if recovered is not None:
                auto[k] = recovered
                invalidated_user.append(k)
                continue
            invalidated_user.append(k)

        # Priority 3: 单次 input_csv 兜底。多 CSV 算子 (e.g. DESeq2 counts + groups)
        # 只允许第一个未填的 *_csv role 接 input_csv；其余保持空 (solver 会报缺失，
        # 或者由后续 LLM mapping 阶段补全)。
        if is_csv and not primary_fallback_used and \
                input_csv is not None and Path(input_csv).is_file():
            auto[k] = str(input_csv)
            primary_fallback_used = True
        # *_path keys with no prior output and no real user_override:
        # leave the role unset so the solver uses its bundled default.

        # Priority 4: data-file params (_h5ad / _h5 / _parquet / _anndata
        # / _npy / _npz) — search the whole workspace for a matching file.
        # Useful when the driver mounted a dataset under
        # ws/benchmark/datasets/<name>/<file>.h5ad and the planner's
        # mapping is empty.
        if data_exts and k not in auto:
            found = _glob_workspace_for(data_exts)
            if found is not None:
                auto[k] = found

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


def _extract_outputs_schema(
    outputs: Dict[str, Any],
    *,
    max_columns: int = 20,
    max_preview_rows: int = 2,
) -> Dict[str, Any]:
    """For each output value that is a path, read a tiny header + 2-row
    preview so downstream agents (coder, planner re-feed, case study)
    can decide whether to consume the artifact without re-reading from
    disk.

    V8 Phase 3 §P3-A.  Failure-tolerant: any IO / parse error on a
    single output silently skips that output (returns just its path).

    Returned shape (one entry per output_key whose value is a path):
        {
          "<key>": {
              "path":         "<absolute path>",
              "kind":         "csv" | "json" | "other",
              "columns":      ["col1", "col2", ...] | None,
              "dtypes":       {"col1": "int64", ...} | None,
              "n_rows":       int | None,
              "preview_rows": [{...}, {...}] | None,
          }, ...
        }

    中文：把"已成功算子"的每个产物文件抽出 schema + 2 行 preview，
    coder 不必读 csv 也能知道列名 / 形状。
    """
    out: Dict[str, Any] = {}
    if not outputs:
        return out
    for key, val in outputs.items():
        if not isinstance(val, (str, Path)):
            continue
        s = str(val)
        if not s:
            continue
        try:
            p = Path(s)
            if not p.is_file():
                continue
        except Exception:
            continue
        suffix = p.suffix.lower()
        entry: Dict[str, Any] = {
            "path": str(p),
            "kind": "csv" if suffix == ".csv"
                     else ("json" if suffix == ".json" else "other"),
            "columns": None,
            "dtypes": None,
            "n_rows": None,
            "preview_rows": None,
        }
        if suffix == ".csv":
            try:
                head = pd.read_csv(p, nrows=max_preview_rows)
                cols = list(head.columns)[:max_columns]
                entry["columns"] = cols
                entry["dtypes"] = {
                    c: str(head[c].dtype) for c in cols
                }
                # n_rows: full count via fast pandas read (`usecols=[0]`)
                try:
                    n = sum(1 for _ in open(p, "rb")) - 1
                    entry["n_rows"] = int(max(n, 0))
                except Exception:
                    entry["n_rows"] = int(len(head))
                # preview rows as list of dicts (truncate cell values)
                preview: List[Dict[str, Any]] = []
                for _, row in head.head(max_preview_rows).iterrows():
                    rec_row: Dict[str, Any] = {}
                    for c in cols:
                        v = row[c]
                        if isinstance(v, (str, bytes)) and len(str(v)) > 60:
                            v = str(v)[:60] + "..."
                        try:
                            if pd.isna(v):
                                v = None
                        except Exception:
                            pass
                        rec_row[c] = (None if v is None else
                                       (float(v) if hasattr(v, "item") else v))
                    preview.append(rec_row)
                entry["preview_rows"] = preview
            except Exception:
                # leave schema fields as None
                pass
        elif suffix == ".json":
            # V8 Phase 3 §P3-A (JSON arm): cheap key-level introspection
            # so the Coder sees the EXACT JSON keys (e.g. F_statistic,
            # p_value, n_a/n_b instead of group_means / n1/n2) and stops
            # hallucinating field names that don't exist.  We render the
            # JSON as one synthetic preview "row" — the keys map cleanly
            # onto the columns field and the values onto preview_rows[0].
            try:
                import json as _json
                with open(p, "r", encoding="utf-8") as fp:
                    payload = _json.load(fp)
                if isinstance(payload, dict):
                    # Flatten ONE level of nested dicts (e.g. group_means)
                    # into dotted keys to give the Coder a peek at nested
                    # structure without exploding the prompt.
                    flat: Dict[str, Any] = {}
                    for k, v in payload.items():
                        if isinstance(v, dict):
                            for kk, vv in v.items():
                                flat[f"{k}.{kk}"] = vv
                        else:
                            flat[k] = v
                    cols = list(flat.keys())[:max_columns]
                    entry["columns"] = cols
                    # dtypes: best-effort Python type names
                    entry["dtypes"] = {
                        c: type(flat[c]).__name__ for c in cols
                    }
                    entry["n_rows"] = 1  # single-record JSON object
                    # Build a single preview row.  Truncate long
                    # strings and avoid embedding deep nested lists.
                    rec: Dict[str, Any] = {}
                    for c in cols:
                        v = flat[c]
                        if isinstance(v, (list, tuple)):
                            sample = list(v)[:8]
                            rec[c] = sample
                        elif isinstance(v, str) and len(v) > 60:
                            rec[c] = v[:60] + "..."
                        else:
                            rec[c] = v
                    entry["preview_rows"] = [rec]
                elif isinstance(payload, list):
                    # List-of-records JSON: peek first record's keys
                    if payload and isinstance(payload[0], dict):
                        first = payload[0]
                        cols = list(first.keys())[:max_columns]
                        entry["columns"] = cols
                        entry["dtypes"] = {
                            c: type(first[c]).__name__ for c in cols
                        }
                        entry["n_rows"] = len(payload)
                        prev = payload[:max_preview_rows]
                        entry["preview_rows"] = [
                            {c: r.get(c) for c in cols} for r in prev
                            if isinstance(r, dict)
                        ]
            except Exception:
                pass
        out[key] = entry
    return out


def _write_step_manifest(step_dir: Path, record: Dict[str, Any]) -> None:
    """Persist a per-step manifest.json (success OR failure).

    Always written under ``<output_dir>/<step.name>/step_manifest.json``
    even when the step crashed, so the planner / case study can read
    per-step status without re-deriving it from logs.

    Side-effect: also normalises the record in-place so every manifest
    has the V8 Phase-2 structured-failure fields (``error_code``,
    ``next_action_hint``, ``error_args``).  On success they are ``None``;
    on failure they may be populated by the runner.  This keeps the
    on-disk schema uniform across all step types.
    """
    import json as _json
    # 统一字段，缺省即 None，避免下游 reader 频繁 .get(...) 兜底
    for k in ("error_code", "next_action_hint", "error_args"):
        record.setdefault(k, None)
    try:
        step_dir.mkdir(parents=True, exist_ok=True)
        path = step_dir / "step_manifest.json"
        safe = {
            k: (str(v) if isinstance(v, Path) else v)
            for k, v in record.items()
        }
        path.write_text(
            _json.dumps(safe, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        # Manifest write must never break the pipeline; swallow errors.
        pass


def execute_pipeline(steps: List[PipelineStep],
                      initial_input_csv: Path,
                      output_dir: Path,
                      use_llm: bool = False,
                      task_description: str = "") -> Dict[str, Any]:
    """Run the steps sequentially, resolving each step's mapping with
    LLM + enhanced rules + the user's override (if any).

    Returns a dict shaped like the runtime ``manifest`` consumed by the
    result template.  Each step (success OR failure) writes a
    ``step_manifest.json`` under its own subdirectory; the returned
    dict also carries a top-level ``failed_steps`` list that downstream
    agents (planner / coder / case study) can read directly.

    中文：顺序执行 ``PipelineStep``；每步读取上一步主 CSV + autowire 路径参数；
    ``use_llm`` 为真时未填角色由 ``mapping_engine`` 调用 LLM 补全。
    每一步无论成败都会在自己的子目录写一份 ``step_manifest.json``，
    顶层 manifest 还会附 ``failed_steps`` 列表方便上游 agent 复盘。
    """
    import time as _time

    output_dir.mkdir(parents=True, exist_ok=True)

    initial_csv = Path(initial_input_csv)
    last_csv = initial_csv
    outputs_by_step: Dict[str, Dict[str, Any]] = {}
    step_records: List[Dict[str, Any]] = []
    overall_ok = True

    for step_idx, step in enumerate(steps):
        step_dir = output_dir / step.name
        t_start = _time.time()
        # 1) resolve input csv (mirrors software1_solver.Pipeline logic).
        if step.input_from is None:
            input_csv = last_csv
        elif step.input_from == "__initial__":
            input_csv = initial_csv
        else:
            prev = outputs_by_step.get(step.input_from)
            if prev is None:
                rec = {
                    "step_index": step_idx,
                    "name": step.name,
                    "solver": step.solver.contract.name,
                    "input_csv": "(unresolved)",
                    "mapping_source": "n/a",
                    "mapping": {},
                    "rationale": [],
                    "status": "error",
                    "error_phase": "input_resolution",
                    "error": f"input_from={step.input_from!r} not yet executed",
                    "outputs": {},
                    "missing_required": [],
                    "duration_sec": round(_time.time() - t_start, 3),
                }
                step_records.append(rec)
                _write_step_manifest(step_dir, rec)
                overall_ok = False
                continue
            key = step.input_output_key#看有没有对应的键值对
            if key == "auto":
                key = _pick_default_csv_key(prev) or ""
            # LLM-friendly alias rewrite: if the planner asked for a
            # csv_key the upstream solver does not actually publish but
            # we know an alias for, transparently swap to the canonical
            # key.  This collapses RADAR-style "filled_csv vs
            # imputed_csv" mismatches without touching contracts.
            if key and key not in prev:
                aliased = _resolve_csv_key(key, prev)
                if aliased is not None:
                    key = aliased
            if key not in prev:
                soft = {
                    "spearman_correlation", "pearson_correlation", "groupby_stat",
                    "welch_t_test", "mann_whitney_u_test", "linear_regression",
                }
                sid = step.solver.contract.name
                # Prefer any csv from upstream when requested key missing (resume wiring)
                fallback_key = _pick_default_csv_key(prev) if prev else None
                if fallback_key and fallback_key in prev and sid in soft:
                    key = fallback_key
                elif sid in soft:
                    rec = {
                        "step_index": step_idx,
                        "name": step.name,
                        "solver": sid,
                        "input_csv": "(unresolved)",
                        "mapping_source": "n/a",
                        "mapping": {},
                        "rationale": [],
                        "status": "skipped",
                        "error_phase": "input_resolution",
                        "error": (
                            f"skipped: csv_key {key!r} not in outputs of "
                            f"{step.input_from!r} (have: {list(prev)})"
                        ),
                        "outputs": {},
                        "missing_required": [],
                        "duration_sec": round(_time.time() - t_start, 3),
                    }
                    step_records.append(rec)
                    _write_step_manifest(step_dir, rec)
                    continue
                else:
                    rec = {
                        "step_index": step_idx,
                        "name": step.name,
                        "solver": sid,
                        "input_csv": "(unresolved)",
                        "mapping_source": "n/a",
                        "mapping": {},
                        "rationale": [],
                        "status": "error",
                        "error_phase": "input_resolution",
                        "error": f"csv_key {key!r} not in outputs of "
                                  f"{step.input_from!r} (have: {list(prev)})",
                        "outputs": {},
                        "missing_required": [],
                        "duration_sec": round(_time.time() - t_start, 3),
                    }
                    step_records.append(rec)
                    _write_step_manifest(step_dir, rec)
                    overall_ok = False
                    continue
            input_csv = Path(prev[key])

        # 2) load + profile + resolve mapping.
        try:
            df = pd.read_csv(input_csv)
            # Inject a universal __row_id__ column so solvers that need an
            # ID role (PSM, normalize_scale, knn, etc.) can fall back to
            # row indices when the input table has no natural identifier
            # column.  Inserted at position 0 to keep it visually obvious
            # in any preview rendering.  Skip insertion if the input csv
            # already carries a __row_id__ from an upstream operator
            # (e.g. data_imputation).
            if "__row_id__" not in df.columns:
                df.insert(0, "__row_id__", range(len(df)))
        except Exception as e:
            rec = {
                "step_index": step_idx,
                "name": step.name,
                "solver": step.solver.contract.name,
                "input_csv": str(input_csv),
                "mapping_source": "n/a",
                "mapping": {},
                "rationale": [],
                "status": "error",
                "error_phase": "csv_read",
                "error": f"failed to read csv: {type(e).__name__}: {e}",
                "outputs": {},
                "missing_required": [],
                "duration_sec": round(_time.time() - t_start, 3),
            }
            step_records.append(rec)
            _write_step_manifest(step_dir, rec)
            overall_ok = False
            continue

        profile = profile_df(df)#获取这份文件的基本信息
        user_override = step.mapping_override or None
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
            task_description=task_description,
        )#让用户对应列或者大模型对应列或者规则对应列
        cm = mapping_engine.to_column_mapping(resolve)#对应不了则报错，但是不崩溃

        # 3) run solver.
        step_dir.mkdir(parents=True, exist_ok=True)
        outputs: Dict[str, Any] = {}
        status = "ok"
        error = None
        error_phase: Optional[str] = None
        # Structured fields populated only when OperatorInputError fires.
        error_code: Optional[str] = None
        next_action_hint: Optional[str] = None
        error_args: Optional[Dict[str, Any]] = None
        if resolve.missing_required:
            # Exploratory group tests often lack a binary group column; skip
            # instead of failing the whole EDA chain.
            soft_skip = {
                "welch_t_test", "mann_whitney_u_test", "groupby_stat",
            }
            sid = step.solver.contract.name
            if sid in soft_skip:
                status = "skipped"
                error_phase = "mapping"
                error = (
                    "skipped: missing required role(s): "
                    + ", ".join(resolve.missing_required)
                )
            else:
                status = "error"
                error_phase = "mapping"
                error = ("missing required role(s): "
                         + ", ".join(resolve.missing_required))
                overall_ok = False
        else:
            try:
                outputs = step.solver.run(df=df, mapping=cm,
                                           output_dir=step_dir)#跑算子
            except OperatorInputError as e:
                # Structured failure: solver explicitly rejected the input
                # via an error code from operator_pipeline.error_codes.
                # Promote all fields onto the step record so manifest
                # readers (planner / coder / case_study) can branch
                # deterministically on `error_code`.
                m = e.to_manifest()
                status = "error"
                error_phase = m["error_phase"]
                error = m["error"]
                error_code = m["error_code"]
                next_action_hint = m["next_action_hint"]
                error_args = m["error_args"]
                outputs = {}
                overall_ok = False
            except BaseException as e:
                # Legacy / uncaught solver crashes: keep wrapping as
                # "solver_run" so old behaviour is preserved, but tag the
                # error_code as UNCAUGHT_EXCEPTION to make it grep-able.
                status = "error"
                error_phase = "solver_run"
                error = f"{type(e).__name__}: {e}"
                error_code = "UNCAUGHT_EXCEPTION"
                next_action_hint = (
                    "solver raised a non-OperatorInputError exception; "
                    "inspect the traceback above for a deeper bug."
                )
                outputs = {}
                overall_ok = False

        if status == "ok":
            key = _pick_default_csv_key(outputs)#结果也规范化输出
            if key is not None:
                last_csv = Path(outputs[key])

        outputs_by_step[step.name] = outputs

        outputs_safe = {
            k: (str(v) if isinstance(v, Path) else v)
            for k, v in outputs.items()
            if isinstance(v, (str, int, float, dict, list, Path))
            or v is None
        }
        # V8 Phase 3 §P3-A: structured artifact manifest with schema +
        # preview.  Only attempted on success (failed steps have no
        # artifacts to describe).
        outputs_schema = (
            _extract_outputs_schema(outputs_safe) if status == "ok" else {}
        )
        # Primary CSV — the artifact downstream Coder/operator steps
        # will default to when no explicit input_source is pinned.
        primary_csv_key = (_pick_default_csv_key(outputs_safe)
                            if status == "ok" else None)
        primary_csv_path = (
            outputs_safe.get(primary_csv_key)
            if primary_csv_key else None
        )
        rec = {
            "step_index": step_idx,
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
            "error_phase": error_phase,
            "error": error,
            # New (V8 Phase 2): structured failure fields.  Always
            # present; None on success so manifest readers can do a
            # simple .get("error_code") check.
            "error_code": error_code,
            "next_action_hint": next_action_hint,
            "error_args": error_args,
            "outputs": outputs_safe,
            # New (V8 Phase 3 §P3-A): structured artifact schema +
            # preview, plus the primary CSV key/path used as the
            # default input for the next step (§P3-B).
            "outputs_schema": outputs_schema,
            "primary_csv_key": primary_csv_key,
            "primary_csv": primary_csv_path,
            "duration_sec": round(_time.time() - t_start, 3),
        }
        step_records.append(rec)
        _write_step_manifest(step_dir, rec)

    # Top-level failed_steps list so the planner / case study can
    # inspect operator failures without re-parsing per-step manifests.
    failed_steps = [
        {
            "step_index": r.get("step_index"),
            "name": r["name"],
            "solver": r["solver"],
            "error_phase": r.get("error_phase"),
            "error": r.get("error"),
            "error_code": r.get("error_code"),
            "next_action_hint": r.get("next_action_hint"),
        }
        for r in step_records if r.get("status") != "ok"
    ]

    pipeline_manifest = {
        "ok": overall_ok,
        "steps": step_records,
        "failed_steps": failed_steps,
        "n_steps_total": len(step_records),
        "n_steps_ok": sum(1 for r in step_records if r.get("status") == "ok"),
        "n_steps_failed": len(failed_steps),
    }

    # Aggregated manifest at the pipeline root so external tooling
    # (case study, planner re-feed) has a single file to read.
    try:
        import json as _json
        (output_dir / "pipeline_manifest.json").write_text(
            _json.dumps(pipeline_manifest, ensure_ascii=False,
                         indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass

    return pipeline_manifest
