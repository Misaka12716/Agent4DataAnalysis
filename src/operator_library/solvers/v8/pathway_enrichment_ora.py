"""Over-Representation Analysis (ORA) of a differential expression
result against a GMT gene-set library.

This is the *V8.2 B2* operator — a thin, **DE-table aware** front-end
that consumes a ``de_table.csv`` (output of B1 or the older
``limma_deg_two_group``) and runs hypergeometric tests (= Fisher's
exact, one-sided greater) for each gene set in a GMT library.

By default the bundled MSigDB Hallmark 2020 library
(``solvers/bio/data/msigdb_hallmark_2020_human.gmt``) is used.  Any
GMT path can be passed via ``gene_set_db_path`` (e.g. KEGG / Reactome /
GO downloaded from Enrichr / MSigDB).

Hit list selection logic (in order):

1. If ``hit_genes`` (list) is supplied via mapping → use it directly.
2. Else read DE table, filter rows with
   ``adj_p_value < adj_p_threshold`` AND
   ``abs(log2FoldChange) >= lfc_threshold`` (both have defaults).
3. If that yields < ``min_hit`` genes, fall back to top-``top_k`` by
   ``adj_p_value`` rank.

References
----------
* Subramanian A et al. (2005) "Gene set enrichment analysis: A
  knowledge-based approach for interpreting genome-wide expression
  profiles" *PNAS* 102:15545-15550.
* Khatri P et al. (2012) "Ten years of pathway analysis: current
  approaches and outstanding challenges" *PLoS Comput Biol* 8:e1002375.

Outputs
-------
``enrichment_table.csv``
    one row per tested pathway with columns:
    ``pathway, n_hit, n_pathway, n_background, expected_overlap,
    fold_enrichment, odds_ratio, p_value, adj_p_value,
    leading_edge``  (sorted by adj_p_value ascending)
``enrichment_summary.json``
    {n_pathways_tested, n_significant_fdr_0.05, top_5_pathways,
    n_hits, n_background, gmt_path}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import hypergeom

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


_BUNDLED_GMT = (Path(__file__).parent.parent / "bio" / "data"
                 / "msigdb_hallmark_2020_human.gmt")


CONTRACT = SolverContract(
    name="pathway_enrichment_ora",
    capability="F_bio_pathway_ora",
    description=(
        "Hypergeometric (Fisher's exact, one-sided greater) Over-"
        "Representation Analysis of a differential expression hit "
        "list against a GMT pathway library.  Auto-selects hits from a "
        "DE table via adj_p_value < threshold AND |log2FC| ≥ threshold "
        "(with top-K fallback if hit list is empty).  Bundles MSigDB "
        "Hallmark 2020 by default; any GMT can be supplied via "
        "gene_set_db_path.  Case-insensitive gene matching for "
        "mouse↔human convenience."
    ),
    roles={
        "deg_table_csv": RoleSpec(
            Role.PARAMS,
            "Path to a DE table CSV.  Must contain a gene_id (or "
            "gene_symbol) column.  Optional columns: adj_p_value (or "
            "FDR / padj / adj.P.Val) and log2FoldChange (or logFC).",
            optional=True),
        "hit_genes": RoleSpec(
            Role.PARAMS,
            "List of gene symbols to test directly (skip DE table "
            "filtering).  Mutually exclusive with deg_table_csv.",
            optional=True),
        "background_genes": RoleSpec(
            Role.PARAMS,
            "List of gene symbols defining the universe.  Default: "
            "union of (DE table gene_id ∪ all genes mentioned in GMT).",
            optional=True),
        "gene_set_db_path": RoleSpec(
            Role.PARAMS,
            "Path to a GMT file.  Default: bundled MSigDB Hallmark 2020.",
            optional=True),
        "adj_p_threshold": RoleSpec(
            Role.PARAMS,
            "Hit selection: adj_p_value < this. Default 0.05.",
            optional=True),
        "lfc_threshold": RoleSpec(
            Role.PARAMS,
            "Hit selection: |log2FoldChange| >= this. Default 1.0. "
            "Set to 0.0 to skip LFC filter.",
            optional=True),
        "top_k": RoleSpec(
            Role.PARAMS,
            "Fallback top-K by adj_p_value rank if threshold filter "
            "yields < min_hit. Default 200.",
            optional=True),
        "min_overlap": RoleSpec(
            Role.PARAMS,
            "Drop pathways with overlap < this (default 2).",
            optional=True),
        "case_insensitive": RoleSpec(
            Role.PARAMS,
            "Case-fold gene symbols before matching (default True).",
            optional=True),
    },
    static_params={
        "adj_p_threshold": 0.05,
        "lfc_threshold":   1.0,
        "top_k":           200,
        "min_overlap":     2,
        "case_insensitive": True,
        "min_hit":         5,
    },
    output_files={
        "enrichment_table_csv":   "enrichment_table.csv",
        "enrichment_summary_json": "enrichment_summary.json",
    },
    output_kind={"enrichment_table_csv": "s",
                  "enrichment_summary_json": "s"},
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _unwrap_mapping_value(v: Any) -> Any:
    """Unwrap ``{'<name>': value}`` → ``value`` for single-key dicts.

    The LLM mapping engine occasionally emits ``{"min_overlap": 2}``
    instead of plain ``2`` for PARAMS roles whose name matches the
    contract ``static_params`` key.
    """
    if isinstance(v, dict) and len(v) == 1:
        only = next(iter(v.values()))
        return _unwrap_mapping_value(only)
    return v


def _parse_gmt(path: Path) -> List[Tuple[str, List[str]]]:
    sets: List[Tuple[str, List[str]]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        parts = raw.rstrip("\r\n").split("\t")
        if len(parts) < 3:
            continue
        term = parts[0].strip()
        genes = [g.strip() for g in parts[2:] if g.strip()]
        if genes:
            sets.append((term, genes))
    return sets


def _bh_fdr(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg FDR (NaN-safe vectorised)."""
    p = np.asarray(p, dtype=float)
    mask = np.isfinite(p)
    adj = np.full_like(p, np.nan, dtype=float)
    if not mask.any():
        return adj
    pp = p[mask]
    n = len(pp)
    order = np.argsort(pp)
    ranked = pp[order]
    raw = ranked * (n / np.arange(1, n + 1))
    adj_sorted = np.minimum.accumulate(raw[::-1])[::-1]
    adj_sorted = np.minimum(adj_sorted, 1.0)
    sub_adj = np.empty(n, dtype=float)
    sub_adj[order] = adj_sorted
    adj[mask] = sub_adj
    return adj


