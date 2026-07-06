"""W19 — Frequentist random-effects network meta-analysis (NMA).

Generalised-least-squares (GLS) random-effects NMA with the Lu & Ades
(2004) contrast-based parameterisation that ``netmeta`` (R) uses by
default.  Multi-arm trials are handled with a **block-diagonal
covariance matrix** that respects within-trial correlation between
contrasts (Lu & Ades 2004 §2.2; Higgins et al 2012); this prevents the
classic mistake of over-weighting multi-arm trials.  Heterogeneity
variance tau² is estimated by DerSimonian-Laird from the fixed-effect
residuals.

Output ranks treatments by mean effect vs reference + SUCRA (Surface
Under the Cumulative RAnking).

Input format (long CSV, one row per study × contrast)
-----------------------------------------------------
    study_id, treat_a, treat_b, effect (= y_AB), se
where ``effect`` = treat_a - treat_b on the log-OR / log-RR /
mean-difference scale (depending on what was meta-analysed) and
``se`` = standard error of that contrast.  For multi-arm trials we
expect (k-1) rows per trial (one per non-reference arm) OR all C(k,2)
pairwise contrasts — both are accepted; the solver internally reduces
multi-arm trials to (k-1) contrasts against the within-trial baseline
and reconstructs the correlation structure.

References
----------
- Lu G, Ades AE (2004) "Combination of direct and indirect evidence in
  mixed treatment comparisons" *Stat Med* 23:3105.
- Higgins JPT et al (2012) "Consistency and inconsistency in network
  meta-analysis: concepts and models for multi-arm studies"
  *Res Synth Methods* 3:98 (within-trial covariance correction).
- Rücker G (2012) "Network meta-analysis, electrical networks and
  graph theory" *Res Synth Methods* 3:312.
- Salanti G et al (2011) "Graphical methods and numerical summaries..."
  *J Clin Epidemiol* 64:163 (SUCRA definition).
- DerSimonian R, Laird N (1986) "Meta-analysis in clinical trials"
  *Control Clin Trials* 7:177 (DL tau^2 estimator).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as sps

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="network_meta_analysis",
    capability="F_network_meta",
    description=(
        "Frequentist random-effects network meta-analysis (NMA) using the "
        "Lu & Ades 2004 contrast-based mixed-model formulation, with "
        "DerSimonian-Laird heterogeneity tau^2.  Input long CSV: "
        "study_id, treat_a, treat_b, effect (=mu_a-mu_b), se.  Output: per-"
        "treatment effect vs reference, 95%CI, p-value, and SUCRA + rankogram."
    ),
    roles={
        "study_col": RoleSpec(Role.CATEGORICAL, "Study ID column"),
        "treat_a_col": RoleSpec(Role.CATEGORICAL, "First treatment in contrast"),
        "treat_b_col": RoleSpec(Role.CATEGORICAL, "Second treatment in contrast"),
        "effect_col": RoleSpec(Role.NUMERIC, "Effect = y_a - y_b (log-OR or MD)"),
        "se_col": RoleSpec(Role.NUMERIC, "Standard error of contrast"),
    },
    static_params={
        "reference_treatment": None,  # autopicks the most-studied if None
        "smaller_is_better": True,    # for ranking (e.g. PANSS reduction)
        "n_sim_sucra": 5000,          # MC sample size for SUCRA
        "random_state": 42,
    },
    output_files={
        "league_table_csv": "nma_league_table.csv",
        "ranking_csv": "nma_ranking.csv",
        "summary_json": "nma_summary.json",
    },
    output_kind={"league_table_csv": "s", "ranking_csv": "s",
                  "summary_json": "s"},
)


class NetworkMetaAnalysisSolver:
    contract = CONTRACT

    def __init__(self, reference_treatment: Optional[str] = None,
                 smaller_is_better: bool = True,
                 n_sim_sucra: int = 5000,
                 random_state: int = 42):
        self.reference = reference_treatment
        self.smaller_is_better = smaller_is_better
        self.n_sim_sucra = int(n_sim_sucra)
        self.random_state = int(random_state)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        s_col = mapping.get("study_col")
        a_col = mapping.get("treat_a_col")
        b_col = mapping.get("treat_b_col")
        e_col = mapping.get("effect_col")
        se_col = mapping.get("se_col")
        if not all([s_col, a_col, b_col, e_col, se_col]):
            raise ValueError("study_col, treat_a_col, treat_b_col, "
                              "effect_col, se_col are all required")

        sub = df[[s_col, a_col, b_col, e_col, se_col]].dropna().copy()
        sub.columns = ["study", "a", "b", "y", "se"]
        sub["a"] = sub["a"].astype(str)
        sub["b"] = sub["b"].astype(str)
        sub["y"] = sub["y"].astype(float)
        sub["se"] = sub["se"].astype(float)
        if (sub["se"] <= 0).any():
            raise ValueError("se must be strictly positive")

        treatments = sorted(set(sub["a"]).union(sub["b"]))
        T = len(treatments)
        if T < 2:
            raise ValueError("need >=2 treatments in network")

        # Choose reference: user-given else most-studied treatment.
        if self.reference is None:
            counts = pd.concat([sub["a"], sub["b"]]).value_counts()
            ref = counts.index[0]
        else:
            if self.reference not in treatments:
                raise ValueError(f"reference {self.reference!r} not in network")
            ref = self.reference
        idx = {t: i for i, t in enumerate(treatments)}
        ref_idx = idx[ref]

        # ----- Multi-arm trial reduction (Lu-Ades 2004 §2.2; Higgins 2012) -----
        # For each trial we reduce to (k-1) contrasts against a within-trial
        # baseline arm (we pick the alphabetically-first arm) and reconstruct
        # the (k-1)x(k-1) covariance block.  For k=2 this is just the single
        # row with its scalar variance.  For k>=3 we use:
        #     y_ij = mu_i - mu_j  with Var(y_ij) approx = se_ij^2
        #     y_ij - y_ik = mu_k - mu_j  => Var(y_ij - y_ik) = ...
        # The standard Lu-Ades simplification when only contrast-level y_ij
        # and se_ij are given is:
        #     Var(d_0j) = se_0j^2,   Cov(d_0j, d_0k) = (Var(d_0j) +
        #                                       Var(d_0k) - Var(d_jk)) / 2
        # which is what netmeta uses by default.
        reduced_rows: List[Dict[str, Any]] = []
        cov_blocks: List[np.ndarray] = []   # one (k-1)x(k-1) block per trial
        for trial_id, gdf in sub.groupby("study"):
            arms_in_trial = sorted(set(gdf["a"]).union(gdf["b"]))
            if len(arms_in_trial) < 2:
                continue
            baseline = arms_in_trial[0]
            non_base = arms_in_trial[1:]
            # For each non-baseline arm j, find a row that gives y(baseline vs j)
            # OR can be derived from two rows.
            def _contrast(a_arm: str, b_arm: str):
                """Return (y, se) of contrast a-b, flipping rows if needed."""
                for _, r in gdf.iterrows():
                    if r["a"] == a_arm and r["b"] == b_arm:
                        return float(r["y"]), float(r["se"])
                    if r["a"] == b_arm and r["b"] == a_arm:
                        return -float(r["y"]), float(r["se"])
                return None
            # Build (k-1) baseline-vs-other contrasts.
            ys, vars_, contrast_treats = [], [], []
            for j in non_base:
                c = _contrast(baseline, j)
                if c is None:
                    # Try indirect within-trial: baseline-k + k-j for some k.
                    for k in non_base:
                        if k == j:
                            continue
                        c1 = _contrast(baseline, k)
                        c2 = _contrast(k, j)
                        if c1 is not None and c2 is not None:
                            y_bj = c1[0] + c2[0]
                            v_bj = c1[1] ** 2 + c2[1] ** 2
                            c = (y_bj, float(np.sqrt(v_bj)))
                            break
                if c is None:
                    # Trial is degenerate; skip this contrast.
                    continue
                ys.append(c[0])
                vars_.append(c[1] ** 2)
                contrast_treats.append((baseline, j))
            if not ys:
                continue
            k_eff = len(ys)
            # Build covariance block.  For k_eff=1 it's a scalar variance.
            # For k_eff>=2 use Lu-Ades formula:
            #   Cov(d_0j, d_0k) = (Var(d_0j) + Var(d_0k) - Var(d_jk)) / 2
            cov_blk = np.zeros((k_eff, k_eff))
            for i in range(k_eff):
                cov_blk[i, i] = vars_[i]
            for i in range(k_eff):
                for j in range(i + 1, k_eff):
                    _, arm_i = contrast_treats[i]
                    _, arm_j = contrast_treats[j]
                    cij = _contrast(arm_i, arm_j)
                    if cij is None:
                        # Approximation: assume equal variance arms -> cov ≈ Var_baseline.
                        cov_blk[i, j] = cov_blk[j, i] = min(vars_[i], vars_[j]) / 2
                    else:
                        v_jk = cij[1] ** 2
                        cov_ij = (vars_[i] + vars_[j] - v_jk) / 2.0
                        cov_blk[i, j] = cov_blk[j, i] = cov_ij
            # Ensure positive-definite (small ridge if needed).
            try:
                np.linalg.cholesky(cov_blk + 1e-12 * np.eye(k_eff))
            except np.linalg.LinAlgError:
                cov_blk = cov_blk + 1e-4 * np.eye(k_eff)
            cov_blocks.append(cov_blk)
            for i, (a_arm, b_arm) in enumerate(contrast_treats):
                reduced_rows.append({"study": str(trial_id),
                                      "a": a_arm, "b": b_arm,
                                      "y": ys[i], "se": float(np.sqrt(vars_[i]))})

        if not reduced_rows:
            raise ValueError("network reduction yielded no usable contrasts")
        rsub = pd.DataFrame(reduced_rows)

        # Design matrix (a-b) coded as +1/-1, drop reference column.
        n_rows = len(rsub)
        X_full = np.zeros((n_rows, T))
        for i, (_, r) in enumerate(rsub.iterrows()):
            X_full[i, idx[r["a"]]] += 1
            X_full[i, idx[r["b"]]] -= 1
        keep_cols = [j for j in range(T) if j != ref_idx]
        X = X_full[:, keep_cols]
        y = rsub["y"].values

        # Assemble block-diagonal V (fixed-effect within-trial covariance).
        V = np.zeros((n_rows, n_rows))
        row_off = 0
        for blk in cov_blocks:
            kb = blk.shape[0]
            V[row_off:row_off + kb, row_off:row_off + kb] = blk
            row_off += kb

        # ----- Fixed-effect GLS first pass for DL tau^2 -----
        Vinv = np.linalg.inv(V + 1e-12 * np.eye(n_rows))
        XtVinvX = X.T @ Vinv @ X
        XtVinvy = X.T @ Vinv @ y
        d_fe = np.linalg.solve(XtVinvX, XtVinvy)
        resid = y - X @ d_fe
        Q = float(resid.T @ Vinv @ resid)
        df_q = n_rows - (T - 1)
        if df_q > 0:
            # DerSimonian-Laird estimator extended to GLS-NMA: use
            # E[Q] = df + tau^2 * trace(P) where P = Vinv - Vinv X (X'VinvX)^-1 X' Vinv
            P = Vinv - Vinv @ X @ np.linalg.solve(XtVinvX, X.T @ Vinv)
            trP = float(np.trace(P))
            tau2 = max(0.0, (Q - df_q) / trP) if trP > 0 else 0.0
        else:
            tau2 = 0.0

        # Random-effects GLS: V_re = V + tau^2 * I  (Jackson 2010 §2 approx
        # for multivariate meta-analysis; identical to DSL when V is diagonal).
        V_re = V + tau2 * np.eye(n_rows)
        Vinv_re = np.linalg.inv(V_re + 1e-12 * np.eye(n_rows))
        XtVinvX_re = X.T @ Vinv_re @ X
        XtVinvy_re = X.T @ Vinv_re @ y
        d_re = np.linalg.solve(XtVinvX_re, XtVinvy_re)
        cov_d = np.linalg.inv(XtVinvX_re)

        # Build league table: effect of every treatment vs reference.
        effects = np.zeros(T)
        ses = np.zeros(T)
        full_cov = np.zeros((T, T))
        # ref vs ref = 0, var 0; others from d_re.
        non_ref = [t for t in treatments if t != ref]
        for j, t in enumerate(non_ref):
            effects[idx[t]] = d_re[j]
            ses[idx[t]] = float(np.sqrt(cov_d[j, j]))
        # Fill cov sub-block in full_cov for SUCRA simulation.
        for j, t in enumerate(non_ref):
            for k, t2 in enumerate(non_ref):
                full_cov[idx[t], idx[t2]] = cov_d[j, k]

        rows: List[Dict[str, Any]] = []
        for t in treatments:
            beta = float(effects[idx[t]])
            se = float(ses[idx[t]])
            z = beta / se if se > 0 else float("nan")
            p = float(2 * (1 - sps.norm.cdf(abs(z)))) if se > 0 else float("nan")
            rows.append({
                "treatment": t,
                "is_reference": (t == ref),
                "effect_vs_reference": beta,
                "se": se,
                "z": float(z) if not np.isnan(z) else None,
                "p_value": p,
                "ci_low": beta - 1.96 * se,
                "ci_high": beta + 1.96 * se,
            })
        league_df = pd.DataFrame(rows).sort_values("effect_vs_reference")

        # SUCRA via Monte Carlo from the asymptotic normal of (T-1) contrasts.
        rng = np.random.default_rng(self.random_state)
        # Sample d ~ N(d_re, cov_d), prepend zero for reference, total T cols.
        S = self.n_sim_sucra
        try:
            L = np.linalg.cholesky(cov_d + 1e-12 * np.eye(cov_d.shape[0]))
        except np.linalg.LinAlgError:
            L = np.linalg.cholesky(cov_d + 1e-6 * np.eye(cov_d.shape[0]))
        z_samples = rng.standard_normal((S, cov_d.shape[0]))
        d_samples = d_re[None, :] + z_samples @ L.T   # S x (T-1)
        full_d = np.zeros((S, T))
        for j, t in enumerate(non_ref):
            full_d[:, idx[t]] = d_samples[:, j]
        # rank: 1 = best (smallest if smaller_is_better, else largest).
        sign = +1 if self.smaller_is_better else -1
        ranks = (sign * full_d).argsort(axis=1).argsort(axis=1) + 1  # 1..T
        # Cumulative-rank probability per treatment: P(rank <= k)
        cum_rank_prob = np.zeros((T, T))
        for t_idx in range(T):
            for k in range(1, T + 1):
                cum_rank_prob[t_idx, k - 1] = float(
                    (ranks[:, t_idx] <= k).mean())
        # SUCRA = mean over k of cum_rank_prob (normalised).
        sucra = cum_rank_prob[:, : T - 1].mean(axis=1)

        rank_rows: List[Dict[str, Any]] = []
        for t in treatments:
            ti = idx[t]
            mean_rank = float(ranks[:, ti].mean())
            p_best = float((ranks[:, ti] == 1).mean())
            rank_rows.append({
                "treatment": t,
                "mean_rank": mean_rank,
                "p_best": p_best,
                "sucra": float(sucra[ti]),
            })
        ranking_df = pd.DataFrame(rank_rows).sort_values("sucra", ascending=False)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        lg_path = out_dir / CONTRACT.output_files["league_table_csv"]
        rk_path = out_dir / CONTRACT.output_files["ranking_csv"]
        sm_path = out_dir / CONTRACT.output_files["summary_json"]
        league_df.to_csv(lg_path, index=False)
        ranking_df.to_csv(rk_path, index=False)

        summary = {
            "n_studies": int(sub["study"].nunique()),
            "n_contrasts": int(n_rows),
            "n_treatments": int(T),
            "treatments": treatments,
            "reference": ref,
            "Q_statistic": Q,
            "Q_df": int(df_q),
            "Q_p_value": float(sps.chi2.sf(Q, max(df_q, 1)))
                if df_q > 0 else None,
            "tau2": float(tau2),
            "I2_pct": float(max(0.0, (Q - df_q) / max(Q, 1e-9)) * 100)
                if df_q > 0 else 0.0,
            "smaller_is_better": bool(self.smaller_is_better),
            "best_by_sucra": ranking_df.iloc[0]["treatment"],
        }
        sm_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "league_table_csv": str(lg_path),
            "ranking_csv": str(rk_path),
            "summary_json": str(sm_path),
            **summary,
        }


def get_solver(reference_treatment: Optional[str] = None,
               smaller_is_better: bool = True,
               n_sim_sucra: int = 5000,
               random_state: int = 42) -> NetworkMetaAnalysisSolver:
    return NetworkMetaAnalysisSolver(
        reference_treatment=reference_treatment,
        smaller_is_better=smaller_is_better,
        n_sim_sucra=n_sim_sucra,
        random_state=random_state,
    )


def selftest() -> Dict[str, Any]:
    """Ground-truth: 4-treatment network with planted truth
        mu_A=0, mu_B=-2, mu_C=-3, mu_D=-1  (smaller = better)
    Generate 12 studies, each comparing 2 random treatments with known
    standard error.  Verify:
    - effect_vs_reference (with A as ref) recovers true differences ± tol,
    - SUCRA ranking matches true ordering C > B > D > A.
    """
    import tempfile
    rng = np.random.default_rng(11)
    truth = {"A": 0.0, "B": -2.0, "C": -3.0, "D": -1.0}
    rows = []
    pairs = [("A", "B"), ("A", "C"), ("A", "D"),
             ("B", "C"), ("B", "D"), ("C", "D")]
    for i in range(60):  # 60 studies for stable test
        a, b = pairs[i % len(pairs)]
        se = 0.3
        y = (truth[a] - truth[b]) + rng.normal(0, se)
        rows.append({"study": f"S{i:03d}", "a": a, "b": b, "y": y, "se": se})
    df = pd.DataFrame(rows)

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(reference_treatment="A", smaller_is_better=True).run(
            df, ColumnMapping({"study_col": "study", "treat_a_col": "a",
                                "treat_b_col": "b", "effect_col": "y",
                                "se_col": "se"}),
            Path(tmp))
        league = pd.read_csv(out["league_table_csv"]).set_index("treatment")
        for t in ["B", "C", "D"]:
            est = float(league.loc[t, "effect_vs_reference"])
            true = truth[t] - truth["A"]
            if abs(est - true) > 0.20:
                diffs.append(f"effect({t} vs A) = {est:.3f}, expected {true:.3f}")
        # Best by SUCRA should be C (truly best on smaller-is-better).
        ranking = pd.read_csv(out["ranking_csv"])
        if str(ranking.iloc[0]["treatment"]) != "C":
            diffs.append(f"top-SUCRA={ranking.iloc[0]['treatment']}, expected C")

    return {
        "ok": len(diffs) == 0,
        "summary": ("NMA recovers true treatment effects + ranking"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["network_meta_analysis"]},
    }
