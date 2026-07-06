"""Categorical-to-numeric encoding (V8 Phase 3 §P0-5).

Methods:
  - ``onehot``: pandas.get_dummies, optionally dropping the first level.
  - ``label``:  per-column integer codes (alphabetical order, stable
                across runs as long as the unique-value set is fixed).
  - ``auto``:   one-hot when ``n_unique <= max_onehot_cardinality``,
                else label.

Output:
  ``encoded.csv``       – the original frame with categorical cols
                          replaced by encoded columns.
  ``encoding_mapping.json`` – per-column record of the chosen method
                              and the actual value→code map.

Robustness:
  - Caller may pass ``categorical_cols`` mapping as a list, OR leave it
    empty; in that case we auto-detect object/string/category-dtype
    columns from the dataframe.
  - Pure-numeric or all-NaN columns are skipped (never re-encoded).

中文：分类特征编码，把 object/category 列转成数值，并写出映射表。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


ENCODE_CATEGORICAL_CONTRACT = SolverContract(
    name="encode_categorical",
    capability="F01_data_governance",
    description=(
        "Encode categorical columns to numeric.  Methods: onehot, "
        "label, auto (one-hot when n_unique <= max_onehot_cardinality, "
        "else label).  Empty/missing mapping → auto-detect object & "
        "category dtype columns.  Output: encoded csv + json mapping "
        "describing every transform."
    ),
    roles={
        "categorical_cols": RoleSpec(
            Role.ITEM_GROUP,
            "categorical columns to encode (omit → auto-detect object/category)",
            optional=True,
        ),
    },
    static_params={
        "method": "auto",
        "max_onehot_cardinality": 5,
        "drop_first": False,
    },
    output_files={
        "encoded_csv":  "encoded.csv",
        "mapping_json": "encoding_mapping.json",
    },
    output_kind={"encoded_csv": "t", "mapping_json": "s"},
)


def _auto_detect_categorical(df: pd.DataFrame) -> List[str]:
    out = []
    for c in df.columns:
        if (df[c].dtype == "object"
                or pd.api.types.is_categorical_dtype(df[c])
                or pd.api.types.is_string_dtype(df[c])):
            if df[c].notna().any():
                out.append(c)
    return out


class EncodeCategoricalSolver:
    contract = ENCODE_CATEGORICAL_CONTRACT

    def __init__(self, method: str = "auto",
                  max_onehot_cardinality: int = 5,
                  drop_first: bool = False) -> None:
        self.method = (method or "auto").strip().lower()
        self.max_onehot = int(max_onehot_cardinality)
        self.drop_first = bool(drop_first)

    def _encode_one(self, df: pd.DataFrame, col: str,
                     method: str) -> tuple:
        col_series = df[col]
        unique_vals = sorted(col_series.dropna().astype(str).unique().tolist())
        if method == "label" or (method == "auto"
                                    and len(unique_vals) > self.max_onehot):
            mp = {v: i for i, v in enumerate(unique_vals)}
            encoded = col_series.astype(str).map(mp)
            encoded = encoded.where(col_series.notna(), np.nan)
            return ({col: encoded}, {
                "method": "label",
                "n_unique": len(unique_vals),
                "value_to_code": mp,
            })
        # one-hot
        dummies = pd.get_dummies(col_series, prefix=col,
                                    drop_first=self.drop_first,
                                    dummy_na=False)
        dummies = dummies.astype(int)
        return (dict(zip(dummies.columns, [dummies[c] for c in dummies.columns])),
                {
                    "method": "onehot",
                    "n_unique": len(unique_vals),
                    "drop_first": self.drop_first,
                    "new_columns": list(dummies.columns),
                    "value_levels": unique_vals,
                })

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        if self.method not in {"auto", "onehot", "label"}:
            raise OperatorInputError(
                "INVALID_STAT", solver="encode_categorical",
                stat=self.method, whitelist=["auto", "onehot", "label"],
            )

        raw = mapping.get("categorical_cols")
        if raw is None:
            cat_cols = _auto_detect_categorical(df)
        elif isinstance(raw, list):
            cat_cols = [c for c in raw if c in df.columns]
        elif isinstance(raw, str):
            cat_cols = [raw] if raw in df.columns else []
        else:
            cat_cols = []

        if not cat_cols:
            # Nothing to encode → return the original frame untouched so
            # downstream coder steps still get a stable artifact.
            path = Path(output_dir) / ENCODE_CATEGORICAL_CONTRACT.output_files["encoded_csv"]
            df.to_csv(path, index=False)
            mp_path = Path(output_dir) / ENCODE_CATEGORICAL_CONTRACT.output_files["mapping_json"]
            mp_path.write_text(json.dumps({
                "encoded_columns": [],
                "skipped_columns": list(df.columns),
                "reason": "no categorical columns detected",
            }, indent=2), encoding="utf-8")
            return {"encoded_csv": str(path), "mapping_json": str(mp_path),
                    "n_encoded": 0}

        out_df = df.copy()
        mapping_info: Dict[str, Any] = {}
        for col in cat_cols:
            new_cols, info = self._encode_one(out_df, col, self.method)
            out_df = out_df.drop(columns=[col])
            for nc, vals in new_cols.items():
                out_df[nc] = vals.values
            mapping_info[col] = info

        path = Path(output_dir) / ENCODE_CATEGORICAL_CONTRACT.output_files["encoded_csv"]
        out_df.to_csv(path, index=False)
        mp_path = Path(output_dir) / ENCODE_CATEGORICAL_CONTRACT.output_files["mapping_json"]
        mp_path.write_text(json.dumps({
            "encoded_columns": list(mapping_info.keys()),
            "details": mapping_info,
            "method": self.method,
            "max_onehot_cardinality": self.max_onehot,
        }, indent=2, default=str), encoding="utf-8")

        return {"encoded_csv": str(path),
                "mapping_json": str(mp_path),
                "n_encoded": len(mapping_info),
                "encoded_columns": list(mapping_info.keys())}


def get_solver(method: str = "auto",
                max_onehot_cardinality: int = 5,
                drop_first: bool = False) -> EncodeCategoricalSolver:
    return EncodeCategoricalSolver(method=method,
                                       max_onehot_cardinality=max_onehot_cardinality,
                                       drop_first=drop_first)


def selftest() -> Dict[str, Any]:
    """Ground truth:
       - color (3 levels) under 'auto' & max_onehot=5  → onehot, 3 cols
       - size (7 levels)  under 'auto' & max_onehot=5  → label, 1 col 0..6
    """
    import tempfile
    df = pd.DataFrame({
        "color": ["red", "green", "blue", "red", "blue"] * 4,
        "size":  ["S", "M", "L", "XL", "XXL", "XXXL", "XXXXL"] * 2 + ["S"] * 6,
        "value": np.arange(20, dtype=float),
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        s = get_solver(method="auto", max_onehot_cardinality=5)
        out = s.run(df=df, mapping=ColumnMapping({}),
                      output_dir=Path(tmp))
        enc = pd.read_csv(out["encoded_csv"])
        info = json.loads(Path(out["mapping_json"]).read_text(encoding="utf-8"))
        details = info["details"]
        if details["color"]["method"] != "onehot":
            diffs.append("color should be onehot")
        if details["size"]["method"] != "label":
            diffs.append("size should be label (7 unique > max_onehot=5)")
        # cross-check label codes
        expected_label = {v: i for i, v in
                            enumerate(sorted(set(df["size"].tolist())))}
        if details["size"]["value_to_code"] != expected_label:
            diffs.append(f"size label map: got {details['size']['value_to_code']}"
                          f" vs expected {expected_label}")
        for level in ["red", "green", "blue"]:
            if f"color_{level}" not in enc.columns:
                diffs.append(f"missing one-hot col color_{level}")
    return {"ok": not diffs,
            "summary": ("encode_categorical onehot/label split matches "
                          "max_onehot_cardinality contract" if not diffs
                          else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs}}


__all__ = ["ENCODE_CATEGORICAL_CONTRACT", "EncodeCategoricalSolver",
            "get_solver", "selftest"]
