"""Marker gene / differential expression discovery for scRNA-seq.

Wraps ``sc.tl.rank_genes_groups`` (Wilcoxon by default) to find per-cluster
marker genes.  Input is a normalized + clustered .h5ad (output of
``sc_clustering`` or any adata with a categorical groupby column in ``obs``).

Output:
- ``markers_long.csv`` : long table (group, gene, logfoldchange, pval, pval_adj, score)
- ``top_markers.csv``  : per-group top-N markers as a flat ranked table
- ``markers_summary.json``
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
    name="sc_marker_genes",
    capability="F17_single_cell_marker_genes",
    description=(
        "Find per-cluster marker genes in a clustered scRNA-seq .h5ad via "
        "rank_genes_groups (Wilcoxon default; t-test / logreg supported). "
        "Output: markers_long.csv (all groups), top_markers.csv (top-N per group), "
        "markers_summary.json."
    ),
    roles={
        "input_h5ad": RoleSpec(
            Role.PARAMS,
            "Path to .h5ad (output of sc_clustering or any clustered .h5ad).",
        ),
        "groupby": RoleSpec(
            Role.PARAMS,
            "obs column to group by (e.g. 'cluster', 'cell_type'). Default 'cluster'.",
            optional=True,
        ),
        "method": RoleSpec(
            Role.PARAMS,
            "DE method: wilcoxon (default), t-test, logreg.",
            optional=True,
        ),
        "n_top": RoleSpec(
            Role.PARAMS,
            "Top-N genes per group for the wide top_markers.csv. Default 10.",
            optional=True,
        ),
        "use_raw": RoleSpec(
            Role.PARAMS,
            "Use adata.raw if available (recommended after subsetting to HVGs). "
            "Default false.",
            optional=True,
        ),
    },
    static_params={"groupby": "cluster", "method": "wilcoxon",
                   "n_top": 10, "use_raw": False},
    output_files={
        "markers_long_csv":  "markers_long.csv",
        "top_markers_csv":   "top_markers.csv",
        "summary_json":      "markers_summary.json",
    },
    output_kind={"markers_long_csv": "t", "top_markers_csv": "s",
                 "summary_json": "s"},
)


def _coerce_bool(v: Any, default: bool) -> bool:
    if v is None: return default
    if isinstance(v, bool): return v
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "t")


class SCMarkerGenesSolver:
    contract = CONTRACT

    def __init__(self, groupby: str = "cluster", method: str = "wilcoxon",
                 n_top: int = 10, use_raw: bool = False):
        if not _SC_OK:
            raise ImportError("scanpy+anndata required for sc_marker_genes")
        self.groupby = groupby
        self.method = method
        self.n_top = n_top
        self.use_raw = use_raw

    def run(self, df, mapping, output_dir):
        import json
        input_h5ad = mapping.get("input_h5ad")
        if not input_h5ad:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                solver="sc_marker_genes", hint="input_h5ad is required")
        groupby = str(mapping.get("groupby") or self.groupby)
        method = str(mapping.get("method") or self.method).lower()
        n_top = int(mapping.get("n_top") or self.n_top)
        use_raw = _coerce_bool(mapping.get("use_raw"), self.use_raw)

        adata = anndata.read_h5ad(input_h5ad)

        if groupby not in adata.obs.columns:
            raise OperatorInputError("COLUMN_NOT_FOUND",
                solver="sc_marker_genes", col=groupby,
                available=list(adata.obs.columns)[:20])

        # ensure the groupby column is categorical and has >=2 categories
        adata.obs[groupby] = adata.obs[groupby].astype("category")
        if len(adata.obs[groupby].cat.categories) < 2:
            raise OperatorInputError("INSUFFICIENT_SAMPLES",
                solver="sc_marker_genes",
                required_n=2, actual_n=len(adata.obs[groupby].cat.categories))

        if method not in {"wilcoxon", "t-test", "t-test_overestim_var", "logreg"}:
            raise OperatorInputError("INVALID_PARAM",
                solver="sc_marker_genes",
                hint=f"unknown method {method!r}; allowed: wilcoxon, t-test, logreg")

        sc.tl.rank_genes_groups(adata, groupby=groupby, method=method,
                                 use_raw=use_raw if adata.raw is not None else False)
        result = adata.uns["rank_genes_groups"]
        groups = list(result["names"].dtype.names)

        long_rows: List[Dict[str, Any]] = []
        top_rows: List[Dict[str, Any]] = []
        for grp in groups:
            names = result["names"][grp]
            scores = result["scores"][grp]
            lfc = result.get("logfoldchanges", {})
            lfc_g = lfc[grp] if grp in lfc.dtype.names else np.full(len(names), np.nan)
            pvals = result.get("pvals", {})
            pv_g = pvals[grp] if grp in pvals.dtype.names else np.full(len(names), np.nan)
            pa = result.get("pvals_adj", {})
            pa_g = pa[grp] if grp in pa.dtype.names else np.full(len(names), np.nan)

            for rank, (gene, sc_v, lfc_v, pv_v, pa_v) in enumerate(
                zip(names, scores, lfc_g, pv_g, pa_g)):
                long_rows.append({
                    "group": grp, "rank": int(rank), "gene": str(gene),
                    "score": float(sc_v) if np.isfinite(sc_v) else np.nan,
                    "logfoldchange": float(lfc_v) if np.isfinite(lfc_v) else np.nan,
                    "pval": float(pv_v) if np.isfinite(pv_v) else np.nan,
                    "pval_adj": float(pa_v) if np.isfinite(pa_v) else np.nan,
                })
            # top-N for this group
            for rank in range(min(n_top, len(names))):
                top_rows.append({
                    "group": grp, "rank": int(rank), "gene": str(names[rank]),
                    "score": float(scores[rank]) if np.isfinite(scores[rank]) else np.nan,
                    "logfoldchange": float(lfc_g[rank]) if np.isfinite(lfc_g[rank]) else np.nan,
                    "pval_adj": float(pa_g[rank]) if np.isfinite(pa_g[rank]) else np.nan,
                })

        long_df = pd.DataFrame(long_rows)
        long_path = output_dir / "markers_long.csv"
        long_df.to_csv(long_path, index=False)

        top_df = pd.DataFrame(top_rows)
        top_path = output_dir / "top_markers.csv"
        top_df.to_csv(top_path, index=False)

        summary = {"groupby": groupby, "method": method,
                    "n_groups": len(groups), "n_top_per_group": n_top,
                    "n_total_rows": int(len(long_df))}
        json_path = output_dir / "markers_summary.json"
        json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {"markers_long_csv": str(long_path),
                "top_markers_csv": str(top_path),
                "summary_json": str(json_path),
                "n_groups": len(groups)}


def get_solver(groupby: str = "cluster", method: str = "wilcoxon",
               n_top: int = 10, use_raw: bool = False):
    return SCMarkerGenesSolver(groupby=groupby, method=method,
                                n_top=n_top, use_raw=use_raw)
