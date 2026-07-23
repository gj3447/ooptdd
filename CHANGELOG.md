# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **ClickHouse / VictoriaLogs typed read surfaces.** CH synthesizes an opaque
  decimal-offset cursor over `LIMIT n OFFSET k` (walkable by `fetch_all_pages`, `_seq`
  stays the global position); VL is **limit-only** by design — LogsQL has no paging
  primitive, so a cursor is refused loudly instead of faked and a filled limit reports
  `complete=False`. Both delegate byte-identically to `query()` when the spec carries
  neither limit nor cursor.
- **OpenLLMetry bridge (`ooptdd.integrations.openllmetry`).** Encodes a verified
  finding rather than a hope: an OpenLLMetry-instrumented app is *not* gate-ready for
  free — it names tools `traceloop.entity.name` (never `gen_ai.tool.name`) — so
  `span_to_event` / `spans_to_events` map its spans onto `gen_ai.*` events, carrying
  the `gen_ai.system` → `gen_ai.provider.name` rename and keeping each span's original
  index as `_emit_seq`. It never fabricates a missing required attribute: an unnamed
  tool span stays unnamed so `conforms:` REDs on the truth.

## [0.5.0] - 2026-07-23

The absorption arc: a structured study of 18 adjacent OSS projects (`docs/research/ooptdd_F_oss_absorption_20260722.md` + `prom16_grok_20260722/`) turned into shipped mechanism. Headline: CI-portable report artifacts, an arrival policy that refuses to read ingestion lag as absence, a comparator/duration gate grammar, platform score sinks that never collapse the three-valued verdict, and a dual-track `gen_ai` ontology. 625 tests green (8-way CI matrix: ubuntu+windows × py3.10-3.13, plus a live OpenObserve demo job).

### Added
- **`tie_skew_ms` ordering window + emitter-authoritative `_emit_seq`.** `must_order`
  gains a per-pair authority ladder: both events carry `_emit_seq` (memory/jsonl ship
  paths stamp it; a SUT-stamped value wins) → compare those; else inside the check's
  `tie_skew_ms` window a timestamp "inversion" is CONCURRENT (cross-node clocks cannot
  prove order tighter than their skew — never a false RED); else the composite
  `(ts, _seq)` stands. The window never excuses missing names or a proven emit-seq
  inversion.
- **Anti-flap confirm rounds (`confirm_rounds` / `confirm_delay_s`).** A FINAL-path
  revocable green (absent/forbid, exact counts, …) is re-read N extra times; any round
  no longer green wins. Irrevocable SAT-latched greens still early-settle with zero
  extra reads; RED/inconclusive terminals are never re-proved. On Settings, the pytest
  ini (`ooptdd_confirm_rounds`), `verify_gate`/`verify_trace`/`session_finish`; the
  count lands in `arrival.confirm_rounds_run`. Default 0 = byte-identical behavior.
- **QuerySpec `limit`/`cursor` activated — OpenObserve reference `query_spec` +
  `fetch_all_pages`.** `QueryResult.next_cursor` + a generic cursor walker (honest
  `max_rows` guard: `complete=False` + the unconsumed cursor). Without limit/cursor the
  OO `query_spec` delegates to the read-to-completion `query()` byte-identically — the
  engine's live path is untouched. CH/VL paged opt-ins remain follow-ups.
- **Mutation drop-all canary.** `mutation_report` runs the gate once on an EMPTY
  stream; survival (`canary_survived: true`) proves the gate has no gating positive
  expectation — vacuity by measurement, the dynamic cross-check of the static lint.
  Not counted into `score`; `ooptdd mutate` maps survival onto the exit-2 rung.
- **Docs batch:** `docs/ci_mutation_gate.md`, `docs/case_study_template.md` + a
  deterministic `scripts/sanitize_case_study.py` (hash-anonymizes identities, `--check`
  leak gate), `docs/research/arrival_benchmark_spec.md` (spec-only), and
  `docs/why_arrival_testing.md` (the category-history narrative, wording verified).
