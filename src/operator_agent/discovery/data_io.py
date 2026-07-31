# -*- coding: utf-8 -*-
"""Hardened CSV / TSV / Excel / SOFT loader (ADR-0006).

The previous loader was effectively ``pd.read_csv(path)`` with default
arguments, which produced four user-visible failures:

- non-UTF-8 files crashed with cryptic decode errors,
- multi-GB uploads exhausted memory,
- non-CSV uploads (TSV / Excel / GDS soft) crashed in the parser,
- pandas' default dtype inference silently dropped values on
  mixed-type columns.

All four bubbled up to the UI as ``supervisor_uncaught:`` traces,
indistinguishable from real crashes.

This module implements the ADR-0006 decision tree:

1. **Encoding** — sniff with ``charset-normalizer`` over the first
   64 KB; on low confidence walk the explicit fallback chain
   (``utf-8-sig`` → ``utf-8`` → ``gbk`` → ``latin-1``).
2. **Size** — hard-cap at :data:`MAX_CSV_BYTES` (500 MB by default);
   reject above the cap with :class:`DataLoadError`.
3. **Type** — dispatch by extension:
   ``.csv`` / ``.tsv`` / ``.txt`` → ``pd.read_csv`` (correct sep);
   ``.xlsx`` / ``.xls`` → ``pd.read_excel``;
   ``.soft`` / ``.soft.gz`` → ``bio.gds_soft_parser`` (parser-only mode).
4. **NA / dtype** — explicit ``na_values=NA_TOKENS`` +
   ``low_memory=False``.  No post-load ``to_numeric(coerce)`` rescue
   (silently drops genuine string values).
5. **Errors** — every failure mode raises :class:`DataLoadError`.
   Callers (webapp, CLI, SDK) catch this **before** TopAgent / Supervisor
   start, so it never becomes a ``supervisor_uncaught:`` trace.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import pandas as pd

from .config import (
    ENCODING_FALLBACK_CHAIN,
    MAX_CSV_BYTES,
    NA_TOKENS,
)
from .errors import DataLoadError

__all__ = [
    "DataLoadInfo",
    "load_dataset",
]

PathLike = Union[str, Path]


@dataclasses.dataclass
class DataLoadInfo:
    """What :func:`load_dataset` actually did, for diagnostics + provenance.

    Attributes
    ----------
    path
        Resolved absolute path.
    file_type
        One of ``"csv"``, ``"tsv"``, ``"excel"``, ``"soft"``.  This is
        the dispatch decision the loader took, NOT the file extension
        verbatim.
    encoding
        Encoding actually used to decode (``None`` for binary formats
        like Excel that don't have a text encoding).
    encoding_source
        How the encoding was chosen: ``"sniff"`` (charset-normalizer
        succeeded), ``"fallback:<encoding>"`` (chain walk), or
        ``"binary"`` (Excel).
    size_bytes
        File size in bytes at load time.
    nrows / ncols
        Shape of the resulting DataFrame.
    na_token_hits
        Per-column count of values matched against :data:`NA_TOKENS`,
        for the data profile to surface "this column was 30% NA / null".
    """
    path: Path
    file_type: str
    encoding: Optional[str]
    encoding_source: str
    size_bytes: int
    nrows: int
    ncols: int
    na_token_hits: Dict[str, int] = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Extension dispatch
# ---------------------------------------------------------------------------
_CSV_EXTS = {".csv"}
_TSV_EXTS = {".tsv", ".tab"}
_TXT_EXTS = {".txt"}  # we treat .txt as TSV by default (most common)
_EXCEL_EXTS = {".xlsx", ".xls", ".xlsm"}
_SOFT_EXTS = {".soft"}


def _classify(path: Path) -> str:
    name_lower = path.name.lower()
    if name_lower.endswith(".soft.gz"):
        return "soft"
    suffix = path.suffix.lower()
    if suffix in _CSV_EXTS:
        return "csv"
    if suffix in _TSV_EXTS or suffix in _TXT_EXTS:
        return "tsv"
    if suffix in _EXCEL_EXTS:
        return "excel"
    if suffix in _SOFT_EXTS:
        return "soft"
    raise DataLoadError(
        f"unsupported file type: {path.name!r} (extension {suffix!r}); "
        "supported: .csv .tsv .txt .xlsx .xls .soft .soft.gz")


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------
def _sniff_encoding(path: Path,
                    sample_bytes: int = 64 * 1024
                    ) -> Tuple[Optional[str], float]:
    """Return ``(best_encoding, confidence)`` from charset-normalizer.

    Confidence is in ``[0, 1]``.  Returns ``(None, 0.0)`` if the library
    is unavailable (unlikely — we list it as a dep).
    """
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        try:
            import chardet  # second-choice fallback
            with path.open("rb") as fh:
                buf = fh.read(sample_bytes)
            res = chardet.detect(buf)
            enc = res.get("encoding")
            conf = float(res.get("confidence") or 0.0)
            return (enc, conf)
        except Exception:
            return (None, 0.0)

    try:
        with path.open("rb") as fh:
            buf = fh.read(sample_bytes)
        match = from_bytes(buf).best()
        if match is None:
            return (None, 0.0)
        # charset-normalizer's chaos is "lower = better"; convert to a
        # confidence-ish number in [0, 1].
        conf = max(0.0, 1.0 - float(getattr(match, "chaos", 1.0)))
        enc = str(match.encoding) if match.encoding else None
        return (enc, conf)
    except Exception:
        return (None, 0.0)


def _try_read(path: Path, file_type: str, encoding: Optional[str],
              **read_kwargs: Any) -> pd.DataFrame:
    """Attempt one read with one encoding; raises pandas errors verbatim."""
    if file_type == "csv":
        return pd.read_csv(path, encoding=encoding, sep=",", **read_kwargs)
    if file_type == "tsv":
        return pd.read_csv(path, encoding=encoding, sep="\t", **read_kwargs)
    raise AssertionError(f"unexpected file_type for text read: {file_type!r}")


def _read_text_with_encoding(path: Path, file_type: str,
                             read_kwargs: Dict[str, Any]
                             ) -> Tuple[pd.DataFrame, str, str]:
    """Read a text-format file with sniffed-then-fallback encoding.

    Returns ``(df, encoding_used, encoding_source)``.

    Tries the sniffed encoding first (if charset-normalizer is confident
    enough), then walks ``ENCODING_FALLBACK_CHAIN`` in order.  For each
    candidate we accept the decode iff the resulting DataFrame has at
    least one data row — a candidate that "decodes" to 0 rows is almost
    always a wrong-encoding decode (e.g. latin-1 on GBK bytes "succeeds"
    but produces garbage with no commas/newlines), so we keep trying
    further encodings.  We track the first non-empty result as a final
    fallback in case ALL candidates produce 0 rows.
    """
    sniffed, conf = _sniff_encoding(path)
    tried: List[str] = []
    candidates: List[str] = []
    if sniffed:
        candidates.append(sniffed)
    for enc in ENCODING_FALLBACK_CHAIN:
        if enc not in candidates:
            candidates.append(enc)

    first_nonempty: Optional[Tuple[pd.DataFrame, str, str]] = None
    last_decoded: Optional[Tuple[pd.DataFrame, str, str]] = None
    high_conf_sniff = (sniffed is not None and conf >= 0.5)

    for idx, enc in enumerate(candidates):
        try:
            df = _try_read(path, file_type, enc, **read_kwargs)
        except (UnicodeDecodeError, UnicodeError) as exc:
            tried.append(f"{enc}:{type(exc).__name__}")
            continue
        except Exception as exc:
            # Non-encoding parser failures: bubble up immediately.  The
            # fallback chain is for encoding mismatches, not for
            # malformed CSV.
            raise DataLoadError(
                f"failed to parse {path.name!r} with encoding {enc!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        source = ("sniff" if (idx == 0 and high_conf_sniff)
                  else f"fallback:{enc}")
        last_decoded = (df, enc, source)

        # Empty-result heuristic: a wrong encoding can decode without
        # raising but produce 0 rows.  Prefer a non-empty decode.
        if df.shape[0] >= 1:
            return (df, enc, source)
        if first_nonempty is None:
            tried.append(f"{enc}:0_rows")

    if first_nonempty is not None:
        return first_nonempty
    if last_decoded is not None:
        # Every encoding decoded to 0 rows.  Return the last one — the
        # caller's empty-df check (see ``load_dataset``) will turn this
        # into a clear DataLoadError, with the encoding chain visible
        # in the trace.
        return last_decoded
    raise DataLoadError(
        f"could not decode {path.name!r}; tried sniff + fallback chain "
        f"({', '.join(tried) or 'none'})"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def load_dataset(path: PathLike,
                 *,
                 max_bytes: Optional[int] = None,
                 na_values: Optional[Iterable[str]] = None,
                 ) -> Tuple[pd.DataFrame, DataLoadInfo]:
    """Load a dataset from disk, hardened per ADR-0006.

    Parameters
    ----------
    path
        File to load.  Must exist; extension drives dispatch.
    max_bytes
        Override :data:`~discovery.config.MAX_CSV_BYTES` for this call
        (e.g. tests pass a tiny limit to exercise the cap).
    na_values
        Override :data:`~discovery.config.NA_TOKENS` (rare; usually
        callers want the default token set).

    Returns
    -------
    Tuple of ``(DataFrame, DataLoadInfo)``.

    Raises
    ------
    DataLoadError
        On every shape problem with the input file: missing path, size
        cap exceeded, unsupported extension, encoding detection failure,
        parser failure.  The message identifies which check tripped so
        the UI can render a helpful explanation.
    """
    p = Path(path).resolve()
    if not p.is_file():
        raise DataLoadError(f"file not found: {p!s}")

    cap = MAX_CSV_BYTES if max_bytes is None else int(max_bytes)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise DataLoadError(f"cannot stat file {p.name!r}: {exc}") from exc
    if size > cap:
        raise DataLoadError(
            f"file too large: {p.name!r} is {size:,} bytes; cap is "
            f"{cap:,} bytes ({cap // (1024 * 1024)} MB).  Pre-process "
            "or split the file before uploading.")

    file_type = _classify(p)
    na_tokens = list(na_values) if na_values is not None else list(NA_TOKENS)
    read_kwargs: Dict[str, Any] = {
        "na_values": na_tokens,
        "keep_default_na": True,
        "low_memory": False,
    }

    if file_type in ("csv", "tsv"):
        df, enc, src = _read_text_with_encoding(p, file_type, read_kwargs)
    elif file_type == "excel":
        try:
            df = pd.read_excel(p, na_values=na_tokens, keep_default_na=True)
        except ImportError as exc:
            raise DataLoadError(
                "Excel support requires the 'openpyxl' package; "
                f"install it or save the file as CSV.  ({exc})") from exc
        except Exception as exc:
            raise DataLoadError(
                f"failed to read Excel file {p.name!r}: "
                f"{type(exc).__name__}: {exc}") from exc
        enc, src = (None, "binary")
    elif file_type == "soft":
        # GEO SOFT files don't fit a single-dataframe view (they produce
        # expression_matrix + sample_groups + annotation as three separate
        # tables).  The bio.gds_soft_parser solver knows how to expand
        # them inside the operator pipeline.  For the discovery loader,
        # ask the user to upload one of the parsed CSVs instead — this
        # keeps the loader's "one file in, one DataFrame out" contract
        # honest and avoids guessing which of the three tables the user
        # wanted to analyse.
        raise DataLoadError(
            f"SOFT files are not loaded directly: pre-process {p.name!r} "
            "with the gds_soft_parser solver and upload one of "
            "expression_matrix.csv / sample_groups.csv / annotation.csv "
            "(or a downstream deg_table.csv).")
    else:  # pragma: no cover — _classify already rejected unknowns
        raise DataLoadError(f"unsupported file type: {file_type!r}")

    if df is None or df.shape[0] == 0:
        raise DataLoadError(
            f"file {p.name!r} produced an empty dataframe (0 rows)")

    info = DataLoadInfo(
        path=p,
        file_type=file_type,
        encoding=enc,
        encoding_source=src,
        size_bytes=size,
        nrows=int(df.shape[0]),
        ncols=int(df.shape[1]),
        na_token_hits=_count_na_per_column(df),
    )
    return (df, info)


def _count_na_per_column(df: pd.DataFrame) -> Dict[str, int]:
    try:
        return {str(col): int(df[col].isna().sum()) for col in df.columns}
    except Exception:
        return {}
