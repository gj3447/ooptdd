# Weakness resolution plan

Companion to `competitive_feedback.md`. That document diagnosed; this one prescribes.
Each item states the weakness, the fix, the concrete artifact, and the acceptance
gate (how we know it is closed — arrival-asserted where possible, of course).

Status audit (2026-07-22): roughly half the original roadmap is already closed in
code — `gen_ai` semconv preset (agent vocabulary), `where`/`must_order`/`trajectory`/
`within_s` gate grammar (selector subset), backend registry + `BackendCaps`
(clickhouse / victorialogs / otel / jsonl), ontology drift classes, mutation
scoring. What remains is mostly *proof, documentation, and integration* work.

---

## 1. Positioning docs — ✅ LANDED 2026-07-22

**Weakness:** `memory` default makes ooptdd look like an in-process log-assert
helper; "does it need OpenObserve?" confusion; no backend capability matrix.

**Fix:**
- README one-liner (verbatim from competitive_feedback):
  > ooptdd is pytest-native positive-arrival testing for structured logs and
  > traces: write the expected event contract, run the system, and verify the
  > event arrived in an independent store.
- Document the proof-strength split explicitly:
  `memory` proves gate *mechanics* · external stores (OpenObserve/ClickHouse/
  VictoriaLogs) prove *arrival* · `otel` proves portable *writing* only.
- **Backend capability matrix, generated from code.** Do not hand-write the
  table: emit it from each driver's `BackendCaps` (`scripts/gen_backend_matrix.py`
  → `docs/backends.md`), and pin it with a test that regenerates and diffs.
  A hand-written matrix would itself be an uncorroborated claim — the exact
  failure mode this library exists to kill.

**Acceptance:** test `test_docs_backend_matrix_current` RED when a driver's caps
change without regenerating the doc.

## 2. The founding incident as a runnable demo — ✅ LANDED 2026-07-22

Verified against a live OpenObserve container: all three demos asserted their
expected verdicts (present / absent / inconclusive). Acceptance honesty (grill
2026-07-22): the "CI job keeps the trio green" half was NOT met at first — a
`demos` job (compose + the trio + the live parity wing) is now in ci.yml; its
first hosted run postdates this note.

**Weakness:** the strongest argument for ooptdd — "a silent 401 dropped ingest
for 22 hours and every 'shipped OK' log lied" — exists only as a docstring
anecdote. Tracetest/Phoenix have demos; we have a story.

**Fix:** `examples/openobserve_demo/`:
- `docker-compose.yml` — OpenObserve single node.
- `demo_green.py` — emit → gate → PRESENT (the happy path).
- `demo_silent_401.py` — misconfigured ingest token; `ship()` returns fine
  ("shipped OK"), verifier reads back → ABSENT → RED. The founding incident,
  reproduced on demand in <60 seconds.
- `demo_inconclusive.py` — store stopped; verdict INCONCLUSIVE, not RED —
  demonstrating why demoting "couldn't observe" to "falsified" is wrong.
- `docs/warn_to_strict.md` — migration guide (observe-only → enforcing), with
  the health-check preflight to run before flipping strict.

**Acceptance:** each demo script asserts its own expected verdict (the demo is
itself a gate); a CI job (compose-enabled runner) keeps the trio green.

## 3. CI credibility artifacts — ✅ LANDED 2026-07-22 (`ooptdd gate --report junit|md`)

Acceptance honesty (grill 2026-07-22): tests are structural (ElementTree
parse + counts + properties), NOT golden-file or XSD-validated — the original
acceptance line overpromised; GitHub's JUnit consumer is schema-lenient, and
the real risk (verdict↔report divergence: pending→failure, suite-level RED
invisible, threshold-GREEN shown red) is now pinned directly by tests instead.

**Weakness:** verdicts die in the terminal; "looks less battle-tested."

**Fix:** `ooptdd gate --format json|md|junit`. The `_emit` plumbing already
exists (mutation report uses it); add serializers:
- JUnit XML: one `<testcase>` per check, `<failure>` carries the offender
  events + backend identity + cid — so any CI (GitLab/GitHub/Jenkins) renders
  gate results natively with zero integration work.
