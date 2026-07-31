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


def _render_column_line(c: Dict[str, Any]) -> str:
    """Render one column's profile entry as a compact ``  - ...`` line."""
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
    return "  - " + "; ".join(bits)


# Column-name hints that mark a column as "semantically salient" (likely
# an id / label / outcome / key) so the wide-table renderer always shows
# it in full instead of folding it into the summarized feature block.
_SALIENT_NAME_HINTS = (
    "id", "label", "target", "outcome", "class", "group", "smiles",
    "time", "date", "event", "y_true", "response", "activity", "category",
)


def _is_salient_column(c: Dict[str, Any]) -> bool:
    """A column is salient (shown in full on wide tables) when it is
    non-numeric, low-cardinality (likely a label/ordinal/binary), looks
    like an id, or has an id/label/outcome-style name.  The bulk of a
    wide table — high-cardinality numeric *feature* columns — is NOT
    salient and gets summarized as a group."""
    dtype = str(c.get("dtype", ""))
    name = str(c.get("name", "")).lower()
    numeric = ("int" in dtype or "float" in dtype) and "object" not in dtype
    if not numeric:
        return True
    nu = c.get("n_unique", 0) or 0
    if nu <= 15:
        return True
    if c.get("looks_like_id"):
        return True
    return any(h in name for h in _SALIENT_NAME_HINTS)


def profile_to_text(profile: Dict[str, Any], max_lines: int = 80) -> str:
    """Render the profile as a short Markdown-ish block suitable for a
    prompt.  Trims at ``max_lines`` lines."""
    lines: List[str] = []
    lines.append(f"DataFrame shape: {profile['shape'][0]} rows × "
                 f"{profile['shape'][1]} columns")
    lines.append("")
    lines.append("Columns:")
    for c in profile["columns"]:
        lines.append(_render_column_line(c))
    lines.append("")
    lines.append(f"Sample rows ({len(profile['sample_rows'])}):")
    for r in profile["sample_rows"]:
        lines.append("  " + json.dumps(r, ensure_ascii=False, default=str))

    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines)-max_lines} more lines truncated)"]
    return "\n".join(lines)


def profile_to_text_wide(profile: Dict[str, Any],
                          wide_threshold: int = 60,
                          max_full_columns: int = 80) -> str:
    """Wide-table-aware renderer for the column-mapping LLM.

    For narrow tables this is identical to :func:`profile_to_text` (no
    truncation).  For wide tables (e.g. a gene-expression / fingerprint /
    descriptor matrix with hundreds–thousands of columns) we:

      * show every *salient* column (id / label / low-cardinality /
        named) IN FULL — so the LLM can still find the target/id column
        even though it sits behind thousands of feature columns, and
      * fold the high-cardinality numeric *feature* columns into a single
        summary line instead of either listing them all (token blow-up)
        or truncating them away (losing the id/label).

    The summary also tells the LLM that list-type roles need NOT be
    enumerated — the runner fills NUMERIC_LIST / ITEM_GROUP roles with
    *all* numeric columns automatically, so the LLM should return ``[]``.

    中文：宽表时只把「关键列(id/标签/低基数/命名相关)」整列展示，成片的
    数值特征列折叠成一行汇总——既不截断丢掉标签列，也不让 LLM 去枚举上千
    列名（那会撑爆 max_tokens 让返回的 JSON 被截断）。
    """
    cols = profile["columns"]
    if len(cols) <= wide_threshold:
        return profile_to_text(profile)

    salient = [c for c in cols if _is_salient_column(c)]
    salient_names = {c["name"] for c in salient}
    bulk = [c for c in cols if c["name"] not in salient_names]

    lines: List[str] = []
    lines.append(f"DataFrame shape: {profile['shape'][0]} rows × "
                 f"{profile['shape'][1]} columns")
    lines.append(f"(WIDE TABLE: {len(salient)} salient columns shown in full; "
                 f"{len(bulk)} numeric feature columns summarized below.)")
    lines.append("")
    lines.append("Salient columns (ids / labels / low-cardinality / named):")
    shown = salient[:max_full_columns]
    for c in shown:
        lines.append(_render_column_line(c))
    if len(salient) > max_full_columns:
        lines.append(f"  ... (+{len(salient)-max_full_columns} more salient "
                     "columns omitted)")

    if bulk:
        names = [str(c["name"]) for c in bulk]
        sample = ", ".join(names[:8])
        mins = [c.get("min") for c in bulk
                if isinstance(c.get("min"), (int, float))]
        maxs = [c.get("max") for c in bulk
                if isinstance(c.get("max"), (int, float))]
        rng = ""
        if mins and maxs:
            rng = f"; values roughly in [{min(mins)}, {max(maxs)}]"
        more = max(0, len(bulk) - 8)
        lines.append("")
        lines.append(f"Numeric feature columns ({len(bulk)} total, all "
                     f"dtype-numeric): {sample}"
                     + (f", ... +{more} more" if more else "") + rng)
        lines.append("NOTE: for list-type roles (NUMERIC_LIST / ITEM_GROUP) you "
                     "do NOT need to enumerate these feature columns — the runner "
                     "uses ALL numeric columns automatically. Return [] for such "
                     "roles and only map the scalar roles (id / label / target / "
                     "group) to the salient columns above.")

    lines.append("")
    lines.append(f"Sample rows ({len(profile['sample_rows'])}, salient columns only):")
    for r in profile["sample_rows"]:
        slim = {k: v for k, v in r.items() if k in salient_names}
        lines.append("  " + json.dumps(slim, ensure_ascii=False, default=str))
    return "\n".join(lines)
