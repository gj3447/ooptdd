# Public arrival-testing benchmark — specification (v0)

**Status: TIER 0 IMPLEMENTED / FINAL MEASUREMENT PENDING; TIER 1 NOT
MEASURED.** The deterministic runner, packaged frozen manifest/fixtures,
observation-first validator, JSON/JUnit/Markdown projections, and
same-definition positive/negative/restored protocol now live in
`src/ooptdd/benchmark.py`, `scripts/run_arrival_benchmark.py`, and
`src/ooptdd/benchmark_fixtures/arrival/v0/`. The v0 manifest fixes 20
repetitions and seed `20260723`; each sample receives a unique seed-derived
identity, while only scenarios with a meaningful factor vary that factor. Tier 0 remains mechanics-only, and code/CI
wiring is not a final measurement artifact or an external-store arrival claim.

## 1. Why a benchmark, and why this one is narrow

Trace-based testing has already died once as a category: `aspecto-io/malabi` is
dead (~26 months idle) and Tracetest's main line stalled with its Cloud EOL —
packfile-decoded evidence in
[`ooptdd_F_oss_absorption_20260722.md`](ooptdd_F_oss_absorption_20260722.md).
The post-mortem there names the design failure: **"timeout = fail"** — reading
ingestion lag as absence. A benchmark for this category is therefore not a
feature bake-off. It measures exactly the properties whose absence killed the
predecessors:

> Does the tool catch silent evidence loss, without manufacturing false REDs
> out of ingestion lag, while staying honest (three-valued) when it cannot
> observe at all — and do its gates actually discriminate?

The benchmark is a **CI-runnable harness with fixed, versioned scenarios**, not
a leaderboard product. It grades event-proof properties only (see §6).

## 2. Headline metrics

Four metrics, each tied to a shipped mechanism. The oracle for every scenario
run is an expected verdict from the LTL3 lattice (`present` ⊤ / `absent` ⊥ /
`inconclusive` ?) plus the CLI exit ladder (`cli._exit`: 0 GREEN, 1 RED,
2 INFRA — a not-clean read can never exit 0).

### M1 — Silent-loss catch rate

The founding incident, made a metric (`examples/openobserve_demo/README.md`:
a silent 401 dropped ingest for 22 hours while every "shipped OK" log line
lied). A fault injector suppresses the events satisfying one required gate
expectation — either by dropping them between shipper and store, or by the
auth-misconfiguration variant (`demo_silent_401.py`: fire-and-forget shipper
swallows the 401, the SUT self-reports success).

- **Measured**: fraction of injected-loss trials where the final verdict is
  ⊥ `absent` (exit 1). A GREEN here is a fake green.
- **Target**: 1.0.

### M2 — False-RED rate under induced ingestion lag, and late-offender catch under flap

Two wings, one per mechanism in `engine/verify.py::poll_until_present`.

**M2a (lag → blind-window guard).** A delaying fault point holds ingested
events for `T` ms, with `T` ≤ the backend's declared
`BackendCaps.query_visibility_delay_ms` (the OpenObserve driver declares
5000 ms). The poll budget (`retries`/`delay`) is deliberately set small enough
that a naive retry loop exhausts *inside* the blind window. The mechanism under
test: `poll_until_present` never concludes ABSENT while the total wait is still
inside the store's declared visibility window — it extends once, bounded by the
declaration, and re-reads (the source comment calls this the exact conflation
"that killed trace-based testing as a category").

- **Measured**: fraction of lag trials whose verdict is ⊥ `absent` although the
  events became queryable inside the declared window (a false RED).
- **Target**: 0.0.

**M2b (flap → `confirm_rounds` anti-flap).** A green that settles on the final
poll from a violation-free-so-far prefix is *revocable*: a late offender (e.g.
an ERROR record under a `forbid_errors` gate) can land right after the last
read. The fault point schedules the offender to arrive after the final read but
within `confirm_delay_s`. With `confirm_rounds ≥ 1`, `poll_until_present`
re-reads and any round that is no longer green wins.