- Markdown: the human PR-comment form; include the correlation id and a
  ready-to-paste backend query so a reviewer can independently re-verify
  (generator ≠ verifier extends to the human reviewer).

**Acceptance:** golden-file tests per format; JUnit output validated against the
schema consumed by GitHub's test summary.

## 4. Compose with eval platforms, don't compete — ✅ LANDED 2026-07-22 (`ooptdd.integrations`)

Acceptance status: deepeval bridge verified against REAL deepeval v4.0.7
(evaluate() loop end-to-end, 2026-07-22 grill); worked examples exist for all
three adapters under examples/integrations/.

**Weakness:** DeepEval/Ragas/Phoenix/LangSmith have rich agent-quality metrics;
ooptdd should not rebuild them ("What not to do") but currently offers no bridge,
so users must choose.

**Fix — three thin, import-guarded adapters (zero new hard deps):**
- `ooptdd.integrations.deepeval`: a DeepEval custom metric (`ArrivalMetric`)
  whose `measure()` runs an ooptdd gate — LLM-judge metrics and arrival proof
  in one DeepEval test case.
- promptfoo: a documented `defaultTest.assert` command hook that shells
  `ooptdd gate --format json` and parses the verdict.
- OTel export: `ooptdd.verdict` event (+ span attributes) emitted after each
  gate run, so Phoenix/LangSmith display arrival verdicts inline with traces —
  competitors become distribution.

**Acceptance:** one worked example per adapter under `examples/integrations/`,
each runnable with the memory backend (no external account required).

## 5. Adoption story — ✅ LANDED 2026-07-22, REWRITTEN wiring-accurate 2026-07-22

Acceptance honesty (grill 2026-07-22): the first version overclaimed
("receipts resident in CI" was true for 1 of 3 cases; "every CI run / strict"
contradicted the consumer's opt-in/warn-default wiring). The doc now states
per-case where the receipt actually runs (blocking CI / local opt-in gate /
manual harness), and the "internal CI job id" acceptance line is dropped for a
public repo — the per-case wiring statement replaces it.

**Weakness:** "no public benchmark or adoption story."

**Fix:** `docs/case_studies.md`, anonymized from real internal consumers:
- a 3,000+-test industrial-inspection suite whose pytest sessions ship LTDD
  receipts and positively verify arrival on every CI run;
- a research-tree engine whose rebuild pipeline treats logs as ground truth
  (`rebuild_start → step_exec×N → metric_compare → rebuild_verdict`);
- a Rust substrate emitting the ooptdd envelope, judged by the Python verifier —
  generator≠verifier across a language boundary.
The discipline: a receipt that only runs in its author's session is not
adoption — every case cited must be a receipt resident in CI.

**Acceptance:** each case study links the (internal) CI job id; external readers
see the shape, internal readers can audit the claim.

## 6. Agent-trajectory vocabulary absorption — ✅ LANDED 2026-07-22 (`engine/trajectory.py`)

**Weakness:** adjacent tools ship task-completion / tool-correctness / path-
convergence metrics; ooptdd verifies events but has no first-class trajectory
vocabulary beyond `trajectory:` ordering.

**Fix:** absorb the *deterministic* subset as gate predicates (the check
registry is the seam — no engine edits), keep LLM-judge metrics on the other
side of the §4 bridge. Licensing: concepts and published attribute names only,
implementations original — this repo is AGPL-3.0; no code is copied from
Apache/ELv2 sources. See `docs/research/` for the absorption analysis and the
`agent_trajectory` module for what landed.

**Acceptance:** predicate tests + a RED/GREEN example pair per absorbed metric
(wrong tool called → RED; forbidden tool absent → GREEN; …).

## Deliberately not fixed

UI/dashboard/trace viewer, red-team generation, LLM-as-judge scoring: excluded
by design (`competitive_feedback.md` "What not to do"). The small engine is the
product advantage; these would blunt it.
