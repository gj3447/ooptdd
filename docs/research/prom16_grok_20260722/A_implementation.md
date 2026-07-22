# PROM16 Grok — Axis A: implementation (구현 — 리포트 출력·문법·caps·mutation)

> cycle `prom16-ooptdd-oss-absorption-grok-20260722` · lenses: official-docs / alternatives / pitfalls / trends-2026

## A1 — implementation::official-docs (HIGH)

**findingId**: `finding_b65bf6c40245d941` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-0` · 2026-07-22T12:00:00Z

**한줄요약**: Emit JUnit per jenkinsci junit-10.xsd + GFM for GITHUB_STEP_SUMMARY; optional CTRF later; duration_s uses OpenSLO lte/ratio good-event semantics, missing attr→inconclusive.

**Root cause / gap**:
ooptdd report output is JSON-only with no CI-portable artifact, and gate grammar has no per-event latency predicate, so implementers lack a primary-spec mapping for JUnit/Markdown/CTRF reports and for a duration_s-style check. The authoritative shapes are de-facto JUnit (Jenkins xunit junit-10.xsd as pytest implements), GitHub Actions GITHUB_STEP_SUMMARY GFM, pre-1.0 CTRF JSON, and OpenSLO latency SLI algebra (ratio/threshold objectives)—not a single RFC.

**Recommendation**:
Extend CLI `_emit` with three formatters from the existing internal verdict document: (1) JUnit XML conforming to jenkinsci xunit-plugin junit-10.xsd as pytest does—root `<testsuites name="ooptdd">` / one `<testsuite name=spec|suite tests failures errors skipped time timestamp hostname>` / one `<testcase classname="ooptdd.gates" name="{gate_id}" time="{seconds:.3f}">` per gate; map present→pass, absent→`<failure message="..." type="ooptdd.absent">`, inconclusive→`<error message="..." type="ooptdd.inconclusive">` (or skipped only if gate was not evaluated); put receipt hash, evidence tier, and LTL3 verdict in `<properties><property name=... value=.../></properties>` and optional failure body in system-out. (2) Markdown report written for GitHub Actions: when `GITHUB_STEP_SUMMARY` is set, append GFM tables (gate, verdict, count, duration) to that file (≤1MiB/step); always also write a standalone `.md` artifact. (3) Keep JSON canonical; optional CTRF export only behind a flag once pinning a stable `specVersion`, with `reportFormat:"CTRF"`, `results.tool.name:"ooptdd"`, summary counts, tests[].status in {passed,failed,skipped,pending,other}, duration in integer ms, ooptdd-specific fields only under `extra`. For duration checks: add optional gate field `duration_s` (float seconds) with `op` default `lte` using OpenSLO objective ops {lt,lte,gt,gte}; after event/where match, extract numeric duration from a configurable attr (default OTel-style `duration_s` or span duration converted to seconds); each matched event is good iff op(duration, threshold); default semantics = all matched good (strict present), optional `min_ratio` following OpenSLO Occurrences ratioMetric good/total; missing duration attr → inconclusive not fail; expose BackendCaps flag `event_duration` for backends that can return duration columns.

**Alternatives**:
  - JUnit-only via pytest --junit-xml when ooptdd runs as pytest plugin, skipping CLI XML—lower work but leaves `ooptdd` CLI / mutate path without CI artifacts and couples report shape to pytest process model.
  - Adopt CTRF as primary machine format instead of JUnit—cleaner three-status mapping (failed vs other for inconclusive) and flat tests[], but CTRF is pre-1.0 Working Draft with possible breaking changes; Jenkins/GitHub still expect JUnit XML for native test UIs.
  - Model duration only as ratioMetric-style aggregate (good/total under threshold) without per-event fail-fast—closer to OpenSLO SLO windows, weaker for single-trace RED gates and for gate mutation targeting one slow event.
  - Copy Tracetest-style `tracetest.span.duration <= 100ms` assertions—familiar to trace users but prior ooptdd canon bans wholesale Tracetest DSL copy; better to keep ooptdd `duration_s`+`op` and only mirror the semantic idea.

**Caveats**: There is no single official JUnit XML standard from junit-team; consumers reverse-engineered Jenkins/Surefire. pytest defaults junit_family=xunit2 (testcase attrs limited to classname+name; file/line only in xunit1/legacy). CTRF spec is Working Draft pre-1.0 (specVersion field SemVer; document examined lists Version 0.0.0 / 2025-11-24)—do not pin as sole interchange yet. OpenSLO duration-shorthand (m/h/d/w/M/Q/Y) is for time windows and alert lookbacks, not sub-second per-event latency; latency is expressed via ratioMetric good/total or thresholdMetric with op+value in metric queries. GitHub Actions has no first-class test-report schema—only GITHUB_STEP_SUMMARY GFM and optional third-party JUnit uploaders. Mapping LTL3 inconclusive to JUnit is ambiguous (error vs skipped); document the choice in receipt properties.

**References**:
  - https://github.com/jenkinsci/xunit-plugin/blob/master/src/main/resources/org/jenkinsci/plugins/xunit/types/model/xsd/junit-10.xsd
  - https://raw.githubusercontent.com/jenkinsci/xunit-plugin/master/src/main/resources/org/jenkinsci/plugins/xunit/types/model/xsd/junit-10.xsd
  - https://docs.pytest.org/en/stable/_modules/_pytest/junitxml.html
  - https://docs.pytest.org/en/stable/how-to/output.html
  - pytest/src/_pytest/junitxml.py
  - https://llg.cubic.org/docs/junit/
  - https://plugins.jenkins.io/junit/
  - https://raw.githubusercontent.com/ctrf-io/ctrf/main/spec/ctrf.md
  - https://github.com/ctrf-io/ctrf
  - https://ctrf.io/docs/intro
  - https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands
  - https://github.com/OpenSLO/OpenSLO/blob/main/README.md
  - https://openslo.com/

---

## A2 — implementation::alternatives (HIGH)

**findingId**: `finding_da5fbb9aa0167adf` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-1` · 2026-07-22T18:45:00Z

