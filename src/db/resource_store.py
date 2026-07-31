# db/resource_store.py
# 个人资源管理数据访问层

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

from db.resource_schema import (
    TABLE_USER_DATASET_VERSIONS,
    TABLE_USER_DATASETS,
    TABLE_USER_FILES,
    TABLE_USER_MODELS,
    ensure_resource_tables,
)
from utils.mysql_utils import mysql_handler

_tables_ready = False


def _ensure() -> Optional[str]:
    global _tables_ready
    if _tables_ready:
        return None
    try:
        ensure_resource_tables(mysql_handler)
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
    for key in ("created_at", "updated_at", "deleted_at"):
        if key in out and isinstance(out[key], (datetime.datetime, datetime.date)):
            out[key] = out[key].isoformat()
    return out


# ---------- files ----------


def insert_file_node(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    payload = dict(data)
    if "tags" in payload:
        payload["tags"] = _json_dump(payload.get("tags"))
    _, node_id, ierr = mysql_handler.insert(TABLE_USER_FILES, payload)
    if ierr:
        return None, ierr
    return int(node_id) if node_id is not None else None, None


def get_file_node(user_id: int, node_id: int, include_deleted: bool = False) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    sql = f"SELECT * FROM {TABLE_USER_FILES} WHERE id = %s AND user_id = %s"
    params: List[Any] = [node_id, user_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("tags",)), None


def list_children(
    user_id: int,
    parent_id: Optional[int],
) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    if parent_id is None:
        sql = (
            f"SELECT * FROM {TABLE_USER_FILES} WHERE user_id = %s AND parent_id IS NULL "
            f"AND deleted_at IS NULL ORDER BY node_type DESC, name ASC"
        )
        rows, qerr = mysql_handler.query(sql, (user_id,))
    else:
        sql = (
            f"SELECT * FROM {TABLE_USER_FILES} WHERE user_id = %s AND parent_id = %s "
            f"AND deleted_at IS NULL ORDER BY node_type DESC, name ASC"
        )
        rows, qerr = mysql_handler.query(sql, (user_id, parent_id))
    if qerr:
        return [], qerr
    return [_parse_json_fields(r, ("tags",)) for r in rows], None


def find_sibling_by_name(
    user_id: int,
    parent_id: Optional[int],
    name: str,
) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    if parent_id is None:
        sql = (
            f"SELECT * FROM {TABLE_USER_FILES} WHERE user_id = %s AND parent_id IS NULL "
            f"AND name = %s AND deleted_at IS NULL LIMIT 1"
        )
        rows, qerr = mysql_handler.query(sql, (user_id, name))
    else:
        sql = (
            f"SELECT * FROM {TABLE_USER_FILES} WHERE user_id = %s AND parent_id = %s "
            f"AND name = %s AND deleted_at IS NULL LIMIT 1"
        )
        rows, qerr = mysql_handler.query(sql, (user_id, parent_id, name))
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("tags",)), None


def update_file_node(user_id: int, node_id: int, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    if not fields:
        return None
    payload = dict(fields)
    if "tags" in payload:
        payload["tags"] = _json_dump(payload.get("tags"))
    sets = ", ".join(f"{k} = %s" for k in payload.keys())
    values = list(payload.values()) + [node_id, user_id]
    sql = f"UPDATE {TABLE_USER_FILES} SET {sets} WHERE id = %s AND user_id = %s"
    _, uerr = mysql_handler.execute(sql, tuple(values))
    return uerr


def soft_delete_file_subtree(user_id: int, root_id: int) -> Optional[str]:
    """软删除节点及其全部子孙（BFS）。"""
    err = _ensure()
    if err:
        return err
    queue = [root_id]
    seen = set()
    while queue:
        nid = queue.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        children, cerr = list_children(user_id, nid)
        if cerr:
            return cerr
        for child in children:
            queue.append(int(child["id"]))
    if not seen:
        return None
    placeholders = ", ".join(["%s"] * len(seen))
    sql = (
        f"UPDATE {TABLE_USER_FILES} SET deleted_at = CURRENT_TIMESTAMP "
        f"WHERE user_id = %s AND id IN ({placeholders}) AND deleted_at IS NULL"
    )
    _, derr = mysql_handler.execute(sql, (user_id, *sorted(seen)))
    return derr


def list_descendant_ids(user_id: int, root_id: int) -> Tuple[List[int], Optional[str]]:
    queue = [root_id]
    seen: List[int] = []
    visited = set()
    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        seen.append(nid)
        children, cerr = list_children(user_id, nid)
        if cerr:
            return [], cerr
        for child in children:
            queue.append(int(child["id"]))
    return seen, None


# ---------- datasets ----------


def insert_dataset(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    _, ds_id, ierr = mysql_handler.insert(TABLE_USER_DATASETS, data)
    if ierr:
        return None, ierr
    return int(ds_id) if ds_id is not None else None, None


def get_dataset(user_id: int, dataset_id: int) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_USER_DATASETS} WHERE id = %s AND user_id = %s",
        (dataset_id, user_id),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ()), None


