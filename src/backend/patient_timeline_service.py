# backend/patient_timeline_service.py — 2.1.3 患者诊疗轨迹时序全景

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

EVENT_TYPES = (
    "diagnosis",
    "admission",
    "discharge",
    "medication",
    "examination",
    "lab",
    "assessment",
    "clinical_note",
    "followup",
)
MAX_TIMELINE_EVENTS = 1000
MAX_PATIENT_LIST = 200


def _resolve_db_handler(db_handler=None):
    if db_handler is not None:
        return db_handler
    try:
        from utils.mysql_utils import mysql_handler
    except Exception as exc:  # pragma: no cover - 宿主集成路径
        raise RuntimeError("未提供数据库适配器；独立测试请使用 standalone.app") from exc
    return mysql_handler


def _require_owner(owner_user_id: Optional[int]) -> int:
    if owner_user_id is None:
        raise PermissionError("诊疗轨迹查询必须提供当前用户身份")
    owner = int(owner_user_id)
    if owner <= 0:
        raise PermissionError("当前用户身份无效")
    return owner


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _parse_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nat"}:
        return None
    try:
        import pandas as pd

        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return None


def _validate_filter_date(value: Optional[str], field: str) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError(f"{field} 日期格式无效")
    return parsed


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _load_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if not value.strip():
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _safe_uri(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("/") and not text.startswith("//"):
        return text
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def _event(
    event_type: str,
    event_date: Any,
    title: Any,
    detail: Mapping[str, Any],
    *,
    modality: str = "structured",
    source_table: str,
    source_id: Any,
) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "event_date": _parse_date(event_date),
        "title": str(title or event_type),
        "detail": _safe_json(dict(detail)),
        "modality": str(modality or "structured"),
        "source_table": source_table,
        "source_id": source_id,
        "assets": [],
    }


def _optional_query(handler, sql: str, params: Sequence[Any], label: str, warnings: List[str]) -> List[Dict[str, Any]]:
    try:
        rows, error = handler.query(sql, tuple(params))
    except Exception as exc:
        warnings.append(f"{label}: {exc}")
        return []
    if error:
        warnings.append(f"{label}: {error}")
        return []
    return list(rows or [])


def _authorized_patient(handler, patient_id: str, owner_user_id: int) -> Optional[Dict[str, Any]]:
    sql = """SELECT id, patient_id, diagnosis, admission_date, discharge_date,
                    medication, outcome, HAMD_total, HAMA_total, PHQ9_total
             FROM mental_health_patients
             WHERE patient_id=%s AND owner_user_id=%s LIMIT 1"""
    params: Tuple[Any, ...] = (patient_id, owner_user_id)
    rows, error = handler.query(sql, params)
    if error:
        raise RuntimeError(error)
    return (rows or [None])[0]


def _attach_assets(events: List[Dict[str, Any]], assets: Sequence[Mapping[str, Any]]) -> int:
    by_source: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    unlinked: List[Dict[str, Any]] = []
    for row in assets:
        uri = _safe_uri(row.get("uri"))
        thumbnail_uri = _safe_uri(row.get("thumbnail_uri"))
        asset = {
            "asset_id": row.get("id") or row.get("asset_id"),
            "title": str(row.get("title") or "多模态附件"),
            "modality": str(row.get("modality") or "document"),
            "mime_type": str(row.get("mime_type") or "application/octet-stream"),
            "uri": uri,
            "thumbnail_uri": thumbnail_uri,
            "size_bytes": row.get("size_bytes"),
            "checksum": row.get("checksum"),
            "metadata": _safe_json(_load_json(row.get("metadata_json")) or {}),
        }
        source_table = str(row.get("event_source_table") or "")
        source_id = str(row.get("event_source_id") or "")
        if source_table and source_id:
            by_source.setdefault((source_table, source_id), []).append(asset)
        else:
            unlinked.append(asset)
    attached = 0
    for event in events:
        key = (str(event.get("source_table") or ""), str(event.get("source_id") or ""))
        linked = by_source.get(key, [])
        if linked:
            event["assets"].extend(linked)
            attached += len(linked)
            modalities = {str(item.get("modality") or "") for item in linked}
            if len(modalities) == 1:
                event["modality"] = next(iter(modalities))
            else:
                event["modality"] = "multimodal"
    if unlinked and events:
        events[0]["assets"].extend(unlinked)
        attached += len(unlinked)
    return attached


