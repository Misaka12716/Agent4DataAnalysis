"""Feature normalization and scaling operator.

Standardizes or normalizes numeric columns: z-score (StandardScaler),
MinMax (0-1 range), RobustScaler (median+IQR), MaxAbs.  Critical for
downstream distance-based models (SVM, KNN, PCA) and ML pipelines.

Output: scaled.csv (full table with scaled columns), scaler_params.json.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.preprocessing import (
    StandardScaler, MinMaxScaler, RobustScaler, MaxAbsScaler,
)

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


CONTRACT = SolverContract(
    name="normalize_scale",
    capability="F09_dimensionality_reduction_features",
    description=(
        "Scale/normalize numeric columns. Supports z-score (standard), "
        "minmax (0-1), robust (median+IQR), maxabs. Important for SVM/KNN/PCA "
        "where feature scale matters. Output: scaled.csv, scaler_params.json."
    ),
    roles={
        "id_col": RoleSpec(Role.ID, "row identifier column"),
        "feature_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "Numeric feature columns to scale. If empty, auto-detects all numeric columns.",
            optional=True,
        ),
        "method": RoleSpec(
            Role.PARAMS,
            "Scaling method: standard (z-score, default), minmax, robust, maxabs.",
            optional=True,
        ),
        "exclude_cols": RoleSpec(
            Role.NUMERIC_LIST,
            "Columns to exclude from scaling (kept as-is in output).",
            optional=True,
        ),
    },
    static_params={"method": "standard"},
    output_files={
        "scaled_csv": "scaled.csv",
        "params_json": "scaler_params.json",
    },
    output_kind={"scaled_csv": "t", "params_json": "s"},
)

_SCALER_MAP = {
    "standard": StandardScaler,
    "zscore": StandardScaler,
    "minmax": MinMaxScaler,
    "robust": RobustScaler,
    "maxabs": MaxAbsScaler,
}

# LLM-friendly aliases that the planner sometimes emits.  Keys are
# normalised to lowercase + dashes/underscores stripped before lookup,
# so "z-score" / "z_score" / "Z Score" / "minmax" / "min-max" all
# resolve to a canonical method name above.
_METHOD_ALIASES = {
    "z-score":   "zscore",
    "z_score":   "zscore",
    "z score":   "zscore",
    "min-max":   "minmax",
    "min_max":   "minmax",
    "min max":   "minmax",
    "minmaxscaler": "minmax",
    "standardscaler": "standard",
    "standardize": "standard",
    "standardized": "standard",
    "normal":    "standard",
}


def _canonical_method(method: str) -> str:
    """Return the canonical scaler-method key (or echoes the input
    untouched, in which case ``run`` will raise an INVALID_PARAM
    OperatorInputError listing the whitelist)."""
    raw = (method or "").strip().lower()
    if raw in _SCALER_MAP:
        return raw
    return _METHOD_ALIASES.get(raw, raw)


class NormalizeScaleSolver:
    contract = CONTRACT

    def __init__(self, method: str = "standard"):
        self.method = method

    def run(self, df, mapping, output_dir):
        import json
        id_col = mapping.get("id_col")
        raw_method = str(mapping.get("method") or self.method)
        method = _canonical_method(raw_method)
        scaler_cls = _SCALER_MAP.get(method)
        if scaler_cls is None:
            raise OperatorInputError("INVALID_PARAM",
                solver="normalize_scale",
                hint=f"unknown method: {raw_method}, choose from "
                      f"{list(_SCALER_MAP)} (aliases: {list(_METHOD_ALIASES)})")

        feature_columns = mapping.get("feature_columns")
        exclude_cols = mapping.get("exclude_cols") or []
        if isinstance(exclude_cols, str):
            exclude_cols = [c.strip() for c in exclude_cols.split(",") if c.strip()]

        if feature_columns:
            if isinstance(feature_columns, str):
                feature_columns = [c.strip() for c in feature_columns.split(",")]
            feature_columns = [c for c in feature_columns if c in df.columns]
        else:
            feature_columns = [
                c for c in df.columns
                if pd.api.types.is_numeric_dtype(df[c])
                and c not in exclude_cols
                and (id_col is None or c != id_col)
            ]

        feature_columns = [c for c in feature_columns if c not in exclude_cols]
        if len(feature_columns) == 0:
            raise OperatorInputError("NO_NUMERIC_COLUMNS",
                solver="normalize_scale")

        X = df[feature_columns].to_numpy(dtype=np.float64)
        finite_mask = np.all(np.isfinite(X), axis=1)
        n_before = len(X)
        X_clean = X[finite_mask]
        n_after = len(X_clean)

        scaler = scaler_cls()
        X_scaled = scaler.fit_transform(X_clean)

        result = df.copy()
        if n_after < n_before:
            for j, col in enumerate(feature_columns):
                result[col] = np.nan
                result.iloc[finite_mask, result.columns.get_loc(col)] = X_scaled[:, j]
        else:
            scaled_df = pd.DataFrame(X_scaled, columns=feature_columns, index=df.index)
            for col in feature_columns:
                result[col] = scaled_df[col]

        out_path = output_dir / "scaled.csv"
        result.to_csv(out_path, index=False)

        params = {
            "method": method,
            "scaled_columns": feature_columns,
            "excluded_columns": exclude_cols,
            "n_samples_scaled": int(n_after),
        }
        if hasattr(scaler, "mean_"):
            params["mean_"] = [float(v) for v in scaler.mean_]
        if hasattr(scaler, "scale_"):
            params["scale_"] = [float(v) for v in scaler.scale_]
        if hasattr(scaler, "min_"):
            params["min_"] = [float(v) for v in scaler.min_]
        if hasattr(scaler, "data_range_"):
            params["data_range_"] = [float(v) for v in scaler.data_range_]

        params_path = output_dir / "scaler_params.json"
        with open(params_path, "w", encoding="utf-8") as f:
            json.dump(params, f, indent=2, default=str)

        return {"scaled_csv": str(out_path), "params_json": str(params_path),
                "n_scaled_columns": len(feature_columns)}


def get_solver(method: str = "standard"):
    return NormalizeScaleSolver(method=method)
