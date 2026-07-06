"""Two-stage least squares (2SLS) instrumental variable estimator.

Closes the QRData causal-IV gap.  Estimates the causal effect of an
*endogenous* regressor on a continuous outcome using one or more
*instruments* via the classical two-stage least squares procedure.

Backed by ``linearmodels.iv.IV2SLS`` (the de facto reference Python
implementation; matches Stata ``ivreg2`` to 4-5 decimals on standard
tests).

References
----------
- Angrist JD & Pischke JS (2009) *Mostly Harmless Econometrics*,
  chapter 4.
- Wooldridge JM (2010) *Econometric Analysis of Cross Section and
  Panel Data*, chapter 5.
- Sheppard K (2024) ``linearmodels`` package documentation
  https://bashtage.github.io/linearmodels/iv/index.html

Outputs
-------
- ``iv_coefficients.csv``
  rows for each regressor (endogenous + exogenous) with
  ``coef / std_error / t_stat / p_value / ci_low / ci_high``.
- ``iv_diagnostics.json``
  - first-stage F (weak-instrument check, F<10 → weak)
  - Wu-Hausman endogeneity test p-value (low p → OLS biased)
  - Sargan / Anderson-Rubin over-identification test (when n_instruments
    > n_endog), p-value (low p → at least one instrument violates
    exclusion).
  - n_obs / n_instruments / n_endog
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from ._inputs import coerce_numeric_friendly, detect_column_kind


CONTRACT = SolverContract(
    name="instrumental_variable_2sls",
    capability="F_causal_iv",
    description=(
        "Two-stage least squares (2SLS) causal-effect estimation for a "
        "continuous outcome with one endogenous regressor and one or more "
        "instruments. Reports coefficient + 95% CI, plus first-stage F "
        "(weak-instrument), Wu-Hausman (endogeneity) and (when over-identified) "
        "Sargan over-identification diagnostics.  Use when a confounded "
        "regressor has a valid external instrument."
    ),
    roles={
        "outcome_col": RoleSpec(Role.NUMERIC_TARGET,
                                  "continuous outcome y"),
        "endogenous_col": RoleSpec(Role.NUMERIC,
                                     "endogenous regressor (the treatment / "
                                     "variable whose causal effect we want)"),
        "instruments": RoleSpec(Role.NUMERIC_LIST,
                                  "one or more instrumental variables "
                                  "(must affect outcome ONLY through the "
                                  "endogenous regressor)"),
        "exog_covariates": RoleSpec(Role.NUMERIC_LIST,
                                      "exogenous control covariates "
                                      "(included in both stages)",
                                      optional=True),
    },
    static_params={
        "robust_se": True,   # heteroskedasticity-robust SE (HC1, ivreg2 default)
    },
    output_files={
        "coefficients_csv": "iv_coefficients.csv",
        "diagnostics_json": "iv_diagnostics.json",
    },
    output_kind={"coefficients_csv": "s", "diagnostics_json": "s"},
)


class InstrumentalVariable2SLSSolver:
    contract = CONTRACT

    def __init__(self, robust_se: bool = True):
        self.robust_se = bool(robust_se)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        from linearmodels.iv import IV2SLS

        y_col = mapping["outcome_col"]
        endog_col = mapping["endogenous_col"]
        iv_cols = list(mapping.get("instruments") or [])
        exog_cols = list(mapping.get("exog_covariates") or [])

        if not iv_cols:
            raise ValueError("instruments role is required (need >=1 IV)")
        needed = [y_col, endog_col, *iv_cols, *exog_cols]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            raise KeyError(f"IV2SLS: missing columns in df: {missing}")
        # ---- INPUT ROBUSTNESS LAYER ----
        # Use the shared messy-input coercer so '$1,234.56', '75%',
        # Int64 nullable, etc. are all parsed identically across V8.1
        # operators.  See solvers/v8/_inputs.py for the contract.
        sub = df[needed].copy()
        column_diagnostics: Dict[str, Any] = {}
        for c in needed:
            column_diagnostics[c] = detect_column_kind(sub[c])
            sub[c] = coerce_numeric_friendly(sub[c])
        sub = sub.replace([np.inf, -np.inf], np.nan).dropna()
        n = len(sub)
        if n < 30:
            raise ValueError(f"IV2SLS: n={n} too small after dropna; "
                              f"need >=30 (textbook minimum for asymptotic "
                              f"approximations).")
        # Detect (near-)constant columns; 2SLS will explode silently or
        # return NaN std errors on perfectly collinear inputs.  We fail
        # fast with a clear message.
        for c in [endog_col] + iv_cols + exog_cols:
            if sub[c].nunique() <= 1:
                raise ValueError(f"IV2SLS: column {c!r} is constant after "
                                  "dropna — model is singular.")
        # Warn (via diagnostics, not raise) when a column looks
        # categorical or extremely low-cardinality — IV semantics for
        # categorical endog / instruments require pre-encoding.
        risky_cols: List[str] = []
        for c in [endog_col] + iv_cols:
            dk = column_diagnostics[c]
            if dk.get("looks_categorical") or (dk.get("is_binary") is False
                                                 and dk.get("n_unique", 99) <= 5):
                risky_cols.append(c)

        y = sub[[y_col]]
        endog = sub[[endog_col]]
        instruments = sub[iv_cols]
        # exog must include intercept for IV2SLS to behave like ivreg2.
        if exog_cols:
            exog = sub[exog_cols].copy()
        else:
            exog = pd.DataFrame(index=sub.index)
        exog.insert(0, "const", 1.0)

        cov_type = "robust" if self.robust_se else "unadjusted"
        model = IV2SLS(dependent=y, exog=exog, endog=endog,
                        instruments=instruments)
        res = model.fit(cov_type=cov_type)

        # --- coefficients ---
        coef_rows: List[Dict[str, Any]] = []
        for name in res.params.index:
            coef_rows.append({
                "term":     name,
                "coef":     float(res.params[name]),
                "std_error": float(res.std_errors[name]),
                "t_stat":   float(res.tstats[name]),
                "p_value":  float(res.pvalues[name]),
                "ci_low":   float(res.conf_int().loc[name, "lower"]),
                "ci_high":  float(res.conf_int().loc[name, "upper"]),
            })
        coef_df = pd.DataFrame(coef_rows)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        coef_path = out_dir / CONTRACT.output_files["coefficients_csv"]
        coef_df.to_csv(coef_path, index=False)

        # --- diagnostics ---
        diag: Dict[str, Any] = {
            "n_obs":           int(n),
            "n_endog":         1,
            "n_instruments":   len(iv_cols),
            "n_exog_controls": len(exog_cols),
            "r_squared":       float(res.rsquared),
        }
        # Wu-Hausman endogeneity test (H0: OLS is consistent).
        try:
            wu = res.wu_hausman()
            diag["wu_hausman_stat"] = float(wu.stat)
            diag["wu_hausman_p"]    = float(wu.pval)
        except Exception as e:
            diag["wu_hausman_p"] = None
            diag["wu_hausman_note"] = f"unavailable: {e}"
        # First-stage diagnostics (weak-instrument F).
        try:
            fs = res.first_stage
            # ``first_stage.diagnostics`` is a DataFrame with rows per endog
            # variable; columns include 'f.stat' / 'f.pval'.
            d = fs.diagnostics.iloc[0]
            diag["first_stage_f"]    = float(d.get("f.stat",
                                                   d.get("f_statistic",
                                                          float("nan"))))
            diag["first_stage_f_p"]  = float(d.get("f.pval",
                                                   d.get("f_pvalue",
                                                          float("nan"))))
            diag["weak_instrument_warn"] = bool(diag["first_stage_f"] < 10)
        except Exception as e:
            diag["first_stage_f"] = None
            diag["first_stage_note"] = f"unavailable: {e}"
        # Sargan over-identification (only when over-identified).
        if len(iv_cols) > 1:
            try:
                so = res.sargan
                diag["sargan_stat"] = float(so.stat)
                diag["sargan_p"]    = float(so.pval)
            except Exception as e:
                diag["sargan_p"] = None
                diag["sargan_note"] = f"unavailable: {e}"

        # 主结果：endog 的因果效应 + 95% CI（这是用户最想要的一行）。
        endog_row = coef_df[coef_df["term"] == endog_col].iloc[0].to_dict()
        diag["iv_estimate"] = {
            "term":   endog_col,
            "coef":   float(endog_row["coef"]),
            "ci_low": float(endog_row["ci_low"]),
            "ci_high": float(endog_row["ci_high"]),
            "p_value": float(endog_row["p_value"]),
        }
        # Surface dirty-input diagnostics + risky-column hints so the
        # coder/planner can react (e.g. one-hot encode and retry).
        diag["column_diagnostics"] = column_diagnostics
        if risky_cols:
            diag["risky_columns"] = risky_cols
            diag["risky_columns_note"] = (
                "These columns look categorical or have <=5 unique levels. "
                "IV2SLS treats them as continuous; consider one-hot "
                "encoding before passing them as endog/instruments.")

        diag_path = out_dir / CONTRACT.output_files["diagnostics_json"]
        diag_path.write_text(json.dumps(diag, indent=2, default=str),
                              encoding="utf-8")

        return {
            "coefficients_csv": str(coef_path),
            "diagnostics_json": str(diag_path),
            **diag,
        }


def get_solver(robust_se: bool = True) -> InstrumentalVariable2SLSSolver:
    return InstrumentalVariable2SLSSolver(robust_se=robust_se)


# ---------------------------------------------------------------------------
# Ground-truth selftest
# ---------------------------------------------------------------------------
def _gt_a_basic_iv() -> List[str]:
    """GT-A — single-IV SEM, β=2.0; strong instrument; endogeneity present."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 5000
    Z = rng.normal(0, 1, n)
    U = rng.normal(0, 1, n)
    X = 0.8 * Z + 0.6 * U + rng.normal(0, 0.3, n)
    Y = 1.0 + 2.0 * X + 1.5 * U + rng.normal(0, 0.5, n)
    df = pd.DataFrame({"Y": Y, "X": X, "Z": Z})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"outcome_col": "Y", "endogenous_col": "X",
                                "instruments": ["Z"]}), Path(tmp))
        if not (1.85 <= out["iv_estimate"]["coef"] <= 2.15):
            diffs.append(f"[A] 2SLS β={out['iv_estimate']['coef']:.4f} "
                          "outside [1.85, 2.15]")
        if not (out["iv_estimate"]["ci_low"] <= 2.0
                <= out["iv_estimate"]["ci_high"]):
            diffs.append(f"[A] 95% CI does NOT contain true β=2.0")
        if out["first_stage_f"] is None or out["first_stage_f"] < 30:
            diffs.append(f"[A] first-stage F={out['first_stage_f']} weak")
        if out["wu_hausman_p"] is None or out["wu_hausman_p"] > 0.05:
            diffs.append(f"[A] Wu-Hausman p={out['wu_hausman_p']} should be "
                          "<0.05 (endogeneity must be detected)")
        # OLS bias sanity (without IV).
        import statsmodels.api as sm
        ols_b = float(sm.OLS(Y, sm.add_constant(X)).fit().params[1])
        if not (ols_b > 2.0 + 0.2):
            diffs.append(f"[A] OLS β={ols_b:.4f} should overshoot 2.0 by "
                          ">0.2 (proves IV is necessary)")
    return diffs


