"""Pre-ranked Gene Set Enrichment Analysis (Subramanian 2005).

Given a *full* gene list pre-ranked by some statistic (usually
log2FoldChange or signed -log10(p)), test each gene set for enrichment
near the top or bottom of the ranking.  Unlike ORA, GSEA uses every
gene's signal — not a thresholded hit list — so it is robust to weak
but coordinated changes.

Backed by ``gseapy.prerank`` (Python re-implementation of the
Broad GSEA Java tool, with the same kernel ES walking the ranking
weighted by ``|score|^weight``; default weight=1.0).

References
----------
* Subramanian A et al. (2005) "Gene set enrichment analysis: a
  knowledge-based approach for interpreting genome-wide expression
  profiles" *PNAS* 102:15545.
* Fang Z et al. (2023) "GSEApy: a comprehensive package for performing
  gene set enrichment analysis in Python" *Bioinformatics* 39:btac757.

Outputs
-------
``gsea_table.csv``
    columns: ``pathway, ES, NES, p_value, fdr_q, n_genes_in_pathway,
    n_genes_matched, leading_edge``  (sorted by FDR q ascending)
``gsea_summary.json``
    {n_pathways_tested, n_significant_fdr_0.05, top_5_up_pathways,
    top_5_down_pathways, n_genes_ranked, permutation_num, gmt_path}
"""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


_BUNDLED_GMT = (Path(__file__).parent.parent / "bio" / "data"
                 / "msigdb_hallmark_2020_human.gmt")


