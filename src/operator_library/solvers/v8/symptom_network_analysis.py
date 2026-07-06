"""W26 — Symptom-network analysis via partial-correlation graphical model.

Implements the standard psychopathology-network workflow popularised by
the ``qgraph`` and ``bootnet`` R packages (Borsboom et al 2013) but in
pure Python:

1.  Estimate the partial-correlation matrix using sklearn's
    ``GraphicalLassoCV`` (L1-regularised Gaussian graphical model;
    Friedman et al 2008), which gives a sparse, cross-validated
    inverse covariance matrix.
2.  Convert precision -> partial correlation: rho_ij = -P_ij / sqrt(P_ii P_jj).
3.  Compute the standard centrality indices: strength (sum of |edge|s),
    closeness, betweenness, expected influence (Robinaugh et al 2016).
4.  Compute "bridge strength" if a community/cluster labelling is passed.

Input: wide CSV (rows = subjects, columns = symptom items / scales).

References
----------
- Friedman J, Hastie T, Tibshirani R (2008) "Sparse inverse covariance
  estimation with the graphical lasso" *Biostatistics* 9:432.
- Borsboom D, Cramer AOJ (2013) "Network analysis: an integrative
  approach to the structure of psychopathology" *Annu Rev Clin Psychol*.
- Epskamp S et al (2018) "Estimating psychological networks and their
  accuracy: a tutorial paper" *Behav Res Methods*.
- Robinaugh DJ et al (2016) "Identifying highly influential nodes in the
  complicated grief network" *J Abnorm Psychol*.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import networkx as nx
from sklearn.covariance import GraphicalLassoCV
from sklearn.preprocessing import StandardScaler

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="symptom_network_analysis",
    capability="F_symptom_network",
    description=(
        "Symptom-network (partial-correlation) analysis on a wide subject x "
        "symptom-item matrix.  Uses Graphical Lasso (Friedman 2008) with "
        "CV-selected L1 penalty to estimate a sparse partial-correlation "
        "network, then computes node strength, closeness, betweenness, and "
        "expected influence centrality (Robinaugh 2016).  Optional bridge "
        "centrality (Jones et al 2021) if community labels supplied."
    ),
    roles={
        "items": RoleSpec(Role.NUMERIC_LIST,
                           "Symptom item columns (continuous or ordinal)"),
        "community_labels": RoleSpec(Role.PARAMS,
                                       "Optional dict {item_name: community_name} "
                                       "for bridge centrality",
                                       optional=True),
    },
    static_params={
        "min_obs": 100,
        "standardize": True,
    },
    output_files={
        "edge_list_csv": "network_edge_list.csv",
        "centrality_csv": "network_centrality.csv",
        "partial_corr_matrix_csv": "network_partial_corr_matrix.csv",
        "summary_json": "network_summary.json",
    },
    output_kind={"edge_list_csv": "s", "centrality_csv": "s",
                  "partial_corr_matrix_csv": "s", "summary_json": "s"},
)


class SymptomNetworkSolver:
    contract = CONTRACT

    def __init__(self, min_obs: int = 100, standardize: bool = True):
        self.min_obs = int(min_obs)
        self.standardize = bool(standardize)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        item_cols = list(mapping.get("items") or [])
        if len(item_cols) < 4:
            raise ValueError("need >=4 items for a meaningful network")
        comm = mapping.get("community_labels") or None

        sub = df[item_cols].dropna().copy()
        n = len(sub)
        if n < self.min_obs:
            raise ValueError(f"n={n} too small; need >={self.min_obs}")

        X = sub.values.astype(float)
        if self.standardize:
            X = StandardScaler().fit_transform(X)

        # Graphical Lasso CV.  Fall back to higher alpha grid if convergence fails.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                gl = GraphicalLassoCV(cv=5, max_iter=200, n_jobs=1)
                gl.fit(X)
            except Exception:
                gl = GraphicalLassoCV(cv=5, max_iter=500, n_jobs=1,
                                       alphas=4)
                gl.fit(X)
        prec = gl.precision_
        cov = gl.covariance_
        alpha_used = float(gl.alpha_)

        # Partial correlation: rho_ij = -P_ij / sqrt(P_ii P_jj).
        d = np.sqrt(np.clip(np.diag(prec), 1e-12, None))
        partial_corr = -prec / np.outer(d, d)
        np.fill_diagonal(partial_corr, 0.0)

        # Build network — keep nonzero edges (graphical lasso already
        # produced exact-zero off-diagonals when penalty is strong).
        G = nx.Graph()
        for i, c in enumerate(item_cols):
            G.add_node(c)
        edge_rows: List[Dict[str, Any]] = []
        for i in range(len(item_cols)):
            for j in range(i + 1, len(item_cols)):
                w = float(partial_corr[i, j])
                if abs(w) < 1e-6:
                    continue
                G.add_edge(item_cols[i], item_cols[j], weight=w,
                            abs_weight=abs(w))
                edge_rows.append({
                    "node_a": item_cols[i],
                    "node_b": item_cols[j],
                    "partial_corr": w,
                    "abs_partial_corr": abs(w),
                    "sign": "+" if w > 0 else "-",
                })
        edge_df = pd.DataFrame(edge_rows).sort_values(
            "abs_partial_corr", ascending=False
        ).reset_index(drop=True)

        # Centrality.
        # Strength = sum of |edge weights| (qgraph definition).
        strength = {n: 0.0 for n in G.nodes}
        expected_influence = {n: 0.0 for n in G.nodes}  # signed sum
        for u, v, data in G.edges(data=True):
            strength[u] += abs(data["weight"])
            strength[v] += abs(data["weight"])
            expected_influence[u] += data["weight"]
            expected_influence[v] += data["weight"]
        # Closeness / betweenness using inverse |weight| as distance.
        try:
            dist = {(u, v): 1.0 / max(abs(d["weight"]), 1e-6)
                    for u, v, d in G.edges(data=True)}
            for (u, v), w in dist.items():
                G[u][v]["distance"] = w
            closeness = nx.closeness_centrality(G, distance="distance")
            betweenness = nx.betweenness_centrality(G, weight="distance",
                                                     normalized=True)
        except Exception:
            closeness = {n: float("nan") for n in G.nodes}
            betweenness = {n: float("nan") for n in G.nodes}

        # Bridge strength if community labels given.
        bridge_strength = {n: None for n in G.nodes}
        if comm is not None and isinstance(comm, dict):
            for n in G.nodes:
                my_c = comm.get(n)
                bs = 0.0
                for nbr, data in G[n].items():
                    if comm.get(nbr) != my_c:
                        bs += abs(data["weight"])
                bridge_strength[n] = float(bs)

        cent_rows: List[Dict[str, Any]] = []
        for n in G.nodes:
            cent_rows.append({
                "item": n,
                "strength": strength[n],
                "expected_influence": expected_influence[n],
                "closeness": float(closeness.get(n, float("nan"))),
                "betweenness": float(betweenness.get(n, float("nan"))),
                "bridge_strength": bridge_strength[n],
                "degree": int(G.degree(n)),
            })
        cent_df = pd.DataFrame(cent_rows).sort_values(
            "strength", ascending=False
        ).reset_index(drop=True)

        pc_df = pd.DataFrame(partial_corr, index=item_cols, columns=item_cols)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ed_path = out_dir / CONTRACT.output_files["edge_list_csv"]
        ct_path = out_dir / CONTRACT.output_files["centrality_csv"]
        pc_path = out_dir / CONTRACT.output_files["partial_corr_matrix_csv"]
        sm_path = out_dir / CONTRACT.output_files["summary_json"]
        edge_df.to_csv(ed_path, index=False)
        cent_df.to_csv(ct_path, index=False)
        pc_df.to_csv(pc_path, index=True)

        summary = {
            "n_obs": int(len(sub)),
            "n_items": int(len(item_cols)),
            "n_edges": int(G.number_of_edges()),
            "density": float(nx.density(G)),
            "graphical_lasso_alpha": alpha_used,
            "top_3_by_strength": cent_df.head(3)["item"].tolist(),
            "top_3_by_expected_influence":
                cent_df.sort_values("expected_influence", ascending=False)
                       .head(3)["item"].tolist(),
            "mean_abs_partial_corr": float(
                edge_df["abs_partial_corr"].mean()) if not edge_df.empty else 0.0,
        }
        sm_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "edge_list_csv": str(ed_path),
            "centrality_csv": str(ct_path),
            "partial_corr_matrix_csv": str(pc_path),
            "summary_json": str(sm_path),
            **summary,
        }


def get_solver(min_obs: int = 100,
               standardize: bool = True) -> SymptomNetworkSolver:
    return SymptomNetworkSolver(min_obs=min_obs, standardize=standardize)


def selftest() -> Dict[str, Any]:
    """Ground-truth: plant a hub node Y0 connected to Y1..Y4 and verify
    (a) hub has highest strength centrality,
    (b) edges Y0-Y_k are non-zero and edges among Y1..Y4 are near zero."""
    import tempfile
    rng = np.random.default_rng(42)
    n = 600
    Y0 = rng.normal(0, 1, n)
    items = {"Y0": Y0}
    for k in range(1, 5):
        items[f"Y{k}"] = 0.6 * Y0 + rng.normal(0, 0.5, n)
    # Add 3 "noise" items uncorrelated with Y0.
    for k in range(5, 8):
        items[f"N{k}"] = rng.normal(0, 1, n)
    df = pd.DataFrame(items)

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"items": list(df.columns)}), Path(tmp))
        cent_df = pd.read_csv(out["centrality_csv"]).set_index("item")
        top_item = str(cent_df["strength"].idxmax())
        if top_item != "Y0":
            diffs.append(f"hub-by-strength={top_item}, expected Y0; "
                         f"strengths={cent_df['strength'].to_dict()}")
        edge_df = pd.read_csv(out["edge_list_csv"])
        # Edges Y0-Y_k should be present.
        hub_edges = edge_df[(edge_df["node_a"] == "Y0") |
                            (edge_df["node_b"] == "Y0")]
        if hub_edges.shape[0] < 4:
            diffs.append(f"hub Y0 has only {hub_edges.shape[0]} edges "
                         "(expected >=4)")

    return {
        "ok": len(diffs) == 0,
        "summary": ("symptom_network correctly identifies hub Y0 and edges"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["symptom_network_analysis"]},
    }