- **Measured**: fraction of flap trials where the late offender flips the
  verdict, at `confirm_rounds = 1`.
- **Target**: 1.0 at `confirm_rounds = 1`.
- **Control wing**: the same trials at `confirm_rounds = 0` are run and
  reported. Missing the offender there is *expected* (a finite monitor stops
  observing when it settles) — the control documents that the mechanism is
  load-bearing; it is not a pass/fail criterion.

Wiring note: `confirm_rounds` is an API/config parameter
(`verify_gate`/`verify_trace`/`session_finish` keyword; pytest ini
`ooptdd_confirm_rounds`; `[tool.ooptdd] confirm_rounds` in `config.py`). The
`ooptdd verify` subcommand currently exposes only `--retries`/`--delay`, so the
benchmark runner drives the Python API directly.

### M3 — Inconclusive honesty under store outage

The store is made unreachable (container stopped, or an unroutable URL —
`demo_inconclusive.py`). The verdict must be ? `inconclusive` (exit 2), never
⊥ (exit 1): `verify_policy` never fails the build on inconclusive even in
strict mode, and `reports.to_junit_xml` renders INFRA as `<skipped>` (or
`<error type="ooptdd.inconclusive">` under the `--junit-inconclusive error`
policy) — never `<failure>`.

- **Measured**: fraction of outage trials producing ? with exit 2 *and* a JUnit
  artifact whose outage testcase is `<skipped>`/inconclusive, with zero
  `<failure>` elements. The expected ? may satisfy the benchmark oracle overall;
  it must not be rendered as an ordinary passing testcase.
- **Target**: 1.0.

### M4 — Mutation score of the frozen trajectory gate

The packaged v0 `trajectory-gate.yaml` and `trajectory-events.json` fixture pair
runs through `mutation_report`: derive semantic mutants from the gate's own
expectations and re-run the gate on each. Survivors are named blind spots.

- **Measured**: `score` (caught / total) per fixture gate; `survivors` listed
  verbatim; `canary_survived` (the drop-all canary — the gate run on an empty
  stream) must be `False`.
- **Pass floor**: every fixture gate must yield `n ≥ 1` derivable mutants and
  must not survive the canary. The expected score per fixture is pinned in the
  fixture manifest; deviation in either direction is a benchmark failure (a
  jump can mean the mutant set silently changed).

Two honesty constraints, stated so the metric cannot lie:

1. Generic drop mutants remain excluded for negative/trajectory predicates,
   because dropping a forbidden event is meaningless. Trajectory rules instead
   derive semantic rename, argument-corruption, required-call reorder,
   exact-mode extra-call, forbidden-tool, and forbidden-call witnesses. The v0
   trajectory fixture pins `eligible=5`, `score=1.0`, and
   `canary_survived=false`; `eligible=0` remains unmeasured, never a perfect score.
2. Ordering discrimination is now covered only when an ordered/exact trajectory
   has at least two required calls. Generic timestamp rewriting remains outside
   M4; it belongs to the separate OrderBreak scenario family in §8.

## 3. Mechanism conformance assertions

These are not gap-hunts — both mechanisms are implemented. The benchmark pins
them as regression instruments.

**C1 — Arrival stamp.** Every verdict that flows through `poll_until_present`
carries an `arrival` stamp: `{visibility_delay_ms, waited_ms, flushed,
extended_for_visibility, confirm_rounds_run}`. The benchmark asserts (a) the
stamp is present on every scenario verdict, and (b) no ⊥ `absent` verdict has
`waited_ms < visibility_delay_ms` when the store answered — the
never-ABSENT-inside-the-blind-window invariant, checked from the verdict's own
receipt rather than from harness timing.

**C2 — Independence wiring.** Two separate mechanisms, asserted separately
(they are commonly conflated):

