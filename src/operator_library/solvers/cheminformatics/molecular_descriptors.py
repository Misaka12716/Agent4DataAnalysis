"""Molecular descriptor calculator for SMILES columns.

Computes 25+ standard RDKit molecular descriptors (MolWt, MolLogP, TPSA,
NumHAcceptors, NumHDonors, RingCount, FractionCsp3, etc.) from a SMILES column.

Output: descriptors.csv (id + descriptor columns), descriptor_stats.json.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen, Lipinski, GraphDescriptors
    from rdkit import RDLogger
    RDLogger.logger().setLevel(RDLogger.ERROR)
    _RDKIT_OK = True
except ImportError:
    _RDKIT_OK = False

_DESCRIPTOR_REGISTRY = [
    ("MolWt",              lambda m: Descriptors.MolWt(m)),
    ("HeavyAtomMolWt",     lambda m: Descriptors.HeavyAtomMolWt(m)),
    ("ExactMolWt",         lambda m: Descriptors.ExactMolWt(m)),
    ("MolLogP",            lambda m: Crippen.MolLogP(m)),
    ("MolMR",              lambda m: Crippen.MolMR(m)),
    ("TPSA",               lambda m: Descriptors.TPSA(m)),
    ("NumHAcceptors",      lambda m: Lipinski.NumHAcceptors(m)),
    ("NumHDonors",         lambda m: Lipinski.NumHDonors(m)),
    ("NumRotatableBonds",  lambda m: Lipinski.NumRotatableBonds(m)),
    ("NumHeteroatoms",     lambda m: Lipinski.NumHeteroatoms(m)),
    ("RingCount",          lambda m: Lipinski.RingCount(m)),
    ("NumAromaticRings",   lambda m: Lipinski.NumAromaticRings(m)),
    ("NumAliphaticRings",  lambda m: Lipinski.NumAliphaticRings(m)),
    ("NumSaturatedRings",  lambda m: Lipinski.NumSaturatedRings(m)),
    ("FractionCsp3",       lambda m: Lipinski.FractionCsp3(m)),
    ("HeavyAtomCount",     lambda m: Descriptors.HeavyAtomCount(m)),
    ("NumValenceElectrons",lambda m: Descriptors.NumValenceElectrons(m)),
    ("MaxPartialCharge",   lambda m: Descriptors.MaxPartialCharge(m)),
    ("MinPartialCharge",   lambda m: Descriptors.MinPartialCharge(m)),
    ("MaxAbsPartialCharge",lambda m: Descriptors.MaxAbsPartialCharge(m)),
    ("MinAbsPartialCharge",lambda m: Descriptors.MinAbsPartialCharge(m)),
    ("BalabanJ",           lambda m: GraphDescriptors.BalabanJ(m)),
    ("BertzCT",            lambda m: GraphDescriptors.BertzCT(m)),
    ("NumSpiroAtoms",      lambda m: Descriptors.NumSpiroAtoms(m)),
    ("NumBridgeheadAtoms", lambda m: Descriptors.NumBridgeheadAtoms(m)),
    ("LabuteASA",          lambda m: Descriptors.LabuteASA(m)),
]

CONTRACT = SolverContract(
    name="molecular_descriptors",
    capability="F15_cheminformatics_descriptors",
    description=(
        "Compute 25+ standard RDKit molecular descriptors from a SMILES column. "
        "Invalid SMILES produce NaN rows. Original non-SMILES columns "
        "(label/activity/id/metadata) are passed through to the output so a "
        "downstream classifier/regressor can still find its target. "
        "Output: descriptors.csv (id + passthrough columns + descriptor columns), "
        "descriptor_stats.json."
    ),
    roles={
        "id_col": RoleSpec(
            Role.ID,
            "Row identifier column. Optional — auto-generated 0..N-1 if absent.",
            optional=True,
        ),
        "smiles_col": RoleSpec(
            Role.TEXT,
            "Column containing SMILES strings",
        ),
    },
    static_params={},
    output_files={
        "descriptors_csv": "descriptors.csv",
        "stats_json": "descriptor_stats.json",
    },
    output_kind={"descriptors_csv": "t", "stats_json": "s"},
)

class MolecularDescriptorsSolver:
    contract = CONTRACT

    def __init__(self):
        if not _RDKIT_OK:
            raise ImportError("rdkit is required for molecular_descriptors")

    def run(self, df, mapping, output_dir):
        id_col = mapping.get("id_col")
        smiles_col = mapping.get("smiles_col")
        if not smiles_col:
            raise OperatorInputError(
                "MISSING_REQUIRED_COLUMNS",
                solver="molecular_descriptors",
                hint="smiles_col is required",
            )
        if id_col and id_col in df.columns:
            ids = df[id_col].astype(str).tolist()
            id_name = id_col
        else:
            ids = [str(i) for i in range(len(df))]
            id_name = "row_id"

        smiles_list = df[smiles_col].astype(str).tolist()

        desc_names = [name for name, _func in _DESCRIPTOR_REGISTRY]
        desc_funcs = [func for _name, func in _DESCRIPTOR_REGISTRY]
        n_desc = len(desc_names)

        desc_matrix = np.full((len(smiles_list), n_desc), np.nan, dtype=np.float64)
        valid_mask = np.zeros(len(smiles_list), dtype=bool)

        for i, smi in enumerate(smiles_list):
            mol = Chem.MolFromSmiles(smi.strip())
            if mol is None:
                continue
            valid_mask[i] = True
            for j, func in enumerate(desc_funcs):
                try:
                    desc_matrix[i, j] = func(mol)
                except Exception:
                    pass

        n_valid = int(valid_mask.sum())
        n_invalid = len(smiles_list) - n_valid

        desc_df = pd.DataFrame(desc_matrix, columns=desc_names, dtype=np.float64)

        # Pass through the original non-SMILES columns (label / activity /
        # id / metadata) so downstream ML operators (logistic_regression,
        # random_forest, ...) can still find the target column.  Without
        # this the descriptors.csv would only have id + descriptors and the
        # target column would be lost.  Drop the SMILES column itself and
        # any original column that collides with a descriptor name.
        passthrough = (df.drop(columns=[smiles_col], errors="ignore")
                         .reset_index(drop=True))
        passthrough = passthrough[[c for c in passthrough.columns
                                    if c not in desc_names]]
        out_df = pd.concat([passthrough, desc_df.reset_index(drop=True)], axis=1)
        # Guarantee an id column exists at the front.
        if id_name not in out_df.columns:
            out_df.insert(0, id_name, ids)

        desc_csv = output_dir / "descriptors.csv"
        out_df.to_csv(desc_csv, index=False)

        import json
        stats = {
            "n_total": len(smiles_list), "n_valid": n_valid,
            "n_invalid": n_invalid, "n_descriptors": n_desc,
            "descriptor_names": desc_names,
        }
        stats_path = output_dir / "descriptor_stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

        return {
            "descriptors_csv": str(desc_csv),
            "stats_json": str(stats_path),
            "n_valid": n_valid, "n_invalid": n_invalid,
        }

def get_solver():
    return MolecularDescriptorsSolver()
