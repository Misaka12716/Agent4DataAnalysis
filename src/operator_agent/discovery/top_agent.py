# -*- coding: utf-8 -*-
"""Top-level coordination agent — user-collaboration layer over DiscoveryFlow.

This module is **purely additive** (V8 §addition): it does NOT modify the
router, supervisor, flow, stage modules, or any solver.  It wraps the existing
:class:`~operator_agent.discovery.flow.DiscoveryFlow` to add the four
collaboration capabilities the framework was missing:

1. **Routing-aware activation** — the full session machinery only "manifests"
   when the user's task is actually going to trigger discovery
   (``router.route(task) == "discovery"``).  Generic / legacy tasks are
   delegated as a one-shot result with the same ``.result()`` API but no
   session state to maintain (V8 §addition: "only when new content will
   actually be generated does the top-level agent take this shape").
2. **Pre-flight clarification** — before spending LLM tokens, check for
   obviously-missing information (no CSV, very short task, etc.) and
   surface clarifying questions to the user.  Caller can either
   register a synchronous ``clarify_hook`` callback (CLI / blocking
   contexts) or poll :meth:`Session.pending_clarifications` /
   :meth:`Session.answer` (webapp / async contexts).
3. **Cooperative cancellation** — :meth:`Session.cancel` sets a flag the
   wrapped stages check at entry; the next stage boundary raises a
   ``UserCancelledError`` which the supervisor catches and reports as
   ``DiscoveryResult(status="error", error="user_cancelled: ...")``.  Cancel
   granularity is **per-stage** (no mid-stage preemption — we deliberately
   don't reach into stage internals to keep this module additive).
4. **Live progress + intermediate-result access** — every stage emits
   ``Start / Done / Error`` signals on a :class:`SignalBus` we own; the
   session keeps a timestamped timeline.  :meth:`Session.progress` returns
   the current stage label, elapsed time, last N signals, and the
   public-blackboard snapshot (requirement / profile / cleaning suggestions
   / hypotheses) so the user can inspect intermediate results without
   waiting for the run to finish.

Usage (CLI)
-----------
::

    from operator_agent.discovery.top_agent import TopAgent

    def ask_user(question):
        print(question["question"])
        return input("> ").strip()

    agent = TopAgent(clarify_hook=ask_user)
    session = agent.start(
        task="看看这个文档里有没有可以发表的内容",
        csv="path/to/data.csv",
        max_hypotheses=2)
    # ... user can call session.progress() / session.cancel() ...
    result = session.result(timeout=600)  # blocks until done / cancelled
    print(result.status, result.summary)

Usage (webapp / async)
----------------------
::

    session = TopAgent().start(task=..., csv=..., clarify_hook=None)
    # GET /api/progress -> session.progress()
    # GET /api/clarify  -> session.pending_clarifications()
    # POST /api/clarify -> session.answer(question_id, answer)
    # POST /api/cancel  -> session.cancel()
    # GET /api/result   -> session.result(timeout=0.0)  if session.is_done()

Scope / non-goals
-----------------
- Does not change supervisor behaviour, never patches its stage callables
  in-place — instead it constructs a fresh ``Supervisor(...)`` with cancel-
  aware stage wrappers, passes that to :class:`DiscoveryFlow`.
- Does not introduce true async preemption (cancel is cooperative at stage
  boundaries — same limitation as :class:`signals.CancellationToken`).
- Does not start a webserver or wire into the FastAPI app; that is the
  caller's responsibility.  This module only exposes the Python API.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import pandas as pd

from . import (
    data_processing_stage,
    hypothesis_stage,
    refine_stage,
    review_stage,
    verify_stage,
)
from .blackboard import PublicBlackboard
from .errors import UserCancelledError as _BaseUserCancelledError
from .flow import DiscoveryFlow
from .paths import run_paths as _run_paths
from .router import RouteDecision, route as _default_route
from .signals import Signal, SignalBus, SignalType
from .supervisor import Supervisor
from .types import DiscoveryResult

__all__ = [
    "TopAgent",
    "Session",
    "Clarification",
    "UserCancelledError",
]


# ---------------------------------------------------------------------------
# Errors + dataclasses
# ---------------------------------------------------------------------------
class UserCancelledError(_BaseUserCancelledError):
    """Raised by a cancel-aware stage wrapper when the user has cancelled.

    ADR-0007 — this class now inherits from
    :class:`discovery.errors.UserCancelledError`, which itself derives from
    :class:`UserActionError`.  The supervisor's ``except UserActionError``
    branch catches it directly and emits ``status=cancelled`` (no
    ``supervisor_uncaught:`` wrapping).  The previous ``RuntimeError`` base
    survives via Python's MRO for any third-party code that did
    ``except RuntimeError``.
    """


@dataclass
class Clarification:
    """One question the top-level agent wants the user to answer.

    ``severity`` is informational: ``"blocking"`` means the run will wait
    for the answer before proceeding; ``"advisory"`` means the run will
    proceed regardless (the user can ignore it).
    """
    id: str
    question: str
    reason: str = ""
    severity: str = "blocking"          # "blocking" | "advisory"
    suggested_answers: List[str] = field(default_factory=list)
    asked_at: float = field(default_factory=time.time)
    answered_at: Optional[float] = None
    answer: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "reason": self.reason,
            "severity": self.severity,
            "suggested_answers": list(self.suggested_answers),
            "asked_at": self.asked_at,
            "answered_at": self.answered_at,
            "answer": self.answer,
        }


# ---------------------------------------------------------------------------
# Cancel-aware stage wrappers (no mutation of the real stage modules)
# ---------------------------------------------------------------------------
def _cancellable(fn: Callable, cancel_event: threading.Event,
                  stage_name: str) -> Callable:
    """Wrap a stage callable so it raises :class:`UserCancelledError` at
    entry when ``cancel_event`` is set.  The supervisor's last-resort
    ``except Exception`` then turns the run into an error result with our
    tag, and downstream stages never run.
    """
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if cancel_event.is_set():
            raise UserCancelledError(
                f"user_cancelled: stage={stage_name} not started")
        result = fn(*args, **kwargs)
        # Don't re-raise after the stage returned — its outputs were
        # already written; cancel takes effect at the *next* stage.
        return result
    wrapped.__name__ = f"cancellable[{stage_name}]"
    return wrapped


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class Session:
    """One running (or finished) discovery session.

    Thread-safe (RLock protects mutable state); the background worker
    thread runs the underlying :class:`DiscoveryFlow`.
    """

    def __init__(
        self,
        *,
        task: str,
        csv: Optional[Union[str, Path]],
        df: Optional[pd.DataFrame],
        route_decision: RouteDecision,
        flow_kwargs: Dict[str, Any],
        clarify_hook: Optional[Callable[[Dict[str, Any]], str]] = None,
        llm: Any = None,
        pre_questions: Optional[List[Clarification]] = None,
    ) -> None:
        self.task = task
        self.csv = csv
        self.df = df
        self.route = route_decision
        self._flow_kwargs = dict(flow_kwargs or {})
        self._clarify_hook = clarify_hook
        self._llm = llm

        self._run_id = self._flow_kwargs.get("run_id") or (
            f"discovery_{uuid.uuid4().hex[:8]}")
        self._flow_kwargs["run_id"] = self._run_id

        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._lock = threading.RLock()

        # Progress + clarification state
        self._stage_now: Optional[str] = None
        self._timeline: List[Dict[str, Any]] = []
        self._questions: List[Clarification] = list(pre_questions or [])
        self._questions_event = threading.Event()
        if self._questions:
            self._questions_event.set()

        self._result: Optional[DiscoveryResult] = None
        self._exc: Optional[BaseException] = None
        self._started_at = time.time()
        self._thread: Optional[threading.Thread] = None

        self._bus: Optional[SignalBus] = None
        self._public_bb_path: Optional[Path] = None
        try:
            self._public_bb_path = _run_paths(self._run_id).public_bb
        except Exception:
            self._public_bb_path = None

    # -------------------------- launch ---------------------------------
    def _launch(self) -> None:
        """Spawn the worker thread that runs the underlying flow."""
        self._thread = threading.Thread(
            target=self._run, name=f"TopAgentSession-{self._run_id}",
            daemon=True)
        self._thread.start()

    # -------------------------- worker ---------------------------------
    def _run(self) -> None:
        # 1) If there are blocking clarifications, wait for answers (or
        #    cancel) before spending LLM tokens.
        if not self._wait_for_clarifications():
            with self._lock:
                self._result = DiscoveryResult(
                    run_id=self._run_id, route="discovery",
                    status="error", findings=[], findings_path=None,
                    summary="cancelled before start",
                    error="user_cancelled: aborted at pre-flight clarify")
            self._done_event.set()
            return

        try:
            # 2) Build the cancel-aware supervisor + signal bus.
            bus = SignalBus()
            bus.subscribe_all(self._on_signal)
            self._bus = bus

            sup = self._build_supervisor(bus)

            # 3) Pin the router to our cached decision (avoid re-routing).
            cached_route = self.route
            def _cached_router(_task: str) -> RouteDecision:
                return cached_route

            flow = DiscoveryFlow(router_fn=_cached_router, supervisor=sup)

            # 4) Run.  Flow accepts either df or csv; we pass through.
            result = flow.run(
                self.task, df=self.df,
                csv=(str(self.csv) if self.csv is not None else None),
                **self._flow_kwargs)
            with self._lock:
                self._result = result
        except UserCancelledError as exc:
            with self._lock:
                self._result = DiscoveryResult(
                    run_id=self._run_id, route="discovery",
                    status="error", findings=[], findings_path=None,
                    summary="cancelled mid-run",
                    error=f"user_cancelled: {exc}")
        except Exception as exc:
            with self._lock:
                self._exc = exc
                self._result = DiscoveryResult(
                    run_id=self._run_id, route="discovery",
                    status="error", findings=[], findings_path=None,
                    summary="top_agent_session_crashed",
                    error=f"{type(exc).__name__}: {exc}")
        finally:
            self._done_event.set()

    # -------------------------- supervisor wiring ----------------------
    def _build_supervisor(self, bus: SignalBus) -> Supervisor:
        """Return a fresh Supervisor with cancel-aware stage wrappers.

        We never patch the real stage modules in-place — we just inject
        wrappers as the supervisor's stage callables (the supervisor was
        explicitly designed to accept injections).  This preserves the
        "additive only" invariant of this module.
        """
        ev = self._cancel_event
        return Supervisor(
            data_processing=_cancellable(
                data_processing_stage.run, ev, "data_processing"),
            hypothesis=_cancellable(
                hypothesis_stage.run, ev, "hypothesis"),
            verify=_cancellable(
                verify_stage.run, ev, "verify"),
            refine=_cancellable(
                refine_stage.decide, ev, "refine"),
            review=_cancellable(
                review_stage.run, ev, "review"),
            bus=bus,
            llm=self._llm,
        )

    # -------------------------- signal subscription --------------------
    def _on_signal(self, sig: Signal) -> None:
        """Record every signal in the timeline + update current stage."""
        with self._lock:
            entry = {
                "t": sig.timestamp,
                "type": sig.type.value,
                "source": sig.source,
                "payload": dict(sig.payload),
            }
            self._timeline.append(entry)
            # Current stage = the most recent Start that has no matching Done.
            if sig.type == SignalType.Start:
                self._stage_now = sig.source
            elif sig.type in (SignalType.Done, SignalType.Error):
                if self._stage_now == sig.source:
                    self._stage_now = None

    # -------------------------- public API: cancel ---------------------
    def cancel(self, reason: str = "user requested cancel") -> bool:
        """Cooperatively cancel the run.  Effective at the next stage
        boundary; returns ``True`` if the session was still running."""
        if self._done_event.is_set():
            return False
        self._cancel_event.set()
        # Release any blocking pre-flight wait so the worker can exit.
        self._questions_event.set()
        with self._lock:
            self._timeline.append({
                "t": time.time(), "type": "cancel",
                "source": "top_agent", "payload": {"reason": reason},
            })
        return True

    # -------------------------- public API: progress -------------------
    def progress(self, *, tail: int = 20) -> Dict[str, Any]:
        """Snapshot of where the run is right now.

        ``tail`` controls how many recent timeline entries to return
        (use a large value for full history, ``0`` for none).
        """
        with self._lock:
            return {
                "run_id": self._run_id,
                "route": self.route.route,
                "is_done": self._done_event.is_set(),
                "cancelled": self._cancel_event.is_set(),
                "stage_now": self._stage_now,
                "elapsed_s": round(time.time() - self._started_at, 2),
                "n_signals": len(self._timeline),
                "timeline_tail": (list(self._timeline[-tail:])
                                  if tail > 0 else []),
                "pending_clarifications": [q.to_dict() for q in self._questions
                                            if q.answer is None],
                "public_bb_snapshot": self._read_public_bb(),
            }

    def _read_public_bb(self) -> Dict[str, Any]:
        """Best-effort read of the on-disk public blackboard so the user
        can inspect intermediate stage outputs (requirement / profile /
        cleaning_suggestions / hypotheses)."""
        if self._public_bb_path is None or not self._public_bb_path.exists():
            return {}
        try:
            import json
            with open(self._public_bb_path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
        except Exception:
            return {}
        if not isinstance(blob, dict):
            return {}
        entries = blob.get("entries") or {}
        out: Dict[str, Any] = {}
        for key in ("requirement", "profile", "clean_suggestions",
                    "hypotheses", "novelty"):
            entry = entries.get(key)
            if isinstance(entry, dict) and "value" in entry:
                out[key] = entry["value"]
        return out

    # -------------------------- public API: clarify --------------------
    def pending_clarifications(self) -> List[Dict[str, Any]]:
        """Return JSON-safe list of unanswered clarification questions."""
        with self._lock:
            return [q.to_dict() for q in self._questions if q.answer is None]

    def answer(self, question_id: str, answer: str) -> bool:
        """Record an answer for one pending clarification.  Returns
        ``True`` if a matching question was found and updated."""
        with self._lock:
            for q in self._questions:
                if q.id == question_id and q.answer is None:
                    q.answer = answer
                    q.answered_at = time.time()
                    # If no questions left blocking → release the worker.
                    blocking_left = [
                        x for x in self._questions
                        if x.answer is None and x.severity == "blocking"
                    ]
                    if not blocking_left:
                        self._questions_event.set()
                    return True
        return False

    def add_urgent_message(self, message: str) -> None:
        """Surface an urgent user instruction in the timeline.  Does not
        change control flow (no preemption); useful as a paper-trail
        annotation when paired with :meth:`cancel`."""
        with self._lock:
            self._timeline.append({
                "t": time.time(), "type": "urgent_message",
                "source": "user", "payload": {"message": message},
            })

    # -------------------------- public API: result ---------------------
    def is_done(self) -> bool:
        return self._done_event.is_set()

    def result(self, timeout: Optional[float] = None) -> DiscoveryResult:
        """Block until the run finishes (or until ``timeout`` elapses).

        ``timeout=None`` means wait forever; ``timeout=0`` returns
        immediately with whatever is available (raises ``TimeoutError``
        if the run is still in progress).
        """
        if timeout == 0:
            if not self._done_event.is_set():
                raise TimeoutError("session still running; result not ready")
        else:
            finished = self._done_event.wait(timeout=timeout)
            if not finished:
                raise TimeoutError(
                    f"session did not finish within {timeout}s")
        with self._lock:
            assert self._result is not None
            return self._result

    # -------------------------- clarify wait loop ----------------------
    def _wait_for_clarifications(self) -> bool:
        """Block until every blocking question is answered (or cancel).

        Returns ``True`` to proceed, ``False`` to abort.  If a synchronous
        ``clarify_hook`` was provided we use it to answer questions
        inline; otherwise we wait for the caller to post answers via
        :meth:`answer`.
        """
        with self._lock:
            blocking = [q for q in self._questions
                        if q.answer is None and q.severity == "blocking"]
        if not blocking:
            return True

        # Synchronous path: ask the hook once per question.
        if self._clarify_hook is not None:
            for q in blocking:
                if self._cancel_event.is_set():
                    return False
                try:
                    ans = self._clarify_hook(q.to_dict())
                except Exception:
                    ans = ""
                if isinstance(ans, str) and ans:
                    self.answer(q.id, ans)
            return not self._cancel_event.is_set()

        # Async path: wait for the caller to post answers.
        while True:
            # Wake every 1s so cancel takes effect promptly.
            self._questions_event.wait(timeout=1.0)
            if self._cancel_event.is_set():
                return False
            with self._lock:
                blocking_left = [
                    q for q in self._questions
                    if q.answer is None and q.severity == "blocking"
                ]
            if not blocking_left:
                return True
            # Clear and loop — more questions may have arrived.
            self._questions_event.clear()


# ---------------------------------------------------------------------------
# A degenerate "session" for general-route tasks (no real session needed).
# ---------------------------------------------------------------------------
class _GeneralDelegateSession(Session):
    """When router says ``general``, we don't spin up a real session — we
    just return the standard delegate marker.  Same ``.result()`` /
    ``.progress()`` / ``.is_done()`` API so callers can be uniform.
    """

    def __init__(self, task: str, route_decision: RouteDecision) -> None:
        # Bypass the heavy Session.__init__: this object has nothing to
        # run.
        self.task = task
        self.csv = None
        self.df = None
        self.route = route_decision
        self._run_id = ""
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._done_event.set()
        self._lock = threading.RLock()
        self._stage_now = None
        self._timeline = [{
            "t": time.time(), "type": "general_delegate",
            "source": "top_agent",
            "payload": {"reason": route_decision.reason},
        }]
        self._questions: List[Clarification] = []
        self._questions_event = threading.Event()
        self._started_at = time.time()
        self._thread = None
        self._bus = None
        self._public_bb_path = None
        self._result = DiscoveryResult(
            run_id="", route="general", status="ok",
            findings=[], findings_path=None,
            summary="delegate_to_legacy", error=None)
        self._exc = None
        self._clarify_hook = None
        self._llm = None
        self._flow_kwargs = {}


# ---------------------------------------------------------------------------
# TopAgent
# ---------------------------------------------------------------------------
class TopAgent:
    """Top-level coordinator.  Owns no scientific logic; only orchestrates
    routing, pre-flight clarification, and session creation.

    Parameters
    ----------
    llm
        Optional LLM passed through to Session → Supervisor (refine /
        review / hypothesis novelty use it when available).  If ``None``
        we fall back to ``operator_pipeline.llm_client`` inside the
        downstream stages.
    clarify_hook
        Optional synchronous callback ``(question_dict) -> answer_str``
        used for blocking pre-flight questions in CLI contexts.  Leave
        ``None`` for webapp / async contexts and use
        :meth:`Session.pending_clarifications` + :meth:`Session.answer`
        instead.
    router_fn
        Override the entry router (defaults to
        :func:`operator_agent.discovery.router.route`).  Tests inject a
        stub here.
    """

    def __init__(
        self,
        *,
        llm: Any = None,
        clarify_hook: Optional[Callable[[Dict[str, Any]], str]] = None,
        router_fn: Optional[Callable[[str], RouteDecision]] = None,
    ) -> None:
        self._llm = llm
        self._clarify_hook = clarify_hook
        self._router = router_fn or _default_route

    # -------------------------- public entry ---------------------------
    def start(
        self,
        task: str,
        *,
        csv: Optional[Union[str, Path]] = None,
        df: Optional[pd.DataFrame] = None,
        **flow_kwargs: Any,
    ) -> Session:
        """Route the task; create a Session that runs the appropriate path.

        ``flow_kwargs`` are forwarded verbatim to
        :meth:`DiscoveryFlow.run` (so ``max_hypotheses``, ``cohort_id``,
        ``seed``, ``hypothesis_impl``, ``run_id`` etc. all work).

        Returns a :class:`Session` whose ``.is_done()`` may already be
        ``True`` (for general-route delegation) or whose worker is
        running in the background (for discovery-route runs).
        """
        # Route — only "manifest" the full session machinery for
        # discovery tasks (per user requirement: 顶层 agent only takes
        # this shape when new content will actually be generated).
        try:
            decision = self._router(task)
        except Exception as exc:
            # Router failure → safe default: delegate to legacy.
            decision = RouteDecision(
                route="general",
                reason=f"router crashed: {type(exc).__name__}: {exc}")

        if decision.route != "discovery":
            return _GeneralDelegateSession(task, decision)

        # Pre-flight clarify: cheap, deterministic checks only.  LLM-driven
        # ambiguity probes can be added later without changing this API.
        pre_questions = self._preflight_questions(task, csv, df)

        session = Session(
            task=task, csv=csv, df=df,
            route_decision=decision,
            flow_kwargs=flow_kwargs,
            clarify_hook=self._clarify_hook,
            llm=self._llm,
            pre_questions=pre_questions)
        session._launch()
        return session

    # -------------------------- helpers --------------------------------
    @staticmethod
    def _preflight_questions(
        task: str,
        csv: Optional[Union[str, Path]],
        df: Optional[pd.DataFrame],
    ) -> List[Clarification]:
        """Cheap deterministic checks for *obviously* missing info.

        Surfaces a clarification only when the run cannot reasonably
        proceed without an answer.  Keep this list short — every entry
        becomes a UX speed-bump.
        """
        qs: List[Clarification] = []

        # (1) No data at all — DiscoveryFlow would later return
        # status=error.  Asking up-front is friendlier than crashing
        # mid-run.
        if df is None and not csv:
            qs.append(Clarification(
                id="missing_data",
                question=("No dataset was provided.  Paste a server-side "
                          "CSV path, upload a CSV, or attach a dataframe "
                          "before we can run discovery."),
                reason="discovery requires a dataframe or csv",
                severity="blocking",
                suggested_answers=[]))
            return qs  # nothing else matters if there's no data

        # (2) Task string is implausibly short — ask the user what they
        # actually want to learn.  Threshold deliberately low (<8 chars
        # is e.g. "test", "deg") so we don't pester normal queries.
        if isinstance(task, str) and len(task.strip()) < 8:
            qs.append(Clarification(
                id="task_too_short",
                question=("Your task description is very short — could "
                          "you describe what scientific question you "
                          "want answered from this dataset?"),
                reason=f"task length = {len(task.strip())} chars",
                severity="blocking",
                suggested_answers=[]))

        return qs
