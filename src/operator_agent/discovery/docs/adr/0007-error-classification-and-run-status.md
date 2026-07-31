# Error classification, RunResult status, and findings invariant

**Status**: accepted (2026-06-07)

The supervisor's previous behavior was to wrap **every** exception
that escaped a stage into `RunResult(status="error",
error="supervisor_uncaught: ...")`.  This conflated three distinct
classes of run termination:

- The **user** asked the run to stop (cancel button, rejection of an
  N2 cleaning preview).
- The **user's input** was unsuitable (CSV that won't decode, file
  type we don't support, dataset with no analysable columns).
- The **agent / system** failed (LLM transport exhausted, operator
  subprocess crash, real bug).

Treating all three as `supervisor_uncaught:` had visible costs:

- The UI showed a stack trace for cancellations, making users think
  they had broken the system by clicking cancel.
- Triage couldn't tell at a glance whether a `runs/<id>/` failure
  needed a bug fix or a user-side fix.
- Retry / debug machinery (planned in ADR-0002) couldn't selectively
  trigger on real system errors without re-triggering on user cancel.

## 1. Three exception base classes

Add three exception base classes in `discovery/errors.py`:

- **`UserActionError`** — the user explicitly caused termination.
  Subclasses:
  - `UserCancelledError` (existing, in `top_agent.py`, will be moved /
    re-rooted under `UserActionError`).
  - `UserRejectedCleaningError` (new, ADR-0004 N2 path: user said no
    to cleaning preview).
  - `UserTimeoutError` (reserved for future use, e.g. clarify wait
    expired with no default).
- **`DataInputError`** — the user's input is shape-wrong.
  Subclasses:
  - `DataLoadError` (defined in ADR-0006: encoding / size / type / NA).
  - `UnanalyzableDataError` (new: N1 detected the dataframe has
    nothing analysable — e.g. all columns are free-text, or no rows).
- **`SystemError`** — agent / framework / operator failure.  Catch-all
  for any `Exception` that is not one of the above.

The supervisor catches by base class, **not** by concrete subclass:
adding a new `UserActionError` subclass later doesn't require touching
the supervisor.

```python
# supervisor.py (sketch)
try:
    ...
except UserActionError as e:
    return _result(status="cancelled", reason=str(e))
except DataInputError as e:
    return _result(status="rejected_input", reason=str(e))
except SystemError as e:
    return _result(status="error", error=f"supervisor_uncaught: {e}")
except Exception as e:           # last-resort net
    return _result(status="error", error=f"supervisor_uncaught: {e}")
```

The bare `except Exception` remains as a safety net for un-classified
exceptions; any time we see one in production, we either reclassify it
into a base class above or fix the bug.

## 2. RunResult.status: 5-state enum

```python
class RunStatus(str, Enum):
    ok              = "ok"               # >=1 finding produced
    empty           = "empty"            # ran cleanly, no findings
    cancelled       = "cancelled"        # UserActionError
    rejected_input  = "rejected_input"   # DataInputError
    error           = "error"            # SystemError or unclassified
```

UI rendering rules:
- `ok` / `empty` — normal result page, no warning.
- `cancelled` — neutral "Run cancelled" page with whatever partial
  findings exist.
- `rejected_input` — user-error page describing what was wrong with
  the input and how to fix.
- `error` — error page with traceback link (collapsible) and "report
  bug" affordance.

`status="error"` is the **only** status that triggers debug-grade
logging (full traceback to `run.log`).  The other four log only the
reason string.

## 3. RunResult shape: add `reason`, keep `error` semantics tight

```python
@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    findings: List[Finding]      # may be empty for any non-ok status
    reason: Optional[str] = None  # user-readable, set for cancelled /
                                  #   rejected_input / empty / error
    error: Optional[str] = None   # set ONLY when status==error,
                                  #   contains "supervisor_uncaught: ..."
    partial: bool = False         # True iff status==cancelled and
                                  #   findings is non-empty
```

`error` is now **scoped to real system failures**, so callers that
want to alert / page on agent failure can `if result.error is not
None` without false positives from cancellations.

`reason` is the user-readable explanation for any non-ok terminal
state.  Examples: `"user clicked cancel"`, `"file too large: 850 MB
exceeds 500 MB cap"`, `"LLM transport failed after 5 retries"`.

## 4. findings.yaml invariant: always written

**Every Discovery Run produces `runs/<run_id>/findings.yaml`,
regardless of terminal status.**  This is a simpler invariant than the
previous "sometimes written, sometimes not" behavior, and it lets any
consumer (UI, archiver, downstream tooling) treat the file's existence
as the run-completion signal.

The yaml schema gains the run status:

```yaml
run_id: discovery_xxxxxxxx
status: cancelled        # one of the 5 RunStatus values
reason: "user clicked cancel during verify_stage"
partial: true            # findings below are partial
findings:
  - hypothesis: ...
    ...
reproducibility:
  ...
```

Per status:
- `ok` — `findings` non-empty, `partial=false`, `reason=null`.
- `empty` — `findings=[]`, `partial=false`,
  `reason="<diagnostic from N3/N5>"`.
- `cancelled` — `findings` may be partial, `partial=true` iff any
  finding made it through review_stage,
  `reason="user cancelled during <stage>"`.
- `rejected_input` — `findings=[]`, `partial=false`,
  `reason="<DataInputError message>"`.  Note: rejected_input runs
  still create the run dir and write findings.yaml, even though no
  agent stage ran — the file's existence + status field tell
  consumers what happened without parsing logs.
- `error` — `findings` may be partial up to the failure point,
  `partial=true` iff non-empty, `reason="<short user message>"`,
  and `error` field on RunResult carries the `supervisor_uncaught:`
  string.

## 5. Consumer migration

- `findings_archive/` (ADR-0001) only copies `findings.yaml` files
  whose `status in {ok, cancelled, error}` AND `findings` is
  non-empty.  `empty` and `rejected_input` are kept inside `runs/<id>/`
  for short-term diagnosis but never archived — they have no
  scientific content.
- `webapp` distinguishes `cancelled` vs `error` for which UI panel to
  render (Q7.4 outcome).
- `TopAgent.Session.result()` now returns the typed `RunResult` with
  `status` / `reason` / `error` fields; existing call sites that did
  `if result.error` keep working but now correctly skip cancellations.

## Considered and rejected

- **Single boolean `error: Optional[str]`, no status enum**: doesn't
  give the UI enough information to render distinct states; reduces
  to the current broken behavior.
- **Many fine-grained statuses** (`partial`, `timeout`,
  `quota_exhausted`, etc.): premature; we can refine `error`'s reason
  string further as patterns emerge in practice.  Five states is the
  small set every UI panel needs.
- **Pattern-matching `supervisor_uncaught:` strings in the UI**: leaks
  framework internals into UI code, fragile across refactors.
