# backend/clinical_mapping_service.py — 临床导入列映射（LLM + 规则）

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from clinical_mapping.import_contracts import IMPORT_CONTRACTS
from operator_library.profiler import profile_df
from operator_pipeline import llm_client
from operator_pipeline.mapping_engine import resolve_mapping

DATASET_ALIASES = {
    "patients": "patient",
    "patient": "patient",
    "followups": "followup",
    "followup": "followup",
    "references": "reference",
    "reference": "reference",
    "reference_range": "reference",
    "reference_ranges": "reference",
}

# 常见中英文列名 → 标准字段（作为 user_override 预填，LLM/规则再补全其余）
_COLUMN_ALIASES: Dict[str, List[str]] = {
    "patient": {
        "patient_id": ["patient_id", "patient id", "患者编号", "患者id", "受试者编号", "subject_id", "id", "编号"],
        "age": ["age", "年龄", "岁数"],
        "gender": ["gender", "sex", "性别"],
        "diagnosis": ["diagnosis", "诊断", "主要诊断"],
        "HAMD_total": ["hamd_total", "hamd", "hamd总分", "hamd-17", "hamd17", "抑郁总分"],
        "HAMA_total": ["hama_total", "hama", "hama总分", "焦虑总分"],
        "PHQ9_total": ["phq9_total", "phq9", "phq-9", "phq9总分"],
        "disease_duration_years": ["disease_duration_years", "病程", "病程年"],
        "medication": ["medication", "用药", "药物"],
        "outcome": ["outcome", "结局", "疗效"],
        "relapse": ["relapse", "复发", "是否复发"],
        "admission_date": ["admission_date", "入院日期", "入院时间"],
        "discharge_date": ["discharge_date", "出院日期", "出院时间"],
    },
    "followup": {
        "patient_id": ["patient_id", "患者编号", "受试者编号", "subject_id"],
        "visit_date": ["visit_date", "随访日期", "访视日期", "就诊日期", "date"],
        "visit_type": ["visit_type", "访视类型", "随访类型", "visit"],
        "HAMD_total": ["hamd_total", "hamd", "hamd总分"],
        "HAMA_total": ["hama_total", "hama", "hama总分"],
        "PHQ9_total": ["phq9_total", "phq9", "phq9总分"],
        "medication": ["medication", "用药"],
        "medication_dose_mg": ["medication_dose_mg", "剂量", "用药剂量"],
        "notes": ["notes", "备注", "note"],
    },
    "reference": {
        "indicator": ["indicator", "指标", "指标名", "item", "检验项目"],
        "lower_bound": ["lower_bound", "下限", "参考下限", "low"],
        "upper_bound": ["upper_bound", "上限", "参考上限", "high"],
        "gender": ["gender", "性别"],
        "diagnosis": ["diagnosis", "诊断"],
        "age_range_lower": ["age_range_lower", "年龄下限", "适用年龄下限"],
        "age_range_upper": ["age_range_upper", "年龄上限", "适用年龄上限"],
        "unit": ["unit", "单位"],
        "source": ["source", "来源", "参考来源"],
    },
}


def _alias_override(dataset_type: str, source_columns: List[str]) -> Dict[str, str]:
    aliases = _COLUMN_ALIASES.get(dataset_type, {})
    override: Dict[str, str] = {}
    used_sources: set[str] = set()
    for canon, patterns in aliases.items():
        for col in source_columns:
            if col in used_sources:
                continue
            norm_col = str(col).strip().lower().replace(" ", "_").replace("-", "_")
            for pat in patterns:
                norm_pat = pat.strip().lower().replace(" ", "_").replace("-", "_")
                if norm_col == norm_pat or norm_pat in norm_col or norm_col in norm_pat:
                    override[canon] = col
                    used_sources.add(col)
                    break
            if canon in override:
                break
    return override


def _normalize_dataset_type(dataset_type: str) -> Optional[str]:
    key = (dataset_type or "").strip().lower()
    return DATASET_ALIASES.get(key)


