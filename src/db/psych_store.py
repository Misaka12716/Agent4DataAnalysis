# db/psych_store.py
# 精神专科分析域数据访问层

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

from db.psych_schema import (
    TABLE_PSYCH_ANALYSIS_PARAMS,
    TABLE_PSYCH_CAPABILITIES,
    TABLE_PSYCH_CAPABILITY_CHANGELOG,
    TABLE_PSYCH_DATA_RECORDS,
    TABLE_PSYCH_DATASETS,
    TABLE_PSYCH_EXPORTS,
    TABLE_PSYCH_FEATURES,
    TABLE_PSYCH_INGEST_JOBS,
    TABLE_PSYCH_LLM_EXTRACTIONS,
    TABLE_PSYCH_ML_MODELS,
    TABLE_PSYCH_PARAM_TEMPLATES,
    TABLE_PSYCH_PIPELINES,
    TABLE_PSYCH_SCALE_FORMS,
    TABLE_PSYCH_SCALE_SCORES,
    TABLE_PSYCH_STATS_RESULTS,
    TABLE_PSYCH_TASKS,
    TABLE_PSYCH_VAR_CATEGORIES,
    TABLE_PSYCH_VARIABLES,
    ensure_psych_tables,
)
from utils.mysql_utils import mysql_handler

_tables_ready = False


def _ensure() -> Optional[str]:
    global _tables_ready
    if _tables_ready:
        return None
    try:
        ensure_psych_tables(mysql_handler)
        _tables_ready = True
        return None
    except Exception as exc:
        return str(exc)


def _json_dump(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _parse_json_fields(row: Dict[str, Any], keys: Tuple[str, ...]) -> Dict[str, Any]:
    out = dict(row)
    for key in keys:
        if key not in out:
            continue
        val = out[key]
        if isinstance(val, str):
            try:
                out[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    for key in ("created_at", "updated_at", "finished_at", "scored_at", "record_time"):
        if key in out and isinstance(out[key], (datetime.datetime, datetime.date)):
            out[key] = out[key].isoformat()
    return out


def _insert(table: str, data: Dict[str, Any], json_keys: Tuple[str, ...] = ()) -> Tuple[Optional[int], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    payload = dict(data)
    for k in json_keys:
        if k in payload:
            payload[k] = _json_dump(payload.get(k))
    _, row_id, ierr = mysql_handler.insert(table, payload)
    if ierr:
        return None, ierr
    return int(row_id) if row_id is not None else None, None


def _get_by_id(table: str, row_id: int, json_keys: Tuple[str, ...] = ()) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(f"SELECT * FROM {table} WHERE id = %s", (row_id,))
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], json_keys), None


# ---------- tasks ----------

def insert_task(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_TASKS, data, ("params_json", "result_json"))


def get_task_by_task_id(task_id: str) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_TASKS} WHERE task_id = %s", (task_id,)
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("params_json", "result_json")), None


def list_tasks(user_id: int, module: Optional[str] = None, limit: int = 50) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_TASKS} WHERE user_id = %s"
    params: List[Any] = [user_id]
    if module:
        sql += " AND module = %s"
        params.append(module)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(int(limit))
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("params_json", "result_json")) for r in (rows or [])], None


def update_task(task_id: str, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    payload = dict(fields)
    for k in ("params_json", "result_json"):
        if k in payload:
            payload[k] = _json_dump(payload.get(k))
    sets = ", ".join(f"{k} = %s" for k in payload)
    vals = list(payload.values()) + [task_id]
    _, uerr = mysql_handler.execute(
        f"UPDATE {TABLE_PSYCH_TASKS} SET {sets} WHERE task_id = %s", tuple(vals)
    )
    return uerr


# ---------- datasets ----------

def insert_dataset(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_DATASETS, data, ("schema_json",))


def get_dataset(dataset_id: int, user_id: Optional[int] = None) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    sql = f"SELECT * FROM {TABLE_PSYCH_DATASETS} WHERE id = %s"
    params: List[Any] = [dataset_id]
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("schema_json",)), None


def list_datasets(user_id: int, limit: int = 50) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_DATASETS} WHERE user_id = %s ORDER BY id DESC LIMIT %s",
        (user_id, int(limit)),
    )
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("schema_json",)) for r in (rows or [])], None


