# backend/data_qc_service.py — 2.1.3 自动化数据质量评估

from __future__ import annotations

import json
import math
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

DEFAULT_RANGES = {
    "age": (0, 120),
    "HAMD_total": (0, 52),
    "HAMA_total": (0, 56),
    "PHQ9_total": (0, 27),
    "disease_duration_years": (0, 80),
    "relapse": (0, 1),
}
DEFAULT_ALLOWED = {
    "gender": {"男", "女", "M", "F", "male", "female", "未知", "其他"},
}

MAX_ROWS = 20_000
MAX_COLUMNS = 512
MAX_TEXT_RECORDS = 10_000
MAX_TEXT_LENGTH = 200_000
MAX_MULTIMODAL_RECORDS = 10_000
MAX_CSV_BYTES = 100 * 1024 * 1024

TEXT_FIELD_CANDIDATES = (
    "content",
    "note",
    "notes",
    "text",
    "description",
    "clinical_note",
    "report_text",
    "conclusion",
)
ALLOWED_MODALITIES = {
    "image",
    "audio",
    "video",
    "document",
    "pdf",
    "dicom",
    "waveform",
}
MODALITY_MIME_PREFIX = {
    "image": "image/",
    "audio": "audio/",
    "video": "video/",
    "document": "application/",
    "pdf": "application/pdf",
    "dicom": "application/dicom",
}


def _resolve_db_handler(db_handler=None):
    if db_handler is not None:
        return db_handler
    try:
        from utils.mysql_utils import mysql_handler
    except Exception as exc:  # pragma: no cover - 宿主集成路径
        raise RuntimeError("未提供数据库适配器；独立测试请使用 standalone.app") from exc
    return mysql_handler


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    try:
        empty = pd.isna(value)
        if isinstance(empty, (bool, np.bool_)):
            return bool(empty)
    except Exception:
        pass
    return False


def _validate_inline_rows(rows: Any) -> List[Dict[str, Any]]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("rows 必须是对象数组")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"rows 超过上限 {MAX_ROWS}")
    normalized: List[Dict[str, Any]] = []
    columns: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"rows[{index}] 必须是对象")
        item = {str(key): value for key, value in row.items()}
        columns.update(item)
        if len(columns) > MAX_COLUMNS:
            raise ValueError(f"字段数超过上限 {MAX_COLUMNS}")
        normalized.append(item)
    return normalized


def _allowed_csv_path(raw_path: Any) -> Path:
    roots_raw = os.getenv("DQ213_ALLOWED_DATA_ROOTS", "").strip()
    if not roots_raw:
        raise PermissionError("服务端 csv_path 默认禁用；请使用 rows 或配置 DQ213_ALLOWED_DATA_ROOTS")
    candidate = Path(str(raw_path)).expanduser().resolve(strict=True)
    roots = [Path(value).expanduser().resolve() for value in roots_raw.split(os.pathsep) if value.strip()]
    if not any(candidate == root or root in candidate.parents for root in roots):
        raise PermissionError("csv_path 不在允许的数据目录内")
    if candidate.suffix.lower() != ".csv":
        raise ValueError("仅允许读取 CSV 文件")
    if candidate.stat().st_size > MAX_CSV_BYTES:
        raise ValueError(f"CSV 文件超过上限 {MAX_CSV_BYTES} 字节")
    return candidate