CONTRACT = SolverContract(
    name="gsea_preranked",
    capability="F_bio_gsea_preranked",
    description=(
        "Pre-ranked GSEA (Subramanian 2005, gseapy.prerank backend). "
        "Input: a full gene ranking (gene_id, score) where score is "
        "typically log2FoldChange or signed -log10(p_value).  Output: "
        "per-pathway Enrichment Score (ES), Normalized ES (NES), "
        "nominal p, FDR q-value, and leading-edge genes.  Use this "
        "instead of ORA when you want to leverage the full ranking "
        "signal (no arbitrary hit cutoff) — better statistical power."
    ),
    roles={
        "ranked_genes_csv": RoleSpec(
            Role.PARAMS,
            "Path to a CSV with two columns: gene_id, score (or "
            "log2FoldChange / logFC / stat / rank_metric).  Genes are "
            "ranked descending by score before running GSEA.",
            optional=True),
        "ranked_genes_inline": RoleSpec(
            Role.PARAMS,
            "Alternative to ranked_genes_csv: a list of (gene_id, "
            "score) tuples, or a {gene_id: score} dict.  Mutually "
            "exclusive.",
            optional=True),
        "gene_set_db_path": RoleSpec(
            Role.PARAMS,
            "Path to a GMT file. Default: bundled MSigDB Hallmark 2020.",
            optional=True),
        "min_size": RoleSpec(
            Role.PARAMS,
            "Drop gene sets smaller than this (after matching). "
            "Default 15.",
            optional=True),
        "max_size": RoleSpec(
            Role.PARAMS,
            "Drop gene sets larger than this. Default 500.",
            optional=True),
        "permutation_num": RoleSpec(
            Role.PARAMS,
            "Number of permutations for nominal p-value. Default 1000.",
            optional=True),
        "weight": RoleSpec(
            Role.PARAMS,
            "Weighting exponent for |score| in the running sum. "
            "Default 1.0 (Subramanian classic).",
            optional=True),
        "seed": RoleSpec(
            Role.PARAMS,
            "RNG seed for reproducible permutations. Default 2026.",
            optional=True),
    },
    static_params={
        "min_size":         15,
        "max_size":         500,
        "permutation_num":  1000,
        "weight":           1.0,
        "seed":             2026,
    },
    output_files={
        "gsea_table_csv":   "gsea_table.csv",
        "gsea_summary_json": "gsea_summary.json",
    },
    output_kind={"gsea_table_csv": "s", "gsea_summary_json": "s"},
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _unwrap_mapping_value(v: Any) -> Any:
    """Unwrap ``{'<name>': value}`` → ``value`` for single-key dicts.

    The LLM mapping engine occasionally emits ``{"min_size": 15}``
    instead of plain ``15`` for PARAMS roles whose name matches the
    contract ``static_params`` key.  This makes the solvers tolerant.
    """
    if isinstance(v, dict) and len(v) == 1:
        only = next(iter(v.values()))
        return _unwrap_mapping_value(only)
    return v


def _parse_gmt_dict(path: Path) -> Dict[str, List[str]]:
    """Parse GMT into a {term: [genes]} dict (gseapy native format)."""
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
            sets[term] = genes
    return sets


def _resolve_rnk(mapping: ColumnMapping) -> pd.DataFrame:
    """Return a 2-col DataFrame: gene_id, score.  Accepts CSV / inline."""
    csv_path = _unwrap_mapping_value(mapping.get("ranked_genes_csv"))
    inline = _unwrap_mapping_value(mapping.get("ranked_genes_inline"))
    if csv_path:
        rnk = pd.read_csv(csv_path)
        if "gene_id" not in rnk.columns:
            for alt in ("gene_symbol", "gene", "Gene", "Symbol", "name"):
                if alt in rnk.columns:
                    rnk = rnk.rename(columns={alt: "gene_id"})
                    break
            else:
                rnk = rnk.rename(columns={rnk.columns[0]: "gene_id"})
        if "score" not in rnk.columns:
            for alt in ("log2FoldChange", "logFC", "log2FC", "stat",
                          "rank_metric", "t", "T"):
                if alt in rnk.columns:
                    rnk = rnk.rename(columns={alt: "score"})
                    break
            else:
                rnk = rnk.rename(columns={rnk.columns[1]: "score"})
    elif inline is not None:
        if isinstance(inline, dict):
            rnk = pd.DataFrame({"gene_id": list(inline.keys()),
                                  "score":   list(inline.values())})
        else:
            rnk = pd.DataFrame(list(inline), columns=["gene_id", "score"])
    else:
        raise ValueError(
            "gsea_preranked needs ranked_genes_csv or ranked_genes_inline")
    rnk = rnk.dropna(subset=["gene_id", "score"]).copy()
    rnk["gene_id"] = rnk["gene_id"].astype(str).str.strip().str.upper()
    rnk["score"] = pd.to_numeric(rnk["score"], errors="coerce")
    rnk = rnk.dropna(subset=["score"])
    # collapse duplicates by max abs score (keep most extreme value)
    rnk["_abs"] = rnk["score"].abs()
    rnk = (rnk.sort_values("_abs", ascending=False)
              .drop_duplicates("gene_id", keep="first")
              .drop(columns="_abs"))
    return rnk.reset_index(drop=True)


# ---------------------------------------------------------------------------
# solver
# ---------------------------------------------------------------------------
class GseaPrerankedSolver:
    contract = CONTRACT

    def __init__(self, min_size: int = 15, max_size: int = 500,
                  permutation_num: int = 1000, weight: float = 1.0,
                  seed: int = 2026,
                  gene_set_db_path: Optional[str] = None):
        self.min_size = int(min_size)
        self.max_size = int(max_size)
        self.permutation_num = int(permutation_num)
        self.weight = float(weight)
        self.seed = int(seed)
        self.gene_set_db_path = gene_set_db_path

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        import gseapy as gp

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
        gmt = Path(gmt) if gmt else _BUNDLED_GMT
        if not gmt.is_file():
            raise FileNotFoundError(f"GMT not found: {gmt}")
        sets = _parse_gmt_dict(gmt)
        if not sets:
            raise ValueError(f"no gene sets parsed from {gmt}")

        rnk = _resolve_rnk(mapping)
        if len(rnk) < 50:
            raise ValueError(
                f"gsea_preranked needs ≥50 ranked genes, got {len(rnk)}")
        # Upper-case GMT genes for case-insensitive match
        sets_up = {term: [g.upper() for g in genes]
                    for term, genes in sets.items()}

        min_size = int(_unwrap_mapping_value(mapping.get("min_size"))
                         or self.min_size)
        max_size = int(_unwrap_mapping_value(mapping.get("max_size"))
                         or self.max_size)
        perm = int(_unwrap_mapping_value(mapping.get("permutation_num"))
                    or self.permutation_num)
        weight = float(_unwrap_mapping_value(mapping.get("weight"))
                         or self.weight)
        seed = int(_unwrap_mapping_value(mapping.get("seed")) or self.seed)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = gp.prerank(
                rnk=rnk[["gene_id", "score"]],
                gene_sets=sets_up,
                outdir=None,        # no figures
                min_size=min_size,
                max_size=max_size,
                permutation_num=perm,
                weight=weight,
                seed=seed,
                threads=1,
                no_plot=True,
                verbose=False,
            )
        res_df = res.res2d.copy()

        # Normalise the columns (gseapy 1.1.x schema):
        # Name, Term, ES, NES, NOM p-val, FDR q-val, FWER p-val,
        # Tag %, Gene %, Lead_genes
        rename = {
            "Term":         "pathway",
            "ES":           "ES",
            "NES":          "NES",
            "NOM p-val":    "p_value",
            "FDR q-val":    "fdr_q",
            "FWER p-val":   "fwer_p",
            "Lead_genes":   "leading_edge",
            "Tag %":        "tag_pct",
            "Gene %":       "gene_pct",
        }
        out_df = res_df.rename(columns=rename).copy()
        if "pathway" not in out_df.columns:
            raise RuntimeError(
                f"unexpected gseapy result schema: {list(res_df.columns)}")

        # Some gseapy versions return "Name" instead of "Term"; align if so.
        if "Name" in out_df.columns and "pathway" not in out_df.columns:
            out_df = out_df.rename(columns={"Name": "pathway"})

        # Pathway sizes (matched within ranking)
        rnk_genes_up = set(rnk["gene_id"].tolist())
        n_in_path = []
        n_matched = []
        for term in out_df["pathway"]:
            gs = sets_up.get(term, [])
            n_in_path.append(len(set(gs)))
            n_matched.append(len(set(gs) & rnk_genes_up))
        out_df["n_genes_in_pathway"] = n_in_path
        out_df["n_genes_matched"]    = n_matched

        # Final column order
        wanted = ["pathway", "ES", "NES", "p_value", "fdr_q",
                   "n_genes_in_pathway", "n_genes_matched",
                   "leading_edge"]
        for c in wanted:
            if c not in out_df.columns:
                out_df[c] = np.nan
        out_df = out_df[wanted + [c for c in ("fwer_p", "tag_pct", "gene_pct")
                                    if c in out_df.columns]]
        out_df = out_df.sort_values(["fdr_q", "p_value"],
                                       kind="mergesort").reset_index(drop=True)

        gt_path = out_dir / CONTRACT.output_files["gsea_table_csv"]
        out_df.to_csv(gt_path, index=False)

        sig = out_df.dropna(subset=["fdr_q"])
        sig = sig[sig["fdr_q"] < 0.05]
        top_up = (sig[sig["NES"] > 0]
                    .sort_values("NES", ascending=False)
                    .head(5)["pathway"].tolist())
        top_down = (sig[sig["NES"] < 0]
                     .sort_values("NES", ascending=True)
                     .head(5)["pathway"].tolist())
        summary = {
            "n_pathways_tested":      int(len(out_df)),
            "n_significant_fdr_0.05":  int(len(sig)),
            "top_5_up_pathways":      top_up,
            "top_5_down_pathways":    top_down,
            "n_genes_ranked":         int(len(rnk)),
            "permutation_num":        perm,
            "weight":                 weight,
            "seed":                   seed,
            "gmt_path":               str(gmt),
        }
        sj_path = out_dir / CONTRACT.output_files["gsea_summary_json"]
        sj_path.write_text(json.dumps(summary, indent=2, default=str),
                            encoding="utf-8")
        return {
            "gsea_table_csv":  str(gt_path),
            "gsea_summary_json": str(sj_path),
            **summary,
        }


def get_solver(min_size: int = 15, max_size: int = 500,
                permutation_num: int = 1000, weight: float = 1.0,
                seed: int = 2026,
                gene_set_db_path: Optional[str] = None
                ) -> GseaPrerankedSolver:
    return GseaPrerankedSolver(
        min_size=min_size, max_size=max_size,
        permutation_num=permutation_num, weight=weight,
        seed=seed, gene_set_db_path=gene_set_db_path,
    )


# ---------------------------------------------------------------------------
# GT selftest
# ---------------------------------------------------------------------------
def _gt_a_injected_top_pathway() -> List[str]:
    """GT-A — inject one gene set's members at the top of the ranking;
    the same pathway must come back as the #1 NES with NES > 1.5 and
    nominal p < 0.05 (with a small permutation_num for speed)."""
    import tempfile
    rng = np.random.default_rng(2026)
    universe = [f"GENE{i:04d}" for i in range(1000)]
    pathway_a = universe[:60]
    pathway_b = universe[60:120]
    pathway_c = universe[120:200]
    # Pathway A is enriched at the top: members get score uniform[1.5, 3]
    # Pathway B/C and others get score normal(0, 1)
    score = rng.normal(loc=0.0, scale=1.0, size=len(universe))
    for i, g in enumerate(universe):
        if g in pathway_a:
            score[i] = rng.uniform(1.5, 3.0)
    rnk = pd.DataFrame({"gene_id": universe, "score": score})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "g.gmt"
        gmt.write_text(
            "PATHWAY_A\t\t" + "\t".join(pathway_a) + "\n"
            "PATHWAY_B\t\t" + "\t".join(pathway_b) + "\n"
            "PATHWAY_C\t\t" + "\t".join(pathway_c) + "\n",
            encoding="utf-8")
        rnk_p = tmp / "rnk.csv"
        rnk.to_csv(rnk_p, index=False)
        out = get_solver(
            permutation_num=200,
            min_size=10,
            max_size=200,
        ).run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "ranked_genes_csv":  str(rnk_p),
                "gene_set_db_path":  str(gmt),
            }),
            output_dir=tmp / "out",
        )
        e = pd.read_csv(out["gsea_table_csv"])
        if e.empty:
            diffs.append("[A] GSEA result table is empty")
            return diffs
        top = e.iloc[0]
        if top["pathway"] != "PATHWAY_A":
            diffs.append(
                f"[A] PATHWAY_A should rank #1, got {top['pathway']}; "
                f"full ordering={e['pathway'].tolist()}")
        if float(top["NES"]) <= 1.5:
            diffs.append(f"[A] PATHWAY_A NES={top['NES']:.3f} expected > 1.5")
        if float(top["p_value"]) > 0.05:
            diffs.append(
                f"[A] PATHWAY_A nominal p={top['p_value']:.3f} expected < 0.05")
    return diffs


