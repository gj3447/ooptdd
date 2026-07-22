# PROM16 Grok — Axis B: integration (통합 — eval 플랫폼 sink·emission source)

> cycle `prom16-ooptdd-oss-absorption-grok-20260722` · lenses: official-docs / alternatives / pitfalls / trends-2026

## B1 — integration::official-docs (HIGH)

**findingId**: `finding_55a167392141258b` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-4` · 2026-07-22T12:00:00Z

**한줄요약**: Official sinks: DeepEval BaseMetric, promptfoo python/js/webhook+hooks, LangSmith create_feedback/evaluate, Langfuse scores (not OTel), Phoenix CODE annotations—fan out via VerdictEnvelope on _emit.

**Root cause / gap**:
ooptdd already produces independent three-valued arrival verdicts and HMAC receipts, but has no export path into the official score/feedback/assertion contracts of DeepEval, promptfoo, LangSmith, Langfuse, or Phoenix. Without a single VerdictEnvelope→sink mapping attached to the existing CLI `_emit` funnel, competitors stay alternatives instead of distribution channels, and three-valued LTL3 outcomes are repeatedly collapsed ad-hoc into binary pass/fail.

**Recommendation**:
Add optional extra `ooptdd[integrations]` with a canonical `VerdictEnvelope` (verdict∈{present,absent,inconclusive}, gate_id, evidence_tier, receipt_hash/chain tip, reason, optional trace_id/span_id/run_id) and register sinks on the existing CLI `_emit` funnel so one gate run can fan out without forking report logic. Ship five thin adapters that call only official surfaces: (1) DeepEval `BaseMetric` subclass `OoptddArrivalMetric` — `measure(LLMTestCase)` runs programmatic gate/query against the external store (or precomputed receipt), sets `score` 1.0/0.0/0.5 and `success=score>=threshold`, `reason`/`error`, implements `a_measure`+`is_successful`+`__name__`; (2) promptfoo `type: python` `file://.../assert_ooptdd.py:get_assert` returning GradingResult `{pass, score, reason, namedScores:{ooptdd_present:…}}` plus optional `extensions` `afterEach` that posts the same envelope; (3) LangSmith `Client.create_feedback(run_id|trace_id, key='ooptdd.<gate>', score=0|1|None, value='present|absent|inconclusive', comment=reason, extra={evidence_tier,receipt_hash})` and/or `RunEvaluator.evaluate_run→EvaluationResult`; (4) Langfuse `create_score`/`POST /api/public/scores` with `dataType=CATEGORICAL` value present|absent|inconclusive (or BOOLEAN 0/1 + comment for inconclusive), `metadata` carrying receipt/evidence_tier — OTLP `/api/public/otel(/v1/traces)` remains SUT emission only, not the verdict sink; (5) Phoenix `client.spans.add_span_annotation(span_id, annotation_name='ooptdd.<gate>', annotator_kind='CODE', label=verdict, score=1|0|None, explanation=reason, metadata=…)` or batch `log_span_annotations` with `SpanAnnotationData`. Mirror portable OTel attrs on the verified span when available: `gen_ai.evaluation.name`, `gen_ai.evaluation.score.label`, `gen_ai.evaluation.score.value`, `gen_ai.evaluation.explanation` plus `ooptdd.*` receipt fields. Keep SDKs optional extras; default inconclusive must not become silent pass.

**Alternatives**:
  - Webhook-only: implement one HTTP handler matching promptfoo `type: webhook` {pass,score,reason} and let users glue other platforms — lowest ooptdd maintenance, highest user plumbing and no first-class DeepEval/LangSmith/Phoenix UX.
  - DeepEval-only distribution: ship just `OoptddArrivalMetric` for Confident/CI ecosystems — fastest eval-tool win, but misses observability sinks where agent traces already live.
  - OTel-attribute-only export: set gen_ai.evaluation.* on spans and skip Scores/Feedback/Annotations APIs — portable across collectors, but Langfuse/Phoenix/LangSmith treat first-class scores/feedback/annotations as the evaluation product surface, not nested span attributes.
  - promptfoo extension hooks alone (afterEach/afterAll) without an assertion type — good for side-effect sinks, weaker as a gate in the assertion matrix where pass/fail must fail the test row.

