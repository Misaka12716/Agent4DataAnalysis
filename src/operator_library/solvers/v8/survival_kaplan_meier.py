"""Kaplan–Meier survival curves + log-rank group comparison.

Complements the existing ``cox_regression`` solver: KM gives the *raw*
non-parametric survival curve and median survival time (with 95% CI)
for each group, plus the log-rank p-value testing equality of two
groups' survival distributions.

Backed by ``lifelines.KaplanMeierFitter`` and ``lifelines.statistics.
logrank_test``  (Greenwood variance for CIs; matches R's ``survfit``
output to 4 decimals on standard tests).

References
----------
- Kaplan EL & Meier P (1958) "Nonparametric estimation from incomplete
  observations" *JASA* 53:457-481.
- Mantel N (1966) "Evaluation of survival data and two new rank order
  statistics arising in its consideration" *Cancer Chemother Rep* 50:163.
- Davidson-Pilon (2019) "lifelines: survival analysis in Python"
  *JOSS* 4:1317.

Outputs
-------
- ``km_curve.csv``
  long form (timeline, group, survival_prob, ci_low, ci_high, n_at_risk).
- ``km_summary.csv``
  one row per group with (median_survival, median_ci_low, median_ci_high,
  n_obs, n_events).
- ``km_summary.json``  (also includes logrank_p when group_col supplied).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from ._inputs import (
    coerce_numeric_friendly, coerce_event_binary, detect_column_kind,
)


CONTRACT = SolverContract(
    name="survival_kaplan_meier",
    capability="F_survival_km",
    description=(
        "Kaplan–Meier non-parametric survival curve(s) with 95% CI "
        "(Greenwood variance) and median survival time + CI per group. "
        "When a binary ``group_col`` is supplied, also runs the Mantel "
        "log-rank test for equality of the two groups' survival "
        "distributions. Lighter-weight than Cox: use when you only need "
        "the raw curves / median survival / two-group equality test "
        "(no covariate adjustment)."
    ),
    roles={
        "time_col":  RoleSpec(Role.TIME_TO_EVENT,
                                "time to event/censoring"),
        "event_col": RoleSpec(Role.EVENT_INDICATOR,
                                "1 = event observed, 0 = censored"),
        "group_col": RoleSpec(Role.BINARY_TARGET,
                                "optional binary group label "
                                "(e.g. treatment vs control); "
                                "when supplied, log-rank test is added.",
                                optional=True),
    },
    static_params={
        "alpha": 0.05,   # 1-α confidence intervals
    },
    output_files={
        "curve_csv":   "km_curve.csv",
        "summary_csv": "km_summary.csv",
        "summary_json": "km_summary.json",
    },
    output_kind={"curve_csv": "t", "summary_csv": "s", "summary_json": "s"},
)


class SurvivalKaplanMeierSolver:
    contract = CONTRACT

    def __init__(self, alpha: float = 0.05):
        self.alpha = float(alpha)

    def _fit_one(self, t, e, label: str, alpha: float):
        """Fit KM for one group and return (curve_df, summary_dict)."""
        from lifelines import KaplanMeierFitter
        kmf = KaplanMeierFitter(alpha=alpha)
        kmf.fit(durations=t, event_observed=e, label=label)
        # kmf.survival_function_ has 1 column (label), index = timeline
        sf = kmf.survival_function_.copy()
        sf.columns = ["survival_prob"]
        ci = kmf.confidence_interval_.copy()
        ci.columns = ["ci_low", "ci_high"]
        # n_at_risk per timeline
        ev = kmf.event_table[["at_risk"]].copy()
        ev.columns = ["n_at_risk"]
        curve = sf.join(ci).join(ev)
        curve = curve.reset_index().rename(columns={"index": "timeline"})
        curve["group"] = label

        # Median survival + CI.
        try:
            med = float(kmf.median_survival_time_)
        except Exception:
            med = float("nan")
        try:
            from lifelines.utils import median_survival_times
            mci = median_survival_times(kmf.confidence_interval_)
            med_lo = float(mci.iloc[0, 0])
            med_hi = float(mci.iloc[0, 1])
        except Exception:
            med_lo, med_hi = float("nan"), float("nan")

        summary = {
            "group": label,
            "n_obs": int(len(t)),
            "n_events": int(np.asarray(e, dtype=int).sum()),
            "median_survival": med,
            "median_ci_low":   med_lo,
            "median_ci_high":  med_hi,
        }
        return curve, summary

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        from lifelines.statistics import logrank_test

        t_col = mapping["time_col"]
        e_col = mapping["event_col"]
        grp_col = mapping.get("group_col")

        needed = [t_col, e_col] + ([grp_col] if grp_col else [])
        for c in needed:
            if c not in df.columns:
                raise KeyError(f"KM: missing column {c!r} in df")
        sub = df[needed].copy()
        column_diagnostics: Dict[str, Any] = {}
        # ---- Time column robustness ----
        # Accept (in priority order):
        #   (1) timedelta (Timedelta dtype) — convert to days
        #   (2) datetime (DatetimeIndex / Timestamp / parseable string)
        #       → days since the per-row minimum
        #   (3) anything else → coerce_numeric_friendly (handles % $ , Int64)
        t_series = sub[t_col]
        if pd.api.types.is_timedelta64_dtype(t_series):
            sub[t_col] = t_series.dt.total_seconds() / 86400.0
        elif pd.api.types.is_datetime64_any_dtype(t_series):
            origin = t_series.min()
            sub[t_col] = (t_series - origin).dt.total_seconds() / 86400.0
        elif t_series.dtype == object:
            # Try datetime first; if <50% parse, fall back to numeric.
            dt = pd.to_datetime(t_series, errors="coerce")
            if dt.notna().sum() >= max(10, int(0.5 * len(t_series))):
                origin = dt.min()
                sub[t_col] = (dt - origin).dt.total_seconds() / 86400.0
            else:
                sub[t_col] = coerce_numeric_friendly(t_series)
        else:
            sub[t_col] = coerce_numeric_friendly(t_series)

        # ---- Event column: accept yes/no, alive/dead, T/F, 0/1, etc. ----
        column_diagnostics[e_col] = detect_column_kind(sub[e_col])
        sub[e_col] = coerce_event_binary(sub[e_col])

        # ---- Group column (optional): factorize any 2 unique labels ----
        if grp_col is not None:
            column_diagnostics[grp_col] = detect_column_kind(sub[grp_col])
            # Try numeric coercion (handles yes/no, T/F, 0/1, "75%").
            # Accept only if a meaningful fraction (>=50%) of rows
            # parsed AND all of them land in {0, 1}.  Otherwise we
            # factorize the original strings to integer codes.
            gnum = coerce_numeric_friendly(sub[grp_col], allow_boolean=True)
            gnn = gnum.dropna()
            uniq = (set(gnn.round().astype(int).unique())
                     if len(gnn) > 0 else set())
            # Numeric path: require (i) >=90% parsed, (ii) >=2 distinct
            # levels, (iii) all levels in {0,1}.  The 'one level only'
            # case happens when (e.g.) 'control' parses to 0 via a bool
            # token but 'treatment' fails — we MUST fall through to
            # string factorize, not silently lose the treatment arm.
            if (len(gnn) >= 0.9 * len(sub)
                    and len(uniq) >= 2 and uniq.issubset({0, 1})):
                sub[grp_col] = gnum.round()
            else:
                # Generic factorize on the raw strings.  sort=True →
                # 'control' < 'treatment' alphabetical → control=0,
                # treatment=1 (deterministic).
                codes, _ = pd.factorize(sub[grp_col].astype(str),
                                         sort=True)
                sub[grp_col] = pd.Series(codes, index=sub.index).astype(float)

        sub = sub.replace([np.inf, -np.inf], np.nan).dropna()
        sub = sub[sub[t_col] >= 0]
        sub[e_col] = sub[e_col].round().astype(int)
        if not set(sub[e_col].unique()).issubset({0, 1}):
            raise ValueError(f"KM: event column {e_col!r} must be 0/1 "
                              f"(got {sorted(sub[e_col].unique())[:5]})")
        n = len(sub)
        if n < 20:
            raise ValueError(f"KM: n={n} too small (need >=20)")

        curves: List[pd.DataFrame] = []
        summaries: List[Dict[str, Any]] = []
        logrank_p: Optional[float] = None

        if grp_col is None:
            cur, smy = self._fit_one(sub[t_col].values, sub[e_col].values,
                                        "ALL", self.alpha)
            curves.append(cur)
            summaries.append(smy)
        else:
            sub[grp_col] = sub[grp_col].astype(float).round().astype(int)
            uniq = sorted(sub[grp_col].unique())
            if len(uniq) < 2:
                raise ValueError(f"KM: group_col {grp_col!r} has only one "
                                  "level after dropna; need >=2 groups for "
                                  "a comparison")
            for g in uniq:
                gsub = sub[sub[grp_col] == g]
                cur, smy = self._fit_one(gsub[t_col].values,
                                            gsub[e_col].values,
                                            f"group_{int(g)}", self.alpha)
                curves.append(cur)
                summaries.append(smy)
            # Log-rank (two-sample if exactly 2 groups; else pairwise lib).
            if len(uniq) == 2:
                g0 = sub[sub[grp_col] == uniq[0]]
                g1 = sub[sub[grp_col] == uniq[1]]
                lr = logrank_test(g0[t_col], g1[t_col],
                                   event_observed_A=g0[e_col],
                                   event_observed_B=g1[e_col])
                logrank_p = float(lr.p_value)

        curve_df = pd.concat(curves, ignore_index=True)
        summary_df = pd.DataFrame(summaries)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cu_path = out_dir / CONTRACT.output_files["curve_csv"]
        su_path = out_dir / CONTRACT.output_files["summary_csv"]
        sj_path = out_dir / CONTRACT.output_files["summary_json"]
        curve_df.to_csv(cu_path, index=False)
        summary_df.to_csv(su_path, index=False)

        meta: Dict[str, Any] = {
            "n_obs":       int(n),
            "n_groups":    int(len(summaries)),
            "alpha":       float(self.alpha),
            "logrank_p":   (float(logrank_p) if logrank_p is not None
                              else None),
            "groups":      summaries,
            "column_diagnostics": column_diagnostics,
        }
        sj_path.write_text(json.dumps(meta, indent=2, default=str),
                            encoding="utf-8")

        return {
            "curve_csv":   str(cu_path),
            "summary_csv": str(su_path),
            "summary_json": str(sj_path),
            **meta,
        }


def get_solver(alpha: float = 0.05) -> SurvivalKaplanMeierSolver:
    return SurvivalKaplanMeierSolver(alpha=alpha)


# ---------------------------------------------------------------------------
# Ground-truth selftest
# ---------------------------------------------------------------------------
def _gt_a_two_group_exponential() -> List[str]:
    """GT-A — two groups of exponentials; median + S(t) + log-rank."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 2000
    t0 = rng.exponential(scale=100.0, size=n)
    t1 = rng.exponential(scale=200.0, size=n)
    e0 = (t0 < 600).astype(int); e1 = (t1 < 600).astype(int)
    t0 = np.minimum(t0, 600.0); t1 = np.minimum(t1, 600.0)
    df = pd.DataFrame({
        "time":  np.concatenate([t0, t1]),
        "event": np.concatenate([e0, e1]),
        "arm":   np.concatenate([np.zeros(n, dtype=int),
                                  np.ones(n, dtype=int)]),
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"time_col": "time", "event_col": "event",
                                "group_col": "arm"}), Path(tmp))
        smy_df = pd.read_csv(out["summary_csv"]).set_index("group")
        med0 = float(smy_df.loc["group_0", "median_survival"])
        med1 = float(smy_df.loc["group_1", "median_survival"])
        if abs(med0 - 100 * np.log(2)) > 5:
            diffs.append(f"[A] median(g0)={med0:.2f} vs analytic "
                          f"{100*np.log(2):.2f} (±5)")
        if abs(med1 - 200 * np.log(2)) > 10:
            diffs.append(f"[A] median(g1)={med1:.2f} vs analytic "
                          f"{200*np.log(2):.2f} (±10)")
        if out["logrank_p"] is None or out["logrank_p"] > 1e-50:
            diffs.append(f"[A] logrank_p={out['logrank_p']} too large")
        # S(t=100)
        curve = pd.read_csv(out["curve_csv"])
        for label, expected in [("group_0", float(np.exp(-100/100))),
                                  ("group_1", float(np.exp(-100/200)))]:
            sub = (curve[curve["group"] == label]
                   .sort_values("timeline"))
            sub_le = sub[sub["timeline"] <= 100]
            if sub_le.empty:
                diffs.append(f"[A] {label}: no timeline<=100")
                continue
            s_emp = float(sub_le.iloc[-1]["survival_prob"])
            if abs(s_emp - expected) > 0.03:
                diffs.append(f"[A] {label} S(100)={s_emp:.4f} vs "
                              f"analytic {expected:.4f} (±0.03)")
    return diffs