def _norm_alias(deg: pd.DataFrame) -> pd.DataFrame:
    """Rename common alternate column names to our schema."""
    rename = {}
    if "gene_id" not in deg.columns:
        for alt in ("gene_symbol", "gene", "Gene", "Symbol", "name"):
            if alt in deg.columns:
                rename[alt] = "gene_id"
                break
        else:
            rename[deg.columns[0]] = "gene_id"
    if "adj_p_value" not in deg.columns:
        for alt in ("FDR", "padj", "adj.P.Val", "adj_pvalue", "qvalue"):
            if alt in deg.columns:
                rename[alt] = "adj_p_value"
                break
    if "log2FoldChange" not in deg.columns:
        for alt in ("logFC", "log2FC", "log2_FC", "LFC"):
            if alt in deg.columns:
                rename[alt] = "log2FoldChange"
                break
    return deg.rename(columns=rename)


# ---------------------------------------------------------------------------
# solver
# ---------------------------------------------------------------------------
class PathwayEnrichmentOraSolver:
    contract = CONTRACT

    def __init__(self, adj_p_threshold: float = 0.05,
                  lfc_threshold: float = 1.0,
                  top_k: int = 200,
                  min_overlap: int = 2,
                  case_insensitive: bool = True,
                  min_hit: int = 5,
                  gene_set_db_path: Optional[str] = None):
        self.adj_p_threshold = float(adj_p_threshold)
        self.lfc_threshold = float(lfc_threshold)
        self.top_k = int(top_k)
        self.min_overlap = int(min_overlap)
        self.case_insensitive = bool(case_insensitive)
        self.min_hit = int(min_hit)
        self.gene_set_db_path = gene_set_db_path

    def _resolve_gmt(self, mapping: ColumnMapping,
                       output_dir: Optional[Path] = None) -> Path:
        gmt = (_unwrap_mapping_value(mapping.get("gene_set_db_path"))
                or self.gene_set_db_path)
        if gmt is None and output_dir is not None:
            # Auto-discover a workspace-local .gmt before falling back to
            # the bundled MSigDB Hallmark library.  This matters when the
            # task ships a custom GMT (e.g., synthetic ``pathways.gmt``
            # in benchmark workspaces).  Layout: WORKSPACE/operator_output/
            # NN_step/...  → walk up at most 4 levels.
            here = Path(output_dir).resolve()
            for _ in range(4):
                if not here.exists():
                    break
                local = sorted(here.glob("*.gmt"))
                if local:
                    return local[0]
                if here.parent == here:
                    break
                here = here.parent
        gmt = Path(gmt) if gmt else _BUNDLED_GMT
        if not gmt.is_file():
            raise FileNotFoundError(f"GMT not found: {gmt}")
        return gmt

    def _resolve_hits(self, mapping: ColumnMapping, deg_path: Optional[str]
                        ) -> Tuple[List[str], List[str], str]:
        """Return (hit_list, background, source_note)."""
        explicit_hits = _unwrap_mapping_value(mapping.get("hit_genes"))
        explicit_bg = _unwrap_mapping_value(mapping.get("background_genes"))
        if explicit_hits is not None:
            hits = [str(g).strip() for g in explicit_hits if str(g).strip()]
            bg = ([str(g).strip() for g in (explicit_bg or [])
                    if str(g).strip()] or hits)
            return hits, bg, f"explicit hit_genes (n={len(hits)})"
        if not deg_path:
            raise ValueError(
                "pathway_enrichment_ora needs either 'hit_genes' or "
                "'deg_table_csv' in mapping.")
        deg = pd.read_csv(deg_path)
        deg = _norm_alias(deg)
        if "gene_id" not in deg.columns:
            raise ValueError(
                "deg_table_csv must contain a gene id column "
                "(gene_id / gene_symbol / Symbol)")
        thr = _unwrap_mapping_value(mapping.get("adj_p_threshold"))
        if thr is None:
            thr = self.adj_p_threshold
        thr = float(thr)
        lfc_thr = _unwrap_mapping_value(mapping.get("lfc_threshold"))
        if lfc_thr is None:
            lfc_thr = self.lfc_threshold
        lfc_thr = float(lfc_thr)
        top_k = int(_unwrap_mapping_value(mapping.get("top_k"))
                      or self.top_k or 200)

        mask = pd.Series(True, index=deg.index)
        used_filters: List[str] = []
        if "adj_p_value" in deg.columns:
            mask &= deg["adj_p_value"] < thr
            used_filters.append(f"adj_p<{thr}")
        if "log2FoldChange" in deg.columns and lfc_thr > 0:
            mask &= deg["log2FoldChange"].abs() >= lfc_thr
            used_filters.append(f"|log2FC|≥{lfc_thr}")
        hits = (deg.loc[mask, "gene_id"]
                .dropna().astype(str).str.strip().unique().tolist())
        note = ("filter(" + " & ".join(used_filters) + f") → {len(hits)}"
                if used_filters else f"all genes ({len(deg)})")
        if len(hits) < self.min_hit and "adj_p_value" in deg.columns:
            # fallback: top-k by adj_p_value
            top = (deg.sort_values("adj_p_value", kind="mergesort",
                                     na_position="last")
                       .head(top_k))
            hits = (top["gene_id"].dropna().astype(str).str.strip()
                      .unique().tolist())
            note += f" → fallback top-{top_k} (n={len(hits)})"

        bg_explicit = ([str(g).strip() for g in (explicit_bg or [])
                          if str(g).strip()])
        if bg_explicit:
            bg = bg_explicit
        else:
            bg = (deg["gene_id"].dropna().astype(str).str.strip()
                    .unique().tolist())
        return hits, bg, note

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        deg_path = _unwrap_mapping_value(mapping.get("deg_table_csv"))
        gmt_path = self._resolve_gmt(mapping, output_dir)
        sets = _parse_gmt(gmt_path)
        if not sets:
            raise ValueError(f"no gene sets parsed from {gmt_path}")

        hits, bg, hit_note = self._resolve_hits(mapping, deg_path)
        if len(hits) < self.min_hit:
            raise ValueError(
                f"pathway_enrichment_ora: hit list too small "
                f"({len(hits)} < min_hit={self.min_hit}); "
                f"selection={hit_note}")

        case_insensitive = _unwrap_mapping_value(mapping.get("case_insensitive"))
        if case_insensitive is None:
            case_insensitive = self.case_insensitive
        case_insensitive = bool(case_insensitive)
        min_overlap = int(_unwrap_mapping_value(mapping.get("min_overlap"))
                            or self.min_overlap)

        def _norm(g):
            return g.upper() if case_insensitive else g

        # Union background with GMT genes so small DE tables don't
        # under-estimate N.  Document this in note.
        all_gmt_genes = {_norm(g) for _, gs in sets for g in gs}
        bg_set = {_norm(g) for g in bg}
        # If background looks shorter than half the GMT vocabulary, expand
        # to bg ∪ gmt to avoid pathological tiny N (would inflate p-values).
        expanded = False
        if len(bg_set) < len(all_gmt_genes) / 2:
            bg_set = bg_set | all_gmt_genes
            expanded = True
        N = len(bg_set)
        hit_set = {_norm(g) for g in hits} & bg_set
        n = len(hit_set)
        if n < self.min_hit:
            raise ValueError(
                f"pathway_enrichment_ora: after intersecting hits with "
                f"background, only {n} hits remain (need ≥{self.min_hit})")

        rows = []
        for term, genes in sets:
            tg = {_norm(g) for g in genes} & bg_set
            K = len(tg)
            if K < min_overlap:
                continue
            overlap = tg & hit_set
            k = len(overlap)
            if k < min_overlap:
                continue
            p = float(hypergeom.sf(k - 1, N, K, n))
            # Odds ratio (2x2): (k*(N-K-n+k)) / ((K-k)*(n-k)).  Use
            # Haldane-Anscombe continuity correction for zero cells.
            a = k
            b = max(K - k, 0)
            c = max(n - k, 0)
            d = max(N - K - n + k, 0)
            haldane = (a == 0 or b == 0 or c == 0 or d == 0)
            if haldane:
                a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
            try:
                odds = (a * d) / (b * c) if (b * c) > 0 else float("inf")
            except Exception:
                odds = float("nan")
            rows.append({
                "pathway":          term,
                "n_hit":            k,
                "n_pathway":        K,
                "n_target":         n,
                "n_background":    N,
                "expected_overlap": K * n / N if N > 0 else float("nan"),
                "fold_enrichment":  (k * N) / (K * n) if (K * n) > 0
                                     else float("nan"),
                "odds_ratio":       odds,
                "p_value":          p,
                "leading_edge":     ";".join(sorted(overlap)),
            })

        if not rows:
            res = pd.DataFrame(columns=[
                "pathway", "n_hit", "n_pathway", "n_target", "n_background",
                "expected_overlap", "fold_enrichment", "odds_ratio",
                "p_value", "adj_p_value", "leading_edge"])
        else:
            res = pd.DataFrame(rows)
            res["adj_p_value"] = _bh_fdr(res["p_value"].to_numpy())
            res = res.sort_values(["adj_p_value", "p_value"],
                                    kind="mergesort").reset_index(drop=True)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        et_path = out_dir / CONTRACT.output_files["enrichment_table_csv"]
        res.to_csv(et_path, index=False)

        n_sig = (int((res["adj_p_value"] < 0.05).sum())
                  if "adj_p_value" in res.columns and len(res) > 0
                  else 0)
        top5 = res.head(5)["pathway"].tolist() if len(res) > 0 else []
        summary = {
            "n_pathways_tested":     int(len(res)),
            "n_significant_fdr_0.05": n_sig,
            "top_5_pathways":        top5,
            "n_hits":                int(n),
            "n_background":         int(N),
            "background_expanded_with_gmt": bool(expanded),
            "hit_selection_note":    hit_note,
            "gmt_path":              str(gmt_path),
        }
        sj_path = out_dir / CONTRACT.output_files["enrichment_summary_json"]
        sj_path.write_text(json.dumps(summary, indent=2, default=str),
                            encoding="utf-8")
        return {
            "enrichment_table_csv":  str(et_path),
            "enrichment_summary_json": str(sj_path),
            **summary,
        }