- **`gen_ai@1.41` ontology preset — dual-track semconv versioning.** The certified final
  in-repo state of the OTel GenAI conventions before the v1.42 repo split: 9 operations
  (+`retrieval`), the 15-member `gen_ai.provider.name` enum (`x_ai` rename enforced),
  provider.name required on client-kind ops (embeddings' `request.model` demoted to
  conditional per spec), the `gen_ai.evaluation.result` event, token-usage expansion
  (cache_read/cache_creation/reasoning). The legacy `gen_ai` preset is FROZEN and pinned by
  a freeze test — version bumps ADD presets, never edit one.
- **Platform score sinks (`ooptdd.integrations.platform_scores`).** The verdict as a
  platform-native score with the ternary preserved: LangSmith categorical feedback kwargs
  (`score=None` for inconclusive — never 0/0.5), Langfuse `POST /api/public/scores` with
  `dataType=CATEGORICAL` (string value = the verdict word), Phoenix `CODE` trace
  annotations (inconclusive = label without a score key). Pure builders + injectable-opener
  posters; zero hard dependencies.
- **Typed rate-limit diagnosis + Retry-After-honoring poll.** `QueryResult.error_kind`
  (rate_limited/auth/timeout/other) + `retry_after_s`; `raise_for_status` raises
  `HTTPStatusError` carrying status + `Retry-After`; the HTTP drivers classify in their
  except blocks; the poller honors a store-sent Retry-After instead of burning attempts
  inside the throttle window. Verdict semantics unchanged (429 was already `?`).
- **Sampled-store evidence cap (`BackendCaps.samples`).** A sampled store proves SOME
  events arrived, not cross-event causal claims: the evidence-tier `queryable_causal` rung
  caps at `arrived` on it; `external_verdict` is untouched (a passing separate-source
  `external:` check bypasses the sampled store).
- **Comparator `where` (op-dict).** A `where` value may be `{op: gte|gt|eq|ne|lte|lt|
  contains|not_contains, value: V}` instead of a literal — numeric thresholds and substring/
  membership filters in any matcher (count/present/absent/ratio/…). Fail-safe rules: a
  missing field never matches an op-dict (not even `ne`), ordering ops require real numbers,
  and an unknown op raises instead of silently matching nothing (which would fake-green the
  absent wing). `must_order` is name-based and intentionally unaffected.
- **`duration:` check — a universal field threshold.** `{duration: {event: E, field: F,
  op: lte, target: N, where: …}}`: every matched event's `F` must satisfy the threshold
  (OpenSLO shape). LTL3 semantics: one offender latches VIOL immediately; satisfaction never
  latches mid-stream (universal claims stay PEND until end-of-stream); zero matched events is
  `no_evidence`, not a pass; a matched event with a missing/non-numeric field fails closed.
- **Arrival policy: the blind-window guard.** `BackendCaps.query_visibility_delay_ms` lets a
  backend declare its officially documented ingest-to-queryable lag (openobserve 5000,
  clickhouse 1000, victorialogs 1000; memory/jsonl 0), and VictoriaLogs gains `force_flush()`
  (`POST /internal/force_flush`, the endpoint its docs recommend for automated tests — called
  once, best-effort, before the first read). The poller never concludes ABSENT while the total
  wait is inside the declared window — if the retry budget ran out early it extends once past
  the window and re-reads; past the window a reachable+complete empty read is still ABSENT
  (catching silent loss is the product). Every polled verdict now carries
  `arrival{visibility_delay_ms, waited_ms, flushed, extended_for_visibility}`.
- **JUnit INFRA policy is now explicit: `--junit-inconclusive=skipped|error`.** Default stays
  `skipped` (? never renders red); `error` is the fail-closed opt-in for pipelines that must
  not let an unverified run scroll past — `<error type="ooptdd.inconclusive">`, an infra rung,
  still never a `<failure>`.

### Fixed
- **CLI: a missing cid or spec file is a clean error, not a traceback.** A `gate`/`monitor`/
  `can-i-deploy` spec with no `cid:` and no `OOPTDD_CID`, or a missing spec path, used to dump an
  uncaught `ValueError`/`FileNotFoundError` traceback. `main()` now catches both and prints a
  one-line `ERROR - …` on the INFRA/usage rung (exit 2), leaving the 0 GREEN / 1 RED / 2 INFRA
  verdict ladder unchanged.

## [0.4.0] - 2026-06-23