def update_dataset(dataset_id: int, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    payload = dict(fields)
    if "schema_json" in payload:
        payload["schema_json"] = _json_dump(payload.get("schema_json"))
    sets = ", ".join(f"{k} = %s" for k in payload)
    vals = list(payload.values()) + [dataset_id]
    _, uerr = mysql_handler.execute(
        f"UPDATE {TABLE_PSYCH_DATASETS} SET {sets} WHERE id = %s", tuple(vals)
    )
    return uerr


def insert_data_record(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_DATA_RECORDS, data, ("tags_json",))


def list_data_records(
    dataset_id: int,
    patient_key: Optional[str] = None,
    record_type: Optional[str] = None,
    limit: int = 100,
) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_DATA_RECORDS} WHERE dataset_id = %s"
    params: List[Any] = [dataset_id]
    if patient_key:
        sql += " AND patient_key = %s"
        params.append(patient_key)
    if record_type:
        sql += " AND record_type = %s"
        params.append(record_type)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(int(limit))
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("tags_json",)) for r in (rows or [])], None


def insert_ingest_job(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_INGEST_JOBS, data, ("stats_json",))


def update_ingest_job(job_id: str, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    payload = dict(fields)
    if "stats_json" in payload:
        payload["stats_json"] = _json_dump(payload.get("stats_json"))
    sets = ", ".join(f"{k} = %s" for k in payload)
    vals = list(payload.values()) + [job_id]
    _, uerr = mysql_handler.execute(
        f"UPDATE {TABLE_PSYCH_INGEST_JOBS} SET {sets} WHERE job_id = %s", tuple(vals)
    )
    return uerr


# ---------- variables ----------

def insert_variable(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_VARIABLES, data, ("mapping_json", "relations_json"))


def get_variable(var_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_VARIABLES} WHERE id = %s AND user_id = %s",
        (var_id, user_id),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("mapping_json", "relations_json")), None


def list_variables(
    user_id: int, dataset_id: Optional[int] = None, limit: int = 500
) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_VARIABLES} WHERE user_id = %s"
    params: List[Any] = [user_id]
    if dataset_id is not None:
        sql += " AND dataset_id = %s"
        params.append(dataset_id)
    sql += " ORDER BY id ASC LIMIT %s"
    params.append(int(limit))
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("mapping_json", "relations_json")) for r in (rows or [])], None


def update_variable(var_id: int, user_id: int, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    payload = dict(fields)
    for k in ("mapping_json", "relations_json"):
        if k in payload:
            payload[k] = _json_dump(payload.get(k))
    sets = ", ".join(f"{k} = %s" for k in payload)
    vals = list(payload.values()) + [var_id, user_id]
    _, uerr = mysql_handler.execute(
        f"UPDATE {TABLE_PSYCH_VARIABLES} SET {sets} WHERE id = %s AND user_id = %s",
        tuple(vals),
    )
    return uerr


def delete_variable(var_id: int, user_id: int) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    _, derr = mysql_handler.execute(
        f"DELETE FROM {TABLE_PSYCH_VARIABLES} WHERE id = %s AND user_id = %s",
        (var_id, user_id),
    )
    return derr


def insert_var_category(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_VAR_CATEGORIES, data)


def list_var_categories(user_id: int) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_VAR_CATEGORIES} WHERE user_id = %s ORDER BY sort_order, id",
        (user_id,),
    )
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ()) for r in (rows or [])], None


def update_var_category(cat_id: int, user_id: int, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    sets = ", ".join(f"{k} = %s" for k in fields)
    vals = list(fields.values()) + [cat_id, user_id]
    _, uerr = mysql_handler.execute(
        f"UPDATE {TABLE_PSYCH_VAR_CATEGORIES} SET {sets} WHERE id = %s AND user_id = %s",
        tuple(vals),
    )
    return uerr


def delete_var_category(cat_id: int, user_id: int) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    _, derr = mysql_handler.execute(
        f"DELETE FROM {TABLE_PSYCH_VAR_CATEGORIES} WHERE id = %s AND user_id = %s",
        (cat_id, user_id),
    )
    return derr


# ---------- param templates / analysis params ----------

def insert_param_template(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_PARAM_TEMPLATES, data, ("params_json",))


def list_param_templates(
    user_id: int, module: Optional[str] = None
) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_PARAM_TEMPLATES} WHERE user_id = %s"
    params: List[Any] = [user_id]
    if module:
        sql += " AND module = %s"
        params.append(module)
    sql += " ORDER BY id DESC"
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("params_json",)) for r in (rows or [])], None