**한줄요약**: Adopt Tracetest’s comparator+duration+JUnit mapping (not its selector DSL) plus pytest XML escape and mutmut CI-stats JSON into ooptdd’s check registry and _emit funnel.

**Root cause / gap**:
ooptdd’s gate surface still has equality-only where, no per-matched-event duration threshold, JSON-only CLI reports, and mutation UX that lacks a machine-readable CI score export. Among inspected clones, Tracetest (not malabi/inspect_ai) is the only production-shaped implementation that jointly solves duration thresholds + field comparators + JUnit mapping; pytest and mutmut supply report/CI patterns that fit ooptdd’s existing _emit and mutate seams without importing foreign DSLs.

**Recommendation**:
Absorb a MINIMAL three-part subset into existing seams—do not copy Tracetest’s span[…] selector DSL or expression filters (json_path/regex/count). (1) Shared comparator registry used by where and duration: register string-keyed ops eq/ne/lt/lte/gt/gte/contains/not_contains (mirroring tracetest/server/assertions/comparator/basic.go Basic + Registry), parse where as either scalar equality (compat) or {field: {op, value}} / list of {field, op, value}; numeric ops coerce int/float, duration ops normalize both sides to nanoseconds. (2) Per-event duration via @check("duration") (or optional duration_s on event gates): after matching event/where, derive duration from end-start timestamps or known duration attrs; compare with op+threshold (units s/ms/us/ns or bare seconds); missing timestamps → inconclusive, not fail. (3) Report writers on CLI _emit: (a) JUnit XML with testsuites=run, testsuite=gate id/selector, testcase=check name; map present→pass, fail/absent→failure@message=observed, inconclusive→error (or skipped) as in tracetest/server/junit/junit.go + testdata/junit_result.xml; apply pytest-style illegal-char escape from pytest/src/_pytest/junitxml.py bin_xml_escape; (b) short markdown summary (counts + failed checks); (c) keep JSON. (4) Gate-mutation report: emit mutmut-like JSON {killed,survived,total,score,by_operator} for ooptdd mutate --min-score (see mutmut export_cicd_stats / save_cicd_stats), not mutmut’s TUI browse. Explicit BackendCaps declarations remain orthogonal but should flag whether timestamp/duration fields are queryable per backend before duration checks claim arrived/queryable_causal tiers.

