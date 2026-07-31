# Litcheck backend selection, caching, and LLM-judged novelty

**Status**: accepted (2026-06-07)

`litcheck_stub.py` was a literal `return None` placeholder.  N3's
**Novelty Gate** had no real evidence to compare against, so any claim
of novelty was nominal.  This ADR replaces the stub with a real
retrieval + scoring pipeline, and pins down the operating envelope
(network failures, caching, PII).

## 1. Backend: Semantic Scholar primary, PubMed bio fallback

The **Discovery Framework** is not strictly biomedical (it can produce
hypotheses on any tabular domain), but its high-value workloads today
are bio (GDS DEG, pathway enrichment).  We need both reach and depth.

- **Primary: Semantic Scholar Graph API.**  Cross-domain coverage,
  JSON output (no XML parsing), abstracts inline, citation graph and
  recommendation signals available for free.  10 qps with a key,
  1 qps without.
- **Fallback: PubMed E-utilities** (NCBI ESearch + EFetch).  When S2
  returns < 3 hits AND query has biomedical hint (variable / outcome
  matches a small bio keyword list, or N2 detected gene symbols / probe
  IDs in the dataframe), the litcheck client retries against PubMed
  and merges by DOI.  3 qps without key, 10 qps with.

Aggregated multi-source query (PubMed + S2 + OpenAlex parallel) was
considered and **rejected for v1**: implementation, rate-limit, and
debugging cost are high; we will revisit if v1 hit-rate is insufficient.

OpenAlex / arXiv / local-corpus options were rejected as primary on
precision, domain-fit, and engineering-cost grounds respectively.

## 2. Caching: disk, 30-day TTL, two-tier keys

Every N3 call generates one or more retrieval queries; without caching
the system will burn API quota and slow runs to a crawl on iteration.

- **Cache root**: `litcheck_cache/` at the same level as
  `findings_archive/` (long-retention sibling, not under `runs/` so it
  survives run cleanup).
- **TTL**: 30 days.  Papers indexed by S2/PubMed don't churn that fast,
  and the agent's iteration loops within a single research project
  typically span hours-to-days, so a single project re-uses the cache
  fully.
- **Two-tier keys**:
  - `retrieval/<sha1(canonical_query)>.json` — raw API response
    (hits + abstracts).
  - `judge/<sha1(canonical_query) + sha1(hypothesis_card_canonical)>.json`
    — LLM-scored novelty verdict (see §4).  Separate key because the
    same query may serve multiple hypothesis cards with different
    contextual claims, and we don't want to re-judge unchanged
    (query, hypothesis) pairs.
- **Canonical query**: `(sorted_variables, primary_outcome, edge_type,
  optional_domain_hint)` joined with `|`.  Order-independence
  guarantees deterministic cache hits.

Memory-only / no-cache / 7-day-TTL alternatives were rejected: they
either lose state on restart, fail under rate limits, or evict useful
results within a single project's lifetime.

## 3. Offline / private-net / CI: visible skip, do not block

The framework must run in air-gapped CI and private deployments.

- When all backends time out / refuse / return network errors, the
  litcheck client returns `LitcheckResult(score=None, hits=[],
  reason="offline")` instead of raising.
- **N3 does NOT block on `score=None`**.  The hypothesis card is
  emitted with `metadata.novelty_check = "skipped"` and a human-visible
  reason.  Reviewers and the review_stage see this metadata and can
  decide post-hoc whether to flag the run as un-vetted.
- Fail-closed (block hypotheses without novelty proof) was rejected:
  it makes the framework un-runnable in the most common deployment
  shape (private network), and CI tests would be impossible to pass.
- A silent default `score=0.5` was rejected: it pollutes metrics and
  hides the offline state from downstream consumers.

## 4. Novelty scoring: LLM judge over retrieved abstracts

A simple hit-count threshold (0 hits → novel, ≥10 → saturated) was
considered as the v1 policy.  We rejected it: hit count conflates
"this exact pair was studied" with "papers mentioning these terms in
unrelated contexts", and produces poor signal in either direction
(high hit count on a generic outcome variable; zero hit count on a
genuinely studied pairing whose papers used different wording).

The accepted v1 policy: **let the LLM judge**.

- After retrieval returns up to **top-5 hits** (title + abstract,
  abstract truncated to 500 chars each), the litcheck client builds a
  prompt: hypothesis card text + retrieved abstracts + a fixed rubric
  asking for `(novelty_score: 0..1, summary: 1-2 sentences,
  closest_prior_work: optional doi)`.
- LLM is the same `LLMClient` used elsewhere; calls go through the
  Tier-1 transient retry layer (ADR-0002).
- Output is parsed via `chat_json` and cached at the
  `judge/<query+hypothesis>.json` key (see §2).
- When retrieval returns 0 hits, the LLM is **still called** with the
  hypothesis card alone and a prompt clarifying "no prior literature
  retrieved".  The LLM typically returns score≈1.0 with an explicit
  caveat about retrieval coverage; we keep the call so reviewers see a
  uniform shape regardless of hit count.

Cost envelope: ~5 abstract × 500 chars + hypothesis card + rubric ≈
3-4k tokens per hypothesis.  At one judge call per N3 hypothesis card,
budget impact is bounded by hypothesis count, not run length.

Embedding-distance and hit-count alternatives can be added later as
parallel features the LLM consults; the architecture doesn't change.

## 5. PII / compliance: strict outbound

Public APIs receive only structured signals, never raw user prose.

- Outbound query is constructed from `variables`, `primary_outcome`,
  optional `edge_type`, and an optional `domain_hint` keyword
  (e.g. "autism", "breast cancer") that comes from N2's profile, not
  from the user's task string.
- Raw user `task` and any free-form clinical notes are explicitly NOT
  forwarded.
- Internal dataset hints (filenames, dataset_hash, cohort labels) are
  NOT forwarded.
- A configurable allow-list governs which `domain_hint` keywords are
  considered safe to send; anything outside is dropped silently and
  logged.

Lax mode (forward task verbatim) was rejected: any deployment with
patient-derived data would have to disable litcheck entirely otherwise.
Strict mode is the default; a "lax" toggle can be added later for
fully-public benchmark workloads if measured precision improvement
justifies it.

## 6. Implementation surface (informational)

- New module `litcheck/` under `discovery/` with submodules
  `client.py` (S2 + PubMed transports), `cache.py` (disk + TTL),
  `judge.py` (LLM scoring), `__init__.py` exports `litcheck_query`.
- `litcheck_stub.py` becomes a thin shim that calls into
  `litcheck.litcheck_query` if backends are configured, else returns
  the offline result described in §3.
- N3 gains a metadata field `novelty_check ∈ {ok, skipped}` and N5
  review_stage gains a soft check that flags `novelty_check=skipped`
  finds for human attention.
