# psych/scales/forms.py — 量表定义与计分

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

BUILTIN_SCALES: Dict[str, Dict[str, Any]] = {
    "PHQ9": {
        "display_name": "患者健康问卷-9",
        "version": "1.0",
        "items": [{"code": f"PHQ9_{i}", "label": f"条目{i}", "min": 0, "max": 3} for i in range(1, 10)],
        "scoring": {"type": "sum", "total_range": [0, 27]},
    },
    "GAD7": {
        "display_name": "广泛性焦虑量表-7",
        "version": "1.0",
        "items": [{"code": f"GAD7_{i}", "label": f"条目{i}", "min": 0, "max": 3} for i in range(1, 8)],
        "scoring": {"type": "sum", "total_range": [0, 21]},
    },
    "HAMD": {
        "display_name": "汉密尔顿抑郁量表",
        "version": "1.0",
        "items": [{"code": f"HAMD_{i}", "label": f"条目{i}", "min": 0, "max": 4} for i in range(1, 18)],
        "scoring": {"type": "sum", "total_range": [0, 52]},
    },
    "HAMA": {
        "display_name": "汉密尔顿焦虑量表",
        "version": "1.0",
        "items": [{"code": f"HAMA_{i}", "label": f"条目{i}", "min": 0, "max": 4} for i in range(1, 15)],
        "scoring": {"type": "sum", "total_range": [0, 56]},
    },
    "PANSS": {
        "display_name": "阳性与阴性症状量表",
        "version": "1.0",
        "items": (
            [{"code": f"P{i}", "label": f"阳性{i}", "min": 1, "max": 7, "subscale": "positive"} for i in range(1, 8)]
            + [{"code": f"N{i}", "label": f"阴性{i}", "min": 1, "max": 7, "subscale": "negative"} for i in range(1, 8)]
            + [{"code": f"G{i}", "label": f"一般{i}", "min": 1, "max": 7, "subscale": "general"} for i in range(1, 17)]
        ),
        "scoring": {
            "type": "sum_with_subscales",
            "subscales": ["positive", "negative", "general"],
        },
    },
}


def score_items(
    scale_code: str, item_scores: Dict[str, Any], form: Optional[Dict[str, Any]] = None
) -> Tuple[float, Dict[str, float], Dict[str, Any]]:
    """返回 (total, subscales, cleaned_item_scores)。"""
    meta = form or BUILTIN_SCALES.get(scale_code.upper()) or {}
    items = meta.get("items") or meta.get("items_json") or []
    scoring = meta.get("scoring") or meta.get("scoring_json") or {"type": "sum"}
    cleaned: Dict[str, Any] = {}
    numeric_vals: List[float] = []
    subscales: Dict[str, List[float]] = {}

    item_by_code = {str(it.get("code")): it for it in items if isinstance(it, dict)}
    for code, raw in (item_scores or {}).items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        cleaned[str(code)] = val
        numeric_vals.append(val)
        spec = item_by_code.get(str(code)) or {}
        sub = spec.get("subscale")
        if sub:
            subscales.setdefault(str(sub), []).append(val)

    total = float(sum(numeric_vals)) if numeric_vals else 0.0
    sub_totals = {k: float(sum(v)) for k, v in subscales.items()}
    if scoring.get("type") == "sum_with_subscales" and not sub_totals:
        # 无条目元数据时按前缀启发式
        for code, val in cleaned.items():
            c = code.upper()
            if c.startswith("P"):
                sub_totals["positive"] = sub_totals.get("positive", 0.0) + val
            elif c.startswith("N"):
                sub_totals["negative"] = sub_totals.get("negative", 0.0) + val
            elif c.startswith("G"):
                sub_totals["general"] = sub_totals.get("general", 0.0) + val
    return total, sub_totals, cleaned