def _load_df(
    body: Optional[Dict[str, Any]] = None,
    *,
    owner_user_id: Optional[int] = None,
    db_handler=None,
) -> Tuple[pd.DataFrame, str]:
    body = body or {}
    if "csv_path" in body:
        csv_path = _allowed_csv_path(body.get("csv_path"))
        frame = pd.read_csv(csv_path, nrows=MAX_ROWS + 1)
        if len(frame) > MAX_ROWS:
            raise ValueError(f"CSV 行数超过上限 {MAX_ROWS}")
        if len(frame.columns) > MAX_COLUMNS:
            raise ValueError(f"CSV 字段数超过上限 {MAX_COLUMNS}")
        return frame, "csv"
    if "rows" in body:
        return pd.DataFrame(_validate_inline_rows(body.get("rows"))), "inline"
    if owner_user_id is None:
        raise PermissionError("数据库质控必须提供当前用户身份")
    handler = _resolve_db_handler(db_handler)
    limit = _bounded_int(body.get("limit"), 5000, 1, MAX_ROWS)
    rows, err = handler.query(
        """SELECT patient_id, age, gender, diagnosis, admission_date, discharge_date,
                  HAMD_total, HAMA_total, PHQ9_total, disease_duration_years,
                  medication, outcome, relapse
           FROM mental_health_patients
           WHERE owner_user_id=%s
           ORDER BY id DESC LIMIT %s""",
        (int(owner_user_id), limit),
    )
    if err:
        raise RuntimeError(err)
    return pd.DataFrame(rows or []), "mental_health_patients"


def assess_completeness(df: pd.DataFrame) -> Dict[str, Any]:
    row_count = max(len(df), 1)
    fields = []
    for column in df.columns:
        missing = int(sum(1 for value in df[column].tolist() if _is_empty(value)))
        fields.append(
            {
                "field": str(column),
                "missing": missing,
                "missing_rate": round(missing / row_count, 4),
                "non_null": len(df) - missing,
            }
        )
    overall = round(float(np.mean([item["missing_rate"] for item in fields])) if fields else 0.0, 4)
    return {
        "dimension": "completeness",
        "applicable": bool(df.columns.size),
        "overall_missing_rate": overall,
        "fields": fields,
        "score": round((1.0 - overall) * 100, 2),
    }


def assess_consistency(df: pd.DataFrame) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    row_count = max(len(df), 1)
    if "patient_id" in df.columns:
        ids = df["patient_id"].map(lambda value: "" if _is_empty(value) else str(value).strip())
        non_empty = ids[ids != ""]
        duplicate_mask = non_empty.duplicated(keep=False)
        duplicate_count = int(duplicate_mask.sum())
        if duplicate_count:
            issues.append(
                {
                    "issue_type": "duplicate_id",
                    "field": "patient_id",
                    "count": duplicate_count,
                    "rate": round(duplicate_count / row_count, 4),
                    "message": "patient_id 存在重复",
                }
            )
        invalid_ids = int(
            sum(bool(value) and re.fullmatch(r"[A-Za-z0-9_-]{2,64}", value) is None for value in ids.tolist())
        )
        if invalid_ids:
            issues.append(
                {
                    "issue_type": "id_format",
                    "field": "patient_id",
                    "count": invalid_ids,
                    "rate": round(invalid_ids / row_count, 4),
                    "message": "patient_id 格式异常",
                }
            )
    for column, allowed in DEFAULT_ALLOWED.items():
        if column not in df.columns:
            continue
        invalid = sum(
            not _is_empty(value) and str(value).strip() not in allowed
            for value in df[column].tolist()
        )
        if invalid:
            issues.append(
                {
                    "issue_type": "invalid_category",
                    "field": column,
                    "count": int(invalid),
                    "rate": round(invalid / row_count, 4),
                    "message": f"{column} 不在允许集合 {sorted(allowed)}",
                }
            )
    if "admission_date" in df.columns and "discharge_date" in df.columns:
        admission = pd.to_datetime(df["admission_date"], errors="coerce")
        discharge = pd.to_datetime(df["discharge_date"], errors="coerce")
        invalid_format = int(
            sum(
                not _is_empty(raw) and pd.isna(parsed)
                for raw, parsed in zip(df["admission_date"].tolist(), admission.tolist())
            )
            + sum(
                not _is_empty(raw) and pd.isna(parsed)
                for raw, parsed in zip(df["discharge_date"].tolist(), discharge.tolist())
            )
        )
        if invalid_format:
            issues.append(
                {
                    "issue_type": "invalid_date",
                    "field": "admission_date/discharge_date",
                    "count": invalid_format,
                    "rate": round(invalid_format / (row_count * 2), 4),
                    "message": "日期格式无法解析",
                }
            )
        reversed_count = int((admission.notna() & discharge.notna() & (discharge < admission)).sum())
        if reversed_count:
            issues.append(
                {
                    "issue_type": "date_order",
                    "field": "admission_date/discharge_date",
                    "count": reversed_count,
                    "rate": round(reversed_count / row_count, 4),
                    "message": "出院日期早于入院日期",
                }
            )
    field_rate = round(float(np.mean([item["rate"] for item in issues])) if issues else 0.0, 4)
    return {
        "dimension": "consistency",
        "applicable": bool(df.columns.size),
        "issues": issues,
        "issue_count": len(issues),
        "field_anomaly_rate": field_rate,
        "score": round(max(0.0, 1.0 - min(field_rate, 1.0)) * 100, 2),
    }


