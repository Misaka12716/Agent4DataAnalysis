# Mid-run clarify mechanism (Clarify signal + bounded wait)

**Status**: accepted (2026-06-07)

The framework had only **pre-flight clarify** — TopAgent could ask the
user questions before launching a Discovery Run, and once the run was
launched it ran straight through to the end with no further interaction
beyond cooperative cancel.  This ADR introduces **mid-run clarify**: a
stage can pause inside the run, surface a question + payload to the user
through TopAgent.Session, wait briefly for an answer, and resume with
either the user's answer or a documented default.

## Mechanism

- New `SignalType.Clarify` on the existing `SignalBus`.  Payload schema:
  `{question_id, question_text, suggested_answers, default_answer,
  timeout_s, payload}` where `payload` carries stage-specific context
  (e.g. for N2 it carries the cleaning preview diff).
- New `SignalBus.wait_for_answer(question_id, timeout_s)` blocks the
  emitting stage until either `Session.answer(question_id, ...)` is
  called (answer wins) or `timeout_s` elapses (default wins).  Internally
  a `threading.Event` per question_id; multiple stages with concurrent
  clarifies are supported but lanes today are sequential.
- TopAgent.Session is unchanged at the API surface — `pending_clarifications()`
  + `answer(qid, ans)` work for both pre-flight and mid-run questions.
  The only change is that mid-run questions appear and disappear in the
  middle of a run as stages emit / resolve them.

## Mode-dependent waiting

- **CLI / sync `clarify_hook`** present: stage calls
  `clarify_hook(question_dict)` synchronously and uses the return value
  immediately.  No timeout involved.
- **Webapp / async** (no `clarify_hook`): stage calls
  `bus.wait_for_answer(qid, timeout_s)`.  Default `timeout_s` is
  short (10s for N2 cleaning approval), tunable per call.  After
  timeout, stage proceeds with the documented default.
- **Headless / batch** (no `clarify_hook`, no answer channel): same
  code path as webapp/async, but no answer ever arrives → timeout fires
  immediately-ish (waits the configured timeout once, then proceeds).
  This means batch jobs see at most one timeout-window of latency per
  mid-run clarify call, not infinite hang.

## Why a short timeout

A long timeout (60s) is dead time for users who trust the default; a
zero timeout would prevent users who *are* watching from intervening.
10s gives a watching user enough time to read the preview and click a
button, while not penalising the trusting majority.  It is config, so
ops can tune per deployment.

## Why this is opt-out, not opt-in

The "user must approve cleaning" framing was rejected because it
imposes friction on every batch / CI / scripted run, and 95% of users
will accept the default cleaning anyway.  The opt-out shape (apply by
default, allow rejection within 10s) preserves the user's *ability* to
intervene without making intervention mandatory.  See ADR-0004 for the
N2-specific consequences.

## Considered and rejected

- **Block-with-long-timeout (60s+)**: penalises trusting users.
- **Non-blocking + cancel-to-undo**: the user's only way to reject is
  to cancel the entire run and restart with a `skip_cleaning` flag.
  This degrades clarify into a notification and removes surgical
  intervention.  Architectural debt.
- **Per-suggestion checkbox UI**: large UI surface.  Today the answer
  is binary (apply all or none); fine-grained selection can be added
  later by extending the question payload — the signal/wait mechanism
  doesn't change.