**Caveats**: DeepEval and promptfoo force binary success (score/threshold or pass bool); LTL3 inconclusive must be policy-mapped (recommended: fail CI / pass=False / score=0.5 / categorical value kept elsewhere). Langfuse OTel endpoint is trace ingest only (OTLP/HTTP JSON|protobuf, Basic Auth; gRPC unsupported); do not use it as the verdict path. Langfuse public score `source` accepts API|ANNOTATION only (EVAL reserved internal). Phoenix annotations require an existing span_id and at least one of label|score|explanation. LangSmith feedback batching wants `trace_id` for background upload. Official `gen_ai.evaluation.*` attrs are marked deprecated in the main semantic-conventions registry (moved to genai repo) — treat as best-effort portable mirror, not the primary sink. Clone trees are primary for API shapes; live cloud docs may lag or lead slightly. No end-to-end ooptdd runtime call was executed in this cell.

**References**:
  - https://deepeval.com/docs/metrics-custom
  - https://deepeval.com/docs/metrics-introduction
  - deepeval/deepeval/metrics/base_metric.py
  - promptfoo/site/docs/configuration/expected-outputs/python.md
  - promptfoo/site/docs/configuration/expected-outputs/javascript.md
  - promptfoo/site/docs/configuration/expected-outputs/deterministic.md
  - promptfoo/site/docs/configuration/reference.md
  - langsmith-sdk/python/langsmith/client.py
  - langsmith-sdk/python/langsmith/evaluation/evaluator.py
  - langsmith-sdk/python/langsmith/evaluation/_runner.py
  - https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk
  - https://langfuse.com/integrations/native/opentelemetry
  - langfuse/fern/apis/client/definition/score.yml
  - langfuse/web/src/pages/api/public/otel/v1/traces/index.ts
  - phoenix/packages/phoenix-client/src/phoenix/client/resources/spans/__init__.py
  - phoenix/packages/phoenix-client/src/phoenix/client/__generated__/v1/__init__.py
  - phoenix/packages/phoenix-client/examples/annotations/log_span_annotations_example.py
  - phoenix/docs/phoenix/tracing/tutorial/annotations-and-evaluations.mdx
  - semantic-conventions/docs/registry/attributes/gen-ai.md

---

## B2 — integration::alternatives (HIGH)