- (a) `BackendCaps.independent` is declared `False` by the in-process `memory`
  and same-host `jsonl` drivers, and is consumed by the
  `require_independent_store` gate (spec key or `OOPTDD_REQUIRE_INDEPENDENT`):
  a green on a non-independent store with zero corroborated checks must come
  back RED with `dependent_store: true`. The benchmark asserts this demotion
  fires.
- (b) `evidence_tier`'s top rung `external_verdict` is reachable **only** via a
  passing separate-source `external:` probe check (oracle corroboration) — it
  does not follow from backend caps. The benchmark asserts a green with no
  probe corroboration never reports `external_verdict`, and one with it does.

## 4. Harness shape

### Tiers

- **Tier 0 — offline default (no infra).** `MemoryBackend`, injected `Clock`
  and `Sleeper` (both are constructor parameters of `poll_until_present`, so
  lag/flap timing is simulated deterministically; the lag wing uses a wrapper
  backend that declares a nonzero `query_visibility_delay_ms` and withholds
  events until the simulated deadline). Runs mechanics-only M1–M4 plus C1–C2
  deterministically in seconds. Tier 0 proves *gate mechanics*, not arrival —
  the memory driver itself says so (`independent=False`).
- **Tier 1 — external judge.** The docker-compose OpenObserve from
  `examples/openobserve_demo/docker-compose.yml` (image
  `public.ecr.aws/zinclabs/openobserve:v0.14.7`, port 5080, root credentials in
  the compose env). Runs M1, M2a (real ingest), M3 against a real store.
  **Headline numbers must come from Tier 1**; Tier-0-only results must be
  labeled as such (see §7). Tier 1 is currently **NOT MEASURED**: there is no
  candidate-bound credentialed external-store receipt and no controlled
  ingest-lag receipt.

### Fault injection points

| point | mechanism | drives |
|---|---|---|
| shipper auth | wrong `OOPTDD_OO_PASSWORD`; fire-and-forget shipper swallows the 401 (the `demo_silent_401.py` shape) | M1 |
| shipper drop | suppress emit calls for the events satisfying one required expectation | M1 |
| ingest lag | delaying proxy in front of `:5080` holding ingest POSTs for `T` ms (Tier 0: the withholding wrapper backend) | M2a |
| late offender | write the offending event after the gate's final poll, inside `confirm_delay_s` | M2b |
| store outage | `docker compose stop openobserve`, or an unroutable `OOPTDD_OO_URL` | M3 |

### Run protocol