def _gt_b_inline_dict_input() -> List[str]:
    """GT-B — inline {gene: score} dict must work the same as CSV."""
    import tempfile
    rng = np.random.default_rng(7)
    universe = [f"GENE{i:04d}" for i in range(500)]
    pathway_a = universe[:50]
    score = {}
    for g in universe:
        score[g] = (float(rng.uniform(1.5, 3.0)) if g in pathway_a
                     else float(rng.normal(0, 1)))
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "g.gmt"
        gmt.write_text("PATHWAY_A\t\t" + "\t".join(pathway_a) + "\n",
                        encoding="utf-8")
        try:
            out = get_solver(permutation_num=200, min_size=10).run(
                df=pd.DataFrame(),
                mapping=ColumnMapping({
                    "ranked_genes_inline": score,
                    "gene_set_db_path":    str(gmt),
                }),
                output_dir=tmp / "out",
            )
            if int(out["n_genes_ranked"]) != len(universe):
                diffs.append(
                    f"[B] n_genes_ranked={out['n_genes_ranked']} "
                    f"expected {len(universe)} from inline dict")
        except Exception as e:
            diffs.append(
                f"[B] inline dict input should work but raised "
                f"{type(e).__name__}: {e}")
    return diffs


def _gt_c_negative_pathway_downreg() -> List[str]:
    """GT-C — inject a gene set's members at the BOTTOM of the ranking
    (negative scores); pathway should appear with NES < -1.5."""
    import tempfile
    rng = np.random.default_rng(11)
    universe = [f"GENE{i:04d}" for i in range(800)]
    pathway_n = universe[:50]
    score = rng.normal(0, 1, size=len(universe))
    for i, g in enumerate(universe):
        if g in pathway_n:
            score[i] = -rng.uniform(1.5, 3.0)
    rnk = pd.DataFrame({"gene_id": universe, "score": score})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "g.gmt"
        gmt.write_text("PATHWAY_N\t\t" + "\t".join(pathway_n) + "\n",
                        encoding="utf-8")
        rnk_p = tmp / "rnk.csv"
        rnk.to_csv(rnk_p, index=False)
        out = get_solver(permutation_num=200, min_size=10).run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "ranked_genes_csv":  str(rnk_p),
                "gene_set_db_path":  str(gmt),
            }),
            output_dir=tmp / "out",
        )
        e = pd.read_csv(out["gsea_table_csv"])
        if e.empty:
            diffs.append("[C] result empty")
            return diffs
        nes = float(e.iloc[0]["NES"])
        if nes >= -1.5:
            diffs.append(f"[C] PATHWAY_N NES={nes:.3f} expected < -1.5 "
                          f"(down-regulated injection)")
    return diffs