def _gt_b_overidentified_sargan() -> List[str]:
    """GT-B — over-identified (3 valid IVs); Sargan must NOT reject (p>0.05).

    All three IVs are valid (independent of U), so Sargan-Hansen J-test
    should fail to reject the over-identification restrictions.
    """
    import tempfile
    rng = np.random.default_rng(7)
    n = 4000
    Z1 = rng.normal(0, 1, n)
    Z2 = rng.normal(0, 1, n)
    Z3 = rng.normal(0, 1, n)
    U = rng.normal(0, 1, n)
    X = 0.5 * Z1 + 0.4 * Z2 + 0.3 * Z3 + 0.6 * U + rng.normal(0, 0.3, n)
    Y = 1.0 + 1.5 * X + 1.2 * U + rng.normal(0, 0.5, n)
    df = pd.DataFrame({"Y": Y, "X": X, "Z1": Z1, "Z2": Z2, "Z3": Z3})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"outcome_col": "Y", "endogenous_col": "X",
                                "instruments": ["Z1", "Z2", "Z3"]}),
            Path(tmp))
        if not (1.40 <= out["iv_estimate"]["coef"] <= 1.60):
            diffs.append(f"[B] 2SLS β={out['iv_estimate']['coef']:.4f} "
                          "outside [1.40, 1.60] (true=1.5)")
        # Sargan p should be > 0.05 (all IVs valid → no rejection).
        sp = out.get("sargan_p")
        if sp is None:
            diffs.append("[B] Sargan p not computed (should be available "
                          "for 3 IVs vs 1 endog)")
        elif sp < 0.05:
            diffs.append(f"[B] Sargan p={sp:.4f} REJECTS at 0.05 even "
                          "though all 3 IVs are valid — false rejection.")
    return diffs


