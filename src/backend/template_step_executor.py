# backend/template_step_executor.py
# 按模板 analysis_steps 逐步执行 operator_library 医学算子

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from operator_library.contract import ColumnMapping

SCALE_COLUMNS = ["HAMD_total", "HAMA_total", "PHQ9_total", "PANSS_total", "GAD7_total"]
OUTCOME_ORDER = {
    "no_response": 0,
    "partial_response": 1,
    "response": 2,
    "relapse": 3,
}


def _numeric_columns(df: pd.DataFrame) -> List[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and c not in ("__row_id__",)
    ]


def _scale_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in SCALE_COLUMNS if c in df.columns]


def _is_longitudinal(df: pd.DataFrame) -> bool:
    if "patient_id" not in df.columns:
        return False
    return int(df.groupby("patient_id").size().max()) > 1


def _prepare_survival_columns(df: pd.DataFrame) -> pd.DataFrame:
    """构造生存分析所需 time_col / event_col（Kaplan–Meier / Cox）。"""
    work = df.copy()
    if "duration_days" not in work.columns:
        if "relapse_date" in work.columns and "visit_date" in work.columns:
            visit = pd.to_datetime(work["visit_date"], errors="coerce")
            relapse = pd.to_datetime(work["relapse_date"], errors="coerce")
            duration = (relapse - visit).dt.days
            censored = 365
            work["duration_days"] = np.where(
                work.get("relapse", 0).astype(int) == 1,
                duration.fillna(censored),
                censored,
            )
            work["duration_days"] = work["duration_days"].clip(lower=1).fillna(censored)
        else:
            work["duration_days"] = 365
    if "event_flag" not in work.columns:
        if "relapse" in work.columns:
            work["event_flag"] = work["relapse"].astype(int)
        elif "readmission" in work.columns:
            work["event_flag"] = work["readmission"].astype(int)
        else:
            work["event_flag"] = 0
    return work


