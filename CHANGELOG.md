# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