The gate-honesty arc: a green now reports *what*, *how hard*, and *on whose authority* it asserted,
and several signals were promoted to gates so the cheap ways to fake a green are closed. All additive
to the result dict / spec (backward-compatible). 314 tests green, 1 skipped.

### Added
- **`external:` check + probe registry — the one input that is not the system's own emit.** Assert
  a gate against a fact read from the territory through an `ExternalProbe` port. Reference adapters
  `FileProbe` / `HttpProbe` / `CallableProbe` in `ooptdd.probes`, resolved like backends (built-ins,
  the `ooptdd.probes` entry-point group, or an instance in code). A missing probe is a loud RED
  (never a silent green); an unreachable one is `inconclusive`. A probe counts as **corroboration**
  only when it declares `separate_source=True` — a genuinely independent store/service; re-reading
  the system's own store is relocation, not independence.
- **Oracle provenance (`result["oracle"]`).** Per-gate `corroborated` vs `derived_self` gating-check
  counts and `single_authority` (true when zero checks are independently corroborated) — the
  meta-blind-spot made visible: a green where the system only agrees with itself.
- **`require_corroboration`** (spec key / `OOPTDD_REQUIRE_CORROBORATION`, default off) promotes
  `single_authority` to a gate: a green with zero separate-source corroboration is RED
  (`uncorroborated`) — a fixable misconfiguration.
- **Charge-ratio (`scope.charged` / `charge_ratio` / `uncharged`).** How many gating checks actually
  saw matching evidence vs passed on absence/emptiness — distinct from stream-coverage.
- **`metamorphic` check.** An intra-trace metamorphic relation between two reductions over two
  matched subsets of the same stream (oracle-free, like `invariant`); `metamorphic_no_evidence` → RED.
- **Strength fingerprint + `ooptdd strength`** (per-check discriminating-power class, with a
  weakening-diff guard) and **stream charge-coverage** (`scope.stream_coverage`: how many arrived
  event-types a green even names; `unasserted_observed`).
- **`invariant` conservation check** (cross-event value consistency; `invariant_no_evidence` → RED)
  and a static anti-vacuity linter **`ooptdd lint`** (refuses a vacuously-satisfiable gate before any run).
- **`evidence_tier(result)` — the assertion-strength ladder, computed.** Grade a whole verdict by the
  strongest kind of evidence it mustered, on a five-rung ladder read off the existing honesty fields:
  `local_pass` < `emitted` < `arrived` < `queryable_causal` < `external_verdict`. So "what prevents a
  fake green" becomes answerable per-verdict — a green that only reaches `local_pass` (vacuous/unreachable)
  or `emitted` (named, but `charge_ratio == 0`) is loudly weak. Load-bearing: a non-`separate_source`
  `external:` check is self-consistency relocated, so it reaches only `arrived`, never `external_verdict`.
  Exported from `ooptdd.engine.gate` (+ the flat `ooptdd.gate` shim).
- **`assert_writeonly_backend_conforms` — conformance for write-only drivers.** `assert_backend_conforms`
  is a ship→query round-trip, so a write-only driver (`queryable=False`, e.g. OTLP) had zero coverage.
  This pairs the driver with a capture sink (`capture.records`, e.g. an OTLP `InMemoryLogExporter`
  adapter) and asserts export + payload fidelity, plus that the driver is *honestly* write-only
  (`caps.write_only`, and `query → reachable=False` — never a silent absent). Negative tests prove it
  catches a dropping exporter and a lying read side.
- **QuerySpec reserved-field contract pinned.** `limit` / `cursor` / `where` are now documented as
  reserved (a per-driver `query_spec` opt-in): `fetch` drops them on the legacy `query` path, and
  `where` is filtered in Python by design (dialect-neutral, injection-safe). Two guard tests pin the
  contract — legacy backends drop the extras; a `query_spec` backend receives them — so the seam can't
  silently rot.
- **Green banner names its signature posture (`sig=…`) when signing is in play.** A GREEN now appends
  `sig=valid` (the receipt is attested) or `sig=unverifiable` (a signature arrived but the verifier
  holds no key), so signing is visible where it matters. Keyless zero-config (`unsigned`) stays quiet,
  and an unsigned receipt in a keyed env is already RED (enforce-if-keyed) — never a silent green.