def _prepare_ordinal_outcome(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "outcome" not in work.columns:
        return work
    if "outcome_ord" not in work.columns:
        work["outcome_ord"] = work["outcome"].astype(str).str.strip().map(OUTCOME_ORDER)
        work["outcome_ord"] = work["outcome_ord"].fillna(
            work["outcome"].astype("category").cat.codes
        )
    return work


def _ordinal_frame(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    work = _prepare_ordinal_outcome(df)
    pred_cols = [c for c in ["age", "HAMD_total", "HAMA_total", "PHQ9_total"] if c in work.columns]
    if "medication" in work.columns:
        dummies = pd.get_dummies(work["medication"].astype(str), prefix="med", drop_first=True)
        work = pd.concat([work, dummies], axis=1)
        pred_cols.extend(dummies.columns.tolist())
    return work, pred_cols


def _prepare_ordinal_predictors(df: pd.DataFrame) -> pd.DataFrame:
    work, _ = _ordinal_frame(df)
    return work


def _serialize_outputs(outputs: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in outputs.items():
        if key.endswith("_dict") and isinstance(val, dict):
            out[key.replace("_dict", "")] = val
            continue
        if isinstance(val, pd.DataFrame):
            out[key] = {
                "rows": len(val),
                "columns": val.columns.tolist(),
                "records": val.replace({np.nan: None}).head(80).to_dict(orient="records"),
            }
        elif isinstance(val, dict):
            out[key] = val
        elif isinstance(val, str) and val.endswith(".json") and Path(val).is_file():
            out[key] = json.loads(Path(val).read_text(encoding="utf-8"))
        elif isinstance(val, str) and val.endswith(".csv") and Path(val).is_file():
            frame = pd.read_csv(val)
            out[key] = {
                "rows": len(frame),
                "columns": frame.columns.tolist(),
                "records": frame.head(80).replace({np.nan: None}).to_dict(orient="records"),
            }
        else:
            try:
                if pd.isna(val):
                    out[key] = None
                    continue
            except (ValueError, TypeError):
                pass
            out[key] = val
    return out


def _run_responder_cross_section(df: pd.DataFrame, output_dir: Path) -> Dict[str, Any]:
    """横截面：用药 × 治疗反应 卡方独立性检验（scipy chi2）。"""
    from scipy import stats

    if "medication" not in df.columns or "outcome" not in df.columns:
        raise ValueError("需要 medication 与 outcome 列")
    work = df[["medication", "outcome"]].dropna()
    ct = pd.crosstab(work["medication"], work["outcome"])
    chi2, p, dof, _expected = stats.chi2_contingency(ct.values)
    summary = {
        "test": "chi2_independence",
        "method": "Pearson chi-square (medication vs treatment outcome)",
        "reference": "Agresti (2013) Categorical Data Analysis",
        "chi2": float(chi2),
        "dof": int(dof),
        "p_value": float(p),
        "n_total": int(ct.values.sum()),
        "interpretation": "检验不同药物组间治疗结局分布是否独立",
    }
    table_path = output_dir / "responder_chi2_table.csv"
    ct.to_csv(table_path)
    json_path = output_dir / "responder_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "summary_json": str(json_path),
        "table_csv": str(table_path),
        "summary_dict": summary,
    }


_VISIT_ORDER = {
    "baseline": 0, "week1": 1, "week2": 2, "week4": 4, "week6": 6,
    "week8": 8, "week12": 12, "week16": 16, "week24": 24, "endpoint": 90,
}


def _visit_trend_table(df: pd.DataFrame) -> pd.DataFrame:
    """按访视（visit_type）聚合各量表均值/标准差/样本量，用于画"基线→随访变化趋势"曲线。"""
    if "visit_type" not in df.columns:
        return pd.DataFrame()
    scales = _scale_columns(df)
    if not scales:
        return pd.DataFrame()
    g = df.groupby("visit_type", dropna=True)[scales].agg(["mean", "std", "count"])
    g.columns = [f"{col}_{stat}" for col, stat in g.columns]
    g = g.reset_index()
    g["_order"] = g["visit_type"].astype(str).str.lower().map(
        lambda v: _VISIT_ORDER.get(v, 50)
    )
    g = g.sort_values("_order").drop(columns="_order").reset_index(drop=True)
    for col in g.columns:
        if col.endswith("_mean") or col.endswith("_std"):
            g[col] = g[col].round(2)
    return g


def _run_responder_longitudinal(df: pd.DataFrame, output_dir: Path) -> Dict[str, Any]:
    """纵向：基线→终点 ≥50% 量表下降为 responder（PANSS 轨迹算子，阈值调至 50%）。

    ``df`` 需为长表（每患者多行，含 ``visit_type``），一般是原始上传文件本身
    （而不是被压缩成"每患者一行"的横截面视图），这样才能真正对比同一患者
    基线 vs 随访终点的量表分数变化。
    """
    from operator_library.solvers.panss_trajectory_responder import get_solver

    pid = "patient_id"
    if "visit_type" in df.columns:
        vt_lower = df["visit_type"].astype(str).str.lower()
        base = df[vt_lower == "baseline"].drop_duplicates(subset=pid, keep="first")
        # 终点优先级 week12 > endpoint > week8：每患者只取一个终点访视，
        # 避免同一患者同时命中 week8 与 week12 两行导致"伪重复"、N 翻倍。
        end_priority = ["week12", "endpoint", "week8"]
        end_pool = df[vt_lower.isin(end_priority)].copy()
        end_pool["_prio"] = vt_lower[vt_lower.isin(end_priority)].map(
            {v: i for i, v in enumerate(end_priority)}
        )
        end = end_pool.sort_values("_prio").drop_duplicates(subset=pid, keep="first").drop(columns="_prio")
        if base.empty or end.empty:
            raise ValueError("纵向数据缺少 baseline / week12 访视")
        score_col = _scale_columns(df)[0] if _scale_columns(df) else "HAMD_total"
        wide = base[[pid, score_col]].rename(columns={score_col: "baseline_score"})
        wide = wide.merge(
            end[[pid, score_col]].rename(columns={score_col: "endpoint_score"}),
            on=pid,
            how="inner",
        )
    else:
        raise ValueError("纵向数据缺少 visit_type")
    mapping = ColumnMapping(
        mapping={
            "id_col": pid,
            "baseline_col": "baseline_score",
            "endpoint_col": "endpoint_score",
        },
        rationale="longitudinal responder",
        source="manual",
    )
    solver = get_solver(responder_threshold_pct=50)
    outputs = solver.run(wide, mapping, output_dir)
    meta = {
        "method": "≥50% reduction baseline→endpoint (responder definition)",
        "reference": "Leucht S et al (2009) Schizophr Res — response thresholds",
        "threshold_pct": 50,
        "n_matched": int(len(wide)),
        "score_col": score_col,
    }
    outputs["summary_dict"] = meta

    trend_df = _visit_trend_table(df)
    if not trend_df.empty:
        trend_path = Path(output_dir) / "visit_trend.csv"
        trend_df.to_csv(trend_path, index=False)
        outputs["trend_csv"] = str(trend_path)

    return outputs


def _build_mapping(operator: str, df: pd.DataFrame) -> ColumnMapping:
    nums = _numeric_columns(df)
    scales = _scale_columns(df) or nums[:5]

    if operator in ("missing_summary", "outlier_iqr_flag", "data_imputation"):
        m: Dict[str, Any] = {}
        if nums:
            m["numeric_columns"] = nums
        return ColumnMapping(mapping=m, rationale="auto numeric", source="rule_based")

    if operator == "describe_full":
        return ColumnMapping(
            mapping={"numeric_columns": scales or nums},
            rationale="scale + numeric cols",
            source="rule_based",
        )

    if operator == "distribution_histogram":
        return ColumnMapping(
            mapping={"numeric_columns": (scales or nums)[:3]},
            rationale="primary scales",
            source="rule_based",
        )

    if operator == "correlation":
        cols = scales if len(scales) >= 2 else nums[: min(6, len(nums))]
        return ColumnMapping(
            mapping={"numeric_columns": cols},
            rationale="inter-scale correlation",
            source="rule_based",
        )

    if operator == "symptom_network_analysis":
        items = list(scales)
        for c in ["age", "disease_duration_years", "medication_dose_mg"]:
            if c in df.columns and c not in items:
                items.append(c)
        if len(items) < 4:
            items = (scales + nums)[: max(4, len(nums))]
        return ColumnMapping(mapping={"items": items[:12]}, rationale="symptom items", source="rule_based")

    if operator == "ordinal_regression":
        _, pred_cols = _ordinal_frame(df)
        return ColumnMapping(
            mapping={"target_col": "outcome_ord", "predictors": pred_cols},
            rationale="ordinal outcome ~ covariates",
            source="rule_based",
        )

    if operator in ("survival_kaplan_meier", "cox_regression"):
        covs = [c for c in ["age", "HAMD_total", "HAMA_total", "PHQ9_total"] if c in df.columns]
        m: Dict[str, Any] = {
            "time_col": "duration_days",
            "event_col": "event_flag",
            "covariates": covs or nums[:3],
        }
        return ColumnMapping(mapping=m, rationale="survival mapping", source="rule_based")

    return ColumnMapping(mapping={}, rationale="empty", source="rule_based")


def _get_solver(operator: str):
    op = operator.strip().lower()
    if op == "missing_summary":
        from operator_library.solvers.data_governance import get_missing_summary_solver
        return get_missing_summary_solver()
    if op == "outlier_iqr_flag":
        from operator_library.solvers.data_governance import get_outlier_iqr_solver
        return get_outlier_iqr_solver()
    if op == "data_imputation":
        from operator_library.solvers.data_governance import get_data_imputation_solver
        return get_data_imputation_solver(method="median")
    if op == "describe_full":
        from operator_library.solvers.descriptive_stats import get_describe_solver
        return get_describe_solver()
    if op == "distribution_histogram":
        from operator_library.solvers.descriptive_stats import get_histogram_solver
        return get_histogram_solver()
    if op == "correlation":
        from operator_library.solvers.correlation import get_spearman_solver
        return get_spearman_solver()
    if op == "ordinal_regression":
        from operator_library.solvers.v8.ordinal_regression import get_solver
        return get_solver()
    if op == "survival_kaplan_meier":
        from operator_library.solvers.v8.survival_kaplan_meier import get_solver
        return get_solver()
    if op == "cox_regression":
        from operator_library.solvers.cox_regression import get_solver
        return get_solver()
    if op == "symptom_network_analysis":
        from operator_library.solvers.v8.symptom_network_analysis import get_solver
        return get_solver(min_obs=30)
    if op == "factor_score":
        from operator_library.solvers.panss_factor_score import get_solver
        return get_solver()
    return None


def _method_label(operator: str) -> str:
    labels = {
        "missing_summary": "缺失率/类型质控",
        "outlier_iqr_flag": "Tukey IQR 异常值标记",
        "data_imputation": "中位数填补 (MAR 探索性)",
        "describe_full": "描述统计 (mean/SD/IQR/skew/kurtosis)",
        "distribution_histogram": "等宽直方图",
        "factor_score": "PANSS/量表因子分",
        "responder_analysis": "治疗反应/responder 或 卡方检验",
        "ordinal_regression": "比例优势有序 logistic (McCullagh 1980)",
        "survival_kaplan_meier": "Kaplan–Meier + log-rank",
        "cox_regression": "Cox 比例风险模型 (lifelines)",
        "symptom_network_analysis": "Graphical Lasso 偏相关网络",
        "correlation": "Spearman 相关矩阵",
    }
    return labels.get(operator, operator)


def execute_template_steps(
    df: pd.DataFrame,
    template: dict,
    workspace_dir: Optional[str] = None,
    long_df: Optional[pd.DataFrame] = None,
) -> Tuple[List[dict], pd.DataFrame]:
    """
    逐步执行模板 analysis_steps。
    返回 (step_results, 最终 DataFrame)。

    :param df: 横截面视图（每患者一行），大多数算子（描述统计/生存分析/
        有序回归/相关矩阵等）基于此，避免同一患者多次随访行造成"伪重复"
        样本膨胀。
    :param long_df: 可选，原始长表（每患者多行，一行一次随访）。仅供
        ``responder_analysis`` 步骤在检测到纵向结构时使用，用来真正计算
        "基线→随访终点"的量表变化趋势；不传时行为与旧版一致（用 df 自己判断）。
    """
    steps = template.get("analysis_steps") or []
    work = df.copy()
    results: List[dict] = []
    root = Path(workspace_dir) if workspace_dir else Path(tempfile.mkdtemp(prefix="tpl_run_"))

    for step_def in steps:
        step_no = step_def.get("step")
        step_name = step_def.get("name") or step_def.get("operator") or "step"
        operator = (step_def.get("operator") or step_def.get("action") or "").strip()
        step_dir = root / f"step_{step_no}_{operator}"
        step_dir.mkdir(parents=True, exist_ok=True)

        record: Dict[str, Any] = {
            "step": step_no,
            "name": step_name,
            "operator": operator,
            "method": _method_label(operator),
            "description": step_def.get("description"),
            "status": "pending",
        }

        try:
            if operator == "responder_analysis":
                responder_source = long_df if long_df is not None else work
                if _is_longitudinal(responder_source):
                    outputs = _run_responder_longitudinal(responder_source, step_dir)
                    record["status"] = "ok"
                    record["note"] = "纵向：≥50% 量表下降 responder（含基线→随访变化趋势）"
                else:
                    outputs = _run_responder_cross_section(work, step_dir)
                    record["status"] = "ok"
                    record["note"] = "横截面：用药×结局 卡方独立性检验"
            elif operator == "factor_score":
                item_cols = [c for c in work.columns if c.startswith(("P", "N", "G", "hamd_", "HAMD_"))]
                item_cols = [c for c in item_cols if c not in SCALE_COLUMNS]
                if len(item_cols) < 3:
                    record["status"] = "skipped"
                    record["note"] = "无条目级量表列，仅有总分；跳过因子分（需 HAMD/PANSS 条目）"
                    results.append(record)
                    continue
                solver = _get_solver(operator)
                mapping = ColumnMapping(mapping={"positive_items": item_cols[:7]}, source="manual")
                outputs = solver.run(work, mapping, step_dir)
                record["status"] = "ok"
            elif operator in ("survival_kaplan_meier", "cox_regression"):
                work = _prepare_survival_columns(work)
                solver = _get_solver(operator)
                if solver is None:
                    raise ValueError(f"未知算子: {operator}")
                mapping = _build_mapping(operator, work)
                outputs = solver.run(work, mapping, step_dir)
                record["status"] = "ok"
            elif operator == "ordinal_regression":
                work, pred_cols = _ordinal_frame(work)
                sub = work.dropna(subset=["outcome_ord"] + list(pred_cols))
                if len(sub) < 30 or sub["outcome_ord"].nunique() < 3:
                    record["status"] = "skipped"
                    record["note"] = "有序结局水平不足或样本量过小，跳过有序回归"
                    results.append(record)
                    continue
                solver = _get_solver(operator)
                mapping = ColumnMapping(
                    mapping={"target_col": "outcome_ord", "predictors": pred_cols},
                    source="manual",
                )
                outputs = solver.run(sub, mapping, step_dir)
                record["status"] = "ok"
            else:
                solver = _get_solver(operator)
                if solver is None:
                    record["status"] = "skipped"
                    record["note"] = f"算子 {operator} 未注册"
                    results.append(record)
                    continue
                mapping = _build_mapping(operator, work)
                outputs = solver.run(work, mapping, step_dir)
                record["status"] = "ok"

            record["outputs"] = _serialize_outputs(outputs)
            if operator == "data_imputation" and outputs.get("imputed_csv"):
                work = pd.read_csv(outputs["imputed_csv"])

        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)

        results.append(record)

    return results, work
