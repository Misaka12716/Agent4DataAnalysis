"""Ordinary least squares linear regression (V8 Phase 3 §P0-3).

Wraps statsmodels OLS so the planner has a real "continuous outcome
regression" tool (complementing logistic_regression for binary
targets and g_formula_tmle for binary-outcome causal ATE).

中文：连续结局的回归算子，statsmodels.OLS 的薄封装。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError
from ._numeric_utils import coerce_to_numeric


LINEAR_REGRESSION_CONTRACT = SolverContract(
    name="linear_regression",
    capability="F07_classical_modeling",
    description=(
        "Ordinary least squares (OLS) regression for a continuous "
        "outcome on one or more numeric features (+ optional covariates). "
        "Returns coefficient table with 95%CI / p-values, R² / adj-R² / "
        "RMSE, and residual summary.  Supports HC0..HC3 robust SE.  "
        "Use for continuous y; for binary y use logistic_regression."
    ),
    roles={
        "outcome_col":   RoleSpec(Role.NUMERIC_TARGET,
                                    "continuous outcome y"),
        "feature_cols":  RoleSpec(Role.NUMERIC_LIST,
                                    "predictor columns"),
        "covariate_cols": RoleSpec(Role.NUMERIC_LIST,
                                     "additional adjustment covariates",
                                     optional=True),
    },
    static_params={
        "add_intercept": True,
        "robust_se": None,      # None | "HC0" | "HC1" | "HC2" | "HC3"
        "standardize_features": False,
    },
    output_files={
        "coef_csv": "linear_regression_coef.csv",
        "fit_json": "linear_regression_fit.json",
    },
    output_kind={"coef_csv": "s", "fit_json": "s"},
)


class LinearRegressionSolver:
    contract = LINEAR_REGRESSION_CONTRACT

    def __init__(self, add_intercept: bool = True,
                  robust_se: Optional[str] = None,
                  standardize_features: bool = False) -> None:
        self.add_intercept = bool(add_intercept)
        self.robust_se = robust_se
        self.standardize_features = bool(standardize_features)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        import statsmodels.api as sm

        y_col = mapping.get("outcome_col")
        x_cols = list(mapping.get("feature_cols") or [])
        cov_cols = list(mapping.get("covariate_cols") or [])

        if not y_col or y_col not in df.columns:
            raise OperatorInputError(
                "COLUMN_NOT_FOUND", solver="linear_regression",
                col=y_col, available=list(df.columns)[:20],
            )
        # Allow auto-coerce on y / x columns (commas / % / currency
        # stripping) so "37,410" or "15.17%" columns work transparently.
        df = df.copy()
        if not pd.api.types.is_numeric_dtype(df[y_col]):
            coerced, ok, rate = coerce_to_numeric(df[y_col])
            if ok:
                df[y_col] = coerced
            else:
                raise OperatorInputError(
                    "COLUMN_NOT_COERCIBLE", solver="linear_regression",
                    col=y_col, dtype=str(df[y_col].dtype),
                    coerce_rate=f"{rate:.0%}",
                )

        all_x = []
        skipped: List[str] = []
        for c in x_cols + cov_cols:
            if c not in df.columns:
                skipped.append(c)
                continue
            if pd.api.types.is_numeric_dtype(df[c]):
                all_x.append(c)
            else:
                coerced, ok, _ = coerce_to_numeric(df[c])
                if ok:
                    df[c] = coerced
                    all_x.append(c)
                else:
                    skipped.append(c)
        if not all_x:
            raise OperatorInputError(
                "NO_NUMERIC_COLUMNS", solver="linear_regression",
            )
        # Pairwise dedup keeping order.
        seen = set()
        feature_order = []
        for c in all_x:
            if c not in seen:
                seen.add(c)
                feature_order.append(c)

        sub = df[[y_col] + feature_order].dropna().copy()
        required_n = max(2, len(feature_order) + (1 if self.add_intercept else 0) + 1)
        if len(sub) < required_n:
            # P3-fix: distinguish "not enough rows for OLS" (data
            # quantity issue → coder might melt/restructure) from
            # "no numeric columns at all" (data quality issue → coder
            # should clean strings).
            raise OperatorInputError(
                "INSUFFICIENT_SAMPLES", solver="linear_regression",
                required_n=required_n, actual_n=len(sub),
            )

        y = sub[y_col].astype(float).values
        X_raw = sub[feature_order].astype(float).copy()
        if self.standardize_features:
            mu = X_raw.mean()
            sd = X_raw.std(ddof=0).replace(0, 1.0)
            X_raw = (X_raw - mu) / sd
        X = X_raw.values
        if self.add_intercept:
            X = np.column_stack([np.ones(len(X)), X])
            names = ["const"] + feature_order
        else:
            names = list(feature_order)

        if self.robust_se in {"HC0", "HC1", "HC2", "HC3"}:
            res = sm.OLS(y, X).fit(cov_type=self.robust_se)
        else:
            res = sm.OLS(y, X).fit()

        ci = res.conf_int(alpha=0.05)
        coef_rows = []
        for i, nm in enumerate(names):
            coef_rows.append({
                "feature":  nm,
                "coef":     float(res.params[i]),
                "std_err":  float(res.bse[i]),
                "t":        float(res.tvalues[i]),
                "p_value":  float(res.pvalues[i]),
                "ci_low":   float(ci[i, 0]),
                "ci_high":  float(ci[i, 1]),
            })
        coef_df = pd.DataFrame(coef_rows)
        coef_path = Path(output_dir) / LINEAR_REGRESSION_CONTRACT.output_files["coef_csv"]
        coef_df.to_csv(coef_path, index=False)

        rmse = float(np.sqrt(np.mean(res.resid ** 2)))
        fit = {
            "n_obs":      int(res.nobs),
            "n_features": int(len(feature_order)),
            "r_squared":  float(res.rsquared),
            "adj_r_squared": float(res.rsquared_adj),
            "rmse":        rmse,
            "f_statistic": float(res.fvalue) if res.fvalue is not None else None,
            "f_p_value":   float(res.f_pvalue) if res.f_pvalue is not None else None,
            "aic":         float(res.aic),
            "bic":         float(res.bic),
            "residual_mean": float(res.resid.mean()),
            "residual_std":  float(res.resid.std(ddof=1)),
            "skipped_features": skipped,
            "robust_se":  self.robust_se,
        }
        fit_path = Path(output_dir) / LINEAR_REGRESSION_CONTRACT.output_files["fit_json"]
        fit_path.write_text(json.dumps(fit, indent=2, default=str),
                              encoding="utf-8")

        return {"coef_csv": str(coef_path),
                "fit_json": str(fit_path),
                **fit}


def get_solver(add_intercept: bool = True,
                robust_se: Optional[str] = None,
                standardize_features: bool = False
                ) -> LinearRegressionSolver:
    return LinearRegressionSolver(
        add_intercept=add_intercept,
        robust_se=robust_se,
        standardize_features=standardize_features,
    )


def selftest() -> Dict[str, Any]:
    """Ground truth via numpy least-squares closed form.

    Fixture (seed=42):
        y = 2 + 1.5*x1 - 0.5*x2 + N(0, 0.3)
        n = 500

    Compare solver coefficients & R² with the analytic solution
    β = (X'X)^-1 X'y.
    """
    import tempfile
    rng = np.random.default_rng(42)
    n = 500
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    y = 2.0 + 1.5 * x1 - 0.5 * x2 + rng.normal(0, 0.3, n)
    df = pd.DataFrame({"y": y, "x1": x1, "x2": x2})

    # Analytic OLS via numpy
    X = np.column_stack([np.ones(n), x1, x2])
    beta_ref = np.linalg.lstsq(X, y, rcond=None)[0]
    y_pred = X @ beta_ref
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2_ref = 1.0 - ss_res / ss_tot

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        s = get_solver()
        out = s.run(df=df, mapping=ColumnMapping({
            "outcome_col": "y",
            "feature_cols": ["x1", "x2"],
        }), output_dir=Path(tmp))
        coef = pd.read_csv(out["coef_csv"]).set_index("feature")["coef"]
        for nm, ref in zip(["const", "x1", "x2"], beta_ref):
            got = float(coef.loc[nm])
            if abs(got - float(ref)) > 1e-9:
                diffs.append(f"coef[{nm}]: {got} vs {ref}")
        if abs(out["r_squared"] - r2_ref) > 1e-9:
            diffs.append(f"r2: {out['r_squared']} vs {r2_ref}")
    return {"ok": not diffs,
            "summary": ("linear_regression coefficients/R² match closed-form "
                          "least-squares" if not diffs
                          else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs}}


__all__ = ["LINEAR_REGRESSION_CONTRACT", "LinearRegressionSolver",
            "get_solver", "selftest"]