def assess_accuracy(df: pd.DataFrame) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    row_count = max(len(df), 1)
    for column, (minimum, maximum) in DEFAULT_RANGES.items():
        if column not in df.columns:
            continue
        raw = df[column]
        numeric = pd.to_numeric(raw, errors="coerce")
        invalid_type = int(sum(not _is_empty(value) and pd.isna(parsed) for value, parsed in zip(raw, numeric)))
        if invalid_type:
            issues.append(
                {
                    "issue_type": "invalid_numeric",
                    "field": column,
                    "count": invalid_type,
                    "rate": round(invalid_type / row_count, 4),
                    "message": f"{column} 含无法解析的数值",
                }
            )
        out_of_range = int((numeric.notna() & ((numeric < minimum) | (numeric > maximum))).sum())
        if out_of_range:
            issues.append(
                {
                    "issue_type": "out_of_range",
                    "field": column,
                    "count": out_of_range,
                    "rate": round(out_of_range / row_count, 4),
                    "expected_range": [minimum, maximum],
                    "message": f"{column} 超出合理范围 [{minimum},{maximum}]",
                }
            )
    if "diagnosis" in df.columns:
        weak = sum(not _is_empty(value) and len(str(value).strip()) < 2 for value in df["diagnosis"].tolist())
        if weak:
            issues.append(
                {
                    "issue_type": "weak_diagnosis",
                    "field": "diagnosis",
                    "count": int(weak),
                    "rate": round(weak / row_count, 4),
                    "message": "诊断文本过短，可能不准确",
                }
            )
    field_rate = round(float(np.mean([item["rate"] for item in issues])) if issues else 0.0, 4)
    return {
        "dimension": "accuracy",
        "applicable": bool(df.columns.size),
        "issues": issues,
        "field_anomaly_rate": field_rate,
        "score": round(max(0.0, 1.0 - min(field_rate, 1.0)) * 100, 2),
    }


def assess_outliers(df: pd.DataFrame) -> Dict[str, Any]:
    fields = []
    row_count = max(len(df), 1)
    for column in df.columns:
        numeric = pd.to_numeric(df[column], errors="coerce")
        numeric = numeric[np.isfinite(numeric)].dropna()
        if len(numeric) < 8:
            continue
        first_quartile = float(numeric.quantile(0.25))
        third_quartile = float(numeric.quantile(0.75))
        interquartile_range = third_quartile - first_quartile
        if interquartile_range <= 0:
            continue
        lower = first_quartile - 1.5 * interquartile_range
        upper = third_quartile + 1.5 * interquartile_range
        full = pd.to_numeric(df[column], errors="coerce")
        finite = full.notna() & np.isfinite(full)
        count = int((finite & ((full < lower) | (full > upper))).sum())
        fields.append(
            {
                "field": str(column),
                "method": "IQR",
                "outlier_count": count,
                "outlier_rate": round(count / row_count, 4),
                "bounds": [round(lower, 4), round(upper, 4)],
            }
        )
    overall = round(float(np.mean([item["outlier_rate"] for item in fields])) if fields else 0.0, 4)
    return {
        "dimension": "outlier",
        "applicable": bool(fields),
        "fields": fields,
        "overall_outlier_rate": overall,
        "score": round(max(0.0, 1.0 - min(overall * 2, 1.0)) * 100, 2),
    }


