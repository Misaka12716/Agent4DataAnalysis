# Failure-mode-aware two-tier LLM retry

**Status**: accepted (2026-06-07)

LLM-related failures in a Discovery Run come in two distinct shapes; we
retry each one differently rather than wrapping every stage in the same
"retry on exception" decorator.

**Tier 1 — `chat_json` transient retry** (per LLM call): 5 attempts with
exponential backoff (2s base, ×2, +0–500ms jitter), capped at 30s per
sleep.  Retries 5xx / network reset / `429 rate limit` / timeout.  4xx
non-429 (`401 unauthorized`, `quota_exceeded`, `model_not_found`) early-
exits without retry — these are deterministic failures.

**Tier 2 — failure-class-specific retry** (above `chat_json`):

- **LLM Stage Retry**: when an LLM stage (hypothesis / refine / review,
  and the planner call inside verify) exhausts its `chat_json` budget
  with no usable result, the stage is re-invoked **once** after a 30s
  sleep.  This applies only to the *transport* failure case — content
  quality issues (e.g. `refine` returning `{"refuse": true}`, or
  `hypothesis` LLM returning a malformed card) are deliberately handled
  by the stage's existing in-stage logic (giveup / try-continue) and do
  not trigger a Tier 2 retry.  Retrying with the same prompt 30s later
  cannot fix a content-quality problem.

- **Planner Retry**: verify_stage is the *only* stage that contains both
  an LLM call (the planner picking operators) and a deterministic
  operator pipeline.  When `execute_pipeline` raises (operator
  subprocess crashed / unrecoverable runtime failure), we re-invoke the
  planner inside verify_stage with a hint about the previous failure,
  then re-execute the new operator spec.  This is targeted at the
  failure mode where the LLM picked an operator combination that does
  not actually work for this hypothesis + dataset; a fresh planner call
  with the failure as context will pick differently.

We explicitly rejected:

- **Stage-blanket retry** (sleep 30s and re-invoke any failing stage):
  useless for `data_processing` (deterministic — same input ⇒ same
  failure) and wasteful for stages that already have internal LLM
  resilience.  The blanket form does not distinguish between LLM
  transport failures (where retry helps) and content-quality failures
  (where retry cannot help).

- **Whole-run retry** (re-do N1 through N7 from scratch on any
  late-stage failure): re-doing N1 (profile) and N3 (hypothesis) wastes
  the work already completed; worse, the regenerated hypothesis set
  almost never contains the lane that originally failed (LLM sampling
  variance), so the failed lane is not actually retried — it is
  silently dropped.  `Planner Retry` solves the same operator-failure
  case surgically, with a single planner re-invocation, and keeps the
  failing lane on the same hypothesis.

We explicitly **did not** add a per-Session token budget cap in this
round.  Production cost control is deferred; today we rely on per-call
`max_tokens` plus the new resilience tiers to keep cost predictable.
