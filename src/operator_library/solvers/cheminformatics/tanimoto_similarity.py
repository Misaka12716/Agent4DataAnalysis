"""Tanimoto (Jaccard) similarity matrix from fingerprints.

Computes pairwise Tanimoto similarity between molecular fingerprints.
Input: a CSV with fingerprint columns (e.g. output of morgan_fingerprint).
Output: similarity_matrix.csv and pairs.csv.

Can also compute similarity from a SMILES column directly (then internally
calls morgan_fingerprint first).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError

try:
    from rdkit import Chem, DataStructs
    from rdkit.Chem import AllChem
    from rdkit import RDLogger
    RDLogger.logger().setLevel(RDLogger.ERROR)
    _RDKIT_OK = True
except ImportError:
    _RDKIT_OK = False

CONTRACT = SolverContract(
    name="tanimoto_similarity",
    capability="F15_cheminformatics_similarity",
    description=(
        "Pairwise Tanimoto/Jaccard similarity of molecular fingerprints. "
        "Accepts either fingerprint CSV (fp_0..fp_2047 columns, output of "
        "morgan_fingerprint) or a SMILES column directly. "
        "Output: similarity_matrix.csv, pairs.csv (id_a, id_b, tanimoto)."
    ),
    roles={
        "id_col": RoleSpec(
            Role.ID,
            "Row identifier column. Optional — auto-generated 0..N-1 if absent.",
            optional=True,
        ),
        "smiles_col": RoleSpec(
            Role.TEXT,
            "SMILES column. If provided, fingerprints are computed internally "
            "(radius=2, 2048 bits). If empty, fp_cols must be provided.",
            optional=True,
        ),
        "fp_cols": RoleSpec(
            Role.PARAMS,
            "List of fingerprint column names. Use only when SMILES is unavailable. "
            "These must be 0/1 binary columns.",
            optional=True,
        ),
    },
    static_params={"radius": 2, "n_bits": 2048, "min_similarity": 0.0},
    output_files={
        "similarity_matrix_csv": "similarity_matrix.csv",
        "pairs_csv": "pairs.csv",
    },
    output_kind={"similarity_matrix_csv": "t", "pairs_csv": "s"},
)


class TanimotoSimilaritySolver:
    contract = CONTRACT

    def __init__(self, radius: int = 2, n_bits: int = 2048,
                 min_similarity: float = 0.0):
        if not _RDKIT_OK:
            raise ImportError("rdkit is required for tanimoto_similarity")
        self.radius = radius
        self.n_bits = n_bits
        self.min_similarity = min_similarity

    def run(self, df, mapping, output_dir):
        id_col = mapping.get("id_col")
        smiles_col = mapping.get("smiles_col")
        fp_cols_raw = mapping.get("fp_cols")

        n_rows = len(df)
        if id_col and id_col in df.columns:
            ids = df[id_col].astype(str).tolist()
        else:
            ids = [str(i) for i in range(n_rows)]

        if smiles_col:
            fps = []
            valid_idx = []
            for i, smi in enumerate(df[smiles_col].astype(str)):
                mol = Chem.MolFromSmiles(smi.strip())
                if mol is None:
                    continue
                fp = AllChem.GetMorganFingerprintAsBitVect(
                    mol, self.radius, nBits=self.n_bits,
                )
                fps.append(fp)
                valid_idx.append(i)
            if len(fps) < 2:
                raise OperatorInputError("INSUFFICIENT_SAMPLES",
                    solver="tanimoto_similarity",
                    hint=f"only {len(fps)} valid molecules, need >= 2")
            ids = [ids[i] for i in valid_idx]
        elif fp_cols_raw:
            if isinstance(fp_cols_raw, str):
                fp_cols = [c.strip() for c in fp_cols_raw.split(",") if c.strip()]
            else:
                fp_cols = list(fp_cols_raw)
            missing = [c for c in fp_cols if c not in df.columns]
            if missing:
                raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                    solver="tanimoto_similarity",
                    hint=f"fp columns not found: {missing}")
            fp_arr = df[fp_cols].to_numpy(dtype=np.float64)
            fps = []
            valid_idx = []
            for i in range(n_rows):
                row = fp_arr[i]
                if np.all(np.isnan(row)):
                    continue
                bits = (row > 0.5).astype(int)
                fp = DataStructs.cDataStructs.CreateFromBitString("".join(str(b) for b in bits))
                fps.append(fp)
                valid_idx.append(i)
            ids = [ids[i] for i in valid_idx]
            if len(fps) < 2:
                raise OperatorInputError("INSUFFICIENT_SAMPLES",
                    solver="tanimoto_similarity",
                    hint=f"only {len(fp_arr)} valid fingerprints, need >= 2")
        else:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                solver="tanimoto_similarity",
                hint="provide either smiles_col or fp_cols")

        n = len(fps)
        mat = np.eye(n, dtype=np.float64)
        for i in range(n):
            sims = DataStructs.BulkTanimotoSimilarity(fps[i], fps[:i+1])
            for j in range(i):
                mat[i, j] = sims[j]
                mat[j, i] = sims[j]

        mat_df = pd.DataFrame(mat, index=ids, columns=ids)
        mat_path = output_dir / "similarity_matrix.csv"
        mat_df.to_csv(mat_path)

        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                s = float(mat[i, j])
                if s >= self.min_similarity:
                    pairs.append({"id_a": ids[i], "id_b": ids[j], "tanimoto": s})
        pairs_df = pd.DataFrame(pairs)
        pairs_path = output_dir / "pairs.csv"
        pairs_df.to_csv(pairs_path, index=False)

        return {
            "similarity_matrix_csv": str(mat_path),
            "pairs_csv": str(pairs_path),
            "n_compounds": n,
        }


def get_solver(radius: int = 2, n_bits: int = 2048,
               min_similarity: float = 0.0):
    return TanimotoSimilaritySolver(radius=radius, n_bits=n_bits,
                                     min_similarity=min_similarity)
