#!/usr/bin/env python3
"""由 mixed-types.csv 派生 .tsv / .xlsx / .xls，供 upload/reader 测试引用。

.xls 优先用 xlwt 写出；若无 xlwt，则经 LibreOffice 从 .xlsx 转换。
日常 pytest 不依赖本脚本。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "tests" / "fixtures" / "table" / "mixed-types.csv"
DEFAULT_OUT_DIR = ROOT / "tests" / "fixtures" / "table"


def _read_source(src: Path) -> pd.DataFrame:
    # 与 Reader 一致：不跳过首行注释，整表原样保留
    return pd.read_csv(src, header=None, dtype=str, keep_default_na=False)


def write_tsv(df: pd.DataFrame, out: Path) -> None:
    df.to_csv(out, sep="\t", index=False, header=False)


def write_xlsx(df: pd.DataFrame, out: Path) -> None:
    df.to_excel(out, index=False, header=False, engine="openpyxl")


def write_xls(df: pd.DataFrame, xlsx_path: Path, out: Path) -> None:
    try:
        df.to_excel(out, index=False, header=False, engine="xlwt")
        return
    except Exception:
        pass

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("无法写出 .xls：需要 xlwt 或 LibreOffice (soffice)")

    with tempfile.TemporaryDirectory(prefix="xls-convert-") as tmp:
        tmp_dir = Path(tmp)
        # LibreOffice 从工作副本转换，避免污染源目录
        work_xlsx = tmp_dir / xlsx_path.name
        shutil.copy2(xlsx_path, work_xlsx)
        proc = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "xls",
                "--outdir",
                str(tmp_dir),
                str(work_xlsx),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        converted = tmp_dir / (work_xlsx.stem + ".xls")
        if proc.returncode != 0 or not converted.is_file():
            raise RuntimeError(
                f"LibreOffice 转换失败 (code={proc.returncode}): "
                f"{proc.stderr or proc.stdout or 'no output'}"
            )
        shutil.copy2(converted, out)


def generate(src: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = _read_source(src)
    stem = src.stem
    tsv_path = out_dir / f"{stem}.tsv"
    xlsx_path = out_dir / f"{stem}.xlsx"
    xls_path = out_dir / f"{stem}.xls"

    write_tsv(df, tsv_path)
    write_xlsx(df, xlsx_path)
    write_xls(df, xlsx_path, xls_path)
    return [tsv_path, xlsx_path, xls_path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    if not args.src.is_file():
        raise SystemExit(f"source not found: {args.src}")
    paths = generate(args.src, args.out_dir)
    for p in paths:
        print(f"wrote {p} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
