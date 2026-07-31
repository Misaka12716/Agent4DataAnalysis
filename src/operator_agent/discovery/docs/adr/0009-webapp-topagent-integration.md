# Webapp ↔ TopAgent integration (REST + polling)

**Status**: accepted (2026-06-07)

`top_agent.py` ships TopAgent + Session + Clarification +
UserCancelledError, but `webapp.py` still calls `Supervisor.run()`
synchronously and never instantiates a Session.  Consequence: the
webapp UI has no cancel button, no pre-flight clarify, no live progress,
no place to wire up N2's mid-run cleaning approval (ADR-0004).  TopAgent
exists only on the CLI / SDK path.

This ADR specifies how webapp integrates with TopAgent so all four
capabilities surface in the browser.

## 1. RESTful endpoints + frontend polling

```
POST /run/start                    body: {task, csv_path, options...}
                                   → 202 {run_id}

GET  /run/<run_id>/status          → 200 {status, current_stage, progress_pct,
                                          pending_clarifications, started_at}

POST /run/<run_id>/cancel          → 200 {ok}

GET  /run/<run_id>/clarifications  → 200 {pending: [Clarification...]}

POST /run/<run_id>/clarifications/<qid>
                                   body: {answer}
                                   → 200 {ok}

GET  /run/<run_id>/result          → 200 RunResult if status terminal
                                   → 202 {status, current_stage} if running
```

Frontend polls `/run/<id>/status` at **1 Hz** while running (Q9.3).
Higher frequencies stress the server for no real benefit; lower
frequencies (5s) eat into the 10s mid-run clarify window.  `/result`
is fetched once when status reports terminal.

SSE / WebSocket alternatives were rejected for v1 — they introduce
protocol complexity without commensurate benefit at the current
workflow timescale (multi-minute runs, 10s clarify windows).  The
endpoint shape doesn't change if v2 swaps polling for streaming.

## 2. Session registry: in-process dict + lock

```python
# discovery/web_session_registry.py
_REGISTRY: Dict[str, Session] = {}
_LOCK = threading.Lock()

def register(session: Session) -> None: ...
def get(run_id: str) -> Optional[Session]: ...
def evict(run_id: str) -> None: ...
def list_for_user(user_key: str) -> List[Session]: ...
```

- Single-process Flask deployment is the assumed shape.  Sticky-session
  reverse proxies or Redis-backed registry are deferred until measured
  multi-worker deployment becomes a priority.
- Eviction policy: 1 hour after a Session reaches a terminal state, OR
  when Q9.4's "auto-cancel previous" fires.
- The registry is **explicitly documented as single-process** in this
  ADR; the contract for consumers is "this works for desktop /
  small-team deployments; do not assume HA".

Redis / SQLite / no-registry alternatives were rejected: Redis is
over-engineering for v1; SQLite adds I/O latency on every poll without
addressing multi-worker; no-registry breaks the polling model.

## 3. Mid-run clarify timing

The mid-run clarify mechanism in ADR-0003 specifies a 10-second default
timeout.  webapp keeps that timeout unchanged but compensates for
polling latency by:

- Frontend polls `/clarifications` at 1 Hz (same poll loop as
  `/status`).
- Once a clarification is rendered, the user has the **full remaining
  timeout** to answer (the 10s clock starts on the bus side, not on
  render).  Worst case the user sees the question ~1s after it was
  raised and has 9s to answer.
- POSTs to `/clarifications/<qid>` map directly to
  `Session.answer(qid, ...)`, which calls
  `bus.set_answer(qid, ...)` and unblocks the waiting stage.

Extending timeout per-deployment (env var) is supported but not
recommended — longer timeouts penalise headless / batch consumers of
the same TopAgent.

## 4. Concurrency: 1 in-flight run per user, auto-cancel previous

`POST /run/start` semantics when a user already has an in-flight run:

1. Look up the user's current in-flight run via the registry.
2. If found, send `Session.cancel()` and **wait for it to actually
   reach a terminal state** (bounded wait, e.g. 5s — current stage
   should observe the cancel within seconds).
3. If the previous run doesn't terminate within the wait, return 409
   to the new request with a "previous run is unresponsive" message.
4. On success, register the new Session and return 202.

User identity for "current user" comes from whatever auth mechanism
the deployment uses (today the webapp is single-user-no-auth; the
"user_key" is a session cookie or just a constant).

Reject-new / queue / unrestricted-concurrency alternatives were
rejected: rejecting frustrates users mid-iteration; queueing hides
the in-flight run; unrestricted concurrency bloats memory and
confuses the UI.

## 5. Cancel UX: cooperative-only in v1

The cancel button in v1 issues `Session.cancel()` which sets the
cooperative cancel event.  The currently-running stage observes the
event at its next checkpoint (start of a stage, or between
hypothesis-card iterations inside a stage) and raises
`UserCancelledError`.  The supervisor catches it (ADR-0007),
returns `RunStatus.cancelled` with whatever partial findings made it
through review.

Hard cancel (kill the worker thread, abort live operator subprocesses)
is **not implemented in v1**.  Operator subprocesses launched by
verify_stage have their own internal timeouts; the worst case for
"I clicked cancel but the run is still running" is bounded by the
slowest operator's timeout (typically minutes, not hours).  A
TODO is recorded for later — clean implementation requires per-stage
subprocess group tracking + signal propagation, out of scope here.

## 6. Implementation surface (informational)

- `webapp.py` adds the routes from §1, all delegating to
  `web_session_registry` + `TopAgent`.
- New module `discovery/web_session_registry.py` (§2 contract).
- `webapp.py`'s pre-existing synchronous `Supervisor.run()` call site
  is **deleted**, not preserved as a fallback — TopAgent is the only
  way the webapp launches runs after this ADR.
- The frontend `templates/run.html` (or equivalent) gains:
  - Cancel button bound to `POST /cancel`.
  - Live progress strip bound to 1Hz `/status` poll.
  - Clarification modal bound to 1Hz `/clarifications` poll +
    `POST /clarifications/<qid>`.
  - Pre-flight clarification block rendered before the run starts (the
    frontend submits the start form, sees pre-flight questions in
    the first poll, holds the run UI in "awaiting clarification"
    state until answered).