**Alternatives**:
  - Adopt full Tracetest assertion language (attr:…, filters, child selectors, duration as tracetest.span.duration ns metadata)—richest UX but Go stack, deliberately banned full selector DSL copy, high learning curve vs pytest-native ooptdd.
  - Rely on pytest --junitxml only for Python tests and leave CLI gate runs JSON-only—lowest effort, fails CI systems that only parse JUnit from ooptdd CLI artifacts.
  - Use malabi’s imperative MalabiSpan/SpansRepository filters (TS/OTel in-process)—good for same-process span asserts, no declarative duration threshold, no JUnit, wrong language/runtime for ooptdd.
  - Use inspect_ai eval log + viewer/score CLI for human-readable run artifacts—strong eval transcript UX, not event/span duration gates or gate-mutation CI scores.
  - Depend on mutmut source-mutation operators/report UX for gate quality—wrong mutation target (Python AST vs YAML gates); only absorb CI stats JSON shape, not libcst operators.
  - Ship markdown-only reports first—human-friendly but weak CI integration vs JUnit; pair with JSON+JUnit for production.

**Caveats**: Tracetest selector filters only parse = and contains (parser.go); full numeric/string ops live in the assertion/comparator path—ooptdd should not assume CSS-like selectors need every op. Duration is stored as nanosecond string metadata then rounded for comparison; bare integers like < 500 are treated as ns-scale numbers, while 100ms/2s use duration typing—ooptdd should prefer explicit units to avoid that footgun. Malabi exposes duration only in OTel span test fixtures, not as a first-class assertion API. inspect_ai was inspected for log/viewer/score surfaces only; it is not a span assertion engine. mutmut has no JUnit/markdown mutation report—only terminal + mutmut-cicd-stats.json. Full pytest xunit2 family/properties/system-out is heavier than needed; start with Tracetest’s flat mapping + bin_xml_escape. BackendCaps for duration requires real event timestamps in query backends; memory/jsonl may work first. Local ooptdd package source not in this competitors tree—seams taken from the supplied 2026-07-22 audit.

**References**:
  - tracetest/server/assertions/comparator/basic.go
  - tracetest/server/assertions/comparator/comparators.go
  - tracetest/server/assertions/selectors/parser.go
  - tracetest/server/traces/span_entitiess.go
  - tracetest/server/traces/trace_entities.go
  - tracetest/server/traces/time_converter.go
  - tracetest/server/expression/types/types.go
  - tracetest/server/expression/executor.go
  - tracetest/server/junit/junit.go
  - tracetest/server/junit/testdata/junit_result.xml
  - tracetest/server/junit/junit_test.go
  - tracetest/server/test/test_json_test.go
  - malabi/packages/telemetry-repository/src/MalabiSpan.ts
  - malabi/packages/telemetry-repository/src/SpansRepository.ts
  - malabi/packages/telemetry-repository/test/MalabiSpan.spec.ts
  - pytest/src/_pytest/junitxml.py
  - mutmut/src/mutmut/__main__.py
  - mutmut/src/mutmut/mutation/mutators.py
  - inspect_ai/src/inspect_ai/log/_log.py
  - inspect_ai/src/inspect_ai/_cli/score.py
  - https://docs.tracetest.io/getting-started/create-assertions
  - https://github.com/kubeshop/tracetest

---

## A3 — implementation::pitfalls (HIGH)

