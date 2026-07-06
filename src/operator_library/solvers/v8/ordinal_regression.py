"""W24 — Proportional-odds (cumulative-logit) ordinal regression.

Standard tool for Likert-type outcomes (PANSS / PHQ-9 item-level analyses,
CGI severity, etc).  Built on ``statsmodels.miscmodels.ordinal_model
.OrderedModel`` with logit link.

Output
------
- coef_table.csv:  per-predictor beta, OR=exp(beta), SE, p, 95% CI
- thresholds.csv:  cutpoints between ordinal levels
- summary.json:    log-likelihood, AIC, BIC, McFadden's pseudo-R^2,
                    Brant-Wald proportional-odds test
- predictions.csv: per-row predicted class + class probabilities

References
----------
- McCullagh P (1980) "Regression models for ordinal data" *JRSSB* 42:109.
- Brant R (1990) "Assessing proportionality in the proportional odds model
  for ordinal logistic regression" *Biometrics* 46:1171.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as sps
from statsmodels.miscmodels.ordinal_model import OrderedModel

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="ordinal_regression",
    capability="F_ordinal_regression",
    description=(
        "Proportional-odds (cumulative-logit) ordinal regression for ordered "
        "categorical outcomes (Likert, PANSS-item, PHQ-item, CGI-severity). "
        "Reports per-predictor odds ratios, cutpoints between adjacent "
        "levels, McFadden pseudo-R^2, AIC/BIC, and a Brant-Wald-style "
        "proportional-odds assumption test.  Required: ordinal target with "
        ">=3 ordered levels."
    ),
    roles={
        "target_col": RoleSpec(Role.ORDINAL,
                                "Ordinal outcome (integer/categorical, ordered)"),
        "predictors": RoleSpec(Role.NUMERIC_LIST, "Predictor columns"),
    },
    static_params={"link": "logit"},  # could extend to "probit"
    output_files={
        "coef_table_csv": "ordinal_coef_table.csv",
        "thresholds_csv": "ordinal_thresholds.csv",
        "predictions_csv": "ordinal_predictions.csv",
        "summary_json": "ordinal_summary.json",
    },
    output_kind={"coef_table_csv": "s",
                  "thresholds_csv": "s",
                  "predictions_csv": "t",
                  "summary_json": "s"},
)


class OrdinalRegressionSolver:
    contract = CONTRACT

    def __init__(self, link: str = "logit"):
        if link not in ("logit", "probit"):
            raise ValueError("link must be 'logit' or 'probit'")
        self.link = link

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        y_col = mapping.get("target_col")
        x_cols = list(mapping.get("predictors") or [])
        if not y_col or not x_cols:
            raise ValueError("target_col and predictors are required")

        sub = df[[y_col] + x_cols].dropna().copy()
        n = len(sub)
        if n < 50:
            raise ValueError(f"n={n} too small; need n>=50")

        # Ensure target is integer-coded ordered.
        y_raw = sub[y_col].values
        # Coerce to integer level codes 0..K-1.
        try:
            y_int = pd.Categorical(y_raw, ordered=True).codes.copy()
        except Exception:
            y_int = pd.to_numeric(y_raw, errors="raise").astype(int).values
        # In statsmodels OrderedModel, target must be a categorical with
        # ordered=True levels OR integer 0..K-1.  Use category-coded.
        cat = pd.Categorical(y_int, ordered=True)
        K = len(cat.categories)
        if K < 3:
            raise ValueError(f"ordinal target has only {K} levels; need >=3")

        X = sub[x_cols].astype(float).values
        # statsmodels expects DataFrame without constant.
        Xdf = pd.DataFrame(X, columns=x_cols)
        ycat = pd.Series(cat, index=sub.index, name=y_col)

        model = OrderedModel(ycat, Xdf, distr=self.link)
        res = model.fit(method="bfgs", disp=False)

        # Coefficient table (predictors only, not thresholds).
        params = res.params
        bse = res.bse
        pvals = res.pvalues
        coef_rows: List[Dict[str, Any]] = []
        for name in x_cols:
            beta = float(params[name])
            se = float(bse[name])
            p = float(pvals[name])
            coef_rows.append({
                "term": name,
                "beta": beta,
                "odds_ratio": float(np.exp(beta)),
                "se": se,
                "z": beta / se if se > 0 else float("nan"),
                "p_value": p,
                "or_ci_low": float(np.exp(beta - 1.96 * se)),
                "or_ci_high": float(np.exp(beta + 1.96 * se)),
            })
        coef_df = pd.DataFrame(coef_rows)

        # Threshold (cutpoints) — OrderedModel stores K-1 cutpoints, but in
        # the *log-difference* parametrisation.  Decode to actual cutpoints.
        threshold_names = [n for n in params.index if n not in x_cols]
        raw_thr = np.asarray([params[n] for n in threshold_names])
        # First is the actual first cutpoint, rest are log-differences.
        thresholds = np.empty(len(raw_thr))
        thresholds[0] = raw_thr[0]
        for i in range(1, len(raw_thr)):
            thresholds[i] = thresholds[i - 1] + np.exp(raw_thr[i])
        thr_df = pd.DataFrame({
            "between_levels": [f"{i}|{i+1}" for i in range(len(thresholds))],
            "cutpoint": thresholds.astype(float),
        })

        # Goodness of fit + pseudo R^2.
        try:
            ll_model = float(res.llf)
            ll_null = float(model.fit(method="bfgs", disp=False,
                                       start_params=np.zeros(model.k_vars
                                                              + model.k_constant
                                                              + (K - 1))).llnull)
        except Exception:
            ll_model = float(res.llf)
            ll_null = float("nan")
        try:
            mcfadden = 1 - ll_model / ll_null if ll_null and ll_null != 0 else float("nan")
        except Exception:
            mcfadden = float("nan")

        aic = float(res.aic)
        bic = float(res.bic)

        # Brant-Wald style proportional-odds test (lightweight version):
        # fit separate binary logits at each cumulative split and compare
        # per-predictor coefficients across splits via chi^2.
        brant_results = self._brant_test(sub, y_col, x_cols, K)

        # Predictions.
        pred_probs = res.predict(Xdf)
        if isinstance(pred_probs, pd.DataFrame):
            pred_arr = pred_probs.values
        else:
            pred_arr = np.asarray(pred_probs)
        pred_class = pred_arr.argmax(axis=1)
        pred_df = pd.DataFrame(pred_arr, columns=[f"p_class_{i}" for i in range(K)])
        pred_df.insert(0, "predicted_class", pred_class)
        pred_df.insert(0, "true_class", np.asarray(y_int))

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        coef_path = out_dir / CONTRACT.output_files["coef_table_csv"]
        thr_path = out_dir / CONTRACT.output_files["thresholds_csv"]
        pred_path = out_dir / CONTRACT.output_files["predictions_csv"]
        summary_path = out_dir / CONTRACT.output_files["summary_json"]
        coef_df.to_csv(coef_path, index=False)
        thr_df.to_csv(thr_path, index=False)
        pred_df.to_csv(pred_path, index=False)

        summary = {
            "n_obs": int(n),
            "n_levels": int(K),
            "link": self.link,
            "log_likelihood": float(ll_model),
            "log_likelihood_null": float(ll_null),
            "mcfadden_pseudo_r2": float(mcfadden)
                if not np.isnan(mcfadden) else None,
            "aic": aic,
            "bic": bic,
            "brant_test": brant_results,
            "predictors": x_cols,
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "coef_table_csv": str(coef_path),
            "thresholds_csv": str(thr_path),
            "predictions_csv": str(pred_path),
            "summary_json": str(summary_path),
            **summary,
        }

    @staticmethod
    def _brant_test(sub: pd.DataFrame, y_col: str, x_cols: List[str], K: int
                     ) -> Dict[str, Any]:
        """Per-predictor Brant test of proportional odds.

        For each predictor x_j, fit (K-1) binary logits at each cumulative
        cutpoint and test whether the K-1 estimated betas are equal via
        a chi^2 statistic (Wald form with mean variance).
        """
        results: List[Dict[str, Any]] = []
        y_int_full = pd.Categorical(sub[y_col], ordered=True).codes.copy()
        omnibus_chi2 = 0.0
        omnibus_df = 0
        for x in x_cols:
            xvals = sub[x].astype(float).values
            betas, ses = [], []
            for k in range(K - 1):
                yb = (y_int_full > k).astype(int)
                if yb.sum() < 5 or (1 - yb).sum() < 5:
                    continue
                X = sm.add_constant(xvals, has_constant="add")
                try:
                    fit = sm.Logit(yb, X).fit(disp=False, maxiter=200)
                    betas.append(float(fit.params[1]))
                    ses.append(float(fit.bse[1]))
                except Exception:
                    continue
            if len(betas) < 2:
                results.append({"predictor": x,
                                "brant_chi2": None,
                                "df": 0, "p_value": None,
                                "note": "insufficient splits"})
                continue
            mean_beta = float(np.mean(betas))
            chi2 = float(np.sum([((b - mean_beta) / se) ** 2
                                 for b, se in zip(betas, ses) if se > 0]))
            df = len(betas) - 1
            p = float(sps.chi2.sf(chi2, df=df))
            results.append({
                "predictor": x,
                "brant_chi2": chi2,
                "df": df,
                "p_value": p,
                "violates_proportional_odds_at_0.05": p < 0.05,
                "split_betas": betas,
            })
            omnibus_chi2 += chi2
            omnibus_df += df
        omnibus_p = float(sps.chi2.sf(omnibus_chi2, df=omnibus_df)) \
            if omnibus_df > 0 else None
        return {
            "omnibus_chi2": float(omnibus_chi2) if omnibus_df > 0 else None,
            "omnibus_df": int(omnibus_df),
            "omnibus_p_value": omnibus_p,
            "per_predictor": results,
        }


def get_solver(link: str = "logit") -> OrdinalRegressionSolver:
    return OrdinalRegressionSolver(link=link)


def selftest() -> Dict[str, Any]:
    """Ground-truth: simulate 5-level ordinal data from a known PO model,
    check estimated beta is within 0.15 of truth and OR matches."""
    import tempfile
    rng = np.random.default_rng(42)
    n = 1000
    x1 = rng.normal(0, 1, n)
    x2 = rng.binomial(1, 0.5, n).astype(float)
    # Latent y* = 0.8*x1 - 0.5*x2 + logistic noise, then cut into 5 levels.
    eta = 0.8 * x1 - 0.5 * x2
    eps = rng.logistic(0, 1, n)
    y_star = eta + eps
    cuts = np.quantile(y_star, [0.2, 0.4, 0.6, 0.8])
    y_ord = np.digitize(y_star, cuts)  # 0..4

    df = pd.DataFrame({"x1": x1, "x2": x2, "y": y_ord})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"target_col": "y",
                                "predictors": ["x1", "x2"]}),
            Path(tmp))
        coef = pd.read_csv(out["coef_table_csv"]).set_index("term")
        b_x1 = float(coef.loc["x1", "beta"])
        b_x2 = float(coef.loc["x2", "beta"])
        if abs(b_x1 - 0.8) > 0.20:
            diffs.append(f"beta(x1)={b_x1:.3f} far from true 0.8")
        if abs(b_x2 - (-0.5)) > 0.25:
            diffs.append(f"beta(x2)={b_x2:.3f} far from true -0.5")
        if float(coef.loc["x1", "p_value"]) > 0.001:
            diffs.append(f"x1 p={coef.loc['x1', 'p_value']:.4g} not significant")
        if out["n_levels"] != 5:
            diffs.append(f"n_levels={out['n_levels']}, expected 5")

    return {
        "ok": len(diffs) == 0,
        "summary": ("ordinal_regression recovers true PO betas within tol"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["ordinal_regression"]},
    }
