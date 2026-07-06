"""Covariate-adjusted multivariate L1 gene selection — V8.3 B8
(``residualization_regress``).

This operator is the **conditional-task** counterpart to
``lasso_cv_select``: it answers *what genes are associated with the
trait **after accounting for one or more condition variables Z***?

It is a faithful frequentist reproduction of the GenoTEX paper's
``ResidualizationRegressor`` (``tools/statistics.py`` L182-260) used
inside ``regress.py`` for every conditional (trait, condition) pair::

    model = ResidualizationRegressor(Lasso, best_config)
    model.fit(normalized_X, Y, normalized_Z)   # → β_Z, β_X
    significant_genes = interpret_result(model, ...)

Algorithm (paper L212-232)
--------------------------
1. **Standardise** ``X`` and ``Z`` (paper ``normalize_data``):
   per-feature centring + unit-variance scaling.
2. **Fit** Y on the augmented design ``[1 | Z]`` via closed-form
   pinv OLS (``Z_ones.T @ Z_ones``-pinv).  Compute the residual
   ``e_Y = Y − Y_hat``.  This removes the linear part of Y that is
   explained by Z (and by an intercept).
3. **Fit** ``LassoCV`` (continuous trait) or
   ``LogisticRegressionCV (penalty='l1')`` (binary trait) on
   ``(X, e_Y)``.  The non-zero coefficients are the gene panel that
   explains the variance in Y *not already absorbed by Z*.
4. Return the same ``lasso_table.csv`` schema (``gene_id,
   coefficient, abs_coefficient, rank``) plus a summary JSON.

Conditions Z that are accepted
------------------------------
* A single continuous covariate (e.g. ``Age``).
* A single binary covariate (e.g. ``Gender``) — encoded 0/1.
* A list of two or more covariates supplied via the
  ``condition_cols`` mapping role (e.g. ``["Age", "Gender"]``).

Outputs
-------
``residual_lasso_table.csv``  — same schema as ``lasso_table.csv``.
``residual_lasso_summary.json`` — additional fields:
    ``condition_cols``, ``condition_kind``, ``y_resid_r2``
    (= 1 − SSE_resid/SSE_total of the Y-on-Z step), ``backend``.

When ``condition_cols`` is *not* supplied, the operator degrades
gracefully to a plain ``lasso_cv_select`` (no residualisation), so
that the planner can safely route every (trait, condition) pair to
this operator regardless of conditionality.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


PAPER_ALPHA_GRID: List[float] = [1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0]


CONTRACT = SolverContract(
    name="residualization_regress",
    capability="F_bio_residual_lasso_gene_selection",
    description=(
        "Covariate-adjusted multivariate L1 gene selection.  Faithful "
        "frequentist reproduction of the GenoTEX paper's "
        "``ResidualizationRegressor`` (statistics.py L182-260) used "
        "for every conditional GTA problem.  First fits Y on the "
        "augmented [1|Z] design via closed-form OLS, takes the "
        "residual ``e_Y = Y − Y_hat``, then runs LassoCV / "
        "L1-LogisticRegressionCV on (X, e_Y).  Use this for any "
        "(trait, condition) pair where condition ≠ None — typical "
        "in GenoTEX conditional problems such as ``Height|Age``, "
        "``Diabetes|Gender``, ``Cancer|Hypertension``.  When no "
        "condition is supplied the operator degrades to plain "
        "``lasso_cv_select`` so the planner can route uniformly."
    ),
    roles={
        "sample_id_col": RoleSpec(
            Role.ID,
            "Sample identifier column.",
            optional=True,
        ),
        "target_col": RoleSpec(
            Role.NUMERIC_TARGET,
            "Trait (Y) column.  Binary or continuous.",
        ),
        "condition_cols": RoleSpec(
            Role.NUMERIC_LIST,
            "Condition / covariate columns (Z) to residualise Y "
            "against.  Accepts a single column name (str) or a list "
            "of names.  Each is auto-coerced to numeric (binary "
            "strings → 0/1).  When omitted, behaves like "
            "lasso_cv_select.",
            optional=True,
        ),
    },
    static_params={
        "alphas": None,
        "cv": 5,
        "max_iter": 20000,
        "max_features": 1000,
        "standardize": True,
        "random_state": 42,
        "binary_backend": "logistic",
        # Note: covariates listed in covariate_cols that ALSO appear
        # in condition_cols stay in the design matrix — the dropping
        # only removes them from the *gene* matrix.
        "drop_covariates_from_genes": True,
        "covariate_cols": ["Age", "Gender"],
        "alpha_boundary_adaptive": True,
        "univariate_fallback": True,
        "fallback_top_k": 50,
    },
    output_files={
        "residual_lasso_table_csv":    "residual_lasso_table.csv",
        "residual_lasso_summary_json": "residual_lasso_summary.json",
    },
    output_kind={
        "residual_lasso_table_csv":    "s",
        "residual_lasso_summary_json": "s",
    },
)


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


def _normalise_condition_cols(raw: Any) -> List[str]:
    """Mapping value → list of column names."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [c.strip() for c in raw.split(",") if c.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(c) for c in raw if str(c).strip()]
    return [str(raw)]


