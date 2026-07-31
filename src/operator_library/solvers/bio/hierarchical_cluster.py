"""Hierarchical clustering on samples (rows) of an expression matrix."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import pdist

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


_VALID_METHODS = {"single", "complete", "average", "ward",
                   "weighted", "centroid", "median"}
_VALID_METRICS = {"correlation", "euclidean", "cosine",
                   "cityblock", "minkowski", "chebyshev"}


CONTRACT = SolverContract(
    name="hclust_samples",
    capability="F05_clustering_segmentation",
    description=(
        "Hierarchical agglomerative clustering on samples (rows of an "
        "expression matrix).  Output: linkage.csv (SciPy linkage matrix "
        "in standard 4-column format) and cluster_assignments.csv "
        "(sample_id + cluster_id at the requested k)."
    ),
    roles={
        "gene_matrix_csv": RoleSpec(
            Role.PARAMS,
            "Path to a gene_matrix.csv (first column = gene_symbol, "
            "rest are sample columns)."),
        "method": RoleSpec(
            Role.PARAMS,
            "Linkage method: single/complete/average/ward (default: "
            "average).  ward requires euclidean metric.",
            optional=True),
        "metric": RoleSpec(
            Role.PARAMS,
            "Distance metric: correlation/euclidean/cosine/... "
            "(default: correlation).",
            optional=True),
        "n_clusters": RoleSpec(
            Role.PARAMS,
            "Number of flat clusters to extract (default: 2).",
            optional=True),
    },
    static_params={"method": "average", "metric": "correlation",
                    "n_clusters": 2},
    output_files={
        "linkage_csv":            "linkage.csv",
        "cluster_assignments_csv": "cluster_assignments.csv",
    },
    output_kind={
        "linkage_csv":            "s",
        "cluster_assignments_csv": "t",  # row = sample with cluster id
    },
)


class HclustSamplesSolver:
    contract = CONTRACT

    def __init__(self, method: str = "average",
                 metric: str = "correlation",
                 n_clusters: int = 2):
        self.method = method
        self.metric = metric
        self.n_clusters = n_clusters

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        gm_path = mapping.get("gene_matrix_csv")
        if not gm_path:
            raise ValueError("hclust_samples requires 'gene_matrix_csv'")
        method = (mapping.get("method") or self.method).lower()
        metric = (mapping.get("metric") or self.metric).lower()
        if method not in _VALID_METHODS:
            raise ValueError(f"method must be one of {_VALID_METHODS}, "
                              f"got {method!r}")
        if metric not in _VALID_METRICS:
            raise ValueError(f"metric must be one of {_VALID_METRICS}, "
                              f"got {metric!r}")
        if method == "ward" and metric != "euclidean":
            metric = "euclidean"   # ward only valid with euclidean

        n_clusters = int(mapping.get("n_clusters") or self.n_clusters)
        if n_clusters < 2:
            n_clusters = 2

        gm = pd.read_csv(gm_path)
        if "gene_symbol" not in gm.columns:
            gm = gm.rename(columns={gm.columns[0]: "gene_symbol"})
        sample_cols = [c for c in gm.columns if c != "gene_symbol"]
        if len(sample_cols) < n_clusters:
            raise ValueError(f"need >= {n_clusters} samples, got "
                              f"{len(sample_cols)}")

        X = gm[sample_cols].to_numpy(dtype=float).T  # (n_samples, n_genes)
        # drop genes that are fully missing across all samples first
        any_obs = ~np.all(np.isnan(X), axis=0)
        X = X[:, any_obs]
        if X.size == 0:
            raise ValueError("no observations remain after NaN filtering")
        # impute remaining NaN with column means
        col_means = np.nanmean(X, axis=0)
        idx = np.where(np.isnan(X))
        X[idx] = np.take(col_means, idx[1])
        # drop zero-variance genes (correlation distance crashes on them)
        nz = X.std(axis=0, ddof=0) > 1e-12
        X = X[:, nz]
        if X.shape[1] == 0:
            raise ValueError("no variable genes remain after filtering")

        D = pdist(X, metric=metric)
        Z = linkage(D, method=method)
        cl = fcluster(Z, t=n_clusters, criterion="maxclust")

        link_df = pd.DataFrame(Z, columns=["i", "j", "distance", "size"])
        link_df["i"] = link_df["i"].astype(int)
        link_df["j"] = link_df["j"].astype(int)
        link_df["size"] = link_df["size"].astype(int)
        cl_df = pd.DataFrame({"sample_id": sample_cols,
                                "cluster_id": cl.astype(int)})

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        lp = out_dir / CONTRACT.output_files["linkage_csv"]
        cp = out_dir / CONTRACT.output_files["cluster_assignments_csv"]
        link_df.to_csv(lp, index=False)
        cl_df.to_csv(cp, index=False)
        return {
            "linkage_csv":            str(lp),
            "cluster_assignments_csv": str(cp),
            "n_samples":              int(X.shape[0]),
            "n_genes_after_filter":   int(X.shape[1]),
            "method":                 method,
            "metric":                 metric,
            "n_clusters":             int(n_clusters),
        }


def get_solver(method: str = "average", metric: str = "correlation",
                n_clusters: int = 2):
    return HclustSamplesSolver(method=method, metric=metric,
                                 n_clusters=n_clusters)


def selftest():
    """6 samples in 2 obvious blocks; assert hclust separates them."""
    import tempfile

    rng = np.random.default_rng(7)
    n_genes = 200
    base = rng.normal(0, 0.3, size=(n_genes, 6))
    # block A = samples 1..3, B = 4..6
    base[:120, 3:] += 6.0
    samples = [f"S{i+1}" for i in range(6)]
    gm = pd.DataFrame(base, columns=samples)
    gm.insert(0, "gene_symbol", [f"G{i}" for i in range(n_genes)])

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gm_p = tmp / "gm.csv"
        gm.to_csv(gm_p, index=False)
        out = get_solver(method="ward", metric="euclidean").run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({"gene_matrix_csv": str(gm_p),
                                     "n_clusters": 2}),
            output_dir=tmp / "out",
        )
        cl = pd.read_csv(out["cluster_assignments_csv"]).set_index("sample_id")
        a_labels = set(cl.loc[["S1", "S2", "S3"], "cluster_id"].tolist())
        b_labels = set(cl.loc[["S4", "S5", "S6"], "cluster_id"].tolist())
        if len(a_labels) != 1 or len(b_labels) != 1 or a_labels == b_labels:
            diffs.append(f"hclust(ward) failed to recover blocks: "
                         f"{cl['cluster_id'].tolist()}")

        # also smoke-test the default correlation+average path runs
        out2 = get_solver().run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({"gene_matrix_csv": str(gm_p),
                                     "n_clusters": 2}),
            output_dir=tmp / "out2",
        )
        cl2 = pd.read_csv(out2["cluster_assignments_csv"])
        if len(cl2) != 6 or cl2["cluster_id"].nunique() != 2:
            diffs.append(f"correlation+average path produced bad output: "
                         f"{cl2.to_dict('records')}")

        link = pd.read_csv(out["linkage_csv"])
        if len(link) != 5:
            diffs.append(f"linkage matrix expected 5 rows for 6 samples, "
                         f"got {len(link)}")
        if (link["distance"].diff().dropna() < -1e-9).any():
            diffs.append("linkage distances should be non-decreasing")

    return {"ok": len(diffs) == 0,
            "summary": ("hclust correctly recovers 2 synthetic blocks"
                         if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs, "tested": ["hclust_samples"]}}
