# -*- coding: utf-8 -*-
"""Discovery framework — exception hierarchy + RunStatus enum (ADR-0007).

Three base classes carve up the failure space:

* :class:`UserActionError` — the user explicitly caused termination
  (cancel button, rejection of a cleaning preview, clarify timeout
  with no default).  Run status: ``cancelled``.  **Not an error**;
  the supervisor does NOT log a traceback for these.

* :class:`DataInputError` — the user's input is shape-wrong (CSV won't
  decode, file too large, file type not supported, dataset has no
  analysable column).  Run status: ``rejected_input``.  Also not an
  agent / system fault — surfaced to the user as "your input wasn't
  usable" rather than as a crash.

* :class:`SystemError` — agent / framework / operator failure (LLM
  transport exhausted, operator subprocess crash, real bug).  Run
  status: ``error``.  This is the **only** category that triggers
  `supervisor_uncaught:` wrapping and full traceback logging.

The supervisor catches by base class, not by concrete subclass — adding
a new subclass later does not require touching the supervisor.

This module is purely additive; the previous behaviour (every
exception → ``status="error"``) survives in the supervisor's
last-resort ``except Exception`` net.
"""
from __future__ import annotations

from enum import Enum

__all__ = [
    "RunStatus",
    "UserActionError",
    "UserCancelledError",
    "UserRejectedCleaningError",
    "UserTimeoutError",
    "DataInputError",
    "DataLoadError",
    "UnanalyzableDataError",
    "SystemError",
]


class RunStatus(str, Enum):
    """Terminal status of a Discovery Run (ADR-0007).

    Five values, distinct UI rendering per value:

    - ``ok``             — at least one finding produced.
    - ``empty``          — ran cleanly but produced no findings.
    - ``cancelled``      — :class:`UserActionError` raised mid-run.
    - ``rejected_input`` — :class:`DataInputError` raised before / early.
    - ``error``          — :class:`SystemError` or unclassified exception.
    """
    ok = "ok"
    empty = "empty"
    cancelled = "cancelled"
    rejected_input = "rejected_input"
    error = "error"


# ---------------------------------------------------------------------------
# Tier 1 — user-action errors  (status: cancelled)
# ---------------------------------------------------------------------------
class UserActionError(Exception):
    """Base class for any termination caused by an explicit user action.

    The supervisor catches this and returns
    ``DiscoveryResult(status="cancelled", reason=str(exc))``; no
    traceback is logged because the user's intent is documented by the
    exception itself.
    """


class UserCancelledError(UserActionError):
    """The user clicked cancel (cooperative cancel; ADR-0009)."""


class UserRejectedCleaningError(UserActionError):
    """The user rejected the N2 cleaning preview within the clarify
    window (ADR-0004).  Run continues only if downstream stages can
    operate on the raw dataframe; today the user-rejection path keeps
    the raw dataframe and the run continues — this class exists for
    future use cases that genuinely abort on rejection."""


class UserTimeoutError(UserActionError):
    """A clarify question timed out without an answer AND the call site
    declined the documented default (rare; reserved for future use).

    The standard timeout path (apply default after window) does NOT
    raise; this is for stages that explicitly opt out of defaults.
    """


# ---------------------------------------------------------------------------
# Tier 2 — data-input errors  (status: rejected_input)
# ---------------------------------------------------------------------------
class DataInputError(Exception):
    """Base class for any termination caused by user-input shape.

    The supervisor catches this and returns
    ``DiscoveryResult(status="rejected_input", reason=str(exc))``.
    Not logged as an agent fault.
    """


class DataLoadError(DataInputError):
    """The CSV / Excel / SOFT loader could not produce a dataframe
    (encoding fallback exhausted, size cap exceeded, unsupported
    extension, parser raised).  Defined in ADR-0006.
    """


class UnanalyzableDataError(DataInputError):
    """The dataframe loaded fine but N1 detected no analysable columns
    (e.g. all columns are free-text, or n_rows == 0).  Reserved for
    future N1 hardening; not raised by current code paths."""


# ---------------------------------------------------------------------------
# Tier 3 — system / agent errors  (status: error)
# ---------------------------------------------------------------------------
class SystemError(Exception):  # noqa: A001 - shadow of builtin is intentional
    """Base class for genuine agent / framework / operator failures.

    The supervisor wraps these (and any unclassified ``Exception``) into
    ``DiscoveryResult(status="error", error="supervisor_uncaught: ...")``
    and writes a full traceback to ``run.log``.

    This name shadows the built-in ``SystemError`` *within this module*;
    callers that import it can reference our class directly, and the
    builtin remains accessible via ``builtins.SystemError`` if needed.
    """
