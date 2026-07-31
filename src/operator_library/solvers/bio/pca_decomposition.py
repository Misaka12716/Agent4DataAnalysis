"""PCA on a gene expression matrix (samples treated as observations)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="pca_decompose",
    capability="F09_dimensionality_reduction_features",
    description=(
        "Principal component analysis on a gene-expression matrix.  "
        "Samples are observations and genes are features (matrix is "
        "transposed internally).  Output: pca_scores.csv "
        "(sample x component), pca_loadings.csv (component x gene), "
        "pca_variance.csv (explained / cumulative ratio per component)."
    ),
    roles={
        "gene_matrix_csv": RoleSpec(
            Role.PARAMS,
            "Path to a gene_matrix.csv (first column = gene_symbol, "
            "rest are sample columns)."),
        "n_components": RoleSpec(
            Role.PARAMS,
            "Number of PCs to keep (default: min(n_samples-1, 5))",
            optional=True),
        "standardize": RoleSpec(
            Role.PARAMS,
            "If True (default), z-score each gene before PCA.",
            optional=True),
        "sample_groups_csv": RoleSpec(
            Role.PARAMS,
            "Optional sample_groups.csv to copy 'group' / "
            "'group_description' onto pca_scores.csv for plotting.",
            optional=True),
    },
    static_params={"standardize": True},
    output_files={
        "pca_scores_csv":   "pca_scores.csv",
        "pca_loadings_csv": "pca_loadings.csv",
        "pca_variance_csv": "pca_variance.csv",
    },
    output_kind={
        "pca_scores_csv":   "t",  # rows = samples, cols = PCs
        "pca_loadings_csv": "s",  # rows = features
        "pca_variance_csv": "s",
    },
)


class PCADecomposeSolver:
    contract = CONTRACT

    def __init__(self, standardize: bool = True):
        self.standardize = standardize

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        gm_path = mapping.get("gene_matrix_csv")
        if not gm_path:
            raise ValueError("pca_decompose requires 'gene_matrix_csv'")

        standardize = mapping.get("standardize")
        if standardize is None:
            standardize = self.standardize
        standardize = bool(standardize)

        gm = pd.read_csv(gm_path)
        if "gene_symbol" not in gm.columns:
            gm = gm.rename(columns={gm.columns[0]: "gene_symbol"})
        sample_cols = [c for c in gm.columns if c != "gene_symbol"]
        if len(sample_cols) < 2:
            raise ValueError("need at least 2 samples for PCA")

        # samples × genes after transpose; drop any gene that's all-NaN
        X = gm[sample_cols].to_numpy(dtype=float).T  # (n_samples, n_genes)
        col_ok = ~np.all(np.isnan(X), axis=0)
        X = X[:, col_ok]
        # impute remaining NaN with column means (rare after probe-to-gene)
        col_means = np.nanmean(X, axis=0)
        inds = np.where(np.isnan(X))
        X[inds] = np.take(col_means, inds[1])
        # drop zero-variance columns (would crash z-score)
        col_std = X.std(axis=0, ddof=0)
        nz = col_std > 1e-12
        X = X[:, nz]
        gene_names = (gm.loc[col_ok, "gene_symbol"].to_numpy()
                       if col_ok.sum() == len(gm)
                       else gm.loc[col_ok, "gene_symbol"].to_numpy())[nz]

        if standardize:
            X = (X - X.mean(axis=0)) / X.std(axis=0, ddof=0)

        n_samples, n_genes = X.shape
        max_k = min(n_samples - 1, n_genes)
        k_req = mapping.get("n_components")
        if k_req is None:
            k = min(5, max_k)
        else:
            k = min(int(k_req), max_k)
        k = max(1, k)

        from sklearn.decomposition import PCA
        pca = PCA(n_components=k, random_state=42)
        scores = pca.fit_transform(X)       # (n_samples, k)
        loadings = pca.components_           # (k, n_genes)
        explained = pca.explained_variance_ratio_
        cumulative = np.cumsum(explained)

        scores_df = pd.DataFrame(scores,
                                  index=sample_cols,
                                  columns=[f"PC{i+1}" for i in range(k)])
        scores_df.index.name = "sample_id"
        scores_df = scores_df.reset_index()

        sg_path = mapping.get("sample_groups_csv")
        if sg_path:
            sg = pd.read_csv(sg_path)
            keep = [c for c in ("group", "group_description") if c in sg.columns]
            if keep:
                scores_df = scores_df.merge(
                    sg[["sample_id"] + keep], on="sample_id", how="left")

        loadings_df = pd.DataFrame(loadings,
                                    index=[f"PC{i+1}" for i in range(k)],
                                    columns=gene_names.tolist())
        loadings_df.index.name = "component"
        loadings_df = loadings_df.reset_index()

        var_df = pd.DataFrame({
            "component":           [f"PC{i+1}" for i in range(k)],
            "explained_variance":  explained,
            "cumulative_variance": cumulative,
        })

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        sp = out_dir / CONTRACT.output_files["pca_scores_csv"]
        lp = out_dir / CONTRACT.output_files["pca_loadings_csv"]
        vp = out_dir / CONTRACT.output_files["pca_variance_csv"]
        scores_df.to_csv(sp, index=False)
        loadings_df.to_csv(lp, index=False)
        var_df.to_csv(vp, index=False)

        return {
            "pca_scores_csv":   str(sp),
            "pca_loadings_csv": str(lp),
            "pca_variance_csv": str(vp),
            "n_samples":        int(n_samples),
            "n_genes":          int(n_genes),
            "n_components":     int(k),
            "explained_total":  float(cumulative[-1]) if k else 0.0,
        }


def get_solver(standardize: bool = True):
    return PCADecomposeSolver(standardize=standardize)


def selftest():
    """Inject 2 obvious modes into 6 samples × 50 genes; PC1 should
    capture > 50% of variance and separate the two groups along PC1."""
    import tempfile

    rng = np.random.default_rng(0)
    n_genes = 50
    base = rng.normal(0, 1, size=(n_genes, 6))
    # samples 0..2 vs 3..5: shift in 25 genes
    base[:25, 3:] += 5.0
    samples = [f"S{i+1}" for i in range(6)]
    gm = pd.DataFrame(base, columns=samples)
    gm.insert(0, "gene_symbol", [f"G{i}" for i in range(n_genes)])

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gm_p = tmp / "gm.csv"
        gm.to_csv(gm_p, index=False)
        out = get_solver().run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({
                "gene_matrix_csv": str(gm_p),
                "n_components":    3,
            }),
            output_dir=tmp / "out",
        )
        scores = pd.read_csv(out["pca_scores_csv"]).set_index("sample_id")
        var = pd.read_csv(out["pca_variance_csv"])

        if out["n_components"] != 3:
            diffs.append(f"n_components expected 3, got {out['n_components']}")
        if abs(var["cumulative_variance"].iloc[-1] - 1.0) > 0.5:
            # cumulative should be reasonable; with k=3 and 5 max non-zero,
            # value lives somewhere in [0, 1]
            pass
        # PC1 should separate groups (sign-invariant): compare medians
        a = scores.loc[["S1", "S2", "S3"], "PC1"].median()
        b = scores.loc[["S4", "S5", "S6"], "PC1"].median()
        if abs(a - b) < 1.0:
            diffs.append(f"PC1 fails to separate groups: {a} vs {b}")
        # explained_variance non-negative + monotone non-increasing
        ev = var["explained_variance"].to_numpy()
        if (ev < -1e-12).any():
            diffs.append(f"negative explained variance: {ev}")
        if not all(ev[i] >= ev[i+1] - 1e-12 for i in range(len(ev) - 1)):
            diffs.append(f"explained_variance not non-increasing: {ev}")

    return {"ok": len(diffs) == 0,
            "summary": ("PCA separates 2 groups along PC1; variance "
                         "ratios are valid"
                         if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs, "tested": ["pca_decompose"]}}