def upsert_analysis_param(user_id: int, scope: str, param_key: str, value: Any) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    existing, qerr = mysql_handler.query(
        f"SELECT id FROM {TABLE_PSYCH_ANALYSIS_PARAMS} WHERE user_id=%s AND scope=%s AND param_key=%s",
        (user_id, scope, param_key),
    )
    if qerr:
        return qerr
    val = _json_dump(value)
    if existing:
        _, uerr = mysql_handler.execute(
            f"UPDATE {TABLE_PSYCH_ANALYSIS_PARAMS} SET value_json=%s WHERE id=%s",
            (val, existing[0]["id"]),
        )
        return uerr
    _, _, ierr = mysql_handler.insert(
        TABLE_PSYCH_ANALYSIS_PARAMS,
        {"user_id": user_id, "scope": scope, "param_key": param_key, "value_json": val},
    )
    return ierr


def list_analysis_params(user_id: int, scope: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_ANALYSIS_PARAMS} WHERE user_id = %s"
    params: List[Any] = [user_id]
    if scope:
        sql += " AND scope = %s"
        params.append(scope)
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("value_json",)) for r in (rows or [])], None


# ---------- stats / ml / features ----------

def insert_stats_result(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_STATS_RESULTS, data, ("summary_json", "tables_json"))


def list_stats_results(task_id: str) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_STATS_RESULTS} WHERE task_id = %s ORDER BY id",
        (task_id,),
    )
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("summary_json", "tables_json")) for r in (rows or [])], None


def insert_ml_model(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_ML_MODELS, data, ("metrics_json", "feature_list_json"))


def get_ml_model(model_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_ML_MODELS} WHERE id = %s AND user_id = %s",
        (model_id, user_id),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("metrics_json", "feature_list_json")), None


def list_ml_models(user_id: int, limit: int = 50) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_ML_MODELS} WHERE user_id = %s AND status='active' ORDER BY id DESC LIMIT %s",
        (user_id, int(limit)),
    )
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("metrics_json", "feature_list_json")) for r in (rows or [])], None


def insert_feature_set(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_FEATURES, data, ("meta_json",))


def get_feature_set(feat_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_FEATURES} WHERE id = %s AND user_id = %s",
        (feat_id, user_id),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("meta_json",)), None


def list_feature_sets(user_id: int, dataset_id: Optional[int] = None) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_FEATURES} WHERE user_id = %s"
    params: List[Any] = [user_id]
    if dataset_id is not None:
        sql += " AND dataset_id = %s"
        params.append(dataset_id)
    sql += " ORDER BY id DESC"
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("meta_json",)) for r in (rows or [])], None


# ---------- scales ----------

def upsert_scale_form(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    code = data.get("scale_code")
    ver = data.get("version") or "1.0"
    rows, qerr = mysql_handler.query(
        f"SELECT id FROM {TABLE_PSYCH_SCALE_FORMS} WHERE scale_code=%s AND version=%s",
        (code, ver),
    )
    if qerr:
        return None, qerr
    payload = {
        "scale_code": code,
        "version": ver,
        "display_name": data.get("display_name"),
        "items_json": _json_dump(data.get("items_json")),
        "scoring_json": _json_dump(data.get("scoring_json")),
    }
    if rows:
        sid = int(rows[0]["id"])
        _, uerr = mysql_handler.execute(
            f"UPDATE {TABLE_PSYCH_SCALE_FORMS} SET display_name=%s, items_json=%s, scoring_json=%s WHERE id=%s",
            (payload["display_name"], payload["items_json"], payload["scoring_json"], sid),
        )
        return (sid, uerr) if not uerr else (None, uerr)
    return _insert(TABLE_PSYCH_SCALE_FORMS, payload)


def get_scale_form(scale_code: str, version: str = "1.0") -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_SCALE_FORMS} WHERE scale_code=%s AND version=%s",
        (scale_code, version),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("items_json", "scoring_json")), None


def list_scale_forms() -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    rows, qerr = mysql_handler.query(f"SELECT * FROM {TABLE_PSYCH_SCALE_FORMS} ORDER BY scale_code")
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("items_json", "scoring_json")) for r in (rows or [])], None


def insert_scale_score(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_SCALE_SCORES, data, ("item_scores_json", "subscales_json"))


