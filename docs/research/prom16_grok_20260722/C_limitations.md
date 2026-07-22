# PROM16 Grok — Axis C: limitations (한계 — arrival 신뢰성·카테고리 교훈)

> cycle `prom16-ooptdd-oss-absorption-grok-20260722` · lenses: official-docs / alternatives / pitfalls / trends-2026

## C1 — limitations::official-docs (HIGH)

**findingId**: `finding_75dfa90515bf4561` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-8` · 2026-07-22T12:00:00Z

**한줄요약**: Specs admit drops, at-least-once dupes, and sub-s–multi-s query invisibility; LTL3 leaves liveness as ?—ooptdd must encode BackendCaps delays/force_flush so pre-visibility misses stay inconclusive, not RED.

**Root cause / gap**:
Primary specs admit that positive-arrival verification cannot equate “SUT emitted” with “queryable in the store,” nor can LTL3 decide liveness on finite prefixes. OTLP scopes reliability to a single hop and requires drops on non-retryable failure and queue overflow; VictoriaLogs/ClickHouse/OpenObserve document multi-stage buffers before searchability; Bauer–Leucker–Schallhart LTL3 forces ? on any non-safety/co-safety property. ooptdd’s poll-until-present path therefore systematically over-claims RED unless BackendCaps encode store-admitted invisibility windows and the monitor maps pre-visibility misses to inconclusive.

**Recommendation**:
Extend typed BackendCaps (and each backend entry-point declaration) with official queryability metadata: `query_visibility_delay_ms` (default from docs: VictoriaLogs 1000; ClickHouse async_insert busy timeout 200–1000; OpenObserve memtable/WAL path ZO_MEM_PERSIST_INTERVAL=5s + ZO_FILE_PUSH_INTERVAL), optional `force_flush` hook (VictoriaLogs POST `/internal/force_flush` before query—docs explicitly recommend this for automated tests), and `max_evidence_tier` (memory/jsonl ≤ queryable_causal; never external_verdict). In the independent verifier poll loop: (1) on SUT end call SDK ForceFlush if available; (2) if gate still unsatisfied and elapsed < caps.query_visibility_delay_ms, emit LTL3 inconclusive (not RED); (3) only after delay + gate timeWindow/deadline may absent become RED for co-safety/present gates; (4) refuse to promote memory-backend passes past arrived. Document what-not-to-do in README: no unbounded request/ack liveness as present/absent; no treating OTLP Export success as arrived; no span-tree selectors. Ship BackendCaps matrix tests in the conformance kit so OpenObserve/CH/VL declare explicit caps instead of silent defaults.

**Alternatives**:
  - Always force_flush + sleep(max_declared_delay) before first query—simple, but slows CI and papers over partial-success/drop cases the OTLP/SDK specs require recording as lost data.
  - Collapse missing-during-delay into soft retry without three-valued verdicts—easier UX, but discards the Bauer–Leucker–Schallhart distinction that premature true/false is unsound and hides the credibility gap of poll-flake RED.
  - Require wait_for_async_insert=1 / synchronous insert only for ooptdd backends—stronger durability, but rejects common observability fire-and-forget paths and still leaves OTLP multi-hop and batch-queue drops out of scope.

**Caveats**: OpenObserve marketing line “Immediate indexing” on the logs feature page conflicts with architecture/env defaults (Memtable→Immutable→WAL parquet→object store; ZO_MEM_PERSIST_INTERVAL=5s, ZO_FILE_PUSH_INTERVAL defaults differ between architecture narrative and env table); use architecture+env as binding. Exact CH Cloud busy-timeout (200 vs 1000 ms) is deployment-dependent. LTL3 papers define theoretical monitorability; ooptdd’s YAML gates are not full LTL formulas—mapping is by analogy (present≈co-safety, forbid≈safety, must_order/heartbeat without finite bound≈non-monitorable). OTLP e2e multi-hop is explicitly out of scope—no official numeric SLA. No local ooptdd clone path verified in this cell.

**References**:
  - https://opentelemetry.io/docs/specs/otlp/
  - https://opentelemetry.io/docs/specs/otel/logs/sdk/
  - https://opentelemetry.io/docs/specs/otel/trace/sdk/
  - https://docs.victoriametrics.com/victorialogs/
  - https://docs.victoriametrics.com/victorialogs/querying/
  - https://clickhouse.com/docs/en/optimize/asynchronous-inserts
  - https://openobserve.ai/docs/architecture/
  - https://openobserve.ai/docs/administration/configuration/environment-variables/
  - https://christian.schallhart.net/publications/2006--fsttcs--monitoring-of-realtime-properties.pdf
  - https://trustworthy.systems/publications/nicta_full_text/3976.pdf

---

## C2 — limitations::alternatives (HIGH)

**findingId**: `finding_5a9075d8ceea3afb` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-9` · 2026-07-22T12:00:00Z