### Fixed
- **Corroboration requires the external check to actually pass.** `oracle.corroborated` counted any
  separate-source `external:` check by its kind alone — an unreachable or *refuting* probe (and,
  under a quorum `threshold`, one riding a self-pass to green) was still tallied as "independently
  corroborated" and could satisfy `require_corroboration`. Now gated on `passed`: corroboration is
  an achievement, not a check kind.
- **`single_authority` is false on an empty/vacuous gate** (no gating checks, never green) instead of
  true — it is a claim *about* the gating checks; the JSON now matches the banner's existing guard.
- **A closed-world `conforms` drift offender counts as charged evidence.** It demonstrably saw a
  forbidden event but was reported `uncharged` because the drift path never incremented `checked`;
  charge now also keys off `unknown` (`ontology_not_loaded` stays uncharged — it truly saw nothing).
- **Enforce-if-keyed: a configured signing key now rejects unsigned receipts by default.**
  `OOPTDD_SIGNING_KEY` and `OOPTDD_REQUIRE_SIGNATURE` were independent, so a verifier holding a key but
  not setting the require flag still accepted *unsigned* receipts from any producer. Now
  `require_signature` defaults ON whenever a key is configured (setting a key is the intent to reject
  unsigned), while an explicit `OOPTDD_REQUIRE_SIGNATURE` still wins either way and keyless zero-config
  stays lenient. Forgery/tamper were already always-RED; this closes the remaining unsigned-tolerance
  vector for keyed environments.
- **OTLP driver repaired against modern `opentelemetry-sdk`.** Wiring the shipped `otel` driver through
  the new write-only conformance surfaced that it was silently broken on current SDKs (1.42): `LogRecord`
  moved to `_internal` and the logs `emit` API went kwargs, so `emit(LogRecord(...))` shipped nothing.
  Fixed: emit via the modern kwargs form (the SDK builds the record), an instance-scoped
  `provider.get_logger` instead of the process-global singleton, and an injectable exporter so the
  driver is testable. The `opentelemetry-sdk` floor is bumped `>=1.20` → `>=1.38` (the modern kwargs
  `emit` TypeErrors on older SDKs — bisected: 1.37 fails, 1.38 works), and CI now installs the `otel`
  extra so the conformance test actually runs instead of `importorskip`-ing itself away.

## [0.3.0] - 2026-06-20

Engine/domain/adapter layering + a streaming monitor kernel, then a hardening pass that
closed several silent-green holes the audit surfaced. 228 tests green, 1 skipped (optional
Toxiproxy chaos layer). Backward-compatible: the flat module names (`ooptdd.gate`,
`ooptdd.verify`, `ooptdd.model`, …) keep working as re-export shims.

### Changed
- **Layering:** the read/judge engine moved to `ooptdd.engine.{gate,verify,monitor}` and the
  pure data/ports to `ooptdd.domain.{model,ports,ontology,semconv}`, with an import-cycle
  (Tarjan SCC) and layer-direction fitness test guarding the boundary. Flat modules remain as
  thin shims so 0.2.x imports are unbroken.
- **Streaming monitor kernel:** every gate check compiles to an LTL₃/MTL monitor automaton
  (anticipatory `sat`/`viol`/`pend` verdict + `settled_at`); the batch, live, and one-shot
  paths share one `compile_check`, so they cannot diverge.

### Fixed
- **xdist no longer ships/verifies *nothing*.** Per-test reports are now collected via
  `pytest_runtest_logreport` (fires on the controller) instead of `pytest_runtest_makereport`
  (fires only on the worker that ran the test). Before this, a `-n` run silently shipped and
  verified nothing — and a `strict` parallel run was a guaranteed green regardless of real
  ingest loss. Regression test runs an actual `-n 2` subprocess.
- **A truncated read is `inconclusive`, not a falsification.** `verify_trace` and `assert_gate`
  now treat an incomplete (`complete=False`) readback as `?` (never fails strict), matching
  the gate path (`evaluate_events`/`verify_gate`) — an undercounted read is no longer
  conflated with a real silent loss.
- **Partial-loss check no longer depends on `expect_total`.** `verify_trace` cross-checks the
  observed per-test `outcomes` against the session summary's own **signed** `total`, so a
  direct caller that passes no `expect_total` still catches a lost-receipt partial loss.
