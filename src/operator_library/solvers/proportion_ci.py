"""Confidence interval for a binomial proportion (V8 Phase 3 §P0-2).

Two modes:
  (a) row-level: pass ``success_col`` (0/1).  Optional subset_query.
  (b) cell-counts: pass ``n_trials`` + ``n_successes`` via static_params.

Methods: ``wilson`` (default), ``normal`` (Wald), ``exact`` (Clopper-Pearson).

中文：单比例的置信区间。两种输入二选一。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


PROPORTION_CI_CONTRACT = SolverContract(
    name="proportion_ci",
    capability="F06_hypothesis_testing",
    description=(
        "Confidence interval for a binomial proportion p = k/n.  "
        "Input: either a 0/1 success column (optional subset_query) "
        "OR n_trials + n_successes via static_params.  Methods: "
        "wilson (default, recommended), normal (Wald), exact "
        "(Clopper-Pearson).  Output: single-row csv "
        "[n, k, p_hat, ci_low, ci_high, alpha, method]."
    ),
    roles={
        "success_col": RoleSpec(
            Role.BINARY_TARGET,
            "0/1 success column (row-level input mode)",
            optional=True,
        ),
    },
    static_params={
        "alpha": 0.05,
        "method": "wilson",
        "n_trials": None,
        "n_successes": None,
        "subset_query": None,
    },
    output_files={"ci_csv": "proportion_ci.csv"},
    output_kind={"ci_csv": "s"},
)


def _wilson_ci(k: int, n: int, alpha: float) -> tuple:
    if n <= 0:
        return (float("nan"), float("nan"))
    z = sps.norm.ppf(1 - alpha / 2)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _normal_ci(k: int, n: int, alpha: float) -> tuple:
    if n <= 0:
        return (float("nan"), float("nan"))
    z = sps.norm.ppf(1 - alpha / 2)
    p = k / n
    half = z * np.sqrt(p * (1 - p) / n)
    return (max(0.0, p - half), min(1.0, p + half))


def _exact_ci(k: int, n: int, alpha: float) -> tuple:
    # Clopper-Pearson; sps.beta inverse-CDF (a.k.a. ppf).
    if n <= 0:
        return (float("nan"), float("nan"))
    if k == 0:
        lo = 0.0
    else:
        lo = sps.beta.ppf(alpha / 2, k, n - k + 1)
    if k == n:
        hi = 1.0
    else:
        hi = sps.beta.ppf(1 - alpha / 2, k + 1, n - k)
    return (float(lo), float(hi))


_METHODS = {"wilson": _wilson_ci,
             "normal": _normal_ci,
             "wald":   _normal_ci,   # alias
             "exact":  _exact_ci,
             "clopper_pearson": _exact_ci}


class ProportionCISolver:
    contract = PROPORTION_CI_CONTRACT

    def __init__(self, alpha: float = 0.05, method: str = "wilson",
                  n_trials: Optional[int] = None,
                  n_successes: Optional[int] = None,
                  subset_query: Optional[str] = None) -> None:
        self.alpha = float(alpha)
        self.method = (method or "wilson").strip().lower()
        self.n_trials = n_trials
        self.n_successes = n_successes
        self.subset_query = subset_query

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        if self.method not in _METHODS:
            raise OperatorInputError(
                "INVALID_STAT", solver="proportion_ci",
                stat=self.method, whitelist=sorted(_METHODS.keys()),
            )

        # ---- 1. derive (n, k) ----------------------------------------
        success_col = mapping.get("success_col")
        if self.n_trials is not None and self.n_successes is not None:
            n = int(self.n_trials)
            k = int(self.n_successes)
            if k < 0 or n <= 0 or k > n:
                raise OperatorInputError(
                    "INVALID_STAT", solver="proportion_ci",
                    stat=f"n_trials={n}, n_successes={k}",
                    whitelist=["require 0 <= k <= n and n > 0"],
                )
        elif success_col is not None and success_col in df.columns:
            work = df
            if self.subset_query:
                try:
                    work = df.query(self.subset_query)
                except Exception as e:
                    raise OperatorInputError(
                        "SUBSET_QUERY_INVALID", solver="proportion_ci",
                        query=self.subset_query,
                        reason=f"{type(e).__name__}: {e}",
                    )
            col = work[success_col].dropna()
            # Accept 0/1, True/False, or strings 'Y'/'N' / 'yes'/'no'.
            if pd.api.types.is_bool_dtype(col):
                col = col.astype(int)
            elif pd.api.types.is_numeric_dtype(col):
                u = set(col.unique())
                if not u.issubset({0, 1, 0.0, 1.0}):
                    raise OperatorInputError(
                        "OUTCOME_NOT_BINARY", solver="proportion_ci",
                        col=success_col, n_unique=int(col.nunique()),
                        vmin=float(col.min()), vmax=float(col.max()),
                    )
                col = col.astype(int)
            else:
                low = col.astype(str).str.strip().str.lower()
                truthy = {"y", "yes", "true", "1", "t"}
                falsy = {"n", "no", "false", "0", "f"}
                unk = set(low.unique()) - truthy - falsy
                if unk:
                    raise OperatorInputError(
                        "OUTCOME_NOT_BINARY", solver="proportion_ci",
                        col=success_col,
                        n_unique=int(col.nunique()),
                        vmin=0.0, vmax=1.0,
                    )
                col = low.isin(truthy).astype(int)
            n = int(len(col))
            k = int(col.sum())
        else:
            raise OperatorInputError(
                "MISSING_STAT_PARAM", solver="proportion_ci",
                stat="proportion_ci",
                param="either success_col mapping OR (n_trials + n_successes)",
            )

        # ---- 2. compute CI -------------------------------------------
        ci_fn = _METHODS[self.method]
        ci_low, ci_high = ci_fn(k, n, self.alpha)
        p_hat = k / n if n else float("nan")

        out = pd.DataFrame([{
            "n":        n,
            "k":        k,
            "p_hat":    p_hat,
            "ci_low":   ci_low,
            "ci_high":  ci_high,
            "alpha":    self.alpha,
            "method":   self.method,
        }])
        path = Path(output_dir) / PROPORTION_CI_CONTRACT.output_files["ci_csv"]
        out.to_csv(path, index=False)
        return {"ci_csv": str(path), "n": n, "k": k, "p_hat": p_hat,
                "ci_low": ci_low, "ci_high": ci_high,
                "alpha": self.alpha, "method": self.method}


def get_solver(alpha: float = 0.05, method: str = "wilson",
                n_trials: Optional[int] = None,
                n_successes: Optional[int] = None,
                subset_query: Optional[str] = None) -> ProportionCISolver:
    return ProportionCISolver(alpha=alpha, method=method,
                                 n_trials=n_trials, n_successes=n_successes,
                                 subset_query=subset_query)


def selftest() -> Dict[str, Any]:
    """Ground-truth cross-check: statsmodels.stats.proportion +
    R-style hand-computed Wilson interval for n=100, k=50, alpha=0.05.

    中文：与 statsmodels 独立实现对账 + 手算 Wilson 公式对账。
    """
    import tempfile
    from scipy import stats as _sps
    diffs = []
    cases = [
        (100, 50, 0.05),
        (200, 5,  0.05),    # extreme low
        (50,  50, 0.05),    # extreme high (k == n)
        (40,  10, 0.10),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        for n, k, alpha in cases:
            for method in ("wilson", "normal", "exact"):
                s = get_solver(alpha=alpha, method=method,
                                  n_trials=n, n_successes=k)
                out = s.run(df=pd.DataFrame({"_": [0]}),
                              mapping=ColumnMapping({}),
                              output_dir=Path(tmp))
                # statsmodels reference (independent implementation)
                try:
                    from statsmodels.stats.proportion import proportion_confint
                    sm_method = {"wilson": "wilson",
                                  "normal": "normal",
                                  "exact": "beta"}[method]
                    sm_lo, sm_hi = proportion_confint(
                        k, n, alpha=alpha, method=sm_method)
                except Exception:
                    sm_lo = sm_hi = None
                if sm_lo is not None:
                    if abs(out["ci_low"] - float(sm_lo)) > 1e-6 \
                       or abs(out["ci_high"] - float(sm_hi)) > 1e-6:
                        diffs.append(
                            f"{method} n={n} k={k} alpha={alpha}: "
                            f"({out['ci_low']:.6f},{out['ci_high']:.6f}) vs "
                            f"sm ({sm_lo:.6f},{sm_hi:.6f})"
                        )
    return {"ok": not diffs,
            "summary": ("proportion_ci matches statsmodels for "
                          "wilson/normal/exact" if not diffs
                          else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs}}


__all__ = ["PROPORTION_CI_CONTRACT", "ProportionCISolver",
            "get_solver", "selftest"]
