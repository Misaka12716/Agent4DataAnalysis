# -*- coding: utf-8 -*-
"""Top-level planning agent (顶层规划 Supervisor) — V8 §3 / §4 / §11.

The supervisor *orchestrates* the discovery framework; it owns no scientific
logic of its own — every stage is an injectable callable (defaulting to the
real one) so the supervisor is unit-testable without a live LLM or the heavy
pipeline.

Control flow (V8 §3):

    clarify_intent (bounded, skippable)        ── batch ⇒ no blocking
        → summarize requirement → public bb
        → data_processing  (profile + cleaning suggestions → public bb)
        → hypothesis       (fan out one private board per hypothesis)
        → for each lane (independent / parallelisable later):
              verify → refine loop (bounded) → review
        → aggregate findings → compile → findings.yaml
        → public-blackboard lifecycle: clear AFTER persistence

Exception model (V8 §3.1):
    The supervisor only *reacts* to ``Error`` signals (it subscribes an Error
    collector to the bus); it never polls / watchdogs an agent.  Stages
    self-report errors.  :meth:`Supervisor.run` itself never raises — any
    unexpected exception is wrapped into a ``DiscoveryResult(status="error")``
    and an ``Error`` is emitted.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable, List, Optional

import pandas as pd

from . import compile as compile_stage
from . import data_processing_stage
from . import hypothesis_stage
from . import litcheck_stub
from . import refine_stage
from . import review_stage
from . import verify_stage
from .blackboard import PublicBlackboard, PrivateBlackboard
from .cleanup import (
    archive_findings,
    cleanup_old_archive,
    cleanup_old_runs,
)
from .data_processing_stage import DataProcessingError
from .errors import (
    DataInputError,
    RunStatus,
    SystemError as DiscoverySystemError,
    UserActionError,
)
from .paths import ensure_run_dir
from .signals import Signal, SignalBus, SignalType
from .types import DiscoveryResult, FindingRecord, RequirementSummary

_LOG = logging.getLogger(__name__)

__all__ = ["Supervisor", "PRODUCER"]

PRODUCER = "supervisor"


class Supervisor:
    """Top-level planner that wires the discovery stages together.

    Every stage is injectable so the orchestration can be unit-tested in
    isolation; defaults are the real stage callables.

    Parameters
    ----------
    data_processing, hypothesis, verify, refine, review, compile, litcheck
        Optional overrides for the corresponding stage callables/modules.
        ``compile`` is expected to expose ``build_finding_records`` +
        ``write_findings`` (defaults to the :mod:`compile` stage module).
    bus
        Optional :class:`SignalBus`; when omitted a fresh bus is created per
        :meth:`run`.
    llm
        Optional LLM object (``is_available()`` + ``chat_json(...)``) threaded
        into refine / review for prose-only assistance.  Never required.
    """

    def __init__(
        self,
        *,
        data_processing: Optional[Callable] = None,
        hypothesis: Optional[Callable] = None,
        verify: Optional[Callable] = None,
        refine: Optional[Callable] = None,
        review: Optional[Callable] = None,
        compile: Any = None,
        litcheck: Optional[Callable] = None,
        bus: Optional[SignalBus] = None,
        llm: Any = None,
    ) -> None:
        self._data_processing = data_processing or data_processing_stage.run
        self._hypothesis = hypothesis or hypothesis_stage.run
        self._verify = verify or verify_stage.run
        self._refine = refine or refine_stage.decide
        self._review = review or review_stage.run
        self._compile = compile or compile_stage
        self._litcheck = litcheck or litcheck_stub.run
        self._bus = bus
        self._llm = llm

    # ---------------------- bounded intent clarification ----------------
    def _clarify_intent(self, task: str, *, clarify: bool) -> RequirementSummary:
        """Summarise the user's research requirement (V8 §3 clarify loop).

        Batch / non-interactive runs pass ``clarify=False`` so this **never
        blocks**; it just folds the task into a bounded
        :class:`RequirementSummary`.  The structure leaves room for a future
        interactive clarification loop (constraints / success_criteria), but
        no question is asked here.
        """
        notes = None
        if clarify:
            # Interactive clarification is out of scope for batch runs; we
            # record the intent but never block waiting for the user.
            notes = ("clarify requested but running non-interactively; "
                     "proceeded with the task verbatim")
        return RequirementSummary(
            task=task,
            goal=task.strip(),
            constraints=[],
            success_criteria=[],
            notes=notes,
        )

    # ---------------------- verify → refine loop (one lane) -------------
    def _verify_refine_loop(
        self,
        task: str,
        df: pd.DataFrame,
        hypothesis,
        public_bb: PublicBlackboard,
        private_bb: PrivateBlackboard,
        *,
        run_dir: Path,
        bus: SignalBus,
        seed: Optional[int],
        refine_max_iter: int,
    ):
        """Run the bounded verify→refine loop for a single hypothesis lane.

        Returns ``(final_hypothesis, final_verify_result, history)``.

        Loop logic (V8 §3 / §3.1):
          1. verify the current hypothesis;
          2. ask refine_stage.decide what to do given the lane history;
          3. if it says ``refine`` AND produced a revised hypothesis AND we
             are still under ``refine_max_iter`` → adopt the revision and
             re-verify (append to history);
          4. otherwise (``converge`` / ``giveup`` / no revision) → stop.

        Lanes are independent and could be parallelised later; we run them
        sequentially here.
        """
        current_hyp = hypothesis
        history: List[Any] = []
        verify_result = None
        iters = 0
        while True:
            # Each lane gets its own working dir so concurrent verify runs
            # never collide on input.csv / pipeline_output.
            lane_work = run_dir / "work" / f"{current_hyp.id}_i{iters}"
            verify_result = self._verify(
                task, df, current_hyp, public_bb, private_bb,
                run_dir=lane_work, bus=bus, seed=seed)
            history.append(verify_result)

            decision = self._refine(
                current_hyp, verify_result, history,
                max_iter=refine_max_iter, llm=self._llm,
                df=df, public_bb=public_bb)

            if (decision.action == "refine" and decision.revised is not None
                    and iters < refine_max_iter):
                current_hyp = decision.revised
                iters += 1
                continue
            break  # converge / giveup / no revision

        return current_hyp, verify_result, history

    # ---------------------- main entry point ----------------------------
    def run(
        self,
        task: str,
        df: pd.DataFrame,
        *,
        run_id: Optional[str] = None,
        cohort_id: str = "X",
        seed: Optional[int] = 42,
        max_hypotheses: int = 3,
        refine_max_iter: int = 3,
        hypothesis_impl: Optional[Callable] = None,
        novelty_checker: Optional[Callable] = None,
        llm_novelty: bool = False,
        drop_replications: bool = True,
        novelty_min_score: float = 0.0,
        clarify: bool = False,
        apply_cleaning: bool = False,
    ) -> DiscoveryResult:
        """Orchestrate one discovery run end-to-end.

        Never raises.  Failures are partitioned per ADR-0007:

        - :class:`UserActionError` → ``status=cancelled``, ``reason``
          set, ``error=None`` (not a system fault).
        - :class:`DataInputError` → ``status=rejected_input``,
          ``reason`` set, ``error=None`` (not a system fault).
        - any other ``Exception`` (incl. :class:`SystemError`) →
          ``status=error``, ``error="supervisor_uncaught: ..."``.

        Per ADR-0007 §4, ``findings.yaml`` is **always written**
        regardless of terminal status, with a ``status`` field inside
        identifying what happened.
        """
        run_id = run_id or f"discovery_{uuid.uuid4().hex[:8]}"

        # --- ADR-0001: lazy startup cleanup of old runs + archive ------
        try:
            cleanup_old_runs()
            cleanup_old_archive()
        except Exception as exc:  # pragma: no cover — never block on cleanup
            _LOG.warning("discovery cleanup failed (non-fatal): %r", exc)

        # --- run tree + data plane + control plane ----------------------
        rp = ensure_run_dir(run_id)
        public_bb = PublicBlackboard(path=rp.public_bb)
        bus = self._bus if self._bus is not None else SignalBus()

        # §3.1: the supervisor only *reacts* to Errors — collect them.
        errors: List[Signal] = []
        bus.subscribe(SignalType.Error, lambda s: errors.append(s))

        # State accumulated across the run for the always-write
        # findings.yaml invariant (ADR-0007 §4).
        accumulated_findings: List[FindingRecord] = []

        def _persist_findings(status: str,
                              findings: List[FindingRecord],
                              reason: Optional[str],
                              n_hyp: int = 0,
                              n_pass: int = 0,
                              n_fail: int = 0,
                              error_str: Optional[str] = None,
                              ) -> Optional[str]:
            """Always-write findings.yaml + archive on terminal status.

            Returns the on-disk findings path (str) or None on failure.
            """
            try:
                path = self._compile.write_findings(
                    rp.run_dir, cohort_id, findings,
                    findings_path=rp.findings,
                    status=status, reason=reason)
            except TypeError:
                # Backwards compat: older write_findings signature
                # without status/reason kwargs.
                try:
                    path = self._compile.write_findings(
                        rp.run_dir, cohort_id, findings,
                        findings_path=rp.findings)
                except Exception:
                    return None
            except Exception:
                return None
            # Archive only "scientific-content" terminal states.
            if status in ("ok", "cancelled", "error") and findings:
                try:
                    archive_findings(run_id, Path(path))
                except Exception:
                    pass
            return str(path) if path is not None else None

        def _cancelled_result(exc: UserActionError) -> DiscoveryResult:
            partial = bool(accumulated_findings)
            findings_path = _persist_findings(
                "cancelled", accumulated_findings,
                reason=str(exc) or "user cancelled")
            return DiscoveryResult(
                run_id=run_id, route="discovery",
                status=RunStatus.cancelled.value,
                findings=accumulated_findings, findings_path=findings_path,
                summary=self._summary(0, 0, 0, len(errors)),
                error=None,
                reason=f"user_cancelled: {exc}" if str(exc) else "user_cancelled",
                partial=partial)

        def _rejected_result(exc: DataInputError) -> DiscoveryResult:
            findings_path = _persist_findings(
                "rejected_input", [], reason=str(exc))
            return DiscoveryResult(
                run_id=run_id, route="discovery",
                status=RunStatus.rejected_input.value,
                findings=[], findings_path=findings_path,
                summary=self._summary(0, 0, 0, len(errors)),
                error=None, reason=str(exc), partial=False)

        def _error_result(message: str) -> DiscoveryResult:
            digest = self._errors_digest(errors)
            full = f"{message} | collected: {digest}" if digest else message
            partial = bool(accumulated_findings)
            findings_path = _persist_findings(
                "error", accumulated_findings,
                reason=message, error_str=full)
            return DiscoveryResult(
                run_id=run_id, route="discovery",
                status=RunStatus.error.value,
                findings=accumulated_findings, findings_path=findings_path,
                summary=self._summary(0, 0, 0, len(errors)),
                error=f"supervisor_uncaught: {full}",
                reason=message, partial=partial)

        try:
            bus.emit(SignalType.Start, source=PRODUCER, run_id=run_id)

            # --- 2) clarify_intent (bounded) + summarise requirement -----
            req = self._clarify_intent(task, clarify=clarify)
            public_bb.put("requirement", req.to_dict(),
                          producer=PRODUCER, seed=seed)

            # --- 3) data processing (profile + cleaning suggestions) -----
            try:
                self._data_processing(df, task, public_bb,
                                      run_dir=rp.run_dir, bus=bus, seed=seed,
                                      apply_cleaning=apply_cleaning)
            except DataProcessingError as exc:
                # The Error signal was already emitted by the stage (§3.1).
                return _error_result(f"data_processing_failed: {exc}")

            analysis_df = df
            cleaned_input_path = public_bb.get("cleaned_input_path")
            if cleaned_input_path:
                try:
                    analysis_df = pd.read_csv(cleaned_input_path)
                except Exception as exc:
                    bus.emit_error(
                        PRODUCER,
                        error=repr(exc),
                        reason="cleaned_input_load_failed",
                        path=cleaned_input_path,
                    )
                    analysis_df = df

            # --- 4) hypothesis generation + fan-out lanes ----------------
            hypotheses, private_boards = self._hypothesis(
                task, analysis_df, public_bb,
                run_dir=rp.run_dir, max_hypotheses=max_hypotheses,
                impl=hypothesis_impl, bus=bus, seed=seed,
                llm=self._llm, novelty_checker=novelty_checker,
                llm_novelty=llm_novelty,
                drop_replications=drop_replications,
                novelty_min_score=novelty_min_score)

            if not hypotheses:
                # Surface *why* there were no hypotheses (e.g. the LLM was
                # unavailable / out of quota).  The reason is collected on the
                # bus as Error signals; without echoing it into the result the
                # user only sees "0 hypotheses" with no diagnosable cause.
                digest = self._errors_digest(errors)
                reason_text = (
                    f"no hypotheses generated; collected: {digest}"
                    if digest else "no hypotheses generated")
                findings_path = _persist_findings(
                    "empty", [], reason=reason_text)
                return DiscoveryResult(
                    run_id=run_id, route="discovery",
                    status=RunStatus.empty.value,
                    findings=[], findings_path=findings_path,
                    summary=self._summary(0, 0, 0, len(errors)),
                    error=None, reason=reason_text, partial=False)

            # --- 5) per-lane verify → refine loop, then review -----------
            # Lanes are independent and could be parallelised later; run
            # sequentially for now.
            n_pass = 0
            n_fail = 0
            for hyp, board in zip(hypotheses, private_boards):
                final_hyp, final_verify, _hist = self._verify_refine_loop(
                    task, analysis_df, hyp, public_bb, board,
                    run_dir=rp.run_dir, bus=bus, seed=seed,
                    refine_max_iter=refine_max_iter)

                review = self._review(final_hyp, final_verify,
                                      private_bb=board, bus=bus, llm=self._llm)
                if getattr(review, "verdict", "fail") == "pass":
                    n_pass += 1
                else:
                    n_fail += 1

            # --- 6) aggregate → compile → findings.yaml ------------------
            accumulated_findings = self._compile.build_finding_records(
                private_boards, cohort_id)
            cleaning_applied = public_bb.get("cleaning_applied") or []
            cleaning_actions = public_bb.get("cleaning_actions") or []
            cleaned_input_path = public_bb.get("cleaned_input_path")
            for fr in accumulated_findings:
                fr.reproducibility["cleaning_applied"] = list(cleaning_applied)
                fr.reproducibility["cleaning_actions"] = list(cleaning_actions)
                fr.reproducibility["cleaned_input_path"] = cleaned_input_path
            findings_path = _persist_findings(
                "ok", accumulated_findings, reason=None,
                n_hyp=len(hypotheses), n_pass=n_pass, n_fail=n_fail)

            # --- 7) lifecycle: persist boards, THEN clear public bb ------
            for board in private_boards:
                if board.path is not None:
                    try:
                        board.save_json()
                    except Exception:
                        pass  # persistence is best-effort, never fatal
            # Public blackboard is cleared only AFTER findings are written
            # and private boards are persisted (V8 §4 / §11).
            public_bb.clear()
            try:
                public_bb.save_json()
            except Exception:
                pass

            bus.emit_done(PRODUCER, run_id=run_id,
                          n_findings=len(accumulated_findings))

            # --- 8) result ----------------------------------------------
            status_value = (RunStatus.ok.value
                            if accumulated_findings else RunStatus.empty.value)
            return DiscoveryResult(
                run_id=run_id, route="discovery", status=status_value,
                findings=accumulated_findings,
                findings_path=findings_path,
                summary=self._summary(len(hypotheses), n_pass, n_fail,
                                      len(errors)),
                error=None,
                reason=(None if accumulated_findings
                        else "no findings produced"),
                partial=False)

        except UserActionError as exc:
            # ADR-0007 — user-driven cancel is NOT an error.
            try:
                bus.emit(SignalType.Done, source=PRODUCER,
                         status="cancelled", reason=str(exc))
            except Exception:
                pass
            return _cancelled_result(exc)

        except DataInputError as exc:
            # ADR-0007 — user-input shape error is NOT an agent fault.
            try:
                bus.emit(SignalType.Done, source=PRODUCER,
                         status="rejected_input", reason=str(exc))
            except Exception:
                pass
            return _rejected_result(exc)

        except DiscoverySystemError as exc:
            # Real system fault, but at least classified.
            try:
                bus.emit_error(PRODUCER, error=repr(exc),
                               reason="discovery_system_error")
            except Exception:
                pass
            return _error_result(f"discovery_system_error: {exc!r}")

        except Exception as exc:  # last-resort guard: never raise (§3.1)
            try:
                bus.emit_error(PRODUCER, error=repr(exc),
                               reason="supervisor_uncaught")
            except Exception:
                pass
            return _error_result(f"supervisor_uncaught: {exc!r}")

    # ---------------------- helpers ----------------------
    @staticmethod
    def _summary(n_hyp: int, n_pass: int, n_fail: int, n_err: int) -> str:
        return (f"{n_hyp} hypotheses; {n_pass} pass / {n_fail} fail; "
                f"{n_err} error signal(s) collected")

    @staticmethod
    def _errors_digest(errors: List[Signal], *, limit: int = 3) -> str:
        """Compact, human-readable digest of collected ``Error`` signals.

        Pulls the most informative fields out of each signal payload
        (``error_code`` / ``reason`` / ``error``) so the failure cause
        (e.g. an LLM ``insufficient_quota``) is visible on the
        :class:`DiscoveryResult` instead of being buried on the bus.
        """
        if not errors:
            return ""
        parts: List[str] = []
        for sig in errors[:limit]:
            payload = getattr(sig, "payload", None) or {}
            bits = [b for b in (
                payload.get("error_code"),
                payload.get("reason"),
                payload.get("error"),
            ) if b]
            detail = "; ".join(str(b) for b in bits) or "unspecified error"
            parts.append(f"{getattr(sig, 'source', '?')}: {detail}")
        if len(errors) > limit:
            parts.append(f"(+{len(errors) - limit} more)")
        return " | ".join(parts)
