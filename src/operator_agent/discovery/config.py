# -*- coding: utf-8 -*-
"""Discovery framework — configuration constants (ADR-0001 / 0006 / 0009).

Single place for tunable thresholds + hard-coded defaults.  Every
constant is overridable via the listed environment variable so
deployments can tune without code changes.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    # Run cleanup (ADR-0001)
    "RUNS_CLEANUP_AGE_DAYS",
    "RUNS_CLEANUP_KEEP_LAST",
    "FINDINGS_ARCHIVE_AGE_DAYS",
    "findings_archive_root",
    # CSV loading (ADR-0006)
    "MAX_CSV_BYTES",
    "ENCODING_FALLBACK_CHAIN",
    "NA_TOKENS",
    # Mid-run clarify (ADR-0003 / 0004)
    "CLARIFY_DEFAULT_TIMEOUT_S",
    "N2_CLEANING_TIMEOUT_S",
    # Webapp (ADR-0009)
    "WEBAPP_POLL_HZ",
    "WEBAPP_SESSION_EVICT_AFTER_S",
    "WEBAPP_AUTO_CANCEL_WAIT_S",
]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Run-directory cleanup (ADR-0001)
# ---------------------------------------------------------------------------
#: Delete a ``runs/discovery/<run_id>/`` tree if it is older than this
#: many days AND we're already keeping the ``RUNS_CLEANUP_KEEP_LAST``
#: most-recent runs untouched.  Triggered lazily at supervisor startup.
RUNS_CLEANUP_AGE_DAYS = _int_env("DISCOVERY_RUNS_AGE_DAYS", 7)

#: Always keep at least this many of the most recent runs, even if they
#: exceed the age threshold.  Protects against losing all runs on a
#: long-idle deployment.
RUNS_CLEANUP_KEEP_LAST = _int_env("DISCOVERY_RUNS_KEEP_LAST", 50)

#: Findings-archive retention.  ``findings.yaml`` files copied into the
#: archive are deleted after this many days.  Long because the archive is
#: the long-term scientific record.
FINDINGS_ARCHIVE_AGE_DAYS = _int_env("DISCOVERY_FINDINGS_AGE_DAYS", 365)


def findings_archive_root() -> Path:
    """Where to copy ``findings.yaml`` to for long retention.

    Defaults to ``<repo_root>/findings_archive/``; override via
    ``DISCOVERY_FINDINGS_ARCHIVE`` env var.  Created on first write.
    """
    override = os.environ.get("DISCOVERY_FINDINGS_ARCHIVE")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "findings_archive"


# ---------------------------------------------------------------------------
# CSV loading (ADR-0006)
# ---------------------------------------------------------------------------
#: Hard upper bound on input file size.  Files above this are rejected
#: with :class:`~discovery.errors.DataLoadError`.  500 MB matches the
#: ADR-0006 decision (Q6.2).
MAX_CSV_BYTES = _int_env("DISCOVERY_MAX_CSV_BYTES", 500 * 1024 * 1024)

#: Encoding fallback chain (ADR-0006 §1).  ``charset-normalizer`` is
#: tried first; on low confidence we walk this list in order.  ``latin-1``
#: is last because it never raises but may produce garbage.
ENCODING_FALLBACK_CHAIN = (
    "utf-8-sig",
    "utf-8",
    "gbk",
    "latin-1",
)

#: Strings ``read_csv`` should treat as NaN, beyond pandas' default set
#: (ADR-0006 §4).  We ALSO keep pandas' default NA values
#: (``keep_default_na=True``) — these are additions, not replacements.
NA_TOKENS = (
    "",
    "NA", "N/A", "n/a",
    "null", "NULL", "Null",
    "None", "none",
    "NaN", "nan",
    "Inf", "-Inf", "inf", "-inf",
    ".",
)


# ---------------------------------------------------------------------------
# Mid-run clarify (ADR-0003 / 0004)
# ---------------------------------------------------------------------------
#: Default timeout for any mid-run clarify question whose call site
#: doesn't pass an explicit timeout.  10 seconds is the
#: webapp-friendly hybrid from ADR-0003 §"Mode-dependent waiting".
CLARIFY_DEFAULT_TIMEOUT_S = _float_env("DISCOVERY_CLARIFY_TIMEOUT_S", 10.0)

#: Specific timeout for the N2 cleaning approval clarify (ADR-0004).
#: Currently same as ``CLARIFY_DEFAULT_TIMEOUT_S`` but kept as a separate
#: knob so cleaning approval can be tuned without affecting other clarify
#: call sites.
N2_CLEANING_TIMEOUT_S = _float_env("DISCOVERY_N2_TIMEOUT_S", 10.0)


# ---------------------------------------------------------------------------
# Webapp ↔ TopAgent (ADR-0009)
# ---------------------------------------------------------------------------
#: Frontend polling rate for ``/run/<id>/status`` and
#: ``/run/<id>/clarifications`` (ADR-0009 §1, Q9.3).  1 Hz keeps
#: clarify latency well under the 10s window.
WEBAPP_POLL_HZ = _float_env("DISCOVERY_WEBAPP_POLL_HZ", 1.0)

#: How long after a Session reaches a terminal state we keep it in the
#: in-process registry (ADR-0009 §2).  1 hour gives users plenty of time
#: to read final results before the entry is evicted.
WEBAPP_SESSION_EVICT_AFTER_S = _float_env(
    "DISCOVERY_WEBAPP_SESSION_EVICT_S", 3600.0)

#: When the user starts a new run while one is already in-flight, how
#: long to wait for the previous run to terminate after we sent it a
#: cancel (ADR-0009 §4).  5 seconds matches "current stage should
#: observe cancel within seconds" expectation.
WEBAPP_AUTO_CANCEL_WAIT_S = _float_env(
    "DISCOVERY_WEBAPP_AUTO_CANCEL_WAIT_S", 5.0)