def get_solver(adj_p_threshold: float = 0.05,
                lfc_threshold: float = 1.0,
                top_k: int = 200,
                min_overlap: int = 2,
                case_insensitive: bool = True,
                min_hit: int = 5,
                gene_set_db_path: Optional[str] = None):
    return PathwayEnrichmentOraSolver(
        adj_p_threshold=adj_p_threshold,
        lfc_threshold=lfc_threshold,
        top_k=top_k,
        min_overlap=min_overlap,
        case_insensitive=case_insensitive,
        min_hit=min_hit,
        gene_set_db_path=gene_set_db_path,
    )


# ---------------------------------------------------------------------------
# GT selftest
# ---------------------------------------------------------------------------
def _gt_a_synthetic_pathway_first() -> List[str]:
    """GT-A — toy 3-pathway GMT, hit list = pathway P; expect PATHWAY_P
    to rank #1 with p << 1e-30."""
    import tempfile
    universe = [f"GENE{i:04d}" for i in range(1000)]
    pathway_p = universe[:50]
    pathway_q = universe[50:120]
    pathway_r = universe[120:200]
    hits = pathway_p + universe[800:805]   # 50 in P + 5 noise

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "test.gmt"
        with gmt.open("w", encoding="utf-8") as f:
            f.write("PATHWAY_P\t\t" + "\t".join(pathway_p) + "\n")
            f.write("PATHWAY_Q\t\t" + "\t".join(pathway_q) + "\n")
            f.write("PATHWAY_R\t\t" + "\t".join(pathway_r) + "\n")

        out = get_solver().run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "hit_genes":         hits,
                "background_genes":  universe,
                "gene_set_db_path":  str(gmt),
                "case_insensitive":  False,
                "min_hit":           5,
            }),
            output_dir=tmp / "out",
        )
        e = pd.read_csv(out["enrichment_table_csv"])
        if e.empty or e.iloc[0]["pathway"] != "PATHWAY_P":
            diffs.append(
                f"[A] PATHWAY_P should rank #1, got "
                f"{e['pathway'].tolist() if not e.empty else '<empty>'}")
        elif e.iloc[0]["p_value"] > 1e-30:
            diffs.append(f"[A] PATHWAY_P p_value too high: "
                          f"{e.iloc[0]['p_value']}")
        elif e.iloc[0]["n_hit"] != 50:
            diffs.append(f"[A] PATHWAY_P overlap expected 50, got "
                          f"{e.iloc[0]['n_hit']}")
    return diffs


