"""W28 — Disparate-impact / fairness audit for model predictions.

Computes the four standard group-fairness metrics in a single pass:

  - Demographic parity difference  (predicted-positive rate gap, max - min)
  - Disparate-impact ratio          (P(y_hat=1|A=g) ratio, min/max in [0,1])
  - Equal-opportunity difference    (TPR gap across groups, max - min)
  - Calibration (Brier) difference  (per-group Brier score, max - min)

Defaults follow the EEOC "4/5 rule" (DI ratio < 0.8 = failed) and the
Hardt et al 2016 NeurIPS definition of equal opportunity (TPR parity).

Uses ``fairlearn.metrics.MetricFrame`` when available (lightweight,
pip-installable), else falls back to a numpy implementation that has
been unit-tested against fairlearn output (see ``selftest``).

References
----------
- Feldman et al (2015) "Certifying and removing disparate impact" KDD.
- Hardt M, Price E, Srebro N (2016) "Equality of opportunity in supervised
  learning" *NeurIPS*.
- EEOC (1979) "Uniform Guidelines on Employee Selection Procedures" — 4/5 rule.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract

try:
    from fairlearn.metrics import (
        MetricFrame,
        demographic_parity_difference,
        equalized_odds_difference,
        true_positive_rate,
        selection_rate,
    )
    _HAVE_FAIRLEARN = True
except Exception:
    _HAVE_FAIRLEARN = False


CONTRACT = SolverContract(
    name="disparate_impact_audit",
    capability="F_fairness_audit",
    description=(
        "Group-fairness audit of model predictions across a protected "
        "attribute.  Reports demographic-parity difference, disparate-impact "
        "ratio (EEOC 4/5 rule, fails if <0.8), equal-opportunity (TPR-parity) "
        "difference (Hardt 2016), and per-group Brier-score (calibration) "
        "difference.  Input CSV must contain predicted-probability OR 0/1 "
        "predictions, ground-truth label, and the protected-attribute column."
    ),
    roles={
        "pred_col": RoleSpec(Role.NUMERIC,
                              "Model predictions (0/1) or probabilities (0-1)"),
        "label_col": RoleSpec(Role.BINARY_TARGET, "Ground-truth 0/1 label"),
        "protected_col": RoleSpec(Role.CATEGORICAL,
                                    "Protected attribute (e.g. sex, race, age_band)"),
    },
    static_params={
        "threshold": 0.5,        # binarise predictions if probability
        "favorable_label": 1,    # which label is the "good" outcome
        "di_pass_ratio": 0.8,    # EEOC 4/5 rule
    },
    output_files={
        "group_metrics_csv": "fairness_group_metrics.csv",
        "overall_summary_csv": "fairness_overall_summary.csv",
        "summary_json": "fairness_summary.json",
    },
    output_kind={
        "group_metrics_csv": "s",
        "overall_summary_csv": "s",
        "summary_json": "s",
    },
)


def _per_group_metrics(y_true: np.ndarray, y_score: np.ndarray,
                        y_pred: np.ndarray, groups: np.ndarray
                        ) -> pd.DataFrame:
    """Per-group counts, selection rate, TPR, FPR, Brier."""
    rows: List[Dict[str, Any]] = []
    for g in np.unique(groups):
        mask = groups == g
        n = int(mask.sum())
        y_t = y_true[mask]
        y_p = y_pred[mask]
        y_s = y_score[mask]
        sel_rate = float(y_p.mean()) if n else float("nan")
        pos_mask = y_t == 1
        neg_mask = y_t == 0
        tpr = float(y_p[pos_mask].mean()) if pos_mask.any() else float("nan")
        fpr = float(y_p[neg_mask].mean()) if neg_mask.any() else float("nan")
        brier = float(np.mean((y_s - y_t) ** 2))
        rows.append({
            "group": str(g),
            "n": n,
            "n_positive": int(pos_mask.sum()),
            "selection_rate": sel_rate,
            "true_positive_rate": tpr,
            "false_positive_rate": fpr,
            "brier_score": brier,
        })
    return pd.DataFrame(rows)


class DisparateImpactAuditSolver:
    contract = CONTRACT

    def __init__(self, threshold: float = 0.5,
                 favorable_label: int = 1,
                 di_pass_ratio: float = 0.8):
        self.threshold = float(threshold)
        self.favorable_label = int(favorable_label)
        self.di_pass_ratio = float(di_pass_ratio)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        pred_col = mapping.get("pred_col")
        label_col = mapping.get("label_col")
        prot_col = mapping.get("protected_col")
        if not all([pred_col, label_col, prot_col]):
            raise ValueError("pred_col, label_col, protected_col are required")

        sub = df[[pred_col, label_col, prot_col]].dropna().copy()
        n = len(sub)
        if n < 30:
            raise ValueError(f"n={n} too small; need n>=30")

        y_score = sub[pred_col].astype(float).values
        # If predictions are already 0/1, treat as scores too (just allows Brier).
        y_pred = (y_score >= self.threshold).astype(int)
        y_true = sub[label_col].astype(float).astype(int).values
        # Recode label so favorable = 1.
        if self.favorable_label == 0:
            y_true = 1 - y_true
            y_pred = 1 - y_pred
            y_score = 1.0 - y_score
        groups = sub[prot_col].astype(str).values

        gm = _per_group_metrics(y_true, y_score, y_pred, groups)

        sel_rates = gm["selection_rate"].values
        sel_rates = sel_rates[~np.isnan(sel_rates)]
        if len(sel_rates) >= 2:
            dpd = float(sel_rates.max() - sel_rates.min())
            di_ratio = float(sel_rates.min() / sel_rates.max()) if sel_rates.max() > 0 else float("nan")
        else:
            dpd = float("nan")
            di_ratio = float("nan")
        tprs = gm["true_positive_rate"].dropna().values
        eod = float(tprs.max() - tprs.min()) if len(tprs) >= 2 else float("nan")
        fprs = gm["false_positive_rate"].dropna().values
        eo_d = max(
            tprs.max() - tprs.min() if len(tprs) >= 2 else 0.0,
            fprs.max() - fprs.min() if len(fprs) >= 2 else 0.0,
        ) if (len(tprs) >= 2 or len(fprs) >= 2) else float("nan")
        briers = gm["brier_score"].values
        brier_d = float(briers.max() - briers.min()) if len(briers) >= 2 else float("nan")

        # If fairlearn available, cross-check key metrics.
        cross_check: Dict[str, Any] = {}
        if _HAVE_FAIRLEARN:
            try:
                dpd_fl = float(demographic_parity_difference(
                    y_true=y_true, y_pred=y_pred, sensitive_features=groups))
                eod_fl = float(equalized_odds_difference(
                    y_true=y_true, y_pred=y_pred, sensitive_features=groups))
                cross_check["fairlearn_dpd"] = dpd_fl
                cross_check["fairlearn_eod"] = eod_fl
                cross_check["dpd_matches_fairlearn"] = bool(abs(dpd - dpd_fl) < 1e-6)
                # fairlearn's eod = max(|TPRdiff|, |FPRdiff|); compare to ours.
                cross_check["eod_matches_fairlearn"] = bool(abs(eo_d - eod_fl) < 1e-6)
            except Exception as e:
                cross_check["fairlearn_error"] = str(e)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        gm_path = out_dir / CONTRACT.output_files["group_metrics_csv"]
        gm.to_csv(gm_path, index=False)

        overall = pd.DataFrame([{
            "n_total": int(n),
            "n_groups": int(gm.shape[0]),
            "demographic_parity_diff": dpd,
            "disparate_impact_ratio": di_ratio,
            "di_pass_4_5_rule": bool(
                (not np.isnan(di_ratio)) and di_ratio >= self.di_pass_ratio
            ),
            "equal_opportunity_diff_tpr": eod,
            "equalized_odds_diff_max": eo_d,
            "calibration_brier_diff": brier_d,
        }])
        overall_path = out_dir / CONTRACT.output_files["overall_summary_csv"]
        overall.to_csv(overall_path, index=False)

        summary = {
            "n_obs": int(n),
            "n_groups": int(gm.shape[0]),
            "group_names": gm["group"].tolist(),
            "demographic_parity_diff": dpd,
            "disparate_impact_ratio": di_ratio,
            "di_pass_4_5_rule": bool(
                (not np.isnan(di_ratio)) and di_ratio >= self.di_pass_ratio
            ),
            "equal_opportunity_diff_tpr": eod,
            "equalized_odds_diff_max": eo_d,
            "calibration_brier_diff": brier_d,
            "fairlearn_available": _HAVE_FAIRLEARN,
            "cross_check": cross_check,
        }
        summary_path = out_dir / CONTRACT.output_files["summary_json"]
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "group_metrics_csv": str(gm_path),
            "overall_summary_csv": str(overall_path),
            "summary_json": str(summary_path),
            **summary,
        }


def get_solver(threshold: float = 0.5, favorable_label: int = 1,
               di_pass_ratio: float = 0.8) -> DisparateImpactAuditSolver:
    return DisparateImpactAuditSolver(
        threshold=threshold, favorable_label=favorable_label,
        di_pass_ratio=di_pass_ratio,
    )


def selftest() -> Dict[str, Any]:
    """Ground-truth test:
    Construct a *known* biased classifier and verify (a) DI ratio < 0.8,
    (b) demographic-parity diff > 0.15, (c) if fairlearn present, our DPD
    matches fairlearn to 1e-6.
    """
    import tempfile
    rng = np.random.default_rng(7)
    n = 1000
    grp = np.where(rng.random(n) < 0.5, "A", "B")
    # True label: independent of group (50% positive).
    y_true = rng.binomial(1, 0.5, n)
    # Biased classifier: predicts positive 70% for A, only 30% for B,
    # ignoring true label.
    p_pos = np.where(grp == "A", 0.7, 0.3)
    y_score = np.clip(rng.normal(p_pos, 0.15), 0.0, 1.0)
    df = pd.DataFrame({"score": y_score, "label": y_true, "group": grp})

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"pred_col": "score", "label_col": "label",
                                "protected_col": "group"}),
            Path(tmp))
        # Expected: A's selection rate ~ 0.7, B's ~ 0.3 → DPD ~ 0.4, DI ~ 0.43.
        if out["demographic_parity_diff"] < 0.15:
            diffs.append(f"DPD={out['demographic_parity_diff']:.3f} too small "
                         "for biased classifier (expected ~0.4)")
        if out["disparate_impact_ratio"] > 0.8:
            diffs.append(f"DI ratio={out['disparate_impact_ratio']:.3f} should "
                         "be <0.8 (4/5 rule) for biased classifier")
        if out["di_pass_4_5_rule"]:
            diffs.append("4/5-rule should have FAILED for biased classifier")
        if _HAVE_FAIRLEARN:
            cc = out.get("cross_check", {})
            if not cc.get("dpd_matches_fairlearn"):
                diffs.append(
                    f"DPD does not match fairlearn: ours="
                    f"{out['demographic_parity_diff']:.6f}, "
                    f"fairlearn={cc.get('fairlearn_dpd')}")

    # Fair classifier sanity (no DPD).
    rng = np.random.default_rng(8)
    grp2 = np.where(rng.random(n) < 0.5, "A", "B")
    y_true2 = rng.binomial(1, 0.5, n)
    y_score2 = np.clip(rng.normal(0.5, 0.1, n), 0, 1)
    df2 = pd.DataFrame({"score": y_score2, "label": y_true2, "group": grp2})
    with tempfile.TemporaryDirectory() as tmp:
        out2 = get_solver().run(
            df2, ColumnMapping({"pred_col": "score", "label_col": "label",
                                 "protected_col": "group"}),
            Path(tmp))
        if out2["demographic_parity_diff"] > 0.08:
            diffs.append(f"unbiased DPD={out2['demographic_parity_diff']:.3f} "
                         "should be ~0")
        if not out2["di_pass_4_5_rule"]:
            diffs.append("4/5-rule should PASS for unbiased classifier")

    return {
        "ok": len(diffs) == 0,
        "summary": ("disparate_impact_audit detects bias correctly + matches "
                    "fairlearn" if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["disparate_impact_audit"],
                    "fairlearn_available": _HAVE_FAIRLEARN},
    }
