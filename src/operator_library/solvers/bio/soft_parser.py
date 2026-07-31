"""Parse a GEO SOFT/GDS file into 3 tidy CSVs (no GEOquery / GEOparse).

A SOFT file is a line-oriented text format::

    ^DATABASE = Geo
    !Database_name = ...
    ^DATASET = GDS6016
    !dataset_title = ...
    ^SUBSET = GDS6016_1
    !subset_description = En2 wildtype
    !subset_sample_id = GSM1249165,GSM1249166,GSM1249167
    !subset_type = genotype/variation
    ^SUBSET = GDS6016_2
    !subset_description = En2 knockout
    !subset_sample_id = GSM1249168,GSM1249169,GSM1249170
    ...
    !dataset_table_begin
    ID_REF\tIDENTIFIER\tGSM1249165\tGSM1249166\t...
    A_51_P100021\tHivep3\tnull\tnull\t...
    ...
    !dataset_table_end

The parser scans entities, extracts subset → sample → group mapping, and
streams the data table into 3 outputs:

  - ``expression_matrix.csv`` (rows = ID_REF, columns = sample GSM ids,
    one numeric value per cell; non-numeric / "null" → NaN)
  - ``sample_groups.csv`` (sample_id, group, group_description)
  - ``annotation.csv`` (ID_REF + every annotation column from the table,
    excluding the per-sample value columns)

中文说明
========
纯 Python 流式扫 SOFT：从 ``^SUBSET`` 块拆样本与分组，从 ``!Dataset_table``
抽探针×样本表达矩阵，并分离出非样本列作为 ``annotation.csv``。无需 R/GEOquery。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="gds_soft_parser",
    capability="F01_data_governance_cleaning",
    description=(
        "Parse a NCBI GEO SOFT (.soft / .gds) file into three tidy CSVs: "
        "expression_matrix.csv (probe x sample), sample_groups.csv "
        "(sample_id + group), annotation.csv (per-probe gene metadata). "
        "Pure-Python, no GEOquery."
    ),
    roles={
        "soft_path": RoleSpec(
            Role.PARAMS,
            "Absolute or relative path to the .soft / .gds file.  "
            "Required.",
        ),
    },
    static_params={"soft_path": None},
    output_files={
        "expression_matrix_csv": "expression_matrix.csv",
        "sample_groups_csv":     "sample_groups.csv",
        "annotation_csv":        "annotation.csv",
    },
    output_kind={
        "expression_matrix_csv": "t",
        "sample_groups_csv":     "t",
        "annotation_csv":        "s",
    },
)


_GSM_RE = re.compile(r"^GSM\d+$")


@dataclass
class _ParseState:
    subsets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    current_subset: Optional[str] = None
    in_table: bool = False
    header: Optional[List[str]] = None


def _parse_header(path: Path) -> Tuple[Dict[str, Dict[str, Any]],
                                         List[str], int]:
    """First pass: scan until ``!dataset_table_begin`` to learn subsets +
    column header.  Returns (subsets, header, header_line_index).
    """
    st = _ParseState()
    header_line_index = -1
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            line = line.rstrip("\r\n")
            if line.startswith("!dataset_table_begin"):
                # next non-empty line is the header
                for j, h in enumerate(f, start=i + 1):
                    h = h.rstrip("\r\n")
                    if not h:
                        continue
                    st.header = h.split("\t")
                    header_line_index = j
                    break
                break
            if line.startswith("^SUBSET"):
                m = re.match(r"^\^SUBSET\s*=\s*(\S+)", line)
                if m:
                    sid = m.group(1)
                    st.current_subset = sid
                    st.subsets.setdefault(sid, {"id": sid})
            elif line.startswith("!subset_description") and st.current_subset:
                m = re.match(r"^!subset_description\s*=\s*(.+)", line)
                if m:
                    st.subsets[st.current_subset]["description"] = \
                        m.group(1).strip()
            elif line.startswith("!subset_sample_id") and st.current_subset:
                m = re.match(r"^!subset_sample_id\s*=\s*(.+)", line)
                if m:
                    st.subsets[st.current_subset]["sample_ids"] = [
                        s.strip() for s in m.group(1).split(",") if s.strip()
                    ]
            elif line.startswith("!subset_type") and st.current_subset:
                m = re.match(r"^!subset_type\s*=\s*(.+)", line)
                if m:
                    st.subsets[st.current_subset]["type"] = m.group(1).strip()
    if st.header is None:
        raise ValueError("SOFT file: !dataset_table_begin / header not found")
    return st.subsets, st.header, header_line_index


def _identify_columns(header: List[str]) -> Tuple[List[str], List[str]]:
    """Split the table header into (sample_columns, annotation_columns).

    Sample columns are those matching ``GSM\\d+``; everything else
    (ID_REF, IDENTIFIER, Gene title, ...) is annotation metadata.
    """
    sample_cols = [c for c in header if _GSM_RE.match(c)]
    annot_cols = [c for c in header if c not in sample_cols]
    return sample_cols, annot_cols


def _stream_table(path: Path, header_line_index: int,
                   header: List[str]) -> pd.DataFrame:
    """Second pass: read every line strictly between header and
    ``!dataset_table_end`` into a DataFrame, keeping all columns as
    strings (we'll cast numerics later)."""
    rows: List[List[str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        # skip up to header_line_index
        for i, line in enumerate(f):
            if i == header_line_index:
                break
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("!dataset_table_end"):
                break
            parts = line.split("\t")
            # pad / trim to header length
            if len(parts) < len(header):
                parts = parts + [""] * (len(header) - len(parts))
            elif len(parts) > len(header):
                parts = parts[:len(header)]
            rows.append(parts)
    df = pd.DataFrame(rows, columns=header)
    return df


def _to_numeric(series: pd.Series) -> pd.Series:
    """Convert a string column into floats.  Empty / 'null' / 'NA' →
    NaN."""
    s = series.replace({"": np.nan, "null": np.nan, "NULL": np.nan,
                          "NA": np.nan, "N/A": np.nan, "NaN": np.nan})
    return pd.to_numeric(s, errors="coerce")


class GDSSoftParserSolver:
    contract = CONTRACT

    def __init__(self, soft_path: Optional[str] = None):
        self.soft_path = soft_path

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
             output_dir: Path) -> Dict[str, Any]:
        soft_path = mapping.get("soft_path") or self.soft_path
        if not soft_path:
            raise ValueError(
                "gds_soft_parser: 'soft_path' is required (pass via "
                "mapping or constructor)")
        soft_path = Path(soft_path)
        if not soft_path.is_file():
            raise FileNotFoundError(soft_path)

        subsets, header, header_idx = _parse_header(soft_path)
        sample_cols, annot_cols = _identify_columns(header)
        if not sample_cols:
            raise ValueError(
                f"SOFT file: no GSM* sample columns found in header: "
                f"{header[:6]}…")

        # Build sample → group map from subsets
        sample_to_group: Dict[str, str] = {}
        sample_to_desc: Dict[str, str] = {}
        for sid, info in subsets.items():
            desc = info.get("description") or sid
            for sample in info.get("sample_ids", []):
                if sample in sample_to_group:
                    # collision: same sample in multiple subsets → keep first
                    continue
                sample_to_group[sample] = sid
                sample_to_desc[sample] = desc

        sg_rows = []
        for s in sample_cols:
            sg_rows.append({
                "sample_id":         s,
                "group":             sample_to_group.get(s, ""),
                "group_description": sample_to_desc.get(s, ""),
            })
        sg_df = pd.DataFrame(sg_rows)

        # Stream data table
        table = _stream_table(soft_path, header_idx, header)
        if table.empty:
            raise ValueError("SOFT file: data table is empty")
        if "ID_REF" not in table.columns:
            raise ValueError("SOFT file: ID_REF column missing")

        expr = table[["ID_REF"] + sample_cols].copy()
        for c in sample_cols:
            expr[c] = _to_numeric(expr[c])
        expr = expr.rename(columns={"ID_REF": "probe_id"})

        annot = table[annot_cols].copy()
        if "ID_REF" in annot.columns:
            annot = annot.rename(columns={"ID_REF": "probe_id"})

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        expr_path = out_dir / CONTRACT.output_files["expression_matrix_csv"]
        sg_path   = out_dir / CONTRACT.output_files["sample_groups_csv"]
        ann_path  = out_dir / CONTRACT.output_files["annotation_csv"]
        expr.to_csv(expr_path, index=False)
        sg_df.to_csv(sg_path, index=False)
        annot.to_csv(ann_path, index=False)

        return {
            "expression_matrix_csv": str(expr_path),
            "sample_groups_csv":     str(sg_path),
            "annotation_csv":        str(ann_path),
            "n_probes":              int(len(expr)),
            "n_samples":             int(len(sample_cols)),
            "n_groups":              int(sg_df["group"].nunique()),
            "annotation_columns":    list(annot.columns),
        }


def get_solver(soft_path: Optional[str] = None):
    return GDSSoftParserSolver(soft_path=soft_path)


def selftest():
    """Synthesise a 5-probe × 4-sample × 2-group SOFT file and re-parse."""
    import tempfile

    text = (
        "^DATABASE = Geo\n"
        "!Database_name = Gene Expression Omnibus\n"
        "^DATASET = GDS_TEST\n"
        "!dataset_title = synthetic\n"
        "^SUBSET = GDS_TEST_1\n"
        "!subset_description = control\n"
        "!subset_sample_id = GSM00001,GSM00002\n"
        "!subset_type = condition\n"
        "^SUBSET = GDS_TEST_2\n"
        "!subset_description = treated\n"
        "!subset_sample_id = GSM00003,GSM00004\n"
        "!subset_type = condition\n"
        "!dataset_table_begin\n"
        "ID_REF\tIDENTIFIER\tGSM00001\tGSM00002\tGSM00003\tGSM00004\tGene symbol\n"
        "p1\tg1\t1.0\t1.1\t5.0\t5.1\tGENE1\n"
        "p2\tg2\t2.0\t2.1\tnull\t2.2\tGENE2\n"
        "p3\tg3\tnull\tnull\tnull\tnull\tGENE3\n"
        "p4\tg4\t10\t11\t12\t13\tGENE4\n"
        "p5\tg5\t0.5\t0.6\t0.7\t0.8\t\n"
        "!dataset_table_end\n"
    )
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        soft_path = Path(tmp) / "synthetic.soft"
        soft_path.write_text(text, encoding="utf-8")
        out_dir = Path(tmp) / "out"
        out = get_solver(str(soft_path)).run(
            df=pd.DataFrame(),
            mapping=ColumnMapping({"soft_path": str(soft_path)}),
            output_dir=out_dir,
        )
        if out["n_probes"] != 5:
            diffs.append(f"n_probes expected 5, got {out['n_probes']}")
        if out["n_samples"] != 4:
            diffs.append(f"n_samples expected 4, got {out['n_samples']}")
        if out["n_groups"] != 2:
            diffs.append(f"n_groups expected 2, got {out['n_groups']}")

        sg = pd.read_csv(out["sample_groups_csv"])
        if sorted(sg["sample_id"].tolist()) != [
                "GSM00001", "GSM00002", "GSM00003", "GSM00004"]:
            diffs.append(f"sample ids: {sg['sample_id'].tolist()}")
        ctrl = sg[sg["sample_id"].isin(["GSM00001", "GSM00002"])]
        if ctrl["group_description"].nunique() != 1 or \
           ctrl["group_description"].iloc[0] != "control":
            diffs.append(f"control group_description wrong: "
                         f"{ctrl['group_description'].tolist()}")

        expr = pd.read_csv(out["expression_matrix_csv"]).set_index("probe_id")
        if expr.loc["p1", "GSM00003"] != 5.0:
            diffs.append(f"p1/GSM00003 expected 5.0, got "
                         f"{expr.loc['p1', 'GSM00003']!r}")
        if not pd.isna(expr.loc["p2", "GSM00003"]):
            diffs.append(f"p2/GSM00003 should be NaN (was 'null'), got "
                         f"{expr.loc['p2', 'GSM00003']!r}")
        if not expr.loc["p3"].isna().all():
            diffs.append(f"p3 should be all NaN, got {expr.loc['p3'].tolist()}")
        if expr.loc["p4", "GSM00004"] != 13.0:
            diffs.append(f"p4/GSM00004 expected 13.0, got "
                         f"{expr.loc['p4', 'GSM00004']!r}")

        annot = pd.read_csv(out["annotation_csv"]).set_index("probe_id")
        if annot.loc["p1", "Gene symbol"] != "GENE1":
            diffs.append(f"annotation gene symbol mismatch")

    return {"ok": len(diffs) == 0,
            "summary": ("synthetic SOFT round-trip ok"
                         if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs, "tested": ["gds_soft_parser"]}}
