"""W22 — Polygenic Risk Score × Environment interaction (linear or logistic).

Tests whether the effect of a polygenic risk score on outcome is modified by
an environmental exposure (e.g. childhood trauma, stress, drug treatment).

Implements the standard GxE regression model:

    y = b0 + b1·PRS + b2·E + b3·(PRS·E) + sum(bk·Z_k) + epsilon

with z-scored PRS by default (so b1 is the per-SD effect at E=0 and b3 is the
per-SD-per-unit-E interaction effect).  Reports main effects, interaction
beta + Wald p-value, simple slopes at E=mean-1SD and E=mean+1SD, and the
likelihood-ratio test against the no-interaction nested model.

References
----------
- Aiken & West (1991) *Multiple Regression: Testing and Interpreting
  Interactions*  — simple-slopes formulas (chapter 2-3).
- Keller MC (2014) "Gene × environment interaction studies have not properly
  controlled for potential confounders" *Biol Psychiatry* — z-score + cov adj.
- Choi SW & O'Reilly PF (2019) "PRSice-2" *GigaScience* — PRS GxE convention.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as sps

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract

CONTRACT = SolverContract(
    name="prs_x_env_interaction",
    capability="F_prs_x_env",
    description=(
        "PRS x environment interaction regression: y ~ PRS + E + PRS*E "
        "(+ covariates).  Reports main + interaction coefs, Wald p, LR "
        "test vs nested model, simple slopes at E mean±1SD.  Set "
        "outcome_type='linear' for continuous y, 'logistic' for 0/1 y."
    ),
    roles={
        "prs_col": RoleSpec(Role.NUMERIC, "PRS column (continuous)"),
        "env_col": RoleSpec(Role.NUMERIC, "Environment / exposure column"),
        "outcome_col": RoleSpec(Role.NUMERIC, "Outcome column (continuous OR 0/1)"),
        "covariates": RoleSpec(
            Role.NUMERIC_LIST, "Covariates (e.g. age, sex, PCs)", optional=True
        ),
    },
    static_params={
        "outcome_type": "linear",   # "linear" or "logistic"
        "standardize_prs": True,
        "center_env": True,
        "random_state": 42,
    },
    output_files={
        "coef_table_csv": "prs_x_env_coef_table.csv",
        "simple_slopes_csv": "prs_x_env_simple_slopes.csv",
        "summary_json": "prs_x_env_summary.json",
    },
    output_kind={
        "coef_table_csv": "s",
        "simple_slopes_csv": "s",
        "summary_json": "s",
    },
)


class PrsEnvInteractionSolver:
    contract = CONTRACT

    def __init__(self, outcome_type: str = "linear",
                 standardize_prs: bool = True,
                 center_env: bool = True,
                 random_state: int = 42):
        if outcome_type not in ("linear", "logistic"):
            raise ValueError("outcome_type must be 'linear' or 'logistic'")
        self.outcome_type = outcome_type
        self.standardize_prs = standardize_prs
        self.center_env = center_env
        self.random_state = random_state

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        prs_col = mapping.get("prs_col")
        env_col = mapping.get("env_col")
        y_col = mapping.get("outcome_col")
        cov_cols = list(mapping.get("covariates") or [])

        if not all([prs_col, env_col, y_col]):
            raise ValueError("prs_col, env_col, outcome_col are required")

        sub = df[[prs_col, env_col, y_col] + cov_cols].dropna().copy()
        n = len(sub)
        if n < 30:
            raise ValueError(f"n={n} too small; need n>=30")

        # Standardise PRS / center env (Keller 2014 recommendation).
        prs = sub[prs_col].astype(float).values
        env = sub[env_col].astype(float).values
        if self.standardize_prs:
            prs = (prs - prs.mean()) / prs.std(ddof=1)
        if self.center_env:
            env_mean = env.mean()
            env_centered = env - env_mean
        else:
            env_mean = 0.0
            env_centered = env

        y = sub[y_col].astype(float).values

        # Design matrices: full vs nested.
        full_X = np.column_stack([prs, env_centered, prs * env_centered])
        nested_X = np.column_stack([prs, env_centered])
        cov_X = sub[cov_cols].astype(float).values if cov_cols else np.zeros((n, 0))
        if cov_X.shape[1]:
            full_X = np.column_stack([full_X, cov_X])
            nested_X = np.column_stack([nested_X, cov_X])

        full_X = sm.add_constant(full_X, has_constant="add")
        nested_X = sm.add_constant(nested_X, has_constant="add")

        col_names = ["const", "PRS", "E", "PRS_x_E"] + list(cov_cols)
        nested_names = ["const", "PRS", "E"] + list(cov_cols)

        if self.outcome_type == "linear":
            full_fit = sm.OLS(y, full_X).fit()
            nested_fit = sm.OLS(y, nested_X).fit()
        else:
            # logistic: tolerate y in {0,1} (cast to int)
            yb = (y > 0.5).astype(int)
            full_fit = sm.Logit(yb, full_X).fit(disp=False)
            nested_fit = sm.Logit(yb, nested_X).fit(disp=False)

        # LR test: 2*(LL_full - LL_nested) ~ chi2(df=1).
        lr_stat = 2.0 * (full_fit.llf - nested_fit.llf)
        lr_p = float(sps.chi2.sf(lr_stat, df=1))

        # Coefficient table.
        coef_rows: List[Dict[str, Any]] = []
        params = np.asarray(full_fit.params)
        bse = np.asarray(full_fit.bse)
        pvals = np.asarray(full_fit.pvalues)
        for i, name in enumerate(col_names):
            beta = float(params[i])
            se = float(bse[i])
            p = float(pvals[i])
            ci_low, ci_high = beta - 1.96 * se, beta + 1.96 * se
            coef_rows.append({
                "term": name,
                "beta": beta,
                "se": se,
                "z_or_t": beta / se if se > 0 else float("nan"),
                "p_value": p,
                "ci_low": float(ci_low),
                "ci_high": float(ci_high),
                "is_interaction": name == "PRS_x_E",
            })
        coef_df = pd.DataFrame(coef_rows)

        # Simple slopes: effect of PRS on y at E=mean-1SD and E=mean+1SD.
        e_sd = float(env.std(ddof=1))
        # Per the centered design, E_low = -1*sd and E_high = +1*sd relative to centered.
        # PRS slope at given E = b1 + b3 * E_centered.
        b1 = float(params[col_names.index("PRS")])
        b3 = float(params[col_names.index("PRS_x_E")])
        cov_mat = np.asarray(full_fit.cov_params())
        i_prs = col_names.index("PRS")
        i_int = col_names.index("PRS_x_E")
        var_b1 = float(cov_mat[i_prs, i_prs])
        var_b3 = float(cov_mat[i_int, i_int])
        cov_b1_b3 = float(cov_mat[i_prs, i_int])
        simple_rows = []
        for label, e_val in [("E_low(-1SD)", -e_sd), ("E_mean(0)", 0.0),
                             ("E_high(+1SD)", +e_sd)]:
            slope = b1 + b3 * e_val
            slope_var = var_b1 + (e_val ** 2) * var_b3 + 2 * e_val * cov_b1_b3
            slope_se = float(np.sqrt(max(slope_var, 0.0)))
            z = slope / slope_se if slope_se > 0 else float("nan")
            p = float(2 * (1 - sps.norm.cdf(abs(z)))) if slope_se > 0 else float("nan")
            simple_rows.append({
                "env_level": label,
                "env_value_centered": float(e_val),
                "env_value_raw": float(e_val + env_mean),
                "prs_slope": float(slope),
                "prs_slope_se": slope_se,
                "z": float(z) if not np.isnan(z) else None,
                "p_value": p,
                "ci_low": float(slope - 1.96 * slope_se),
                "ci_high": float(slope + 1.96 * slope_se),
            })
        slopes_df = pd.DataFrame(simple_rows)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        coef_path = out_dir / CONTRACT.output_files["coef_table_csv"]
        slopes_path = out_dir / CONTRACT.output_files["simple_slopes_csv"]
        summary_path = out_dir / CONTRACT.output_files["summary_json"]
        coef_df.to_csv(coef_path, index=False)
        slopes_df.to_csv(slopes_path, index=False)

        summary = {
            "n_obs": int(n),
            "outcome_type": self.outcome_type,
            "standardize_prs": bool(self.standardize_prs),
            "center_env": bool(self.center_env),
            "env_mean_raw": float(env.mean()),
            "env_sd_raw": e_sd,
            "interaction_beta": b3,
            "interaction_p_wald": float(pvals[col_names.index("PRS_x_E")]),
            "interaction_p_lrt": lr_p,
            "lr_stat": float(lr_stat),
            "covariates": cov_cols,
        }
        import json
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "coef_table_csv": str(coef_path),
            "simple_slopes_csv": str(slopes_path),
            "summary_json": str(summary_path),
            **summary,
        }


def get_solver(outcome_type: str = "linear",
               standardize_prs: bool = True,
               center_env: bool = True,
               random_state: int = 42) -> PrsEnvInteractionSolver:
    return PrsEnvInteractionSolver(
        outcome_type=outcome_type,
        standardize_prs=standardize_prs,
        center_env=center_env,
        random_state=random_state,
    )


def selftest() -> Dict[str, Any]:
    """Ground-truth test: planted interaction beta=0.5, recover within tol."""
    import tempfile
    rng = np.random.default_rng(42)
    n = 1500
    prs_raw = rng.normal(0, 1, n)
    # E ~ Bernoulli(0.4) and continuous variant.
    env_raw = rng.binomial(1, 0.4, n).astype(float)
    # True model: y = 0.1 + 0.2*PRS + 0.3*E + 0.5*PRS*E + eps.
    # PRS not yet standardised (already std=1); E centered=0/1 -> mean=0.4.
    true_b1, true_b2, true_b3 = 0.2, 0.3, 0.5
    e_c = env_raw - env_raw.mean()
    y_lin = 0.1 + true_b1 * prs_raw + true_b2 * e_c + true_b3 * prs_raw * e_c \
        + rng.normal(0, 0.6, n)

    df_lin = pd.DataFrame({"prs": prs_raw, "env": env_raw, "y": y_lin})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(outcome_type="linear").run(
            df_lin, ColumnMapping({"prs_col": "prs", "env_col": "env",
                                    "outcome_col": "y"}),
            Path(tmp))
        b3_est = out["interaction_beta"]
        if abs(b3_est - true_b3) > 0.15:
            diffs.append(f"linear interaction beta={b3_est:.3f} far from "
                         f"true {true_b3}")
        if out["interaction_p_lrt"] > 0.01:
            diffs.append(f"linear LRT p={out['interaction_p_lrt']:.4g} not sig "
                         "for strong interaction")

    # Logistic version.
    p = 1 / (1 + np.exp(-(0.0 + 0.4 * prs_raw + 0.5 * e_c + 0.8 * prs_raw * e_c)))
    yb = rng.binomial(1, p, n)
    df_log = pd.DataFrame({"prs": prs_raw, "env": env_raw, "y": yb})
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(outcome_type="logistic").run(
            df_log, ColumnMapping({"prs_col": "prs", "env_col": "env",
                                    "outcome_col": "y"}),
            Path(tmp))
        if abs(out["interaction_beta"] - 0.8) > 0.25:
            diffs.append(f"logistic interaction beta={out['interaction_beta']:.3f} "
                         "far from true 0.8")
        if out["interaction_p_lrt"] > 0.05:
            diffs.append(f"logistic LRT p={out['interaction_p_lrt']:.4g} not sig")

    return {
        "ok": len(diffs) == 0,
        "summary": ("prs_x_env recovers true interaction beta within tolerance"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["prs_x_env_interaction"]},
    }