- **OpenObserve `query()` raises on a non-2xx search** (`_raise_for_status`, mirroring `ship`),
  so an error response can no longer read as an empty result set (a false `absent`).

### Added
- Plugin ini keys `ooptdd_retries` / `ooptdd_delay` / `ooptdd_backoff` to tune the arrival
  poll from `[tool.ooptdd]` (e.g. `ooptdd_delay = 0` for fast offline runs).

## [0.2.0] - 2026-06-16

OSS-adoption pass (prom12 research, `docs/research/ooptdd_E_oss_adoption_prom12_20260616.md`):
align ooptdd's surfaces with mature standards instead of bespoke shapes. All additive
and backward-compatible (existing specs/records unchanged); 137 tests green.

### Added
- **Gate vocabulary (OpenSLO/Keptn):** word operators (`gte`/`lte`/`eq`/…), `target`
  alias for `count`, `timeWindow` rolling readback window, `indicators`/`indicatorRef`
  SLI-vs-SLO split, `ratioMetric` (good/total), and `present` (subset match in **any**
  order, `testfixtures.check_present` semantics).
- **Pact-style gating:** `pending` checks (verified + surfaced but non-gating, with
  `pending_satisfied` promotion hint) and `can_i_deploy()` multi-gate deploy decision.
- **promptfoo/DeepEval:** per-check `weight` + spec-level `threshold` (weighted quorum),
  `trajectory` ordered-sequence alias, and `assert_gate`/`assert_present` in-test
  assertions (`TraceAssertionError`).
- **MTL bounded intervals (RTAMT):** `must_order … within_s` (`F[0,within]`) and
  `heartbeat`/`every_s` (`G[0,T]` liveness).
- **Backends:** `clickhouse` driver (Apache-2.0 SQL; also `signoz`), env-only,
  injection-safe parameterized cid. `otel` backend `simple=True` (synchronous processor
  for deterministic test ingestion).
- **Ontology:** `additional_properties: false` (JSON Schema `additionalProperties`
  → attribute-level closed-world drift); `Ontology.builtin("gen_ai")` (version-pinned
  OTel GenAI semconv preset); `ontology_compat()` (Confluent Schema Registry
  BACKWARD/FORWARD/FULL evolution gating).
- **Model:** CloudEvents 1.0 envelope floor (`cloudevents_envelope`/`validate_cloudevents`),
  `with_trace_context` (W3C `trace_id`/`span_id`), and tamper-evident HMAC hash chain
  (`sign_chain`/`verify_chain`, optional forward-secure key evolution).
- **Docs/examples:** LTL3 honesty pass in `METHODOLOGY.md` (cite Bauer-Leucker-Schallhart;
  ooptdd = LTL3 verdicts over a counting/past-time fragment, not full LTL); `examples/`
  GenAI agent dogfood with the OpenLLMetry→OTLP→`otel`-backend production wiring.

## [0.1.0] - 2026-06-16

Initial extraction into a standalone, infrastructure-neutral project.

### Added
- Core: `build_outcome_records`, `verify_trace` (three-valued LTL3 verdict:
  present / absent / inconclusive), `verify_policy`, `session_finish`.
- Pluggable backends behind a 2-method `Backend` protocol (`ship`, `query`):
  - `memory` — in-process, zero-infra; default for CI and the demo.
  - `openobserve` — reference network driver, env-only secrets.
  - `otel` — OTLP write path (optional `[otel]` extra; write-only).
  - third-party drivers via the `ooptdd.backends` entry point.
- pytest plugin (auto-registered via `pytest11`): ships every test outcome and
  asserts arrival; xdist-safe (controller-only ship); true no-op when disabled.
- `ooptdd` CLI: `verify`, `gate`, `version`.
- YAML gate runner (backend-agnostic, count-based).
- `METHODOLOGY.md` (LTDD, scrubbed public writeup) and `docs/research/` (the
  16-cell prior-art / design study behind the design).
- Killer demo (`examples/`): silent-ingest-loss caught against a self-reporting
  "ok" function, runnable with no infrastructure.

### Notes
- Extracted from internal harnesses where the core has run in production. No
  long-horizon operational data yet; see caveats in `METHODOLOGY.md`.
