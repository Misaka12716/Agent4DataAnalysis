# backend/psych_scale_service.py — 量表智能结构化与分析

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from db import psych_store as store
from psych.scales.forms import BUILTIN_SCALES, score_items

logger = logging.getLogger(__name__)


def ensure_builtin_forms() -> None:
    for code, meta in BUILTIN_SCALES.items():
        store.upsert_scale_form(
            {
                "scale_code": code,
                "version": meta.get("version") or "1.0",
                "display_name": meta.get("display_name"),
                "items_json": meta.get("items"),
                "scoring_json": meta.get("scoring"),
            }
        )


def list_forms() -> Tuple[List[dict], Optional[str]]:
    ensure_builtin_forms()
    return store.list_scale_forms()


def parse_raw(
    user_id: int,
    scale_code: str,
    raw: Any,
    patient_key: Optional[str] = None,
    dataset_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """将原始量表数据结构化为条目分数字典。"""
    ensure_builtin_forms()
    code = scale_code.upper().strip()
    form, err = store.get_scale_form(code)
    if err:
        return None, err
    if not form:
        return None, f"未知量表: {scale_code}"

    item_scores: Dict[str, Any] = {}
    if isinstance(raw, dict):
        item_scores = dict(raw)
    elif isinstance(raw, list):
        # [{code, score}] or [score, ...]
        items = form.get("items_json") or []
        if raw and isinstance(raw[0], dict):
            for it in raw:
                c = it.get("code") or it.get("item")
                if c is not None:
                    item_scores[str(c)] = it.get("score", it.get("value"))
        else:
            for i, val in enumerate(raw):
                if i < len(items):
                    item_scores[str(items[i].get("code"))] = val
                else:
                    item_scores[f"item_{i+1}"] = val
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parse_raw(user_id, scale_code, parsed, patient_key, dataset_id)
        except json.JSONDecodeError:
            # 逗号分隔
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            items = form.get("items_json") or []
            for i, val in enumerate(parts):
                try:
                    num = float(val)
                except ValueError:
                    continue
                if i < len(items):
                    item_scores[str(items[i].get("code"))] = num
                else:
                    item_scores[f"item_{i+1}"] = num
    else:
        return None, "raw 格式不支持，请传 dict/list/json字符串"

    return {
        "scale_code": code,
        "patient_key": patient_key,
        "dataset_id": dataset_id,
        "item_scores": item_scores,
        "form": {"display_name": form.get("display_name"), "version": form.get("version")},
    }, None


def score(
    user_id: int,
    scale_code: str,
    item_scores: Dict[str, Any],
    patient_key: str,
    dataset_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    ensure_builtin_forms()
    code = scale_code.upper().strip()
    if not patient_key:
        return None, "patient_key 不能为空"
    form, err = store.get_scale_form(code)
    if err:
        return None, err
    total, subscales, cleaned = score_items(code, item_scores, form)
    sid, ierr = store.insert_scale_score(
        {
            "user_id": user_id,
            "dataset_id": dataset_id,
            "patient_key": patient_key,
            "scale_code": code,
            "item_scores_json": cleaned,
            "total": total,
            "subscales_json": subscales,
        }
    )
    if ierr:
        return None, ierr
    return {
        "id": sid,
        "scale_code": code,
        "patient_key": patient_key,
        "total": total,
        "subscales": subscales,
        "item_scores": cleaned,
    }, None


def list_scores(
    user_id: int,
    scale_code: Optional[str] = None,
    patient_key: Optional[str] = None,
    dataset_id: Optional[int] = None,
    limit: int = 200,
) -> Tuple[List[dict], Optional[str]]:
    return store.list_scale_scores(
        user_id, scale_code=scale_code, patient_key=patient_key, dataset_id=dataset_id, limit=limit
    )


def trend(
    user_id: int, patient_key: str, scale_code: str
) -> Tuple[Optional[dict], Optional[str]]:
    rows, err = store.list_scale_scores(user_id, scale_code=scale_code.upper(), patient_key=patient_key, limit=500)
    if err:
        return None, err
    points = [
        {
            "scored_at": r.get("scored_at"),
            "total": r.get("total"),
            "subscales": r.get("subscales_json"),
            "id": r.get("id"),
        }
        for r in (rows or [])
    ]
    # 按时间升序
    points = list(reversed(points))
    totals = [p["total"] for p in points if p.get("total") is not None]
    delta = None
    if len(totals) >= 2:
        delta = float(totals[-1]) - float(totals[0])
    return {
        "patient_key": patient_key,
        "scale_code": scale_code.upper(),
        "points": points,
        "n": len(points),
        "delta_first_last": delta,
    }, None


def compare(
    user_id: int,
    scale_code: str,
    group_a: List[str],
    group_b: List[str],
) -> Tuple[Optional[dict], Optional[str]]:
    """按 patient_key 分组对比最近一次总分。"""
    if not group_a or not group_b:
        return None, "group_a 与 group_b 不能为空"

    def _latest_totals(keys: List[str]) -> List[float]:
        vals = []
        for pk in keys:
            rows, _ = store.list_scale_scores(user_id, scale_code=scale_code.upper(), patient_key=pk, limit=1)
            if rows and rows[0].get("total") is not None:
                vals.append(float(rows[0]["total"]))
        return vals

    a = _latest_totals(group_a)
    b = _latest_totals(group_b)
    result: Dict[str, Any] = {
        "scale_code": scale_code.upper(),
        "group_a": {"n": len(a), "mean": float(sum(a) / len(a)) if a else None, "values": a},
        "group_b": {"n": len(b), "mean": float(sum(b) / len(b)) if b else None, "values": b},
    }
    if len(a) >= 2 and len(b) >= 2:
        try:
            from scipy import stats as sps

            stat, p = sps.ttest_ind(a, b, equal_var=False)
            result["welch_t"] = {"statistic": float(stat), "pvalue": float(p)}
        except Exception as exc:
            result["welch_t_error"] = str(exc)
    return result, None


def export_scores(
    user_id: int,
    scale_code: Optional[str] = None,
    dataset_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    rows, err = list_scores(user_id, scale_code=scale_code, dataset_id=dataset_id, limit=5000)
    if err:
        return None, err
    flat = []
    for r in rows or []:
        flat.append(
            {
                "id": r.get("id"),
                "patient_key": r.get("patient_key"),
                "scale_code": r.get("scale_code"),
                "total": r.get("total"),
                "subscales": json.dumps(r.get("subscales_json") or {}, ensure_ascii=False),
                "item_scores": json.dumps(r.get("item_scores_json") or {}, ensure_ascii=False),
                "scored_at": r.get("scored_at"),
            }
        )
    return {"count": len(flat), "rows": flat}, None