**findingId**: `finding_fba639b92a0513aa` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-5` · 2026-07-22T12:00:00Z

**한줄요약**: Ship ooptdd adapters onto host hooks; max L/M is OpenLLMetry zero-emit+queryable store, then promptfoo python assert, DeepEval BaseMetric, OTel eval attrs, then Langfuse/LangSmith/Phoenix sinks.

**Root cause / gap**:
ooptdd's missing eval/observability integrations are not a missing niche feature but a distribution and emission problem: adoption hinges on (1) zero-friction SUT telemetry that already matches the shipped gen_ai.* ontology, and (2) thin adapters that call ooptdd from hosts' existing extension seams rather than waiting for those hosts to depend on ooptdd. Shipping six first-class vendor plugins without a shared verdict substrate multiplies maintenance without proportional leverage.

**Recommendation**:
Ranked build order for max leverage/maintenance: (1) Treat OpenLLMetry (primary) and OpenInference+genai dual-write (secondary) as the default SUT emission source—ship a RED/GREEN proof recipe: Traceloop.init (or OI with OPENINFERENCE_ENABLE_GENAI_SEMCONV=true) → OTLP/export into a QUERYABLE backend (OpenObserve/JSONL/memory; never verify via ooptdd's OTLP write-only driver) → ooptdd gate with the existing gen_ai ontology preset selecting gen_ai.operation.name=execute_tool and requiring gen_ai.tool.name. Document that OpenInference alone emits tool.name under openinference conventions by default (DEFAULT_ENABLE_GENAI_SEMCONV=False), so zero-emit against the gen_ai preset needs dual-write or an openinference alias preset. (2) Direction: ooptdd ships adapters that CONSUME host hooks; do not pursue upstream 'native ooptdd' plugins. (3) On CLI _emit, add a VerdictSink protocol mapping LTL3 present/absent/inconclusive → {pass,score,label,reason} and implement OtelEvaluationAttributesSink first (gen_ai.evaluation.name / .score.value / .score.label / .explanation). (4) Ship examples/integrations/promptfoo_assert.py for type: python value: file://... returning GradingResult {pass,score,reason} from the gate API (webhook variant optional). (5) Optional extra ooptdd[deepeval]: OoptddArrivalMetric(BaseMetric) with measure/a_measure setting score 1.0/0.0 and success against threshold; inconclusive → self.error. (6) Wave-2 optional sinks only: LangSmith Client.create_feedback(key,score,comment,run_id|trace_id), Langfuse create_score(name,value,data_type,trace_id), Phoenix Client().spans.log_span_annotations(_dataframe) with annotator_kind='CODE'—all behind VerdictSink, not separate products.

**Alternatives**:
  - Wait for DeepEval/promptfoo/LangSmith to adopt ooptdd natively — high distribution if accepted, but slow, political, and loses control of generator≠verifier semantics; reject as primary path.
  - Ship only vendor verdict sinks (Langfuse/LangSmith/Phoenix) without emission recipe — improves UI visibility for existing ooptdd users but does not unlock zero-emit for instrumented agents; lower leverage than OpenLLMetry path.
  - Add full OpenInference ontology preset (tool.name, openinference.span.kind) instead of requiring gen_ai dual-write — better Phoenix-native zero-config, but forks from the already-shipped gen_ai.* preset and SEMCONV 1.30.0 pin; consider as additive alias, not replacement.
  - Upstream a first-class promptfoo assertion type 'ooptdd' — nicer UX than file://python, but ongoing TS maintenance inside promptfoo's assertion registry with low ROI vs existing python/webhook seams.
  - Rely solely on LLM-as-judge tools (DeepEval GEval, promptfoo llm-rubric, Ragas) for agent quality — wins when the defect is semantic content without reliable structured traces; loses when the bug is missing tool spans, attribute RED, order, or arrival tiers that ooptdd targets.
  - Use Malabi/Tracetest-style span selectors as the integration surface — alternative landscape already audited; full DSL copy banned; only partial conceptual borrow remains.

**Caveats**: ooptdd source was not in this competitors tree; integration seams assumed from the provided 2026-07-22 audit (OTLP write-only; gen_ai ontology preset; CLI _emit funnel). OpenLLMetry zero-emit is proven only for instrumented frameworks that set gen_ai.tool.name (e.g. langchain tools, openai-agents tools); app-local tools still need manual spans per OTel execute_tool guidance. OpenInference gen_ai dual-write is opt-in (default False). OTel gen_ai.evaluation.* attrs are marked deprecated/moved to semantic-conventions-genai in the registry docs—names may drift. Vendor sink APIs need stable run/trace/span ID correlation from the SUT path; not free. DeepEval custom metrics are float-score oriented—ternary inconclusive does not map cleanly without error/skip policy. Promptfoo python assertions execute user code; packaging must be an example or optional helper, not a privileged plugin. No end-to-end ooptdd+OpenLLMetry run was executed in this cell (read-only).

**References**:
  - openllmetry/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/callback_handler.py
  - openllmetry/packages/opentelemetry-instrumentation-openai-agents/tests/test_openai_agents.py
  - openllmetry/README.md
  - openinference/python/openinference-instrumentation/src/openinference/instrumentation/config.py
  - openinference/python/openinference-instrumentation/src/openinference/instrumentation/_genai_conversion.py
  - openinference/python/openinference-instrumentation/tests/test_genai.py
  - openinference/python/openinference-semantic-conventions/src/openinference/semconv/trace/__init__.py
  - openinference/spec/semantic_conventions.md
  - openinference/README.md
  - semantic-conventions/model/gen-ai/deprecated/spans-deprecated.yaml
  - semantic-conventions/docs/registry/attributes/gen-ai.md
  - deepeval/deepeval/metrics/base_metric.py
  - deepeval/docs/content/guides/guides-building-custom-metrics.mdx
  - promptfoo/src/assertions/python.ts
  - promptfoo/src/assertions/webhook.ts
  - promptfoo/src/assertions/index.ts
  - promptfoo/src/assertions/AGENTS.md
  - langsmith-sdk/python/langsmith/client.py
  - phoenix/docs/phoenix/tracing/how-to-tracing/feedback-and-annotations/llm-evaluations.mdx
  - phoenix/MIGRATION.md
  - https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk
  - https://www.promptfoo.dev/docs/configuration/expected-outputs/python/
  - https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/

---

## B3 — integration::pitfalls (HIGH)

**findingId**: `finding_e472e229f296d290` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-6` · 2026-07-22T12:00:00Z