def build_patient_timeline(
    patient_id: str,
    event_types: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 500,
    *,
    modalities: Optional[List[str]] = None,
    keyword: Optional[str] = None,
    owner_user_id: Optional[int] = None,
    db_handler=None,
) -> Dict[str, Any]:
    patient_key = str(patient_id or "").strip()
    if not patient_key:
        raise ValueError("patient_id 必填")
    if len(patient_key) > 128:
        raise ValueError("patient_id 过长")
    owner = _require_owner(owner_user_id)
    handler = _resolve_db_handler(db_handler)
    wanted = set(EVENT_TYPES if event_types is None else event_types)
    unknown_types = sorted(wanted.difference(EVENT_TYPES))
    if unknown_types:
        raise ValueError(f"不支持的事件类型: {unknown_types}")
    start = _validate_filter_date(start_date, "start_date")
    end = _validate_filter_date(end_date, "end_date")
    if start and end and start > end:
        raise ValueError("start_date 不能晚于 end_date")
    event_limit = _bounded_int(limit, 500, 1, MAX_TIMELINE_EVENTS)
    modality_filter = {str(item).strip().lower() for item in (modalities or []) if str(item).strip()}
    search_term = str(keyword or "").strip().lower()
    if len(search_term) > 200:
        raise ValueError("keyword 过长")

    patient = _authorized_patient(handler, patient_key, owner)
    if not patient:
        return {"ok": False, "error": "患者不存在或无访问权限", "patient_id": patient_key, "events": []}

    events: List[Dict[str, Any]] = []
    warnings: List[str] = []
    patient_source_id = patient.get("id")
    admission_date = patient.get("admission_date")
    if "diagnosis" in wanted and patient.get("diagnosis"):
        events.append(
            _event(
                "diagnosis",
                admission_date,
                f"诊断：{patient.get('diagnosis')}",
                {"diagnosis": patient.get("diagnosis"), "outcome": patient.get("outcome")},
                source_table="mental_health_patients",
                source_id=patient_source_id,
            )
        )
    if "admission" in wanted and patient.get("admission_date"):
        events.append(
            _event(
                "admission",
                patient.get("admission_date"),
                "入院",
                {"admission_date": patient.get("admission_date")},
                source_table="mental_health_patients",
                source_id=patient_source_id,
            )
        )
    if "discharge" in wanted and patient.get("discharge_date"):
        events.append(
            _event(
                "discharge",
                patient.get("discharge_date"),
                "出院",
                {"discharge_date": patient.get("discharge_date"), "outcome": patient.get("outcome")},
                source_table="mental_health_patients",
                source_id=patient_source_id,
            )
        )
    if "medication" in wanted and patient.get("medication"):
        events.append(
            _event(
                "medication",
                admission_date,
                f"入院用药：{patient.get('medication')}",
                {"medication": patient.get("medication"), "source": "patient_master"},
                source_table="mental_health_patients",
                source_id=patient_source_id,
            )
        )
    if "assessment" in wanted:
        for scale, column in (("HAMD", "HAMD_total"), ("HAMA", "HAMA_total"), ("PHQ9", "PHQ9_total")):
            if patient.get(column) is not None:
                events.append(
                    _event(
                        "assessment",
                        admission_date,
                        f"{scale} 总分 {patient.get(column)}",
                        {"scale": scale, "total_score": patient.get(column), "source": "patient_master"},
                        modality="scale",
                        source_table="mental_health_patients",
                        source_id=patient_source_id,
                    )
                )

    if "clinical_note" in wanted:
        rows = _optional_query(
            handler,
            """SELECT id, note_type, note_date, title, content
               FROM mental_health_clinical_notes
               WHERE patient_id=%s AND owner_user_id=%s ORDER BY note_date DESC, id DESC LIMIT 200""",
            (patient_key, owner),
            "clinical_note",
            warnings,
        )
        for row in rows:
            content = str(row.get("content") or "")
            events.append(
                _event(
                    "clinical_note",
                    row.get("note_date"),
                    row.get("title") or f"病历-{row.get('note_type') or 'progress'}",
                    {"note_type": row.get("note_type"), "preview": content[:500], "content_len": len(content)},
                    modality="text",
                    source_table="mental_health_clinical_notes",
                    source_id=row.get("id"),
                )
            )

    if "assessment" in wanted:
        rows = _optional_query(
            handler,
            """SELECT id, scale_name, assess_date, total_score, item_scores, visit_type
               FROM mental_health_assessments
               WHERE patient_id=%s AND owner_user_id=%s ORDER BY assess_date DESC, id DESC LIMIT 300""",
            (patient_key, owner),
            "assessment",
            warnings,
        )
        for row in rows:
            events.append(
                _event(
                    "assessment",
                    row.get("assess_date"),
                    f"{row.get('scale_name') or '量表'} = {row.get('total_score')}",
                    {
                        "scale_name": row.get("scale_name"),
                        "total_score": row.get("total_score"),
                        "visit_type": row.get("visit_type"),
                        "item_scores": _load_json(row.get("item_scores")),
                    },
                    modality="scale",
                    source_table="mental_health_assessments",
                    source_id=row.get("id"),
                )
            )

    if "medication" in wanted:
        rows = _optional_query(
            handler,
            """SELECT id, drug_name, dose, frequency, route, start_date, end_date, status
               FROM mental_health_med_orders
               WHERE patient_id=%s AND owner_user_id=%s ORDER BY start_date DESC, id DESC LIMIT 300""",
            (patient_key, owner),
            "medication",
            warnings,
        )
        for row in rows:
            events.append(
                _event(
                    "medication",
                    row.get("start_date"),
                    f"用药 {row.get('drug_name') or ''} {row.get('dose') or ''}".strip(),
                    {
                        "drug_name": row.get("drug_name"),
                        "dose": row.get("dose"),
                        "frequency": row.get("frequency"),
                        "route": row.get("route"),
                        "status": row.get("status"),
                        "end_date": row.get("end_date"),
                    },
                    source_table="mental_health_med_orders",
                    source_id=row.get("id"),
                )
            )

    if "examination" in wanted:
        rows = _optional_query(
            handler,
            """SELECT id, exam_type, exam_date, body_site, finding, conclusion
               FROM mental_health_examinations
               WHERE patient_id=%s AND owner_user_id=%s ORDER BY exam_date DESC, id DESC LIMIT 300""",
            (patient_key, owner),
            "examination",
            warnings,
        )
        for row in rows:
            events.append(
                _event(
                    "examination",
                    row.get("exam_date"),
                    f"检查：{row.get('exam_type') or '辅助检查'}",
                    {
                        "exam_type": row.get("exam_type"),
                        "body_site": row.get("body_site"),
                        "finding": row.get("finding"),
                        "conclusion": row.get("conclusion"),
                    },
                    modality="image",
                    source_table="mental_health_examinations",
                    source_id=row.get("id"),
                )
            )

    if "lab" in wanted:
        rows = _optional_query(
            handler,
            """SELECT id, report_date, item_name, value_num, value_text, unit, flag
               FROM mental_health_lab_reports
               WHERE patient_id=%s AND owner_user_id=%s ORDER BY report_date DESC, id DESC LIMIT 500""",
            (patient_key, owner),
            "lab",
            warnings,
        )
        for row in rows:
            value = row.get("value_num") if row.get("value_num") is not None else row.get("value_text")
            events.append(
                _event(
                    "lab",
                    row.get("report_date"),
                    f"检验 {row.get('item_name') or ''} = {value if value is not None else ''}".strip(),
                    {
                        "item_name": row.get("item_name"),
                        "value": value,
                        "unit": row.get("unit"),
                        "flag": row.get("flag"),
                    },
                    modality="lab",
                    source_table="mental_health_lab_reports",
                    source_id=row.get("id"),
                )
            )

    if "followup" in wanted:
        rows = _optional_query(
            handler,
            """SELECT id, visit_date, visit_type, HAMD_total, HAMA_total, PHQ9_total,
                      medication, medication_dose_mg, adverse_events, notes
               FROM mental_health_followups
               WHERE patient_id=%s AND owner_user_id=%s ORDER BY visit_date DESC, id DESC LIMIT 500""",
            (patient_key, owner),
            "followup",
            warnings,
        )
        for row in rows:
            events.append(
                _event(
                    "followup",
                    row.get("visit_date"),
                    f"随访：{row.get('visit_type') or '常规'}",
                    {
                        "visit_type": row.get("visit_type"),
                        "HAMD_total": row.get("HAMD_total"),
                        "HAMA_total": row.get("HAMA_total"),
                        "PHQ9_total": row.get("PHQ9_total"),
                        "medication": row.get("medication"),
                        "medication_dose_mg": row.get("medication_dose_mg"),
                        "adverse_events": _load_json(row.get("adverse_events")),
                        "notes": row.get("notes"),
                    },
                    source_table="mental_health_followups",
                    source_id=row.get("id"),
                )
            )

    assets = _optional_query(
        handler,
        """SELECT id, modality, mime_type, uri, thumbnail_uri, title, size_bytes, checksum,
                  event_source_table, event_source_id, metadata_json, captured_at
           FROM mental_health_multimodal_assets
           WHERE patient_id=%s AND owner_user_id=%s ORDER BY captured_at DESC, id DESC LIMIT 500""",
        (patient_key, owner),
        "multimodal_asset",
        warnings,
    )
    linked_asset_count = _attach_assets(events, assets)

    filtered: List[Dict[str, Any]] = []
    for event in events:
        event_date = event.get("event_date")
        if start and (not event_date or event_date < start):
            continue
        if end and (not event_date or event_date > end):
            continue
        event_modalities = {str(event.get("modality") or "").lower()}
        event_modalities.update(str(asset.get("modality") or "").lower() for asset in event.get("assets", []))
        if modality_filter and event_modalities.isdisjoint(modality_filter):
            continue
        if search_term:
            haystack = json.dumps(
                {"title": event.get("title"), "detail": event.get("detail")},
                ensure_ascii=False,
                default=str,
            ).lower()
            if search_term not in haystack:
                continue
        filtered.append(event)

    filtered.sort(key=lambda item: (item.get("event_date") is None, item.get("event_date") or "9999-12-31", str(item.get("source_id") or "")))
    filtered = filtered[:event_limit]
    by_type: Dict[str, int] = {}
    by_modality: Dict[str, int] = {}
    for event in filtered:
        event_type = str(event.get("event_type") or "unknown")
        modality = str(event.get("modality") or "unknown")
        by_type[event_type] = by_type.get(event_type, 0) + 1
        by_modality[modality] = by_modality.get(modality, 0) + 1
    dates = [event["event_date"] for event in filtered if event.get("event_date")]
    return {
        "ok": True,
        "patient_id": patient_key,
        "n_events": len(filtered),
        "n_assets": sum(len(event.get("assets", [])) for event in filtered),
        "linked_assets": linked_asset_count,
        "by_type": by_type,
        "by_modality": by_modality,
        "date_range": {"start": min(dates) if dates else None, "end": max(dates) if dates else None},
        "filters": {
            "event_types": sorted(wanted),
            "start_date": start,
            "end_date": end,
            "modalities": sorted(modality_filter),
            "keyword": search_term or None,
            "limit": event_limit,
        },
        "warnings": warnings,
        "events": filtered,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def list_timeline_patients(
    limit: int = 50,
    *,
    owner_user_id: Optional[int] = None,
    db_handler=None,
) -> Dict[str, Any]:
    owner = _require_owner(owner_user_id)
    handler = _resolve_db_handler(db_handler)
    row_limit = _bounded_int(limit, 50, 1, MAX_PATIENT_LIST)
    sql = """SELECT patient_id, diagnosis, admission_date
             FROM mental_health_patients
             WHERE owner_user_id=%s ORDER BY id DESC LIMIT %s"""
    params: Tuple[Any, ...] = (owner, row_limit)
    rows, error = handler.query(sql, params)
    if error:
        raise RuntimeError(error)
    return {"ok": True, "items": list(rows or []), "limit": row_limit}
