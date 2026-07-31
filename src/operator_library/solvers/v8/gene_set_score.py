"""Single-sample Gene Set Enrichment Analysis (ssGSEA) — per-sample
pathway scores from a sample × gene expression table.

For each sample, ssGSEA (Barbie et al. 2009) computes a separate
enrichment score per pathway from that sample's gene ranking; no group
contrast is needed.  This turns a gene-level expression matrix into a
pathway-level "signature score" matrix that downstream analyses
(classifiers, survival, clustering) can use directly.

Backed by ``gseapy.ssgsea`` with ``sample_norm_method='rank'`` (the
original Barbie 2009 specification).

References
----------
* Barbie DA et al. (2009) "Systematic RNA interference reveals that
  oncogenic KRAS-driven cancers require TBK1" *Nature* 462:108-112.
* Fang Z et al. (2023) "GSEApy" *Bioinformatics* 39:btac757.

Outputs
-------
``sample_scores.csv``
    sample × pathway NES matrix (one row per sample).  First column is
    the sample id, then one column per pathway.
``sample_scores_long.csv``
    long format (sample, pathway, ES, NES) for easy joining /
    downstream stat tests.
``ssgsea_summary.json``
    {n_samples, n_pathways_tested, n_pathways_total, sample_id_col,
    gmt_path, top_pathway_by_mean_NES}
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


_BUNDLED_GMT = (Path(__file__).parent.parent / "bio" / "data"
                 / "msigdb_hallmark_2020_human.gmt")


CONTRACT = SolverContract(
    name="gene_set_score",
    capability="F_bio_ssgsea",
    description=(
        "Per-sample pathway scores (ssGSEA; Barbie 2009) on a sample × "
        "gene expression table.  Computes an Enrichment Score (ES) "
        "and Normalized ES (NES) for every (sample, pathway) pair. "
        "Output: a sample × pathway NES matrix suitable as features "
        "for downstream models (classifiers, survival, clustering).  "
        "Use this when you want pathway-level signatures from "
        "expression data, without needing a group contrast."
    ),
    roles={
        "sample_id_col": RoleSpec(
            Role.ID,
            "Sample identifier column. One row per sample.",
            optional=True),
        "skip_cols": RoleSpec(
            Role.PARAMS,
            "Extra non-gene columns to ignore (e.g. group / phenotype "
            "/ batch labels).  Default: [].",
            optional=True),
        "gene_set_db_path": RoleSpec(
            Role.PARAMS,
            "Path to a GMT file.  Default: bundled MSigDB Hallmark 2020.",
            optional=True),
        "min_size": RoleSpec(
            Role.PARAMS, "Drop gene sets smaller than this. Default 15.",
            optional=True),
        "max_size": RoleSpec(
            Role.PARAMS, "Drop gene sets larger than this. Default 500.",
            optional=True),
        "weight": RoleSpec(
            Role.PARAMS,
            "Weighting exponent for |score| in the running sum. "
            "Default 0.25 (Barbie 2009 ssGSEA convention).",
            optional=True),
        "sample_norm_method": RoleSpec(
            Role.PARAMS,
            "Per-sample normalisation: 'rank' (default), 'log', "
            "'log_rank', 'custom'.",
            optional=True),
        "seed": RoleSpec(
            Role.PARAMS, "RNG seed. Default 2026.", optional=True),
    },
    static_params={
        "min_size":           15,
        "max_size":           500,
        "weight":             0.25,
        "sample_norm_method": "rank",
        "seed":               2026,
    },
    output_files={
        "sample_scores_csv":      "sample_scores.csv",
        "sample_scores_long_csv": "sample_scores_long.csv",
        "ssgsea_summary_json":    "ssgsea_summary.json",
    },
    output_kind={"sample_scores_csv": "s",
                  "sample_scores_long_csv": "s",
                  "ssgsea_summary_json": "s"},
)


def _unwrap_mapping_value(v: Any) -> Any:
    """Unwrap ``{'<name>': value}`` → ``value`` for single-key dicts.

    The LLM-driven ``mapping_engine`` sometimes emits ``{"min_size": 15}``
    instead of plain ``15`` for PARAMS roles whose name matches the
    ``static_params`` key.  This caused ``int({'min_size': 15})``
    TypeErrors during solver execution.  This helper makes the
    solvers tolerant to that nesting.
    """
    if isinstance(v, dict) and len(v) == 1:
        only = next(iter(v.values()))
        return _unwrap_mapping_value(only)
    return v


def _parse_gmt_dict(path: Path) -> Dict[str, List[str]]:
    sets: Dict[str, List[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        parts = raw.rstrip("\r\n").split("\t")
        if len(parts) < 3:
            continue
        term = parts[0].strip()
        genes = [g.strip() for g in parts[2:] if g.strip()]
        if genes:
            sets[term] = [g.upper() for g in genes]
    return sets


class GeneSetScoreSolver:
    contract = CONTRACT

    def __init__(self, min_size: int = 15, max_size: int = 500,
                  weight: float = 0.25,
                  sample_norm_method: str = "rank",
                  seed: int = 2026,
                  gene_set_db_path: Optional[str] = None):
        self.min_size = int(min_size)
        self.max_size = int(max_size)
        self.weight = float(weight)
        self.sample_norm_method = str(sample_norm_method)
        self.seed = int(seed)
        self.gene_set_db_path = gene_set_db_path

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        import gseapy as gp

        sample_id_col = _unwrap_mapping_value(mapping.get("sample_id_col"))
        skip_cols = list(_unwrap_mapping_value(mapping.get("skip_cols")) or [])
        gmt = (_unwrap_mapping_value(mapping.get("gene_set_db_path"))
                or self.gene_set_db_path)
        if gmt is None:
            # Auto-discover a workspace-local .gmt (e.g., synthetic
            # ``pathways.gmt`` shipped with the benchmark task) before
            # falling back to the bundled MSigDB Hallmark library.
            here = Path(output_dir).resolve()
            for _ in range(4):
                if not here.exists():
                    break
                local = sorted(here.glob("*.gmt"))
                if local:
                    gmt = local[0]
                    break
                if here.parent == here:
                    break
                here = here.parent
        gmt = gmt if gmt else str(_BUNDLED_GMT)
        gmt = Path(gmt)
        if not gmt.is_file():
            raise FileNotFoundError(f"GMT not found: {gmt}")
        sets = _parse_gmt_dict(gmt)
        if not sets:
            raise ValueError(f"no gene sets parsed from {gmt}")

        # Build the gene × sample expression matrix
        skip = set(skip_cols) | {"__row_id__"}
        if sample_id_col:
            skip.add(sample_id_col)
        gene_cols: List[str] = [
            c for c in df.columns
            if c not in skip and pd.api.types.is_numeric_dtype(df[c])
        ]
        if len(gene_cols) < 30:
            raise ValueError(
                f"gene_set_score needs ≥30 gene columns; got {len(gene_cols)}")

        if sample_id_col and sample_id_col in df.columns:
            sample_ids = df[sample_id_col].astype(str).tolist()
        else:
            sample_ids = [f"sample_{i}" for i in range(len(df))]
        if len(set(sample_ids)) != len(sample_ids):
            # de-dup
            sample_ids = [f"{s}__{i}" for i, s in enumerate(sample_ids)]

        expr = df[gene_cols].copy().apply(pd.to_numeric, errors="coerce")
        expr.columns = [str(c).upper() for c in expr.columns]
        # gene × sample (gseapy convention)
        expr_gs = expr.T.copy()
        expr_gs.columns = sample_ids
        expr_gs.index.name = "gene_id"

        min_size = int(_unwrap_mapping_value(mapping.get("min_size"))
                         or self.min_size)
        max_size = int(_unwrap_mapping_value(mapping.get("max_size"))
                         or self.max_size)
        weight = float(_unwrap_mapping_value(mapping.get("weight"))
                         or self.weight)
        norm = (_unwrap_mapping_value(mapping.get("sample_norm_method"))
                 or self.sample_norm_method or "rank")
        seed = int(_unwrap_mapping_value(mapping.get("seed"))
                     or self.seed)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = gp.ssgsea(
                data=expr_gs,
                gene_sets=sets,
                outdir=None,
                sample_norm_method=norm,
                min_size=min_size,
                max_size=max_size,
                weight=weight,
                threads=1,
                seed=seed,
                no_plot=True,
                verbose=False,
                permutation_num=None,
            )
        long_df = res.res2d.copy()
        # Schema: Name=sample, Term=pathway, ES, NES
        long_df = long_df.rename(columns={
            "Name": "sample", "Term": "pathway",
        })
        for c in ("ES", "NES"):
            if c in long_df.columns:
                long_df[c] = pd.to_numeric(long_df[c], errors="coerce")

        # Pivot to sample × pathway NES matrix
        wide = (long_df.pivot_table(index="sample", columns="pathway",
                                       values="NES", aggfunc="mean")
                       .reset_index())
        # keep sample column first
        # add canonical id-col name as the first column
        sid_name = sample_id_col or "sample_id"
        wide = wide.rename(columns={"sample": sid_name})

        ss_path = out_dir / CONTRACT.output_files["sample_scores_csv"]
        wide.to_csv(ss_path, index=False)
        long_path = out_dir / CONTRACT.output_files["sample_scores_long_csv"]
        long_df.to_csv(long_path, index=False)

        # Summary
        pw_mean = (long_df.groupby("pathway")["NES"].mean()
                     .sort_values(ascending=False))
        top_pw = pw_mean.head(5).index.tolist()
        summary = {
            "n_samples":           int(len(wide)),
            "n_pathways_tested":   int(len(pw_mean)),
            "n_pathways_total":    int(len(sets)),
            "sample_id_col":       sid_name,
            "skip_cols":           skip_cols,
            "weight":              weight,
            "sample_norm_method":  norm,
            "seed":                seed,
            "top_5_pathways_by_mean_NES": top_pw,
            "gmt_path":            str(gmt),
        }
        sj_path = out_dir / CONTRACT.output_files["ssgsea_summary_json"]
        sj_path.write_text(json.dumps(summary, indent=2, default=str),
                            encoding="utf-8")
        return {
            "sample_scores_csv":      str(ss_path),
            "sample_scores_long_csv": str(long_path),
            "ssgsea_summary_json":    str(sj_path),
            **summary,
        }


def get_solver(min_size: int = 15, max_size: int = 500,
                weight: float = 0.25,
                sample_norm_method: str = "rank",
                seed: int = 2026,
                gene_set_db_path: Optional[str] = None
                ) -> GeneSetScoreSolver:
    return GeneSetScoreSolver(
        min_size=min_size, max_size=max_size, weight=weight,
        sample_norm_method=sample_norm_method, seed=seed,
        gene_set_db_path=gene_set_db_path,
    )


# ---------------------------------------------------------------------------
# GT selftest
# ---------------------------------------------------------------------------
def _gt_a_injection_high_subset() -> List[str]:
    """GT-A — inject PATH_A overexpression in samples S0-S4; the mean
    NES of PATH_A in those 5 samples must be strictly greater than in
    samples S5-S9."""
    import tempfile
    rng = np.random.default_rng(2026)
    universe = [f"G{i:04d}" for i in range(400)]
    pathway_a = universe[:50]
    pathway_b = universe[50:120]
    sample_ids = [f"S{i}" for i in range(10)]
    rows = []
    for i, s in enumerate(sample_ids):
        base = rng.uniform(2, 8, size=len(universe))
        if i < 5:
            for j, g in enumerate(universe):
                if g in pathway_a:
                    base[j] += 4.0     # boost PATH_A in S0..S4
        rows.append([s] + base.tolist())
    df = pd.DataFrame(rows, columns=["sample_id"] + universe)
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "g.gmt"
        gmt.write_text(
            "PATH_A\t\t" + "\t".join(pathway_a) + "\n"
            "PATH_B\t\t" + "\t".join(pathway_b) + "\n",
            encoding="utf-8")
        out = get_solver(min_size=10, max_size=200).run(
            df, ColumnMapping({
                "sample_id_col":    "sample_id",
                "gene_set_db_path": str(gmt),
            }),
            Path(tmp / "out"),
        )
        long_df = pd.read_csv(out["sample_scores_long_csv"])
        a = long_df[long_df["pathway"] == "PATH_A"]
        if a.empty:
            diffs.append("[A] PATH_A absent from result")
            return diffs
        hi = a[a["sample"].isin([f"S{i}" for i in range(5)])]["NES"].mean()
        lo = a[a["sample"].isin([f"S{i}" for i in range(5, 10)])]["NES"].mean()
        if not (hi > lo + 0.1):
            diffs.append(
                f"[A] PATH_A NES should be higher in injected samples; "
                f"S0-4 mean={hi:.3f} vs S5-9 mean={lo:.3f}")
        # PATH_B mean should NOT show big lift
        b = long_df[long_df["pathway"] == "PATH_B"]
        if not b.empty:
            bhi = b[b["sample"].isin([f"S{i}" for i in range(5)])]["NES"].mean()
            blo = b[b["sample"].isin([f"S{i}" for i in range(5, 10)])]["NES"].mean()
            if abs(bhi - blo) > abs(hi - lo):
                diffs.append(
                    f"[A] PATH_B (negative control) lifted more than "
                    f"PATH_A; |Δ_B|={abs(bhi-blo):.3f} ≥ |Δ_A|={abs(hi-lo):.3f}")
    return diffs


def _gt_b_wide_matrix_shape() -> List[str]:
    """GT-B — sample_scores.csv must be a wide matrix of shape
    (n_samples, 1 + n_pathways_tested)."""
    import tempfile
    rng = np.random.default_rng(5)
    universe = [f"G{i:04d}" for i in range(300)]
    pa = universe[:40]
    pb = universe[40:90]
    sample_ids = [f"X{i}" for i in range(8)]
    rows = []
    for s in sample_ids:
        rows.append([s] + rng.uniform(2, 8, size=len(universe)).tolist())
    df = pd.DataFrame(rows, columns=["sample_id"] + universe)
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "g.gmt"
        gmt.write_text(
            "PA\t\t" + "\t".join(pa) + "\n"
            "PB\t\t" + "\t".join(pb) + "\n",
            encoding="utf-8")
        out = get_solver(min_size=10).run(
            df, ColumnMapping({
                "sample_id_col":    "sample_id",
                "gene_set_db_path": str(gmt),
            }),
            Path(tmp / "out"),
        )
        wide = pd.read_csv(out["sample_scores_csv"])
        if wide.shape[0] != len(sample_ids):
            diffs.append(
                f"[B] wide rows={wide.shape[0]} expected {len(sample_ids)}")
        # 1 id + up-to 2 pathways
        if wide.shape[1] not in (3, 2):
            diffs.append(
                f"[B] wide cols={wide.shape[1]} expected 3 (id + PA + PB)")
        if "sample_id" not in wide.columns:
            diffs.append(f"[B] sample_id col missing in {list(wide.columns)}")
    return diffs


def _gt_c_skip_cols() -> List[str]:
    """GT-C — extra metadata columns listed in skip_cols must be
    ignored (not treated as genes)."""
    import tempfile
    rng = np.random.default_rng(9)
    universe = [f"G{i:04d}" for i in range(120)]
    pa = universe[:30]
    sample_ids = [f"Z{i}" for i in range(8)]
    rows = []
    for i, s in enumerate(sample_ids):
        meta = float(i)                     # batch id (should NOT be a gene)
        rows.append([s, meta] + rng.uniform(2, 8, size=len(universe)).tolist())
    df = pd.DataFrame(rows, columns=["sample_id", "batch"] + universe)
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "g.gmt"
        gmt.write_text("PA\t\t" + "\t".join(pa) + "\n", encoding="utf-8")
        out = get_solver(min_size=10).run(
            df, ColumnMapping({
                "sample_id_col":    "sample_id",
                "skip_cols":        ["batch"],
                "gene_set_db_path": str(gmt),
            }),
            Path(tmp / "out"),
        )
        wide = pd.read_csv(out["sample_scores_csv"])
        if "batch" in wide.columns:
            diffs.append("[C] skip_cols ignored: 'batch' surfaced as pathway")
        if int(out["n_samples"]) != len(sample_ids):
            diffs.append(f"[C] n_samples={out['n_samples']} expected "
                          f"{len(sample_ids)}")
    return diffs


def selftest() -> Dict[str, Any]:
    """3-scenario GT suite for gene_set_score (ssGSEA).

      GT-A  injected PATH_A in 5 samples → mean NES strictly higher
            in injected sub-cohort vs control sub-cohort
      GT-B  output wide matrix has shape (n_samples, 1 + n_pathways)
      GT-C  skip_cols metadata columns are not scored as pathways
    """
    diffs = (_gt_a_injection_high_subset()
             + _gt_b_wide_matrix_shape()
             + _gt_c_skip_cols())
    return {
        "ok": len(diffs) == 0,
        "summary": ("3/3 pass: injected sub-cohort NES higher, "
                    "wide matrix shape, skip_cols respected"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["gene_set_score"],
                    "n_scenarios": 3},
    }


if __name__ == "__main__":
    rep = selftest()
    print(f"gene_set_score SELFTEST: "
           f"{'PASS' if rep['ok'] else 'FAIL'}")
    print(f"  {rep['summary']}")
    for d in rep["details"]["diffs"]:
        print(f"  {d}")
