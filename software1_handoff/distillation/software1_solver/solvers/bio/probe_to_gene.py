"""Collapse a probe-level expression matrix into a gene-level matrix.

Given a probe × sample matrix and an annotation table mapping
``probe_id → gene_symbol``, aggregate multiple probes for the same gene
into one row using ``max`` (limma convention), ``mean`` or ``median``.

Probes whose ``gene_symbol`` is missing / blank are dropped.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="probe_to_gene_collapse",
    capability="F09_dimensionality_reduction_features",
    description=(
        "Collapse a probe x sample expression matrix into a gene x sample "
        "matrix.  Multiple probes mapping to the same gene_symbol are "
        "aggregated by max / mean / median (default: max, the limma "
        "convention).  Probes without a gene_symbol are dropped."
    ),
    roles={
        "expression_matrix_csv": RoleSpec(
            Role.PARAMS,
            "Path to a probe-level expression matrix csv (first column "
            "= probe_id, remaining columns = numeric sample values)."),
        "annotation_csv": RoleSpec(
            Role.PARAMS,
            "Path to an annotation csv with probe_id + gene_symbol "
            "columns (extra columns ignored)."),
        "gene_symbol_col": RoleSpec(
            Role.PARAMS,
            "Column name in annotation_csv that holds the gene symbol "
            "(default: 'Gene symbol').",
            optional=True,
        ),
        "method": RoleSpec(
            Role.PARAMS,
            "Aggregation method: 'max' / 'mean' / 'median' (default: max)",
            optional=True,
        ),
    },
    static_params={"method": "max", "gene_symbol_col": "Gene symbol"},
    output_files={"gene_matrix_csv": "gene_matrix.csv"},
)


_VALID_METHODS = {"max", "mean", "median"}


class ProbeToGeneCollapseSolver:
    contract = CONTRACT

    def __init__(self, method: str = "max",
                 gene_symbol_col: str = "Gene symbol"):
        self.method = method
        self.gene_symbol_col = gene_symbol_col

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        method = (mapping.get("method") or self.method or "max").lower()
        if method not in _VALID_METHODS:
            raise ValueError(f"method must be one of {_VALID_METHODS}, "
                              f"got {method!r}")
        gs_col = (mapping.get("gene_symbol_col") or self.gene_symbol_col
                  or "Gene symbol")

        expr_path = mapping.get("expression_matrix_csv")
        annot_path = mapping.get("annotation_csv")
        if not expr_path or not annot_path:
            raise ValueError(
                "probe_to_gene_collapse requires both "
                "'expression_matrix_csv' and 'annotation_csv' in mapping.")

        expr = pd.read_csv(expr_path)
        annot = pd.read_csv(annot_path)

        if "probe_id" not in expr.columns:
            # try the SOFT default
            if expr.columns[0] not in annot.columns and \
               expr.columns[0] != "probe_id":
                expr = expr.rename(columns={expr.columns[0]: "probe_id"})
        if "probe_id" not in annot.columns:
            annot = annot.rename(columns={annot.columns[0]: "probe_id"})

        if gs_col not in annot.columns:
            raise ValueError(
                f"annotation_csv has no column {gs_col!r}.  Columns: "
                f"{list(annot.columns)[:8]}…")

        # Build probe -> gene_symbol map (drop blanks / NaN)
        a = annot[["probe_id", gs_col]].copy()
        a[gs_col] = (a[gs_col].astype(str)
                     .replace({"": np.nan, "nan": np.nan, "None": np.nan})
                     .str.strip())
        a = a.dropna(subset=[gs_col])
        a = a[a[gs_col] != ""]

        merged = expr.merge(a, on="probe_id", how="inner")
        sample_cols = [c for c in expr.columns if c != "probe_id"]
        if not sample_cols:
            raise ValueError("expression_matrix has no sample columns")

        if method == "max":
            agg = merged.groupby(gs_col)[sample_cols].max()
        elif method == "mean":
            agg = merged.groupby(gs_col)[sample_cols].mean()
        else:
            agg = merged.groupby(gs_col)[sample_cols].median()

        agg = agg.reset_index().rename(columns={gs_col: "gene_symbol"})
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / CONTRACT.output_files["gene_matrix_csv"]
        agg.to_csv(path, index=False)
        return {
            "gene_matrix_csv": str(path),
            "n_probes_input":  int(len(expr)),
            "n_probes_with_symbol": int(len(merged)),
            "n_genes_output":  int(len(agg)),
            "method":          method,
        }


def get_solver(method: str = "max", gene_symbol_col: str = "Gene symbol"):
    return ProbeToGeneCollapseSolver(method=method,
                                       gene_symbol_col=gene_symbol_col)


def selftest():
    """6 probes mapped to 3 genes (gene B has 3 probes); test max + mean."""
    import tempfile
    expr = pd.DataFrame({
        "probe_id": ["p1", "p2", "p3", "p4", "p5", "p6"],
        "S1":       [1.0, 2.0, 4.0, 6.0, 10.0, 0.0],
        "S2":       [1.0, 3.0, 5.0, 7.0, 11.0, 0.0],
    })
    annot = pd.DataFrame({
        "probe_id":    ["p1", "p2", "p3", "p4", "p5", "p6"],
        "Gene symbol": ["A",  "B",  "B",  "B",  "C",  ""],
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        expr_p = tmp / "expr.csv"
        annot_p = tmp / "annot.csv"
        expr.to_csv(expr_p, index=False)
        annot.to_csv(annot_p, index=False)

        out_max = get_solver("max").run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "expression_matrix_csv": str(expr_p),
                "annotation_csv":        str(annot_p),
            }),
            output_dir=tmp / "max",
        )
        gm_max = pd.read_csv(out_max["gene_matrix_csv"]).set_index("gene_symbol")
        if out_max["n_genes_output"] != 3:
            diffs.append(f"max: n_genes expected 3, got "
                         f"{out_max['n_genes_output']}")
        if gm_max.loc["B", "S1"] != 6.0:
            diffs.append(f"max: B/S1 expected 6.0, got {gm_max.loc['B','S1']}")
        if gm_max.loc["B", "S2"] != 7.0:
            diffs.append(f"max: B/S2 expected 7.0, got {gm_max.loc['B','S2']}")
        if "p6" in gm_max.index or "" in gm_max.index:
            diffs.append("max: blank-symbol probe should have been dropped")

        out_mean = get_solver("mean").run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "expression_matrix_csv": str(expr_p),
                "annotation_csv":        str(annot_p),
            }),
            output_dir=tmp / "mean",
        )
        gm_mean = pd.read_csv(out_mean["gene_matrix_csv"]).set_index("gene_symbol")
        # B/S1 mean = (2+4+6)/3 = 4
        if abs(gm_mean.loc["B", "S1"] - 4.0) > 1e-9:
            diffs.append(f"mean: B/S1 expected 4.0, got "
                         f"{gm_mean.loc['B','S1']}")
        # A/S2 = 1, C/S1 = 10
        if gm_mean.loc["A", "S2"] != 1.0:
            diffs.append(f"mean: A/S2 expected 1.0, got "
                         f"{gm_mean.loc['A','S2']}")

    return {"ok": len(diffs) == 0,
            "summary": ("max+mean collapse matches hand-derived"
                         if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["probe_to_gene_collapse"]}}