def _gt_b_hypergeom_exact() -> List[str]:
    """GT-B — verify p_value matches scipy.hypergeom.sf(k-1, N, K, n)
    exactly for a tiny manual example.

    Background N=1000; pathway has K=60; hit list n=80 of which k=15
    are in the pathway.  scipy gives p = hypergeom.sf(14, 1000, 60, 80).
    """
    import tempfile
    universe = [f"GENE{i:04d}" for i in range(1000)]
    pathway = universe[:60]
    hits = pathway[:15] + universe[60:60 + 65]  # 15 in pathway + 65 random

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gmt = tmp / "test.gmt"
        gmt.write_text("PATH\t\t" + "\t".join(pathway) + "\n",
                        encoding="utf-8")
        out = get_solver().run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "hit_genes":         hits,
                "background_genes":  universe,
                "gene_set_db_path":  str(gmt),
                "case_insensitive":  False,
                "min_hit":           5,
            }),
            output_dir=tmp / "out",
        )
        e = pd.read_csv(out["enrichment_table_csv"])
        expected_p = float(hypergeom.sf(14, 1000, 60, 80))
        got_p = float(e.iloc[0]["p_value"])
        if abs(got_p - expected_p) > 1e-12:
            diffs.append(
                f"[B] p_value mismatch with scipy.hypergeom.sf: "
                f"op={got_p:.6e} scipy={expected_p:.6e}")
        if int(e.iloc[0]["n_hit"]) != 15:
            diffs.append(f"[B] n_hit={e.iloc[0]['n_hit']} expected 15")
    return diffs