def list_datasets(
    user_id: int,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[dict], int, Optional[str]]:
    err = _ensure()
    if err:
        return [], 0, err
    where = ["user_id = %s"]
    params: List[Any] = [user_id]
    if status:
        where.append("status = %s")
        params.append(status)
    if keyword:
        where.append("(name LIKE %s OR description LIKE %s)")
        like = f"%{keyword}%"
        params.extend([like, like])
    where_sql = " AND ".join(where)
    count_rows, cerr = mysql_handler.query(
        f"SELECT COUNT(*) AS cnt FROM {TABLE_USER_DATASETS} WHERE {where_sql}",
        tuple(params),
    )
    if cerr:
        return [], 0, cerr
    total = int((count_rows[0] or {}).get("cnt") or 0) if count_rows else 0
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_USER_DATASETS} WHERE {where_sql} "
        f"ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        tuple(params + [limit, offset]),
    )
    if qerr:
        return [], 0, qerr
    return [_parse_json_fields(r, ()) for r in rows], total, None


def update_dataset(user_id: int, dataset_id: int, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    if not fields:
        return None
    sets = ", ".join(f"{k} = %s" for k in fields.keys())
    values = list(fields.values()) + [dataset_id, user_id]
    sql = f"UPDATE {TABLE_USER_DATASETS} SET {sets} WHERE id = %s AND user_id = %s"
    _, uerr = mysql_handler.execute(sql, tuple(values))
    return uerr


def insert_dataset_version(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    payload = dict(data)
    for key in ("schema_json", "missing_stats_json", "preview_json"):
        if key in payload:
            payload[key] = _json_dump(payload.get(key))
    _, vid, ierr = mysql_handler.insert(TABLE_USER_DATASET_VERSIONS, payload)
    if ierr:
        return None, ierr
    return int(vid) if vid is not None else None, None


def get_dataset_version(dataset_id: int, version: int) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_USER_DATASET_VERSIONS} WHERE dataset_id = %s AND version = %s",
        (dataset_id, version),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("schema_json", "missing_stats_json", "preview_json")), None


def list_dataset_versions(dataset_id: int) -> Tuple[List[dict], Optional[str]]:
    err = _ensure()
    if err:
        return [], err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_USER_DATASET_VERSIONS} WHERE dataset_id = %s ORDER BY version DESC",
        (dataset_id,),
    )
    if qerr:
        return [], qerr
    return [
        _parse_json_fields(r, ("schema_json", "missing_stats_json", "preview_json"))
        for r in rows
    ], None


def count_datasets_by_source_file(user_id: int, file_id: int) -> Tuple[int, Optional[str]]:
    err = _ensure()
    if err:
        return 0, err
    rows, qerr = mysql_handler.query(
        f"SELECT COUNT(*) AS cnt FROM {TABLE_USER_DATASETS} "
        f"WHERE user_id = %s AND source_file_id = %s AND status = 'active'",
        (user_id, file_id),
    )
    if qerr:
        return 0, qerr
    return int((rows[0] or {}).get("cnt") or 0) if rows else 0, None


# ---------- models ----------


def insert_model(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    payload = dict(data)
    for key in ("features", "metrics", "params"):
        if key in payload:
            payload[key] = _json_dump(payload.get(key))
    _, mid, ierr = mysql_handler.insert(TABLE_USER_MODELS, payload)
    if ierr:
        return None, ierr
    return int(mid) if mid is not None else None, None


def get_model(user_id: int, model_id: int, include_deleted: bool = False) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    sql = f"SELECT * FROM {TABLE_USER_MODELS} WHERE id = %s AND user_id = %s"
    params: List[Any] = [model_id, user_id]
    if not include_deleted:
        sql += " AND status = 'active'"
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("features", "metrics", "params")), None


def list_models(
    user_id: int,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[dict], int, Optional[str]]:
    err = _ensure()
    if err:
        return [], 0, err
    where = ["user_id = %s", "status = 'active'"]
    params: List[Any] = [user_id]
    if keyword:
        where.append("(model_name LIKE %s OR model_type LIKE %s OR task_type LIKE %s)")
        like = f"%{keyword}%"
        params.extend([like, like, like])
    where_sql = " AND ".join(where)
    count_rows, cerr = mysql_handler.query(
        f"SELECT COUNT(*) AS cnt FROM {TABLE_USER_MODELS} WHERE {where_sql}",
        tuple(params),
    )
    if cerr:
        return [], 0, cerr
    total = int((count_rows[0] or {}).get("cnt") or 0) if count_rows else 0
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_USER_MODELS} WHERE {where_sql} "
        f"ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        tuple(params + [limit, offset]),
    )
    if qerr:
        return [], 0, qerr
    return [_parse_json_fields(r, ("features", "metrics", "params")) for r in rows], total, None


def update_model(user_id: int, model_id: int, fields: Dict[str, Any]) -> Optional[str]:
    err = _ensure()
    if err:
        return err
    if not fields:
        return None
    payload = dict(fields)
    for key in ("features", "metrics", "params"):
        if key in payload:
            payload[key] = _json_dump(payload.get(key))
    sets = ", ".join(f"{k} = %s" for k in payload.keys())
    values = list(payload.values()) + [model_id, user_id]
    sql = f"UPDATE {TABLE_USER_MODELS} SET {sets} WHERE id = %s AND user_id = %s"
    _, uerr = mysql_handler.execute(sql, tuple(values))
    return uerr


def find_model_by_source_ref(
    user_id: int,
    source: str,
    source_ref_id: int,
) -> Tuple[Optional[dict], Optional[str]]:
    err = _ensure()
    if err:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT * FROM {TABLE_USER_MODELS} WHERE user_id = %s AND source = %s "
        f"AND source_ref_id = %s AND status = 'active' LIMIT 1",
        (user_id, source, source_ref_id),
    )
    if qerr:
        return None, qerr
    if not rows:
        return None, None
    return _parse_json_fields(rows[0], ("features", "metrics", "params")), None
