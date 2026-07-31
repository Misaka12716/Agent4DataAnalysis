"""Single-cell RNA-seq gene filtering and normalization operator.

Performs quality-control filtering and library-size normalization on a
single-cell expression matrix.  Accepts .h5ad or CSV input.  Backed by scanpy.

Output: normalized.h5ad, qc_metrics.csv.
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
    name="gene_filter_normalize",
    capability="F17_single_cell_preprocessing",
    description=(
        "Single-cell RNA-seq QC and normalization. Filters genes/cells by "
        "counts, normalizes to counts-per-10K, log1p transforms. "
        "Accepts .h5ad file or CSV input. Output: normalized.h5ad, qc_metrics.csv."
    ),
    roles={
        "input_file": RoleSpec(
            Role.PARAMS,
            "Path to .h5ad file or CSV (genes x cells). When CSV, first column = gene_id.",
        ),
        "min_genes_per_cell": RoleSpec(
            Role.PARAMS,
            "Min genes expressed per cell. Default: 200.",
            optional=True,
        ),
        "min_cells_per_gene": RoleSpec(
            Role.PARAMS,
            "Min cells expressing a gene. Default: 3.",
            optional=True,
        ),
        "target_sum": RoleSpec(
            Role.PARAMS,
            "Normalization target sum. Default: 1e4 (counts per 10K).",
            optional=True,
        ),
    },
    static_params={"min_genes_per_cell": 200, "min_cells_per_gene": 3,
                   "target_sum": 1e4},
    output_files={
        "normalized_h5ad": "normalized.h5ad",
        "qc_metrics_csv": "qc_metrics.csv",
    },
    output_kind={"normalized_h5ad": "t", "qc_metrics_csv": "s"},
)


class GeneFilterNormalizeSolver:
    contract = CONTRACT

    def __init__(self, min_genes_per_cell: int = 200,
                 min_cells_per_gene: int = 3, target_sum: float = 1e4):
        if not _SC_OK:
            raise ImportError("scanpy+anndata required for gene_filter_normalize")
        self.min_genes = min_genes_per_cell
        self.min_cells = min_cells_per_gene
        self.target_sum = target_sum

    def _load(self, path: str):
        path_lower = str(path).lower()
        if path_lower.endswith('.h5ad'):
            return anndata.read_h5ad(path)
        elif path_lower.endswith('.csv'):
            df = pd.read_csv(path)
            df = df.set_index(df.columns[0])
            X = df.to_numpy(dtype=np.float32).T
            return anndata.AnnData(X, var=pd.DataFrame(index=df.index),
                                   obs=pd.DataFrame(index=df.columns))
        else:
            raise OperatorInputError("UNSUPPORTED_FILE_FORMAT",
                solver="gene_filter_normalize",
                hint=f"unsupported file format: {path}")

    def run(self, df, mapping, output_dir):
        input_file = mapping.get("input_file")
        if not input_file:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="gene_filter_normalize",
                                     hint="input_file (path to .h5ad or genes×cells .csv) is required")
        min_genes = int(mapping.get("min_genes_per_cell") or self.min_genes)
        min_cells = int(mapping.get("min_cells_per_gene") or self.min_cells)
        target_sum = float(mapping.get("target_sum") or self.target_sum)

        adata = self._load(input_file)
        n_cells_before, n_genes_before = adata.shape

        sc.pp.filter_cells(adata, min_genes=min_genes)
        sc.pp.filter_genes(adata, min_cells=min_cells)

        n_cells_after, n_genes_after = adata.shape
        adata.layers["counts"] = adata.X.copy()
        sc.pp.normalize_total(adata, target_sum=target_sum)
        sc.pp.log1p(adata)

        sc.pp.calculate_qc_metrics(adata, inplace=True)
        qc_df = adata.obs[["n_genes_by_counts", "total_counts",
                           "pct_counts_in_top_50_genes"]].copy()
        qc_df.index.name = "cell_barcode"
        qc_path = output_dir / "qc_metrics.csv"
        qc_df.to_csv(qc_path)

        h5ad_path = output_dir / "normalized.h5ad"
        adata.write_h5ad(h5ad_path)

        import json
        stats = {"n_cells_before": n_cells_before, "n_genes_before": n_genes_before,
                 "n_cells_after": n_cells_after, "n_genes_after": n_genes_after,
                 "target_sum": target_sum}
        stats_path = output_dir / "preprocessing_stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

        return {"normalized_h5ad": str(h5ad_path), "qc_metrics_csv": str(qc_path),
                "n_cells_after": n_cells_after, "n_genes_after": n_genes_after}


def get_solver(min_genes_per_cell: int = 200, min_cells_per_gene: int = 3,
               target_sum: float = 1e4):
    return GeneFilterNormalizeSolver(min_genes_per_cell=min_genes_per_cell,
                                      min_cells_per_gene=min_cells_per_gene,
                                      target_sum=target_sum)