**한줄요약**: Don’t copy Tracetest span trees, Phoenix UIs, or inspect_ai .eval; adopt polling+JUnit+_emit verdict export to LangSmith/Phoenix/Langfuse—export is the adoption limit.

**Root cause / gap**:
Competitors win in three product categories ooptdd cannot and should not own: (1) full span-tree assertion DSLs (Tracetest ChildSelector / parent-subtree filters; Malabi fluent span filters over in-process or Jaeger traces), (2) platform UI/experiments/datasets/annotations (Phoenix span annotations, Langfuse scores, LangSmith create_feedback), and (3) offline eval record+replay packages (inspect_ai ZIP .eval with EvalSample events/scores). The positive-arrival lane only needs a thin slice of those ecosystems—durable external readback, explicit ingestion polling, CI-consumable reports, and optional verdict export into platforms users already open—not the full products. The adoption-cost limit is the credibility gap of a small pytest library with JSON-only reports and no export into LangSmith/Phoenix/Langfuse, plus timing/polling flakiness; not the absence of a Tracetest-class selector language.

**Recommendation**:
Ship a VerdictExport/ReportSink layer on the existing CLI `_emit` single funnel (and receipt post-gate), not a new product surface. (1) Register sinks: `json` (current), `junit` (mirror Tracetest’s selector→checks suite shape: one testcase per gate with failure/error/skipped mapped from present/absent/inconclusive), optional `markdown`. (2) Register optional score exporters behind extras: map each gate verdict + evidence_tier + receipt hash to (a) OTel attributes on a write-only `ooptdd.gate.verdict` event (`ooptdd.verdict`, `ooptdd.evidence_tier`, `ooptdd.gate_id`, `ooptdd.receipt_sha`), (b) LangSmith `Client.create_feedback` / `testing.log_feedback(key, score)` where score encodes present=1/absent=0/inconclusive=None, (c) Phoenix `client.spans.log_span_annotations` / `SpanAnnotationData` with annotator_kind LLM or CODE, (d) Langfuse PostScoreBodyFoundation-shaped payload (name, traceId/observationId, comment=receipt ref). (3) Add first-class `polling:` block in gate YAML (retry_delay_s, timeout_s) that only advances evidence tier and keeps LTL3 inconclusive until timeout—do not treat late ingest as absent. (4) Freeze what-not-to-do in docs+tests: no Tracetest selector DSL (contains/nth_child/parent-subtree), no DeepEval BaseMetric / promptfoo llm-rubric wrappers as core checks, no Phoenix/Langfuse UI or experiment runner, no inspect_ai .eval re-score harness, no promoting memory/jsonl to external_verdict. Optional later: equality `where` plus optional `parent_id`/`trace_id` equality filters only if backends expose them—never recursive subtree operators.

**Alternatives**:
  - Copy Tracetest span-selector + parent-child DSL into ooptdd gates — tradeoff: solves multi-call ambiguity for distributed traces but abandons the deliberate ban, balloons grammar, and competes as a second Tracetest without UI/trigger product; wrong category for flat log/event arrival.
  - Build or embed UI/experiments (Phoenix/Langfuse-like) — tradeoff: closes credibility gap for explorers but destroys the small-library value prop and dilutes positive-arrival kernel.
  - Become an eval harness (inspect_ai .eval / promptfoo / DeepEval metrics) — tradeoff: large market but judges model quality and offline transcripts, not independent external arrival of SUT-emitted events; confuses lanes.
  - Stay pure event-gate with only local JSON reports — tradeoff: purest kernel, but CI tooling and platform users ignore results; credibility gap remains the adoption killer.
  - Malabi-style in-process span repo as primary verifier — tradeoff: fast DX, but generator≈store (memory-backend illusion) and collapses evidence tier to local_pass/emitted.

**Caveats**: Did not run competitor builds or live API calls; evidence is clone source + official Tracetest selectors docs. ooptdd source tree is not in this competitors workspace—gate/`_emit` seam recommendations rely on the 2026-07-22 audit brief. Exact Langfuse Python SDK method names and Phoenix REST payload field lists may differ from the foundation schemas/examples cited; implement behind thin optional extras with contract tests. Parent-id equality is only useful if backends actually return parent/trace linkage today (QuerySpec still incomplete per audit). No claim that JUnit alone closes the credibility gap without at least one platform exporter used in a real CI path.