**한줄요약**: Preserve LTL3 via VerdictExport+entry-point sinks; categorical first, binary only with explicit inconclusive_policy; fail-loud extras—never collapse inconclusive to bool/0.5.

**Root cause / gap**:
Eval and observability sinks force boolean/score contracts (DeepEval BaseMetric.score+success, promptfoo GradingResult pass/score, LangSmith create_feedback score) that cannot natively carry ooptdd LTL3 present|absent|inconclusive or evidence-tier. Naively mapping inconclusive→fail/pass/0.5 destroys arrival semantics; optional-extra sinks that import-fail as ModuleNotFoundError or silently no-op create partial-integration UX debt; competitor APIs churn (e.g. Phoenix client.annotations removal) breaks thin wrappers pinned to call-sites.

**Recommendation**:
Ship a single portable VerdictExport DTO and sink adapter layer on existing seams—not N one-off wrappers. (1) Canonical JSON (CLI _emit + programmatic API): {verdict: present|absent|inconclusive, evidence_tier, receipt_hmac_chain_head, gate_id, run_id/trace_id, backend, inconclusive_reason?}. Never drop the ternary before export. (2) Register sinks as entry-points (same pattern as backend drivers), e.g. ooptdd.verdict_sinks; lazy-import each competitor so core stays free of deepeval/langfuse/phoenix/langsmith. (3) Mapping policy required at adapter construction: for categorical sinks use LangSmith feedback_config type=categorical with categories present/absent/inconclusive (value label in value/comment, score only when present|absent), Langfuse Score data_type=CATEGORICAL with string_value=verdict, Phoenix span annotation label=verdict + annotator_kind=CODE + metadata evidence_tier/receipt; for binary sinks (DeepEval BaseMetric, promptfoo python assert) require explicit inconclusive_policy in {error, skip, fail} and ban defaulting inconclusive→pass or score=0.5—DeepEval measure() maps present→score=1.0 success=True, absent→0.0 False, inconclusive→set error and is_successful() False (or skip if policy=skip) with reason containing ooptdd verdict+tier; promptfoo returns {pass, score, reason, metadata:{ooptdd_verdict,evidence_tier}} and treats inconclusive as pass=false with reason prefix INCONCLUSIVE (not a soft 0.5). (4) Packaging: extras ooptdd[deepeval], [promptfoo] (docs-only/example assert), [langsmith], [langfuse], [phoenix]; on missing dep raise ImportError('pip install ooptdd[<extra>]') not bare ModuleNotFoundError; never fail-open (silent skip of export). (5) CI: pin competitor minors + contract tests that assert ternary round-trip and that adapter import without extras fails loudly; version-gate adapters with known-good ranges to absorb churn.

