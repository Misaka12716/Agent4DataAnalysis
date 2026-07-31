# backend/psych_capability_service.py — 能力模块化管理与升级预留

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from db import psych_store as store
from psych.capability.bootstrap import default_capabilities

logger = logging.getLogger(__name__)

_bootstrapped = False


def bootstrap_capabilities(force: bool = False) -> Tuple[int, Optional[str]]:
    global _bootstrapped
    if _bootstrapped and not force:
        return 0, None
    n = 0
    for cap in default_capabilities():
        _, err = store.upsert_capability(cap)
        if err:
            return n, err
        n += 1
    _bootstrapped = True
    return n, None


def list_caps(kind: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    bootstrap_capabilities()
    return store.list_capabilities(kind=kind)


def update_cap(
    capability_id: str, enabled: Optional[bool] = None, meta_json: Optional[Any] = None, version: Optional[str] = None
) -> Tuple[Optional[dict], Optional[str]]:
    bootstrap_capabilities()
    fields: Dict[str, Any] = {}
    if enabled is not None:
        fields["enabled"] = enabled
    if meta_json is not None:
        fields["meta_json"] = meta_json
    if version is not None:
        fields["version"] = version
    if not fields:
        return None, "无更新字段"
    err = store.update_capability(capability_id, fields)
    if err:
        return None, err
    return store.get_capability(capability_id)


def compose(
    user_id: int, capability_ids: List[str], name: Optional[str] = None
) -> Tuple[Optional[dict], Optional[str]]:
    """将能力 id 列表编排为一条 psych_pipelines 记录。"""
    if not capability_ids:
        return None, "capability_ids 不能为空"
    bootstrap_capabilities()
    steps = []
    for cid in capability_ids:
        cap, err = store.get_capability(cid)
        if err:
            return None, err
        if not cap:
            return None, f"能力不存在: {cid}"
        if not cap.get("enabled"):
            return None, f"能力已关闭: {cid}"
        steps.append(
            {
                "capability_id": cid,
                "method_id": cap.get("impl_ref"),
                "solver_id": cap.get("impl_ref"),
                "kind": cap.get("kind"),
            }
        )
    from backend.psych_pipeline_service import create_pipeline

    return create_pipeline(user_id, name or f"compose_{capability_ids[0]}", steps)


def upgrade(
    capability_id: str, to_ver: str, note: Optional[str] = None
) -> Tuple[Optional[dict], Optional[str]]:
    bootstrap_capabilities()
    cap, err = store.get_capability(capability_id)
    if err:
        return None, err
    if not cap:
        return None, f"能力不存在: {capability_id}"
    from_ver = cap.get("version")
    uerr = store.update_capability(capability_id, {"version": to_ver})
    if uerr:
        return None, uerr
    lid, lerr = store.insert_capability_changelog(
        {
            "capability_id": capability_id,
            "from_ver": from_ver,
            "to_ver": to_ver,
            "note": note or "季度能力升级",
        }
    )
    if lerr:
        return None, lerr
    # 重新装载默认能力元数据（预留热更新入口）
    bootstrap_capabilities(force=True)
    updated, _ = store.get_capability(capability_id)
    return {"capability": updated, "changelog_id": lid, "from_ver": from_ver, "to_ver": to_ver}, None


def list_changelog(capability_id: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    return store.list_capability_changelog(capability_id=capability_id)


def health_summary() -> Dict[str, Any]:
    bootstrap_capabilities()
    caps, _ = store.list_capabilities()
    by_kind: Dict[str, int] = {}
    enabled = 0
    for c in caps or []:
        k = c.get("kind") or "unknown"
        by_kind[k] = by_kind.get(k, 0) + 1
        if c.get("enabled"):
            enabled += 1
    return {
        "service": "psych",
        "capabilities_total": len(caps or []),
        "capabilities_enabled": enabled,
        "by_kind": by_kind,
    }
