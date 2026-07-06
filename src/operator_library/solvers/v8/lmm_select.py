"""Batch-adjusted multivariate gene selection — V8.3 B7 (``lmm_select``).

This operator is the **batch-effect-aware** counterpart of
``lasso_cv_select``.  It is meant to be invoked when an upstream
``batch_effect_detect`` step (or the user) has flagged the data as
contaminated by a discrete latent factor (typically a sequencing
batch, microarray platform, or scanning date).

Implementation note
-------------------
The GenoTEX paper plugs an L1-penalised Linear Mixed Model
(``sparse_lmm.LMM`` — research code, not on PyPI) into the same
``ResidualizationRegressor`` slot as Lasso.  We use a well-known
**frequentist two-step approximation** that is reproducible with
only ``scikit-learn`` + ``statsmodels`` (both pure pip):

1. **Step 1 — batch residualisation.**  Regress the trait ``y`` on
   one-hot-encoded batch dummies via ordinary least squares (OLS)
   and keep the residual ``y_resid``.  If the trait is binary,
   batch-mean centering is applied per group.  When a ``batch_col``
   is not provided, we cluster samples into ``n_batches`` groups
   via the top principal component (PCA-1 quantile bins) — the
   same surrogate-batch idea used in limma's ``removeBatchEffect``.
2. **Step 2 — sparse selection on residuals.**  Standardise the
   gene matrix and run ``LassoCV`` (continuous trait) or
   ``LogisticRegressionCV`` with L1 penalty (binary trait) on
   ``(X, y_resid)``.  When the random effect is just an intercept
   per batch (no slope), step 1 is mathematically equivalent to
   absorbing the batch into a fixed effect, which is the BLUP of a
   MixedLM with diagonal Σ.

This yields the same *direction* of correction as a full MixedLM
(genes whose signal is confounded with batch are penalised) without
needing per-gene MixedLM fits (which would take ~5h on 17K genes).

Static parameters
-----------------
* ``batch_strategy``   : ``"explicit"`` (use ``batch_col``) /
                         ``"pca1_quantile"`` (default; surrogate batch
                         via PCA-1 quantile binning) /
                         ``"none"`` (no batch step → behaves like
                         ``lasso_cv_select``)
* ``n_surrogate_batches``: only for ``pca1_quantile`` (default 3)
* All other knobs (``alphas, cv, max_features, standardize,
                   binary_backend, drop_covariates, …``) mirror
                   ``lasso_cv_select`` 1-for-1 so the operator is a
                   drop-in replacement.

Outputs
-------
``lmm_table.csv``  — identical schema to ``lasso_table.csv``
``lmm_summary.json`` — adds ``batch_strategy``, ``n_batches``,
                       ``batch_resid_r2``,
                       ``covariates_dropped``, ``backend``.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


PAPER_ALPHA_GRID: List[float] = [1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0]


CONTRACT = SolverContract(
    name="lmm_select",
    capability="F_bio_lmm_gene_selection",
    description=(
        "Batch-adjusted multivariate L1 gene selection.  Frequentist "
        "two-step approximation of the GenoTEX paper's "
        "L1-penalised Linear Mixed Model: first residualises the "
        "trait against batch indicators (explicit ``batch_col`` or "
        "surrogate PCA-1 quantile bins), then runs LassoCV / "
        "L1-LogisticRegressionCV on the residualised trait against "
        "standardised gene expression.  Use this instead of "
        "``lasso_cv_select`` whenever ``batch_effect_detect`` flags "
        "``has_batch_effect=True``, or whenever the data is known "
        "to come from multiple platforms / scanning batches / "
        "sequencing runs."
    ),
    roles={
        "sample_id_col": RoleSpec(
            Role.ID,
            "Sample identifier column.",
            optional=True,
        ),
        "target_col": RoleSpec(
            Role.NUMERIC_TARGET,
            "Trait column.  Binary traits auto-route to logistic "
            "backend; continuous to LassoCV.",
        ),
        "batch_col": RoleSpec(
            Role.CATEGORICAL,
            "Batch / platform / cohort indicator column.  Required "
            "only when ``batch_strategy='explicit'``; ignored "
            "otherwise.  Discrete (any dtype) — gets one-hot "
            "encoded internally.",
            optional=True,
        ),
    },
    static_params={
        "alphas": None,                # None ⇒ PAPER_ALPHA_GRID
        "cv": 5,
        "max_iter": 20000,
        "max_features": 1000,
        "standardize": True,
        "random_state": 42,
        "binary_backend": "logistic",
        "drop_covariates": True,
        "covariate_cols": ["Age", "Gender"],
        # batch handling
        "batch_strategy": "pca1_quantile",   # explicit / pca1_quantile / none
        "n_surrogate_batches": 3,
        # boundary adaptation + fallback (same as lasso_cv_select)
        "alpha_boundary_adaptive": True,
        "univariate_fallback": True,
        "fallback_top_k": 50,
    },
    output_files={
        "lmm_table_csv":    "lmm_table.csv",
        "lmm_summary_json": "lmm_summary.json",
    },
    output_kind={"lmm_table_csv": "s", "lmm_summary_json": "s"},
)


# Shared helpers — reuse the same coercion/encoding logic as
# lasso_cv_select.  Imported lazily so we don't create a circular
# dependency at the registry layer.
def _unwrap_mapping_value(v: Any) -> Any:
    if isinstance(v, dict) and len(v) == 1:
        only = next(iter(v.values()))
        return _unwrap_mapping_value(only)
    return v


def _coerce_continuous(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    sstr = s.astype(str).str.strip()
    cleaned = sstr.str.replace(r"\s*([a-zA-Z%]+)\s*$", "", regex=True)
    mask_eu = (cleaned.str.count(",") == 1) & (~cleaned.str.contains(r"\."))
    cleaned = cleaned.where(~mask_eu, cleaned.str.replace(",", ".",
                                                              regex=False))
    return pd.to_numeric(cleaned, errors="coerce")


def _classify_trait(s: pd.Series) -> str:
    sclean = s.dropna()
    if len(sclean) == 0:
        return "categorical"
    sstr = sclean.astype(str).str.strip()
    uniq_str = [u for u in sstr.unique().tolist()
                  if u not in ("", "nan", "None", "NA", "<NA>")]
    if len(uniq_str) == 2:
        return "binary"
    coerced = _coerce_continuous(sclean)
    if (coerced.notna().sum() >= 0.8 * len(sclean)
            and coerced.dropna().nunique() >= 3):
        return "continuous"
    return "categorical"


def _binary_encode(s: pd.Series) -> pd.Series:
    sclean = s.dropna()
    snum = pd.to_numeric(sclean, errors="coerce")
    if snum.notna().sum() == len(sclean) and snum.nunique() == 2:
        levels = sorted(snum.unique().tolist())
        out = pd.to_numeric(s, errors="coerce")
        out = (out == levels[1]).astype(float)
        out[pd.to_numeric(s, errors="coerce").isna()] = np.nan
        return out
    sstr = s.astype(str).str.strip()
    uniq = sorted([u for u in sstr.unique().tolist()
                     if u not in ("", "nan", "None", "NA", "<NA>")])
    return pd.Series(np.where(sstr == uniq[1], 1.0,
                                np.where(sstr == uniq[0], 0.0, np.nan)),
                       index=s.index)


def _gene_cols(df: pd.DataFrame, sample_id_col: Optional[str],
                 target_col: str, batch_col: Optional[str],
                 drop_covariates: bool,
                 covariate_cols: List[str]) -> List[str]:
    skip = {target_col, "__row_id__"}
    if sample_id_col:
        skip.add(sample_id_col)
    if batch_col:
        skip.add(batch_col)
    cov_lc = ({c.strip().lower() for c in covariate_cols}
                 if drop_covariates and covariate_cols else set())
    cols: List[str] = []
    for c in df.columns:
        if c in skip:
            continue
        if str(c).strip().lower() in cov_lc:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


def _univariate_rank(X: pd.DataFrame, y: pd.Series, trait_kind: str,
                       top_k: int) -> pd.DataFrame:
    from scipy import stats as _stats
    rows: List[Dict[str, Any]] = []
    if trait_kind == "binary":
        mask0 = y == 0
        mask1 = y == 1
        if int(mask0.sum()) < 2 or int(mask1.sum()) < 2:
            return pd.DataFrame(
                columns=["gene_id", "coefficient",
                          "abs_coefficient", "rank"])
        for g in X.columns:
            xg = X[g].to_numpy(dtype=float)
            try:
                t, p = _stats.ttest_ind(xg[mask1.to_numpy()],
                                            xg[mask0.to_numpy()],
                                            equal_var=False,
                                            nan_policy="omit")
            except Exception:
                t, p = (np.nan, np.nan)
            rows.append({"gene_id": g, "coefficient": float(t),
                            "abs_coefficient": float(abs(t))
                                if np.isfinite(t) else 0.0,
                            "_p": float(p) if np.isfinite(p) else 1.0})
    else:
        y_arr = y.to_numpy(dtype=float)
        for g in X.columns:
            xg = X[g].to_numpy(dtype=float)
            try:
                rho, p = _stats.spearmanr(xg, y_arr,
                                              nan_policy="omit")
                rho, p = float(rho), float(p)
            except Exception:
                rho, p = (np.nan, np.nan)
            rows.append({"gene_id": g, "coefficient": rho,
                            "abs_coefficient": float(abs(rho))
                                if np.isfinite(rho) else 0.0,
                            "_p": p if np.isfinite(p) else 1.0})
    tbl = pd.DataFrame(rows)
    tbl = tbl.sort_values("_p", ascending=True,
                              kind="mergesort").reset_index(drop=True)
    tbl = tbl.head(int(top_k)).reset_index(drop=True)
    tbl["rank"] = np.arange(1, len(tbl) + 1, dtype=int)
    return tbl[["gene_id", "coefficient", "abs_coefficient", "rank"]]


class LmmSelectSolver:
    contract = CONTRACT

    def __init__(self, alphas: Optional[List[float]] = None,
                  cv: int = 5, max_iter: int = 20000,
                  max_features: Optional[int] = 1000,
                  standardize: bool = True,
                  random_state: int = 42,
                  binary_backend: str = "logistic",
                  drop_covariates: bool = True,
                  covariate_cols: Optional[List[str]] = None,
                  batch_strategy: str = "pca1_quantile",
                  n_surrogate_batches: int = 3,
                  alpha_boundary_adaptive: bool = True,
                  univariate_fallback: bool = True,
                  fallback_top_k: int = 50):
        self.alphas = list(alphas) if alphas is not None else None
        self.cv = int(cv)
        self.max_iter = int(max_iter)
        self.max_features = (None if max_features is None
                                  else int(max_features))
        self.standardize = bool(standardize)
        self.random_state = int(random_state)
        self.binary_backend = str(binary_backend or "logistic").lower()
        if self.binary_backend not in {"linear", "logistic"}:
            raise ValueError(
                f"lmm_select: binary_backend must be one of "
                f"{{linear, logistic}}, got {self.binary_backend!r}")
        self.drop_covariates = bool(drop_covariates)
        self.covariate_cols = (list(covariate_cols)
                                  if covariate_cols is not None
                                  else ["Age", "Gender"])
        self.batch_strategy = str(batch_strategy or
                                       "pca1_quantile").lower()
        if self.batch_strategy not in {"explicit", "pca1_quantile",
                                              "none"}:
            raise ValueError(
                f"lmm_select: batch_strategy must be one of "
                f"{{explicit, pca1_quantile, none}}, got "
                f"{self.batch_strategy!r}")
        self.n_surrogate_batches = max(2, int(n_surrogate_batches))
        self.alpha_boundary_adaptive = bool(alpha_boundary_adaptive)
        self.univariate_fallback = bool(univariate_fallback)
        self.fallback_top_k = int(fallback_top_k)

    def _build_batch_labels(self, X: np.ndarray,
                                batch_series: Optional[pd.Series],
                                n_samples: int) -> Optional[np.ndarray]:
        """Return integer batch labels (length = n_samples) or None
        when no batch correction should be applied."""
        if self.batch_strategy == "none":
            return None
        if self.batch_strategy == "explicit":
            if batch_series is None or batch_series.isna().all():
                return None
            # Encode any dtype to integer codes.
            return pd.Categorical(batch_series).codes
        # pca1_quantile surrogate
        if n_samples < self.n_surrogate_batches * 2:
            return None
        # PC1 via SVD of centred X (faster than full PCA for top-1)
        Xc = X - X.mean(axis=0, keepdims=True)
        try:
            U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
            pc1 = U[:, 0] * S[0]
        except np.linalg.LinAlgError:
            return None
        # Quantile bin → equal-size batches
        edges = np.quantile(
            pc1,
            np.linspace(0, 1, self.n_surrogate_batches + 1))
        # ensure strictly increasing edges
        edges = np.unique(edges)
        if len(edges) < 3:
            return np.zeros(n_samples, dtype=int)
        labels = np.digitize(pc1, edges[1:-1], right=False)
        return labels.astype(int)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        from sklearn.linear_model import (LassoCV, LogisticRegressionCV,
                                              LinearRegression)
        from sklearn.preprocessing import StandardScaler

        sample_id_col = _unwrap_mapping_value(
            mapping.get("sample_id_col"))
        target_col = _unwrap_mapping_value(mapping.get("target_col"))
        batch_col = _unwrap_mapping_value(mapping.get("batch_col"))
        if target_col is None or target_col not in df.columns:
            raise KeyError(
                f"lmm_select: target_col {target_col!r} missing "
                "in DataFrame")

        # Override-friendly covariate handling.
        cov_cols_raw = _unwrap_mapping_value(
            mapping.get("covariate_cols")) if mapping.get(
            "covariate_cols") is not None else None
        if cov_cols_raw is None:
            cov_cols = list(self.covariate_cols)
        elif isinstance(cov_cols_raw, (list, tuple)):
            cov_cols = [str(c) for c in cov_cols_raw]
        elif isinstance(cov_cols_raw, str):
            cov_cols = [c.strip() for c in cov_cols_raw.split(",")
                          if c.strip()]
        else:
            cov_cols = list(self.covariate_cols)
        drop_cov_override = _unwrap_mapping_value(
            mapping.get("drop_covariates"))
        drop_cov = (self.drop_covariates if drop_cov_override is None
                       else bool(drop_cov_override))

        gene_cols = _gene_cols(df, sample_id_col, target_col,
                                  batch_col, drop_covariates=drop_cov,
                                  covariate_cols=cov_cols)
        if len(gene_cols) < 5:
            raise ValueError(
                f"lmm_select: need ≥5 feature columns; got "
                f"{len(gene_cols)}.")

        trait_kind = _classify_trait(df[target_col])
        if trait_kind == "categorical":
            n_lv = (df[target_col].dropna().astype(str).str.strip()
                      .nunique())
            raise ValueError(
                f"lmm_select: target_col {target_col!r} has {n_lv} "
                "discrete levels; only binary or continuous traits "
                "are supported.")

        X_full = df[gene_cols].apply(pd.to_numeric, errors="coerce")
        if trait_kind == "binary":
            y_full = _binary_encode(df[target_col])
        else:
            y_full = _coerce_continuous(df[target_col])

        # Build batch series before subsetting so we can include it
        # in the row-wise NA filter.
        if batch_col and batch_col in df.columns:
            batch_full = df[batch_col]
        else:
            batch_full = pd.Series(
                index=df.index, dtype=object)  # all-NaN sentinel

        keep = (y_full.notna()
                  & X_full.notna().all(axis=1))
        if batch_col and batch_col in df.columns:
            keep = keep & batch_full.notna()

        if int(keep.sum()) < max(8, self.cv * 2):
            raise ValueError(
                f"lmm_select: only {int(keep.sum())} fully-finite "
                f"samples; need ≥max(8, 2·cv)={max(8, self.cv * 2)}.")
        X = X_full.loc[keep].to_numpy(dtype=float)
        y = y_full.loc[keep].to_numpy(dtype=float)
        batch_series = (batch_full.loc[keep]
                          if batch_col and batch_col in df.columns
                          else None)
        n_samples = int(X.shape[0])

        # Standardise features once (per paper normalize_data).
        if self.standardize:
            scaler = StandardScaler(with_mean=True, with_std=True)
            X_use = scaler.fit_transform(X)
        else:
            X_use = X

        # ------------------------------------------------------------
        # Step 1: batch-residualise y.  When the chosen strategy
        # yields no labels (e.g. n too small, or strategy='none'),
        # we skip this step → behaves exactly like lasso_cv_select.
        # ------------------------------------------------------------
        labels = self._build_batch_labels(X_use, batch_series,
                                              n_samples)
        if labels is None or len(np.unique(labels)) < 2:
            y_resid = y.copy()
            batch_resid_r2 = 0.0
            n_batches = 0
            batch_used = "none"
        else:
            # One-hot encode (drop one level to avoid collinearity).
            uniq = np.unique(labels)
            D = np.zeros((n_samples, len(uniq) - 1), dtype=float)
            for j, lab in enumerate(uniq[1:]):
                D[:, j] = (labels == lab).astype(float)
            ols = LinearRegression(fit_intercept=True)
            ols.fit(D, y)
            y_hat = ols.predict(D)
            y_resid = y - y_hat
            ss_tot = float(np.sum((y - y.mean()) ** 2))
            ss_res = float(np.sum((y - y_hat) ** 2))
            batch_resid_r2 = (1.0 - ss_res / ss_tot
                                  if ss_tot > 0 else 0.0)
            n_batches = int(len(uniq))
            batch_used = self.batch_strategy

        # ------------------------------------------------------------
        # Step 2: L1-CV selection on (X_use, y_resid).
        # Binary residuals are no longer 0/1; pass to LassoCV.
        # ------------------------------------------------------------
        alphas = self.alphas or PAPER_ALPHA_GRID
        cv_folds = min(self.cv, n_samples)
        is_binary_logistic = (trait_kind == "binary"
                                  and self.binary_backend == "logistic"
                                  and batch_used == "none")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if is_binary_logistic:
                # No batch residualisation → pure classification
                Cs = [1.0 / a for a in alphas]
                model = LogisticRegressionCV(
                    Cs=Cs, cv=cv_folds, penalty="l1",
                    solver="liblinear", max_iter=self.max_iter,
                    scoring="neg_log_loss",
                    random_state=self.random_state, n_jobs=1,
                )
                model.fit(X_use, y.astype(int))
                coefs = model.coef_.ravel()
                best_C = float(model.C_[0])
                best_alpha = (1.0 / best_C if best_C > 0
                                 else float("inf"))
                backend_used = "l1_logistic_no_batch"
            else:
                # Linear LassoCV on residuals (works for both
                # continuous and binary-residualised traits).
                model = LassoCV(
                    alphas=alphas, cv=cv_folds,
                    max_iter=self.max_iter,
                    random_state=self.random_state, n_jobs=1,
                )
                model.fit(X_use, y_resid)
                coefs = model.coef_
                best_alpha = float(model.alpha_)
                backend_used = ("linear_lasso_resid"
                                  if batch_used != "none"
                                  else "linear_lasso")

        coef_series = pd.Series(coefs, index=gene_cols)
        nonzero = coef_series[coef_series != 0.0]

        # --- α-boundary adaptive retry (same logic as lasso_cv_select)
        retry_alphas_used: List[float] = []
        if (self.alpha_boundary_adaptive
                and len(nonzero) == 0
                and not is_binary_logistic
                and len(alphas) >= 3):
            sorted_a = sorted(alphas)
            picked = best_alpha
            if picked >= sorted_a[-1] - 1e-12:
                retry_grid = [sorted_a[-2]]
            elif picked <= sorted_a[0] + 1e-18:
                retry_grid = [sorted_a[1]]
            else:
                retry_grid = []
            for ra in retry_grid:
                retry_alphas_used.append(ra)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    from sklearn.linear_model import Lasso as _SkLasso
                    m2 = _SkLasso(alpha=float(ra),
                                    max_iter=self.max_iter,
                                    random_state=self.random_state)
                    m2.fit(X_use, y_resid)
                coefs2 = m2.coef_
                cs2 = pd.Series(coefs2, index=gene_cols)
                nz2 = cs2[cs2 != 0.0]
                if len(nz2) > 0:
                    coefs = coefs2
                    coef_series = cs2
                    nonzero = nz2
                    best_alpha = float(ra)
                    backend_used = backend_used + "_retry"
                    break

        # --- Univariate-augmented fallback (low-yield aware).
        # Trigger widened to ``nnz < fallback_top_k`` so sparse-
        # collapsed Lasso outputs on real GenoTEX cohorts (e.g.
        # batch_resid_r2 ≈ 0.05 → nnz = 5) still get a usable list.
        # Lasso non-zero genes are emitted first to preserve ranking
        # stability; univariate fills up to ``fallback_top_k`` while
        # dedup'ing against the Lasso picks; max_features still caps.
        fallback_used: Optional[str] = None
        nnz_count = int(len(nonzero))
        lasso_seed = pd.DataFrame({
            "gene_id":         nonzero.index,
            "coefficient":     nonzero.values,
            "abs_coefficient": np.abs(nonzero.values),
        }).sort_values("abs_coefficient",
                       ascending=False).reset_index(drop=True) \
            if nnz_count > 0 \
            else pd.DataFrame(columns=["gene_id", "coefficient",
                                       "abs_coefficient"])

        if self.univariate_fallback and nnz_count < self.fallback_top_k:
            target_total = self.fallback_top_k
            if self.max_features is not None:
                target_total = min(target_total, self.max_features)
            n_extra = max(0, target_total - nnz_count)
            if n_extra > 0:
                X_df = pd.DataFrame(X_use, columns=gene_cols)
                if batch_used == "none" and trait_kind == "binary":
                    uni = _univariate_rank(
                        X_df, pd.Series(y, name="y"),
                        "binary", top_k=n_extra + nnz_count)
                    uni_tag = "welch_top_k"
                else:
                    uni = _univariate_rank(
                        X_df, pd.Series(y_resid, name="y_resid"),
                        "continuous", top_k=n_extra + nnz_count)
                    uni_tag = "spearman_top_k_on_resid"
                seen = set(lasso_seed["gene_id"]) if nnz_count > 0 else set()
                uni_extra = (uni[~uni["gene_id"].isin(seen)]
                             .head(n_extra)
                             .reset_index(drop=True))
                tbl = pd.concat([lasso_seed[["gene_id", "coefficient",
                                              "abs_coefficient"]],
                                  uni_extra[["gene_id", "coefficient",
                                              "abs_coefficient"]]],
                                 ignore_index=True)
            else:
                tbl = lasso_seed.copy()
                uni_tag = "welch_top_k" if trait_kind == "binary" \
                          else "spearman_top_k_on_resid"
            tbl["rank"] = np.arange(1, len(tbl) + 1, dtype=int)
            if self.max_features is not None:
                tbl = tbl.head(self.max_features
                               ).reset_index(drop=True)
            fallback_used = (uni_tag if nnz_count == 0
                             else f"lasso_plus_{uni_tag}")
        else:
            if nnz_count == 0:
                tbl = pd.DataFrame(columns=["gene_id", "coefficient",
                                                  "abs_coefficient", "rank"])
            else:
                tbl = lasso_seed.copy()
                tbl["rank"] = np.arange(1, len(tbl) + 1, dtype=int)
                if self.max_features is not None:
                    tbl = tbl.head(self.max_features
                                    ).reset_index(drop=True)

        # Write outputs.
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tbl_path = out_dir / CONTRACT.output_files["lmm_table_csv"]
        tbl.to_csv(tbl_path, index=False)

        summary = {
            "trait_kind":           trait_kind,
            "backend":              backend_used,
            "batch_strategy_used":  batch_used,
            "n_batches":            int(n_batches),
            "batch_resid_r2":       float(batch_resid_r2),
            "n_samples_used":       int(n_samples),
            "n_features_input":     int(len(gene_cols)),
            "n_features_nonzero":   int(len(nonzero)),
            "n_features_reported":  int(len(tbl)),
            "best_alpha":           float(best_alpha),
            "cv_folds":             int(cv_folds),
            "standardised":         bool(self.standardize),
            "alpha_grid":           list(alphas),
            "max_features_cap":     self.max_features,
            "top_5":                tbl["gene_id"].head(5).tolist(),
            "covariates_dropped": [
                c for c in df.columns
                if str(c).lower() in {x.lower() for x in cov_cols}
                and c != target_col and c != batch_col
            ] if drop_cov else [],
            "retry_alphas_used":    retry_alphas_used,
            "fallback_used":        fallback_used,
        }
        sj_path = out_dir / CONTRACT.output_files["lmm_summary_json"]
        sj_path.write_text(json.dumps(summary, indent=2,
                                            default=str),
                                encoding="utf-8")
        return {
            "lmm_table_csv":    str(tbl_path),
            "lmm_summary_json": str(sj_path),
            **summary,
        }


def get_solver(alphas: Optional[List[float]] = None,
                cv: int = 5, max_iter: int = 20000,
                max_features: Optional[int] = 1000,
                standardize: bool = True,
                random_state: int = 42,
                binary_backend: str = "logistic",
                drop_covariates: bool = True,
                covariate_cols: Optional[List[str]] = None,
                batch_strategy: str = "pca1_quantile",
                n_surrogate_batches: int = 3,
                alpha_boundary_adaptive: bool = True,
                univariate_fallback: bool = True,
                fallback_top_k: int = 50,
                ) -> LmmSelectSolver:
    return LmmSelectSolver(
        alphas=alphas, cv=cv, max_iter=max_iter,
        max_features=max_features, standardize=standardize,
        random_state=random_state, binary_backend=binary_backend,
        drop_covariates=drop_covariates,
        covariate_cols=covariate_cols,
        batch_strategy=batch_strategy,
        n_surrogate_batches=n_surrogate_batches,
        alpha_boundary_adaptive=alpha_boundary_adaptive,
        univariate_fallback=univariate_fallback,
        fallback_top_k=fallback_top_k,
    )