Each scenario runs `R` repetitions (v0 default: 20) under a fixed seed
recorded in the result. For repeat indices `0..19`, the implementation derives
`variant_id = SHA256(seed, scenario_id, repeat)[:16]`; the v0 result therefore
contains 20 deterministic repetitions with unique seed-derived identities per
scenario, bound to the frozen manifest and fixture hashes. Lag, visibility,
confirmation, and probe parameters vary where meaningful; outage and mutation
also repeat invariant mechanics, so these are not 20 independent semantic
trials. The cases are a fixed robustness panel, not a random sample from a population. The runner is a Python script
driving `verify_gate` / `verify_trace` / `mutation_report` directly; no new CLI
surface is required. Every scenario's expected verdict is asserted by the
runner itself — the benchmark is itself a gate, the same convention the demo
scripts already use ("each script asserts its own expected verdict and exits 0
only when the demonstration held").

## 5. Scoring and reporting

**Canonical result** = one JSON document per run:

- `benchmark_version`, `fixture_version`, tool version, seed, tier;
- explicit `tier`/`independent` fields and per-sample `BackendCaps` snapshots
  (caps are the honesty surface, not fine print);
- per-scenario rows: expected vs observed verdict, oracle match, `arrival`
  stamp, and `pass_hat_k` over the fixed variant panel;
- `provenance.files` for fixture hashes plus `provenance.code_manifest` for the
  complete packaged Python surface; `benchmark_definition_sha256` binds both;
- metric rollups M1–M4 and conformance results C1–C2.

**Summary artifact: reuse `reports.to_junit_xml` — no new format.** The runner
projects the canonical JSON into the `evaluate()`-result shape (one check row
per scenario plus C1/C2 conformance rows; `passed` = scenario oracle held; the
run id as `cid`; tier identity in `oracle.emit_identity`) and renders it with
`to_junit_xml(result, suite="ooptdd.arrival-benchmark")`. The existing renderer
then provides for free: one `<testcase>` per projected check, `<failure>` on a
scenario or conformance miss, the suite `<properties>` naming cid and backend,
XML control-character hygiene, and the INFRA policy switch
(`inconclusive="skipped"` default, `"error"` for fail-closed CI). Two mapping
rules keep the artifact honest:

1. A store-outage scenario that correctly produced ? is an oracle **match**, so
   it can contribute to an overall benchmark pass. The projected check also
   retains `inconclusive: true`, so its JUnit testcase must be `<skipped
   message="INCONCLUSIVE: ...">`, never an ordinary pass and never `<failure>`.
   A failure of the harness itself (compose did not start, fixture missing) is a
   separate suite-level INFRA condition.
2. Writers stay pure projections: the JUnit artifact re-judges nothing; the
   verdicts come only from the engine. `to_markdown` may additionally render
   the same result for PR display.

**Pass criteria (v0):** M1 = 1.0 · M2a = 0.0 · M2b = 1.0 (at
`confirm_rounds=1`; the 0-round control is reported, not gated) · M3 = 1.0 ·
M4 per the fixture manifest with `canary_survived=False` and `n ≥ 1` · C1, C2
all assertions hold.

`pass_hat_k = C(c,k) / C(n,k)` answers one deliberately narrow question: how
consistent were the recorded outcomes across all-`k` subsets of the frozen,
seeded repetition panel? It
is **panel robustness**, not an estimate of population success, unseen-input
generalization, or production reliability. Even `pass_hat_k=1.0` cannot cross
that boundary.

## 6. What this benchmark deliberately does NOT measure

Per the what-not-to-do list in
[`docs/competitive_feedback.md`](../competitive_feedback.md) ("What not to
do"), the benchmark excludes, permanently:

- **LLM answer quality** and any LLM-as-judge scoring — DeepEval, Ragas,
  Phoenix, LangSmith, promptfoo, and OpenAI Evals own that space; an arrival
  benchmark that graded answer quality would be measuring a different claim.
- **Dashboards / leaderboard UI** — the deliverable is a CI-runnable harness
  and a JUnit/JSON artifact, nothing rendered.
- **Provider matrices** — no per-LLM-provider scenario axes.
- **Span-selector breadth** — no Tracetest-style selector-language coverage
  scoring; scenarios use flat event gates.
- **Red-team / attack coverage** — out of scope entirely.
- **Store ranking** — the benchmark does not rank observability backends by
  ingest speed; a store's declared visibility delay is taken as given and only
  its *honesty* (declaration vs behavior) is exercised.
- **OpenObserve as a requirement** — it is the reference Tier-1 judge because
  the repo ships its compose file; any queryable, independent backend that
  passes `backends/conformance.py` may substitute.

## 7. Gaming and validity threats

- **Tier gaming.** Running only Tier 0 and quoting the numbers as arrival proof
  is the exact self-judging the tool exists to catch. Mitigation: tier is a
  mandatory field in the canonical result, and C2(a) makes a
  non-independent-store green demote under `require_independent_store` — the
  benchmark turns that gate on for its Tier-1 headline runs.
- **Declared-delay inflation.** A driver could declare an absurd
  `query_visibility_delay_ms` so the blind-window guard extends past every
  induced loss. Mitigation: the harness caps wall-clock per scenario and
  reports budget-exceeded runs as harness failures (INFRA, not pass); the
  fixture pins the shipped OpenObserve driver's declared 5000 ms.
- **Fixture overfitting.** The fixture set is versioned; a result is only
  comparable at equal `fixture_version`.
- **Panel generalization.** The 20 repetitions have distinct deterministic
  identities, but some exercise the same invariant mechanic and all remain one
  frozen panel. `pass_hat_k` summarizes recorded panel consistency only; it supplies
  no confidence claim for an unseen workload distribution.
- **Runtime identity.** Byte-identical replay is claimed only for the frozen
  Python/PyYAML/platform identity in the measurement lock. The tested Python
  matrix is compatibility evidence, not a cross-environment bit-reproducibility claim.
- **Single-author bias.** Scenarios and oracles are defined tool-neutrally
  (verdict lattice + exit semantics), so another tool can run them through an
  adapter mapping its outcome to {⊤, ⊥, ?}. A tool with no third value must
  choose what an outage becomes — and that forced choice, documented per run,
  is itself a benchmark finding (it is the design failure §1 names).

## 8. Out of scope for v0 — candidate v1 scenarios

Kept out to hold the v0 metric set small; each has an implemented mechanism
that could be pinned later:

- **OrderBreak** — `must_order` violations including the reorder semantics
  around `tie_skew_ms` (OrderMonitor's timestamp-tie tolerance) and the
  ingest-order vs timestamp-order early-settle hazard already handled in
  `_settled_green`.
- **ShapeDrift** — ontology `conforms` REDs (wrong enum, missing required
  attribute).
- **Truncation honesty** — a paging read cut short (`fetch_all_pages` /
  `complete=False`) must land ?, not ⊥.
- **Throttle honoring** — the poll loop already honors a store-sent
  `Retry-After`; a conformance assertion could pin it.
- **gen_ai preset gates** as a second fixture family.

## References (all paths repo-relative)

- `src/ooptdd/engine/verify.py` — `poll_until_present` (blind-window guard,
  arrival stamp, `confirm_rounds` anti-flap), `verify_gate`, `verify_trace`,
  `verify_policy`.
- `src/ooptdd/engine/gate.py` — `evaluate_events`, `require_independent_store`
  / `dependent_store`, `evidence_tier` / `EVIDENCE_TIERS`.
- `src/ooptdd/domain/ports.py` — `BackendCaps` (`independent`,
  `query_visibility_delay_ms`, `samples`), `fetch_all_pages`.
- `src/ooptdd/mutation.py` — `derive_mutations` (exclusion list),
  `mutation_report` (score, survivors, drop-all canary).
- `src/ooptdd/benchmark.py` — deterministic scenarios, repeated reliability,
  raw-sample rollup recomputation, packaged fixture/code-manifest binding, and
  report projections.
- `scripts/run_arrival_benchmark.py` — Tier-0 CLI artifact emitter.
- `scripts/run_efficacy_measurement.py` — prospectively locked
  positive/negative/restored sequence.
- `src/ooptdd/benchmark_fixtures/arrival/v0/` — packaged, versioned manifest
  and trajectory fixtures; explicit filesystem fixture roots remain supported.
- `src/ooptdd/cli.py` — `_exit` ladder, `_cmd_mutate` exit-2 rungs,
  `--report junit|md`, `--junit-inconclusive`.
- `src/ooptdd/reports.py` — `to_junit_xml` (INFRA-never-failure, suite
  properties), `to_markdown`.
- `tests/test_arrival_benchmark.py` — deterministic replay, seeded variants,
  fault localization, integrity rejection, and outage/JUnit projection guards.
- `examples/openobserve_demo/` — compose file and the three verdict demos.
- `docs/competitive_feedback.md` — the what-not-to-do perimeter.
- `docs/research/ooptdd_F_oss_absorption_20260722.md` — malabi/Tracetest
  category-death evidence (packfile-decoded).
