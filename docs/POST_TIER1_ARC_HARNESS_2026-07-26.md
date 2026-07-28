# Post-Tier-1 advancement arc — harness

> Tracking issue: https://github.com/gj3447/ooptdd/issues/6
> Date: 2026-07-26 · Actor: Kimi Code CLI · Status: IN EXECUTION (2026-07-28 user
> proceed signal — "ooptdd 고도화 진행"; A9 policy verdict still pending as its own item)
> Consolidates: `prom-ooptdd-full-synthesis-2026-07-24` §5–6, `prom-next-move-2026-07-24`
> KG anchor: `ooptdd-post-tier1-arc-2026-07-26`

What already landed on `main`: v0.5.0 absorption arc → trajectory bridges (PR #5) →
Tier-1 MEASURED (2026-07-24: silent-loss catch 40/40, lag false-RED 0/20, outage
inconclusive 20/20, restore 20/20; single backend/version, 20-rep panel) → Tier-1
scheduled-regression CI on ci-runner-01 (07-25). This document is the execution
harness for everything that remains open.

Each front states: goal, concrete artifact, acceptance gate (arrival-asserted where
possible), closing receipt's evidence_tier, KG anchor, decision owner.

## Harness rules (all fronts)

1. Preregistration before measurement — metric, baseline, threshold, and script sha
   locked before any positive run (LakatoTree `register_prediction` discipline).
2. Negative oracle per front: same spec sha, deliberately broken expectation must
   go RED, then restore.
3. Receipts land in `docs/receipts/` and carry an explicit `evidence_tier`
   (`local_pass < emitted < arrived < queryable_causal < external_verdict`).
   The name "ooptdd receipt" guarantees nothing — the tier is read, not counted.
4. No unmeasured claims in status lines. A RED expected in advance is an honesty
   result, not a failure of the arc.
5. On front close: update the tracking-issue checkbox, the LakatoTree question /
   verdict, and the KG front node in the same commit message trail.

---

## A1. Re-verify `lakatotree-full-ooptdd-green` (receipt rerun)

- **Goal:** the single unreceipted green in `LakatosTree_OOPTDD_20260616`
  (`provenance.inconclusive_progress`, currently excluded from progress counts)
  becomes receipted — or is corrected to its honest verdict.
- **Artifact:** rerun transcript + receipt under `docs/receipts/`; LakatoTree node
  `verdict_source` set.
- **Acceptance:** `tree_metrics(LakatosTree_OOPTDD_20260616)` shows
  `provenance.inconclusive_progress == []` and `count == 0`.
- **Receipt tier:** `queryable_causal` (tree re-fold, not self-report).
- **KG anchor:** `LakatosTree_OOPTDD_20260616` · provenance alert.
- **Owner:** agent.

## A2. D3 — pin `mark-ooptdd-loop`'s declared lag as a BEHIND-drift gate

- **Goal:** the intentional pre-hardening lag of the `mark-ooptdd-loop` snapshot
  (07-15, missing the 4 hardening commits) stops being a doc note and becomes a
  gate: pinned sha allowlist, RED on any *undeclared* further drift.
- **Artifact:** drift test adjacent to the existing vendor drift trio
  (tamper / structural / BEHIND) in the consuming repo
  (`SYMPOSIUM/GIT/mark-ooptdd-loop`, cross-rail).
- **Acceptance:** test GREEN at the pinned sha set; deleting one hardening commit's
  worth of content from the pin set goes RED (negative oracle).
- **Receipt tier:** `local_pass` (gate mechanics) — no external store involved.
- **KG anchor:** synthesis D3 · `mark-ooptdd-loop` snapshot lineage.
- **Owner:** agent.

## A3. v2.8(b) — winner's-curse lock (RED expected)

- **Goal:** lock the mutation-benchmark winner selection against re-picking after
  peeking at results (winner's curse). Current measurements (4/5 negative) say the
  lock goes RED immediately — that is the *point*: claim shrinks to honest size.
- **Artifact:** prereg (thresholds + script sha, locked first), lock mechanism in
  the benchmark runner, honest status-line updates.
- **Acceptance:** prereg receipt exists *before* the run; post-lock score reported
  exactly as measured; status lines claim no more than the locked result.
- **Receipt tier:** `arrived` minimum (external-store arrival of the audit trail).
- **KG anchor:** `prom-next-move-2026-07-24` front C(b).
- **Owner:** agent.

## A4. v2.8(a) — nDCG infra gate for the audit pipeline

- **Goal:** infra-level gate computing nDCG over ranked mutation kills, raising the
  audit integrity grade.
- **Artifact:** gate spec (drafted at front start — SP phase), implementation,
  wiring into the mutation-audit pipeline.
- **Acceptance:** gate consumes audit output; permuting the kill ranking goes RED;
  pipeline refuses unaudited rankings.
- **Receipt tier:** `local_pass` + negative-oracle pair.
- **KG anchor:** `prom-next-move-2026-07-24` front C(a).
- **Owner:** agent.
- **CLOSED 2026-07-28 (premise corrected):** "ranked mutation kills" had no referent —
  `mutation_report` returns an unranked derivation-order list (this arc's 4th stale
  premise; the source line, `prom-next-move` C(a), was six words with no ranking
  definition, and nDCG's actual corpus referent is the HSWM retrieval metric). Second
  correction, mathematical: nDCG is invariant under equal-relevance permutations —
  measured over all 24 permutations of the all-killed `[1,1,1,1]` list: `{1.0}` — so
  on the committed all-killed benchmark artifact the "permuting goes RED" acceptance is
  unsatisfiable by nDCG alone. Honest reinterpretation implemented as `ooptdd
  audit-rank` (`ndcg`/`ranked_kills`/`verify_audit_ranking` in `src/ooptdd/mutation.py`):
  ranking authority = derivation order authenticated by per-row `mutation_id`
  re-derivation (this layer, not nDCG, is the integrity defence and satisfies
  "refuses unaudited rankings" — exit 2 before any number); relevance = measured kill
  status only; `--min-ndcg` defaults to 1.0. Prereg fixed before any positive run
  (sha `a967da70…`, 2026-07-28T01:10:32Z). Measured: canonical ranking ndcg=1.0 exit 0;
  survivor-above-kill permutation ndcg=0.63093 (=1/log2(3)) **exit 1**, restored to
  exit 0; permuted report rows **exit 2** `RANKING REFUSED` (both the mixed fixture and
  the committed A3 artifact). A3's lock is reused as the spec sha pin, per the
  fresh-state note below. Docs: `docs/ci_mutation_gate.md` §"The nDCG ranking gate".

## A5. HSWM v21 residuals (cross-rail: `/Users/lagyeongjun/CD/HSWM`)

- **Goal:** mutation audit 8/12 → 12/12. Two items: binop@92 dReLU gate-corruption
  mutant still unkilled; side finding `weight_field` 0/11 — source binding is not
  actual coverage, so a battery that measures the real cover.
- **Artifact:** kill test for binop@92; `weight_field` battery (11 cells or
  declared exclusions); HSWM v22 audit record.
- **Acceptance:** HSWM mutation audit reports 12/12 kills and `weight_field`
  coverage 11/11 (or each exclusion named with reason).
- **Receipt tier:** `local_pass` per cell + audit chain receipt.
- **KG anchor:** HSWM v21 mutation audit (synthesis C7 / §5.3).
- **Owner:** agent.

## A6. Adoption attestation sweep (TOOL_ADOPTION_HARNESS G07–G09)

- **Goal:** close the measurement-vs-usage gap (D4): real consumers
  (lakatotree, HSWM, 333, SQCEDIT, web_back) already run ooptdd, but no attestation
  receipts exist, so the adoption profile sits at DEFINED 56.00% / 49.25%.
- **Artifact:** per-consumer attestation receipts; refreshed
  `TOOL_ADOPTION_HARNESS` scores (SYMPOSIUM rail).
- **Acceptance:** G07–G09 re-evaluated with attestation evidence attached; the new
  percentage is computed from receipts, not asserted.
- **Receipt tier:** `arrived` (attestations are external-to-ooptdd events).
- **KG anchor:** synthesis D4 / §6.4.
- **Owner:** agent.

## A7. Adjudicate `backend-abstraction-port` (abandon candidate)

- **Goal:** the branch sits at Laudan problem-balance −2 (abandon candidate alert).
  Either abandon it (close branch, update coverage backlog honestly) or revive it
  with opened questions that have positive expected gain.
- **Artifact:** LakatoTree stack quorum record (Popper/Bayes/Laudan, 2/3);
  branch verdict; updated coverage statement.
- **Acceptance:** `tree_metrics` no longer lists the branch as an abandon candidate
  — because it was abandoned with a record, or because revived questions are open
  with gain > cost.
- **Receipt tier:** `queryable_causal`.
- **KG anchor:** `LakatosTree_OOPTDD_20260616` · laudan.abandon_candidates.
- **Owner:** agent proposes; verdict via stack quorum.

## A8. Ontology tree: close OQ×3, scope V4

- **Goal:** `LakatosTree_ooptdd_ontology_20260616` — close the three open questions
  (`longinus-ontology-compose`, `closed-vs-open-world`, `minimal-eventtype-schema`)
  with receipts; scope (not execute) V4 inference.
- **Artifact:** one receipt per OQ; V4 span plan appended to the tree's coverage
  backlog.
- **Acceptance:** `tree_metrics` frontier `open == 0` with `close_ratio_receipted`
  honestly reported; V4 appears as scoped backlog, not as started work.
- **Receipt tier:** `queryable_causal`.
- **KG anchor:** `LakatosTree_ooptdd_ontology_20260616` · synthesis §5.4.
- **Owner:** agent.

## A9. v2 auditor-budget policy (USER VERDICT REQUIRED)

- **Goal:** the receipt v2 schema already carries `auditor_id` / `budget` fields but
  no policy: who audits receipts, on what rotation, with what calibration, plus
  cross-machine chain-head anchoring into LakatoTree verdict receipts.
- **Draft default (proposal, user decides):**
  - *assignment:* every `arrived`-or-higher receipt gets an auditor ≠ its author
    (no self-vouch); `local_pass` receipts are audit-sampled at 1-in-5.
  - *rotation:* per-arc rotation; an auditor may not audit two consecutive arcs by
    the same author.
  - *budget:* max unaudited window of 7 days — receipts older than that without an
    auditor are auto-demoted one evidence tier until audited.
  - *calibration:* auditor verdicts themselves spot-checked 1-in-10 by a second
    auditor; disagreement opens an OpenQuestion instead of silently resolving.
  - *anchoring:* LakatoTree verdict receipts record the chain head of the machine
    that produced them; cross-machine receipts must reference the remote head.
- **Acceptance:** policy doc merged; schema validator enforces `auditor_id` +
  `budget` on covered receipts; first audited cycle produces a receipt.
- **Receipt tier:** `external_verdict` (that is the point of the policy).
- **KG anchor:** OpenQuestion `ooptdd-v2-auditor-budget`.
- **Owner:** **user verdict on the policy; agent implements.**
- **CLOSED 2026-07-28 (premise corrected):** the asked-for policy already existed
  when this front was drafted — v2.6 `audit_policy` (GIT/HSWM `ooptdd/audit_policy.py`,
  `OOPTDD_AUDIT_POLICY_2026-07-24.md`, 17/17 tests) closed KG `ooptdd-v2-auditor-budget`
  on 2026-07-24, two days before this doc. R1 no-self-audit (hard) / R2 rotation
  (advisory, deterministic) / R3 budget calibration (hard for structured budgets)
  are live. User ratified the draft default 2026-07-28; deltas between this draft
  and the implemented v2.6 stay as named residuals for an HSWM-rail session:
  (a) 7-day unaudited auto-demotion window, (b) 1-in-10 auditor-verdict spot-check,
  (c) cross-machine chain-head anchoring into LakatoTree verdict receipts.

---

## Recommended order

A1 → A2 (cheap honesty first) → A3 (RED-expected honesty) → A5 → A4 → A6 →
A7 → A8. A9 is drafted above and waits on user verdict — it blocks nothing.

## Declared backlog (explicitly not in this arc)

- mutmut interpretation un-green: 49.19% on mutmut 3.6.0; 2.5.1 rerun 121k mutants
  / 124s. Hardening backlog.
- Public deploy blockers ×3: DGX OAuth issuer absent; wildcard certificate expired;
  Neo4j Community cannot separate principals (public URL 404 persists).

## Ops note

The Tier-1 scheduled-regression target `openobserve-01` (192.168.0.27:5081,
Proxmox) is unreachable while the home `.0/24` boxes are powered off
(2026-07-26 network upheaval). Expect scheduled CI failure until infra returns —
an infra condition, not a code defect.

<!-- KG: ooptdd-post-tier1-arc-2026-07-26 -->