def _gt_c_from_de_table() -> List[str]:
    """GT-C — feed a synthetic DE table (adj_p_value + log2FoldChange) and
    verify the threshold filter resolves the expected hit list."""
    import tempfile
    universe = [f"GENE{i:04d}" for i in range(500)]
    pathway = universe[:30]
    # injected DEGs = pathway[:25] + 5 random
    injected = pathway[:25] + universe[100:105]
    n_inj = len(injected)
    rng = np.random.default_rng(0)
    rows = []
    for g in universe:
        if g in injected:
            rows.append({"gene_id": g, "adj_p_value": 0.001,
                          "log2FoldChange": float(rng.uniform(1.5, 3.0))})
        else:
            rows.append({"gene_id": g, "adj_p_value": float(rng.uniform(0.2, 1)),
                          "log2FoldChange": float(rng.uniform(-0.5, 0.5))})
    deg_df = pd.DataFrame(rows)
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        deg_p = tmp / "deg.csv"
        deg_df.to_csv(deg_p, index=False)
        gmt = tmp / "test.gmt"
        gmt.write_text("PATHWAY_X\t\t" + "\t".join(pathway) + "\n",
                        encoding="utf-8")
        out = get_solver().run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "deg_table_csv":      str(deg_p),
                "gene_set_db_path":   str(gmt),
                "case_insensitive":   False,
                "min_hit":            5,
                "adj_p_threshold":    0.05,
                "lfc_threshold":      1.0,
            }),
            output_dir=tmp / "out",
        )
        # Should pick exactly the injected list as hits
        if int(out["n_hits"]) != n_inj:
            diffs.append(
                f"[C] n_hits={out['n_hits']} expected {n_inj} from "
                f"adj_p<0.05 & |log2FC|>=1.0 filter")
        e = pd.read_csv(out["enrichment_table_csv"])
        if e.empty:
            diffs.append("[C] enrichment table empty unexpectedly")
        elif int(e.iloc[0]["n_hit"]) != 25:
            diffs.append(f"[C] PATHWAY_X overlap expected 25, got "
                          f"{e.iloc[0]['n_hit']}")
        elif e.iloc[0]["p_value"] > 1e-20:
            diffs.append(f"[C] PATHWAY_X p_value too high: "
                          f"{e.iloc[0]['p_value']}")
    return diffs


