"""Graph-based clustering for single-cell RNA-seq (Leiden / Louvain).

Runs Leiden or Louvain community detection on the cell-cell KNN graph in
``adata.obsp['connectivities']`` (computed by ``sc_dim_reduction`` /
``sc.pp.neighbors``).  Falls back to KMeans on the PCA embedding when the
graph is missing or the Leiden/Louvain backends aren't installed.

Output: clustered.h5ad (label added to obs), clusters.csv (cell_barcode + label),
cluster_sizes.csv.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError

try:
    import anndata
    import scanpy as sc
    sc.settings.verbosity = 0
    _SC_OK = True
except ImportError:
    _SC_OK = False


CONTRACT = SolverContract(
    name="sc_clustering",
    capability="F17_single_cell_clustering",
    description=(
        "Cluster cells in a normalized scRNA-seq .h5ad (Leiden default, "
        "Louvain fallback, KMeans final fallback). Requires neighbors graph "
        "from sc_dim_reduction. Output: clustered.h5ad, clusters.csv (cell→label), "
        "cluster_sizes.csv."
    ),
    roles={
        "input_h5ad": RoleSpec(
            Role.PARAMS,
            "Path to .h5ad with neighbors graph (output of sc_dim_reduction).",
        ),
        "resolution": RoleSpec(
            Role.PARAMS,
            "Resolution for Leiden/Louvain (higher → more clusters). Default 0.8.",
            optional=True,
        ),
        "method": RoleSpec(
            Role.PARAMS,
            "Clustering method: leiden (default), louvain, kmeans.",
            optional=True,
        ),
        "n_clusters_kmeans": RoleSpec(
            Role.PARAMS,
            "K for KMeans fallback only. Default 8.",
            optional=True,
        ),
        "label_key": RoleSpec(
            Role.PARAMS,
            "obs column name for the cluster label. Default 'cluster'.",
            optional=True,
        ),
    },
    static_params={"resolution": 0.8, "method": "leiden",
                   "n_clusters_kmeans": 8, "label_key": "cluster"},
    output_files={
        "clustered_h5ad": "clustered.h5ad",
        "clusters_csv":   "clusters.csv",
        "cluster_sizes_csv": "cluster_sizes.csv",
    },
    output_kind={"clustered_h5ad": "t", "clusters_csv": "t",
                 "cluster_sizes_csv": "s"},
)


class SCClusteringSolver:
    contract = CONTRACT

    def __init__(self, resolution: float = 0.8, method: str = "leiden",
                 n_clusters_kmeans: int = 8, label_key: str = "cluster"):
        if not _SC_OK:
            raise ImportError("scanpy+anndata required for sc_clustering")
        self.resolution = resolution
        self.method = method
        self.n_clusters_kmeans = n_clusters_kmeans
        self.label_key = label_key

    @staticmethod
    def _try_leiden(adata, resolution, key_added):
        try:
            sc.tl.leiden(adata, resolution=resolution, key_added=key_added)
            return True
        except Exception:
            return False

    @staticmethod
    def _try_louvain(adata, resolution, key_added):
        try:
            sc.tl.louvain(adata, resolution=resolution, key_added=key_added)
            return True
        except Exception:
            return False

    @staticmethod
    def _kmeans_on_pca(adata, n_clusters, key_added):
        from sklearn.cluster import KMeans
        if "X_pca" not in adata.obsm:
            raise OperatorInputError(
                "MISSING_REQUIRED_COLUMNS", solver="sc_clustering",
                hint="adata.obsm['X_pca'] missing — run sc_dim_reduction first",
            )
        n_clusters = max(2, min(int(n_clusters), max(2, adata.n_obs - 1)))
        km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
        labels = km.fit_predict(adata.obsm["X_pca"])
        adata.obs[key_added] = pd.Categorical(labels.astype(str))

    def run(self, df, mapping, output_dir):
        import json
        input_h5ad = mapping.get("input_h5ad")
        if not input_h5ad:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                solver="sc_clustering", hint="input_h5ad is required")
        resolution = float(mapping.get("resolution") or self.resolution)
        method = str(mapping.get("method") or self.method).lower()
        label_key = str(mapping.get("label_key") or self.label_key)
        k_for_km = int(mapping.get("n_clusters_kmeans") or self.n_clusters_kmeans)

        adata = anndata.read_h5ad(input_h5ad)

        has_graph = "connectivities" in adata.obsp
        actual_method = method
        if method in ("leiden", "louvain") and not has_graph:
            # try to recover by building neighbors on the fly
            try:
                n_pcs = (adata.obsm["X_pca"].shape[1]
                          if "X_pca" in adata.obsm else min(50, adata.n_obs - 1))
                n_neigh = max(2, min(15, adata.n_obs - 1))
                sc.pp.neighbors(adata, n_pcs=n_pcs, n_neighbors=n_neigh)
                has_graph = True
            except Exception:
                actual_method = "kmeans"

        ok = False
        if actual_method == "leiden":
            ok = self._try_leiden(adata, resolution, label_key)
            if not ok:
                ok = self._try_louvain(adata, resolution, label_key)
                if ok: actual_method = "louvain"
            if not ok:
                self._kmeans_on_pca(adata, k_for_km, label_key)
                actual_method = "kmeans"
        elif actual_method == "louvain":
            ok = self._try_louvain(adata, resolution, label_key)
            if not ok:
                self._kmeans_on_pca(adata, k_for_km, label_key)
                actual_method = "kmeans"
        else:
            self._kmeans_on_pca(adata, k_for_km, label_key)

        labels_series = adata.obs[label_key].astype(str)
        cluster_sizes = labels_series.value_counts().sort_index()

        clusters_df = pd.DataFrame({
            "cell_barcode": adata.obs_names,
            label_key: labels_series.values,
        })
        cl_path = output_dir / "clusters.csv"
        clusters_df.to_csv(cl_path, index=False)

        sizes_df = cluster_sizes.rename_axis("cluster").reset_index(name="n_cells")
        sz_path = output_dir / "cluster_sizes.csv"
        sizes_df.to_csv(sz_path, index=False)

        h5_path = output_dir / "clustered.h5ad"
        adata.write_h5ad(h5_path)

        stats = {"method_requested": method, "method_used": actual_method,
                 "resolution": resolution, "n_clusters": int(len(cluster_sizes)),
                 "n_cells": int(adata.n_obs)}
        (output_dir / "clustering_stats.json").write_text(
            json.dumps(stats, indent=2), encoding="utf-8")

        return {"clustered_h5ad": str(h5_path), "clusters_csv": str(cl_path),
                "cluster_sizes_csv": str(sz_path),
                "n_clusters": int(len(cluster_sizes)),
                "method_used": actual_method}


def get_solver(resolution: float = 0.8, method: str = "leiden",
               n_clusters_kmeans: int = 8, label_key: str = "cluster"):
    return SCClusteringSolver(resolution=resolution, method=method,
                              n_clusters_kmeans=n_clusters_kmeans,
                              label_key=label_key)
