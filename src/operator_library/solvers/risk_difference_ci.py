"""Risk difference / risk ratio / odds ratio with 95%CI (V8 Phase 3 §P0-4).

Two input modes:
  (a) row-level: binary ``treatment_col`` + binary ``outcome_col``.
  (b) cell-counts: four static_params n_treated_event / n_treated_no_event
      / n_control_event / n_control_no_event (a 2x2 table).

Formulas:
  RD  = p1 - p0,             Wald CI on the difference of two proportions.
  RR  = p1 / p0,             log-Wald CI on ln(RR).
  OR  = (a*d) / (b*c),       Woolf log-Wald CI on ln(OR).

Where p1 = a/(a+b)  (treated event rate),
      p0 = c/(c+d)  (control event rate),
      n1 = a+b, n0 = c+d.

中文：流行病学常用的"风险差/风险比/比值比 + 95%CI"。可吃逐行表
也可吃 2x2 cells。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


RD_CI_CONTRACT = SolverContract(
    name="risk_difference_ci",
    capability="F11_causal_inference",
    description=(
        "Risk difference, risk ratio and odds ratio with 95%CI from "
        "either patient-level binary treatment/outcome columns OR a "
        "2x2 cell table via static_params (n_treated_event, "
        "n_treated_no_event, n_control_event, n_control_no_event).  "
        "Output: single-row csv with RD/RR/OR and their lower/upper "
        "CI bounds."
    ),
    roles={
        "treatment_col": RoleSpec(Role.BINARY_TARGET,
                                    "0/1 treatment", optional=True),
        "outcome_col":   RoleSpec(Role.BINARY_TARGET,
                                    "0/1 outcome",   optional=True),
    },
    static_params={
        "alpha": 0.05,
        "n_treated_event": None,
        "n_treated_no_event": None,
        "n_control_event": None,
        "n_control_no_event": None,
        # Continuity correction (add 0.5 to each cell) when any cell is 0.
        # On by default — without it RR/OR CIs blow up to inf.
        "haldane_correction": True,
    },
    output_files={"rd_csv": "risk_difference_ci.csv"},
    output_kind={"rd_csv": "s"},
)


def _coerce_binary(s: pd.Series, col_name: str,
                    solver_name: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(s):
        return s.astype(int)
    if pd.api.types.is_numeric_dtype(s):
        u = set(s.dropna().unique())
        if not u.issubset({0, 1, 0.0, 1.0}):
            raise OperatorInputError(
                "OUTCOME_NOT_BINARY", solver=solver_name,
                col=col_name, n_unique=int(s.nunique()),
                vmin=float(s.min()), vmax=float(s.max()),
            )
        return s.astype(int)
    low = s.dropna().astype(str).str.strip().str.lower()
    truthy = {"y", "yes", "true", "1", "t"}
    falsy = {"n", "no", "false", "0", "f"}
    if set(low.unique()) - truthy - falsy:
        raise OperatorInputError(
            "OUTCOME_NOT_BINARY", solver=solver_name,
            col=col_name, n_unique=int(s.nunique()),
            vmin=0.0, vmax=1.0,
        )
    out = s.copy()
    out.loc[:] = s.astype(str).str.strip().str.lower().isin(truthy).astype(int)
    return out


def _ci_from_cells(a: float, b: float, c: float, d: float, alpha: float,
                    haldane: bool) -> Dict[str, float]:
    """All three estimands + CIs from a 2x2 cell count tuple."""
    if haldane and (a == 0 or b == 0 or c == 0 or d == 0):
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    n1 = a + b
    n0 = c + d
    p1 = a / n1 if n1 > 0 else float("nan")
    p0 = c / n0 if n0 > 0 else float("nan")
    z = sps.norm.ppf(1 - alpha / 2)

    # Risk Difference (Wald)
    rd = p1 - p0
    se_rd = np.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    rd_lo, rd_hi = rd - z * se_rd, rd + z * se_rd

    # Risk Ratio (log-Wald)
    if p1 > 0 and p0 > 0:
        ln_rr = np.log(p1 / p0)
        se_lnrr = np.sqrt((1 - p1) / (n1 * p1) + (1 - p0) / (n0 * p0))
        rr = float(np.exp(ln_rr))
        rr_lo = float(np.exp(ln_rr - z * se_lnrr))
        rr_hi = float(np.exp(ln_rr + z * se_lnrr))
    else:
        rr = rr_lo = rr_hi = float("nan")

    # Odds Ratio (Woolf log-Wald)
    if a > 0 and b > 0 and c > 0 and d > 0:
        ln_or = np.log((a * d) / (b * c))
        se_lnor = np.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
        odds_ratio = float(np.exp(ln_or))
        or_lo = float(np.exp(ln_or - z * se_lnor))
        or_hi = float(np.exp(ln_or + z * se_lnor))
    else:
        odds_ratio = or_lo = or_hi = float("nan")

    return {
        "n_treated": float(n1), "n_control": float(n0),
        "p_treated": float(p1), "p_control": float(p0),
        "RD":         float(rd),
        "RD_ci_low":  float(rd_lo),
        "RD_ci_high": float(rd_hi),
        "RR":         rr,
        "RR_ci_low":  rr_lo,
        "RR_ci_high": rr_hi,
        "OR":         odds_ratio,
        "OR_ci_low":  or_lo,
        "OR_ci_high": or_hi,
    }


class RiskDifferenceCISolver:
    contract = RD_CI_CONTRACT

    def __init__(self, alpha: float = 0.05,
                  n_treated_event: Optional[float] = None,
                  n_treated_no_event: Optional[float] = None,
                  n_control_event: Optional[float] = None,
                  n_control_no_event: Optional[float] = None,
                  haldane_correction: bool = True) -> None:
        self.alpha = float(alpha)
        self.cells = (n_treated_event, n_treated_no_event,
                       n_control_event, n_control_no_event)
        self.haldane = bool(haldane_correction)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        t_col = mapping.get("treatment_col")
        y_col = mapping.get("outcome_col")
        cells = self.cells

        if all(v is not None for v in cells):
            a = float(cells[0]); b = float(cells[1])
            c = float(cells[2]); d = float(cells[3])
            mode = "cells"
        elif t_col and y_col and t_col in df.columns and y_col in df.columns:
            t = _coerce_binary(df[t_col], t_col, "risk_difference_ci")
            y = _coerce_binary(df[y_col], y_col, "risk_difference_ci")
            sub = pd.DataFrame({"t": t, "y": y}).dropna()
            a = float(((sub["t"] == 1) & (sub["y"] == 1)).sum())
            b = float(((sub["t"] == 1) & (sub["y"] == 0)).sum())
            c = float(((sub["t"] == 0) & (sub["y"] == 1)).sum())
            d = float(((sub["t"] == 0) & (sub["y"] == 0)).sum())
            mode = "row_level"
        else:
            raise OperatorInputError(
                "MISSING_STAT_PARAM", solver="risk_difference_ci",
                stat="risk_difference_ci",
                param=("either (treatment_col, outcome_col) mapping "
                        "OR all 4 cell static_params"),
            )

        if (a + b) == 0 or (c + d) == 0:
            raise OperatorInputError(
                "INVALID_STAT", solver="risk_difference_ci",
                stat=f"empty group: n_treated={a+b}, n_control={c+d}",
                whitelist=["both arms must have >0 subjects"],
            )

        res = _ci_from_cells(a, b, c, d, self.alpha, self.haldane)
        res["alpha"] = self.alpha
        res["mode"]  = mode
        res["a"] = float(a); res["b"] = float(b)
        res["c"] = float(c); res["d"] = float(d)

        out = pd.DataFrame([res])
        path = Path(output_dir) / RD_CI_CONTRACT.output_files["rd_csv"]
        out.to_csv(path, index=False)
        return {"rd_csv": str(path), **res}


def get_solver(alpha: float = 0.05,
                n_treated_event=None, n_treated_no_event=None,
                n_control_event=None, n_control_no_event=None,
                haldane_correction: bool = True
                ) -> RiskDifferenceCISolver:
    return RiskDifferenceCISolver(
        alpha=alpha,
        n_treated_event=n_treated_event,
        n_treated_no_event=n_treated_no_event,
        n_control_event=n_control_event,
        n_control_no_event=n_control_no_event,
        haldane_correction=haldane_correction,
    )


def selftest() -> Dict[str, Any]:
    """Reference: numpy-computed RD/RR/OR + CIs from a canonical 2x2,
    re-derived to full float64 precision so the solver and the
    reference go through the *same* arithmetic from cells onward.

    Cells: a=15, b=85, c=10, d=90 (haldane disabled).
    """
    import tempfile
    from scipy import stats as _sps
    # ---- independent ground truth at full precision ------------------
    a, b, c, d = 15.0, 85.0, 10.0, 90.0
    n1 = a + b; n0 = c + d
    p1 = a / n1; p0 = c / n0
    z = float(_sps.norm.ppf(0.975))
    rd_ref = p1 - p0
    se_rd = float(np.sqrt(p1*(1-p1)/n1 + p0*(1-p0)/n0))
    rd_lo_ref = rd_ref - z*se_rd
    rd_hi_ref = rd_ref + z*se_rd
    ln_rr = float(np.log(p1/p0))
    se_ln_rr = float(np.sqrt((1-p1)/(n1*p1) + (1-p0)/(n0*p0)))
    rr_ref = float(np.exp(ln_rr))
    rr_lo_ref = float(np.exp(ln_rr - z*se_ln_rr))
    rr_hi_ref = float(np.exp(ln_rr + z*se_ln_rr))
    ln_or = float(np.log((a*d)/(b*c)))
    se_ln_or = float(np.sqrt(1/a + 1/b + 1/c + 1/d))
    or_ref = float(np.exp(ln_or))
    or_lo_ref = float(np.exp(ln_or - z*se_ln_or))
    or_hi_ref = float(np.exp(ln_or + z*se_ln_or))

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        s = get_solver(alpha=0.05,
                          n_treated_event=15, n_treated_no_event=85,
                          n_control_event=10, n_control_no_event=90,
                          haldane_correction=False)
        out = s.run(df=pd.DataFrame({"_": [0]}),
                      mapping=ColumnMapping({}),
                      output_dir=Path(tmp))
        ref = {
            "RD":         rd_ref,
            "RD_ci_low":  rd_lo_ref, "RD_ci_high": rd_hi_ref,
            "RR":         rr_ref,
            "RR_ci_low":  rr_lo_ref, "RR_ci_high": rr_hi_ref,
            "OR":         or_ref,
            "OR_ci_low":  or_lo_ref, "OR_ci_high": or_hi_ref,
        }
        for k, v_ref in ref.items():
            got = out[k]
            if abs(got - v_ref) > 1e-9:
                diffs.append(f"{k}: got {got:.10f} vs ref {v_ref:.10f}")
    return {"ok": not diffs,
            "summary": ("risk_difference_ci matches numpy/scipy "
                          "closed-form Wald / log-Wald CIs"
                          if not diffs
                          else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs}}


__all__ = ["RD_CI_CONTRACT", "RiskDifferenceCISolver",
            "get_solver", "selftest"]