**findingId**: `finding_7b67b375a7114bf0` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-2` · 2026-07-22T18:45:00Z

**한줄요약**: JUnit/markdown/selector/mutation growth needs lossy-projection contracts: fail-closed LTL3→JUnit map, JSON-canonical MD, no Tracetest CSS, SemanticOpSet min-score only.

**Root cause / gap**:
Report formats and gate/selector grammar are lossy projection surfaces, not free extensions of the JSON verdict kernel. Unconstrained growth creates four failure modes that look like progress: CI parsers that ignore custom attributes/properties while still reporting green; markdown that drifts from the machine receipt; CSS-like selector languages that overfit span trees and break under retries/ambiguity; and mutation min-score thresholds that are Goodharted by equivalent or non-semantic operators.

**Recommendation**:
Attach all new report formats only through the existing CLI `_emit` funnel as pure projections of one canonical JSON verdict object (gate id, LTL3 present|absent|inconclusive, evidence-tier, receipt hash-chain, matched event ids). (1) JUnit: emit minimal Jenkins/xunit2-compatible `testsuites/testsuite/testcase` with required attrs only (`name`, `classname`, `tests|failures|errors|skipped|time`). Map LTL3 as Tracetest does for assert outcomes—semantic miss → `<failure type=... message=actual>`; infrastructure/parse/backend/query errors → `<error>`; **never map `inconclusive` to pass**. Default `inconclusive` → `<error type="ooptdd.inconclusive">` (fail-closed); optional `--junit-inconclusive=skip` for explicit flake windows. Put evidence-tier, receipt hash, and matched selectors in `<system-out>` text, **not** `<property>`/`record_property` (pytest marks those xunit2-incompatible; Surefire/GitLab often ignore or bloat on properties). One gate check = one testcase; classname=gate id, name=check key. Ship a golden XML fixture + CI-parser smoke (GitHub/GitLab JUnit consumers). (2) Markdown: single template renderer over the same JSON; forbid independent markdown writers; header must include receipt hash so rot is visible. (3) Grammar: do **not** copy Tracetest’s full selector surface (`span[...]`, `contains`, `:first|:last|:nth_child`, recursive parent-child). Keep `where` a closed operator enum starting from equality; add ops only when `BackendCaps` declares pushdown or client-side postfilter support. Implement per-event duration as a **gate assertion field** (e.g. `max_duration_s` on present/count), matching Tracetest’s separation of selector vs `attr:tracetest.span.duration < ...`, not as selector DSL. (4) Mutation: redefine `--min-score` over a fixed SemanticOpSet only—{flip present↔absent|forbid, drop/alter where clause, weaken/strengthen count, invert must_order/trajectory, disable require_signature/corroboration, loosen max_duration_s}; exclude message-only/string/no-op equivalent operators from the denominator; require per-operator kill matrix artifact and refuse a single scalar as the only gate.

**Alternatives**:
  - Emit only JSON + pytest’s native `--junitxml` via the plugin path and skip a custom JUnit emitter — lower maintenance, but loses gate-level testcase granularity and three-valued mapping control.
  - Map inconclusive→skipped by default to reduce flakes — CI may treat skipped as non-blocking, silently masking store lag and undercutting arrived/queryable evidence tiers.
  - Grow `where` to full CSS/Tracetest parity for expressiveness — highest selector power, but recursive parent-child and pseudo-classes do not map cleanly to flat log streams; order-sensitive :nth_child fails under retries; ambiguous multi-match remains a documented Tracetest pitfall; BackendCaps cannot honestly advertise pushdown.
  - Keep a single aggregate mutation score including all operators (mutmut-style) — simpler CLI, but string/message and equivalent mutants inflate or deflate the metric (Goodhart) without improving gate strength.
  - Put rich ooptdd fields in JUnit `<property>` tags for dashboards — convenient for some Jenkins UIs, but dialect-dependent, ignored by many parsers, and bloats artifacts until reports fail to load.

**Caveats**: ooptdd source is not in this competitors workspace; seams (_emit, BackendCaps, mutate --min-score, equality-only where) are taken from the 2026-07-22 audit brief, not re-verified against v0.4.0 tree here. JUnit has no single normative schema (Jenkins junit-10.xsd vs Surefire XSDs diverge on optional attrs). Whether every target CI treats `<error>` as build-failing was not re-probed per product version. Markdown-rot evidence is inferential from dual-artifact practice rather than a named ooptdd incident. Mutation Goodharting for gate-mutation (not source mutmut) is by analogy to equivalent-mutant literature and mutmut string false-positives.

**References**:
  - https://llg.cubic.org/docs/junit/
  - https://techblog.topdesk.com/coding/reporting-on-large-amounts-of-junit-tests-in-gitlab-ci/
  - https://softengbook.org/articles/mutation-testing
  - https://github.com/boxed/mutmut/issues/175
  - https://docs.tracetest.io/concepts/selectors
  - tracetest/server/junit/junit.go
  - tracetest/server/junit/testdata/junit_result.xml
  - tracetest/server/assertions/selectors/parser.go
  - tracetest/server/assertions/selectors/pseudo_classes.go
  - tracetest/docs/docs/concepts/selectors.mdx
  - pytest/src/_pytest/junitxml.py
  - pytest/doc/en/reference/reference.rst
  - mutmut/src/mutmut/__main__.py

---

## A4 — implementation::trends-2026 (HIGH)

**findingId**: `finding_077f258b890c4af5` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-3` · 2026-07-22T12:00:00Z

**한줄요약**: Bet on JUnit+markdown via CLI _emit first, optional CTRF for GH PR UX; keep rich JSON canonical; skip SARIF-for-tests.

**Root cause / gap**:
ooptdd emits only proprietary JSON while 2025–2026 CI platforms still gate and display results via JUnit XML (GitLab native artifacts:reports:junit; GitHub via dorny/test-reporter or CTRF github-test-reporter), with human PR UX moving to markdown job summaries. The three-valued LTL3 model (present/absent/inconclusive) and evidence-tier/receipt fields have no portable interchange path, so gate outcomes stay invisible to MR/pipeline UIs and multi-tool aggregators.

