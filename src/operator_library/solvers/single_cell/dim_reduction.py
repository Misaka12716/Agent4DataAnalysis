"""Dimensionality reduction for single-cell RNA-seq data.

Runs PCA + UMAP/t-SNE on a normalized single-cell expression matrix.
Backed by scanpy.  Input: .h5ad file.

Output: reduced.h5ad, embeddings.csv, variance.csv.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

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
    name="sc_dim_reduction",
    capability="F17_single_cell_dim_reduction",
    description=(
        "Dimensionality reduction for scRNA-seq: PCA + UMAP or t-SNE. "
        "Input: .h5ad file. Output: reduced.h5ad, embeddings.csv (cell x "
        "component), variance.csv (explained variance per PC)."
    ),
    roles={
        "input_h5ad": RoleSpec(
            Role.PARAMS,
            "Path to .h5ad file (after gene_filter_normalize or highly_variable_genes).",
        ),
        "n_pcs": RoleSpec(
            Role.PARAMS,
            "Number of principal components. Default: 50.",
            optional=True,
        ),
        "n_neighbors": RoleSpec(
            Role.PARAMS,
            "Number of neighbors for UMAP/t-SNE. Default: 15.",
            optional=True,
        ),
        "method": RoleSpec(
            Role.PARAMS,
            "Reduction method: pca_only, umap (default), tsne.",
            optional=True,
        ),
        "color_by": RoleSpec(
            Role.PARAMS,
            "Optional obs column for coloring (copied verbatim).",
            optional=True,
        ),
    },
    static_params={"n_pcs": 50, "n_neighbors": 15, "method": "umap"},
    output_files={
        "reduced_h5ad": "reduced.h5ad",
        "embeddings_csv": "embeddings.csv",
        "variance_csv": "variance.csv",
    },
    output_kind={"reduced_h5ad": "t", "embeddings_csv": "t", "variance_csv": "s"},
)


class SCDimReductionSolver:
    contract = CONTRACT

    def __init__(self, n_pcs: int = 50, n_neighbors: int = 15,
                 method: str = "umap"):
        if not _SC_OK:
            raise ImportError("scanpy+anndata required")
        self.n_pcs = n_pcs
        self.n_neighbors = n_neighbors
        self.method = method

    def run(self, df, mapping, output_dir):
        input_h5ad = mapping.get("input_h5ad")
        if not input_h5ad:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="sc_dim_reduction",
                                     hint="input_h5ad is required")
        n_pcs = int(mapping.get("n_pcs") or self.n_pcs)
        n_neighbors = int(mapping.get("n_neighbors") or self.n_neighbors)
        method = str(mapping.get("method") or self.method)

        adata = anndata.read_h5ad(input_h5ad)
        # Auto-clip n_pcs and n_neighbors for small datasets
        max_pcs = max(1, min(adata.n_obs, adata.n_vars) - 1)
        n_pcs = max(2, min(n_pcs, max_pcs))
        n_neighbors = max(2, min(n_neighbors, adata.n_obs - 1))
        sc.tl.pca(adata, n_comps=n_pcs, svd_solver="arpack")
        sc.pp.neighbors(adata, n_pcs=n_pcs, n_neighbors=n_neighbors)

        if method == "tsne":
            sc.tl.tsne(adata, n_pcs=n_pcs)
            emb_key = "X_tsne"
            emb_cols = ["tsne_1", "tsne_2"]
        else:
            sc.tl.umap(adata, min_dist=0.3)
            emb_key = "X_umap"
            emb_cols = ["umap_1", "umap_2"]

        n_cells = adata.n_obs
        pca_emb = adata.obsm["X_pca"]
        pca_cols = [f"PC{i+1}" for i in range(pca_emb.shape[1])]
        var_df = pd.DataFrame({
            "PC": pca_cols,
            "variance_ratio": adata.uns["pca"]["variance_ratio"],
        })
        var_path = output_dir / "variance.csv"
        var_df.to_csv(var_path, index=False)

        emb = adata.obsm[emb_key]
        emb_df = pd.DataFrame(emb, columns=emb_cols)
        emb_df.index.name = "cell_barcode"
        if mapping.get("color_by") and mapping["color_by"] in adata.obs.columns:
            emb_df[mapping["color_by"]] = adata.obs[mapping["color_by"]].values
        # Add first few PCs
        for i in range(min(5, pca_emb.shape[1])):
            emb_df[pca_cols[i]] = pca_emb[:, i]
        emb_path = output_dir / "embeddings.csv"
        emb_df.to_csv(emb_path)

        h5ad_path = output_dir / "reduced.h5ad"
        adata.write_h5ad(h5ad_path)

        import json
        summary = {"n_cells": n_cells, "method": method, "n_pcs": n_pcs,
                   "n_neighbors": n_neighbors}
        json_path = output_dir / "dim_reduction_stats.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        return {"reduced_h5ad": str(h5ad_path), "embeddings_csv": str(emb_path),
                "variance_csv": str(var_path), "n_cells": n_cells}


def get_solver(n_pcs: int = 50, n_neighbors: int = 15, method: str = "umap"):
    return SCDimReductionSolver(n_pcs=n_pcs, n_neighbors=n_neighbors, method=method)
