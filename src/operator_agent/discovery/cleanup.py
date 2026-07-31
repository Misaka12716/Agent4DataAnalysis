# -*- coding: utf-8 -*-
"""Lazy run-directory cleanup + findings-archive management (ADR-0001).

Two storage tiers in the discovery framework:

- **Short-term run scratch** — ``runs/discovery/<run_id>/`` carries the
  full lane state (public_bb, private_bbs, intermediate artifacts).
  Allowed to grow during a session but pruned aggressively.

- **Long-term findings archive** — ``findings_archive/`` keeps copies of
  ``findings.yaml`` indexed by run_id.  Long retention because it's the
  scientific record; the rest of the run scratch is reproducible from
  the dataset + the cleaning_applied snippets recorded in findings.

Cleanup runs lazily at supervisor startup (called from
:meth:`Supervisor.run` BEFORE ``ensure_run_dir``).  Failures are logged
but never block the new run.
"""
from __future__ import annotations

import datetime as dt
import shutil
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .config import (
    FINDINGS_ARCHIVE_AGE_DAYS,
    RUNS_CLEANUP_AGE_DAYS,
    RUNS_CLEANUP_KEEP_LAST,
    findings_archive_root,
)
from .paths import DEFAULT_RUNS_ROOT

__all__ = [
    "cleanup_old_runs",
    "cleanup_old_archive",
    "archive_findings",
    "ensure_archive_root",
]


# ---------------------------------------------------------------------------
# Run scratch cleanup
# ---------------------------------------------------------------------------
def _list_run_dirs(runs_root: Path) -> List[Tuple[Path, float]]:
    if not runs_root.is_dir():
        return []
    out: List[Tuple[Path, float]] = []
    for child in runs_root.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        out.append((child, mtime))
    return out


def cleanup_old_runs(*,
                     runs_root: Optional[Path] = None,
                     age_days: Optional[int] = None,
                     keep_last: Optional[int] = None,
                     ) -> List[str]:
    """Delete old ``runs/discovery/<run_id>/`` directories.

    A directory is deleted iff it is **older than** ``age_days`` AND we
    are already keeping at least ``keep_last`` newer directories.  This
    protects against losing all runs on a long-idle deployment (the
    most-recent ``keep_last`` are always kept regardless of age).

    Returns the list of run_ids that were deleted.  Errors during
    individual deletes are swallowed (best-effort cleanup) but the
    failed run_id is included in the returned list with a leading
    ``"!"`` prefix so callers can log them.
    """
    runs_root = Path(runs_root) if runs_root is not None else DEFAULT_RUNS_ROOT
    age_days = age_days if age_days is not None else RUNS_CLEANUP_AGE_DAYS
    keep_last = keep_last if keep_last is not None else RUNS_CLEANUP_KEEP_LAST
    if age_days <= 0:
        return []

    entries = _list_run_dirs(runs_root)
    if not entries:
        return []

    # Sort newest first; keep_last protects the head of the list.
    entries.sort(key=lambda e: e[1], reverse=True)
    if len(entries) <= keep_last:
        return []  # not enough runs to bother cleaning

    cutoff = time.time() - (age_days * 86400.0)
    candidates = entries[keep_last:]  # everything past keep_last
    deleted: List[str] = []
    for path, mtime in candidates:
        if mtime >= cutoff:
            continue
        try:
            shutil.rmtree(path)
            deleted.append(path.name)
        except Exception as exc:  # pragma: no cover — best-effort
            deleted.append(f"!{path.name}:{type(exc).__name__}")
    return deleted


# ---------------------------------------------------------------------------
# Findings archive
# ---------------------------------------------------------------------------
def ensure_archive_root() -> Path:
    """Create + return the findings-archive directory (idempotent)."""
    root = findings_archive_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def archive_findings(run_id: str, findings_path: Path) -> Optional[Path]:
    """Copy a run's ``findings.yaml`` into the long-retention archive.

    File is named ``<YYYYMMDD>_<run_id>.yaml`` so the date prefix sorts
    chronologically.  Returns the destination path on success, ``None``
    on failure (best-effort — the run already succeeded by the time we
    archive, so we never raise).
    """
    if findings_path is None:
        return None
    try:
        src = Path(findings_path)
        if not src.is_file():
            return None
        root = ensure_archive_root()
        date = dt.datetime.now().strftime("%Y%m%d")
        dst = root / f"{date}_{run_id}.yaml"
        shutil.copy2(src, dst)
        return dst
    except Exception:
        return None


def cleanup_old_archive(*,
                        archive_root: Optional[Path] = None,
                        age_days: Optional[int] = None,
                        ) -> List[str]:
    """Delete archive entries older than ``age_days`` (default 365).

    Returns the list of filenames deleted.
    """
    archive_root = (Path(archive_root) if archive_root is not None
                    else findings_archive_root())
    age_days = (age_days if age_days is not None
                else FINDINGS_ARCHIVE_AGE_DAYS)
    if age_days <= 0 or not archive_root.is_dir():
        return []
    cutoff = time.time() - (age_days * 86400.0)
    deleted: List[str] = []
    for child in archive_root.iterdir():
        if not child.is_file():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        try:
            child.unlink()
            deleted.append(child.name)
        except Exception as exc:  # pragma: no cover
            deleted.append(f"!{child.name}:{type(exc).__name__}")
    return deleted
