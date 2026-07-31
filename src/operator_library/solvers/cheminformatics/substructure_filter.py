"""Substructure filter for drug-like compound screening.

PAINS, Brenk, and custom SMARTS-based substructure filtering.  Removes
compounds with undesirable substructure alerts.  Backed by rdkit FilterCatalog.

Output: clean.csv (passed compounds), flagged.csv (removed + reasons),
filter_stats.json.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError

try:
    from rdkit import Chem
    from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams
    from rdkit import RDLogger
    RDLogger.logger().setLevel(RDLogger.ERROR)
    _RDKIT_OK = True
except ImportError:
    _RDKIT_OK = False

CONTRACT = SolverContract(
    name="substructure_filter",
    capability="F15_cheminformatics_filter",
    description=(
        "Remove compounds with PAINS/Brenk undesirable substructure alerts. "
        "Supported filter_sets: pains, brenk, pains_brenk (default). "
        "Output: clean.csv (passed), flagged.csv (removed + match_reason), "
        "filter_stats.json."
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
        "filter_sets": RoleSpec(
            Role.PARAMS,
            "Comma-separated filter names: pains, brenk, pains_brenk. Default: pains_brenk.",
            optional=True,
        ),
    },
    static_params={"filter_sets": "pains_brenk"},
    output_files={
        "clean_csv": "clean.csv",
        "flagged_csv": "flagged.csv",
        "stats_json": "filter_stats.json",
    },
    output_kind={"clean_csv": "t", "flagged_csv": "s", "stats_json": "s"},
)


class SubstructureFilterSolver:
    contract = CONTRACT

    def __init__(self, filter_sets: str = "pains_brenk"):
        if not _RDKIT_OK:
            raise ImportError("rdkit is required for substructure_filter")
        self.filter_sets = filter_sets

    def run(self, df, mapping, output_dir):
        id_col = mapping.get("id_col")
        smiles_col = mapping.get("smiles_col")
        filter_sets = mapping.get("filter_sets") or self.filter_sets
        if not smiles_col:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="substructure_filter",
                                     hint="smiles_col is required")
        if id_col and id_col in df.columns:
            ids = df[id_col].astype(str).tolist()
            id_name = id_col
        else:
            ids = [str(i) for i in range(len(df))]
            id_name = "row_id"

        params = FilterCatalogParams()
        sets = [s.strip().lower() for s in str(filter_sets).split(",")]
        if "pains" in sets or "pains_brenk" in sets:
            params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
        if "brenk" in sets or "pains_brenk" in sets:
            params.AddCatalog(FilterCatalogParams.FilterCatalogs.BRENK)
        catalog = FilterCatalog(params)

        smiles_list = df[smiles_col].astype(str).tolist()

        clean_rows, flagged_rows = [], []
        for i, smi in enumerate(smiles_list):
            smi = smi.strip()
            mol = Chem.MolFromSmiles(smi)
            if mol is None:
                flagged_rows.append({
                    id_name: ids[i], smiles_col: smi,
                    "match_reason": "invalid_smiles",
                })
                continue
            entry = catalog.GetFirstMatch(mol)
            if entry is None:
                row = df.iloc[i].to_dict()
                if id_name not in row:
                    row[id_name] = ids[i]
                clean_rows.append(row)
            else:
                reason = (
                    entry.GetDescription()
                    if hasattr(entry, "GetDescription") else "substructure_alert"
                )
                flagged_rows.append({
                    id_name: ids[i], smiles_col: smi,
                    "match_reason": str(reason),
                })

        clean_df = pd.DataFrame(clean_rows)
        flagged_df = pd.DataFrame(flagged_rows)

        n_total = len(smiles_list)
        n_clean = len(clean_rows)
        n_flagged = len(flagged_rows)

        clean_csv = output_dir / "clean.csv"
        clean_df.to_csv(clean_csv, index=False)

        flagged_csv = output_dir / "flagged.csv"
        flagged_df.to_csv(flagged_csv, index=False)

        import json
        stats = {
            "filter_sets": filter_sets, "n_total": n_total,
            "n_clean": n_clean, "n_flagged": n_flagged,
        }
        stats_path = output_dir / "filter_stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

        return {
            "clean_csv": str(clean_csv),
            "flagged_csv": str(flagged_csv),
            "stats_json": str(stats_path),
            "n_clean": n_clean, "n_flagged": n_flagged,
        }


def get_solver(filter_sets: str = "pains_brenk"):
    return SubstructureFilterSolver(filter_sets=filter_sets)
