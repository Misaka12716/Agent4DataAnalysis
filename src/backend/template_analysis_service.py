# backend/template_analysis_service.py
# 模板驱动定量分析 — 按模板 analysis_steps 执行医学算子

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np
import pandas as pd

from backend.template_service import TemplateService
from backend.template_step_executor import execute_template_steps


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return value.replace({np.nan: None}).to_dict(orient="records")
    if isinstance(value, pd.Series):
        return value.replace({np.nan: None}).to_dict()
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, (Path,)):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (np.floating, np.integer)):
        try:
            return _json_safe(value.item())
        except Exception:
            return str(value)
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    try:
        if pd.isna(value):
            return None
    except (ValueError, TypeError):
        pass
    return value


def _find_data_file(session_workspace: str) -> Optional[str]:
    exts = (".xlsx", ".xls", ".csv", ".tsv")
    if session_workspace and os.path.isdir(session_workspace):
        for root, _, files in os.walk(session_workspace):
            for fname in sorted(files):
                if fname.lower().endswith(exts):
                    return os.path.join(root, fname)
    sample = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "mental_health_sample.xlsx"
    if sample.exists():
        return str(sample)
    return None


def _load_df(path: str) -> pd.DataFrame:
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    return pd.read_csv(path)


def _split_longitudinal(df: pd.DataFrame) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """检测上传数据是否为"每患者多行"的纵向长表；如是，拆成：

    - baseline_view：每患者仅保留 baseline 行（找不到 baseline 则退化为该患者第一行），
      喂给大多数横截面算子（描述统计/生存分析/有序回归/相关矩阵等），
      避免同一患者的多次随访被当成独立样本重复计数。
    - long_df：原始长表本身，只喂给 responder_analysis 步骤，用来计算
      真正的"基线→随访终点"量表变化趋势。

    非纵向数据（每患者一行）：baseline_view = df 本身，long_df = None
    （行为与旧版完全一致）。
    """
    if "patient_id" not in df.columns:
        return df, None
    counts = df.groupby("patient_id").size()
    if counts.max() <= 1:
        return df, None

    if "visit_type" in df.columns:
        is_baseline = df["visit_type"].astype(str).str.lower() == "baseline"
        baseline_view = df[is_baseline].drop_duplicates("patient_id", keep="first")
        missing_ids = set(df["patient_id"]) - set(baseline_view["patient_id"])
        if missing_ids:
            fallback = df[df["patient_id"].isin(missing_ids)].drop_duplicates("patient_id", keep="first")
            baseline_view = pd.concat([baseline_view, fallback], ignore_index=True)
    else:
        baseline_view = df.drop_duplicates("patient_id", keep="first")

    return baseline_view.reset_index(drop=True), df


def _build_report_md(
    template: dict,
    step_results: list,
    data_path: str,
    n_rows: int,
    is_longitudinal: bool = False,
    raw_row_count: Optional[int] = None,
) -> str:
    data_line = f"- 数据文件: `{os.path.basename(data_path)}` ({n_rows} 行)"
    if is_longitudinal and raw_row_count:
        data_line = (
            f"- 数据文件: `{os.path.basename(data_path)}`（纵向数据：{n_rows} 名患者，"
            f"共 {raw_row_count} 条随访记录；横截面算子基于每患者的 baseline 行）"
        )
    lines = [
        f"## {template.get('template_name')} ({template.get('disease_type')})",
        f"",
        data_line,
        f"- 执行模式: **按模板 analysis_steps 逐步调用医学算子**",
        f"",
        f"### 分析步骤执行摘要",
        f"",
        f"| 步 | 名称 | 算子 | 方法 | 状态 |",
        f"|---|------|------|------|------|",
    ]
    for s in step_results:
        status = s.get("status", "?")
        note = s.get("note") or s.get("error") or ""
        status_cell = status + (f" — {note}" if note else "")
        lines.append(
            f"| {s.get('step')} | {s.get('name')} | `{s.get('operator')}` | {s.get('method')} | {status_cell} |"
        )
    lines.append("")
    lines.append("> 说明：演示数据为合成横截面样本；纵向/responder 类步骤在缺少访视结构时会自动降级为适用的横截面检验。")
    return "\n".join(lines)


def _index_step_outputs(step_results: list) -> dict:
    """便于前端展示：按常见模块名索引最后一步成功输出。"""
    by_op = {}
    for s in step_results:
        if s.get("status") == "ok" and s.get("outputs"):
            by_op[s["operator"]] = s["outputs"]
    legacy = {}
    if "describe_full" in by_op:
        legacy["data_distribution"] = by_op["describe_full"].get("stats_csv", {}).get("records") or by_op["describe_full"]
    if "correlation" in by_op:
        legacy["scale_correlation"] = by_op["correlation"]
    if "responder_analysis" in by_op:
        legacy["medication_outcome_assoc"] = by_op["responder_analysis"]
    if "survival_kaplan_meier" in by_op:
        legacy["relapse_analysis"] = by_op["survival_kaplan_meier"]
    if "symptom_network_analysis" in by_op:
        legacy["symptom_network"] = by_op["symptom_network_analysis"]
    if "ordinal_regression" in by_op:
        legacy["ordinal_regression"] = by_op["ordinal_regression"]
    if "cox_regression" in by_op:
        legacy["cox_regression"] = by_op["cox_regression"]
    return legacy


def run_template_analysis(
    session_id: str,
    template_id: int,
    workspace_root: str,
    file_path: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    template, err = TemplateService.get_template(template_id)
    if err:
        return None, err

    session_dir = workspace_root or session_id
    data_path = file_path or _find_data_file(session_dir)
    if not data_path or not os.path.isfile(data_path):
        return None, "未找到可分析的数据文件，请先上传 xlsx/csv"

    try:
        raw_df = _load_df(data_path)
    except Exception as e:
        return None, f"读取数据失败: {e}"

    df, long_df = _split_longitudinal(raw_df)

    run_dir = os.path.join(session_dir or ".", "template_runs", str(template_id))
    os.makedirs(run_dir, exist_ok=True)

    step_results, _final_df = execute_template_steps(df, template, workspace_dir=run_dir, long_df=long_df)

    executed = [s.get("name") for s in step_results if s.get("status") == "ok"]
    skipped = [s.get("name") for s in step_results if s.get("status") == "skipped"]
    errors = [s.get("name") for s in step_results if s.get("status") == "error"]

    legacy = _index_step_outputs(step_results)
    result = {
        "template_id": template_id,
        "template_name": template.get("template_name"),
        "disease_type": template.get("disease_type"),
        "data_file": os.path.basename(data_path),
        "row_count": len(df),
        "column_count": len(df.columns),
        "is_longitudinal": long_df is not None,
        "raw_row_count": len(raw_df),
        "execution_mode": "template_steps_medical_operators",
        "step_results": step_results,
        "analysis_steps_executed": executed,
        "analysis_steps_skipped": skipped,
        "analysis_steps_errors": errors,
        "report_markdown": _build_report_md(
            template, step_results, data_path, len(df),
            is_longitudinal=long_df is not None, raw_row_count=len(raw_df),
        ),
        **legacy,
    }
    try:
        from backend.project_asset_registry import register_template_run_outputs

        register_template_run_outputs(session_id, session_dir)
    except Exception:
        pass
    return _json_safe(result), None
