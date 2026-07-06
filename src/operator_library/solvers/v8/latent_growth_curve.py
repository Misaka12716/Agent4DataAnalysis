"""W21 — Latent Growth Curve Model (linear LGCM).

Fits a random-intercept + random-slope linear growth model on long-form
panel data using ``statsmodels.MixedLM`` (REML), which is the same
mixed-model estimator R's ``lme4`` uses internally.

Reports
-------
- Fixed effects:   mean intercept + mean slope (across all subjects)
- Random effects:  var(intercept), var(slope), cov(intercept, slope)
- Per-subject BLUPs of (intercept, slope) for downstream GMM / clustering
- ICC(intercept) and proportion-of-variance metrics
- Optional Growth Mixture Model (k-class GMM) on subject (intercept, slope)
  estimates via sklearn GaussianMixture, with BIC for model selection.

References
----------
- Bryk AS, Raudenbush SW (1992) *Hierarchical Linear Models* §6.
- Singer JD, Willett JB (2003) *Applied Longitudinal Data Analysis* §4.
- Muthen B, Asparouhov T (2010) "Growth Mixture Modeling" — GMM rationale.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from sklearn.mixture import GaussianMixture

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="latent_growth_curve",
    capability="F_lgcm",
    description=(
        "Linear Latent Growth Curve Model on long-form panel data (id, "
        "time, y).  Random intercept + random slope via REML "
        "(statsmodels.MixedLM == lme4 lmer equivalent).  Returns fixed-effect "
        "mean intercept + mean slope, between-subject variances, per-subject "
        "BLUPs, and optional Growth Mixture (GMM) k-class trajectory "
        "classification with BIC-based k selection."
    ),
    roles={
        "id_col": RoleSpec(Role.ID, "Subject id (one value per subject)"),
        "time_col": RoleSpec(Role.NUMERIC, "Time / visit (numeric, in study units)"),
        "y_col": RoleSpec(Role.NUMERIC, "Outcome (e.g. PANSS total)"),
        "covariates": RoleSpec(Role.NUMERIC_LIST,
                                "Time-invariant covariates (e.g. age, sex)",
                                optional=True),
    },
    static_params={
        "gmm_k_grid": [1, 2, 3, 4],
        "min_obs_per_subject": 3,
        "random_state": 42,
    },
    output_files={
        "fixed_effects_csv": "lgcm_fixed_effects.csv",
        "random_effects_csv": "lgcm_random_effects.csv",
        "subject_blups_csv": "lgcm_subject_blups.csv",
        "trajectory_classes_csv": "lgcm_trajectory_classes.csv",
        "summary_json": "lgcm_summary.json",
    },
    output_kind={"fixed_effects_csv": "s",
                  "random_effects_csv": "t",
                  "subject_blups_csv": "t",
                  "trajectory_classes_csv": "t",
                  "summary_json": "s"},
)


class LatentGrowthCurveSolver:
    contract = CONTRACT

    def __init__(self, gmm_k_grid: Optional[List[int]] = None,
                 min_obs_per_subject: int = 3,
                 random_state: int = 42):
        self.k_grid = list(gmm_k_grid) if gmm_k_grid else [1, 2, 3, 4]
        self.min_obs_per_subject = int(min_obs_per_subject)
        self.random_state = int(random_state)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping.get("id_col")
        t_col = mapping.get("time_col")
        y_col = mapping.get("y_col")
        cov_cols = list(mapping.get("covariates") or [])
        if not all([id_col, t_col, y_col]):
            raise ValueError("id_col, time_col, y_col are required")

        sub = df[[id_col, t_col, y_col] + cov_cols].dropna().copy()
        sub.columns = ["__id__", "__t__", "__y__"] + cov_cols
        sub["__id__"] = sub["__id__"].astype(str)
        sub["__t__"] = sub["__t__"].astype(float)
        sub["__y__"] = sub["__y__"].astype(float)

        # Drop subjects with too few observations.
        counts = sub.groupby("__id__").size()
        kept = counts[counts >= self.min_obs_per_subject].index
        sub = sub[sub["__id__"].isin(kept)].copy()
        n_subj = sub["__id__"].nunique()
        if n_subj < 10:
            raise ValueError(f"only {n_subj} eligible subjects; need >=10")

        # Build formula.
        formula = "__y__ ~ __t__"
        if cov_cols:
            formula = formula + " + " + " + ".join(cov_cols)
        re_formula = "~__t__"  # random intercept + slope

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = smf.mixedlm(formula, data=sub, groups=sub["__id__"],
                                 re_formula=re_formula)
            fit = model.fit(method="lbfgs", reml=True, maxiter=200)

        # Fixed effects table.
        fe_rows: List[Dict[str, Any]] = []
        for name in fit.params.index:
            if name.startswith("Group ") or name.endswith(" Var") or " x " in name:
                continue
            beta = float(fit.params[name])
            try:
                se = float(fit.bse[name])
            except Exception:
                se = float("nan")
            try:
                p = float(fit.pvalues[name])
            except Exception:
                p = float("nan")
            fe_rows.append({
                "term": name,
                "beta": beta,
                "se": se,
                "z": beta / se if se > 0 else float("nan"),
                "p_value": p,
                "ci_low": beta - 1.96 * se,
                "ci_high": beta + 1.96 * se,
            })
        fe_df = pd.DataFrame(fe_rows)

        # Random-effect variances/covariances.
        cov_re = np.asarray(fit.cov_re)
        re_names = list(fit.cov_re.index)
        var_intercept = float(cov_re[0, 0])
        var_slope = float(cov_re[1, 1]) if cov_re.shape[0] > 1 else 0.0
        cov_int_slope = float(cov_re[0, 1]) if cov_re.shape[0] > 1 else 0.0
        var_resid = float(fit.scale)
        icc = var_intercept / (var_intercept + var_resid) \
            if (var_intercept + var_resid) > 0 else float("nan")

        re_df = pd.DataFrame([
            {"parameter": "var_intercept", "value": var_intercept},
            {"parameter": "var_slope", "value": var_slope},
            {"parameter": "cov_intercept_slope", "value": cov_int_slope},
            {"parameter": "var_residual", "value": var_resid},
            {"parameter": "icc_intercept", "value": float(icc)
                if not np.isnan(icc) else None},
            {"parameter": "corr_intercept_slope",
                "value": float(cov_int_slope /
                                np.sqrt(max(var_intercept * var_slope, 1e-12)))
                    if var_slope > 1e-12 else None},
        ])

        # Per-subject BLUPs (intercept + slope offsets).
        blups: Dict[str, np.ndarray] = fit.random_effects  # dict[group] -> Series
        fix_int = float(fit.params.get("Intercept", 0.0))
        fix_slope = float(fit.params.get("__t__", 0.0))
        blup_rows: List[Dict[str, Any]] = []
        for sid, vec in blups.items():
            vec = np.asarray(vec)
            int_offset = float(vec[0])
            slope_offset = float(vec[1]) if vec.size > 1 else 0.0
            blup_rows.append({
                "subject_id": str(sid),
                "intercept": fix_int + int_offset,
                "slope": fix_slope + slope_offset,
                "intercept_offset": int_offset,
                "slope_offset": slope_offset,
            })
        blup_df = pd.DataFrame(blup_rows).sort_values("subject_id")

        # GMM on (intercept, slope) BLUPs.
        gmm_results: List[Dict[str, Any]] = []
        gmm_features = blup_df[["intercept", "slope"]].values
        best = None
        best_bic = np.inf
        best_k = 1
        for k in self.k_grid:
            if k < 1 or k > blup_df.shape[0]:
                continue
            try:
                g = GaussianMixture(
                    n_components=k, covariance_type="full",
                    random_state=self.random_state, n_init=3,
                    max_iter=200,
                )
                g.fit(gmm_features)
                bic = float(g.bic(gmm_features))
                gmm_results.append({"k": int(k), "bic": bic, "aic": float(g.aic(gmm_features))})
                if bic < best_bic:
                    best_bic = bic
                    best_k = int(k)
                    best = g
            except Exception as e:
                gmm_results.append({"k": int(k), "bic": float("inf"),
                                     "error": str(e)})

        if best is not None:
            labels = best.predict(gmm_features)
            proba = best.predict_proba(gmm_features)
            class_df = blup_df.copy()
            class_df["trajectory_class"] = labels.astype(int)
            class_df["entropy"] = -np.sum(
                proba * np.log(proba + 1e-12), axis=1) / np.log(max(best_k, 2))
            # Class summary: % subjects, mean intercept, mean slope.
            class_summary = (class_df.groupby("trajectory_class")
                             .agg(n=("subject_id", "count"),
                                  mean_intercept=("intercept", "mean"),
                                  mean_slope=("slope", "mean"))
                             .reset_index())
        else:
            class_df = blup_df.copy()
            class_df["trajectory_class"] = 0
            class_summary = pd.DataFrame()

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        fe_path = out_dir / CONTRACT.output_files["fixed_effects_csv"]
        re_path = out_dir / CONTRACT.output_files["random_effects_csv"]
        bl_path = out_dir / CONTRACT.output_files["subject_blups_csv"]
        tc_path = out_dir / CONTRACT.output_files["trajectory_classes_csv"]
        sm_path = out_dir / CONTRACT.output_files["summary_json"]
        fe_df.to_csv(fe_path, index=False)
        re_df.to_csv(re_path, index=False)
        blup_df.to_csv(bl_path, index=False)
        class_df.to_csv(tc_path, index=False)

        summary = {
            "n_subjects": int(n_subj),
            "n_observations": int(len(sub)),
            "min_obs_per_subject": int(self.min_obs_per_subject),
            "mean_intercept": fix_int,
            "mean_slope": fix_slope,
            "var_intercept": var_intercept,
            "var_slope": var_slope,
            "cov_intercept_slope": cov_int_slope,
            "var_residual": var_resid,
            "icc_intercept": float(icc) if not np.isnan(icc) else None,
            "gmm_bic_path": gmm_results,
            "best_k_by_bic": int(best_k),
            "class_summary": class_summary.to_dict(orient="records")
                if not class_summary.empty else [],
            "converged": bool(getattr(fit, "converged", True)),
        }
        sm_path.write_text(json.dumps(summary, indent=2, default=str),
                            encoding="utf-8")

        return {
            "fixed_effects_csv": str(fe_path),
            "random_effects_csv": str(re_path),
            "subject_blups_csv": str(bl_path),
            "trajectory_classes_csv": str(tc_path),
            "summary_json": str(sm_path),
            **summary,
        }


def get_solver(gmm_k_grid: Optional[List[int]] = None,
               min_obs_per_subject: int = 3,
               random_state: int = 42) -> LatentGrowthCurveSolver:
    return LatentGrowthCurveSolver(
        gmm_k_grid=gmm_k_grid,
        min_obs_per_subject=min_obs_per_subject,
        random_state=random_state,
    )


def selftest() -> Dict[str, Any]:
    """Ground-truth: 200 subjects, 5 timepoints, planted true
    intercept-mean=10, slope-mean=-1, var(int)=4, var(slope)=0.25,
    resid var=1.  Verify recovered fixed effects within tol."""
    import tempfile
    rng = np.random.default_rng(42)
    N = 200
    T = 5
    true_int_mu, true_int_sd = 10.0, 2.0
    true_slope_mu, true_slope_sd = -1.0, 0.5
    true_resid_sd = 1.0
    rows = []
    for sid in range(N):
        a_i = rng.normal(true_int_mu, true_int_sd)
        b_i = rng.normal(true_slope_mu, true_slope_sd)
        for t in range(T):
            y = a_i + b_i * t + rng.normal(0, true_resid_sd)
            rows.append({"id": f"S{sid:04d}", "time": t, "y": y})
    df = pd.DataFrame(rows)

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(gmm_k_grid=[1, 2]).run(
            df, ColumnMapping({"id_col": "id", "time_col": "time",
                                "y_col": "y"}),
            Path(tmp))
        if abs(out["mean_intercept"] - true_int_mu) > 0.5:
            diffs.append(f"mean_intercept={out['mean_intercept']:.3f} far from "
                         f"{true_int_mu}")
        if abs(out["mean_slope"] - true_slope_mu) > 0.15:
            diffs.append(f"mean_slope={out['mean_slope']:.3f} far from "
                         f"{true_slope_mu}")
        if abs(out["var_intercept"] - true_int_sd ** 2) > 2.0:
            diffs.append(f"var_int={out['var_intercept']:.3f} far from "
                         f"{true_int_sd ** 2}")
        if abs(out["var_slope"] - true_slope_sd ** 2) > 0.20:
            diffs.append(f"var_slope={out['var_slope']:.3f} far from "
                         f"{true_slope_sd ** 2}")
        if abs(out["var_residual"] - true_resid_sd ** 2) > 0.30:
            diffs.append(f"var_resid={out['var_residual']:.3f} far from "
                         f"{true_resid_sd ** 2}")

    return {
        "ok": len(diffs) == 0,
        "summary": ("LGCM recovers true fixed + random effects within tol"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["latent_growth_curve"]},
    }