**Alternatives**:
  - Thin per-tool wrappers only (DeepEval metric class / promptfoo file:// assert / one LangSmith create_feedback call) — fastest ship, highest churn tax and highest risk of ternary collapse at each call-site.
  - Export only OTel span/log attributes (ooptdd.verdict, ooptdd.evidence_tier, ooptdd.receipt) and let platforms scrape — preserves semantics without SDK coupling, but loses first-class feedback/score UX and needs platform-side parsers.
  - Shell-out only (`ooptdd gate` subprocess from promptfoo/CI) without DTO — zero Python dependency surface, weak structured correlation (trace_id/receipt) and no sink-native scores.
  - Map ternary to continuous score (present=1, absent=0, inconclusive=0.5) for all sinks — simple aggregations, systematically misleads threshold/average UIs and promptfoo aggregate score>=threshold.

**Caveats**: Did not execute competitor packages or live APIs; API shapes from local clones as of 2026-07-22 workspace. Exact ooptdd CLI _emit / entry-point names not re-audited in this cell (trusted from CURRENT STATE brief). Langfuse Python SDK package is monorepo-server-centric here—Score wire shape taken from code_based_eval_handler; public cloud SDK method names may differ slightly. Phoenix annotation client surface still moving (client.annotations already removed; use current phoenix-client annotation helpers). No measured release-cadence statistics for deepeval/promptfoo beyond changelog evidence of frequent Phoenix breaking changes.

**References**:
  - deepeval/deepeval/metrics/base_metric.py
  - deepeval/deepeval/metrics/g_eval/g_eval.py
  - deepeval/pyproject.toml
  - promptfoo/src/assertions/python.ts
  - promptfoo/src/assertions/scriptResultNormalization.ts
  - promptfoo/src/assertions/assertionsResult.ts
  - langsmith-sdk/python/langsmith/schemas.py
  - langsmith-sdk/python/langsmith/client.py
  - langfuse/scripts/code-eval-runners/python/code_based_eval_handler.py
  - langfuse/worker/src/features/database-read-stream/trace-stream.ts
  - phoenix/CHANGELOG.md
  - phoenix/packages/phoenix-client/tests/client/utils/test_annotation_hepers.py
  - openinference/python/openinference-instrumentation/pyproject.toml
  - https://docs.confident-ai.com/docs/metrics-custom
  - https://docs.confident-ai.com/docs/metrics-introduction
  - https://github.com/pypa/packaging.python.org/issues/1605
  - https://hynek.me/articles/python-recursive-optional-dependencies/
  - https://discuss.python.org/t/help-packaging-optional-application-features-using-extras/14074

---

## B4 — integration::trends-2026 (HIGH)

**findingId**: `finding_f22dece781b6b684` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-7` · 2026-07-22T12:00:00Z

**한줄요약**: Target OTel gen_ai.evaluation.result once on _emit; map LTL3 labels; add thin LangSmith/Langfuse/Phoenix sinks plus DeepEval/promptfoo wrappers.

**Root cause / gap**:
2025–2026 did not produce a shared scores/feedback API across LangSmith, Langfuse, and Phoenix; each remains a proprietary sink (feedback key/score, POST /scores, span annotations). The only once-target interoperability surface that emerged is OpenTelemetry GenAI’s Development-status evaluation event `gen_ai.evaluation.result` (name/score.label/score.value/explanation, parented to the GenAI span or correlated via gen_ai.response.id). ooptdd already pins gen_ai.* for SUT emission and has a CLI `_emit` funnel, but has no canonical eval-result export—so N harness/platform integrations would multiply maintenance without that intermediate.

**Recommendation**:
Ship one canonical EvaluationResultMapper + OTel emitter on the existing CLI `_emit`/JSON report path (not N vendor SDKs first): for each gate (or overall receipt), emit a log/event named `gen_ai.evaluation.result` with `gen_ai.evaluation.name` = gate id or `ooptdd.<spec>`, `gen_ai.evaluation.score.label` ∈ {present, absent, inconclusive, pass, fail} (map LTL3 present→pass/present, absent→fail/absent, inconclusive→inconclusive), `gen_ai.evaluation.score.value` = 1.0 / 0.0 / omit-or-NaN-policy for inconclusive, `gen_ai.evaluation.explanation` = short reason + evidence-tier + receipt hash, optional `gen_ai.response.id` / trace_id from SUT context. Parent to the active GenAI span when available; else emit as independent event on the same trace. Then add thin optional sinks that consume that mapped payload only: LangSmith `create_feedback(key=name, score=value, comment=explanation)`, Langfuse `POST /api/public/scores` (name/value/comment/traceId), Phoenix `spans.log_span_annotations` (annotation_name, label, score). For inbound harnesses: DeepEval `BaseMetric.measure` wrapping `ooptdd gate` (success from non-inconclusive pass); promptfoo `type: python` assertion calling the same gate CLI/API. Treat OpenInference/openllmetry as SUT emission sources (OI dual-write via `TraceConfig.enable_genai_semconv` / OPENINFERENCE_ENABLE_GENAI_SEMCONV)—ooptdd’s gen_ai ontology already covers verification of their spans.

**Alternatives**:
  - Implement only vendor-native sinks first (LangSmith/Langfuse/Phoenix adapters without OTel eval events): faster UI integration for those three, but locks ooptdd into N API drift and ignores the only cross-vendor wire standard.
  - Export eval-result solely as freeform span attributes on a synthetic ooptdd span (not the named `gen_ai.evaluation.result` event): works on any OTLP backend today, but diverges from the GenAI SIG evaluation event model and loses backend auto-recognition as quality evaluations.
  - DeepEval-only or promptfoo-only first-class plugins without a shared mapper: maximizes one ecosystem’s UX; higher long-term cost when a second harness or score sink is requested.
  - Wait for GenAI conventions to leave Development / for Events API maturity before any export: lower churn risk, but leaves the documented MISSING eval-tool surface open while competitors ship proprietary score writes.

**Caveats**: GenAI evaluation conventions are still Development status (not stable); attribute names can still move, and OTel Events/log event APIs remain uneven across languages. Vendor UIs do not reliably auto-materialize `gen_ai.evaluation.result` as first-class scores—thin adapters remain necessary for LangSmith/Langfuse/Phoenix product surfaces. Three-valued ooptdd verdicts have no first-class field in the OTel eval event (only score.value + low-cardinality label); inconclusive mapping is an ooptdd policy choice. OpenInference dual-write is off by default (DEFAULT_ENABLE_GENAI_SEMCONV=False). Local clone of semantic-conventions only holds moved/deprecated gen_ai registry stubs; live event text was verified from semantic-conventions-genai raw docs. No claim that LangSmith/Langfuse/Phoenix share a common scores protocol—evidence shows they do not.

**References**:
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-events.md
  - https://github.com/open-telemetry/semantic-conventions-genai
  - https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
  - semantic-conventions/docs/registry/attributes/gen-ai.md
  - https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions
  - langsmith-sdk/python/langsmith/client.py
  - langfuse/fern/apis/client/definition/score.yml
  - https://langfuse.com/docs/evaluation/scores/data-model
  - phoenix/packages/phoenix-client/examples/annotations/log_span_annotations_example.py
  - phoenix/MIGRATION.md
  - openinference/python/openinference-instrumentation/src/openinference/instrumentation/_genai_conversion.py
  - openinference/python/openinference-instrumentation/src/openinference/instrumentation/config.py
  - openinference/python/openinference-instrumentation/src/openinference/instrumentation/_spans.py
  - openinference/spec/traces.md
  - deepeval/deepeval/metrics/base_metric.py
  - promptfoo/src/types/index.ts
  - promptfoo/src/assertions/index.ts
