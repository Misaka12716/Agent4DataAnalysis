"""Feature selection operator: SFS and importance-based selection.

Provides Sequential Forward Selection (SFS) and feature-importance-based
selection for reducing dimensionality before ML.  Uses sklearn SFS or
model.feature_importances_.

Output: selected_features.csv, selection_report.json.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


CONTRACT = SolverContract(
    name="feature_selection",
    capability="F09_dimensionality_reduction_features",
    description=(
        "Feature selection via SFS (Sequential Forward Selection) or "
        "model-based importance ranking. Supports forward/backward/floating SFS. "
        "Output: selected_features.csv (id + selected columns), "
        "selection_report.json (ranking + scores). "
        "Use when: feature selection, variable selection, forward/backward "
        "selection, recursive feature elimination (RFE), feature importance, "
        "mutual information, dimensionality / predictor reduction."
    ),
    roles={
        "id_col": RoleSpec(Role.ID, "row identifier column"),
        "feature_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "Numeric feature columns to select from.",
        ),
        "target_col": RoleSpec(
            Role.NUMERIC_TARGET,
            "Target column (numeric for regression, binary 0/1 for classification).",
        ),
        "n_features_to_select": RoleSpec(
            Role.PARAMS,
            "Number of features to select. Default: 10.",
            optional=True,
        ),
        "method": RoleSpec(
            Role.PARAMS,
            "Selection method: sfs_forward (default), sfs_backward, "
            "rf_importance, mutual_info.",
            optional=True,
        ),
    },
    static_params={"n_features_to_select": 10, "method": "sfs_forward",
                   "cv_folds": 5, "random_state": 42},
    output_files={
        "selected_features_csv": "selected_features.csv",
        "selection_report_json": "selection_report.json",
    },
    output_kind={"selected_features_csv": "t", "selection_report_json": "s"},
)


class FeatureSelectionSolver:
    contract = CONTRACT

    def __init__(self, n_features_to_select: int = 10,
                 method: str = "sfs_forward", cv_folds: int = 5,
                 random_state: int = 42):
        self.n_features = n_features_to_select
        self.method = method
        self.cv_folds = cv_folds
        self.random_state = random_state

    @staticmethod
    def _is_classification(y: np.ndarray) -> bool:
        """Treat target as classification if it has a small discrete set of
        values, regardless of dtype (binary 0/1 floats are common in real
        CSVs after pd.read_csv)."""
        uniq = np.unique(y[~np.isnan(y)] if np.issubdtype(y.dtype, np.floating)
                          else y)
        if len(uniq) <= 2:
            return True
        if len(uniq) <= 10 and np.allclose(uniq, uniq.astype(int)):
            return True
        return False

    def _select_rf_importance(self, X, y, feature_names):
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        is_class = self._is_classification(y)
        if is_class:
            model = RandomForestClassifier(n_estimators=100,
                                            random_state=self.random_state)
            model.fit(X, y.astype(int))
        else:
            model = RandomForestRegressor(n_estimators=100,
                                           random_state=self.random_state)
            model.fit(X, y)
        importances = model.feature_importances_
        idx = np.argsort(importances)[::-1][:self.n_features]
        return [feature_names[i] for i in idx], importances[idx].tolist()

    def _select_mutual_info(self, X, y, feature_names):
        from sklearn.feature_selection import mutual_info_classif, mutual_info_regression
        is_class = self._is_classification(y)
        mi_func = mutual_info_classif if is_class else mutual_info_regression
        y_use = y.astype(int) if is_class else y
        mi = mi_func(X, y_use, random_state=self.random_state)
        idx = np.argsort(mi)[::-1][:self.n_features]
        return [feature_names[i] for i in idx], mi[idx].tolist()

    def _select_sfs(self, X, y, feature_names, direction):
        from sklearn.feature_selection import SequentialFeatureSelector
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import LogisticRegression, LinearRegression
        is_class = self._is_classification(y)
        if is_class:
            estimator = RandomForestClassifier(n_estimators=100, n_jobs=-1,
                                                random_state=self.random_state)
            y_use = y.astype(int)
        else:
            estimator = LinearRegression()
            y_use = y
        sfs = SequentialFeatureSelector(
            estimator, n_features_to_select=self.n_features,
            direction=direction, cv=self.cv_folds, n_jobs=-1,
        )
        sfs.fit(X, y_use)
        selected = [feature_names[i] for i in range(len(feature_names))
                    if sfs.get_support()[i]]
        return selected, []

    def run(self, df, mapping, output_dir):
        import json
        id_col = mapping.get("id_col")
        feature_columns = mapping.get("feature_columns")
        target_col = mapping.get("target_col")
        n_feat = int(mapping.get("n_features_to_select") or self.n_features)
        method = str(mapping.get("method") or self.method)

        if not id_col or not feature_columns or not target_col:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="feature_selection",
                                     hint="id_col, feature_columns, target_col required")

        if isinstance(feature_columns, str):
            feature_columns = [c.strip() for c in feature_columns.split(",")]
        feature_columns = [c for c in feature_columns if c in df.columns]
        if len(feature_columns) < 2:
            raise OperatorInputError("INSUFFICIENT_FEATURES",
                solver="feature_selection",
                hint=f"need >= 2 features, got {len(feature_columns)}")

        X = df[feature_columns].fillna(df[feature_columns].median()).to_numpy(dtype=np.float64)
        y = df[target_col].to_numpy()
        y = y[np.isfinite(y)]
        if len(y) != len(X):
            valid_mask = df[target_col].notna() & np.isfinite(df[target_col])
            X = X[valid_mask.values]
            y = y

        n_feat = min(n_feat, len(feature_columns))
        self.n_features = n_feat

        if "rf_importance" in method:
            selected, scores = self._select_rf_importance(X, y, feature_columns)
        elif "mutual_info" in method:
            selected, scores = self._select_mutual_info(X, y, feature_columns)
        elif "backward" in method:
            selected, scores = self._select_sfs(X, y, feature_columns, "backward")
        else:
            selected, scores = self._select_sfs(X, y, feature_columns, "forward")

        out_df = df[[id_col] + selected].copy()
        out_path = output_dir / "selected_features.csv"
        out_df.to_csv(out_path, index=False)

        report = {
            "method": method, "n_features_selected": len(selected),
            "selected_features": selected,
            "scores": scores if scores else [],
            "all_features": feature_columns,
        }
        rpt_path = output_dir / "selection_report.json"
        with open(rpt_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        return {"selected_features_csv": str(out_path),
                "selection_report_json": str(rpt_path),
                "n_selected": len(selected)}


def get_solver(n_features_to_select: int = 10, method: str = "sfs_forward",
               cv_folds: int = 5, random_state: int = 42):
    return FeatureSelectionSolver(n_features_to_select=n_features_to_select,
                                   method=method, cv_folds=cv_folds,
                                   random_state=random_state)
