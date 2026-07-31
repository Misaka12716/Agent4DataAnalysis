# psych/capability/bootstrap.py — 能力注册种子与编排

from __future__ import annotations

from typing import Any, Dict, List

from psych.ml.registry import list_algorithms
from psych.stats.catalog import list_stats_methods


def default_capabilities() -> List[Dict[str, Any]]:
    caps: List[Dict[str, Any]] = []
    for m in list_stats_methods():
        caps.append(
            {
                "capability_id": f"stats.{m['method_id']}",
                "kind": "stats",
                "impl_ref": m.get("solver_id") or m["method_id"],
                "version": "1.0.0",
                "enabled": True,
                "meta_json": {"name_zh": m.get("name_zh"), "category": m.get("category")},
            }
        )
    for a in list_algorithms():
        caps.append(
            {
                "capability_id": f"ml.{a['algo_id']}",
                "kind": "ml",
                "impl_ref": a.get("solver_id") or a["algo_id"],
                "version": "1.0.0",
                "enabled": True,
                "meta_json": {"name_zh": a.get("name_zh"), "task_type": a.get("task_type")},
            }
        )
    caps.extend(
        [
            {
                "capability_id": "llm.extract",
                "kind": "llm",
                "impl_ref": "psych_llm_service.extract",
                "version": "1.0.0",
                "enabled": True,
                "meta_json": {"name_zh": "诊疗信息抽取"},
            },
            {
                "capability_id": "llm.qa",
                "kind": "llm",
                "impl_ref": "psych_llm_service.qa",
                "version": "1.0.0",
                "enabled": True,
                "meta_json": {"name_zh": "分析问答"},
            },
            {
                "capability_id": "dl.text_cnn",
                "kind": "dl",
                "impl_ref": "psych.dl.text_cnn",
                "version": "1.0.0",
                "enabled": True,
                "meta_json": {"name_zh": "文本CNN"},
            },
            {
                "capability_id": "dl.text_transformer",
                "kind": "dl",
                "impl_ref": "psych.dl.text_transformer",
                "version": "1.0.0",
                "enabled": True,
                "meta_json": {"name_zh": "文本Transformer"},
            },
            {
                "capability_id": "feature.stat",
                "kind": "pipeline",
                "impl_ref": "psych_feature_service.stat",
                "version": "1.0.0",
                "enabled": True,
                "meta_json": {"name_zh": "统计特征"},
            },
            {
                "capability_id": "scale.score",
                "kind": "pipeline",
                "impl_ref": "psych_scale_service.score",
                "version": "1.0.0",
                "enabled": True,
                "meta_json": {"name_zh": "量表计分"},
            },
        ]
    )
    return caps