def _gene_cols(df: pd.DataFrame, sample_id_col: Optional[str],
                 target_col: str, condition_cols: List[str],
                 drop_covariates_from_genes: bool,
                 covariate_cols: List[str]) -> List[str]:
    skip = {target_col, "__row_id__"}
    if sample_id_col:
        skip.add(sample_id_col)
    for c in condition_cols:
        skip.add(c)
    cov_lc = ({c.strip().lower() for c in covariate_cols}
                 if drop_covariates_from_genes and covariate_cols
                 else set())
    cond_lc = {c.lower() for c in condition_cols}
    cols: List[str] = []
    for c in df.columns:
        if c in skip:
            continue
        lc = str(c).strip().lower()
        if lc in cov_lc and lc not in cond_lc:
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


class ResidualizationRegressSolver:
    contract = CONTRACT

    def __init__(self, alphas: Optional[List[float]] = None,
                  cv: int = 5, max_iter: int = 20000,
                  max_features: Optional[int] = 1000,
                  standardize: bool = True,
                  random_state: int = 42,
                  binary_backend: str = "logistic",
                  drop_covariates_from_genes: bool = True,
                  covariate_cols: Optional[List[str]] = None,
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
                f"residualization_regress: binary_backend must be "
                f"one of {{linear, logistic}}, got "
                f"{self.binary_backend!r}")
        self.drop_covariates_from_genes = bool(
            drop_covariates_from_genes)
        self.covariate_cols = (list(covariate_cols)
                                  if covariate_cols is not None
                                  else ["Age", "Gender"])
        self.alpha_boundary_adaptive = bool(alpha_boundary_adaptive)
        self.univariate_fallback = bool(univariate_fallback)
        self.fallback_top_k = int(fallback_top_k)

    def _encode_condition_matrix(self, df_sub: pd.DataFrame,
                                       condition_cols: List[str]
                                       ) -> Tuple[Optional[np.ndarray],
                                                       List[str]]:
        if not condition_cols:
            return None, []
        cols_used: List[str] = []
        Z_cols = []
        for c in condition_cols:
            if c not in df_sub.columns:
                continue
            col = df_sub[c]
            kind = _classify_trait(col)
            if kind == "binary":
                Z_cols.append(_binary_encode(col).to_numpy(
                    dtype=float))
                cols_used.append(c)
            elif kind == "continuous":
                Z_cols.append(_coerce_continuous(col).to_numpy(
                    dtype=float))
                cols_used.append(c)
            else:
                # Multinomial → one-hot, drop first level
                cat = pd.Categorical(col.astype(str))
                if len(cat.categories) >= 2:
                    for lv in list(cat.categories)[1:]:
                        Z_cols.append((col.astype(str) == lv
                                            ).astype(float).to_numpy())
                        cols_used.append(f"{c}={lv}")
        if not Z_cols:
            return None, []
        Z = np.column_stack(Z_cols)
        return Z, cols_used

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        from sklearn.linear_model import (LassoCV, LogisticRegressionCV)
        from sklearn.preprocessing import StandardScaler

        sample_id_col = _unwrap_mapping_value(
            mapping.get("sample_id_col"))
        target_col = _unwrap_mapping_value(mapping.get("target_col"))
        cond_cols_raw = _unwrap_mapping_value(
            mapping.get("condition_cols"))
        if target_col is None or target_col not in df.columns:
            raise KeyError(
                f"residualization_regress: target_col "
                f"{target_col!r} missing in DataFrame")
        condition_cols = _normalise_condition_cols(cond_cols_raw)
        # Keep only those that actually exist in df
        condition_cols = [c for c in condition_cols
                              if c in df.columns]

        # Override-friendly gene-side covariate handling.
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

        gene_cols = _gene_cols(
            df, sample_id_col, target_col, condition_cols,
            drop_covariates_from_genes=self.drop_covariates_from_genes,
            covariate_cols=cov_cols,
        )
        if len(gene_cols) < 5:
            raise ValueError(
                f"residualization_regress: need ≥5 feature columns; "
                f"got {len(gene_cols)}.")

        trait_kind = _classify_trait(df[target_col])
        if trait_kind == "categorical":
            n_lv = (df[target_col].dropna().astype(str).str.strip()
                      .nunique())
            raise ValueError(
                f"residualization_regress: target_col "
                f"{target_col!r} has {n_lv} discrete levels; only "
                "binary or continuous traits supported.")

        X_full = df[gene_cols].apply(pd.to_numeric, errors="coerce")
        if trait_kind == "binary":
            y_full = _binary_encode(df[target_col])
        else:
            y_full = _coerce_continuous(df[target_col])

        keep = y_full.notna() & X_full.notna().all(axis=1)
        if condition_cols:
            for c in condition_cols:
                col = df[c]
                kind_c = _classify_trait(col)
                if kind_c == "binary":
                    keep = keep & _binary_encode(col).notna()
                elif kind_c == "continuous":
                    keep = keep & _coerce_continuous(col).notna()
                else:
                    keep = keep & col.notna()
        if int(keep.sum()) < max(8, self.cv * 2):
            raise ValueError(
                f"residualization_regress: only {int(keep.sum())} "
                f"fully-finite samples; need ≥max(8, 2·cv)="
                f"{max(8, self.cv * 2)}.")

        X = X_full.loc[keep].to_numpy(dtype=float)
        y = y_full.loc[keep].to_numpy(dtype=float)
        df_sub = df.loc[keep]
        n_samples = int(X.shape[0])

        # Standardise X.
        if self.standardize:
            scaler = StandardScaler(with_mean=True, with_std=True)
            X_use = scaler.fit_transform(X)
        else:
            X_use = X

        # ------------------------------------------------------------
        # Build Z, standardise, residualise Y on [1 | Z] via paper
        # closed-form pinv OLS (statistics.py L218-222).
        # ------------------------------------------------------------
        Z, cond_used = self._encode_condition_matrix(df_sub,
                                                          condition_cols)
        if Z is not None and Z.shape[1] >= 1:
            if self.standardize:
                Z_use = StandardScaler(with_mean=True, with_std=True
                                          ).fit_transform(Z)
            else:
                Z_use = Z
            Z_ones = np.column_stack([np.ones(n_samples), Z_use])
            beta_Z = np.linalg.pinv(Z_ones.T @ Z_ones) @ Z_ones.T @ y
            y_hat = Z_ones @ beta_Z
            y_resid = y - y_hat
            ss_tot = float(np.sum((y - y.mean()) ** 2))
            ss_res = float(np.sum((y - y_hat) ** 2))
            y_resid_r2 = (1.0 - ss_res / ss_tot
                              if ss_tot > 0 else 0.0)
            condition_active = True
            condition_kind = ("multivariate"
                                  if Z.shape[1] >= 2
                                  else _classify_trait(
                                  df_sub[condition_cols[0]]))
        else:
            y_resid = y.copy()
            y_resid_r2 = 0.0
            condition_active = False
            condition_kind = "none"

        # ------------------------------------------------------------
        # L1 selection on (X_use, y_resid).
        # ------------------------------------------------------------
        alphas = self.alphas or PAPER_ALPHA_GRID
        cv_folds = min(self.cv, n_samples)
        is_binary_logistic = (trait_kind == "binary"
                                   and self.binary_backend == "logistic"
                                   and not condition_active)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if is_binary_logistic:
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
                backend_used = "l1_logistic_no_resid"
            else:
                model = LassoCV(
                    alphas=alphas, cv=cv_folds,
                    max_iter=self.max_iter,
                    random_state=self.random_state, n_jobs=1,
                )
                model.fit(X_use, y_resid)
                coefs = model.coef_
                best_alpha = float(model.alpha_)
                backend_used = ("linear_lasso_resid"
                                   if condition_active
                                   else "linear_lasso")

        coef_series = pd.Series(coefs, index=gene_cols)
        nonzero = coef_series[coef_series != 0.0]

        # α-boundary adaptive retry
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

        # Univariate-augmented fallback (low-yield aware) — same
        # structure as lasso_cv_select / lmm_select, but the residualised
        # ``y_resid`` is used as the response when a condition is active.
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
                if not condition_active and trait_kind == "binary":
                    uni = _univariate_rank(
                        X_df, pd.Series(y, name="y"),
                        "binary", top_k=n_extra + nnz_count)
                    uni_tag = "welch_top_k"
                else:
                    uni = _univariate_rank(
                        X_df, pd.Series(y_resid, name="y_resid"),
                        "continuous", top_k=n_extra + nnz_count)
                    uni_tag = ("spearman_top_k_on_resid"
                                if condition_active
                                else "spearman_top_k")
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
                if not condition_active and trait_kind == "binary":
                    uni_tag = "welch_top_k"
                else:
                    uni_tag = ("spearman_top_k_on_resid"
                               if condition_active else "spearman_top_k")
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

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tbl_path = (out_dir
                       / CONTRACT.output_files["residual_lasso_table_csv"])
        tbl.to_csv(tbl_path, index=False)

        summary = {
            "trait_kind":           trait_kind,
            "backend":              backend_used,
            "condition_active":     bool(condition_active),
            "condition_cols_used":  cond_used,
            "condition_kind":       condition_kind,
            "y_resid_r2":           float(y_resid_r2),
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
                and c != target_col
                and c not in condition_cols
            ] if self.drop_covariates_from_genes else [],
            "retry_alphas_used":    retry_alphas_used,
            "fallback_used":        fallback_used,
        }
        sj_path = (out_dir
                       / CONTRACT.output_files
                                 ["residual_lasso_summary_json"])
        sj_path.write_text(json.dumps(summary, indent=2,
                                            default=str),
                                encoding="utf-8")
        return {
            "residual_lasso_table_csv":    str(tbl_path),
            "residual_lasso_summary_json": str(sj_path),
            **summary,
        }


def get_solver(alphas: Optional[List[float]] = None,
                cv: int = 5, max_iter: int = 20000,
                max_features: Optional[int] = 1000,
                standardize: bool = True,
                random_state: int = 42,
                binary_backend: str = "logistic",
                drop_covariates_from_genes: bool = True,
                covariate_cols: Optional[List[str]] = None,
                alpha_boundary_adaptive: bool = True,
                univariate_fallback: bool = True,
                fallback_top_k: int = 50,
                ) -> ResidualizationRegressSolver:
    return ResidualizationRegressSolver(
        alphas=alphas, cv=cv, max_iter=max_iter,
        max_features=max_features, standardize=standardize,
        random_state=random_state, binary_backend=binary_backend,
        drop_covariates_from_genes=drop_covariates_from_genes,
        covariate_cols=covariate_cols,
        alpha_boundary_adaptive=alpha_boundary_adaptive,
        univariate_fallback=univariate_fallback,
        fallback_top_k=fallback_top_k,
    )