def _contract_fields(dataset_type: str) -> Tuple[List[str], List[str]]:
    contract = IMPORT_CONTRACTS[dataset_type]
    required = [k for k, spec in contract.roles.items() if not spec.optional]
    optional = [k for k, spec in contract.roles.items() if spec.optional]
    return required, optional


def _rows_to_df(rows: List[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.columns = [str(c) for c in df.columns]
    return df


def suggest_import_mapping(
    dataset_type: str,
    rows: List[dict],
    *,
    user_override: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
) -> Tuple[Optional[dict], Optional[str]]:
    """Suggest canonical_field -> source_column mapping for an uploaded table."""
    norm = _normalize_dataset_type(dataset_type)
    if not norm:
        return None, f"未知 dataset_type: {dataset_type}（支持 patient/followup/reference）"
    if not rows:
        return None, "无数据行，无法推断列映射"

    df = _rows_to_df(rows[:200])
    if df.empty or len(df.columns) == 0:
        return None, "无法解析表头列"

    contract = IMPORT_CONTRACTS[norm]
    profile = profile_df(df)
    llm_available = llm_client.is_available()

    merged_override = _alias_override(norm, [str(c) for c in df.columns])
    if user_override:
        merged_override.update({k: v for k, v in user_override.items() if v})

    result = resolve_mapping(
        df=df,
        profile=profile,
        contract=contract,
        user_override=merged_override or None,
        use_llm=bool(use_llm and llm_available),
        task_description=contract.description,
    )

    column_mapping = {
        k: v for k, v in result.mapping.items() if isinstance(v, str) and v.strip()
    }
    mapped_sources = set(column_mapping.values())
    source_columns = [str(c) for c in df.columns]
    unmapped_source_columns = [c for c in source_columns if c not in mapped_sources]

    required_fields, optional_fields = _contract_fields(norm)
    missing_required = [f for f in result.missing_required if f in required_fields]
    preview_rows, _ = apply_import_mapping(rows[:5], column_mapping)

    warnings: List[str] = []
    if missing_required:
        warnings.append(f"必填字段未映射: {', '.join(missing_required)}")
    if use_llm and not llm_available:
        warnings.append("未配置 LLM，已使用规则映射；可在 .env 配置 OPENAI_API_KEY / OPENAI_API_BASE")
    if result.llm_attempted and not result.llm_ok and result.llm_error:
        warnings.append(f"LLM 映射失败，已回退规则: {result.llm_error}")

    return {
        "dataset_type": norm,
        "source_columns": source_columns,
        "column_mapping": column_mapping,
        "required_fields": required_fields,
        "optional_fields": optional_fields,
        "missing_required": missing_required,
        "unmapped_source_columns": unmapped_source_columns,
        "rationale": result.rationale,
        "mapping_source": result.source,
        "llm_available": llm_available,
        "llm_attempted": result.llm_attempted,
        "llm_ok": result.llm_ok,
        "llm_error": result.llm_error,
        "warnings": warnings,
        "preview_rows": preview_rows,
        "ready_to_import": len(missing_required) == 0,
    }, None


def apply_import_mapping(
    rows: List[dict],
    column_mapping: Optional[Dict[str, Any]],
) -> Tuple[List[dict], List[str]]:
    """Remap uploaded rows using canonical_field -> source_column dict."""
    if not column_mapping:
        return list(rows), []

    mapping = {
        str(k): str(v).strip()
        for k, v in column_mapping.items()
        if v is not None and str(v).strip()
    }
    if not mapping:
        return list(rows), []

    out: List[dict] = []
    errors: List[str] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        mapped: Dict[str, Any] = {}
        for canon, src in mapping.items():
            if src in raw:
                val = raw[src]
                if val is not None and (not isinstance(val, float) or pd.notna(val)):
                    mapped[canon] = val
        if not mapped:
            errors.append(f"行{i + 1}: 映射后为空")
            continue
        out.append(mapped)
    return out, errors[:10]
