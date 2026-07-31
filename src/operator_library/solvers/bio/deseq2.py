"""DESeq2 differential expression for RNA-seq counts.

Uses Negative Binomial GLM with size factor normalization (median-of-ratios)
and Wald test. Input: counts matrix CSV + sample metadata CSV.

Reference:
- Love MI, Huber W, Anders S (2014) Genome Biol 15:550.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract

CONTRACT = SolverContract(
    name="deseq2_de",
    capability="F04_group_difference_hypothesis_test",
    description=(
        "Differential expression for RNA-seq counts using DESeq2 "
        "(median-of-ratios normalization + Wald test). "
        "Input: counts_matrix CSV (genes x samples) + sample_groups CSV "
        "(sample_id + group columns). Output: deg_table.csv."
    ),
    roles={
        "counts_matrix_csv": RoleSpec(
            Role.PARAMS,
            "Path to a counts matrix CSV. First column = gene_id, "
            "remaining columns = sample counts (integer).",
        ),
        "sample_groups_csv": RoleSpec(
            Role.PARAMS,
            "Path to sample_groups.csv with columns sample_id + group "
            "(or group_description). Must reference exactly 2 groups.",
        ),
        "group_a": RoleSpec(
            Role.PARAMS,
            "Reference group label. Optional.",
            optional=True,
        ),
        "group_b": RoleSpec(
            Role.PARAMS,
            "Test group label. Optional.",
            optional=True,
        ),
        "group_field": RoleSpec(
            Role.PARAMS,
            "Column in sample_groups_csv for grouping. "
            "Default: 'group_description'.",
            optional=True,
        ),
    },
    static_params={"alpha": 0.05, "lfc_threshold": 0.0,
                   "independent_filtering": True, "min_count": 10},
    output_files={"deg_table_csv": "deg_table.csv"},
    output_kind={"deg_table_csv": "s"},
)


class DESeq2Solver:
    contract = CONTRACT

    def __init__(self, alpha: float = 0.05, lfc_threshold: float = 0.0,
                 independent_filtering: bool = True, min_count: int = 10):
        self.alpha = alpha
        self.lfc_threshold = lfc_threshold
        self.independent_filtering = independent_filtering
        self.min_count = min_count

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        import os
        import pandas as _pd; _pd.DataFrame.iteritems = _pd.DataFrame.items  # rpy2 compat
        import rpy2.robjects as ro
        from rpy2.robjects import pandas2ri, globalenv
        from rpy2.robjects.packages import importr

        counts_path = mapping.get("counts_matrix_csv")
        groups_path = mapping.get("sample_groups_csv")
        if not counts_path or not groups_path:
            raise ValueError("counts_matrix_csv and sample_groups_csv are required")

        counts_df = pd.read_csv(counts_path)
        groups_df = pd.read_csv(groups_path)

        if counts_df.dtypes.iloc[0] == object:
            counts_df = counts_df.set_index(counts_df.columns[0])
        if groups_df.dtypes.iloc[0] == object:
            groups_df = groups_df.set_index(groups_df.columns[0])

        group_a = mapping.get("group_a")
        group_b = mapping.get("group_b")
        group_field = mapping.get("group_field") or "group_description"

        n_input = len(counts_df)
        keep = counts_df.sum(axis=1) >= self.min_count
        counts_df = counts_df.loc[keep]
        n_after_filter = len(counts_df)

        try:
            from configs.config import R_LIBS_USER as _R_LIBS_USER
        except Exception:
            _R_LIBS_USER = str(Path(__file__).parents[4] / "Rlibrary")
        os.environ["R_LIBS_USER"] = _R_LIBS_USER
        globalenv["r_url"] = _R_LIBS_USER
        ro.r(".libPaths(r_url)")
        pandas2ri.activate()

        try:
            importr("DESeq2")
        except Exception as e:
            raise ImportError(f"R package DESeq2 not available: {e}")

        globalenv["counts_mat"] = pandas2ri.py2rpy(counts_df)
        globalenv["coldata"] = pandas2ri.py2rpy(groups_df)
        globalenv["group_fld"] = group_field
        globalenv["ref_a"] = group_a if group_a else ro.NULL
        globalenv["ref_b"] = group_b if group_b else ro.NULL
        globalenv["lfc_thr"] = float(self.lfc_threshold)
        globalenv["alpha_v"] = float(self.alpha)
        globalenv["ind_filt"] = self.independent_filtering

        ro.r("""
            coldata[[group_fld]] <- factor(coldata[[group_fld]])
            lvls <- levels(coldata[[group_fld]])
            # 当用户没指定 ref 时, 优先把常见对照组名当 ref;
            # 没匹配再退到首位 level (factor 默认行为).
            .ctrl_keywords <- c("untrt","untreated","control","ctrl","placebo",
                                "wt","wildtype","wild_type","baseline","normal",
                                "ref","reference","sham","dmso","vehicle")
            .auto_ref <- NA_character_
            for (lv in lvls) {
                if (tolower(lv) %in% .ctrl_keywords) {
                    .auto_ref <- lv; break
                }
            }
            if (is.null(ref_a) && is.null(ref_b)) {
                if (!is.na(.auto_ref)) {
                    ref <- .auto_ref
                } else {
                    ref <- lvls[1]
                }
                test_grp <- setdiff(lvls, ref)[1]
            } else if (!is.null(ref_a)) {
                ref <- ref_a
                test_grp <- ref_b
            } else {
                ref <- if (!is.na(.auto_ref)) .auto_ref else lvls[1]
                test_grp <- setdiff(lvls, ref)[1]
            }
            coldata[[group_fld]] <- relevel(coldata[[group_fld]], ref = ref)
            counts_int <- round(as.matrix(counts_mat))
            mode(counts_int) <- "integer"
            dds <- DESeqDataSetFromMatrix(
                countData = counts_int,
                colData = coldata,
                design = as.formula(paste("~", group_fld)))
            dds <- DESeq(dds)
            res <- results(dds, alpha = alpha_v, lfcThreshold = lfc_thr,
                           independentFiltering = ind_filt)
            res_df <- as.data.frame(res)
            res_df$gene <- rownames(res_df)
            res_df <- res_df[order(res_df$padj), ]
            size_factors <- sizeFactors(dds)
        """)

        _res = globalenv["res_df"]
        de_df = _res if isinstance(_res, pd.DataFrame) else pandas2ri.rpy2py(_res)

        de_df["padj"] = pd.to_numeric(de_df["padj"], errors="coerce")
        de_df["log2FoldChange"] = pd.to_numeric(
            de_df["log2FoldChange"], errors="coerce")

        sig_mask = (de_df["padj"] < self.alpha) & de_df["padj"].notna()
        up_mask = sig_mask & (de_df["log2FoldChange"] > 0)
        down_mask = sig_mask & (de_df["log2FoldChange"] < 0)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / CONTRACT.output_files["deg_table_csv"]
        de_df.to_csv(path, index=False)

        return {
            "deg_table_csv": str(path),
            "n_genes_input": n_input,
            "n_genes_after_filter": n_after_filter,
            "n_significant": int(sig_mask.sum()),
            "n_up": int(up_mask.sum()),
            "n_down": int(down_mask.sum()),
            "alpha": self.alpha,
            "lfc_threshold": self.lfc_threshold,
            "method": "DESeq2 Wald test (NB GLM)",
        }


def get_solver(alpha: float = 0.05, lfc_threshold: float = 0.0,
               independent_filtering: bool = True, min_count: int = 10):
    return DESeq2Solver(alpha=alpha, lfc_threshold=lfc_threshold,
                        independent_filtering=independent_filtering,
                        min_count=min_count)


def selftest():
    """Synthetic 100 genes x 6 samples; DESeq2 should run without error."""
    import tempfile
    rng = np.random.default_rng(42)
    n_genes = 100
    n_samples = 6
    base = rng.poisson(100, (n_genes, n_samples)).astype(int)
    base[0:10, 3:] += rng.poisson(200, (10, 3))
    samples = [f"S{i+1}" for i in range(n_samples)]
    gm = pd.DataFrame(base, columns=samples)
    gm.insert(0, "gene_id", [f"G{i}" for i in range(n_genes)])
    sg = pd.DataFrame({
        "sample_id": samples,
        "group": ["A"] * 3 + ["B"] * 3,
        "group_description": ["control"] * 3 + ["treated"] * 3,
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gm_p = tmp / "counts.csv"
        sg_p = tmp / "groups.csv"
        gm.to_csv(gm_p, index=False)
        sg.to_csv(sg_p, index=False)
        try:
            s = get_solver()
            out = s.run(pd.DataFrame(), ColumnMapping({
                "counts_matrix_csv": str(gm_p),
                "sample_groups_csv": str(sg_p),
            }), tmp / "out")
            res = pd.read_csv(out['deg_table_csv'])
            if len(res) == 0:
                diffs.append("empty result table")
            if out['n_significant'] < 1:
                diffs.append("no significant DEGs found")
        except Exception as e:
            diffs.append(f"DESeq2 selftest error: {e}")
    return {
        "ok": len(diffs) == 0,
        "summary": "DESeq2 synthetic test passed" if not diffs else f"{len(diffs)} error(s)",
        "details": {"diffs": diffs, "tested": ["deseq2_de"]},
    }