def _gt_d_alias_columns() -> List[str]:
    """GT-D — DE table with alternate column names (gene_symbol + FDR +
    logFC) must be auto-renamed by _norm_alias."""
    import tempfile
    universe = [f"GENE{i:04d}" for i in range(300)]
    pathway = universe[:30]
    inj = pathway[:25]
    rng = np.random.default_rng(2)
    rows = []
    for g in universe:
        if g in inj:
            rows.append({"gene_symbol": g, "FDR": 0.001,
                          "logFC": float(rng.uniform(1.5, 3))})
        else:
            rows.append({"gene_symbol": g, "FDR": float(rng.uniform(0.2, 1)),
                          "logFC": float(rng.uniform(-0.5, 0.5))})
    deg_df = pd.DataFrame(rows)
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        deg_p = tmp / "deg.csv"
        deg_df.to_csv(deg_p, index=False)
        gmt = tmp / "g.gmt"
        gmt.write_text("PATH\t\t" + "\t".join(pathway) + "\n",
                        encoding="utf-8")
        try:
            out = get_solver().run(
                df=pd.DataFrame(),
                mapping=ColumnMapping({
                    "deg_table_csv":     str(deg_p),
                    "gene_set_db_path":  str(gmt),
                    "case_insensitive":  False,
                    "min_hit":           5,
                }),
                output_dir=tmp / "out",
            )
            if int(out["n_hits"]) != 25:
                diffs.append(
                    f"[D] alias gene_symbol+FDR+logFC not picked up; "
                    f"n_hits={out['n_hits']} expected 25")
        except Exception as e:
            diffs.append(
                f"[D] alias columns should be auto-renamed but raised "
                f"{type(e).__name__}: {e}")
    return diffs


