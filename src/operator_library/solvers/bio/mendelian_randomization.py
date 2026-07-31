"""Mendelian Randomization - IVW / MR-Egger / Weighted Median.

Wraps the R MendelianRandomization package (Yavorska & Burgess 2017, IJE)
via rpy2. Uses genetic variants (SNPs) as instrumental variables to estimate
the causal effect of an exposure X on outcome Y, excluding confounding.
Input: harmonized GWAS summary statistics (beta + SE for exposure and outcome).

References:
- Yavorska OO, Burgess S (2017) Int J Epidemiol 46:1734
- Bowden J et al. (2015) Int J Epidemiol 44:512 (MR-Egger)
- Bowden J et al. (2016) Genet Epidemiol 40:304 (Weighted Median)
- Burgess S et al. (2013) Genet Epidemiol 37:658 (IVW)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract

CONTRACT = SolverContract(
    name="mendelian_randomization",
    capability="F12_association_comorbidity_pattern",
    description=(
        "Mendelian Randomization using IVW, MR-Egger, and Weighted Median "
        "via the R MendelianRandomization package (Yavorska & Burgess 2017). "
        "Estimates causal effect of exposure on outcome using GWAS summary "
        "statistics. Input: harmonized CSV with beta_exposure, se_exposure, "
        "beta_outcome, se_outcome columns. Output: mr_results.csv."
    ),
    roles={
        "harmonized_data_csv": RoleSpec(
            Role.PARAMS,
            "Path to harmonized GWAS summary stats CSV with columns: "
            "beta_exposure, se_exposure, beta_outcome, se_outcome",
        ),
        "beta_exposure_col": RoleSpec(
            Role.PARAMS,
            "Column name for SNP-exposure beta. Default: beta_exposure",
            optional=True,
        ),
        "se_exposure_col": RoleSpec(
            Role.PARAMS,
            "Column name for SNP-exposure SE. Default: se_exposure",
            optional=True,
        ),
        "beta_outcome_col": RoleSpec(
            Role.PARAMS,
            "Column name for SNP-outcome beta. Default: beta_outcome",
            optional=True,
        ),
        "se_outcome_col": RoleSpec(
            Role.PARAMS,
            "Column name for SNP-outcome SE. Default: se_outcome",
            optional=True,
        ),
    },
    static_params={"random_state": 42},
    output_files={"mr_results_csv": "mr_results.csv"},
    output_kind={"mr_results_csv": "s"},
)

# Slot names differ by method class: IVW, Egger, WeightedMedian
_IVW_SLOTS = {"Estimate": "Estimate", "StdError": "StdError",
              "CILower": "CILower", "CIUpper": "CIUpper", "Pvalue": "Pvalue"}
_EGGER_SLOTS = {"Estimate": "Estimate", "StdError": "StdError.Est",
                "CILower": "CILower.Est", "CIUpper": "CIUpper.Est",
                "Pvalue": "Pvalue.Est", "Intercept": "Intercept",
                "InterceptSE": "StdError.Int", "InterceptP": "Pvalue.Int"}
_MEDIAN_SLOTS = {"Estimate": "Estimate", "StdError": "StdError",
                 "CILower": "CILower", "CIUpper": "CIUpper", "Pvalue": "Pvalue"}


def _extract_slot(res_var: str, slot_name: str) -> float:
    """Extract a numeric slot from an R S4 object via rpy2."""
    import rpy2.robjects as ro
    return float(ro.r(res_var + "@" + slot_name)[0])


class MendelianRandomizationSolver:
    contract = CONTRACT

    def __init__(self, random_state: int = 42):
        self.random_state = random_state

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        import os
        import rpy2.robjects as ro
        from rpy2.robjects import pandas2ri, globalenv
        from rpy2.robjects.packages import importr

        csv_path = mapping.get("harmonized_data_csv")
        if csv_path:
            df = pd.read_csv(csv_path)

        bx_col = mapping.get("beta_exposure_col") or "beta_exposure"
        sx_col = mapping.get("se_exposure_col") or "se_exposure"
        by_col = mapping.get("beta_outcome_col") or "beta_outcome"
        sy_col = mapping.get("se_outcome_col") or "se_outcome"

        sub = df[[bx_col, sx_col, by_col, sy_col]].dropna().copy()
        n = len(sub)
        if n < 3:
            raise ValueError(f"Need at least 3 IVs, got {n}")

        bx = sub[bx_col].values.astype(float)
        sx = sub[sx_col].values.astype(float)
        by = sub[by_col].values.astype(float)
        sy = sub[sy_col].values.astype(float)

        try:
            from configs.config import R_LIBS_USER as _R_LIBS_USER
        except Exception:
            _R_LIBS_USER = str(Path(__file__).parents[4] / "Rlibrary")
        os.environ["R_LIBS_USER"] = _R_LIBS_USER
        globalenv["r_url"] = _R_LIBS_USER
        ro.r(".libPaths(r_url)")
        pandas2ri.activate()

        try:
            importr("MendelianRandomization")
        except Exception as e:
            raise ImportError(
                f"R package MendelianRandomization not available: {e}"
            )

        globalenv["bx_vec"] = ro.FloatVector(bx.tolist())
        globalenv["sx_vec"] = ro.FloatVector(sx.tolist())
        globalenv["by_vec"] = ro.FloatVector(by.tolist())
        globalenv["sy_vec"] = ro.FloatVector(sy.tolist())
        ro.r("mr_obj <- mr_input(bx = bx_vec, bxse = sx_vec, by = by_vec, byse = sy_vec)")

        results = {}

        # --- IVW ---
        try:
            ro.r("ivw_res <- mr_ivw(mr_obj)")
            results["ivw"] = {
                "estimate": _extract_slot("ivw_res", _IVW_SLOTS["Estimate"]),
                "se": _extract_slot("ivw_res", _IVW_SLOTS["StdError"]),
                "pvalue": _extract_slot("ivw_res", _IVW_SLOTS["Pvalue"]),
                "ci_low": _extract_slot("ivw_res", _IVW_SLOTS["CILower"]),
                "ci_high": _extract_slot("ivw_res", _IVW_SLOTS["CIUpper"]),
                "method_full_name": "IVW (Inverse Variance Weighted)",
            }
        except Exception as e:
            results["ivw"] = {"error": str(e)[:200], "estimate": None,
                              "se": None, "pvalue": None, "ci_low": None,
                              "ci_high": None, "method_full_name": "IVW (failed)"}

        # --- MR-Egger ---
        try:
            ro.r("egger_res <- mr_egger(mr_obj)")
            results["mr_egger"] = {
                "estimate": _extract_slot("egger_res", _EGGER_SLOTS["Estimate"]),
                "se": _extract_slot("egger_res", _EGGER_SLOTS["StdError"]),
                "pvalue": _extract_slot("egger_res", _EGGER_SLOTS["Pvalue"]),
                "ci_low": _extract_slot("egger_res", _EGGER_SLOTS["CILower"]),
                "ci_high": _extract_slot("egger_res", _EGGER_SLOTS["CIUpper"]),
                "intercept": _extract_slot("egger_res", _EGGER_SLOTS["Intercept"]),
                "intercept_se": _extract_slot("egger_res", _EGGER_SLOTS["InterceptSE"]),
                "intercept_pvalue": _extract_slot("egger_res", _EGGER_SLOTS["InterceptP"]),
                "horizontal_pleiotropy_detected": bool(
                    _extract_slot("egger_res", _EGGER_SLOTS["InterceptP"]) < 0.05),
                "method_full_name": "MR-Egger (intercept tests horizontal pleiotropy)",
            }
        except Exception as e:
            results["mr_egger"] = {"error": str(e)[:200], "estimate": None,
                                   "se": None, "pvalue": None, "ci_low": None,
                                   "ci_high": None, "intercept": None,
                                   "intercept_se": None, "intercept_pvalue": None,
                                   "horizontal_pleiotropy_detected": False,
                                   "method_full_name": "MR-Egger (failed)"}

        # --- Weighted Median ---
        try:
            ro.r("med_res <- mr_median(mr_obj)")
            results["weighted_median"] = {
                "estimate": _extract_slot("med_res", _MEDIAN_SLOTS["Estimate"]),
                "se": _extract_slot("med_res", _MEDIAN_SLOTS["StdError"]),
                "pvalue": _extract_slot("med_res", _MEDIAN_SLOTS["Pvalue"]),
                "ci_low": _extract_slot("med_res", _MEDIAN_SLOTS["CILower"]),
                "ci_high": _extract_slot("med_res", _MEDIAN_SLOTS["CIUpper"]),
                "method_full_name": "Weighted Median",
            }
        except Exception as e:
            results["weighted_median"] = {"error": str(e)[:200], "estimate": None,
                                          "se": None, "pvalue": None,
                                          "ci_low": None, "ci_high": None,
                                          "method_full_name": "Weighted Median (failed)"}

        results["n_snps"] = int(n)

        # Consistency check
        ests = []
        for m in ["ivw", "mr_egger", "weighted_median"]:
            e = results[m].get("estimate")
            if e is not None:
                ests.append(e)
        if len(ests) >= 2:
            signs = [np.sign(e) for e in ests]
            same_sign = all(s == signs[0] for s in signs)
            max_diff = max(ests) - min(ests)
            if same_sign and max_diff < 0.5 * abs(np.mean(ests) + 1e-9):
                results["consistency"] = "consistent"
            elif not same_sign:
                results["consistency"] = "inconsistent_sign"
            else:
                results["consistency"] = "inconsistent_magnitude"
        else:
            results["consistency"] = "insufficient_methods"

        # Build output CSV
        out_rows = []
        for method in ["ivw", "mr_egger", "weighted_median"]:
            r = results[method]
            row = {
                "method": method,
                "estimate": r.get("estimate"),
                "se": r.get("se"),
                "pvalue": r.get("pvalue"),
                "ci_low": r.get("ci_low"),
                "ci_high": r.get("ci_high"),
                "method_full_name": r.get("method_full_name", ""),
            }
            if method == "mr_egger":
                row["intercept"] = r.get("intercept")
                row["intercept_se"] = r.get("intercept_se")
                row["intercept_pvalue"] = r.get("intercept_pvalue")
            out_rows.append(row)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / CONTRACT.output_files["mr_results_csv"]
        pd.DataFrame(out_rows).to_csv(out_path, index=False)

        results["mr_results_csv"] = str(out_path)
        return results


def get_solver(random_state: int = 42):
    return MendelianRandomizationSolver(random_state=random_state)


def selftest():
    """Generate synthetic GWAS data and verify IVW recovers true causal effect
    via the R MendelianRandomization package."""
    import tempfile
    rng = np.random.default_rng(42)
    n_snps = 50
    true_causal = 0.4
    bx = rng.normal(0.2, 0.15, n_snps)
    sx = np.full(n_snps, 0.03)
    by = true_causal * bx + rng.normal(0, 0.05, n_snps)
    sy = np.full(n_snps, 0.04)
    df = pd.DataFrame({
        "beta_exposure": bx, "se_exposure": sx,
        "beta_outcome": by, "se_outcome": sy,
    })

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_p = tmp / "harmonized.csv"
        df.to_csv(csv_p, index=False)
        s = get_solver()
        out = s.run(df, ColumnMapping({"harmonized_data_csv": str(csv_p)}), tmp)

        ivw = out.get("ivw", {})
        if ivw.get("estimate") is not None:
            if abs(ivw["estimate"] - true_causal) > 0.15:
                diffs.append(
                    "IVW estimate {:.4f} far from true 0.4".format(ivw["estimate"]))
            if ivw["pvalue"] is not None and ivw["pvalue"] > 0.05:
                diffs.append(
                    "IVW p-value {:.6f} not significant".format(ivw["pvalue"]))
            if (ivw["ci_low"] is not None and ivw["ci_high"] is not None
                    and not (ivw["ci_low"] <= true_causal <= ivw["ci_high"])):
                diffs.append(
                    "IVW CI [{:.4f}, {:.4f}] does not cover 0.4".format(
                        ivw["ci_low"], ivw["ci_high"]))
        else:
            diffs.append("IVW method failed: {}".format(ivw.get("error", "unknown")))

        n_ok = sum(1 for m in ["ivw", "mr_egger", "weighted_median"]
                   if out.get(m, {}).get("estimate") is not None)
        if n_ok < 2:
            diffs.append("Only {}/3 methods succeeded".format(n_ok))

        # Determinism check
        out2 = s.run(df, ColumnMapping({"harmonized_data_csv": str(csv_p)}), tmp)
        for method in ["ivw", "mr_egger", "weighted_median"]:
            e1 = out.get(method, {}).get("estimate")
            e2 = out2.get(method, {}).get("estimate")
            if e1 is not None and e2 is not None and abs(e1 - e2) > 1e-14:
                diffs.append(
                    "{} not deterministic: {:.15f} vs {:.15f}".format(method, e1, e2))

    return {
        "ok": len(diffs) == 0,
        "summary": (
            "MR via R MendelianRandomization recovers true causal effect"
            if not diffs else "{} mismatch(es): {}".format(len(diffs), "; ".join(diffs[:3]))
        ),
        "details": {"diffs": diffs, "tested": ["mendelian_randomization"]},
    }
