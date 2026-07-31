# N2 cleaning is transformational and opt-out

**Status**: accepted (2026-06-07)

N2 (`data_processing_stage` cleaning portion) used to be **suggest-only**:
it computed `CleanSuggestion` entries and wrote them to the public
blackboard, but never modified the dataframe.  V8 §B.1.8 originally drew
N2 as `cleaned_df → N3` but the implementation had been informational
only, leaving the data flow figure inconsistent with the code.

This ADR makes N2 actually transformational, with three constraints:

1. **Cleaning is opt-out, not opt-in.**  The default is to apply the
   suggested cleaning unless the user explicitly rejects it within a
   10-second window (see ADR-0003 for the mid-run clarify
   mechanism).  Headless / batch / CI runs see the default apply
   immediately (timeout fires with no answer, default wins).

2. **Cleaning is transparent.**  Before any change is committed to the
   dataframe, N2 dry-runs the suggestion list on `df.copy()` and emits
   a `Clarify` signal whose payload contains a human-readable diff:
   per-column missing-rate before/after, row-count delta, dtype
   changes, and the executable pandas snippets that will be applied.
   The user (when present) reviews this diff.

3. **Cleaning replaces raw_df in the data flow.**  After N2 commits
   the cleaning, the cleaned dataframe is what N3-N7 see.  `dataset_hash`
   in `VerifyResult.reproducibility` is the hash of the *cleaned*
   dataframe, not of the user's original CSV.  This is the source of
   truth that downstream lanes verify against.

## Reproducibility

The cleaned dataframe is persisted to `runs/<run_id>/cleaned_input.csv`
the moment N2 commits.  `findings.yaml.reproducibility` gains a new
field `cleaning_applied: List[snippet]` containing the exact pandas
expressions executed, so a later reviewer can:

- Re-run the cohort by loading `cleaned_input.csv` directly (fast path,
  exact bit-for-bit), OR
- Re-run from the original CSV by replaying the `cleaning_applied`
  snippets (audit path, useful when checking whether the cleaning was
  reasonable).

When the user rejects the cleaning, `cleaned_input.csv` is identical to
the original CSV and `cleaning_applied` is the empty list — the path
stays single, reviewers don't need a separate "cleaning rejected" code
branch.

## Why not opt-in (rejected)

Opt-in (default = skip cleaning, only clean if user explicitly accepts)
would force every batch / scripted / CI run to either (a) accept user
friction on every invocation, or (b) inject an "auto-yes" hook that
silently bypasses the approval mechanism — defeating the design.  Most
users in practice accept algorithm-suggested cleaning; making them
accept it once per run is friction without commensurate value.

## Why not pure side-channel (rejected)

The previously-proposed "informational only" form (cleaning_log goes to
public_bb, raw_df continues downstream untouched) was rejected because
it leaves V8 §B.1.8 figure forever inconsistent with code, and because
"the LLM sees a cleaner picture but the operators see the dirty df"
produces hypotheses that downstream operators can't actually verify.
If we want the agent to act on cleaning insight, the data has to
actually be cleaned.