def _gt_b_single_group_weibull() -> List[str]:
    """GT-B — single group, Weibull(shape=1.5, scale=100).

    Analytic median for Weibull = scale * (ln 2)^(1/shape)
                                = 100 * (ln 2)^(1/1.5)
                                ≈ 100 * 0.7842 ≈ 78.42

    Strict assertions:
      - n_groups == 1
      - logrank_p is None (no two-group test possible)
      - median_survival within ±5 of 78.42
      - S(50) within ±0.04 of  exp(-(50/100)^1.5) ≈ 0.7022
    """
    import tempfile
    rng = np.random.default_rng(11)
    n = 3000
    shape, scale = 1.5, 100.0
    t = scale * rng.weibull(shape, size=n)
    e = (t < 600).astype(int)
    t = np.minimum(t, 600.0)
    df = pd.DataFrame({"time": t, "event": e})
    true_med = scale * (np.log(2) ** (1.0 / shape))
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"time_col": "time", "event_col": "event"}),
            Path(tmp))
        if out["n_groups"] != 1:
            diffs.append(f"[B] n_groups={out['n_groups']} expected 1")
        if out["logrank_p"] is not None:
            diffs.append(f"[B] logrank_p should be None for 1 group, "
                          f"got {out['logrank_p']}")
        smy = pd.read_csv(out["summary_csv"]).iloc[0]
        med = float(smy["median_survival"])
        if abs(med - true_med) > 5:
            diffs.append(f"[B] Weibull median={med:.2f} vs analytic "
                          f"{true_med:.2f} (±5)")
        curve = pd.read_csv(out["curve_csv"]).sort_values("timeline")
        sub = curve[curve["timeline"] <= 50]
        if not sub.empty:
            s_emp = float(sub.iloc[-1]["survival_prob"])
            s_an = float(np.exp(-((50 / scale) ** shape)))
            if abs(s_emp - s_an) > 0.04:
                diffs.append(f"[B] S(50)={s_emp:.4f} vs analytic "
                              f"{s_an:.4f} (±0.04)")
    return diffs