def _gt_e_msigdb_hallmark_smoke() -> List[str]:
    """GT-E — bundled MSigDB Hallmark 2020 should load and produce a
    valid (possibly empty) enrichment for an inflammatory hit list."""
    import tempfile
    if not _BUNDLED_GMT.is_file():
        return [f"[E] bundled hallmark gmt missing at {_BUNDLED_GMT}"]
    # canonical NF-kB / inflammation hits — should hit
    # "TNF-alpha Signaling via NF-kB"
    hits = ["JUNB", "CXCL2", "ATF3", "NFKBIA", "TNFAIP3", "PTGS2",
             "CXCL1", "IER3", "CD83", "CCL20", "CXCL3", "MAFF",
             "NFKB2", "TNFAIP2", "HBEGF", "KLF6", "BIRC3", "PLAUR",
             "ZFP36", "ICAM1"]
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df=pd.DataFrame(),
                mapping=ColumnMapping({
                    "hit_genes":         hits,
                    "case_insensitive":  True,
                    "min_hit":           5,
                }),
                output_dir=Path(tmp),
            )
        except Exception as e:
            diffs.append(f"[E] hallmark gmt load failed: "
                          f"{type(e).__name__}: {e}")
            return diffs
        if int(out["n_pathways_tested"]) < 5:
            diffs.append(f"[E] only {out['n_pathways_tested']} hallmark "
                          f"sets tested (expect ≥5)")
        e_df = pd.read_csv(out["enrichment_table_csv"])
        if e_df.empty:
            diffs.append("[E] hallmark enrichment table empty")
        elif "TNF-alpha Signaling via NF-kB" not in e_df.head(3)["pathway"].tolist():
            diffs.append(
                f"[E] NF-kB hits should put 'TNF-alpha Signaling via "
                f"NF-kB' in top-3; got {e_df.head(3)['pathway'].tolist()}")
    return diffs


def selftest() -> Dict[str, Any]:
    """5-scenario GT suite for pathway_enrichment_ora.

      GT-A  synthetic 3-pathway: PATHWAY_P must rank #1 with p < 1e-30
      GT-B  hypergeom p_value exactly matches scipy reference
      GT-C  DE table → automatic threshold filter resolves correct hits
      GT-D  alias column names (gene_symbol/FDR/logFC) get renamed
      GT-E  bundled MSigDB Hallmark NF-kB hits put TNF-α/NF-kB in top-3
    """
    diffs = (_gt_a_synthetic_pathway_first()
             + _gt_b_hypergeom_exact()
             + _gt_c_from_de_table()
             + _gt_d_alias_columns()
             + _gt_e_msigdb_hallmark_smoke())
    return {
        "ok": len(diffs) == 0,
        "summary": ("5/5 pass: synthetic #1, scipy hypergeom exact, "
                    "DE-table filter, alias-rename, MSigDB Hallmark smoke"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["pathway_enrichment_ora"],
                    "n_scenarios": 5},
    }


if __name__ == "__main__":
    rep = selftest()
    print(f"pathway_enrichment_ora SELFTEST: "
           f"{'PASS' if rep['ok'] else 'FAIL'}")
    print(f"  {rep['summary']}")
    for d in rep["details"]["diffs"]:
        print(f"  {d}")
