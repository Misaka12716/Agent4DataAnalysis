"""edgeR differential expression - quasi-likelihood F-test for RNA-seq counts.

Uses TMM normalization + quasi-likelihood F-test (or LRT / exactTest).
Input: counts matrix CSV (genes x samples) + sample metadata CSV.

Reference:
- Robinson MD, McCarthy DJ, Smyth GK (2010) Bioinformatics 26:139.
- Chen Y, Lun ATL, Smyth GK (2016) F1000Research 5:1438.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract

CONTRACT = SolverContract(
    name="edger_de",
    capability="F04_group_difference_hypothesis_test",
    description=(
        "Differential expression for RNA-seq counts using edgeR "
        "(TMM normalization + quasi-likelihood F-test / LRT / exactTest). "
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
            "Reference group label (denominator of logFC). Optional; "
            "if omitted alphabetical-first is used.",
            optional=True,
        ),
        "group_b": RoleSpec(
            Role.PARAMS,
            "Test group label (numerator of logFC). Optional.",
            optional=True,
        ),
        "test_method": RoleSpec(
            Role.PARAMS,
            "Test method: qlf (quasi-likelihood F, default), lrt, or exact.",
            optional=True,
        ),
        "group_field": RoleSpec(
            Role.PARAMS,
            "Column in sample_groups_csv for grouping: 'group' or "
            "'group_description'. Default: 'group_description'.",
            optional=True,
        ),
    },
    static_params={"test_method": "qlf", "group_field": "group_description",
                   "alpha": 0.05, "min_count": 10, "min_total_count": 15},
    output_files={"deg_table_csv": "deg_table.csv"},
    output_kind={"deg_table_csv": "s"},  # rows = genes with DE stats
)


class EdgeRSolver:
    contract = CONTRACT

    def __init__(self, test_method: str = "qlf",
                 group_field: str = "group_description",
                 alpha: float = 0.05, min_count: int = 10,
                 min_total_count: int = 15):
        self.test_method = test_method
        self.group_field = group_field
        self.alpha = alpha
        self.min_count = min_count
        self.min_total_count = min_total_count

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

        # Heuristic: set index if first column is gene names
        if counts_df.dtypes.iloc[0] == object:
            counts_df = counts_df.set_index(counts_df.columns[0])
        if groups_df.dtypes.iloc[0] == object:
            groups_df = groups_df.set_index(groups_df.columns[0])

        group_a = mapping.get("group_a")
        group_b = mapping.get("group_b")
        test_method = mapping.get("test_method") or self.test_method
        group_field = mapping.get("group_field") or self.group_field

        n_input = len(counts_df)

        # R environment
        try:
            from configs.config import R_LIBS_USER as _R_LIBS_USER
        except Exception:
            _R_LIBS_USER = str(Path(__file__).parents[4] / "Rlibrary")
        os.environ["R_LIBS_USER"] = _R_LIBS_USER
        globalenv["r_url"] = _R_LIBS_USER
        ro.r(".libPaths(r_url)")
        pandas2ri.activate()

        try:
            importr("edgeR")
        except Exception as e:
            raise ImportError(f"R package edgeR not available: {e}")

        globalenv["counts_mat"] = pandas2ri.py2rpy(counts_df)
        globalenv["coldata"] = pandas2ri.py2rpy(groups_df)
        globalenv["group_fld"] = group_field
        globalenv["ref_a"] = group_a if group_a else ro.NULL
        globalenv["ref_b"] = group_b if group_b else ro.NULL
        globalenv["test_m"] = test_method
        globalenv["min_cnt"] = int(self.min_count)
        globalenv["min_total"] = int(self.min_total_count)
        globalenv["alpha_v"] = float(self.alpha)

        ro.r("""
            coldata[[group_fld]] <- factor(coldata[[group_fld]])
            lvls <- levels(coldata[[group_fld]])
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
                ref <- if (!is.na(.auto_ref)) .auto_ref else lvls[1]
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

            y <- DGEList(counts = counts_int, group = coldata[[group_fld]])
            keep <- filterByExpr(y, min.count = min_cnt, min.total.count = min_total)
            y <- y[keep, , keep.lib.sizes = FALSE]
            y <- calcNormFactors(y, method = "TMM")
            design <- model.matrix(~ coldata[[group_fld]])
            y <- estimateDisp(y, design)

            if (test_m == "qlf") {
                fit <- glmQLFit(y, design)
                qlf <- glmQLFTest(fit, coef = 2)
            } else if (test_m == "lrt") {
                fit <- glmFit(y, design)
                qlf <- glmLRT(fit, coef = 2)
            } else if (test_m == "exact") {
                qlf <- exactTest(y)
            } else {
                stop(paste("Unknown test_method:", test_m))
            }

            res_df <- as.data.frame(topTags(qlf, n = Inf)$table)
            res_df$gene <- rownames(res_df)
            n_after_filter <- nrow(y$counts)
            norm_factors <- y$samples$norm.factors
            names(norm_factors) <- rownames(y$samples)
        """)

        _res = globalenv["res_df"]
        de_df = _res if isinstance(_res, pd.DataFrame) else pandas2ri.rpy2py(_res)

        fdr_col = "FDR" if "FDR" in de_df.columns else "padj"
        lfc_col = "logFC"

        de_df[fdr_col] = pd.to_numeric(de_df[fdr_col], errors="coerce")
        de_df[lfc_col] = pd.to_numeric(de_df[lfc_col], errors="coerce")

        sig_mask = (de_df[fdr_col] < self.alpha) & de_df[fdr_col].notna()
        up_mask = sig_mask & (de_df[lfc_col] > 0)
        down_mask = sig_mask & (de_df[lfc_col] < 0)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / CONTRACT.output_files["deg_table_csv"]
        de_df.to_csv(path, index=False)

        return {
            "deg_table_csv": str(path),
            "n_genes_input": n_input,
            "n_genes_after_filter": int(list(globalenv["n_after_filter"])[0]),
            "n_significant": int(sig_mask.sum()),
            "n_up": int(up_mask.sum()),
            "n_down": int(down_mask.sum()),
            "test_method": test_method,
            "alpha": self.alpha,
            "method": f"edgeR {test_method.upper()} (TMM-normalized NB GLM)",
        }


def get_solver(test_method: str = "qlf", group_field: str = "group_description",
               alpha: float = 0.05, min_count: int = 10,
               min_total_count: int = 15):
    return EdgeRSolver(test_method=test_method, group_field=group_field,
                       alpha=alpha, min_count=min_count, min_total_count=min_total_count)


def selftest():
    """Synthetic 10 genes x 6 samples; edgeR should run without error."""
    import tempfile
    rng = np.random.default_rng(42)
    n_genes = 100
    n_samples = 6
    base = rng.poisson(100, (n_genes, n_samples)).astype(int)
    base[0:10, 3:] += rng.poisson(200, (10, 3))  # inject DEGs
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
            s = get_solver(test_method="qlf")
            out = s.run(pd.DataFrame(), ColumnMapping({
                "counts_matrix_csv": str(gm_p),
                "sample_groups_csv": str(sg_p),
                "group_a": "control", "group_b": "treated",
            }), tmp / "out")
            res = pd.read_csv(out['deg_table_csv'])
            if len(res) == 0:
                diffs.append("empty result table")
            if out['n_significant'] < 1:
                diffs.append("no significant DEGs found with injected effect")
        except Exception as e:
            diffs.append(f"edgeR selftest error: {e}")
    return {
        "ok": len(diffs) == 0,
        "summary": "edgeR synthetic test passed" if not diffs else f"{len(diffs)} error(s)",
        "details": {"diffs": diffs, "tested": ["edger_de"]},
    }