def _gt_c_datetime_input() -> List[str]:
    """GT-C — datetime time column (pd.Timestamp).  Convert to days
    internally and recover the same medians as the numeric case."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 1500
    t_days = rng.exponential(scale=100.0, size=n)
    e = (t_days < 600).astype(int)
    t_days = np.minimum(t_days, 600.0)
    base = pd.Timestamp("2020-01-01")
    df = pd.DataFrame({
        "event_time": [base + pd.Timedelta(days=float(x))
                        for x in t_days],
        "event":      e,
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"time_col": "event_time",
                                    "event_col": "event"}), Path(tmp))
        except Exception as e:
            diffs.append(f"[C] datetime input should be parsed, "
                          f"raised {type(e).__name__}: {e}")
            return diffs
        smy = pd.read_csv(out["summary_csv"]).iloc[0]
        med = float(smy["median_survival"])
        # Origin = min(event_time) = base+0 days, so median should be
        # ≈ 100*ln(2) ≈ 69.31 days.
        if abs(med - 100 * np.log(2)) > 6:
            diffs.append(f"[C] datetime median={med:.2f} vs analytic "
                          f"{100*np.log(2):.2f} (±6 days)")
    return diffs


def _gt_d_robustness() -> List[str]:
    """GT-D — input robustness: dtype, non-binary event, missing col."""
    import tempfile
    rng = np.random.default_rng(3)
    n = 300
    t = rng.exponential(scale=100.0, size=n)
    e = (rng.uniform(0, 1, n) < 0.7).astype(int)
    diffs: List[str] = []

    # (1) string-encoded numeric time + Int64 event
    df = pd.DataFrame({
        "time":  [f"{v:.3f}" for v in t],
        "event": pd.array(e.astype(np.int64), dtype="Int64"),
    })
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"time_col": "time", "event_col": "event"}),
                Path(tmp))
            assert out["n_obs"] > 0
        except Exception as e:
            diffs.append(f"[D-coerce] should accept string-num + Int64, "
                          f"raised {type(e).__name__}: {e}")

    # (2) non-binary event → should ValueError
    df2 = pd.DataFrame({"time": t, "event": np.arange(n) % 3})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            get_solver().run(df2,
                              ColumnMapping({"time_col": "time",
                                              "event_col": "event"}),
                              Path(tmp))
            diffs.append("[D-event] non-binary event should ValueError")
        except ValueError:
            pass
        except Exception as e:
            diffs.append(f"[D-event] expected ValueError, got "
                          f"{type(e).__name__}")

    # (3) missing column
    df3 = pd.DataFrame({"time": t})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            get_solver().run(df3,
                              ColumnMapping({"time_col": "time",
                                              "event_col": "event"}),
                              Path(tmp))
            diffs.append("[D-missing] missing event should KeyError")
        except KeyError:
            pass
        except Exception as e:
            diffs.append(f"[D-missing] expected KeyError, got "
                          f"{type(e).__name__}")
    return diffs


def _gt_e_medical_strings() -> List[str]:
    """GT-E — realistic medical data: event as 'alive'/'dead' strings,
    group as 'treatment'/'control' strings, time as days numeric.
    Must reproduce GT-A two-group exponential medians within tolerance."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 1500
    t0 = rng.exponential(scale=100.0, size=n)
    t1 = rng.exponential(scale=200.0, size=n)
    e0 = (t0 < 600).astype(int); e1 = (t1 < 600).astype(int)
    t0 = np.minimum(t0, 600.0); t1 = np.minimum(t1, 600.0)
    df = pd.DataFrame({
        "follow_up_days": np.concatenate([t0, t1]),
        # Survival convention: dead = event = 1, alive = censored = 0.
        "vital_status":   ["dead" if e else "alive"
                            for e in np.concatenate([e0, e1])],
        "arm":            (["control"] * n) + (["treatment"] * n),
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"time_col": "follow_up_days",
                                    "event_col": "vital_status",
                                    "group_col": "arm"}), Path(tmp))
        except Exception as e:
            diffs.append(f"[E] should accept 'alive'/'dead' + "
                          f"'control'/'treatment' strings, raised "
                          f"{type(e).__name__}: {e}")
            return diffs
        if out["n_groups"] != 2:
            diffs.append(f"[E] n_groups={out['n_groups']} expected 2")
        if out["logrank_p"] is None or out["logrank_p"] > 1e-30:
            diffs.append(f"[E] logrank_p={out['logrank_p']} should reject "
                          "(huge median split)")
        # Group factorization may swap labels (alphabetical: control=0,
        # treatment=1). Check both medians are recovered regardless of
        # which is g0/g1.
        smy = pd.read_csv(out["summary_csv"]).set_index("group")
        meds = sorted([float(smy.loc[g, "median_survival"])
                        for g in smy.index])
        if not (abs(meds[0] - 100 * np.log(2)) <= 8
                and abs(meds[1] - 200 * np.log(2)) <= 16):
            diffs.append(f"[E] medians {meds} differ from analytic "
                          f"[{100*np.log(2):.1f}, {200*np.log(2):.1f}]")
    return diffs


