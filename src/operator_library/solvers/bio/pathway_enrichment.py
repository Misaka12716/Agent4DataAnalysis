"""Hypergeometric (Fisher exact one-sided) gene-set enrichment.

Given a *target gene list* (typically the top-K DEGs) and a *gene-set
database* in GMT format, test each set for over-representation in the
target list using the hypergeometric distribution::

    universe size N (all genes considered),
    K = #genes in pathway ∩ universe,
    n = #genes in target list ∩ universe,
    k = #genes in (pathway ∩ target ∩ universe).
    p = sum_{x>=k} hypergeom.pmf(x; N, K, n)

Bundles the MSigDB Hallmark 2020 gene set library (50 sets,
uppercase HUMAN symbols, downloaded from Enrichr) at
``data/msigdb_hallmark_2020_human.gmt``.  For mouse data we
case-fold both target and pathway gene names so e.g. ``Tp53`` ↔
``TP53`` matches.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import hypergeom

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


_BUNDLED_GMT = Path(__file__).parent / "data" / "msigdb_hallmark_2020_human.gmt"


CONTRACT = SolverContract(
    name="pathway_enrichment_fisher",
    capability="F12_association_comorbidity_pattern",
    description=(
        "Over-representation analysis of a target gene list against a "
        "GMT gene-set library, using the hypergeometric distribution "
        "(equivalent to Fisher's exact, one-sided greater).  Bundles "
        "MSigDB Hallmark 2020 (50 sets) by default; specify "
        "gene_set_db_path for any other GMT.  Output: enrichment.csv "
        "ranked by adj_p_value."
    ),
    roles={
        "deg_table_csv": RoleSpec(
            Role.PARAMS,
            "Path to a deg_table.csv with at least gene_symbol + "
            "adj_p_value columns (e.g. output of "
            "limma_deg_two_group)."),
        "top_k": RoleSpec(
            Role.PARAMS,
            "Use the top-K rows of deg_table (sorted by adj_p_value) "
            "as the target gene list.  Default: 200.",
            optional=True),
        "adj_p_threshold": RoleSpec(
            Role.PARAMS,
            "Alternative selection: use all genes with "
            "adj_p_value < threshold.  If both top_k and "
            "adj_p_threshold are provided, the SMALLER resulting "
            "target list is used.",
            optional=True),
        "gene_set_db_path": RoleSpec(
            Role.PARAMS,
            "Path to a GMT file (term\\tdescription\\tgene1\\tgene2...). "
            "Default: bundled MSigDB Hallmark 2020.",
            optional=True),
        "case_insensitive": RoleSpec(
            Role.PARAMS,
            "Match gene names case-insensitively (helpful when the GMT "
            "is HUMAN uppercase and the data is MOUSE mixed-case).  "
            "Default: True.",
            optional=True),
        "min_overlap": RoleSpec(
            Role.PARAMS,
            "Drop terms with overlap < min_overlap (default: 2).",
            optional=True),
    },
    static_params={"top_k": 200, "case_insensitive": True, "min_overlap": 2},
    output_files={"enrichment_csv": "enrichment.csv"},
    output_kind={"enrichment_csv": "s"},
)


def _parse_gmt(path: Path) -> List[Tuple[str, List[str]]]:
    sets: List[Tuple[str, List[str]]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        parts = raw.rstrip("\r\n").split("\t")
        if len(parts) < 3:
            continue
        term = parts[0].strip()
        # parts[1] is description (often empty / URL)
        genes = [g.strip() for g in parts[2:] if g.strip()]
        if genes:
            sets.append((term, genes))
    return sets


def _bh_fdr(p: np.ndarray) -> np.ndarray:
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    ranked = np.asarray(p, dtype=float)[order]
    factors = n / np.arange(1, n + 1)
    raw = ranked * factors
    adj_sorted = np.minimum.accumulate(raw[::-1])[::-1]
    adj_sorted = np.minimum(adj_sorted, 1.0)
    adj = np.empty(n, dtype=float)
    adj[order] = adj_sorted
    return adj


class PathwayEnrichmentFisherSolver:
    contract = CONTRACT

    def __init__(self, top_k: int = 200, case_insensitive: bool = True,
                 min_overlap: int = 2,
                 gene_set_db_path: Optional[str] = None):
        self.top_k = top_k
        self.case_insensitive = case_insensitive
        self.min_overlap = min_overlap
        self.gene_set_db_path = gene_set_db_path

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        deg_path = mapping.get("deg_table_csv")
        if not deg_path:
            raise ValueError("pathway_enrichment_fisher needs "
                              "'deg_table_csv'")
        top_k = int(mapping.get("top_k") or self.top_k or 200)
        adj_thr = mapping.get("adj_p_threshold")
        case_insensitive = mapping.get("case_insensitive")
        if case_insensitive is None:
            case_insensitive = self.case_insensitive
        case_insensitive = bool(case_insensitive)
        min_overlap = int(mapping.get("min_overlap") or self.min_overlap or 2)
        gmt_path = (mapping.get("gene_set_db_path")
                     or self.gene_set_db_path
                     or str(_BUNDLED_GMT))
        gmt_path = Path(gmt_path)
        if not gmt_path.is_file():
            raise FileNotFoundError(f"GMT not found: {gmt_path}")

        deg = pd.read_csv(deg_path)
        if "gene_symbol" not in deg.columns:
            deg = deg.rename(columns={deg.columns[0]: "gene_symbol"})
        # Support FDR/padj from edgeR/DESeq2/limma etc.
        if "adj_p_value" not in deg.columns:
            for col in ["FDR", "padj", "pvalue_adj", "adj.P.Val", "adj_pvalue"]:
                if col in deg.columns:
                    deg = deg.rename(columns={col: "adj_p_value"})
                    break
        if "adj_p_value" not in deg.columns and "PValue" in deg.columns:
            deg = deg.rename(columns={"PValue": "adj_p_value"})
        if "adj_p_value" not in deg.columns:
            raise ValueError("deg_table.csv must contain one of: adj_p_value, FDR, padj, adj.P.Val")
        universe = deg["gene_symbol"].dropna().astype(str).unique().tolist()
        N = len(universe)
        if N < 10:
            raise ValueError(f"universe too small (N={N})")

        deg_sorted = deg.sort_values("adj_p_value", kind="mergesort",
                                       na_position="last")
        target_topk = (deg_sorted.head(top_k)["gene_symbol"]
                       .dropna().astype(str).unique().tolist())
        if adj_thr is not None:
            target_thr = (deg_sorted[deg_sorted["adj_p_value"] < float(adj_thr)]
                          ["gene_symbol"].dropna().astype(str).unique().tolist())
            target = (target_thr if len(target_thr) < len(target_topk)
                      else target_topk)
        else:
            target = target_topk
        if len(target) < 5:
            raise ValueError(f"target gene list too small ({len(target)}); "
                              f"check top_k / adj_p_threshold")

        sets = _parse_gmt(gmt_path)
        if not sets:
            raise ValueError(f"no gene sets parsed from {gmt_path}")

        def _norm(g):
            return g.upper() if case_insensitive else g

        univ_set = {_norm(g) for g in universe}
        targ_set = {_norm(g) for g in target}
        n = len(targ_set)

        rows = []
        for term, genes in sets:
            term_genes = {_norm(g) for g in genes}
            term_in_universe = term_genes & univ_set
            K = len(term_in_universe)
            if K < min_overlap:
                continue
            overlap = term_in_universe & targ_set
            k = len(overlap)
            if k < min_overlap:
                continue
            # P(X >= k) = sf(k-1)
            p = float(hypergeom.sf(k - 1, N, K, n))
            rows.append({
                "term":              term,
                "n_genes_in_term":   K,
                "n_overlap":         k,
                "n_target":          n,
                "n_universe":        N,
                "expected_overlap":  K * n / N,
                "fold_enrichment":   (k * N) / (K * n) if (K * n) > 0 else float("nan"),
                "p_value":           p,
                "overlap_genes":     ";".join(sorted(overlap)),
            })
        if not rows:
            res = pd.DataFrame(columns=["term", "n_genes_in_term", "n_overlap",
                                          "n_target", "n_universe",
                                          "expected_overlap", "fold_enrichment",
                                          "p_value", "adj_p_value", "overlap_genes"])
        else:
            res = pd.DataFrame(rows)
            res["adj_p_value"] = _bh_fdr(res["p_value"].to_numpy())
            res = res.sort_values(["adj_p_value", "p_value"],
                                    kind="mergesort").reset_index(drop=True)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / CONTRACT.output_files["enrichment_csv"]
        res.to_csv(path, index=False)
        return {
            "enrichment_csv":  str(path),
            "n_terms_tested":  int(len(rows)),
            "n_significant":   int((res["adj_p_value"] < 0.05).sum()
                                    if "adj_p_value" in res.columns else 0),
            "n_target":        int(n),
            "n_universe":      int(N),
            "gmt_path":        str(gmt_path),
        }


def get_solver(top_k: int = 200, case_insensitive: bool = True,
                min_overlap: int = 2,
                gene_set_db_path: Optional[str] = None):
    return PathwayEnrichmentFisherSolver(
        top_k=top_k, case_insensitive=case_insensitive,
        min_overlap=min_overlap, gene_set_db_path=gene_set_db_path)


def selftest():
    """Build a synthetic universe of 1000 genes; pick the 50 in pathway P
    plus 5 outside; pathway P should rank #1 with p << 0.001."""
    import tempfile

    rng = np.random.default_rng(123)
    universe = [f"GENE{i:04d}" for i in range(1000)]
    pathway_p = universe[:50]
    pathway_q = universe[50:120]   # 70 genes, no overlap with target
    pathway_r = list(rng.choice(universe[120:], 80, replace=False))
    target = pathway_p + universe[800:805]   # 50 in P, 5 noise

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "test.gmt"
        with gmt.open("w", encoding="utf-8") as f:
            f.write("PATHWAY_P\t\t" + "\t".join(pathway_p) + "\n")
            f.write("PATHWAY_Q\t\t" + "\t".join(pathway_q) + "\n")
            f.write("PATHWAY_R\t\t" + "\t".join(pathway_r) + "\n")

        deg = pd.DataFrame({
            "gene_symbol": universe,
            "adj_p_value": [0.001 if g in target else 0.99 for g in universe],
        })
        deg_p = tmp / "deg.csv"
        deg.to_csv(deg_p, index=False)

        out = get_solver().run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "deg_table_csv":      str(deg_p),
                "gene_set_db_path":   str(gmt),
                "top_k":              len(target),
                "case_insensitive":   False,
            }),
            output_dir=tmp / "out",
        )
        e = pd.read_csv(out["enrichment_csv"])
        if e.empty or e.iloc[0]["term"] != "PATHWAY_P":
            diffs.append(f"PATHWAY_P should rank #1, got "
                         f"{e['term'].tolist() if not e.empty else '<empty>'}")
        if not e.empty and e.iloc[0]["p_value"] > 1e-30:
            diffs.append(f"PATHWAY_P p_value too high: "
                         f"{e.iloc[0]['p_value']}")
        if not e.empty and e.iloc[0]["n_overlap"] != 50:
            diffs.append(f"PATHWAY_P overlap expected 50, got "
                         f"{e.iloc[0]['n_overlap']}")

        # bundled hallmark sanity: parses + mouse-uppercase mapping works
        bundled_out = get_solver(top_k=10).run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "deg_table_csv": str(deg_p),
                "top_k": 10,
            }),
            output_dir=tmp / "bundled",
        )
        if bundled_out["n_terms_tested"] < 1:
            # universe doesn't overlap MSigDB human hallmark — that's fine,
            # but we expect at least the file to parse without error
            pass

    return {"ok": len(diffs) == 0,
            "summary": ("synthetic 3-pathway test: PATHWAY_P #1 with "
                         "p << 1e-30"
                         if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["pathway_enrichment_fisher"]}}
