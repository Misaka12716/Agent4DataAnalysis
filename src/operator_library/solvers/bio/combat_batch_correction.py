"""ComBat / ComBat_seq batch effect correction.

Fits location/scale adjustments using Empirical Bayes for microarray (ComBat)
or negative-binomial for RNA-seq (ComBat_seq). Input: expression matrix CSV
(genes x samples) + sample metadata CSV (with batch column).

Reference:
- Johnson WE, Li C, Rabinovic A (2007) Biostatistics 8:118 (ComBat).
- Zhang Y, Jenkins DF, Leek JT (2018) NAR 46:e127 (ComBat_seq).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract

CONTRACT = SolverContract(
    name="combat_batch_correction",
    capability="F01_data_governance_cleaning",
    description=(
        "Batch effect correction using ComBat (microarray, Empirical Bayes) "
        "or ComBat_seq (RNA-seq, Negative Binomial). "
        "Input: expression matrix CSV (genes x samples) + sample_metadata CSV "
        "(with batch column). Output: corrected_matrix.csv."
    ),
    roles={
        "expression_matrix_csv": RoleSpec(
            Role.PARAMS,
            "Path to expression matrix CSV. Rows = genes, columns = samples. "
            "For microarray, values should be log-transformed; for rnaseq, raw counts.",
        ),
        "sample_metadata_csv": RoleSpec(
            Role.PARAMS,
            "Path to sample metadata CSV with at minimum a batch column.",
        ),
        "batch_col": RoleSpec(
            Role.PARAMS,
            "Column name in sample_metadata that identifies batch.",
        ),
        "data_type": RoleSpec(
            Role.PARAMS,
            "Data type: 'microarray' (uses ComBat) or 'rnaseq' (uses ComBat_seq). "
            "Default: 'microarray'.",
            optional=True,
        ),
        "covariates_of_interest": RoleSpec(
            Role.PARAMS,
            "Biological covariates to preserve (e.g. treatment, disease status). "
            "Comma-separated column names from sample_metadata.",
            optional=True,
        ),
        "par_prior": RoleSpec(
            Role.PARAMS,
            "Use parametric prior? TRUE/FALSE. Default: TRUE.",
            optional=True,
        ),
        "mean_only": RoleSpec(
            Role.PARAMS,
            "Correct mean only? TRUE/FALSE. Default: FALSE.",
            optional=True,
        ),
        "ref_batch": RoleSpec(
            Role.PARAMS,
            "Reference batch level. Default: NULL (use first batch).",
            optional=True,
        ),
    },
    static_params={"data_type": "microarray", "par_prior": True,
                   "mean_only": False},
    output_files={"corrected_matrix_csv": "corrected_matrix.csv"},
    output_kind={"corrected_matrix_csv": "t"},  # batch-corrected expr matrix
)


class ComBatBatchCorrectionSolver:
    contract = CONTRACT

    def __init__(self, data_type: str = "microarray",
                 par_prior: bool = True, mean_only: bool = False):
        self.data_type = data_type
        self.par_prior = par_prior
        self.mean_only = mean_only

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        import os
        import pandas as _pd; _pd.DataFrame.iteritems = _pd.DataFrame.items  # rpy2 compat
        import rpy2.robjects as ro
        from rpy2.robjects import pandas2ri, globalenv
        from rpy2.robjects.packages import importr

        expr_path = mapping.get("expression_matrix_csv")
        meta_path = mapping.get("sample_metadata_csv")
        batch_col = mapping.get("batch_col")
        if not expr_path or not meta_path or not batch_col:
            raise ValueError(
                "expression_matrix_csv, sample_metadata_csv and batch_col are required")

        expr_df = pd.read_csv(expr_path)
        meta_df = pd.read_csv(meta_path)

        if expr_df.dtypes.iloc[0] == object:
            expr_df = expr_df.set_index(expr_df.columns[0])
        if meta_df.dtypes.iloc[0] == object:
            meta_df = meta_df.set_index(meta_df.columns[0])

        data_type = mapping.get("data_type") or self.data_type
        par_prior_str = str(mapping.get("par_prior", self.par_prior)).upper()
        mean_only_str = str(mapping.get("mean_only", self.mean_only)).upper()
        ref_batch = mapping.get("ref_batch")
        covariates_raw = mapping.get("covariates_of_interest")

        # Handle covariates: string -> list
        mod_cols = None
        if covariates_raw:
            if isinstance(covariates_raw, str):
                mod_cols = [c.strip() for c in covariates_raw.split(",") if c.strip()]
            elif isinstance(covariates_raw, list):
                mod_cols = covariates_raw

        try:
            from configs.config import R_LIBS_USER as _R_LIBS_USER
        except Exception:
            _R_LIBS_USER = str(Path(__file__).parents[4] / "Rlibrary")
        os.environ["R_LIBS_USER"] = _R_LIBS_USER
        globalenv["r_url"] = _R_LIBS_USER
        ro.r(".libPaths(r_url)")
        pandas2ri.activate()

        try:
            importr("sva")
        except Exception as e:
            raise ImportError(f"R package sva (ComBat) not available: {e}")

        globalenv["expr_mat"] = pandas2ri.py2rpy(expr_df)
        globalenv["meta_df"] = pandas2ri.py2rpy(meta_df)
        globalenv["batch_col_r"] = batch_col
        globalenv["par_prior_r"] = par_prior_str == "TRUE"
        globalenv["mean_only_r"] = mean_only_str == "TRUE"
        globalenv["ref_batch_r"] = ref_batch if ref_batch else ro.NULL
        globalenv["data_type_r"] = data_type

        if mod_cols and len(mod_cols) > 0:
            globalenv["mod_formula_str"] = "~ " + " + ".join(mod_cols)
        else:
            globalenv["mod_formula_str"] = "~ 0"

        if data_type == "microarray":
            ro.r("""
                batch_vec <- as.character(meta_df[[batch_col_r]])
                mod_mat <- model.matrix(as.formula(mod_formula_str),
                                        data = meta_df)
                expr_mat_np <- as.matrix(expr_mat)
                corrected <- ComBat(
                    dat = expr_mat_np,
                    batch = batch_vec,
                    mod = mod_mat,
                    par.prior = par_prior_r,
                    mean.only = mean_only_r,
                    ref.batch = ref_batch_r)
                corrected_df <- as.data.frame(corrected)
            """)
        elif data_type == "rnaseq":
            try:
                importr("sva")  # ComBat_seq is in sva package
            except Exception:
                pass
            ro.r("""
                batch_vec <- as.character(meta_df[[batch_col_r]])
                group_vec <- NULL
                corrected <- ComBat_seq(
                    counts = as.matrix(expr_mat),
                    batch = batch_vec,
                    group = group_vec,
                    full_mod = !is.null(group_vec))
                corrected_df <- as.data.frame(corrected)
            """)
        else:
            raise ValueError(
                f"Unknown data_type: {data_type}. Use 'microarray' or 'rnaseq'.")

        _res_obj = globalenv["corrected_df"]
        corrected_df = _res_obj if isinstance(
            _res_obj, pd.DataFrame) else pandas2ri.rpy2py(_res_obj)
        try:
            corrected_df.index = expr_df.index
            corrected_df.columns = expr_df.columns
        except Exception:
            pass

        n_batches = meta_df[batch_col].nunique()
        batches = sorted(meta_df[batch_col].astype(str).unique().tolist())

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / CONTRACT.output_files["corrected_matrix_csv"]
        corrected_df.to_csv(path)

        return {
            "corrected_matrix_csv": str(path),
            "n_genes": int(len(corrected_df)),
            "n_samples": int(len(corrected_df.columns)),
            "n_batches": int(n_batches),
            "batches": batches,
            "data_type": data_type,
            "preserved_covariates": mod_cols if mod_cols else [],
            "method": ("ComBat (Empirical Bayes)" if data_type == "microarray"
                       else "ComBat_seq (Negative Binomial)"),
        }


def get_solver(data_type: str = "microarray", par_prior: bool = True,
               mean_only: bool = False):
    return ComBatBatchCorrectionSolver(
        data_type=data_type, par_prior=par_prior, mean_only=mean_only)


def selftest():
    """Synthetic 50 genes x 9 samples with 2 batches; ComBat should run."""
    import tempfile
    rng = np.random.default_rng(42)
    n_genes = 50
    n_samples = 9
    # Generate data with batch effects
    base = rng.normal(10, 2, (n_genes, n_samples))
    # Add batch effect: batch 2 has +3 shift
    base[:, 3:6] += 3.0
    base[:, 6:] += 6.0

    samples = [f"S{i+1}" for i in range(n_samples)]
    gm = pd.DataFrame(base, columns=samples)
    gm.insert(0, "gene_id", [f"G{i}" for i in range(n_genes)])
    meta = pd.DataFrame({
        "sample_id": samples,
        "batch": ["B1"] * 3 + ["B2"] * 3 + ["B3"] * 3,
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gm_p = tmp / "expr.csv"
        meta_p = tmp / "meta.csv"
        gm.to_csv(gm_p, index=False)
        meta.to_csv(meta_p, index=False)
        try:
            s = get_solver(data_type="microarray")
            out = s.run(pd.DataFrame(), ColumnMapping({
                "expression_matrix_csv": str(gm_p),
                "sample_metadata_csv": str(meta_p),
                "batch_col": "batch",
            }), tmp / "out")
            corr = pd.read_csv(out['corrected_matrix_csv'])
            if corr.shape[0] != n_genes:
                diffs.append(
                    f"output rows {corr.shape[0]} != {n_genes}")
            if out['n_batches'] != 3:
                diffs.append(f"n_batches={out['n_batches']} != 3")
        except Exception as e:
            diffs.append(f"ComBat selftest error: {e}")
    return {
        "ok": len(diffs) == 0,
        "summary": "ComBat synthetic test passed" if not diffs else f"{len(diffs)} error(s)",
        "details": {"diffs": diffs, "tested": ["combat_batch_correction"]},
    }
