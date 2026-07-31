# backend/psych_variable_service.py — 变量管理

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from db import psych_store as store

logger = logging.getLogger(__name__)


def create_variable(user_id: int, body: Dict[str, Any]) -> Tuple[Optional[dict], Optional[str]]:
    var_name = (body.get("var_name") or "").strip()
    if not var_name:
        return None, "var_name 不能为空"
    vid, err = store.insert_variable(
        {
            "user_id": user_id,
            "dataset_id": body.get("dataset_id"),
            "var_name": var_name,
            "display_name": body.get("display_name") or var_name,
            "category": body.get("category"),
            "dtype": body.get("dtype"),
            "dict_code": body.get("dict_code"),
            "mapping_json": body.get("mapping_json") or body.get("mapping"),
            "relations_json": body.get("relations_json") or body.get("relations"),
            "description": body.get("description"),
        }
    )
    if err:
        return None, err
    return store.get_variable(int(vid), user_id)  # type: ignore[arg-type]


def list_variables(
    user_id: int, dataset_id: Optional[int] = None
) -> Tuple[List[dict], Optional[str]]:
    return store.list_variables(user_id, dataset_id=dataset_id)


def get_variable(var_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = store.get_variable(var_id, user_id)
    if err:
        return None, err
    if not row:
        return None, f"变量不存在: {var_id}"
    return row, None


def update_variable(var_id: int, user_id: int, fields: Dict[str, Any]) -> Tuple[Optional[dict], Optional[str]]:
    allowed = {
        "var_name", "display_name", "category", "dtype", "dict_code",
        "mapping_json", "relations_json", "description", "dataset_id", "mapping", "relations",
    }
    payload = {}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "mapping":
            payload["mapping_json"] = v
        elif k == "relations":
            payload["relations_json"] = v
        else:
            payload[k] = v
    if not payload:
        return None, "无有效更新字段"
    uerr = store.update_variable(var_id, user_id, payload)
    if uerr:
        return None, uerr
    return get_variable(var_id, user_id)


def delete_variable(var_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    derr = store.delete_variable(var_id, user_id)
    if derr:
        return None, derr
    return {"id": var_id, "deleted": True}, None


def batch_edit(user_id: int, items: List[Dict[str, Any]]) -> Tuple[Optional[dict], Optional[str]]:
    if not items:
        return None, "items 不能为空"
    updated = created = failed = 0
    errors = []
    for i, item in enumerate(items):
        vid = item.get("id")
        if vid:
            _, err = update_variable(int(vid), user_id, item)
            if err:
                failed += 1
                errors.append({"index": i, "error": err})
            else:
                updated += 1
        else:
            _, err = create_variable(user_id, item)
            if err:
                failed += 1
                errors.append({"index": i, "error": err})
            else:
                created += 1
    return {"updated": updated, "created": created, "failed": failed, "errors": errors}, None


def set_mapping(
    user_id: int, var_id: int, mapping: Dict[str, Any]
) -> Tuple[Optional[dict], Optional[str]]:
    return update_variable(var_id, user_id, {"mapping_json": mapping})


def create_category(
    user_id: int, name: str, parent_id: Optional[int] = None, sort_order: int = 0
) -> Tuple[Optional[dict], Optional[str]]:
    if not name:
        return None, "name 不能为空"
    cid, err = store.insert_var_category(
        {"user_id": user_id, "name": name, "parent_id": parent_id, "sort_order": sort_order}
    )
    if err:
        return None, err
    cats, _ = store.list_var_categories(user_id)
    for c in cats or []:
        if c.get("id") == cid:
            return c, None
    return {"id": cid, "name": name}, None


def list_categories(user_id: int) -> Tuple[List[dict], Optional[str]]:
    return store.list_var_categories(user_id)


def update_category(cat_id: int, user_id: int, fields: Dict[str, Any]) -> Tuple[Optional[dict], Optional[str]]:
    allowed = {"name", "parent_id", "sort_order"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    if not payload:
        return None, "无有效更新字段"
    uerr = store.update_var_category(cat_id, user_id, payload)
    if uerr:
        return None, uerr
    cats, err = store.list_var_categories(user_id)
    if err:
        return None, err
    for c in cats or []:
        if c.get("id") == cat_id:
            return c, None
    return {"id": cat_id, **payload}, None


def delete_category(cat_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    derr = store.delete_var_category(cat_id, user_id)
    if derr:
        return None, derr
    return {"id": cat_id, "deleted": True}, None


def export_dictionary(
    user_id: int, dataset_id: Optional[int] = None, fmt: str = "json"
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    vars_, err = store.list_variables(user_id, dataset_id=dataset_id)
    if err:
        return None, err
    dictionary = {
        "dataset_id": dataset_id,
        "variables": [
            {
                "var_name": v.get("var_name"),
                "display_name": v.get("display_name"),
                "category": v.get("category"),
                "dtype": v.get("dtype"),
                "dict_code": v.get("dict_code"),
                "mapping": v.get("mapping_json"),
                "relations": v.get("relations_json"),
                "description": v.get("description"),
            }
            for v in (vars_ or [])
        ],
    }
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["var_name", "display_name", "category", "dtype", "dict_code", "description"],
        )
        writer.writeheader()
        for item in dictionary["variables"]:
            writer.writerow({k: item.get(k) for k in writer.fieldnames})
        return {"format": "csv", "content": buf.getvalue(), "dictionary": dictionary}, None
    return {"format": "json", "dictionary": dictionary}, None