def list_scale_scores(
    user_id: int,
    scale_code: Optional[str] = None,
    patient_key: Optional[str] = None,
    dataset_id: Optional[int] = None,
    limit: int = 200,
) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_SCALE_SCORES} WHERE user_id = %s"
    params: List[Any] = [user_id]
    if scale_code:
        sql += " AND scale_code = %s"
        params.append(scale_code)
    if patient_key:
        sql += " AND patient_key = %s"
        params.append(patient_key)
    if dataset_id is not None:
        sql += " AND dataset_id = %s"
        params.append(dataset_id)
    sql += " ORDER BY scored_at DESC LIMIT %s"
    params.append(int(limit))
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("item_scores_json", "subscales_json")) for r in (rows or [])], None


# ---------- llm / exports / capabilities / pipelines ----------

def insert_llm_extraction(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_LLM_EXTRACTIONS, data, ("result_json",))


def insert_export(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_EXPORTS, data)


def get_export(export_id: str, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_EXPORTS} WHERE export_id = %s AND user_id = %s",
        (export_id, user_id),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ()), None


def upsert_capability(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    cid = data.get("capability_id")
    rows, qerr = mysql_handler.query(
        f"SELECT id FROM {TABLE_PSYCH_CAPABILITIES} WHERE capability_id = %s", (cid,)
    )
    if qerr:
        return None, qerr
    payload = {
        "capability_id": cid,
        "kind": data.get("kind"),
        "impl_ref": data.get("impl_ref"),
        "version": data.get("version") or "1.0.0",
        "enabled": 1 if data.get("enabled", True) else 0,
        "meta_json": _json_dump(data.get("meta_json")),
    }
    if rows:
        sid = int(rows[0]["id"])
        _, uerr = mysql_handler.execute(
            f"UPDATE {TABLE_PSYCH_CAPABILITIES} SET kind=%s, impl_ref=%s, version=%s, enabled=%s, meta_json=%s WHERE id=%s",
            (payload["kind"], payload["impl_ref"], payload["version"], payload["enabled"], payload["meta_json"], sid),
        )
        return (sid, uerr) if not uerr else (None, uerr)
    return _insert(TABLE_PSYCH_CAPABILITIES, payload)


def get_capability(capability_id: str) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_CAPABILITIES} WHERE capability_id = %s", (capability_id,)
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("meta_json",)), None


def list_capabilities(kind: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_CAPABILITIES}"
    params: List[Any] = []
    if kind:
        sql += " WHERE kind = %s"
        params.append(kind)
    sql += " ORDER BY kind, capability_id"
    rows, qerr = mysql_handler.query(sql, tuple(params) if params else None)
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("meta_json",)) for r in (rows or [])], None


def update_capability(capability_id: str, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    payload = dict(fields)
    if "meta_json" in payload:
        payload["meta_json"] = _json_dump(payload.get("meta_json"))
    if "enabled" in payload:
        payload["enabled"] = 1 if payload["enabled"] else 0
    sets = ", ".join(f"{k} = %s" for k in payload)
    vals = list(payload.values()) + [capability_id]
    _, uerr = mysql_handler.execute(
        f"UPDATE {TABLE_PSYCH_CAPABILITIES} SET {sets} WHERE capability_id = %s",
        tuple(vals),
    )
    return uerr


def insert_capability_changelog(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_CAPABILITY_CHANGELOG, data)


def list_capability_changelog(capability_id: Optional[str] = None, limit: int = 50) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    sql = f"SELECT * FROM {TABLE_PSYCH_CAPABILITY_CHANGELOG}"
    params: List[Any] = []
    if capability_id:
        sql += " WHERE capability_id = %s"
        params.append(capability_id)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(int(limit))
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ()) for r in (rows or [])], None


def insert_pipeline(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    return _insert(TABLE_PSYCH_PIPELINES, data, ("steps_json",))


def get_pipeline(pipe_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_PIPELINES} WHERE id = %s AND user_id = %s",
        (pipe_id, user_id),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("steps_json",)), None


def list_pipelines(user_id: int) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_PSYCH_PIPELINES} WHERE user_id = %s ORDER BY id DESC",
        (user_id,),
    )
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("steps_json",)) for r in (rows or [])], None


def update_pipeline(pipe_id: int, user_id: int, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    payload = dict(fields)
    if "steps_json" in payload:
        payload["steps_json"] = _json_dump(payload.get("steps_json"))
    if "enabled" in payload:
        payload["enabled"] = 1 if payload["enabled"] else 0
    sets = ", ".join(f"{k} = %s" for k in payload)
    vals = list(payload.values()) + [pipe_id, user_id]
    _, uerr = mysql_handler.execute(
        f"UPDATE {TABLE_PSYCH_PIPELINES} SET {sets} WHERE id = %s AND user_id = %s",
        tuple(vals),
    )
    return uerr
