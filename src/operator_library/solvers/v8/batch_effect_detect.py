"""Batch-effect detector for sample × gene expression tables — V8.3 B6.

Mirrors GenoTEX paper ``tools/statistics.py::detect_batch_effect``
(L152-179): runs a PCA on the (centred) feature matrix XXᵀ, sorts the
top-10 eigenvalues descending, normalises them by the largest, then
checks for a "large gap" between consecutive normalised eigenvalues.
A gap larger than the empirical threshold ``200/n_samples`` is taken
as evidence of a discrete latent factor — typically batch / platform.

Why this operator exists
------------------------
Down-stream operators (``lasso_cv_select``, ``lmm_select``,
``residualization_regress``) need to know whether the data is
contaminated by a strong nuisance factor *before* deciding which
regression backend to invoke.  This is exactly the routing decision
paper ``regress.py`` makes at L29-33::

    has_batch_effect = detect_batch_effect(X)
    if has_batch_effect:
        model_constructor = LMM
    else:
        model_constructor = Lasso

Inputs
------
A sample × feature numeric table.  The trait and any covariate /
id columns are skipped automatically (mirrors paper which calls
``trait_data.drop(columns=[trait, 'Age', 'Gender'])`` first).

Outputs
-------
``batch_effect.json``::

    {
        "has_batch_effect":      bool,
        "max_gap":               float,
        "threshold":             float,    # 200 / n_samples
        "n_samples":             int,
        "n_features_used":       int,
        "eigenvalues_top10":     [float, ...],   # raw descending
        "eigenvalues_normalised":[float, ...],   # divided by λ_max
        "gap_index":             int,            # arg-max of gap
        "method":                "pca_eigvalue_gap",
        "reference":             "GenoTEX statistics.py L152-179",
    }

References
----------
* Liu et al. 2025 "GenoTEX" — ``tools/statistics.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="batch_effect_detect",
    capability="F_bio_batch_effect_detect",
    description=(
        "Detect potential batch effects in a sample × gene expression "
        "table using the PCA eigenvalue-gap heuristic from the "
        "GenoTEX paper (statistics.py L152-179).  Computes the top-10 "
        "eigenvalues of XXᵀ on centred features, normalises by the "
        "largest, and flags a batch effect when any consecutive gap "
        "exceeds 200/n_samples.  Outputs a small JSON with the "
        "decision and supporting numbers.  Use this *before* fitting "
        "Lasso / LMM to decide which regression backend to invoke."
    ),
    roles={
        "sample_id_col": RoleSpec(
            Role.ID,
            "Sample identifier column.  Skipped during the PCA.",
            optional=True,
        ),
        "target_col": RoleSpec(
            Role.NUMERIC_TARGET,
            "Trait column.  Skipped during the PCA so only the "
            "feature matrix is decomposed.",
            optional=True,
        ),
    },
    static_params={
        # paper threshold ratio (numerator / n_samples).  Don't change
        # unless you know what you're doing.
        "gap_threshold_numerator": 200.0,
        "top_k_eigvals": 10,
        # Same covariate-dropping convention as lasso_cv_select so
        # the PCA is run on the gene matrix only.
        "drop_covariates": True,
        "covariate_cols": ["Age", "Gender"],
    },
    output_files={
        "batch_effect_json": "batch_effect.json",
    },
    output_kind={"batch_effect_json": "s"},
)


def _unwrap_mapping_value(v: Any) -> Any:
    if isinstance(v, dict) and len(v) == 1:
        only = next(iter(v.values()))
        return _unwrap_mapping_value(only)
    return v


def _numeric_feature_cols(df: pd.DataFrame,
                            target_col: Optional[str],
                            sample_id_col: Optional[str],
                            drop_covariates: bool,
                            covariate_cols: List[str]) -> List[str]:
    skip: set = set()
    if target_col:
        skip.add(target_col)
    if sample_id_col:
        skip.add(sample_id_col)
    skip.add("__row_id__")
    cov_lc: set = (
        {str(c).strip().lower() for c in covariate_cols}
        if drop_covariates and covariate_cols else set()
    )
    cols: List[str] = []
    for c in df.columns:
        if c in skip:
            continue
        if str(c).strip().lower() in cov_lc:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


class BatchEffectDetectSolver:
    contract = CONTRACT

    def __init__(self, gap_threshold_numerator: float = 200.0,
                  top_k_eigvals: int = 10,
                  drop_covariates: bool = True,
                  covariate_cols: Optional[List[str]] = None):
        self.gap_threshold_numerator = float(gap_threshold_numerator)
        self.top_k_eigvals = int(top_k_eigvals)
        self.drop_covariates = bool(drop_covariates)
        self.covariate_cols = (list(covariate_cols)
                                  if covariate_cols is not None
                                  else ["Age", "Gender"])

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        sample_id_col = _unwrap_mapping_value(
            mapping.get("sample_id_col"))
        target_col = _unwrap_mapping_value(mapping.get("target_col"))

        gene_cols = _numeric_feature_cols(
            df, target_col=target_col, sample_id_col=sample_id_col,
            drop_covariates=self.drop_covariates,
            covariate_cols=self.covariate_cols,
        )
        if len(gene_cols) < 2:
            raise ValueError(
                f"batch_effect_detect: need ≥2 numeric feature "
                f"columns; got {len(gene_cols)}.")

        X_full = df[gene_cols].apply(pd.to_numeric, errors="coerce")
        keep = X_full.notna().all(axis=1)
        if int(keep.sum()) < 3:
            raise ValueError(
                f"batch_effect_detect: only {int(keep.sum())} fully-"
                f"finite samples after coercion; need ≥3.")
        X = X_full.loc[keep].to_numpy(dtype=float)
        n_samples = int(X.shape[0])
        n_features = int(X.shape[1])

        # Faithful port of paper detect_batch_effect:
        X_centered = X - X.mean(axis=0)
        XXt = X_centered @ X_centered.T
        eig_all = np.linalg.eigvalsh(XXt)  # ascending
        eig_desc = np.sort(eig_all)[::-1]
        k = min(self.top_k_eigvals, len(eig_desc))
        eig_top = eig_desc[:k].astype(float)
        # Guard against degenerate λ_max = 0
        lam_max = float(eig_top[0]) if eig_top[0] > 0 else 1.0
        eig_norm = (eig_top / lam_max).astype(float)

        # Compute the gap series (paper L174-178)
        threshold = self.gap_threshold_numerator / float(n_samples)
        gaps = [float(eig_norm[i] - eig_norm[i + 1])
                  for i in range(len(eig_norm) - 1)]
        max_gap = float(max(gaps)) if gaps else 0.0
        gap_index = int(np.argmax(gaps)) if gaps else -1
        has_batch_effect = any(g > threshold for g in gaps)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "has_batch_effect":       bool(has_batch_effect),
            "max_gap":                max_gap,
            "threshold":              float(threshold),
            "n_samples":              n_samples,
            "n_features_used":        n_features,
            "eigenvalues_top10":      [float(v) for v in eig_top],
            "eigenvalues_normalised": [float(v) for v in eig_norm],
            "gaps":                   gaps,
            "gap_index":              gap_index,
            "method":                 "pca_eigvalue_gap",
            "reference":              "GenoTEX statistics.py L152-179",
            "covariates_dropped": [
                c for c in df.columns
                if str(c).lower() in
                {x.lower() for x in self.covariate_cols}
                and c != target_col
            ] if self.drop_covariates else [],
        }
        out_path = out_dir / CONTRACT.output_files["batch_effect_json"]
        out_path.write_text(json.dumps(result, indent=2,
                                            default=str),
                                encoding="utf-8")
        return {
            "batch_effect_json": str(out_path),
            **result,
        }


def get_solver(gap_threshold_numerator: float = 200.0,
                top_k_eigvals: int = 10,
                drop_covariates: bool = True,
                covariate_cols: Optional[List[str]] = None,
                ) -> BatchEffectDetectSolver:
    return BatchEffectDetectSolver(
        gap_threshold_numerator=gap_threshold_numerator,
        top_k_eigvals=top_k_eigvals,
        drop_covariates=drop_covariates,
        covariate_cols=covariate_cols,
    )
