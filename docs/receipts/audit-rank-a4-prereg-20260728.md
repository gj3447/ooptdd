# A4 preregistration — nDCG audit-ranking gate (fixed BEFORE any positive run)

Date: 2026-07-28. Front: A4 (POST_TIER1_ARC). Repo: /Users/lagyeongjun/CD/ooptdd @ e8976f7.

## Premise correction (recon, confirmed by direct read before this prereg)
- "ranked mutation kills" has no referent in the code: `mutation_report` returns an
  unranked, unweighted derivation-order list (src/ooptdd/mutation.py:403-447). grep of
  src/tests/examples for ndcg|rank: 0 hits.
- nDCG is invariant under permutations within equal relevance grades. The committed
  benchmark artifact (benchmarks/mutation/v0/order_pipeline.locked_result.json) is
  all-killed 4/4, so NO permutation of it can change nDCG. The acceptance "permuting
  the kill ranking goes RED" therefore REQUIRES a separate order-authority layer
  (mutation_id sequence re-derivation), not nDCG alone.

## Honest reinterpretation (fixed now)
- canonical ranking authority = derive_mutations(events, spec) derivation order,
  authenticated per-row by recomputing _mutation_id(label, mutant_events).
- relevance = measured kill status only: caught -> 1, survived -> 0. No invented grades.
- ranked-kill view = stable sort by (caught desc, canonical position asc).

## Metric (fixed)
nDCG with textbook log2 discount: DCG = sum(rel_i / log2(i+2)), i 0-based over the
published order; IDCG = DCG of relevances sorted descending. IDCG == 0 -> UNDEFINED
(None), mapped to refusal exit 2 — never filled with 0.0 or 1.0.

## Threshold (fixed)
min_ndcg = 1.0 (CLI default). Exit ladder (matches repo convention):
- exit 2 = refusal, no number produced: lock schema/sha mismatch; report not a dict;
  baseline_green false; canary_survived true; n==0 or score_status != "measured";
  missing/forged/permuted mutation_id sequence vs re-derivation; published ranking not
  a permutation of the audited rows; IDCG == 0.
- exit 1 = measured RED: authenticated ranking with ndcg < min_ndcg (only reachable
  when relevances are mixed, i.e. order_sensitive true).
- exit 0 = measured GREEN: authenticated ranking with ndcg >= min_ndcg.

## Preregistered expectations (before running anything)
- E1 (GREEN): mixed-relevance fixture (1 kill + 2 survivors, kill first in derivation
  order), canonical ranking -> ndcg 1.0, exit 0.
- E2 (negative oracle A, measured RED): same artifact, published ranking that promotes
  a survivor above the kill -> ndcg = 1/log2(3) ≈ 0.630930 < 1.0, exit 1.
- E3 (negative oracle B, refusal): permuted report rows (id sequence broken) -> exit 2,
  "RANKING REFUSED".
- E4 (honesty case): permuting the all-killed committed benchmark report -> nDCG value
  would be unchanged (1.0), but the id-sequence layer refuses (exit 2). This measures
  the nDCG limit instead of hiding it.
- E5: all-survivor report (IDCG 0) -> exit 2. E6: n==0 / unmeasured report -> exit 2.

## Verification commands (fixed)
.venv/bin/pytest -q tests/test_mutation_ndcg.py tests/test_mutation.py tests/test_mutation_lock.py
.venv/bin/pytest -q            # full suite, baseline 795± GREEN must hold
.venv/bin/ruff check src/ tests/ examples/
