# -*- coding: utf-8 -*-
"""V8 Pattern D — Output auditor agent.

After the Coder's ``main.py`` runs and prints ``Final answer: X``, this
auditor performs a tiny LLM call (or, when the task is unambiguous, a
purely deterministic check) to verify that ``X`` matches the *form*
the task requires.  Common bugs the auditor catches:

1. Task asks for a multiple-choice letter (A/B/C/D) but the Coder
   printed a number (e.g. a correlation coefficient).
2. Task asks for a categorical token (``"numerical"`` /
   ``"categorical"`` / ``"yes"`` / ``"no"``) but the Coder printed
   a paragraph.
3. Task asks for a numerical answer with a specific unit / decimal
   precision but the Coder printed prose.

When the LLM is unavailable the auditor is a no-op (returns the raw
answer unchanged) so the auditor never *breaks* a working pipeline —
only ever refines an obviously-wrong-shape answer.

Design notes
------------
- We **never** invent a new numeric value; if the Coder's number is
  clearly wrong, the auditor can only flag it, not fix it.  The
  auditor's superpower is *form fixing*, not *truth fixing*.
- The audit is logged into the per-task ``record`` (drivers attach it
  as ``audit_raw`` / ``audit_pred`` / ``audit_reason``) so the
  downstream case study can show both the raw Coder answer and the
  audited answer.
- LLM call is bounded to ~200 tokens + temperature=0; very cheap.

中文
----
Pattern D 的「审查员 agent」: Coder 跑完之后, 用一次小 LLM 调用 (或在
明确无歧义时用纯确定性检查) 校验输出的**形态**是否符合题目要求 ——
比如选项题印了数字、应该印 ``yes``/``no`` 印了一段散文、应该 2 位小数
印了带单位的字符串等。仅修「形态」, 绝不臆造新数字。LLM 不可用时直接
透传原答案, 不会破坏正常 pipeline。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from operator_pipeline import llm_client


_FORM_NORMALIZER_SYSTEM_EN = (
    "You are the FORM-NORMALIZER stage of a two-stage output auditor "
    "for a data analysis pipeline.  A worker LLM wrote Python that "
    "computed an answer and printed `Final answer: X`.  Your ONLY job "
    "in this stage is to check whether `X` matches the FORM the task "
    "requires; if it does not, rewrite it to the required form using "
    "ONLY the numeric / categorical content already present in `X` "
    "(plus any options the task provided).\n"
    "\n"
    "Hard rules:\n"
    "  1. Output ONE JSON object.  No prose.  No markdown fences.  "
    "Schema: `{\"audited_answer\": <string>, \"reason\": <string>, "
    "\"changed\": <bool>}`.\n"
    "  2. NEVER invent new numeric values, never invent a label that "
    "was not already present in the worker's output.  If the worker "
    "printed prose, you may extract a number / token from that prose, "
    "but you MUST NOT pull a number out of thin air just because the "
    "task asked for one.  When the worker's output contains NO number "
    "at all and the task demands one, return the worker's answer "
    "unchanged with `reason` explaining the gap (the downstream "
    "SemanticReviewer will flag it).\n"
    "  3. If the task provides options A/B/C/... AND the worker "
    "returned a number / phrase that clearly matches exactly one "
    "option, return the option LETTER (single uppercase letter, no "
    "period).\n"
    "  4. If the task asks for a categorical token (`numerical` / "
    "`categorical` / `yes` / `no` / a known short string) and the "
    "worker returned a paragraph that uses that exact token, extract "
    "it.  Do NOT guess between options when the worker did not name "
    "one.\n"
    "  5. If the task asks for a number with a specific unit / "
    "decimal precision and the worker printed prose containing a "
    "number, extract the bare number and apply the requested "
    "rounding.\n"
    "  6. If you cannot determine the right form with high confidence "
    "from content the worker actually printed, return the worker's "
    "answer unchanged (`changed=false`)."
)


# Backwards-compatible alias for callers that still import the old
# symbol; the form normalizer is the only Stage-1 auditor.
_AUDITOR_SYSTEM_EN = _FORM_NORMALIZER_SYSTEM_EN


_SEMANTIC_REVIEWER_SYSTEM_EN = (
    "You are the SEMANTIC REVIEWER stage of a two-stage output auditor "
    "for a data analysis pipeline.  The worker LLM finished, the "
    "FormNormalizer fixed any trivial form issue, and now your job is "
    "to assess whether the produced answer is **semantically plausible** "
    "given (a) the task, (b) the worker's printed output, and (c) the "
    "summaries of operator artefacts already computed upstream.  You "
    "must NEVER invent numeric values and MUST NOT 'fix' a wrong "
    "number — your output is a structured judgement that downstream "
    "logging can record.\n"
    "\n"
    "Hard rules:\n"
    "  1. Output ONE JSON object.  No prose.  No markdown fences.  "
    "Schema: `{\"verdict\": <\"plausible\"|\"low_confidence\"|"
    "\"clearly_wrong\">, \"reason\": <string>, "
    "\"suggested_fix\": <string|null>, "
    "\"evidence\": <string|null>}`.\n"
    "  2. The schema is *advisory*.  You MUST keep the audited answer "
    "as produced by the FormNormalizer; this stage does NOT rewrite "
    "the answer.  Use `suggested_fix` only when you can point to a "
    "concrete evidence row in the operator artefacts that shows what "
    "the answer should have been.\n"
    "  3. Branch the review by question type:\n"
    "       - Numerical answer: check unit, decimal precision, sign, "
    "and order of magnitude against any operator artefact / context "
    "the user message provides.  Mark `clearly_wrong` only when an "
    "evidence row contradicts the answer by >>2x or the wrong sign.\n"
    "       - Multiple-choice letter: check the letter exists in the "
    "options, and (if upstream artefacts show a numeric quantity that "
    "implies a specific option) check internal consistency.\n"
    "       - Categorical token (`numerical`/`categorical`/`yes`/`no`/"
    "etc.): trust the metadata_parser / domain hint in the user "
    "message if provided; otherwise mark `plausible` unless the "
    "answer is one of the listed-but-wrong choices.\n"
    "       - Open / multi-line FINDINGS (RDAB-style): mark "
    "`plausible` if the worker printed structured findings; mark "
    "`low_confidence` if the block is empty.\n"
    "  4. Do NOT hallucinate evidence.  `evidence` MUST quote a short "
    "string the user message actually supplied (operator artefact "
    "snippet, metadata field, etc.); leave it null if no evidence "
    "applies.\n"
    "  5. Be conservative.  When in doubt, use `low_confidence` with "
    "a brief reason — downstream code uses this only for logging.\n"
    "  6. **This prompt applies to EVERY benchmark question type "
    "(numerical, multiple-choice, categorical, open-form findings, "
    "RDAB / RADAR / QRData / etc).**  Do not optimise for one "
    "question; check the task wording before judging."
)


@dataclass
class SemanticReview:
    """Stage-2 semantic verdict from SemanticReviewer.

    The reviewer NEVER rewrites the audited answer; it only emits a
    structured judgement that downstream logging records.  Schema:

    - ``verdict``: ``plausible`` | ``low_confidence`` | ``clearly_wrong``
    - ``reason``: short free-form explanation
    - ``suggested_fix``: optional candidate answer if the reviewer can
      point to concrete operator-artefact evidence (NEVER applied
      automatically — driver decides what to do with it)
    - ``evidence``: a short quoted snippet from the user message that
      justifies the verdict (operator artefact row, metadata field, …)
    """
    verdict: str = "plausible"
    reason: str = ""
    suggested_fix: Optional[str] = None
    evidence: Optional[str] = None
    llm_used: bool = False
    llm_error: Optional[str] = None
    skipped_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "suggested_fix": self.suggested_fix,
            "evidence": self.evidence,
            "llm_used": self.llm_used,
            "llm_error": self.llm_error,
            "skipped_reason": self.skipped_reason,
        }


@dataclass
class AuditResult:
    raw_answer: str
    audited_answer: str
    changed: bool = False
    reason: str = ""
    llm_used: bool = False
    llm_error: Optional[str] = None
    skipped_reason: Optional[str] = None
    semantic_review: Optional[SemanticReview] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "raw_answer": self.raw_answer,
            "audited_answer": self.audited_answer,
            "changed": self.changed,
            "reason": self.reason,
            "llm_used": self.llm_used,
            "llm_error": self.llm_error,
            "skipped_reason": self.skipped_reason,
        }
        if self.semantic_review is not None:
            out["semantic_review"] = self.semantic_review.to_dict()
        return out


_OPTION_LETTER_RE = re.compile(r"^\s*([A-Z])\b[.\):\-\s]")


def _option_letters(options: Sequence[str]) -> List[str]:
    """Extract the leading capital letter (A/B/C/...) of each option,
    if any.  Used to short-circuit the LLM call when the answer
    already is a valid option letter."""
    out: List[str] = []
    for o in options:
        s = str(o).strip()
        m = _OPTION_LETTER_RE.match(s)
        if m:
            out.append(m.group(1).upper())
    return out


def _looks_like_option_letter(answer: str,
                                option_letters: Sequence[str]) -> bool:
    s = answer.strip().rstrip(".)").upper()
    return len(s) == 1 and s in set(option_letters)


def _build_audit_user_message(task: str,
                                raw_answer: str,
                                options: Sequence[str],
                                expected_form: Optional[str]) -> str:
    parts: List[str] = []
    parts.append("## Task description")
    parts.append(task.strip())
    parts.append("")
    if options:
        parts.append("## Multiple-choice options (verbatim from task)")
        for o in options:
            parts.append(f"  - {o}")
        parts.append("")
    if expected_form:
        parts.append("## Expected answer FORM hint (from task / driver)")
        parts.append(expected_form.strip())
        parts.append("")
    parts.append("## Worker's printed answer")
    parts.append("```")
    parts.append(raw_answer.strip())
    parts.append("```")
    parts.append("")
    parts.append("Return the JSON object now.")
    return "\n".join(parts)


def _summarise_operator_artifacts(operator_results: Optional[Dict[str, Any]],
                                   max_rows: int = 5,
                                   max_chars: int = 2400) -> str:
    """Render a compact text summary of operator artefacts to feed the
    SemanticReviewer.  Reads at most a few rows from each successful
    step's primary CSV; quietly skips files that don't exist."""
    if not operator_results:
        return ""
    steps = operator_results.get("steps") or []
    lines: List[str] = []
    for s in steps:
        if s.get("status") != "ok":
            continue
        name = s.get("name", "?")
        solver = s.get("solver", "?")
        outs = s.get("outputs") or {}
        csv_items = [(k, v) for k, v in outs.items()
                     if isinstance(v, str) and v.lower().endswith(".csv")]
        if not csv_items:
            continue
        for csv_key, csv_path in csv_items[:2]:
            try:
                import pandas as _pd  # local to avoid top-level import in module load
                df_head = _pd.read_csv(csv_path, nrows=max_rows)
                rendered = df_head.to_csv(index=False).rstrip()
            except Exception:
                rendered = "(could not read csv)"
            lines.append(f"### step `{name}` ({solver}) -> `{csv_key}`:")
            lines.append("```")
            lines.append(rendered)
            lines.append("```")
            if sum(len(x) for x in lines) > max_chars:
                lines.append("(operator artefact summary truncated)")
                return "\n".join(lines)
    return "\n".join(lines)


def _build_semantic_user_message(task: str,
                                   audited_answer: str,
                                   raw_answer: str,
                                   options: Sequence[str],
                                   expected_form: Optional[str],
                                   operator_results: Optional[Dict[str, Any]],
                                   worker_stdout_tail: Optional[str]) -> str:
    parts: List[str] = []
    parts.append("## Task description")
    parts.append(task.strip())
    parts.append("")
    if options:
        parts.append("## Multiple-choice options (verbatim from task)")
        for o in options:
            parts.append(f"  - {o}")
        parts.append("")
    if expected_form:
        parts.append("## Expected answer FORM hint")
        parts.append(expected_form.strip())
        parts.append("")
    parts.append("## Worker's printed answer (raw)")
    parts.append("```")
    parts.append(raw_answer.strip())
    parts.append("```")
    parts.append("")
    parts.append("## After form normalization")
    parts.append("```")
    parts.append(audited_answer.strip())
    parts.append("```")
    parts.append("")
    if worker_stdout_tail:
        tail = worker_stdout_tail.strip()
        if tail:
            parts.append("## Worker stdout tail (last ~1200 chars)")
            parts.append("```")
            parts.append(tail[-1200:])
            parts.append("```")
            parts.append("")
    op_summary = _summarise_operator_artifacts(operator_results)
    if op_summary:
        parts.append("## Operator artefacts available upstream")
        parts.append(op_summary)
        parts.append("")
    parts.append("Return the JSON object now.")
    return "\n".join(parts)


def semantic_review(task: str,
                     audited_answer: str,
                     raw_answer: str,
                     *,
                     options: Optional[Sequence[str]] = None,
                     expected_form: Optional[str] = None,
                     operator_results: Optional[Dict[str, Any]] = None,
                     worker_stdout_tail: Optional[str] = None,
                     max_tokens: int = 600,
                     temperature: float = 0.0) -> SemanticReview:
    """Run the Stage-2 semantic reviewer.

    Returns a :class:`SemanticReview`.  Skips silently when the LLM is
    unavailable or when the audited answer is empty.  Never overwrites
    the audited answer; downstream code only logs the verdict.
    """
    if not (audited_answer or "").strip():
        return SemanticReview(verdict="low_confidence",
                              reason="empty audited answer",
                              skipped_reason="empty audited answer")
    if not llm_client.is_available():
        return SemanticReview(verdict="plausible",
                              reason="LLM unavailable; skipped",
                              skipped_reason="LLM unavailable")
    user_msg = _build_semantic_user_message(
        task=task,
        audited_answer=audited_answer,
        raw_answer=raw_answer,
        options=list(options or []),
        expected_form=expected_form,
        operator_results=operator_results,
        worker_stdout_tail=worker_stdout_tail,
    )
    try:
        out = llm_client.chat_json(_SEMANTIC_REVIEWER_SYSTEM_EN,
                                    user_msg,
                                    max_tokens=max_tokens,
                                    temperature=temperature,
                                    stage="auditor")
    except llm_client.LLMError as e:
        return SemanticReview(verdict="plausible",
                              reason="reviewer LLM error",
                              llm_used=True,
                              llm_error=f"{type(e).__name__}: {e}",
                              skipped_reason="LLM error")
    except Exception as e:  # pragma: no cover — defensive
        return SemanticReview(verdict="plausible",
                              reason="reviewer crashed",
                              llm_used=True,
                              llm_error=f"{type(e).__name__}: {e}",
                              skipped_reason="unexpected error")

    if not isinstance(out, dict):
        return SemanticReview(verdict="plausible",
                              reason="reviewer returned non-dict",
                              llm_used=True,
                              skipped_reason="non-dict")

    verdict_raw = str(out.get("verdict", "") or "").strip().lower()
    if verdict_raw not in {"plausible", "low_confidence", "clearly_wrong"}:
        verdict_raw = "plausible"
    sug = out.get("suggested_fix")
    ev = out.get("evidence")
    return SemanticReview(
        verdict=verdict_raw,
        reason=str(out.get("reason", "") or "").strip(),
        suggested_fix=(str(sug).strip() if sug not in (None, "") else None),
        evidence=(str(ev).strip() if ev not in (None, "") else None),
        llm_used=True,
    )


def audit_final_answer(task: str,
                        raw_answer: str,
                        *,
                        options: Optional[Sequence[str]] = None,
                        expected_form: Optional[str] = None,
                        max_tokens: int = 400,
                        temperature: float = 0.0,
                        operator_results: Optional[Dict[str, Any]] = None,
                        worker_stdout_tail: Optional[str] = None,
                        run_semantic_review: bool = True) -> AuditResult:
    """Audit the worker's final answer and return :class:`AuditResult`.

    Parameters
    ----------
    task
        Full task description (same string fed to the planner).
    raw_answer
        The string the driver extracted from ``Final answer: <…>``.
    options
        Multiple-choice options if the task is MC; empty / None
        otherwise.  When ``raw_answer`` already is a valid option
        letter, the auditor short-circuits and skips the LLM call.
    expected_form
        Optional human-readable hint such as
        ``"single capital letter A/B/C/D"`` or
        ``"numerical, 2 decimal places, no unit"``.  Driver-supplied;
        the auditor uses it to bias the LLM.
    max_tokens, temperature
        Knobs for the LLM call.

    Returns
    -------
    AuditResult
        Always has a non-empty ``audited_answer`` (falls back to
        ``raw_answer`` on any error).  ``changed`` is True iff the
        audited string differs from the raw string.
    """
    raw = (raw_answer or "").strip()
    options = list(options or [])

    if not raw:
        return AuditResult(raw_answer=raw, audited_answer=raw,
                           skipped_reason="empty raw answer")

    letters = _option_letters(options)
    if letters and _looks_like_option_letter(raw, letters):
        return AuditResult(raw_answer=raw,
                            audited_answer=raw.strip().rstrip(".)").upper(),
                            changed=raw != raw.strip().rstrip(".)").upper(),
                            reason="already a valid option letter",
                            skipped_reason="short-circuit MC letter")

    if not llm_client.is_available():
        return AuditResult(raw_answer=raw, audited_answer=raw,
                           skipped_reason="LLM unavailable; auditor no-op")

    user_msg = _build_audit_user_message(
        task=task, raw_answer=raw,
        options=options, expected_form=expected_form,
    )

    try:
        out = llm_client.chat_json(_FORM_NORMALIZER_SYSTEM_EN, user_msg,
                                    max_tokens=max_tokens,
                                    temperature=temperature,
                                    stage="auditor")
    except llm_client.LLMError as e:
        result = AuditResult(raw_answer=raw, audited_answer=raw,
                              llm_used=True,
                              llm_error=f"{type(e).__name__}: {e}",
                              skipped_reason="LLM error; FormNormalizer no-op")
        if run_semantic_review:
            result.semantic_review = semantic_review(
                task=task, audited_answer=raw, raw_answer=raw,
                options=options, expected_form=expected_form,
                operator_results=operator_results,
                worker_stdout_tail=worker_stdout_tail,
            )
        return result
    except Exception as e:  # pragma: no cover — defensive
        return AuditResult(raw_answer=raw, audited_answer=raw,
                            llm_used=True,
                            llm_error=f"{type(e).__name__}: {e}",
                            skipped_reason="unexpected error; FormNormalizer no-op")

    if not isinstance(out, dict):
        return AuditResult(raw_answer=raw, audited_answer=raw,
                            llm_used=True,
                            skipped_reason="FormNormalizer returned non-dict")

    audited = str(out.get("audited_answer", "") or "").strip()
    reason = str(out.get("reason", "") or "").strip()
    changed_field = bool(out.get("changed", audited != raw))
    if not audited:
        result = AuditResult(raw_answer=raw, audited_answer=raw,
                              llm_used=True, reason=reason,
                              skipped_reason="FormNormalizer returned empty answer")
    else:
        result = AuditResult(
            raw_answer=raw,
            audited_answer=audited,
            changed=(audited != raw) or changed_field,
            reason=reason,
            llm_used=True,
        )

    if run_semantic_review:
        result.semantic_review = semantic_review(
            task=task,
            audited_answer=result.audited_answer,
            raw_answer=raw,
            options=options,
            expected_form=expected_form,
            operator_results=operator_results,
            worker_stdout_tail=worker_stdout_tail,
        )
    return result


__all__ = [
    "AuditResult",
    "SemanticReview",
    "audit_final_answer",
    "semantic_review",
]
