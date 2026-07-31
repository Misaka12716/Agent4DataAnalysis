# Separate findings archive from per-run scratch

**Status**: accepted (2026-06-07)

`findings.yaml` is the only durable product of a Discovery Run; everything
else under `runs/<run_id>/` (`artifacts/`, `work/`, `public_bb.json`,
`private_*.json`) is debugging / audit material that can be regenerated
from the yaml's `dataset_hash` + `seed` + `operator_versions` block.  We
therefore keep two retention tiers:

- `runs/<run_id>/` — short retention (default 7 days, see Q2 for the
  exact policy), aggressively pruned.
- `findings_archive/<cohort_id>__<run_id>__<timestamp>.yaml` — long
  retention (default 365 days or unbounded), populated by a copy issued
  inside `compile.write_findings()` immediately after the in-run yaml is
  written successfully.

Without this split, retention has to be conservative enough to never
accidentally delete a user's only deliverable, which means a single run
of a few hundred MB of artifacts gets dragged along for a year.  The
split lets us delete debugging material weekly while the deliverables
live forever, with no operational coupling between the two.

A consequence callers must respect: an archived yaml's `artifact_paths`
will eventually point to deleted files.  Tooling that reads from
`findings_archive/` MUST treat `artifact_paths` as advisory only and
never follow them blindly; the numbers in the yaml are the source of
truth.  We will mark archived yamls with `archived: true` at copy time
so consumers can branch on it.

Considered and rejected: keeping `findings.yaml` inside `runs/` and
making the whole run dir long-retention.  Rejected because typical
`artifacts/` size (5-50MB per run) × moderate traffic (1k runs/month) ×
1 year retention = 60-600GB, which is not viable on a single-VM deploy.
