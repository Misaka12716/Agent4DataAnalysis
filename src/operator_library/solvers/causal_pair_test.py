"""Bivariate causal direction screening (V8 Phase 3 §P0-6).

Lightweight "which direction is more plausible" test between two
numeric variables.  Two backends:
  - ``granger``: statsmodels Granger F-test on lagged values
                 (X→Y if X's past helps predict Y after controlling
                 for Y's own past).  Suitable for time-ordered data.
  - ``corr_lag``: simple lagged Pearson correlation comparison
                  (asymmetry test, fallback when statsmodels Granger
                  fails or N < 30).

NOT a replacement for proper causal inference (g-formula / TMLE).
Use as a screening / hypothesis-generation tool.

中文：两变量"哪个方向更像因"的轻量筛查，不是严格因果证据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError
from ._numeric_utils import coerce_to_numeric


CAUSAL_PAIR_CONTRACT = SolverContract(
    name="causal_pair_test",
    capability="F11_causal_inference",
    description=(
        "Screen the more plausible causal direction between two numeric "
        "columns.  Backends: granger (statsmodels F-test on time-ordered "
        "lags) or corr_lag (lagged Pearson asymmetry).  Output: single-row "
        "csv [var_a, var_b, decision, p_a_to_b, p_b_to_a, score_a_to_b, "
        "score_b_to_a].  decision ∈ {a_to_b, b_to_a, none, inconclusive}."
    ),
    roles={
        "var_a": RoleSpec(Role.NUMERIC, "first numeric variable"),
        "var_b": RoleSpec(Role.NUMERIC, "second numeric variable"),
    },
    static_params={
        "method": "auto",
        "max_lag": 3,
        "alpha":   0.05,
    },
    output_files={"causal_csv": "causal_pair_test.csv"},
    output_kind={"causal_csv": "s"},
)


def _granger_pvalue(y: np.ndarray, x: np.ndarray, max_lag: int) -> float:
    """Smallest Granger p-value across lags 1..max_lag for x→y."""
    from statsmodels.tsa.stattools import grangercausalitytests
    data = np.column_stack([y, x])
    try:
        res = grangercausalitytests(data, maxlag=max_lag, verbose=False)
    except Exception:
        return float("nan")
    pvals = [res[L][0]["ssr_ftest"][1] for L in res]
    return float(min(pvals)) if pvals else float("nan")


def _corr_lag_test(y: np.ndarray, x: np.ndarray, max_lag: int) -> tuple:
    """Return (best_|r|, p) using one-sided lagged correlation."""
    from scipy import stats as sps
    best_r = 0.0
    best_p = 1.0
    for L in range(1, max_lag + 1):
        if len(x) <= L + 5:
            break
        a = x[:-L]
        b = y[L:]
        if len(a) < 5:
            break
        r, p = sps.pearsonr(a, b)
        if abs(r) > abs(best_r):
            best_r = float(r)
            best_p = float(p)
    return best_r, best_p


class CausalPairTestSolver:
    contract = CAUSAL_PAIR_CONTRACT

    def __init__(self, method: str = "auto", max_lag: int = 3,
                  alpha: float = 0.05) -> None:
        self.method = (method or "auto").strip().lower()
        self.max_lag = int(max_lag)
        self.alpha = float(alpha)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        a_col = mapping.get("var_a")
        b_col = mapping.get("var_b")
        df = df.copy()
        for nm, col in (("var_a", a_col), ("var_b", b_col)):
            if not col or col not in df.columns:
                raise OperatorInputError(
                    "COLUMN_NOT_FOUND", solver="causal_pair_test",
                    col=col, available=list(df.columns)[:20],
                )
            if not pd.api.types.is_numeric_dtype(df[col]):
                coerced, ok, rate = coerce_to_numeric(df[col])
                if ok:
                    df[col] = coerced
                else:
                    raise OperatorInputError(
                        "COLUMN_NOT_COERCIBLE", solver="causal_pair_test",
                        col=col, dtype=str(df[col].dtype),
                        coerce_rate=f"{rate:.0%}",
                    )

        sub = df[[a_col, b_col]].dropna()
        n = len(sub)

        method = self.method
        if method == "auto":
            method = "granger" if n >= 30 else "corr_lag"

        # P3-fix: instead of a single hard cut at n<max(20, 3*max_lag)
        # that returned the (misleading) NO_NUMERIC_COLUMNS code, split:
        #   1. n insufficient for Granger → silently fall back to corr_lag
        #   2. n insufficient even for corr_lag (need >= max_lag+5 pairs
        #      after lagging) → raise INSUFFICIENT_SAMPLES with the real
        #      sample-count.
        granger_min = max(20, self.max_lag * 3)
        corr_lag_min = self.max_lag + 5
        if method == "granger" and n < granger_min:
            method = "corr_lag"
        if n < corr_lag_min:
            raise OperatorInputError(
                "INSUFFICIENT_SAMPLES", solver="causal_pair_test",
                required_n=corr_lag_min, actual_n=n,
            )

        a = sub[a_col].astype(float).values
        b = sub[b_col].astype(float).values

        if method == "granger":
            p_ab = _granger_pvalue(b, a, self.max_lag)
            p_ba = _granger_pvalue(a, b, self.max_lag)
            if not np.isfinite(p_ab) or not np.isfinite(p_ba):
                method = "corr_lag"
        if method == "corr_lag":
            r_ab, p_ab = _corr_lag_test(b, a, self.max_lag)
            r_ba, p_ba = _corr_lag_test(a, b, self.max_lag)
            score_ab, score_ba = abs(r_ab), abs(r_ba)
        else:
            # Use 1/p as "strength score" so larger = more evidence.
            score_ab = float("inf") if p_ab == 0 else float(1.0 / max(p_ab, 1e-300))
            score_ba = float("inf") if p_ba == 0 else float(1.0 / max(p_ba, 1e-300))

        a_sig = p_ab < self.alpha
        b_sig = p_ba < self.alpha
        if a_sig and not b_sig:
            decision = "a_to_b"
        elif b_sig and not a_sig:
            decision = "b_to_a"
        elif a_sig and b_sig:
            decision = "inconclusive"   # both directions significant
        else:
            decision = "none"

        row = {
            "var_a":         a_col,
            "var_b":         b_col,
            "method":        method,
            "n":             int(len(a)),
            "max_lag":       self.max_lag,
            "p_a_to_b":      float(p_ab),
            "p_b_to_a":      float(p_ba),
            "score_a_to_b":  float(score_ab),
            "score_b_to_a":  float(score_ba),
            "alpha":         self.alpha,
            "decision":      decision,
        }
        out = pd.DataFrame([row])
        path = Path(output_dir) / CAUSAL_PAIR_CONTRACT.output_files["causal_csv"]
        out.to_csv(path, index=False)
        return {"causal_csv": str(path), **row}


def get_solver(method: str = "auto", max_lag: int = 3,
                alpha: float = 0.05) -> CausalPairTestSolver:
    return CausalPairTestSolver(method=method, max_lag=max_lag, alpha=alpha)


def selftest() -> Dict[str, Any]:
    """Construct y[t] = 0.6*y[t-1] + 0.7*x[t-1] + noise where x is
    independent AR(1).  Ground truth: x → y is causally significant,
    y → x is not.

    Tolerance: just check the decision ∈ {a_to_b} when var_a=x var_b=y.
    """
    import tempfile
    rng = np.random.default_rng(7)
    n = 300
    x = np.zeros(n); y = np.zeros(n)
    x[0] = rng.normal()
    y[0] = rng.normal()
    for t in range(1, n):
        x[t] = 0.4 * x[t-1] + rng.normal(0, 1)
        y[t] = 0.6 * y[t-1] + 0.7 * x[t-1] + rng.normal(0, 1)
    df = pd.DataFrame({"x": x, "y": y})

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        s = get_solver(method="granger", max_lag=3, alpha=0.05)
        out = s.run(df=df, mapping=ColumnMapping(
            {"var_a": "x", "var_b": "y"}), output_dir=Path(tmp))
        if out["decision"] != "a_to_b":
            diffs.append(f"x→y fixture: got decision={out['decision']} "
                         f"p_a_to_b={out['p_a_to_b']:.4g} "
                         f"p_b_to_a={out['p_b_to_a']:.4g}")
        # Swap roles: should now be b_to_a
        out2 = s.run(df=df, mapping=ColumnMapping(
            {"var_a": "y", "var_b": "x"}), output_dir=Path(tmp))
        if out2["decision"] != "b_to_a":
            diffs.append(f"y,x fixture: got decision={out2['decision']}")
    return {"ok": not diffs,
            "summary": ("causal_pair_test correctly identifies x→y on "
                          "AR(1) leader-follower fixture" if not diffs
                          else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs}}


__all__ = ["CAUSAL_PAIR_CONTRACT", "CausalPairTestSolver",
            "get_solver", "selftest"]