def _extract_text_records(body: Dict[str, Any], df: pd.DataFrame) -> Optional[List[Any]]:
    for key in ("unstructured_rows", "texts", "notes"):
        if key in body:
            value = body.get(key)
            if not isinstance(value, list):
                raise ValueError(f"{key} 必须是数组")
            return value
    text_fields = body.get("text_fields")
    if text_fields is not None:
        if not isinstance(text_fields, list) or not all(isinstance(item, str) for item in text_fields):
            raise ValueError("text_fields 必须是字符串数组")
        candidates = [field for field in text_fields if field in df.columns]
    else:
        candidates = [field for field in TEXT_FIELD_CANDIDATES if field in df.columns]
    if not candidates:
        return None
    records = []
    for _, row in df[candidates].iterrows():
        for field in candidates:
            records.append({"field": field, "content": row.get(field)})
    return records


def _text_value(record: Any) -> str:
    if isinstance(record, str):
        return record
    if isinstance(record, Mapping):
        for field in TEXT_FIELD_CANDIDATES:
            if field in record:
                return str(record.get(field) or "")
    return ""


def assess_unstructured_records(records: Sequence[Any]) -> Dict[str, Any]:
    if len(records) > MAX_TEXT_RECORDS:
        raise ValueError(f"非结构化记录超过上限 {MAX_TEXT_RECORDS}")
    texts = []
    for record in records:
        text = _text_value(record)
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"单条文本超过上限 {MAX_TEXT_LENGTH}")
        texts.append(text)
    empty = sum(not text.strip() for text in texts)
    short = sum(bool(text.strip()) and len(text.strip()) < 10 for text in texts)
    garbled = sum(bool(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]", text)) for text in texts)
    normalized = [re.sub(r"\s+", " ", text.strip()) for text in texts if text.strip()]
    duplicate = len(normalized) - len(set(normalized))
    total = max(len(texts), 1)
    issue_rate = round((empty + short + garbled + duplicate) / (total * 4), 4)
    return {
        "dimension": "unstructured",
        "applicable": bool(texts),
        "ok": True,
        "n_records": len(texts),
        "empty_rate": round(empty / total, 4),
        "too_short_rate": round(short / total, 4),
        "garbled_rate": round(garbled / total, 4),
        "duplicate_rate": round(duplicate / total, 4),
        "issue_rate": issue_rate,
        "average_length": round(float(np.mean([len(text) for text in texts])) if texts else 0.0, 2),
        "score": round(max(0.0, 1.0 - issue_rate) * 100, 2),
    }


def assess_unstructured_notes(
    limit: int = 200,
    *,
    owner_user_id: Optional[int] = None,
    db_handler=None,
) -> Dict[str, Any]:
    if owner_user_id is None:
        return {
            "dimension": "unstructured",
            "applicable": False,
            "ok": True,
            "n_records": 0,
            "issue_rate": 0.0,
            "score": None,
            "message": "未提供非结构化数据",
        }
    handler = _resolve_db_handler(db_handler)
    rows, err = handler.query(
        """SELECT content FROM mental_health_clinical_notes
           WHERE owner_user_id=%s AND patient_id IN (
             SELECT patient_id FROM mental_health_patients WHERE owner_user_id=%s
           )
           ORDER BY id DESC LIMIT %s""",
        (int(owner_user_id), int(owner_user_id), _bounded_int(limit, 200, 1, MAX_TEXT_RECORDS)),
    )
    if err:
        return {
            "dimension": "unstructured",
            "applicable": False,
            "ok": False,
            "n_records": 0,
            "issue_rate": 0.0,
            "score": None,
            "error": str(err),
        }
    return assess_unstructured_records(rows or [])


def _normalize_multimodal_records(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    records: List[Dict[str, Any]] = []
    if isinstance(value, Mapping):
        for modality, entries in value.items():
            if isinstance(entries, int):
                records.extend({"modality": modality, "asset_id": f"{modality}-{index}"} for index in range(entries))
            elif isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, Mapping):
                        records.append({"modality": modality, **dict(entry)})
                    else:
                        records.append({"modality": modality, "uri": entry})
            else:
                raise ValueError("modalities 中每个值必须是数量或数组")
    elif isinstance(value, list):
        for index, entry in enumerate(value):
            if not isinstance(entry, Mapping):
                raise ValueError(f"multimodal_items[{index}] 必须是对象")
            records.append(dict(entry))
    else:
        raise ValueError("multimodal_items/modalities 必须是数组或对象")
    if len(records) > MAX_MULTIMODAL_RECORDS:
        raise ValueError(f"多模态记录超过上限 {MAX_MULTIMODAL_RECORDS}")
    return records


def assess_multimodal_records(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {
            "dimension": "multimodal",
            "applicable": False,
            "n_records": 0,
            "issue_rate": 0.0,
            "coverage_ratio": 0.0,
            "score": None,
            "issues": [],
            "message": "未提供多模态数据",
        }
    issues: List[Dict[str, Any]] = []
    modality_counts: Dict[str, int] = {}
    seen_ids: set[str] = set()
    seen_checksums: set[str] = set()
    total = len(records)
    invalid = 0
    for index, record in enumerate(records):
        modality = str(record.get("modality") or record.get("type") or "").strip().lower()
        modality_counts[modality or "unknown"] = modality_counts.get(modality or "unknown", 0) + 1
        record_issues = []
        if modality not in ALLOWED_MODALITIES:
            record_issues.append("unsupported_modality")
        uri = str(record.get("uri") or record.get("url") or record.get("path") or "").strip()
        if not uri:
            record_issues.append("missing_uri")
        mime_type = str(record.get("mime_type") or record.get("mime") or "").strip().lower()
        expected = MODALITY_MIME_PREFIX.get(modality)
        if expected and mime_type and not mime_type.startswith(expected):
            record_issues.append("mime_mismatch")
        size = record.get("size_bytes")
        if size is not None:
            try:
                if int(size) <= 0:
                    record_issues.append("invalid_size")
            except (TypeError, ValueError):
                record_issues.append("invalid_size")
        asset_id = str(record.get("asset_id") or record.get("id") or "").strip()
        if asset_id:
            if asset_id in seen_ids:
                record_issues.append("duplicate_asset_id")
            seen_ids.add(asset_id)
        checksum = str(record.get("checksum") or "").strip().lower()
        if checksum:
            if not re.fullmatch(r"(?:[a-f0-9]{32}|[a-f0-9]{64})", checksum):
                record_issues.append("invalid_checksum")
            elif checksum in seen_checksums:
                record_issues.append("duplicate_checksum")
            seen_checksums.add(checksum)
        if record_issues:
            invalid += 1
            issues.append({"index": index, "asset_id": asset_id or None, "issues": record_issues})
    issue_rate = round(invalid / total, 4)
    present_modalities = sum(1 for name in modality_counts if name in ALLOWED_MODALITIES)
    coverage = round(present_modalities / len(ALLOWED_MODALITIES), 4)
    return {
        "dimension": "multimodal",
        "applicable": True,
        "n_records": total,
        "modality_counts": modality_counts,
        "types_present": present_modalities,
        "types_total": len(ALLOWED_MODALITIES),
        "coverage_ratio": coverage,
        "issue_rate": issue_rate,
        "issues": issues,
        "score": round(max(0.0, 1.0 - issue_rate) * 100, 2),
    }


def assess_multimodal_data(
    body: Dict[str, Any],
    *,
    owner_user_id: Optional[int] = None,
    db_handler=None,
) -> Dict[str, Any]:
    if "multimodal_items" in body or "modalities" in body:
        value = body.get("multimodal_items") if "multimodal_items" in body else body.get("modalities")
        return assess_multimodal_records(_normalize_multimodal_records(value))
    if owner_user_id is None:
        return assess_multimodal_records([])
    handler = _resolve_db_handler(db_handler)
    rows, err = handler.query(
        """SELECT id AS asset_id, modality, mime_type, uri, size_bytes, checksum
           FROM mental_health_multimodal_assets
           WHERE owner_user_id=%s AND patient_id IN (
             SELECT patient_id FROM mental_health_patients WHERE owner_user_id=%s
           )
           ORDER BY id DESC LIMIT %s""",
        (int(owner_user_id), int(owner_user_id), MAX_MULTIMODAL_RECORDS),
    )
    if err:
        result = assess_multimodal_records([])
        result.update({"ok": False, "error": str(err)})
        return result
    return assess_multimodal_records(rows or [])


def assess_multitype_coverage(*, owner_user_id: Optional[int], db_handler=None) -> Dict[str, Any]:
    tables = {
        "patient_master": "mental_health_patients",
        "clinical_note": "mental_health_clinical_notes",
        "assessment": "mental_health_assessments",
        "medication": "mental_health_med_orders",
        "examination": "mental_health_examinations",
        "lab": "mental_health_lab_reports",
        "followup": "mental_health_followups",
        "multimodal_asset": "mental_health_multimodal_assets",
    }
    if owner_user_id is None:
        return {
            "dimension": "multitype_coverage",
            "applicable": False,
            "counts": {},
            "types_present": 0,
            "types_total": len(tables),
            "coverage_ratio": 0.0,
            "score": None,
        }
    handler = _resolve_db_handler(db_handler)
    counts: Dict[str, int] = {}
    errors: Dict[str, str] = {}
    for name, table in tables.items():
        if table == "mental_health_patients":
            sql = f"SELECT COUNT(*) AS c FROM {table} WHERE owner_user_id=%s"
        else:
            sql = (
                f"SELECT COUNT(*) AS c FROM {table} WHERE owner_user_id=%s AND patient_id IN "
                "(SELECT patient_id FROM mental_health_patients WHERE owner_user_id=%s)"
            )
        params = (int(owner_user_id),) if table == "mental_health_patients" else (int(owner_user_id), int(owner_user_id))
        rows, err = handler.query(sql, params)
        if err:
            counts[name] = 0
            errors[name] = str(err)
        else:
            counts[name] = int((rows or [{}])[0].get("c") or 0)
    present = sum(value > 0 for value in counts.values())
    coverage = round(present / len(tables), 4)
    return {
        "dimension": "multitype_coverage",
        "applicable": True,
        "counts": counts,
        "errors": errors,
        "types_present": present,
        "types_total": len(tables),
        "coverage_ratio": coverage,
        "score": round(coverage * 100, 2),
    }


def _report_root() -> Path:
    default_root = Path(__file__).resolve().parents[2] / "runtime" / "reports"
    root = Path(os.getenv("DQ213_REPORT_DIR", str(default_root))).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _write_report(report: Dict[str, Any], owner_user_id: Optional[int]) -> str:
    owner = int(owner_user_id or 0)
    report_id = secrets.token_hex(16)
    path = _report_root() / f"qc_{owner}_{report_id}.json"
    stored_report = dict(report)
    stored_report["report_id"] = report_id
    path.write_text(json.dumps(stored_report, ensure_ascii=False, indent=2, allow_nan=False, default=str), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return report_id


def get_quality_report_path(report_id: str, owner_user_id: int) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", str(report_id or "")):
        raise ValueError("非法报告编号")
    path = (_report_root() / f"qc_{int(owner_user_id)}_{report_id}.json").resolve()
    root = _report_root()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError("质控报告不存在")
    return path


def run_quality_assessment(
    body: Optional[Dict[str, Any]] = None,
    *,
    owner_user_id: Optional[int] = None,
    db_handler=None,
) -> Dict[str, Any]:
    body = body or {}
    if not isinstance(body, dict):
        raise ValueError("请求体必须是对象")
    frame, source = _load_df(body, owner_user_id=owner_user_id, db_handler=db_handler)
    if frame.empty:
        return {"ok": False, "error": "数据集为空", "source": source}

    completeness = assess_completeness(frame)
    consistency = assess_consistency(frame)
    accuracy = assess_accuracy(frame)
    outliers = assess_outliers(frame)
    text_records = _extract_text_records(body, frame)
    database_owner = owner_user_id if source == "mental_health_patients" else None
    if text_records is None:
        unstructured = assess_unstructured_notes(
            _bounded_int(body.get("note_limit"), 200, 1, MAX_TEXT_RECORDS),
            owner_user_id=database_owner,
            db_handler=db_handler,
        )
    else:
        unstructured = assess_unstructured_records(text_records)
    multimodal = assess_multimodal_data(body, owner_user_id=database_owner, db_handler=db_handler)
    coverage = assess_multitype_coverage(
        owner_user_id=database_owner,
        db_handler=db_handler,
    )

    dimensions = {
        "completeness": completeness,
        "consistency": consistency,
        "accuracy": accuracy,
        "outlier": outliers,
        "unstructured": unstructured,
        "multimodal": multimodal,
        "multitype_coverage": coverage,
    }
    scores = [
        float(item["score"])
        for item in dimensions.values()
        if item.get("applicable") and isinstance(item.get("score"), (int, float))
    ]
    health = round(float(np.mean(scores)) if scores else 0.0, 2)
    if health >= 85:
        label = "Excellent"
    elif health >= 70:
        label = "Good"
    elif health >= 55:
        label = "Fair"
    elif health >= 40:
        label = "Poor"
    else:
        label = "Critical"

    field_anomaly: Dict[str, float] = {}
    for issue in consistency.get("issues", []) + accuracy.get("issues", []):
        field = str(issue.get("field") or "")
        if field:
            field_anomaly[field] = max(field_anomaly.get(field, 0.0), float(issue.get("rate") or 0))
    for item in outliers.get("fields", []):
        field = str(item.get("field") or "")
        if field:
            field_anomaly[field] = max(field_anomaly.get(field, 0.0), float(item.get("outlier_rate") or 0))

    report = {
        "ok": True,
        "report_version": "2.1.3-safe-1",
        "source": source,
        "n_rows": int(len(frame)),
        "n_cols": int(frame.shape[1]),
        "health_score": health,
        "health_label": label,
        "core_metrics": {
            "missing_rate": completeness["overall_missing_rate"],
            "field_anomaly_rate": round(float(np.mean(list(field_anomaly.values()))) if field_anomaly else 0.0, 4),
            "outlier_rate": outliers["overall_outlier_rate"],
            "unstructured_issue_rate": float(unstructured.get("issue_rate") or 0),
            "multimodal_issue_rate": float(multimodal.get("issue_rate") or 0),
            "multimodal_coverage": float(multimodal.get("coverage_ratio") or 0),
            "multitype_coverage": float(coverage.get("coverage_ratio") or 0),
        },
        "dimensions": dimensions,
        "field_anomaly_rates": [
            {"field": field, "anomaly_rate": round(rate, 4)}
            for field, rate in sorted(field_anomaly.items(), key=lambda item: -item[1])
        ],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    if body.get("export"):
        report["report_id"] = _write_report(report, owner_user_id)
    return report
