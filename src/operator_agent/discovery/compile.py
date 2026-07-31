# -*- coding: utf-8 -*-
"""N7 aggregation → ``findings.yaml`` (V8 §F).

This is the compile step that turns a cohort's per-lane findings into a
single, reproducible ``findings.yaml`` document.  Each finding carries the
full §F field set:

- ``finding_id``          — ``F_C{cohort_id}_N{i}`` (i starting at 1).
- ``hypothesis``          — the full hypothesis card (``Hypothesis.to_dict``).
- ``statistical_evidence``— the VERBATIM numbers from the verify run
                            (``effect`` / ``effect_type`` / ``ci`` / ``p`` /
                            ``n``).
- ``review_result``       — the reviewer verdict (``ReviewResult.to_dict``).
- ``literature_context``  — ``None`` in this phase (N6 stub).
- ``reproducibility``     — checksums for one-click traceback
                            (``seed`` / ``dataset_hash`` /
                            ``operator_versions`` / ``artifact_paths``).

Number credibility (V8 §11): the statistical-evidence and reproducibility
blocks are copied straight from :class:`VerifyResult` (via
:meth:`FindingRecord.from_verify`), so the numbers in ``findings.yaml`` are
byte-for-byte the numbers the operators produced.

YAML backend
------------
Uses :mod:`yaml` (PyYAML) when importable.  If PyYAML is unavailable we fall
back to a deterministic ``json.dump`` — JSON is a syntactic subset of YAML
1.2, so the emitted file still parses as YAML (just without block style).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from operator_agent.hypothesis import Hypothesis
from .types import FindingRecord, ReviewResult, VerifyResult
from .blackboard import PrivateBlackboard

try:  # PyYAML is preferred; degrade to JSON-as-YAML if absent.
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except Exception:  # pragma: no cover - exercised only without PyYAML
    _yaml = None
    _HAS_YAML = False

PathLike = Union[str, "os.PathLike[str]"]

__all__ = [
    "write_findings",
    "build_finding_records",
    "HAS_YAML",
]

#: Whether a real PyYAML backend is in use (False → JSON-as-YAML fallback).
HAS_YAML = _HAS_YAML

# ``F_C{cohort}_N{i}`` — used to decide whether an id is already normalised.
_FINDING_ID_RE = re.compile(r"^F_C.+_N\d+$")
# A cohort token already written as ``C<digits>`` (e.g. ``C02``) — the
# ``F_C{token}`` template hardcodes the leading ``C``, so a caller-supplied
# ``cohort_id="C02"`` would otherwise double-prefix to ``F_CC02_N1``.
_COHORT_C_PREFIX_RE = re.compile(r"^[Cc](?=\d)")


def _cohort_token(cohort_id: str) -> str:
    """Normalise a cohort id for the ``F_C{token}_N{i}`` template.

    The template already supplies the leading ``C``; if the caller passes a
    canonical cohort code that *also* starts with ``C`` + a digit (e.g.
    ``"C02"``), strip that redundant ``C`` so we get ``F_C02_N1`` rather than
    ``F_CC02_N1``.  Non-``C<digit>`` ids (``"02_mdd"``, ``"99"``,
    ``"Cohort_A"``) are left untouched.
    """
    return _COHORT_C_PREFIX_RE.sub("", str(cohort_id or ""))


def _normalize_finding_id(existing: Optional[str], cohort_id: str,
                          index: int) -> str:
    """Return ``existing`` if it is already a canonical id, else assign
    ``F_C{cohort_id}_N{index}`` (index starting at 1).
    """
    if existing and _FINDING_ID_RE.match(existing):
        return existing
    return f"F_C{_cohort_token(cohort_id)}_N{index}"


def write_findings(run_dir: PathLike,
                   cohort_id: str,
                   findings: List[FindingRecord],
                   *,
                   findings_path: Optional[PathLike] = None,
                   status: Optional[str] = None,
                   reason: Optional[str] = None) -> Path:
    """Emit the cohort's findings to ``findings.yaml`` (V8 §F).

    Parameters
    ----------
    run_dir
        The run directory; the default output path is
        ``<run_dir>/findings.yaml``.
    cohort_id
        Cohort identifier used to build ``finding_id`` = ``F_C{cohort}_N{i}``.
    findings
        The per-lane :class:`FindingRecord` objects to aggregate.  Each is
        serialised via ``to_dict()`` (so it already carries the full §F
        field set); a missing/non-canonical ``finding_id`` is normalised in
        sequence (i starting at 1).  May be empty (per ADR-0007 the
        invariant is that a findings.yaml is **always written** regardless
        of run status).
    findings_path
        Optional explicit output path (overrides ``<run_dir>/findings.yaml``).
    status, reason
        ADR-0007 — top-level ``status`` and ``reason`` fields written
        into the YAML doc so that consumers can determine the run's
        terminal state from the file alone (without parsing logs).
        Both optional; omitted fields are simply absent in the YAML.

    Returns
    -------
    pathlib.Path
        The path the document was written to.
    """
    out_path = Path(findings_path) if findings_path is not None \
        else Path(run_dir) / "findings.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    finding_docs: List[Dict[str, Any]] = []
    for i, fr in enumerate(findings, start=1):
        d = fr.to_dict()
        d["finding_id"] = _normalize_finding_id(fr.finding_id, cohort_id, i)
        finding_docs.append(d)

    doc: Dict[str, Any] = {
        "cohort_id": cohort_id,
        "n_findings": len(finding_docs),
        "findings": finding_docs,
    }
    if status is not None:
        doc["status"] = status
    if reason is not None:
        doc["reason"] = reason

    # Round-trip through JSON first so every value is a plain JSON-safe type
    # (avoids PyYAML emitting Python-specific tags for tuples/etc.).
    doc = json.loads(json.dumps(doc, ensure_ascii=False))

    with open(out_path, "w", encoding="utf-8") as fh:
        if _HAS_YAML:
            _yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False,
                            default_flow_style=False)
        else:  # JSON is a subset of YAML 1.2 — still parses as YAML.
            json.dump(doc, fh, ensure_ascii=False, indent=2)
    return out_path


def build_finding_records(private_bbs: List[PrivateBlackboard],
                          cohort_id: str) -> List[FindingRecord]:
    """Assemble :class:`FindingRecord` objects from per-lane private boards.

    Reads each board's ``hypothesis`` / ``verify_result`` / ``review_result``
    keys and builds a record via :meth:`FindingRecord.from_verify` (keeping
    the verify numbers verbatim).  ``finding_id`` is assigned in sequence as
    ``F_C{cohort_id}_N{i}`` (i starting at 1).  Used by the supervisor later.
    """
    records: List[FindingRecord] = []
    for i, bb in enumerate(private_bbs, start=1):
        h_raw = bb.get("hypothesis")
        v_raw = bb.get("verify_result")
        r_raw = bb.get("review_result")
        n_raw = bb.get("novelty")

        hypothesis = Hypothesis.from_dict(h_raw) if h_raw else None
        verify = VerifyResult.from_dict(v_raw) if v_raw else VerifyResult()
        review = ReviewResult.from_dict(r_raw) if r_raw else None
        literature_context = None
        if isinstance(n_raw, dict):
            source = str(n_raw.get("source") or "stub")
            if source != "stub" or n_raw.get("references"):
                literature_context = dict(n_raw)

        finding_id = f"F_C{_cohort_token(cohort_id)}_N{i}"
        records.append(
            FindingRecord.from_verify(finding_id, hypothesis, verify,
                                      review=review,
                                      literature_context=literature_context))
    return records