def _gt_d_alias_columns() -> List[str]:
    """GT-D — CSV with 'gene_symbol' + 'logFC' columns should be picked up."""
    import tempfile
    rng = np.random.default_rng(3)
    universe = [f"GENE{i:04d}" for i in range(500)]
    pathway_a = universe[:40]
    rnk = pd.DataFrame({
        "gene_symbol": universe,
        "logFC":       [float(rng.uniform(1.5, 3)) if g in pathway_a
                          else float(rng.normal(0, 1))
                          for g in universe],
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "g.gmt"
        gmt.write_text("PATHWAY_A\t\t" + "\t".join(pathway_a) + "\n",
                        encoding="utf-8")
        rnk_p = tmp / "rnk.csv"
        rnk.to_csv(rnk_p, index=False)
        try:
            out = get_solver(permutation_num=200, min_size=10).run(
                df=pd.DataFrame(),
                mapping=ColumnMapping({
                    "ranked_genes_csv":  str(rnk_p),
                    "gene_set_db_path":  str(gmt),
                }),
                output_dir=tmp / "out",
            )
            if int(out["n_genes_ranked"]) != len(universe):
                diffs.append(
                    f"[D] alias gene_symbol+logFC not picked up; "
                    f"n_ranked={out['n_genes_ranked']}")
            e = pd.read_csv(out["gsea_table_csv"])
            if e.empty or e.iloc[0]["pathway"] != "PATHWAY_A":
                diffs.append(
                    f"[D] alias columns rename produced wrong result: "
                    f"{e['pathway'].tolist() if not e.empty else '<empty>'}")
        except Exception as e:
            diffs.append(
                f"[D] alias columns gene_symbol/logFC should be auto-"
                f"renamed but raised {type(e).__name__}: {e}")
    return diffs


def selftest() -> Dict[str, Any]:
    """4-scenario GT suite for gsea_preranked.

      GT-A  injected top-enriched pathway → NES > 1.5, nominal p < 0.05,
            ranks #1
      GT-B  inline {gene: score} dict input works equivalent to CSV
      GT-C  injected bottom-enriched (negative) pathway → NES < -1.5
      GT-D  CSV alias columns (gene_symbol / logFC) auto-renamed
    """
    diffs = (_gt_a_injected_top_pathway()
             + _gt_b_inline_dict_input()
             + _gt_c_negative_pathway_downreg()
             + _gt_d_alias_columns())
    return {
        "ok": len(diffs) == 0,
        "summary": ("4/4 pass: top-NES injection, inline dict, "
                    "bottom-NES injection, alias columns"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["gsea_preranked"],
                    "n_scenarios": 4},
    }


if __name__ == "__main__":
    rep = selftest()
    print(f"gsea_preranked SELFTEST: "
           f"{'PASS' if rep['ok'] else 'FAIL'}")
    print(f"  {rep['summary']}")
    for d in rep["details"]["diffs"]:
        print(f"  {d}")
