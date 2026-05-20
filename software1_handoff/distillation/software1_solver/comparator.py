"""GT comparators.

Three flavors:

1. ``compare_csv_exact``     — strict row-by-row equality (numeric tol = 0
   for ints/strings; tiny tol for floats to absorb IEEE 754 jitter).
2. ``compare_csv_numeric_tol`` — numeric columns with abs/rel tolerance.
3. ``compare_json_with_assertions`` — for json GTs that encode threshold
   assertions like ``"shapiro_p_lt": 0.05`` or ``"expected_count": 7``.

Each returns a dict with keys::

    {
      "match":     bool,
      "n_diffs":   int,
      "details":   [...short list of first 10 diffs...],
      "summary":   "string for the report",
    }
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _flat_list(rows):
    return list(rows) if rows is not None else []


def compare_metadata_schema(actual: Dict[str, Any],
                            gt: Dict[str, Any]) -> Dict[str, Any]:
    """Specialised comparator for metadata_parser-style outputs.

    GT and actual share keys ``n_rows``, ``n_cols``, ``columns``
    (list of dicts indexed by ``name``), and ``preprocessing_recommendations``
    (list-of-strings).

    For each column we check:
      - ``inferred_type`` exact match
      - ``n_unique`` exact match (if GT specifies)
      - ``missing_count <= GT.missing_count_max`` (if GT specifies)
      - ``is_unique_after_dropna`` exact match (if GT specifies)
    """
    diffs: List[str] = []
    for k in ("n_rows", "n_cols"):
        if k in gt and actual.get(k) != gt[k]:
            diffs.append(f"{k}: expected {gt[k]}, got {actual.get(k)}")

    a_cols = {c.get("name"): c for c in actual.get("columns", [])}
    for gt_col in gt.get("columns", []):
        name = gt_col.get("name")
        if name not in a_cols:
            diffs.append(f"column {name!r}: missing in actual schema")
            continue
        a = a_cols[name]
        if "inferred_type" in gt_col and a.get("inferred_type") != gt_col["inferred_type"]:
            diffs.append(f"column {name!r}.inferred_type: expected "
                         f"{gt_col['inferred_type']!r}, got "
                         f"{a.get('inferred_type')!r}")
        if "n_unique" in gt_col and a.get("n_unique") != gt_col["n_unique"]:
            diffs.append(f"column {name!r}.n_unique: expected "
                         f"{gt_col['n_unique']}, got {a.get('n_unique')}")
        if "missing_count_max" in gt_col:
            mc = a.get("missing_count")
            if mc is None or mc > gt_col["missing_count_max"]:
                diffs.append(f"column {name!r}.missing_count={mc} > "
                             f"max {gt_col['missing_count_max']}")
        if "is_unique_after_dropna" in gt_col and \
                a.get("is_unique_after_dropna") != gt_col["is_unique_after_dropna"]:
            diffs.append(f"column {name!r}.is_unique_after_dropna: "
                         f"expected {gt_col['is_unique_after_dropna']}, "
                         f"got {a.get('is_unique_after_dropna')}")

    expected_recs = gt.get("expected_preprocessing_recommendations") or []
    actual_recs = actual.get("preprocessing_recommendations") or []
    if expected_recs and len(actual_recs) < min(3, len(expected_recs)):
        diffs.append(
            f"preprocessing_recommendations: expected ≥3, got "
            f"{len(actual_recs)}"
        )

    return {
        "match": len(diffs) == 0,
        "n_diffs": len(diffs),
        "details": diffs[:15],
        "summary": "schema match" if not diffs else f"{len(diffs)} schema mismatches",
    }


def compare_association_rules(actual_rules: pd.DataFrame,
                              expected_rules: List[Dict[str, Any]]
                              ) -> Dict[str, Any]:
    """Verify each expected {antecedent, consequent, confidence_min} rule
    is present in ``actual_rules`` (a DataFrame with columns
    ``antecedent``/``consequent``/``confidence``) and that its
    confidence ≥ ``confidence_min``."""
    diffs: List[str] = []
    n_recovered = 0
    for r in expected_rules:
        ant_key = "+".join(sorted(r["antecedent"]))
        cons = r["consequent"]
        cmin = float(r.get("confidence_min", 0.0))
        match = actual_rules[
            (actual_rules["antecedent"] == ant_key) &
            (actual_rules["consequent"] == cons)
        ]
        if match.empty:
            diffs.append(f"missing rule {ant_key} -> {cons} "
                         f"(needed conf ≥ {cmin})")
            continue
        conf = float(match["confidence"].iloc[0])
        if conf < cmin:
            diffs.append(f"rule {ant_key} -> {cons}: actual conf "
                         f"{conf:.3f} < expected {cmin:.3f}")
        else:
            n_recovered += 1

    return {
        "match": len(diffs) == 0,
        "n_diffs": len(diffs),
        "details": diffs[:10],
        "summary": (f"recovered {n_recovered}/{len(expected_rules)} "
                    f"expected rules"),
    }


def compare_csv_exact(actual_path: str | Path, gt_path: str | Path,
                      float_atol: float = 1e-9) -> Dict[str, Any]:
    """Strict csv comparison.  Both must have the same columns + rows in
    the same order; numerics within ``float_atol``."""
    actual = pd.read_csv(actual_path)
    gt = pd.read_csv(gt_path)

    diffs: List[str] = []

    if list(actual.columns) != list(gt.columns):
        diffs.append(f"columns differ: actual={list(actual.columns)} vs "
                     f"gt={list(gt.columns)}")
    if actual.shape != gt.shape:
        diffs.append(f"shape differs: actual={actual.shape} vs "
                     f"gt={gt.shape}")

    if not diffs:
        for c in gt.columns:
            a, b = actual[c], gt[c]
            if pd.api.types.is_numeric_dtype(b):
                close = np.isclose(a.fillna(np.nan).to_numpy(dtype=float),
                                   b.fillna(np.nan).to_numpy(dtype=float),
                                   atol=float_atol, rtol=0,
                                   equal_nan=True)
                bad = (~close).sum()
                if bad:
                    diffs.append(f"column {c!r}: {bad} rows mismatch")
            else:
                bad_idx = a.fillna("__nan__").astype(str).ne(
                    b.fillna("__nan__").astype(str))
                if bad_idx.any():
                    diffs.append(f"column {c!r}: {int(bad_idx.sum())} rows mismatch")

    return {
        "match": len(diffs) == 0,
        "n_diffs": len(diffs),
        "details": diffs[:10],
        "summary": "exact match" if not diffs else f"{len(diffs)} differences",
    }


def compare_csv_numeric_tol(actual_path: str | Path, gt_path: str | Path,
                            atol: float = 1e-6, rtol: float = 1e-4
                            ) -> Dict[str, Any]:
    """Loose csv comparison with abs+rel tolerance for numeric columns."""
    actual = pd.read_csv(actual_path)
    gt = pd.read_csv(gt_path)

    diffs: List[str] = []
    if list(actual.columns) != list(gt.columns):
        diffs.append(f"columns differ: actual={list(actual.columns)} vs "
                     f"gt={list(gt.columns)}")
    if actual.shape != gt.shape:
        diffs.append(f"shape differs: actual={actual.shape} vs gt={gt.shape}")

    if not diffs:
        for c in gt.columns:
            a, b = actual[c], gt[c]
            if pd.api.types.is_numeric_dtype(b):
                close = np.isclose(a.fillna(np.nan).to_numpy(dtype=float),
                                   b.fillna(np.nan).to_numpy(dtype=float),
                                   atol=atol, rtol=rtol, equal_nan=True)
                bad = int((~close).sum())
                if bad:
                    diffs.append(f"column {c!r}: {bad} rows outside tol")
            else:
                bad = int(a.fillna("__nan__").astype(str).ne(
                    b.fillna("__nan__").astype(str)).sum())
                if bad:
                    diffs.append(f"column {c!r}: {bad} rows mismatch")

    return {
        "match": len(diffs) == 0,
        "n_diffs": len(diffs),
        "details": diffs[:10],
        "summary": "exact (with tol)" if not diffs else f"{len(diffs)} columns out of tol",
    }


# Keys that appear in GT json files purely as documentation / metadata,
# not as solver-output assertions.  Comparator skips these silently.
DEFAULT_SKIP_KEYS = frozenset({
    "test", "task", "task_modes", "interpretation",
    "deliverable_csv_columns", "deliverable_csv", "deliverables",
    "scoring", "scoring_metric", "scoring_weights",
    "evaluation", "tags", "rdab_yaml_path",
    "model", "label_columns",
    "flagging_rule",  # reference-range GT
    "expected_strong_rules",  # association-rule GT (richer compare elsewhere)
})


def compare_json_with_assertions(actual: Dict[str, Any],
                                 gt: Dict[str, Any],
                                 skip_keys: Optional[set] = None,
                                 numeric_abs_tol: float = 1e-3,
                                 numeric_rel_tol: float = 1e-3,
                                 ) -> Dict[str, Any]:
    """Verify ``actual`` (a solver-produced dict) satisfies the assertions
    encoded in ``gt`` (a typically nested json with keys like
    ``..._lt`` / ``..._gt`` / numeric values / boolean flags).

    Supported keys (recursive):
      - "<key>_lt": x       — actual["<key>"] < x
      - "<key>_gt": x       — actual["<key>"] > x
      - "<key>_lte": x      — actual["<key>"] <= x
      - "<key>_gte": x      — actual["<key>"] >= x
      - "<key>_min": x      — actual["<key>"] >= x  (alias)
      - "<key>_max": x      — actual["<key>"] <= x  (alias)
      - "<key>": numeric    — equality within ``numeric_abs_tol`` /
                              ``numeric_rel_tol``
      - "<key>": bool       — strict equality
      - "<key>": str        — strict equality (case-insensitive)
      - "<key>": list       — set-equality

    Keys in ``skip_keys`` (default: ``DEFAULT_SKIP_KEYS``) are ignored —
    they typically appear in our self-constructed GT json files as
    free-text documentation (``"interpretation": "right-skewed"``).
    """
    diffs: List[str] = []
    skip = skip_keys if skip_keys is not None else DEFAULT_SKIP_KEYS
    _check_assertions("", actual, gt, diffs, skip,
                      numeric_abs_tol, numeric_rel_tol)
    return {
        "match": len(diffs) == 0,
        "n_diffs": len(diffs),
        "details": diffs[:20],
        "summary": "all assertions hold" if not diffs else f"{len(diffs)} assertion failures",
    }


def _check_assertions(path: str, actual, gt, diffs: List[str],
                      skip: set, atol: float, rtol: float) -> None:
    if isinstance(gt, dict):
        if not isinstance(actual, dict):
            diffs.append(f"{path}: expected dict, got {type(actual).__name__}")
            return
        for k, v in gt.items():
            if k in skip:
                continue
            sub_path = f"{path}.{k}" if path else k
            # threshold suffix
            for suffix, op in (
                ("_lt", lambda a, b: a < b),
                ("_lte", lambda a, b: a <= b),
                ("_gt", lambda a, b: a > b),
                ("_gte", lambda a, b: a >= b),
                ("_min", lambda a, b: a >= b),
                ("_max", lambda a, b: a <= b),
            ):
                if k.endswith(suffix):
                    base = k[: -len(suffix)]
                    if base not in actual:
                        diffs.append(f"{path}.{base}: missing in actual "
                                     f"(needed by GT key {k})")
                    else:
                        try:
                            ok = op(actual[base], v)
                        except Exception as e:
                            ok = False
                            diffs.append(f"{path}.{base}: comparison error {e}")
                        if not ok:
                            diffs.append(
                                f"{path}.{base}={actual[base]} fails {suffix} "
                                f"{v}"
                            )
                    break
            else:
                _check_assertions(sub_path, actual.get(k), v, diffs, skip,
                                   atol, rtol)
    elif isinstance(gt, bool):
        if actual != gt:
            diffs.append(f"{path}: expected {gt}, got {actual}")
    elif isinstance(gt, (int, float)) and not isinstance(gt, bool):
        try:
            if actual is None or not math.isclose(float(actual), float(gt),
                                                  abs_tol=atol, rel_tol=rtol):
                diffs.append(f"{path}: expected ≈{gt}, got {actual}")
        except (TypeError, ValueError):
            diffs.append(f"{path}: expected ≈{gt}, got {actual!r}")
    elif isinstance(gt, str):
        if actual is None:
            # treat free-text GT strings without an actual counterpart as
            # documentation (skip silently)
            return
        if str(actual).lower() != gt.lower():
            diffs.append(f"{path}: expected {gt!r}, got {actual!r}")
    elif isinstance(gt, list):
        if actual is None:
            return  # documentation list
        try:
            if set(map(str, actual)) != set(map(str, gt)):
                diffs.append(f"{path}: list set mismatch, expected {gt}, "
                             f"got {actual}")
        except TypeError:
            diffs.append(f"{path}: list compare failed")
