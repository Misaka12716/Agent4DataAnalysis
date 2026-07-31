
"""Morgan (ECFP) fingerprint generator for SMILES columns.

Converts a SMILES column into a fixed-length binary fingerprint vector
(ECFP4 / Morgan radius=2, 2048 bits by default).  Output is a wide CSV
ready for downstream ML operators (random_forest, svm_rbf, etc.).

Reference: Rogers & Hahn (2010) J. Chem. Inf. Model. 50:742-754.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from rdkit import RDLogger
    from rdkit import DataStructs
    RDLogger.logger().setLevel(RDLogger.ERROR)
    _RDKIT_OK = True
except ImportError:
    _RDKIT_OK = False

CONTRACT = SolverContract(
    name="morgan_fingerprint",
    capability="F15_cheminformatics_fingerprint",
    description=(
        "Generate Morgan/ECFP4 fingerprints (2048-bit) from a SMILES column. "
        "Invalid/unparseable SMILES produce a row of NaN and a warning log. "
        "Original non-SMILES columns (label/activity/id/metadata) are passed "
        "through so a downstream classifier can find its target. "
        "Output: fingerprints.csv (id + passthrough columns + 2048 fp_{i} columns), "
        "fingerprint_stats.json (n_valid, n_invalid, invalid_ids)."
    ),
    roles={
        "id_col": RoleSpec(
            Role.ID,
            "Row identifier column. Optional — if not provided we synthesize "
            "a 0..N-1 row index and report it under 'row_id'.",
            optional=True,
        ),
        "smiles_col": RoleSpec(
            Role.TEXT,
            "Column containing SMILES strings (e.g. 'CCO', 'c1ccccc1')",
        ),
    },
    static_params={"radius": 2, "n_bits": 2048, "use_features": False},
    output_files={
        "fingerprints_csv": "fingerprints.csv",
        "stats_json": "fingerprint_stats.json",
    },
    output_kind={
        "fingerprints_csv": "t",
        "stats_json": "s",
    },
)


class MorganFingerprintSolver:
    contract = CONTRACT

    def __init__(self, radius: int = 2, n_bits: int = 2048,
                 use_features: bool = False):
        if not _RDKIT_OK:
            raise ImportError("rdkit is required for morgan_fingerprint")
        self.radius = radius
        self.n_bits = n_bits
        self.use_features = use_features

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping.get("id_col")
        smiles_col = mapping.get("smiles_col")
        if not smiles_col:
            raise OperatorInputError(
                "MISSING_REQUIRED_COLUMNS",
                solver="morgan_fingerprint",
                hint="smiles_col is required",
            )
        # If id_col is not given or not in df, synthesize a row index.
        if id_col and id_col in df.columns:
            ids = df[id_col].astype(str).tolist()
            id_name = id_col
        else:
            ids = [str(i) for i in range(len(df))]
            id_name = "row_id"
        radius = self.radius
        n_bits = self.n_bits

        smiles_list = df[smiles_col].astype(str).tolist()

        fp_matrix = np.full((len(smiles_list), n_bits), np.nan, dtype=np.float64)
        valid_mask = np.zeros(len(smiles_list), dtype=bool)
        invalid_ids: List[str] = []

        for i, smi in enumerate(smiles_list):
            smi = smi.strip()
            if not smi:
                invalid_ids.append(ids[i])
                continue
            mol = Chem.MolFromSmiles(smi)
            if mol is None:
                invalid_ids.append(ids[i])
                continue
            fp = AllChem.GetMorganFingerprintAsBitVect(
                mol, radius, nBits=n_bits, useFeatures=self.use_features,
            )
            arr = np.zeros(n_bits, dtype=np.float64)
            DataStructs.ConvertToNumpyArray(fp, arr)
            fp_matrix[i] = arr
            valid_mask[i] = True

        n_total = len(smiles_list)
        n_valid = int(valid_mask.sum())
        n_invalid = n_total - n_valid

        fp_cols = [f"fp_{j}" for j in range(n_bits)]
        fp_df = pd.DataFrame(fp_matrix, columns=fp_cols, dtype=np.float64)

        # Pass through the original non-SMILES columns (label / activity /
        # id / metadata) so downstream ML operators can still find the
        # target column; otherwise fingerprints.csv would only carry id +
        # fp_{i} bits and the label would be lost.
        passthrough = (df.drop(columns=[smiles_col], errors="ignore")
                         .reset_index(drop=True))
        passthrough = passthrough[[c for c in passthrough.columns
                                    if c not in fp_cols]]
        fp_df = pd.concat([passthrough, fp_df.reset_index(drop=True)], axis=1)
        if id_name not in fp_df.columns:
            fp_df.insert(0, id_name, ids)

        fp_csv_path = output_dir / "fingerprints.csv"
        fp_df.to_csv(fp_csv_path, index=False)

        import json
        stats = {
            "n_total": n_total, "n_valid": n_valid, "n_invalid": n_invalid,
            "invalid_ids": invalid_ids[:100],
            "n_bits": n_bits, "radius": radius,
        }
        stats_path = output_dir / "fingerprint_stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

        return {
            "fingerprints_csv": str(fp_csv_path),
            "stats_json": str(stats_path),
            "n_valid": n_valid,
            "n_invalid": n_invalid,
        }


def get_solver(radius: int = 2, n_bits: int = 2048,
               use_features: bool = False):
    return MorganFingerprintSolver(radius=radius, n_bits=n_bits,
                                   use_features=use_features)