**Recommendation**:
Implement a pluggable report-writer registry on the existing CLI `_emit` funnel (not a parallel path): keep the rich ooptdd JSON verdict bundle as canonical, then fan-out writers selected by `--report <fmt>=<path>` (repeatable). Ship writers in this order: (1) `junit` — map one gate → one `<testcase>` (Tracetest pattern: suite name = selector/spec id; case name = gate predicate); LTL3 map present→pass, absent/forbid-hit→`<failure>`, inconclusive→`<skipped message="inconclusive:…">` with optional suite `<properties>` for evidence_tier, receipt_hmac, backend; (2) `markdown` — compact table for `$GITHUB_STEP_SUMMARY` / GitLab notes (gate, verdict, tier, note); (3) optional `ctrf` — `reportFormat=CTRF`, status from same map with `rawStatus` preserving present|absent|inconclusive and `extra`/`labels` carrying evidence_tier + receipt hash so ctrf-io/github-test-reporter can publish PR comments without custom UI. Do not invent SARIF-for-tests; do not prioritize Open Test Reporting until GitLab/GitHub consume it. Prefer stdlib xml.etree for JUnit; validate against GitLab’s parsed subset (name, classname, time, failure/error/skipped, system-out).

**Alternatives**:
  - JUnit-only via pytest plugin recording ooptdd gates as virtual tests: reuses pytest --junit-xml, zero new CI docs; tradeoff: CLI-only `ooptdd verify` runs and xdist edge cases need separate handling, and three-valued semantics compress into skip/fail only.
  - CTRF-first with community github-test-reporter (accepts CTRF or JUnit): best GitHub PR/summary UX and flaky insights; tradeoff: CTRF still pre-1.0, weaker GitLab-native MR Test summary than artifacts:reports:junit.
  - Markdown-only + keep JSON: fastest human value; tradeoff: no machine CI gate panels, no dorny/GitLab Tests tab.
  - SARIF export of failed gates as 'findings': wrong standard (static analysis/code scanning), stagnating as a test interchange; avoid.
  - Wait for Open Test Reporting (JUnit Platform): richer Java event model; tradeoff: GitLab does not parse it yet; not native to pytest/ooptdd.

**Caveats**: CTRF is still explicitly pre-1.0 (community refinements before lock); schema fields used here (rawStatus, extra, labels) were verified from the live schema fetch but may shift before v1.0. Three-valued→binary CI mapping loses nuance if consumers treat skipped as green without reading message/rawStatus—document that inconclusive must fail the job via exit code when required. GitLab ignores many JUnit extensions (properties, status attrs); put critical failure text in failure/skipped body. SARIF remains the SAST interchange, not a test standard—do not conflate. Open Test Reporting momentum is Java-side; GitLab support open/unresolved. Local ooptdd `_emit` source not in this clone set; design assumes the stated single-funnel seam from the cell brief. Malabi appears quieter vs Tracetest/promptfoo for CI report artifacts.

**References**:
  - https://ctrf.io/
  - https://github.com/ctrf-io/ctrf
  - https://raw.githubusercontent.com/ctrf-io/ctrf/main/schema/ctrf.schema.json
  - https://raw.githubusercontent.com/ctrf-io/ctrf/main/README.md
  - https://github.com/ctrf-io/github-test-reporter
  - https://docs.gitlab.com/ci/testing/unit_test_reports/
  - https://github.com/dorny/test-reporter
  - https://docs.pytest.org/en/stable/how-to/output.html
  - pytest/src/_pytest/junitxml.py
  - tracetest/server/junit/junit.go
  - tracetest/server/junit/testdata/junit_result.xml
  - tracetest/docs/docs/cli/reference/tracetest_run.md
  - promptfoo/src/util/outputFormats.ts
  - promptfoo/src/util/output.ts
  - promptfoo/src/util/junit.ts
  - promptfoo/src/assertions/traceSpanDuration.ts
  - malabi/README.md
  - inspect_ai/docs/eval-logs.qmd
  - https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/sarif-support-for-code-scanning
  - https://techblog.topdesk.com/coding/reporting-on-large-amounts-of-junit-tests-in-gitlab-ci/
  - https://github.com/microsoft/testfx/issues/8858
