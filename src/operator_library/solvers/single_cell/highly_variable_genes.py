"""Highly variable gene (HVG) selection operator for single-cell RNA-seq.

Identifies the most informative genes in a single-cell dataset using
seurat_v3 or seurat flavor.  Must be run after gene_filter_normalize.

Input: normalized .h5ad file. Output: hvg.h5ad (subsetted), hvg_list.csv.
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
    name="highly_variable_genes",
    capability="F17_single_cell_feature_selection",
    description=(
        "Select highly variable genes (HVG) from a normalized scRNA-seq dataset. "
        "Uses seurat_v3 or seurat flavor. Input: .h5ad file. "
        "Output: hvg.h5ad (subsetted), hvg_list.csv, hvg_stats.csv."
    ),
    roles={
        "input_h5ad": RoleSpec(
            Role.PARAMS,
            "Path to a normalized .h5ad file (output of gene_filter_normalize).",
        ),
        "n_top_genes": RoleSpec(
            Role.PARAMS,
            "Number of top HVGs to keep. Default: 2000.",
            optional=True,
        ),
        "flavor": RoleSpec(
            Role.PARAMS,
            "HVG selection method: seurat_v3 (default, needs raw counts in .layers['counts']) "
            "or seurat.",
            optional=True,
        ),
        "batch_key": RoleSpec(
            Role.PARAMS,
            "Optional batch key in adata.obs for batch-aware HVG (seurat_v3 only).",
            optional=True,
        ),
    },
    static_params={"n_top_genes": 2000, "flavor": "seurat_v3"},
    output_files={
        "hvg_h5ad": "hvg.h5ad",
        "hvg_list_csv": "hvg_list.csv",
        "hvg_stats_csv": "hvg_stats.csv",
    },
    output_kind={"hvg_h5ad": "t", "hvg_list_csv": "s", "hvg_stats_csv": "s"},
)


class HVGSolver:
    contract = CONTRACT

    def __init__(self, n_top_genes: int = 2000, flavor: str = "seurat_v3"):
        if not _SC_OK:
            raise ImportError("scanpy+anndata required for highly_variable_genes")
        self.n_top_genes = n_top_genes
        self.flavor = flavor

    @staticmethod
    def _seurat_v3_available() -> bool:
        try:
            import skmisc.loess  # noqa: F401
            return True
        except Exception:
            return False

    def run(self, df, mapping, output_dir):
        input_h5ad = mapping.get("input_h5ad")
        if not input_h5ad:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="highly_variable_genes",
                                     hint="input_h5ad is required")
        n_top = int(mapping.get("n_top_genes") or self.n_top_genes)
        flavor = str(mapping.get("flavor") or self.flavor)
        batch_key = mapping.get("batch_key") or None

        adata = anndata.read_h5ad(input_h5ad)
        n_genes_before = adata.n_vars
        # cap n_top to available genes
        n_top = min(n_top, max(2, n_genes_before - 1))

        # seurat_v3 needs scikit-misc; fall back to seurat (default) if missing
        if flavor == "seurat_v3" and not self._seurat_v3_available():
            flavor = "seurat"
        # seurat_v3 also needs raw counts; without them fall back to seurat
        if flavor == "seurat_v3" and "counts" not in adata.layers:
            flavor = "seurat"

        kwargs = {"flavor": flavor, "n_top_genes": n_top}
        if flavor == "seurat_v3":
            kwargs["layer"] = "counts"
        if batch_key and batch_key in adata.obs.columns:
            kwargs["batch_key"] = batch_key

        sc.pp.highly_variable_genes(adata, **kwargs)

        n_hvg = int(adata.var["highly_variable"].sum())
        hvg_list = adata.var_names[adata.var["highly_variable"]].tolist()

        hvg_list_df = pd.DataFrame({"gene": hvg_list})
        list_path = output_dir / "hvg_list.csv"
        hvg_list_df.to_csv(list_path, index=False)

        stats_df = adata.var[["highly_variable", "means", "dispersions",
                              "dispersions_norm"]].copy()
        stats_path = output_dir / "hvg_stats.csv"
        stats_df.to_csv(stats_path)

        adata_subset = adata[:, adata.var["highly_variable"]].copy()
        h5ad_path = output_dir / "hvg.h5ad"
        adata_subset.write_h5ad(h5ad_path)

        import json
        summary = {"n_genes_before": n_genes_before, "n_hvg": n_hvg,
                   "flavor": flavor, "n_top_genes": n_top}
        json_path = output_dir / "hvg_summary.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        return {"hvg_h5ad": str(h5ad_path), "hvg_list_csv": str(list_path),
                "hvg_stats_csv": str(stats_path),
                "n_hvg": n_hvg}


def get_solver(n_top_genes: int = 2000, flavor: str = "seurat_v3"):
    return HVGSolver(n_top_genes=n_top_genes, flavor=flavor)