def _gt_f_percent_time() -> List[str]:
    """GT-F — time as percent-of-trial-length strings ('45.5%' of 600 days
    follow-up).  After parsing, '45.5%' → 0.455.  Solver should still
    run without crashing; medians would be on the [0, 1] scale."""
    import tempfile
    rng = np.random.default_rng(7)
    n = 200
    t = rng.exponential(scale=0.2, size=n)
    t = np.minimum(t, 1.0)
    e = (t < 0.8).astype(int)
    df = pd.DataFrame({
        "pct_done": [f"{v*100:.2f}%" for v in t],
        "had_event": [1 if v else 0 for v in e],
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"time_col": "pct_done",
                                    "event_col": "had_event"}), Path(tmp))
            # median on [0,1] scale should be ≈ 0.2*ln(2) ≈ 0.139
            smy = pd.read_csv(out["summary_csv"]).iloc[0]
            med = float(smy["median_survival"])
            if abs(med - 0.2 * np.log(2)) > 0.04:
                diffs.append(f"[F] median={med:.4f} vs analytic "
                              f"{0.2*np.log(2):.4f} after %-parse (±0.04)")
        except Exception as e:
            diffs.append(f"[F] should parse '45%' time strings, raised "
                          f"{type(e).__name__}: {e}")
    return diffs


def selftest() -> Dict[str, Any]:
    """6-scenario ground-truth suite for Kaplan–Meier.

      GT-A  two-group exponential        (median + S(t) + log-rank)
      GT-B  single-group Weibull         (analytic median + S(t))
      GT-C  datetime time column         (parse + recover same median)
      GT-D  input robustness             (dtype, non-binary event, missing)
      GT-E  medical strings              (alive/dead + treatment/control)
      GT-F  percent-of-trial time strings

    All six must pass for ok=True.
    """
    diffs = (_gt_a_two_group_exponential()
             + _gt_b_single_group_weibull()
             + _gt_c_datetime_input()
             + _gt_d_robustness()
             + _gt_e_medical_strings()
             + _gt_f_percent_time())
    return {
        "ok": len(diffs) == 0,
        "summary": ("6/6 pass: two-group exp, Weibull single, datetime, "
                    "robustness, alive/dead+treatment/control, %-time"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["survival_kaplan_meier"],
                    "n_scenarios": 6},
    }
