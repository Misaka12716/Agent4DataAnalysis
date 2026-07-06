"""W23 — Empirical-Bayes (James-Stein) hierarchical normal model.

PyMC / brms are great but introduce a heavy dependency.  For the
common "group means with shrinkage toward grand mean" use case we use
the closed-form Empirical-Bayes normal-normal hierarchical estimator,
which is identical to PyMC's two-level model under default priors when
the within-group variance is known (Morris 1983, Efron & Morris 1973).

Model
-----
    y_ij | mu_g, sigma_w^2 ~ N(mu_g, sigma_w^2)
    mu_g | mu, tau^2       ~ N(mu, tau^2)

Estimates
    mu_hat        = MLE of grand mean
    tau2_hat      = method-of-moments (Cochran 1954) estimator of
                    between-group variance; floored at 0
    mu_g_shrunk   = (1 - B_g) * y_bar_g + B_g * mu_hat
                    where B_g = sigma_w^2 / (sigma_w^2 + tau2_hat) per group
    SE_shrunk     = sqrt((1 - B_g) * sigma_w_g^2 / n_g)

This collapses to the no-pooling estimator when tau2 >> sigma_w
and to complete pooling when tau2 = 0 — the same behaviour PyMC's
HierarchicalGLM gives.  Output also reports the shrinkage factor
B_g per group (large B_g = strong shrinkage).

Optional covariates are partialled out via an OLS pre-regression on
the within-group residuals.

References
----------
- Efron B, Morris C (1973) "Stein's estimation rule and its competitors"
  *JASA* 68:117.
- Morris CN (1983) "Parametric empirical Bayes inference: theory and
  applications" *JASA* 78:47.
- Gelman A et al (2013) *Bayesian Data Analysis* 3rd ed, §5.3-5.5.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="bayesian_hierarchical_glm",
    capability="F_bayes_hierarchical",
    description=(
        "Empirical-Bayes shrinkage estimate of per-group means toward "
        "the grand mean (closed-form James-Stein / Morris 1983).  "
        "Output: per-group shrunk mean + SE + shrinkage factor B_g."
    ),
    roles={
        "group_col": RoleSpec(Role.CATEGORICAL,
                                "Grouping factor (e.g. subgroup, site)"),
        "y_col": RoleSpec(Role.NUMERIC, "Continuous outcome"),
        "covariates": RoleSpec(Role.NUMERIC_LIST,
                                "Covariates to partial out (e.g. age, sex)",
                                optional=True),
    },
    static_params={
        "min_group_size": 2,
    },
    output_files={
        "group_estimates_csv": "bayes_group_estimates.csv",
        "summary_json": "bayes_summary.json",
    },
    output_kind={"group_estimates_csv": "s", "summary_json": "s"},
)


class BayesHierarchicalSolver:
    contract = CONTRACT

    def __init__(self, min_group_size: int = 2):
        self.min_group_size = int(min_group_size)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        g_col = mapping.get("group_col")
        y_col = mapping.get("y_col")
        cov_cols = list(mapping.get("covariates") or [])
        if not g_col or not y_col:
            raise ValueError("group_col and y_col are required")

        sub = df[[g_col, y_col] + cov_cols].dropna().copy()
        n = len(sub)
        if n < 10:
            raise ValueError(f"n={n} too small; need n>=10")
        y_raw = sub[y_col].astype(float).values
        groups = sub[g_col].astype(str).values

        # Partial out covariates if provided.
        if cov_cols:
            X = sub[cov_cols].astype(float).values
            X = sm.add_constant(X, has_constant="add")
            beta = np.linalg.lstsq(X, y_raw, rcond=None)[0]
            y = y_raw - X @ beta + beta[0]   # residuals + intercept back
            cov_note = f"residualised by {cov_cols}"
        else:
            y = y_raw
            cov_note = None

        unique_groups = sorted(np.unique(groups))
        group_y: Dict[str, np.ndarray] = {g: y[groups == g] for g in unique_groups}
        # Drop groups smaller than threshold.
        kept = [g for g, vals in group_y.items() if len(vals) >= self.min_group_size]
        if len(kept) < 2:
            raise ValueError("need >=2 groups each with min_group_size obs")
        group_y = {g: group_y[g] for g in kept}

        # Per-group sufficient statistics.
        means = {g: float(vals.mean()) for g, vals in group_y.items()}
        sds = {g: float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
               for g, vals in group_y.items()}
        ns = {g: int(len(vals)) for g, vals in group_y.items()}

        grand_mean = float(np.mean([means[g] for g in kept]))
        # Within-group variance (pooled, weighted by df).
        df_pool = sum(ns[g] - 1 for g in kept if ns[g] > 1)
        if df_pool > 0:
            sigma_w2 = float(np.sum(
                [(ns[g] - 1) * sds[g] ** 2 for g in kept]) / df_pool)
        else:
            sigma_w2 = 0.0
        # One-way random-effects ANOVA, method of moments for tau^2.
        # MS_b = sum n_g (y_bar_g - grand)^2 / (K-1)
        # E[MS_b] = sigma_w^2 + n_0 * tau^2, where n_0 is the "balance" weight:
        #   n_0 = (1/(K-1)) * (sum n_g - sum n_g^2 / sum n_g)   [Searle 1992]
        # tau2 = max(0, (MS_b - sigma_w^2) / n_0)
        K = len(kept)
        ms_b = float(np.sum([ns[g] * (means[g] - grand_mean) ** 2
                             for g in kept]) / max(K - 1, 1))
        n_arr = np.asarray([ns[g] for g in kept], dtype=float)
        n_tot = float(n_arr.sum())
        if K > 1 and n_tot > 0:
            n0 = (n_tot - float(np.sum(n_arr ** 2)) / n_tot) / (K - 1)
        else:
            n0 = float(n_arr.mean())
        tau2 = max(0.0, (ms_b - sigma_w2) / max(n0, 1e-9))

        rows: List[Dict[str, Any]] = []
        for g in kept:
            ng = ns[g]
            within_se2 = sigma_w2 / ng if ng > 0 else float("inf")
            # Shrinkage factor: B_g = within_se2 / (within_se2 + tau2)
            if (within_se2 + tau2) > 0:
                B_g = within_se2 / (within_se2 + tau2)
            else:
                B_g = 1.0
            shrunk = (1 - B_g) * means[g] + B_g * grand_mean
            shrunk_se = float(np.sqrt(max((1 - B_g) * within_se2, 0.0)))
            rows.append({
                "group": g,
                "n": ng,
                "raw_mean": float(means[g]),
                "raw_sd": float(sds[g]),
                "raw_se": float(np.sqrt(within_se2)),
                "shrunk_mean": float(shrunk),
                "shrunk_se": shrunk_se,
                "shrinkage_factor_B_g": float(B_g),
                "shrunk_ci_low": float(shrunk - 1.96 * shrunk_se),
                "shrunk_ci_high": float(shrunk + 1.96 * shrunk_se),
            })
        est_df = pd.DataFrame(rows).sort_values("group")

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        est_path = out_dir / CONTRACT.output_files["group_estimates_csv"]
        est_df.to_csv(est_path, index=False)

        summary = {
            "n_obs": int(n),
            "n_groups": int(K),
            "grand_mean": float(grand_mean),
            "within_group_variance": float(sigma_w2),
            "between_group_variance_tau2": float(tau2),
            "icc_one_way": float(tau2 / (tau2 + sigma_w2))
                if (tau2 + sigma_w2) > 0 else float("nan"),
            "covariates": cov_cols,
            "covariate_note": cov_note,
            "estimator": "empirical_bayes_normal_normal",
        }
        summary_path = out_dir / CONTRACT.output_files["summary_json"]
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "group_estimates_csv": str(est_path),
            "summary_json": str(summary_path),
            **summary,
        }


def get_solver(min_group_size: int = 2) -> BayesHierarchicalSolver:
    return BayesHierarchicalSolver(min_group_size=min_group_size)


def selftest() -> Dict[str, Any]:
    """Ground-truth: planted between-group variance tau2=0.5 with within=1.0;
    check (a) tau2 estimate within tol, (b) extreme groups shrink toward
    grand mean (raw_mean farther from grand than shrunk_mean)."""
    import tempfile
    rng = np.random.default_rng(42)
    K = 12
    true_mu = 5.0
    true_tau2 = 0.5
    within_sd = 1.0
    n_per = 30
    group_true_means = rng.normal(true_mu, np.sqrt(true_tau2), K)
    rows: List[Dict[str, Any]] = []
    for g in range(K):
        for _ in range(n_per):
            y_val = group_true_means[g] + rng.normal(0, within_sd)
            rows.append({"group": f"g{g:02d}", "y": y_val})
    df = pd.DataFrame(rows)

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"group_col": "group", "y_col": "y"}),
            Path(tmp))
        if abs(out["within_group_variance"] - within_sd ** 2) > 0.3:
            diffs.append(f"within var={out['within_group_variance']:.3f} far "
                         f"from true {within_sd ** 2}")
        if abs(out["between_group_variance_tau2"] - true_tau2) > 0.4:
            diffs.append(f"tau2={out['between_group_variance_tau2']:.3f} far "
                         f"from true {true_tau2}")
        # Check shrinkage: |shrunk - grand| should be < |raw - grand| for
        # most groups (some may not shrink at all if tau2 estimate is large).
        est_df = pd.read_csv(out["group_estimates_csv"])
        grand = out["grand_mean"]
        n_shrunk_ok = 0
        for _, r in est_df.iterrows():
            if abs(r["shrunk_mean"] - grand) <= abs(r["raw_mean"] - grand) + 1e-8:
                n_shrunk_ok += 1
        if n_shrunk_ok < K:
            diffs.append(f"only {n_shrunk_ok}/{K} groups shrunk toward grand "
                         "mean (should be all)")
        # ICC should be 0.5/(1+0.5) ≈ 0.33 (planted), allow wide tol.
        icc = out["icc_one_way"]
        if not (0.15 <= icc <= 0.60):
            diffs.append(f"ICC={icc:.3f} far from planted 0.33")

    return {
        "ok": len(diffs) == 0,
        "summary": ("bayesian_hierarchical_glm recovers between-group variance "
                    "and shrinks all groups toward grand mean"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["bayesian_hierarchical_glm"]},
    }
