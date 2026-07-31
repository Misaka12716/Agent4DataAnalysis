# Per-operator evidence extraction in verify_stage

**Status**: accepted (2026-06-07)

`verify_stage._extract_numbers` walked every operator's result dict
with a flat `_EFFECT_KEYS` / `_P_KEYS` / `_N_KEYS` lookup, taking the
**first matching key** as "the answer".  This produced two real bugs
on bio workloads:

- **Cross-operator semantic mixing.**  When `limma_deg_two_group` and
  `pathway_enrichment_fisher` both ran in one verify pipeline, the
  extracted `(effect, p)` pair could come from different operators
  with different statistical meanings — `effect=logFC of one gene`
  paired with `p=hypergeometric pvalue of one pathway`, presented to
  `review_stage` as if they were jointly the answer to one hypothesis.
- **Order-dependent extraction.**  First-match-wins depended on dict
  iteration order, operator execution order, and `_KEYS` dictionary
  iteration order.  Re-running the same hypothesis could yield
  different `(effect, p)` triples.

This ADR replaces the flat scan with **per-operator evidence
extraction owned inside `verify_stage`**, keeps the existing
single-value `VerifyResult.effect / p / n` interface backwards
compatible, and adds a structured `evidence_per_operator` list for
future review-stage upgrades.

## 1. Per-operator extractor table (in verify_stage, not on operators)

```python
# verify_stage._extractors.py
@dataclass
class Evidence:
    source_operator: str       # operator id, e.g. "limma_deg_two_group"
    effect: Optional[float]
    effect_kind: Optional[str] # "logFC", "fold_enrichment", "OR", ...
    p: Optional[float]
    p_kind: Optional[str]      # "raw_p", "adj_p", "permutation_p", ...
    n: Optional[int]
    n_kind: Optional[str]      # "n_target", "n_overlap", "n_total", ...
    raw: Dict[str, Any]        # whole operator result, kept for review

_OPERATOR_EXTRACTORS: Dict[str, Callable[[Dict[str, Any]], Evidence]] = {
    "limma_deg_two_group":          _extract_limma_deg,
    "probe_deg_collapse_to_gene":   _extract_collapse_passthrough,
    "pathway_enrichment_fisher":    _extract_pathway_fisher,
    # ... one entry per known bio operator ...
}
```

Each extractor knows which keys mean which thing for *that* operator.
For example, `_extract_pathway_fisher` prefers `adj_pvalue` over `pvalue`
and reports `effect_kind="fold_enrichment"`.  `_extract_limma_deg` knows
`adj.P.Val` is BH-corrected and `P.Value` is raw, and reports both via
`p_kind`.

## 2. Fallback for unknown operators

Operators not in `_OPERATOR_EXTRACTORS` (any non-bio operator, third
party additions, the `__coder__` placeholder) fall back to the existing
flat-scan logic with the existing `_EFFECT_KEYS / _P_KEYS / _N_KEYS`
dictionaries.  The fallback is intentionally preserved so:

- Non-bio workflows that worked before continue to work bit-for-bit.
- New operators don't crash verify_stage; they just get the
  best-effort first-match treatment until someone adds a per-operator
  entry.

The fallback is **per-operator** in the sense that it walks one
operator's result dict at a time and produces one Evidence per
operator, instead of merging across operators.  This alone fixes the
cross-operator semantic mixing bug — even without an explicit
extractor table entry, evidence from `limma_deg` and `pathway_fisher`
are now separate Evidence rows.

## 3. VerifyResult shape: backwards compatible + new list

```python
@dataclass
class VerifyResult:
    # existing fields, unchanged shape:
    effect: Optional[float]
    p: Optional[float]
    n: Optional[int]
    n_significant: int
    n_strong: int
    # ... existing flags ...

    # new field:
    evidence_per_operator: List[Evidence] = field(default_factory=list)
```

- Existing single-value `effect / p / n` is filled from the **primary
  operator's** evidence.  Primary is determined in this order:
  1. Operator marked `primary=true` in `plan.operator_steps` (planner
     can decide; today it doesn't, so this slot is reserved).
  2. The first Evidence with all three of `effect / p / n` populated.
  3. The first Evidence with at least `p` populated (matches today's
     fallback behaviour).
  4. None of the above → `effect / p / n` stay `None` (current
     behaviour for "operator returned nothing useful").
- `evidence_per_operator` carries every operator's contribution in
  plan order, untouched by the primary-pick logic.

`review_stage` continues to read single-value fields for its
`pass/fail` verdict in v1.  A future ADR can extend it to consult
`evidence_per_operator` for multi-evidence verdicts (e.g. require BOTH
gene-level AND pathway-level significance).

## 4. Hypothesis card: not changed

`Hypothesis` carries the *claim* (variables, primary_outcome,
edge_type, rationale).  Evidence is the verify-stage product about
that claim.  Evidence does not belong on the hypothesis card — adding
it would require shoehorning "before-verify" and "after-verify" card
shapes into one schema.

## 5. Test coverage

- New probe `tests_e2e/_eval_discovery/probe_verify_per_operator.py`
  constructs a synthetic 2-operator verify result (limma + pathway)
  and asserts:
  - `evidence_per_operator` has exactly 2 entries with the right
    `source_operator` ids.
  - `effect_kind` / `p_kind` / `n_kind` are populated correctly per
    operator.
  - Single-value `effect` / `p` come from the same operator (no cross
    mixing), per the primary-pick rules in §3.
- Existing `tests_e2e/v8_discovery_verify_refine_test.py` is **not**
  modified.  Tests that pass today should continue to pass; the new
  list field is additive.

## Considered and rejected

- **Operator self-report (`summary_evidence` field on operator
  output)**: cleanest design but requires touching every bio operator
  and breaks the operator output contract.  This ADR keeps a path open
  for a future migration to that design — the per-operator extractor
  table can be progressively replaced by reading `summary_evidence`
  from operators that opt in.
- **Priority table inside flat-scan logic**: treats the symptom
  (wrong key picked) without addressing the root cause (evidence from
  different operators getting merged).
- **No change**: the bug is observed in production (transcript shows
  a hypothesis getting `effect=logFC, p=pathway_p`).  Leaving it would
  contaminate review_stage verdicts on every multi-operator pipeline.