**References**:
  - tracetest/server/assertions/selectors/selector.go
  - tracetest/server/assertions/selectors/builder_test.go
  - tracetest/server/assertions/selectors/parser_test.go
  - tracetest/server/assertions/selectors/search.go
  - tracetest/docs/docs/concepts/selectors.mdx
  - tracetest/docs/docs/concepts/polling-profiles.mdx
  - tracetest/server/junit/junit.go
  - tracetest/README.md
  - https://docs.tracetest.io/concepts/selectors
  - malabi/README.md
  - malabi/packages/telemetry-repository/src/SpansRepository.ts
  - malabi/packages/telemetry-repository/src/MalabiSpan.ts
  - phoenix/packages/phoenix-client/examples/annotations/log_span_annotations_example.py
  - phoenix/packages/phoenix-client/src/phoenix/client/resources/spans/__init__.py
  - phoenix/MIGRATION.md
  - langfuse/packages/shared/src/features/scores/interfaces/shared.ts
  - langfuse/README.md
  - langsmith-sdk/python/langsmith/testing/_internal.py
  - inspect_ai/src/inspect_ai/log/_recorders/eval.py
  - inspect_ai/src/inspect_ai/log/_log.py
  - deepeval/deepeval/metrics/base_metric.py
  - promptfoo/src/types/index.ts

---

## C3 — limitations::pitfalls (HIGH)

**findingId**: `finding_e9270b7ab474c927` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-10` · 2026-07-22T12:00:00Z

**한줄요약**: TBT died from timeout=fail + incomplete-trace-as-ready and commercial non-adoption; ooptdd must default wait exhaustion to LTL3 inconclusive with evidence-tier caps, not RED.

**Root cause / gap**:
Trace-based testing as a product category died less from missing selectors than from conflating ingestion/polling lag with assertion failure, plus commercial non-adoption: Tracetest Cloud EOL (2024-10-31) admitted adoption never justified investment; Malabi was archived the same day after Aspecto’s acquisition orphaned the in-process library. The failure signature that destroys trust is timeout/incomplete-trace → RED (or worse, span-count stability declaring a partial trace “ready”), not lack of UI features.

**Recommendation**:
Make ArrivalPolicy a first-class, default-on product law on existing LTL3 + BackendCaps + evidence-tier + CLI `_emit` seams—never map wait-budget exhaustion to absent/fail. (1) On query empty/timeout, OTLP write-only, or backend missing query caps, force verdict=inconclusive with reason codes `ingestion_delay|backend_write_only|evidence_tier_insufficient|partial_arrival` and freeze the HMAC receipt at the highest achieved tier (never claim arrived/queryable_causal). (2) For `present`/`absent`/`forbid`/`must_order` gates, default `on_wait_exhausted: inconclusive` (opt-in only: `strict_absent_on_timeout: true` for deliberate negative proofs after a forced flush). (3) Refuse CI RED when declared `min_evidence_tier` is unmet—pytest plugin maps inconclusive → skip/xfail or exit code 2, not fail; extend `_emit` so JSON (and later JUnit) expose `verdict`/`reason`/`evidence_tier` as distinct fields. (4) Cap memory/jsonl BackendCaps so local_pass/emitted cannot satisfy require_corroboration or external_verdict. (5) Ship a WHAT_NOT_TO_DO ratchet: no Tracetest-style poller server/UI, no span-count “trace ready” heuristic, no full span-tree selector DSL, no selling memory-backend GREEN as production arrival proof. This is the anti-death feature Tracetest lacked and Malabi never had.

**Alternatives**:
  - Copy Tracetest PollingProfile (retryDelay+timeout) as YAML and keep binary pass/fail on timeout — fastest familiar UX but reintroduces the exact flaky-wait hell that made the category untrustworthy in CI.
  - Only document the pitfall (inconclusive semantics already in LTL3) without CLI/report/pytest mapping — zero code risk but users still see generic failures and churn like Tracetest adopters did.
  - Pivot ooptdd into a full trace platform (agent + multi-backend poller + UI) to “complete” the category — maximizes surface area and maintainer burn, the path Tracetest commercial already proved non-viable.
  - Rely solely on generator-side capture-sink conformance and ban external stores — avoids polling flakiness but collapses generator≠verifier and evidence-tier ladder into Malabi’s in-memory illusion.

**Caveats**: Primary user-complaint evidence is architectural (docs + poller source + commercial EOL wording), not a large corpus of opened GitHub issue threads with flaky-wait anecdotes; release page nightly date lacked a clear year in the fetch UI. Tracetest OSS repo is not archived—stagnation is commercial EOL + last stable v1.7.1 (2024-10-10) + archived Testkube executor (2025-12-18), not a formal archive banner. 2026 tertiary blogs still recommend Tracetest/Malabi without noting death signals—treat category narrative as stale. ooptdd-local code paths for ArrivalPolicy were not audited in this cell (competitors workspace only).

**References**:
  - https://tracetest.io/blog/end-of-life-announcement-for-tracetest-cloud
  - https://github.com/aspecto-io/malabi
  - https://github.com/kubeshop/tracetest/releases
  - https://github.com/kubeshop/testkube-executor-tracetest
  - https://docs.tracetest.io/concepts/polling-profiles
  - https://smartbear.com/news/news-releases/smartbear-acquires-opentelemetry-pioneer-aspecto/
  - https://qaskills.sh/blog/trace-based-testing-opentelemetry-2026
  - tracetest/docs/docs/configuration/trace-polling.mdx
  - tracetest/server/executor/default_poller_executor.go
  - tracetest/server/executor/selector_based_poller_executor.go
  - tracetest/TEST_RUN_EVENTS.csv
  - malabi/README.md
  - malabi/packages/malabi/package.json

---

## C4 — limitations::trends-2026 (HIGH)

**findingId**: `finding_d977ade1bebeb180` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-11` · 2026-07-22T12:00:00Z