def _gt_c_weak_instrument() -> List[str]:
    """GT-C — weak instrument; first-stage F < 10 MUST be flagged."""
    import tempfile
    rng = np.random.default_rng(99)
    n = 1000
    Z = rng.normal(0, 1, n)
    U = rng.normal(0, 1, n)
    # Z's effect on X is tiny (0.05) → first-stage R² ≈ 0.0025
    X = 0.05 * Z + 0.6 * U + rng.normal(0, 1, n)
    Y = 2.0 * X + 1.5 * U + rng.normal(0, 1, n)
    df = pd.DataFrame({"Y": Y, "X": X, "Z": Z})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"outcome_col": "Y", "endogenous_col": "X",
                                "instruments": ["Z"]}), Path(tmp))
        if out.get("first_stage_f") is None:
            diffs.append("[C] first-stage F not computed")
        elif out["first_stage_f"] >= 10:
            diffs.append(f"[C] first-stage F={out['first_stage_f']:.2f} "
                          ">=10 even though Z·X coef is only 0.05 — weak-IV "
                          "should be detected")
        elif not out.get("weak_instrument_warn"):
            diffs.append("[C] weak_instrument_warn not set even though "
                          f"F={out['first_stage_f']:.2f} < 10")
    return diffs


def _gt_d_robustness() -> List[str]:
    """GT-D — input robustness: string-numeric, Int64 nullable, NaN handling,
    fail-fast on bad inputs."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 500
    Z = rng.normal(0, 1, n)
    U = rng.normal(0, 1, n)
    X = 0.8 * Z + 0.6 * U + rng.normal(0, 0.3, n)
    Y = 1.0 + 2.0 * X + 1.5 * U + rng.normal(0, 0.5, n)
    diffs: List[str] = []

    # (1) string-encoded numerics + Int64 nullable dtype must be coerced.
    df = pd.DataFrame({
        "Y": Y.astype(float),
        "X": [f"{v:.6f}" for v in X],        # string-encoded floats
        "Z": pd.array(np.round(Z * 100).astype(np.int64), dtype="Int64"),
    })
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"outcome_col": "Y", "endogenous_col": "X",
                                    "instruments": ["Z"]}), Path(tmp))
            # Z is now ~ 100*original Z, so coefficient on X should still
            # recover β≈2 (instrument scale doesn't affect 2SLS).
            if not (1.7 <= out["iv_estimate"]["coef"] <= 2.3):
                diffs.append(f"[D-coerce] 2SLS β={out['iv_estimate']['coef']} "
                              "should still ≈ 2 after dtype coercion")
        except Exception as e:
            diffs.append(f"[D-coerce] should accept string-num + Int64, "
                          f"raised {type(e).__name__}: {e}")

    # (2) NaN-heavy input: 80% NaN should still work after dropna.
    Xn = X.copy()
    Xn[: int(0.6 * n)] = np.nan
    df2 = pd.DataFrame({"Y": Y, "X": Xn, "Z": Z})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out2 = get_solver().run(
                df2, ColumnMapping({"outcome_col": "Y", "endogenous_col": "X",
                                     "instruments": ["Z"]}), Path(tmp))
            if out2["n_obs"] >= n:
                diffs.append(f"[D-nan] n_obs={out2['n_obs']} should be < {n} "
                              "after 60% NaN dropna")
        except Exception as e:
            diffs.append(f"[D-nan] should survive 60% NaN, raised {e}")

    # (3) Constant column must fail fast.
    df3 = pd.DataFrame({"Y": Y, "X": X, "Z": np.zeros_like(Z)})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            get_solver().run(
                df3, ColumnMapping({"outcome_col": "Y", "endogenous_col": "X",
                                     "instruments": ["Z"]}), Path(tmp))
            diffs.append("[D-const] constant IV should raise but didn't")
        except ValueError:
            pass  # expected
        except Exception as e:
            diffs.append(f"[D-const] expected ValueError, got "
                          f"{type(e).__name__}: {e}")

    # (4) Missing column must raise KeyError.
    df4 = pd.DataFrame({"Y": Y, "X": X})  # no Z
    with tempfile.TemporaryDirectory() as tmp:
        try:
            get_solver().run(
                df4, ColumnMapping({"outcome_col": "Y", "endogenous_col": "X",
                                     "instruments": ["Z"]}), Path(tmp))
            diffs.append("[D-missing] missing Z column should raise KeyError")
        except KeyError:
            pass
        except Exception as e:
            diffs.append(f"[D-missing] expected KeyError, got "
                          f"{type(e).__name__}")
    return diffs


def _gt_e_messy_strings() -> List[str]:
    """GT-E — messy real-world strings: '$' currency on outcome,
    ',' thousands on endog, '%' percent on instrument. β must still
    recover (after the conversion rescaling)."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 1500
    Z = rng.normal(0, 1, n)
    U = rng.normal(0, 1, n)
    X = 0.8 * Z + 0.6 * U + rng.normal(0, 0.3, n)
    Y = 1.0 + 2.0 * X + 1.5 * U + rng.normal(0, 0.5, n)
    df = pd.DataFrame({
        # Outcome as currency strings: "$1,234.56"
        "Y": [f"${v:,.2f}" if v >= 0 else f"-${-v:,.2f}" for v in Y],
        # Endogenous regressor as plain numeric strings with thousands sep
        "X": [f"{v * 1000:,.4f}" for v in X],   # scaled ×1000 + comma
        # Instrument as percentages
        "Z": [f"{v * 10:.2f}%" for v in Z],     # ×10 / 100 = ×0.1
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"outcome_col": "Y", "endogenous_col": "X",
                                    "instruments": ["Z"]}), Path(tmp))
        except Exception as e:
            diffs.append(f"[E] should parse '$1,234' / '75%' strings, "
                          f"raised {type(e).__name__}: {e}")
            return diffs
        # β on the rescaled vars: Y stays the same (currency parses to
        # original value), X is multiplied by 1000, so β should be 2/1000
        # = 0.002.  Tolerance ±0.0002.
        b = out["iv_estimate"]["coef"]
        if not (0.0018 <= b <= 0.0022):
            diffs.append(f"[E] rescaled β={b:.6f} expected ≈0.002 (±0.0002) "
                          "— string parsing may have corrupted the values")
        # Column diagnostics must report the dirty patterns detected.
        cd = out.get("column_diagnostics", {})
        if not cd.get("Y", {}).get("had_currency"):
            diffs.append("[E] diagnostics missed currency on Y")
        if not cd.get("X", {}).get("had_thousands"):
            diffs.append("[E] diagnostics missed thousands separator on X")
        if not cd.get("Z", {}).get("had_percent"):
            diffs.append("[E] diagnostics missed percent on Z")
    return diffs


def selftest() -> Dict[str, Any]:
    """5-scenario ground-truth + robustness test suite.

      GT-A  basic IV (β=2.0 recovery, strong-Z, endogeneity detected)
      GT-B  over-identified (Sargan should NOT reject — all IVs valid)
      GT-C  weak-IV (first-stage F<10 must be flagged)
      GT-D  input robustness (dtype coercion, NaN, constant col, missing)
      GT-E  messy strings ($ / , / %)  — parse + diagnostics must surface

    All five must pass for ok=True.
    """
    diffs = (_gt_a_basic_iv() + _gt_b_overidentified_sargan()
             + _gt_c_weak_instrument() + _gt_d_robustness()
             + _gt_e_messy_strings())
    return {
        "ok": len(diffs) == 0,
        "summary": ("5/5 scenarios pass: basic IV, over-id Sargan, "
                    "weak-IV, input robustness, messy strings ($/%/,)"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["instrumental_variable_2sls"],
                    "n_scenarios": 5},
    }
