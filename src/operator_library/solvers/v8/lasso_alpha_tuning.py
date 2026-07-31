"""Shared L1 alpha selection helpers (V8.3).

Two modes:
* ``cv``              — sklearn LassoCV / LogisticRegressionCV (default).
* ``prior_precision`` — GenoTEX paper ``tune_hyperparameters`` criterion:
  pick alpha that maximises *selection precision* against an external
  prior gene list (OpenTargets ``related_genes``), NOT CV prediction
  error.

The prior mode is **opt-in** only.  When ``prior_related_genes`` is
empty or does not overlap the feature columns, callers must fall
back to ``cv``.
"""
from __future__ import annotations

import warnings
from typing import Iterable, List, Sequence, Tuple

import numpy as np


def selection_precision(selected: Iterable[str],
                          prior: Iterable[str]) -> float:
    sel = {str(g) for g in selected}
    pri = {str(g) for g in prior}
    if not sel:
        return 0.0
    return len(sel & pri) / len(sel)


def tune_alpha_prior_precision(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: Sequence[str],
    alphas: Sequence[float],
    prior_related_genes: Sequence[str],
    *,
    max_iter: int = 20000,
    random_state: int = 42,
    default_alpha: float = 0.1,
) -> Tuple[float, np.ndarray, float, int]:
    """Paper-style alpha grid search by selection precision.

    Returns (best_alpha, coefs, best_precision, n_prior_in_panel).
    """
    from sklearn.linear_model import Lasso

    prior = [g for g in prior_related_genes if g in feature_names]
    if not prior:
        raise ValueError(
            "prior_precision tuning: no prior genes overlap features")

    best_alpha = float(default_alpha)
    best_prec = -1.0
    best_coefs = np.zeros(len(feature_names), dtype=float)

    for a in alphas:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = Lasso(alpha=float(a), max_iter=max_iter,
                        random_state=random_state)
            m.fit(X, y)
        nz = np.where(m.coef_ != 0.0)[0]
        selected = [feature_names[i] for i in nz]
        prec = selection_precision(selected, prior)
        if prec > best_prec:
            best_prec = prec
            best_alpha = float(a)
            best_coefs = m.coef_.copy()

    # Paper L502-504: if no alpha yields any prior hit, use default.
    if best_prec <= 0.0:
        best_alpha = float(default_alpha)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = Lasso(alpha=best_alpha, max_iter=max_iter,
                        random_state=random_state)
            m.fit(X, y)
        best_coefs = m.coef_
        nz = np.where(best_coefs != 0.0)[0]
        best_prec = selection_precision(
            [feature_names[i] for i in nz], prior)

    return best_alpha, best_coefs, float(best_prec), len(prior)
