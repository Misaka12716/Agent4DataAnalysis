"""W27 — Joint longitudinal + survival model (two-stage approximation).

True joint models (R: ``JM``, ``joineR``, ``JMbayes2``) maximise a single
likelihood over both the longitudinal mixed-effect submodel and the Cox
survival submodel, sharing subject-specific random effects.  In Python this
is non-trivial because no first-class library exists.

We implement the **standard two-stage estimator** (Tsiatis & Davidian 2004
*Stat Sin*):

  Stage 1: Fit linear mixed-effects model on the longitudinal data:
              y_ij = (b0 + u0_i) + (b1 + u1_i) * t_ij + eps_ij
           Extract subject-specific BLUPs (u0_i, u1_i) and the predicted
           trajectory m_i(t) = (b0 + u0_i) + (b1 + u1_i) * t.

  Stage 2: Fit Cox proportional-hazards model on time-to-event with
              the subject's current biomarker level m_i(T-) as a
              time-varying covariate (or, simpler variant, the baseline
              estimate m_i(0) and the slope u1_i as two fixed covariates).

The two-stage method is known to slightly under-estimate the
association parameter (alpha) compared to the joint likelihood, but is
asymptotically consistent under the assumption of correct longitudinal
model and gives standard-error estimates within ~10% of full JM
(Sweeting & Thompson 2011).

Inputs
------
- Long longitudinal CSV: ``id, time, y`` + optional covariates.
- Survival CSV (one row per subject): ``id, event_time, event`` + cov.

Outputs
-------
- longitudinal_params.csv  — fixed and random effects, residual variance
- survival_params.csv      — Cox coefficients including association alpha
- subject_blups.csv        — (intercept, slope) per subject
- summary.json             — convergence + key numbers

References
----------
- Tsiatis AA & Davidian M (2004) "Joint modeling of longitudinal and
  time-to-event data: an overview" *Stat Sin* 14:809.
- Sweeting MJ, Thompson SG (2011) "Joint modelling of longitudinal and
  time-to-event data..." *Biom J* 53:750.
- Rizopoulos D (2012) *Joint Models for Longitudinal and Time-to-Event
  Data* §3 (two-stage section).
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from lifelines import CoxPHFitter

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="joint_longitudinal_survival",
    capability="F_joint_long_surv",
    description=(
        "Two-stage joint model: stage-1 LMM (random intercept + slope) "
        "on long biomarker CSV, then stage-2 Cox using each subject's "
        "stage-1 baseline + slope as fixed covariates (Tsiatis & Davidian "
        "2004 approximation)."
    ),
    roles={
        "long_id_col": RoleSpec(Role.ID, "Subject ID in the longitudinal data"),
        "long_time_col": RoleSpec(Role.NUMERIC,
                                    "Time of repeat measurement"),
        "long_y_col": RoleSpec(Role.NUMERIC,
                                "Repeated-measure outcome (biomarker)"),
        "surv_id_col": RoleSpec(Role.ID,
                                  "Subject ID in the survival data (same IDs "
                                  "as long_id_col)"),
        "event_time_col": RoleSpec(Role.TIME_TO_EVENT,
                                     "Time to event or censoring"),
        "event_col": RoleSpec(Role.EVENT_INDICATOR,
                                "0=censored, 1=event"),
        "surv_covariates": RoleSpec(Role.NUMERIC_LIST,
                                      "Extra survival covariates",
                                      optional=True),
        "long_covariates": RoleSpec(Role.NUMERIC_LIST,
                                      "Extra longitudinal covariates",
                                      optional=True),
        "surv_data": RoleSpec(Role.PARAMS,
                                "DataFrame of survival data; pass via "
                                "mapping override.  If absent, run "
                                "expects the longitudinal df to *also* "
                                "have event_time + event columns.",
                                optional=True),
    },
    static_params={
        "min_obs_per_subject": 2,
        "random_state": 42,
    },
    output_files={
        "longitudinal_params_csv": "joint_longitudinal_params.csv",
        "survival_params_csv": "joint_survival_params.csv",
        "subject_blups_csv": "joint_subject_blups.csv",
        "summary_json": "joint_summary.json",
    },
    output_kind={
        "longitudinal_params_csv": "s",
        "survival_params_csv": "s",
        "subject_blups_csv": "t",  # one row per subject
        "summary_json": "s",
    },
)


class JointLongSurvSolver:
    contract = CONTRACT

    def __init__(self, min_obs_per_subject: int = 2,
                 random_state: int = 42):
        self.min_obs_per_subject = int(min_obs_per_subject)
        self.random_state = int(random_state)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        long_id = mapping.get("long_id_col")
        long_t = mapping.get("long_time_col")
        long_y = mapping.get("long_y_col")
        surv_id = mapping.get("surv_id_col")
        evt_time = mapping.get("event_time_col")
        evt = mapping.get("event_col")
        surv_cov = list(mapping.get("surv_covariates") or [])
        long_cov = list(mapping.get("long_covariates") or [])
        surv_data = mapping.get("surv_data")

        if not all([long_id, long_t, long_y, evt_time, evt]):
            raise ValueError("long_id_col, long_time_col, long_y_col, "
                              "event_time_col, event_col are required")

        # Longitudinal df.
        long_df = df[[long_id, long_t, long_y] + long_cov].dropna().copy()
        long_df.columns = ["__id__", "__t__", "__y__"] + long_cov
        long_df["__id__"] = long_df["__id__"].astype(str)
        long_df["__t__"] = long_df["__t__"].astype(float)
        long_df["__y__"] = long_df["__y__"].astype(float)

        # Survival df: either an explicit DataFrame in mapping, or extract from df.
        if surv_data is not None:
            sdf = surv_data.copy() if isinstance(surv_data, pd.DataFrame) else pd.DataFrame(surv_data)
            sdf = sdf[[surv_id, evt_time, evt] + surv_cov].dropna().copy()
            sdf.columns = ["__id__", "__T__", "__E__"] + surv_cov
        else:
            need_cols = [long_id, evt_time, evt] + surv_cov
            for c in need_cols:
                if c not in df.columns:
                    raise ValueError(f"missing survival column {c!r}; pass "
                                      "surv_data=DataFrame via mapping or "
                                      "ensure {evt_time}/{evt} are in df")
            sdf = (df[[long_id, evt_time, evt] + surv_cov]
                   .dropna()
                   .drop_duplicates(subset=[long_id])
                   .copy())
            sdf.columns = ["__id__", "__T__", "__E__"] + surv_cov

        sdf["__id__"] = sdf["__id__"].astype(str)
        sdf["__T__"] = sdf["__T__"].astype(float)
        sdf["__E__"] = sdf["__E__"].astype(int)

        # Filter subjects with enough longitudinal obs.
        counts = long_df.groupby("__id__").size()
        kept = counts[counts >= self.min_obs_per_subject].index
        long_df = long_df[long_df["__id__"].isin(kept)].copy()
        n_subj = long_df["__id__"].nunique()
        if n_subj < 30:
            raise ValueError(f"only {n_subj} eligible subjects; need >=30")

        # Stage 1: LMM y ~ t + long_cov, random ~ t | id.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            formula = "__y__ ~ __t__"
            if long_cov:
                formula = formula + " + " + " + ".join(long_cov)
            lmm = smf.mixedlm(formula, data=long_df, groups=long_df["__id__"],
                              re_formula="~__t__")
            lmm_fit = lmm.fit(method="lbfgs", reml=True, maxiter=200)
        fix_int = float(lmm_fit.params.get("Intercept", 0.0))
        fix_slope = float(lmm_fit.params.get("__t__", 0.0))
        cov_re = np.asarray(lmm_fit.cov_re)
        blups = lmm_fit.random_effects
        blup_rows: List[Dict[str, Any]] = []
        for sid, vec in blups.items():
            v = np.asarray(vec)
            blup_rows.append({
                "subject_id": str(sid),
                "intercept_blup": fix_int + float(v[0]),
                "slope_blup": fix_slope + float(v[1]) if v.size > 1 else fix_slope,
                "intercept_offset": float(v[0]),
                "slope_offset": float(v[1]) if v.size > 1 else 0.0,
            })
        blup_df = pd.DataFrame(blup_rows).sort_values("subject_id")

        # Longitudinal params table.
        long_rows: List[Dict[str, Any]] = []
        for name in lmm_fit.params.index:
            if name.startswith("Group ") or name.endswith(" Var") or " x " in name:
                continue
            beta = float(lmm_fit.params[name])
            try:
                se = float(lmm_fit.bse[name])
                p = float(lmm_fit.pvalues[name])
            except Exception:
                se, p = float("nan"), float("nan")
            long_rows.append({
                "param": name, "value": beta, "se": se, "p_value": p,
            })
        long_rows.append({"param": "var_intercept",
                          "value": float(cov_re[0, 0]),
                          "se": None, "p_value": None})
        if cov_re.shape[0] > 1:
            long_rows.append({"param": "var_slope",
                              "value": float(cov_re[1, 1]),
                              "se": None, "p_value": None})
            long_rows.append({"param": "cov_intercept_slope",
                              "value": float(cov_re[0, 1]),
                              "se": None, "p_value": None})
        long_rows.append({"param": "var_residual",
                          "value": float(lmm_fit.scale),
                          "se": None, "p_value": None})
        long_params_df = pd.DataFrame(long_rows)

        # Stage 2: Cox PH with subject baseline + slope BLUPs as covariates.
        cox_df = sdf.merge(blup_df[["subject_id", "intercept_blup",
                                     "slope_blup"]],
                            left_on="__id__", right_on="subject_id",
                            how="inner")
        cox_df = cox_df.drop(columns=["subject_id"]).copy()
        cph = CoxPHFitter()
        cox_cols = ["intercept_blup", "slope_blup"] + surv_cov
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cph.fit(cox_df[["__T__", "__E__"] + cox_cols],
                    duration_col="__T__", event_col="__E__")
        surv_params_df = cph.summary.reset_index().rename(columns={
            "covariate": "param", "coef": "beta", "exp(coef)": "hazard_ratio",
            "se(coef)": "se", "p": "p_value", "z": "z",
            "coef lower 95%": "ci_low", "coef upper 95%": "ci_high",
        })

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        lp_path = out_dir / CONTRACT.output_files["longitudinal_params_csv"]
        sp_path = out_dir / CONTRACT.output_files["survival_params_csv"]
        bl_path = out_dir / CONTRACT.output_files["subject_blups_csv"]
        sm_path = out_dir / CONTRACT.output_files["summary_json"]
        long_params_df.to_csv(lp_path, index=False)
        surv_params_df.to_csv(sp_path, index=False)
        blup_df.to_csv(bl_path, index=False)

        # Extract key association params.
        try:
            alpha_int = float(cph.summary.loc["intercept_blup", "coef"])
            alpha_slope = float(cph.summary.loc["slope_blup", "coef"])
            p_int = float(cph.summary.loc["intercept_blup", "p"])
            p_slope = float(cph.summary.loc["slope_blup", "p"])
        except Exception:
            alpha_int, alpha_slope, p_int, p_slope = (None, None, None, None)

        summary = {
            "n_subjects": int(n_subj),
            "n_long_obs": int(len(long_df)),
            "n_events": int((cox_df["__E__"] == 1).sum()),
            "longitudinal_intercept_mean": fix_int,
            "longitudinal_slope_mean": fix_slope,
            "longitudinal_var_intercept": float(cov_re[0, 0]),
            "longitudinal_var_slope": float(cov_re[1, 1])
                if cov_re.shape[0] > 1 else 0.0,
            "longitudinal_var_residual": float(lmm_fit.scale),
            "alpha_intercept_to_hazard": alpha_int,
            "alpha_slope_to_hazard": alpha_slope,
            "p_alpha_intercept": p_int,
            "p_alpha_slope": p_slope,
            "method": "two-stage (Tsiatis & Davidian 2004)",
            "concordance": float(cph.concordance_index_),
        }
        sm_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "longitudinal_params_csv": str(lp_path),
            "survival_params_csv": str(sp_path),
            "subject_blups_csv": str(bl_path),
            "summary_json": str(sm_path),
            **summary,
        }


def get_solver(min_obs_per_subject: int = 2,
               random_state: int = 42) -> JointLongSurvSolver:
    return JointLongSurvSolver(min_obs_per_subject=min_obs_per_subject,
                                random_state=random_state)


def selftest() -> Dict[str, Any]:
    """Ground-truth: simulate biomarker trajectory + survival driven by
    subject slope.  Verify (a) recovered longitudinal fixed effects close
    to truth, (b) Cox slope-coefficient alpha is significantly negative
    (faster biomarker rise = higher hazard)."""
    import tempfile
    rng = np.random.default_rng(42)
    N = 300
    Tvis = 5
    true_int_mu, true_int_sd = 50.0, 8.0
    true_slope_mu, true_slope_sd = 2.0, 1.0
    long_rows = []
    surv_rows = []
    for sid in range(N):
        a_i = rng.normal(true_int_mu, true_int_sd)
        b_i = rng.normal(true_slope_mu, true_slope_sd)
        for t in range(Tvis):
            y = a_i + b_i * t + rng.normal(0, 3.0)
            long_rows.append({"id": f"S{sid:04d}", "time": t, "y": y})
        # Survival: hazard proportional to subject slope b_i.
        #   lambda_i = 0.05 * exp(0.6 * b_i)
        lam = 0.05 * np.exp(0.6 * b_i)
        T_event = rng.exponential(1.0 / lam)
        T_censor = rng.uniform(2.0, 12.0)
        T_obs = min(T_event, T_censor)
        E = int(T_event <= T_censor)
        surv_rows.append({"id": f"S{sid:04d}", "event_time": T_obs, "event": E})

    long_df = pd.DataFrame(long_rows)
    surv_df = pd.DataFrame(surv_rows)

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        mapping = ColumnMapping({
            "long_id_col": "id", "long_time_col": "time", "long_y_col": "y",
            "surv_id_col": "id", "event_time_col": "event_time",
            "event_col": "event", "surv_data": surv_df,
        })
        out = get_solver().run(long_df, mapping, Path(tmp))
        if abs(out["longitudinal_intercept_mean"] - true_int_mu) > 2.0:
            diffs.append(f"long int mean={out['longitudinal_intercept_mean']:.2f} "
                         f"far from {true_int_mu}")
        if abs(out["longitudinal_slope_mean"] - true_slope_mu) > 0.30:
            diffs.append(f"long slope mean={out['longitudinal_slope_mean']:.3f} "
                         f"far from {true_slope_mu}")
        # Slope -> hazard should be significantly positive.
        a_s = out["alpha_slope_to_hazard"]
        p_s = out["p_alpha_slope"]
        if a_s is None or a_s < 0.2:
            diffs.append(f"alpha(slope→hazard)={a_s} should be >0.2 (planted 0.6)")
        if p_s is None or p_s > 0.01:
            diffs.append(f"p(alpha slope)={p_s} should be <0.01 for strong assoc")
        if out["n_events"] < 30:
            diffs.append(f"too few events: {out['n_events']}")

    return {
        "ok": len(diffs) == 0,
        "summary": ("joint two-stage recovers longitudinal effects and "
                    "biomarker→hazard association"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["joint_longitudinal_survival"]},
    }
