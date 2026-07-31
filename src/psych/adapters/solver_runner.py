# psych/adapters/solver_runner.py
# 将 operator_pipeline.registry solver 适配为统一 TaskResult

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from operator_library.contract import ColumnMapping
from operator_library.mapper import map_columns_rule_based
from operator_library.profiler import profile_df
from operator_pipeline.registry import make_solver

logger = logging.getLogger(__name__)


def load_dataframe(file_path: str) -> pd.DataFrame:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"数据文件不存在: {file_path}")
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".json":
        return pd.read_json(path)
    return pd.read_csv(path)


def _serialize_outputs(outputs: Dict[str, Any]) -> Dict[str, Any]:
    """将 solver 输出路径中的小文件内容内联，便于 API 返回。"""
    result: Dict[str, Any] = {}
    for key, val in (outputs or {}).items():
        if isinstance(val, (str, Path)):
            p = Path(val)
            if p.is_file() and p.stat().st_size < 2 * 1024 * 1024:
                try:
                    if p.suffix.lower() == ".json":
                        result[key] = {
                            "path": str(p),
                            "content": json.loads(p.read_text(encoding="utf-8")),
                        }
                    elif p.suffix.lower() == ".csv":
                        df = pd.read_csv(p)
                        preview = df.head(100).where(pd.notnull(df.head(100)), None)
                        result[key] = {
                            "path": str(p),
                            "columns": list(df.columns),
                            "row_count": int(len(df)),
                            "preview": preview.to_dict(orient="records"),
                        }
                    else:
                        result[key] = {"path": str(p)}
                except Exception as exc:
                    logger.warning("serialize output %s failed: %s", key, exc)
                    result[key] = {"path": str(p)}
            else:
                result[key] = {"path": str(val)}
        elif isinstance(val, (dict, list, int, float, bool)) or val is None:
            result[key] = val
        else:
            result[key] = str(val)
    return result


def run_solver(
    solver_id: str,
    df: pd.DataFrame,
    output_dir: str | Path,
    mapping_override: Optional[Dict[str, Any]] = None,
    solver_params: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    执行单个 registry solver。

    Returns
    -------
    (result_dict, error)
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        solver = make_solver(solver_id, solver_params or {})
    except Exception as exc:
        return {}, f"创建算子失败: {exc}"

    profile = profile_df(df)
    if mapping_override:
        mapping = ColumnMapping(
            mapping=dict(mapping_override),
            rationale="psych api override",
            source="manual",
        )
    else:
        try:
            mapping = map_columns_rule_based(profile, solver.contract)
        except Exception as exc:
            logger.warning("rule mapping failed for %s: %s", solver_id, exc)
            mapping = ColumnMapping(mapping={}, rationale=str(exc), source="empty")

    try:
        outputs = solver.run(df=df, mapping=mapping, output_dir=out)
        return {
            "solver_id": solver_id,
            "status": "ok",
            "mapping": mapping.mapping,
            "outputs": _serialize_outputs(outputs if isinstance(outputs, dict) else {}),
            "profile_summary": {
                "n_rows": int(len(df)),
                "n_cols": int(df.shape[1]),
                "columns": [str(c) for c in df.columns[:80]],
            },
        }, None
    except Exception as exc:
        logger.exception("solver %s failed", solver_id)
        return {
            "solver_id": solver_id,
            "status": "error",
            "mapping": mapping.mapping,
            "outputs": {},
            "error": f"{type(exc).__name__}: {exc}",
        }, str(exc)


def run_solvers_batch(
    solver_ids: List[str],
    df: pd.DataFrame,
    output_dir: str | Path,
    mappings: Optional[Dict[str, Dict[str, Any]]] = None,
    params_by_method: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """批量执行多个 solver。"""
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    mappings = mappings or {}
    params_by_method = params_by_method or {}
    results = []
    ok = fail = 0
    for sid in solver_ids:
        sub = base / sid
        res, err = run_solver(
            sid,
            df,
            sub,
            mapping_override=mappings.get(sid),
            solver_params=params_by_method.get(sid),
        )
        if err and res.get("status") != "ok":
            fail += 1
            if "error" not in res:
                res["error"] = err
        else:
            ok += 1
        results.append(res)
    return {"results": results, "ok_count": ok, "fail_count": fail}
