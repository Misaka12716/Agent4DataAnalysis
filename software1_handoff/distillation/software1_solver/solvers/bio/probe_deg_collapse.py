"""Collapse a probe-level DEG table to gene-level using GEO2R's
default convention: per gene, keep the row with the smallest
``adj_p_value`` (tie-break: smaller ``p_value``, then larger ``|logFC|``).

This is the standard approach when comparing limma-on-probes results
across pipelines: the choice of "which probe represents this gene" is
made AFTER the differential test, not before by collapsing the
expression matrix.  Doing it this way means that two pipelines using
the same probe-level limma but different probe-to-gene strategies will
yield comparable gene-level top tables.

Solver name: ``probe_deg_collapse_to_gene``.

Inputs (mapping):

  - ``deg_table_csv``  (PARAMS, required) — path to a probe-level DEG
    table CSV.  Must contain ``adj_p_value``, ``p_value``, ``logFC``.
    The probe identifier must be in either a ``probe_id`` column or
    the first column.  May also have a ``gene_symbol`` column already
    populated; if present, it is used directly and ``annotation_csv``
    becomes optional.
  - ``annotation_csv`` (PARAMS, optional) — path to annotation CSV
    with a probe identifier column and a gene symbol column.  Used
    when ``deg_table`` does not already include a gene symbol column.
  - ``probe_col``      (PARAMS, optional) — column name in
    ``deg_table_csv`` that holds the probe ID.  Default: auto-detect
    (try ``probe_id``, fall back to the first column).
  - ``gene_col``       (PARAMS, optional) — column name in
    ``annotation_csv`` for gene symbol.  Default: auto-detect from
    ``["Gene symbol", "gene_symbol", "GeneSymbol", "symbol"]``.
  - ``annotation_probe_col`` (PARAMS, optional) — probe id column in
    ``annotation_csv``.  Default: auto-detect (``probe_id``,
    ``IDENTIFIER``, first column).
  - ``drop_unmapped``  (PARAMS, optional, default True) — whether to
    drop probes with no resolvable gene symbol.

Output: ``gene_deg_table.csv`` with one row per gene, sorted by
``adj_p_value`` ascending.  The picked row is copied verbatim and
augmented with ``probe_id`` (id of the chosen probe) and
``n_probes_for_gene`` (how many probes mapped to that gene before
collapse).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="probe_deg_collapse_to_gene",
    capability="F18_bio_probe_to_gene_deg_collapse",
    description=(
        "Collapse a probe-level DEG table to gene-level by keeping, "
        "for each gene symbol, the probe with the smallest "
        "adj_p_value (GEO2R-style 'best probe per gene').  Tie-break: "
        "smaller p_value, then larger |logFC|.  Output: "
        "gene_deg_table.csv ranked by adj_p_value."
    ),
    roles={
        "deg_table_csv": RoleSpec(
            Role.PARAMS,
            "Path to a probe-level DEG table CSV with at least "
            "adj_p_value, p_value, logFC columns and a probe identifier."),
        "annotation_csv": RoleSpec(
            Role.PARAMS,
            "Optional path to annotation CSV providing probe→gene "
            "mapping.  Required only if deg_table_csv lacks a "
            "gene_symbol column.",
            optional=True),
        "probe_col": RoleSpec(
            Role.PARAMS,
            "Column name in deg_table_csv holding probe IDs.  "
            "Default: auto-detect.",
            optional=True),
        "gene_col": RoleSpec(
            Role.PARAMS,
            "Column name in annotation_csv holding gene symbols.  "
            "Default: auto-detect.",
            optional=True),
        "annotation_probe_col": RoleSpec(
            Role.PARAMS,
            "Column name in annotation_csv holding probe IDs.  "
            "Default: auto-detect.",
            optional=True),
        "drop_unmapped": RoleSpec(
            Role.PARAMS,
            "Whether to drop rows whose probe has no gene symbol.  "
            "Default: True.",
            optional=True),
    },
    static_params={"drop_unmapped": True},
    output_files={"gene_deg_table_csv": "gene_deg_table.csv"},
)


_GENE_COL_CANDIDATES = ["Gene symbol", "gene_symbol", "GeneSymbol",
                          "symbol", "Symbol", "Gene.symbol",
                          "GENE_SYMBOL"]
_PROBE_COL_CANDIDATES_DEG = ["probe_id", "ProbeID", "ID", "id",
                                "Probe", "PROBE_ID"]
_PROBE_COL_CANDIDATES_ANN = ["probe_id", "IDENTIFIER", "ID", "id",
                                "ProbeID", "PROBE_ID"]


def _auto_pick(cols: List[str], candidates: List[str],
                fallback_first: bool = True) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return c
    if fallback_first and cols:
        return cols[0]
    return None


class ProbeDegCollapseToGeneSolver:
    contract = CONTRACT

    def __init__(self, drop_unmapped: bool = True):
        self.drop_unmapped = drop_unmapped

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        deg_path = mapping.get("deg_table_csv")
        if not deg_path:
            raise ValueError(
                "probe_deg_collapse_to_gene requires 'deg_table_csv' "
                "in mapping.")
        ann_path = mapping.get("annotation_csv")
        drop_unmapped = mapping.get("drop_unmapped")
        if drop_unmapped is None:
            drop_unmapped = self.drop_unmapped
        drop_unmapped = bool(drop_unmapped)

        deg = pd.read_csv(deg_path)
        if deg.empty:
            raise ValueError(f"deg_table_csv at {deg_path} is empty")
        for col in ("adj_p_value", "p_value", "logFC"):
            if col not in deg.columns:
                raise ValueError(
                    f"deg_table_csv must contain {col!r}; "
                    f"have {list(deg.columns)}")

        probe_col = (mapping.get("probe_col")
                       or _auto_pick(list(deg.columns),
                                     _PROBE_COL_CANDIDATES_DEG))
        if probe_col is None or probe_col not in deg.columns:
            raise ValueError(
                f"could not determine probe column in {deg_path}; "
                f"got {probe_col!r}, columns={list(deg.columns)}")
        # Many limma-style outputs name the probe column 'gene_symbol'
        # because the solver stamped it that way.  Treat the first column
        # as the probe id when it has clearly probe-shaped values.
        deg = deg.rename(columns={probe_col: "probe_id"}).copy()
        deg["probe_id"] = deg["probe_id"].astype(str)

        # Build the probe → gene map.
        if "gene_symbol" in deg.columns and probe_col != "gene_symbol":
            # Gene symbol already in DEG table — use as-is.
            symbol_series = deg.set_index("probe_id")["gene_symbol"]
            gene_source = "deg_inline"
        else:
            if not ann_path:
                raise ValueError(
                    "deg_table_csv has no 'gene_symbol' column and no "
                    "annotation_csv was provided; cannot collapse to "
                    "gene level.")
            ann = pd.read_csv(ann_path)
            ann_cols = list(ann.columns)
            # Caller-provided hints may be wrong (e.g. an upstream LLM
            # may confuse the gene-symbol column of annotation_csv with
            # an unrelated column name from another csv).  Validate
            # before trusting; otherwise fall through to auto-detect.
            ann_probe_hint = mapping.get("annotation_probe_col")
            if ann_probe_hint and ann_probe_hint not in ann_cols:
                ann_probe_hint = None
            ann_gene_hint = mapping.get("gene_col")
            if ann_gene_hint and ann_gene_hint not in ann_cols:
                ann_gene_hint = None
            ann_probe_col = (ann_probe_hint
                                or _auto_pick(ann_cols,
                                                _PROBE_COL_CANDIDATES_ANN))
            ann_gene_col = (ann_gene_hint
                                or _auto_pick(ann_cols,
                                                _GENE_COL_CANDIDATES,
                                                fallback_first=False))
            if ann_probe_col is None or ann_probe_col not in ann_cols:
                raise ValueError(
                    f"could not determine probe column in {ann_path}; "
                    f"got {ann_probe_col!r}, columns={ann_cols}")
            if ann_gene_col is None or ann_gene_col not in ann_cols:
                raise ValueError(
                    f"could not determine gene-symbol column in "
                    f"{ann_path}; tried {_GENE_COL_CANDIDATES}, "
                    f"columns={ann_cols}")
            ann2 = ann[[ann_probe_col, ann_gene_col]].copy()
            ann2.columns = ["probe_id", "gene_symbol"]
            ann2["probe_id"] = ann2["probe_id"].astype(str)
            ann2 = ann2.drop_duplicates(subset=["probe_id"], keep="first")
            symbol_series = ann2.set_index("probe_id")["gene_symbol"]
            gene_source = "annotation"

        deg["gene_symbol"] = (
            deg["probe_id"].map(symbol_series)
                          .astype(object)
                          .where(lambda s: s.notna(), other=None))
        # treat empty / whitespace as missing
        deg["gene_symbol"] = deg["gene_symbol"].apply(
            lambda v: None if (v is None
                                or (isinstance(v, float) and np.isnan(v))
                                or (isinstance(v, str) and not v.strip()))
            else (v.strip() if isinstance(v, str) else str(v)))

        n_probes_total = int(len(deg))
        unmapped_mask = deg["gene_symbol"].isna()
        n_unmapped = int(unmapped_mask.sum())
        if drop_unmapped:
            deg = deg.loc[~unmapped_mask].copy()
        if deg.empty:
            raise ValueError(
                f"no probes left after dropping unmapped (n_total="
                f"{n_probes_total}, n_unmapped={n_unmapped})")

        # Tie-break ranking key: (adj_p, p, -|logFC|) — smaller is better.
        deg["__abs_logfc__"] = deg["logFC"].abs()
        deg = deg.sort_values(
            by=["adj_p_value", "p_value", "__abs_logfc__"],
            ascending=[True, True, False],
            kind="mergesort",
        )
        n_per_gene = (deg.groupby("gene_symbol", sort=False)
                          .size().rename("n_probes_for_gene"))

        # Drop duplicates within each gene, keeping the first
        # (smallest adj_p_value) row.
        gene_deg = (deg.drop_duplicates(subset=["gene_symbol"], keep="first")
                        .merge(n_per_gene, left_on="gene_symbol",
                                right_index=True, how="left"))
        gene_deg = gene_deg.drop(columns=["__abs_logfc__"])
        gene_deg = gene_deg.sort_values(
            by=["adj_p_value", "p_value"], ascending=[True, True],
            kind="mergesort",
        )

        # Reorder columns: gene_symbol first, then probe_id, then the rest.
        front = ["gene_symbol", "probe_id"]
        rest = [c for c in gene_deg.columns if c not in front]
        gene_deg = gene_deg[front + rest]

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / CONTRACT.output_files["gene_deg_table_csv"]
        gene_deg.to_csv(out_path, index=False)

        return {
            "gene_deg_table_csv":   str(out_path),
            "n_probes_input":       n_probes_total,
            "n_probes_unmapped":    n_unmapped,
            "n_probes_kept":        int(n_probes_total - n_unmapped),
            "n_genes_output":       int(len(gene_deg)),
            "n_significant":        int((gene_deg["adj_p_value"] < 0.05).sum()),
            "drop_unmapped":        drop_unmapped,
            "gene_source":          gene_source,
            "tie_break":            "min_adj_p_then_min_p_then_max_abs_logfc",
        }


def get_solver(drop_unmapped: bool = True):
    return ProbeDegCollapseToGeneSolver(drop_unmapped=drop_unmapped)


def selftest():
    """Verify per-gene best-probe collapse on a hand-built fixture.

    Setup: 6 probes, 4 genes.

      probe   gene   adj_p   p     logFC
      p1      G1     0.01   0.001   3.0
      p2      G1     0.05   0.005   2.0
      p3      G1     0.01   0.002   3.5     # tie with p1 on adj_p, larger p → loses
      p4      G2     0.02   0.001   1.0
      p5      G3     0.10   0.05   -0.5
      p6      None   0.001  0.0001  5.0     # unmapped, must be dropped

    Expected gene table:
      G1 → p1   (smallest adj_p, then smallest p)
      G2 → p4
      G3 → p5
      4 probes kept (after dropping p6), 3 genes.
    """
    import tempfile

    deg = pd.DataFrame({
        "probe_id":   ["p1","p2","p3","p4","p5","p6"],
        "adj_p_value":[0.01,0.05,0.01,0.02,0.10,0.001],
        "p_value":    [0.001,0.005,0.002,0.001,0.05,0.0001],
        "logFC":      [3.0, 2.0, 3.5, 1.0, -0.5, 5.0],
    })
    ann = pd.DataFrame({
        "probe_id":    ["p1","p2","p3","p4","p5","p6"],
        "Gene symbol": ["G1","G1","G1","G2","G3", None],
    })

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        deg_p = tmp / "probe_deg.csv"; deg.to_csv(deg_p, index=False)
        ann_p = tmp / "ann.csv";       ann.to_csv(ann_p, index=False)

        out = get_solver().run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "deg_table_csv":  str(deg_p),
                "annotation_csv": str(ann_p),
            }),
            output_dir=tmp / "out",
        )
        if out["n_probes_input"] != 6:
            diffs.append(f"n_probes_input={out['n_probes_input']} != 6")
        if out["n_probes_unmapped"] != 1:
            diffs.append(f"n_probes_unmapped={out['n_probes_unmapped']} != 1")
        if out["n_genes_output"] != 3:
            diffs.append(f"n_genes_output={out['n_genes_output']} != 3")
        gene_df = pd.read_csv(out["gene_deg_table_csv"])
        picked = dict(zip(gene_df["gene_symbol"], gene_df["probe_id"]))
        expected = {"G1": "p1", "G2": "p4", "G3": "p5"}
        if picked != expected:
            diffs.append(f"picked={picked} != expected={expected}")
        # Verify "n_probes_for_gene" column is correct.
        npfg = dict(zip(gene_df["gene_symbol"],
                          gene_df["n_probes_for_gene"]))
        expected_n = {"G1": 3, "G2": 1, "G3": 1}
        if npfg != expected_n:
            diffs.append(f"n_probes_for_gene={npfg} != {expected_n}")
        # Sorted ascending by adj_p_value
        adjs = gene_df["adj_p_value"].tolist()
        if adjs != sorted(adjs):
            diffs.append(f"output not sorted by adj_p_value: {adjs}")

        # Also check: when DEG already has gene_symbol column, ann not needed.
        deg_with_gene = deg.copy()
        deg_with_gene["gene_symbol"] = ["G1","G1","G1","G2","G3","Gx"]
        deg_with_gene_p = tmp / "deg_with_gene.csv"
        deg_with_gene.to_csv(deg_with_gene_p, index=False)
        out2 = get_solver().run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({"deg_table_csv": str(deg_with_gene_p)}),
            output_dir=tmp / "out2",
        )
        if out2["gene_source"] != "deg_inline":
            diffs.append(
                f"gene_source should be deg_inline; got {out2['gene_source']}")
        gd2 = pd.read_csv(out2["gene_deg_table_csv"])
        if set(gd2["gene_symbol"]) != {"G1","G2","G3","Gx"}:
            diffs.append(
                f"inline-gene path missed genes: {set(gd2['gene_symbol'])}")

    return {
        "ok": len(diffs) == 0,
        "summary": ("per-gene best-probe collapse matches hand calc + "
                     "inline gene_symbol path works"
                     if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                     "tested": ["probe_deg_collapse_to_gene"]},
    }