**한줄요약**: ODD/TBT stagnated commercially; agent-harness verification revived it—ooptdd wins by exporting LTL3 verdicts as independent scores and forbidding memory backends above local_pass.

**Root cause / gap**:
Classic microservice 'trace-based testing' commercially stagnated (Tracetest Cloud EOL: adoption did not justify investment), while 2025–2026 discourse rebranded and accelerated under agent harness verification: observability-driven feedback loops, production SLO rings, and falsifiable runtime sensors. ooptdd's narrow claim fits the revived category but is adoption-blocked by evidence-tier dishonesty (memory-backend illusion), missing score/feedback export into where teams already look, and category confusion with LLM-as-judge eval platforms.

**Recommendation**:
Implement a single VerdictExport seam on the existing CLI `_emit` funnel (do not grow gate grammar): after each run, serialize {gate_id, verdict∈{present,absent,inconclusive}, evidence_tier, receipt_hmac, event_counts, ontology_violations} and fan out via optional adapters—(1) report formats: junit.xml + markdown attached at `_emit`; (2) platform scores: LangSmith testing.log_feedback/create_feedback(key='ooptdd.<gate>', score=1|0|null for inconclusive, comment=receipt hash); Langfuse PostScoreBodyFoundation-compatible score (name, traceId/observationId, comment, metadata); Phoenix span annotation/eval integration path already documented for code-based checks; (3) OTel attributes on a post-run span using gen_ai.evaluation.name/score.label/value when available. Enforce BackendCaps hard gate: tiers ≥ arrived require a queryable external backend (OpenObserve/ClickHouse/VictoriaLogs); memory/jsonl max out at emitted/local_pass unless OOPTDD_ALLOW_LOCAL_EVIDENCE=1. Ship a one-page WHAT_NOT_TO_DO: no span-tree DSL, no LLM-as-judge, no Tracetest clone, no semantic quality scores—only independent-store arrival of expected runtime evidence.

**Alternatives**:
  - Compete as full agent-eval platform (DeepEval/promptfoo/LangSmith parity)—high mindshare, destroys narrow falsifiable claim and credibility of small tool
  - Only document the claim without exporters—zero code cost, leaves the 89% observability / 52% eval gap unbridged so ooptdd stays invisible beside platforms
  - Rebuild Tracetest-style span selectors for microservices—category already commercially failed; burns design budget on what agent teams do not ask for first
  - Soft-warn on memory backend only—preserves DX but perpetuates the adoption-killing illusion that generator==verifier tests prove external arrival

**Caveats**: Tracetest Cloud EOL is late-2024; OSS Tracetest still exists so 'category dead' is commercial-stagnation not zero usage. LangChain survey (n≈1340, Nov–Dec 2025, published Jun 2026) is vendor-run—observability 89% vs evals 52% is directional. Ranking of adoption costs (memory illusion > missing exporters > no span-tree) is inferred from competitor failure modes and survey gap, not ooptdd user telemetry. devops.com article content was thin on fetch. Exact Phoenix annotation API surface not fully walked beyond docs overview; Langfuse score create path confirmed via PostScoreBodyFoundationSchema only.

**References**:
  - https://www.datadoghq.com/blog/ai/harness-first-agents/
  - https://www.honeycomb.io/blog/your-questions-about-ai-agents-production-feedback-answered
  - https://www.langchain.com/state-of-agent-engineering
  - https://tracetest.io/blog/end-of-life-announcement-for-tracetest-cloud
  - https://arxiv.org/html/2605.18747v1
  - https://devops.com/observability-driven-continuous-testing-in-cloud-native-devops/
  - malabi/README.md
  - deepeval/README.md
  - langsmith-sdk/python/tests/evaluation/test_decorator.py
  - langsmith-sdk/python/langsmith/client.py
  - langfuse/packages/shared/src/features/scores/interfaces/shared.ts
  - phoenix/docs/phoenix.mdx
  - openinference/README.md
  - semantic-conventions/docs/registry/attributes/gen-ai.md
