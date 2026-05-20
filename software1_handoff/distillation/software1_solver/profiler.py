"""Compact, LLM-friendly DataFrame profiler.

Goal: given a user-supplied csv / DataFrame, produce a *short* summary
that contains everything an LLM needs to map column-roles to actual
column names: dtype, missing rate, n_unique, basic stats / top values,
and a small head() sample.

The output is intentionally trimmed to fit easily in an LLM prompt:
~10 columns × ~6 fields = ~600 tokens for typical clinical tables.

中文说明
========
把宽表压成「每列：类型、缺失率、基数、示例值」的 JSON，供规划/映射 LLM
选列名；刻意截断以控制 prompt 长度，不替代完整 EDA。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import numpy as np
import pandas as pd


def _safe_round(x, ndigits=4):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        return None
    if isinstance(x, (np.integer,)):
        return int(x)
    return round(float(x), ndigits)


def profile_df(df: pd.DataFrame, n_sample: int = 3,
               max_topk: int = 5) -> Dict[str, Any]:
    """Return a compact JSON-serializable schema profile."""
    columns: List[Dict[str, Any]] = []
    for c in df.columns:
        col = df[c]
        info: Dict[str, Any] = {
            "name": str(c),
            "dtype": str(col.dtype),
            "missing_rate": _safe_round(col.isna().mean(), 4),
            "n_unique": int(col.nunique(dropna=True)),
        }

        if pd.api.types.is_numeric_dtype(col):
            non_null = col.dropna()
            if len(non_null):
                info.update({
                    "min": _safe_round(non_null.min()),
                    "max": _safe_round(non_null.max()),
                    "mean": _safe_round(non_null.mean()),
                    "std": _safe_round(non_null.std()) if len(non_null) > 1 else 0.0,
                })
            # for low-cardinality numeric (likely binary/ordinal),
            # also list top values
            if info["n_unique"] <= 10:
                vc = col.value_counts(dropna=True).head(max_topk)
                info["top_values"] = [
                    [_safe_round(k), int(v)] for k, v in vc.items()
                ]
        elif pd.api.types.is_datetime64_any_dtype(col):
            non_null = col.dropna()
            if len(non_null):
                info["min"] = str(non_null.min())
                info["max"] = str(non_null.max())
        else:  # object / categorical / string
            vc = col.value_counts(dropna=True).head(max_topk)
            info["top_values"] = [
                [str(k), int(v)] for k, v in vc.items()
            ]
            # heuristic: if all unique → treat as id-like
            info["looks_like_id"] = info["n_unique"] == len(col.dropna())

        columns.append(info)

    # head() sample — convert to JSON-serializable
    head = df.head(n_sample)
    sample_rows = []
    for _, row in head.iterrows():
        r = {}
        for k, v in row.items():
            if pd.isna(v):
                r[str(k)] = None
            elif isinstance(v, (np.integer,)):
                r[str(k)] = int(v)
            elif isinstance(v, (np.floating,)):
                r[str(k)] = _safe_round(v, 4)
            else:
                r[str(k)] = str(v)
        sample_rows.append(r)

    return {
        "shape": list(df.shape),
        "columns": columns,
        "sample_rows": sample_rows,
    }


def profile_to_text(profile: Dict[str, Any], max_lines: int = 80) -> str:
    """Render the profile as a short Markdown-ish block suitable for a
    prompt.  Trims at ``max_lines`` lines."""
    lines: List[str] = []
    lines.append(f"DataFrame shape: {profile['shape'][0]} rows × "
                 f"{profile['shape'][1]} columns")
    lines.append("")
    lines.append("Columns:")
    for c in profile["columns"]:
        bits = [f"{c['name']!r} ({c['dtype']})"]
        bits.append(f"missing={c['missing_rate']}")
        bits.append(f"unique={c['n_unique']}")
        if "mean" in c:
            bits.append(f"mean={c.get('mean')}")
            bits.append(f"range=[{c.get('min')}, {c.get('max')}]")
        if "top_values" in c:
            tv = ", ".join(f"{k}({v})" for k, v in c["top_values"][:3])
            bits.append(f"top=[{tv}]")
        if c.get("looks_like_id"):
            bits.append("looks_like_id")
        lines.append("  - " + "; ".join(bits))
    lines.append("")
    lines.append(f"Sample rows ({len(profile['sample_rows'])}):")
    for r in profile["sample_rows"]:
        lines.append("  " + json.dumps(r, ensure_ascii=False, default=str))

    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines)-max_lines} more lines truncated)"]
    return "\n".join(lines)
