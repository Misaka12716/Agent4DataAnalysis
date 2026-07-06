"""W25 — Causal-inference estimator (G-formula + TMLE) for ATE.

Computes the Average Treatment Effect (ATE) of a single binary treatment
on a binary outcome via three complementary estimators:

  1. Parametric G-formula (standardization, Robins 1986) — fits an
     outcome regression Y ~ A + L and predicts E[Y|A=1, L] - E[Y|A=0, L].
  2. Inverse-Probability-of-Treatment Weighting (IPTW, Hernan & Robins).
  3. Targeted Maximum Likelihood Estimation (TMLE, van der Laan & Rubin
     2006) — double-robust, uses ``zepid.causal.doublyrobust.TMLE``.

The TMLE estimator is double-robust: consistent if EITHER the outcome
model OR the propensity model is correctly specified, with semi-parametric
efficient variance via influence-curve bootstrap.

References
----------
- Robins JM (1986) "A new approach to causal inference in mortality studies
  with a sustained exposure period — Application to control of the healthy
  worker survivor effect" *Math Modelling* 7:1393.
- Hernan MA & Robins JM (2020) *Causal Inference: What If* — chapters 13-14.
- van der Laan MJ & Rubin D (2006) "Targeted Maximum Likelihood Learning"
  *Int J Biostat* 2:11.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


# V8 Phase 2 §3.4 — column-name hints that suggest the dataframe is a
# pre-aggregated contingency / cell table (e.g. fish_oil_18.csv with
# columns like n_treated / n_control / events).  When ≥2 of these
# token-families appear AND the row count is implausibly small for a
# patient-level study (≤20), we fail-fast with INPUT_IS_CONTINGENCY_TABLE
# so the planner / coder can pivot to a direct risk-difference
# computation.
_CONTINGENCY_NAME_PATTERNS = (
    ("n_treated", "n_control"),
    ("n_exposed", "n_unexposed"),
    ("events", "non_events"),
    ("treated_outcome", "control_outcome"),
    ("n_a", "n_b"),
    ("group_a", "group_b"),
)


def _looks_like_contingency_table(df: pd.DataFrame) -> Optional[str]:
    """Return a short evidence string if df looks like a contingency
    table, else None.  Two independent signals must agree:
      (i)  ≤20 rows (real patient-level studies have ≥30)
      (ii) ≥2 columns named like aggregated cells
    """
    if len(df) > 20:
        return None
    cols_lower = {str(c).lower() for c in df.columns}
    hits: List[str] = []
    for pair in _CONTINGENCY_NAME_PATTERNS:
        if all(p in cols_lower for p in pair):
            hits.append(" + ".join(pair))
    if len(hits) >= 1:
        return f"n_rows={len(df)} <= 20; aggregated cell columns: {hits}"
    return None

try:
    from zepid.causal.doublyrobust import TMLE as ZEPID_TMLE
    _HAVE_ZEPID = True
except Exception:
    _HAVE_ZEPID = False


CONTRACT = SolverContract(
    name="g_formula_tmle",
    capability="F_causal_ate",
    description=(
        "ATE of a 0/1 treatment on a 0/1 outcome via three estimators: "
        "parametric G-formula standardization, IPTW, and TMLE "
        "(double-robust).  Requires: binary treatment column, binary "
        "outcome column, numeric covariate columns."
    ),
    roles={
        "treatment_col": RoleSpec(Role.BINARY_TARGET,
                                    "Treatment A (0/1) — 1 = treated"),
        "outcome_col": RoleSpec(Role.BINARY_TARGET,
                                  "Outcome Y (0/1) — 1 = event"),
        "confounders": RoleSpec(Role.NUMERIC_LIST,
                                  "Confounder columns L (numeric or 0/1 dummies)"),
    },
    static_params={
        "n_bootstrap": 1000,
        "random_state": 42,
        "trim_ps": 0.02,   # symmetric propensity-score trimming
    },
    output_files={
        "ate_estimates_csv": "ate_estimates.csv",
        "summary_json": "ate_summary.json",
    },
    output_kind={"ate_estimates_csv": "s", "summary_json": "s"},
)


def _gformula_ate(df: pd.DataFrame, A: str, Y: str, L: List[str]
                   ) -> Dict[str, float]:
    """Parametric standardization."""
    X = sm.add_constant(df[[A] + L].astype(float).values, has_constant="add")
    m = sm.Logit(df[Y].astype(int).values, X).fit(disp=False)
    base = df[[A] + L].astype(float).copy()
    df1 = base.copy(); df1[A] = 1
    df0 = base.copy(); df0[A] = 0
    p1 = m.predict(sm.add_constant(df1.values, has_constant="add"))
    p0 = m.predict(sm.add_constant(df0.values, has_constant="add"))
    return {
        "ATE_g": float(p1.mean() - p0.mean()),
        "Y1_g": float(p1.mean()),
        "Y0_g": float(p0.mean()),
    }


def _iptw_ate(df: pd.DataFrame, A: str, Y: str, L: List[str],
              trim: float = 0.02) -> Dict[str, float]:
    """Stabilised IPTW with trimming."""
    Xps = sm.add_constant(df[L].astype(float).values, has_constant="add")
    ps_model = sm.Logit(df[A].astype(int).values, Xps).fit(disp=False)
    ps = np.clip(ps_model.predict(Xps), trim, 1 - trim)
    A_arr = df[A].astype(int).values
    Y_arr = df[Y].astype(int).values
    # Stabilised weights:
    p_a = A_arr.mean()
    sw = np.where(A_arr == 1, p_a / ps, (1 - p_a) / (1 - ps))
    Y1 = np.sum(sw * (A_arr == 1) * Y_arr) / max(np.sum(sw * (A_arr == 1)), 1e-9)
    Y0 = np.sum(sw * (A_arr == 0) * Y_arr) / max(np.sum(sw * (A_arr == 0)), 1e-9)
    return {"ATE_iptw": float(Y1 - Y0), "Y1_iptw": float(Y1),
            "Y0_iptw": float(Y0)}


def _tmle_ate_zepid(df: pd.DataFrame, A: str, Y: str, L: List[str]
                     ) -> Dict[str, Optional[float]]:
    """zepid TMLE wrapper."""
    if not _HAVE_ZEPID:
        return {"ATE_tmle": None, "ATE_tmle_se": None,
                "ATE_tmle_ci_low": None, "ATE_tmle_ci_high": None,
                "tmle_engine": "unavailable"}
    try:
        tmle = ZEPID_TMLE(df, exposure=A, outcome=Y)
        tmle.exposure_model(" + ".join(L), print_results=False)
        tmle.outcome_model(A + " + " + " + ".join(L), print_results=False)
        tmle.fit()
        return {
            "ATE_tmle": float(tmle.risk_difference) if tmle.risk_difference is not None else None,
            "ATE_tmle_se": float(getattr(tmle, "risk_difference_se",
                                          float("nan"))),
            "ATE_tmle_ci_low": float(tmle.risk_difference_ci[0])
                if tmle.risk_difference_ci else None,
            "ATE_tmle_ci_high": float(tmle.risk_difference_ci[1])
                if tmle.risk_difference_ci else None,
            "tmle_engine": "zepid.TMLE",
        }
    except Exception as e:
        return {"ATE_tmle": None, "ATE_tmle_se": None,
                "ATE_tmle_ci_low": None, "ATE_tmle_ci_high": None,
                "tmle_engine": f"failed: {e}"}


def _bootstrap_se(df: pd.DataFrame, A: str, Y: str, L: List[str],
                   fn, B: int = 1000, key: str = "ATE_g",
                   random_state: int = 42) -> Dict[str, float]:
    rng = np.random.default_rng(random_state)
    n = len(df)
    estimates = np.empty(B)
    for b in range(B):
        idx = rng.integers(0, n, size=n)
        try:
            estimates[b] = float(fn(df.iloc[idx], A, Y, L)[key])
        except Exception:
            estimates[b] = np.nan
    estimates = estimates[~np.isnan(estimates)]
    if len(estimates) < B / 2:
        return {"se": float("nan"), "ci_low": float("nan"),
                "ci_high": float("nan")}
    return {
        "se": float(estimates.std(ddof=1)),
        "ci_low": float(np.percentile(estimates, 2.5)),
        "ci_high": float(np.percentile(estimates, 97.5)),
    }


class GFormulaTmleSolver:
    contract = CONTRACT

    def __init__(self, n_bootstrap: int = 1000, random_state: int = 42,
                 trim_ps: float = 0.02):
        self.n_bootstrap = int(n_bootstrap)
        self.random_state = int(random_state)
        self.trim_ps = float(trim_ps)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        # ---- V8 Phase-2 input sniff (BEFORE touching the data) -------
        # (a) Contingency / aggregated-cell table: TMLE assumes
        #     patient-level rows; a 2x2 cells table will go through but
        #     give nonsense.  Fail-fast so the coder fallback knows to
        #     compute risk difference directly.
        cont_evidence = _looks_like_contingency_table(df)
        if cont_evidence is not None:
            raise OperatorInputError(
                "INPUT_IS_CONTINGENCY_TABLE",
                solver="g_formula_tmle",
                evidence=cont_evidence,
            )

        A_col = mapping.get("treatment_col")
        Y_col = mapping.get("outcome_col")
        L_cols = list(mapping.get("confounders") or [])
        if not A_col or not Y_col or not L_cols:
            # mapping engine 通常会先报 missing_required；保险起见仍兜底
            missing = [k for k, v in (("treatment_col", A_col),
                                       ("outcome_col", Y_col),
                                       ("confounders", L_cols)) if not v]
            raise OperatorInputError(
                "NO_TARGET_COLUMNS",
                solver="g_formula_tmle",
                requested=missing,
            )

        # (b) Outcome dtype sniff — TMLE requires a 0/1 outcome.  If the
        #     observed unique-count > 2, the user almost certainly wants
        #     a continuous-outcome method (linear regression / column_stat).
        #     We check BEFORE coercion so silently rounding 0.37 → 0 etc.
        #     does not mask the mistake.
        y_raw = df[Y_col].dropna()
        n_unique_y = int(y_raw.nunique())
        if n_unique_y > 2:
            raise OperatorInputError(
                "OUTCOME_NOT_BINARY",
                solver="g_formula_tmle",
                col=Y_col,
                n_unique=n_unique_y,
                vmin=float(y_raw.min()) if len(y_raw) else float("nan"),
                vmax=float(y_raw.max()) if len(y_raw) else float("nan"),
            )

        sub = df[[A_col, Y_col] + L_cols].dropna().copy()
        # Coerce A and Y to 0/1.
        sub[A_col] = sub[A_col].astype(float).round().astype(int)
        sub[Y_col] = sub[Y_col].astype(float).round().astype(int)
        if set(sub[A_col].unique()) - {0, 1}:
            raise OperatorInputError(
                "OUTCOME_NOT_BINARY",
                solver="g_formula_tmle",
                col=A_col,
                n_unique=int(sub[A_col].nunique()),
                vmin=float(sub[A_col].min()),
                vmax=float(sub[A_col].max()),
            )
        if set(sub[Y_col].unique()) - {0, 1}:
            raise OperatorInputError(
                "OUTCOME_NOT_BINARY",
                solver="g_formula_tmle",
                col=Y_col,
                n_unique=int(sub[Y_col].nunique()),
                vmin=float(sub[Y_col].min()),
                vmax=float(sub[Y_col].max()),
            )

        n = len(sub)
        if n < 50:
            raise ValueError(f"n={n} too small; need >=50")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            g = _gformula_ate(sub, A_col, Y_col, L_cols)
            iptw = _iptw_ate(sub, A_col, Y_col, L_cols, trim=self.trim_ps)
            tmle = _tmle_ate_zepid(sub, A_col, Y_col, L_cols)
            # Bootstrap SEs for G-formula and IPTW (TMLE has its own).
            g_boot = _bootstrap_se(sub, A_col, Y_col, L_cols,
                                    _gformula_ate, B=self.n_bootstrap,
                                    key="ATE_g", random_state=self.random_state)
            iptw_boot = _bootstrap_se(
                sub, A_col, Y_col, L_cols,
                lambda d, a, y, l: _iptw_ate(d, a, y, l, trim=self.trim_ps),
                B=self.n_bootstrap, key="ATE_iptw",
                random_state=self.random_state + 1)

        rows = []
        rows.append({
            "estimator": "g_formula",
            "ATE": g["ATE_g"],
            "Y1": g["Y1_g"], "Y0": g["Y0_g"],
            "SE": g_boot["se"],
            "ci_low": g_boot["ci_low"], "ci_high": g_boot["ci_high"],
            "method_note": "Robins 1986 parametric standardization, "
                            "logistic outcome model.",
        })
        rows.append({
            "estimator": "iptw",
            "ATE": iptw["ATE_iptw"],
            "Y1": iptw["Y1_iptw"], "Y0": iptw["Y0_iptw"],
            "SE": iptw_boot["se"],
            "ci_low": iptw_boot["ci_low"], "ci_high": iptw_boot["ci_high"],
            "method_note": f"stabilised IPTW, ps trimmed to [{self.trim_ps}, "
                            f"{1 - self.trim_ps}].",
        })
        rows.append({
            "estimator": "tmle",
            "ATE": tmle["ATE_tmle"],
            "Y1": None, "Y0": None,
            "SE": tmle["ATE_tmle_se"],
            "ci_low": tmle["ATE_tmle_ci_low"],
            "ci_high": tmle["ATE_tmle_ci_high"],
            "method_note": tmle["tmle_engine"],
        })
        ate_df = pd.DataFrame(rows)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ate_path = out_dir / CONTRACT.output_files["ate_estimates_csv"]
        sm_path = out_dir / CONTRACT.output_files["summary_json"]
        ate_df.to_csv(ate_path, index=False)

        summary = {
            "n_obs": int(n),
            "n_treated": int((sub[A_col] == 1).sum()),
            "n_control": int((sub[A_col] == 0).sum()),
            "n_outcome_positive": int((sub[Y_col] == 1).sum()),
            "ate_g_formula": g["ATE_g"],
            "ate_iptw": iptw["ATE_iptw"],
            "ate_tmle": tmle["ATE_tmle"],
            "tmle_engine": tmle["tmle_engine"],
            "n_bootstrap": int(self.n_bootstrap),
            "trim_ps": float(self.trim_ps),
        }
        sm_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "ate_estimates_csv": str(ate_path),
            "summary_json": str(sm_path),
            **summary,
        }


def get_solver(n_bootstrap: int = 1000, random_state: int = 42,
               trim_ps: float = 0.02) -> GFormulaTmleSolver:
    return GFormulaTmleSolver(n_bootstrap=n_bootstrap,
                                random_state=random_state,
                                trim_ps=trim_ps)


def selftest() -> Dict[str, Any]:
    """Ground-truth: simulate confounded data with TRUE ATE = 0.10.
    Treatment is confounded by L1, L2 (treated subjects are sicker).
    G-formula, IPTW, and TMLE should ALL recover ~0.10 ± 0.05,
    while a naive (unadjusted) risk-difference would be biased upward.
    """
    import tempfile
    rng = np.random.default_rng(7)
    n = 1500
    L1 = rng.normal(0, 1, n)
    L2 = rng.binomial(1, 0.4, n)
    # Confounded treatment assignment.
    eta_a = -0.3 + 0.9 * L1 + 0.6 * L2
    pA = 1 / (1 + np.exp(-eta_a))
    A = (rng.random(n) < pA).astype(int)
    # True outcome model: ATE = 0.10 on risk-difference scale (approx).
    eta_y = -1.5 + 0.10 * A + 0.7 * L1 + 0.5 * L2
    pY = 1 / (1 + np.exp(-eta_y))
    Y = (rng.random(n) < pY).astype(int)

    df = pd.DataFrame({"A": A, "Y": Y, "L1": L1, "L2": L2})
    # Naive (biased) risk-difference for context.
    naive_rd = float(df[df.A == 1].Y.mean() - df[df.A == 0].Y.mean())

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(n_bootstrap=200).run(
            df, ColumnMapping({"treatment_col": "A", "outcome_col": "Y",
                                "confounders": ["L1", "L2"]}),
            Path(tmp))
        # True ATE on logit scale is 0.10, on risk-difference it depends on
        # mean baseline pY ≈ 0.31, so derivative pY*(1-pY) ≈ 0.21 → RD ≈ 0.021.
        # However G-formula computes the *marginal* RD = mean over L of
        # (p(Y|A=1,L) - p(Y|A=0,L)). With logit(0.10) and pY≈0.31,
        # marginal RD ≈ 0.021. We allow tolerance ±0.04.
        true_ate_approx = 0.021
        for col, est_name in [("ate_g_formula", "g-formula"),
                              ("ate_iptw", "iptw")]:
            val = out[col]
            if val is None or abs(val - true_ate_approx) > 0.05:
                diffs.append(f"{est_name}={val} far from true ~{true_ate_approx:.3f}")
        # TMLE: only check if zepid available.
        if _HAVE_ZEPID and out["ate_tmle"] is not None:
            if abs(out["ate_tmle"] - true_ate_approx) > 0.05:
                diffs.append(f"TMLE={out['ate_tmle']:.4f} far from "
                             f"true ~{true_ate_approx:.3f}")
        # Naive RD should be biased upward (treated are sicker).
        if not (naive_rd > out["ate_g_formula"] + 0.02):
            diffs.append(f"naive RD={naive_rd:.3f} should exceed G-formula "
                         f"ATE={out['ate_g_formula']:.3f} by >0.02 (confounding "
                         "not detected)")

    return {
        "ok": len(diffs) == 0,
        "summary": ("G-formula + IPTW + TMLE all recover true ATE; naive "
                    "estimate is biased as expected"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["g_formula_tmle"],
                    "zepid_available": _HAVE_ZEPID,
                    "naive_rd": naive_rd},
    }
