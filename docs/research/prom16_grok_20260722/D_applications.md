# PROM16 Grok — Axis D: applications (적용 — 에이전트 CI·semconv 현행화·채택)

> cycle `prom16-ooptdd-oss-absorption-grok-20260722` · lenses: official-docs / alternatives / pitfalls / trends-2026

## D1 — applications::official-docs (HIGH)

**findingId**: `finding_e8e9c4bcf468cf4f` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-12` · 2026-07-22T00:00:00Z

**한줄요약**: Pin ooptdd gen_ai preset to gen-ai-dev/1.42.0-dev: provider.name required, message attrs not events, evaluation.result + agent/tool ops, dual-track 1.30 legacy.

**Root cause / gap**:
ooptdd pins SEMCONV_VERSION 1.30.0-experimental while GenAI conventions diverged hard after v1.37 (event→attribute consolidation, gen_ai.system→gen_ai.provider.name), gained evaluation/agent/MCP surface through v1.41, and at core v1.42 moved canon out of open-telemetry/semantic-conventions into open-telemetry/semantic-conventions-genai under development schema gen-ai-dev/1.42.0-dev. A static 1.30 ontology preset will false-RED modern emitters and false-GREEN emitters that still use deprecated names.

**Recommendation**:
Bump the ontology-preset registry (not a one-off hardcode) to dual-track: keep preset id `otel_gen_ai@1.30.0-experimental` for freeze tests, and add default `otel_gen_ai@gen-ai-dev-1.42.0` sourced from semantic-conventions-genai model YAML. Wire pin fields as `core_semconv=1.43.0` + `genai_schema_url=https://opentelemetry.io/schemas/gen-ai-dev/1.42.0-dev`. Required closed_world RED rules for the new preset: (1) client spans with gen_ai.operation.name in {chat,generate_content,text_completion,embeddings,create_agent,invoke_agent} MUST have gen_ai.provider.name; (2) execute_tool MUST have gen_ai.operation.name=execute_tool AND gen_ai.tool.name (span name SHOULD be execute_tool {tool.name}); (3) reject sole gen_ai.system as the provider identity (optionally dual-accept gen_ai.system only under explicit legacy flag, never as sole GREEN); (4) forbid content gates that still expect per-message events gen_ai.{system,user,assistant,tool}.message or gen_ai.choice—require gen_ai.input.messages / gen_ai.output.messages / gen_ai.system_instructions on the span or event gen_ai.client.inference.operation.details; (5) evaluation seam: event gen_ai.evaluation.result requires gen_ai.evaluation.name, with score.value/score.label conditionally required when applicable; (6) expand operation enum with invoke_agent, create_agent, invoke_workflow, plan, retrieval, embeddings, generate_content, and memory ops (search_memory, create_memory, update_memory, upsert_memory, delete_memory, create_memory_store, delete_memory_store); (7) token attrs input_tokens/output_tokens (not prompt/completion); (8) provider enum include x_ai (not xai), moonshot_ai, azure.ai.*; (9) invoke_agent.internal must NOT require gen_ai.provider.name. Ship one RED/GREEN proof pair under the new pin via existing proof-examples path and attach preset version to CLI _emit/report JSON so CI receipts name the pin.

**Alternatives**:
  - Stay pinned at 1.30.0-experimental only and document incompatibility with modern OTel GenAI emitters — lowest churn but blocks agent-CI credibility and false-fails current instrumentations.
  - Code-generate the ontology preset from Weaver-resolved model/*.yaml of semantic-conventions-genai on each release (CI job) instead of hand YAML — highest fidelity, more tooling cost.
  - Accept both old and new attribute names indefinitely without dual presets — reduces RED false positives short-term but blurs closed_world conformance and weakens mutate --min-score signal.

**Caveats**: GenAI remains DocumentStatus Development; genai repo has no tagged releases (CHANGELOG Unreleased only; schema_url still TODO in README). Local semantic-conventions clone only retains deprecated stubs for gen_ai (live model lives in semantic-conventions-genai). ooptdd source tree was not opened in this cell—pin/preset path names assume the stated ontology preset registry seam. Exact 1.30.0-experimental ooptdd YAML contents were not audited here; update checklist is relative to official post-1.30 deltas. Span-event API deprecation is ecosystem-wide (logs-based events) and reinforces GenAI’s log-event model but is not GenAI-specific stabilization.

**References**:
  - https://opentelemetry.io/docs/specs/semconv/gen-ai/
  - https://github.com/open-telemetry/semantic-conventions-genai
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/README.md
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-events.md
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-agent-spans.md
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/model/manifest.yaml
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/model/gen-ai/registry.yaml
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/model/gen-ai/spans.yaml
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/model/gen-ai/events.yaml
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/versions.env
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/changelog.d/217.breaking.md
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/changelog.d/257.breaking.md
  - https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/changelog.d/289.breaking.md
  - https://opentelemetry.io/blog/2024/otel-generative-ai/
  - https://opentelemetry.io/blog/2026/deprecating-span-events/
  - semantic-conventions/CHANGELOG.md
  - semantic-conventions/docs/gen-ai/README.md
  - semantic-conventions/docs/registry/attributes/gen-ai.md
  - semantic-conventions/model/gen-ai/deprecated/registry-deprecated.yaml
  - semantic-conventions/model/gen-ai/deprecated/spans-deprecated.yaml

---

## D2 — applications::alternatives (HIGH)

**findingId**: `finding_d6241e296c03c73a` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-13` · 2026-07-22T18:30:00Z

**한줄요약**: Structural agent trajectories map to ooptdd gates; unique value is external arrival+LTL3+receipts—add @check tool_call_accuracy + _emit eval_scores vs Ragas/Phoenix/Inspect/AgentOps.

**Root cause / gap**:
Agent-trajectory verification in production CI is fragmented across offline metric libraries (Ragas, DeepEval), LLM-judge platforms (Phoenix), full eval harnesses (Inspect AI), and replay observability (AgentOps). Structural properties (required/forbidden tool, ordered path, ontology-required attrs) already map to ooptdd event-arrival gates, but ooptdd lacks a first-class deterministic tool-trajectory check plus score-export bridge, so teams default to in-process scorers that never prove external arrival or three-valued inconclusive.

**Recommendation**:
Ship one agent-trajectory interop package on existing seams: (1) register `@check("tool_call_accuracy")` (optional `@check("tool_call_f1")`) that, after backend query of arrived events with `where` equality on `gen_ai.operation.name=execute_tool` / `gen_ai.tool.name`, flattens ordered tool names + JSON-equality args and scores exactly like Ragas ToolCallAccuracy (`strict_order` default True; score = arg-match fraction × sequence-aligned bit; empty-pred→0) without any LLM; (2) document a YAML gate pack composing present/absent|forbid + must_order|trajectory+within_s + conforms(gen_ai ontology) + forbid_errors + require_signature for the CI patterns required-tool / forbidden-tool / completion-after-tool-result; (3) extend CLI `_emit` with report format `eval_scores` mapping LTL3 present→1.0, absent→0.0, inconclusive→null into a small JSON list `{name,value,comment,source:"ooptdd"}` suitable for Langfuse scores / Phoenix annotations / LangSmith feedback, elevating evidence tier toward external_verdict. Ship one RED/GREEN proof pair under examples using real gen_ai.* emits + independent OpenObserve/JSONL verifier.

**Alternatives**:
  - Ragas ToolCallAccuracy/ToolCallF1/AgentGoalAccuracy (Apache-2.0): wins for offline dataset ranking with continuous 0–1 scores and LLM goal judges on message histories; loses when you need generator≠verifier, store readback, LTL3 inconclusive, or HMAC CI receipts—structural tool sequence/args reduce to ooptdd present+must_order+where, goals/topic do not.
  - DeepEval ToolCorrectnessMetric/ArgumentCorrectnessMetric/TaskCompletionMetric (Apache-2.0): wins for pytest-flavored agent suites with expected_tools + optional LLM selection scoring; loses on external arrival tiers and ontology RED—ordering/exact tools reduce to ooptdd gates; plan/step-efficiency soft metrics do not.
  - Phoenix/Arize tool-selection, tool-invocation, and Agent Trajectory LLM-judge (Elastic-2.0 server; OTel/OpenInference): wins for production trace UI, fuzzy trajectory quality, and annotation workflows; loses for deterministic flake-resistant CI gates—ordered tool-call presence reduces to ooptdd; LLM correct/incorrect path efficiency does not.
  - Inspect AI scorers + .eval log (MIT, UK AISI): wins as full agent eval harness (sandbox, multi-model, transcript ToolEvent function/arguments/result/error, custom @scorer over TaskState); built-ins (includes/match/exact/model_graded_*) target final answers not tool paths—trajectory reduces only via custom scorers or scanners; ooptdd unique value is independent external-store verifier + evidence ladder + mutate, not research log packaging.
  - AgentOps session replay/export (MIT SDK/app): wins for multi-framework auto-instrument, cost, and human time-travel debug (export tools/LLM/actions via REST); is not a gate engine—tool-call counts/errors can feed ooptdd as an event source but ooptdd remains the CI oracle.

**Caveats**: Local clones may lag PyPI; Ragas legacy ToolCallAccuracy vs collections API is mid-migration (v1 deprecation noted in docs). Phoenix agent-trajectory docs target Arize AX export/log_evaluations more than pure OSS Phoenix UI. AgentOps product mixes OSS MIT app with hosted dashboard—export REST needs API key. ooptdd where=equality-only still cannot express soft arg similarity, nested partial args, or LLM goal/topic judges; gen_ai.tool.call.arguments/result exist in semconv changelog but ooptdd selector grammar may not deep-match nested JSON without a dedicated check. DeepEval ToolCorrectness mixes deterministic ordering flags with optional LLM tool-selection scoring—do not claim pure determinism. No ooptdd source tree in this competitors workspace; recommendation binds to stated seams (check registry, gen_ai preset, _emit) from the 2026-07-22 audit brief.

**References**:
  - https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/
  - ragas/src/ragas/metrics/_tool_call_accuracy.py
  - ragas/docs/concepts/metrics/available_metrics/agents.md
  - https://arize.com/docs/ax/evaluate/evaluators/trace-and-session-evals/trace-level-evaluations/agent-trajectory-evaluations
  - phoenix/docs/phoenix/evaluation/pre-built-metrics/tool-selection.mdx
  - phoenix/docs/phoenix/evaluation/pre-built-metrics/tool-invocation.mdx
  - phoenix/docs/phoenix/evaluation/pre-built-metrics/tool-calling-eval.mdx
  - phoenix/docs/phoenix/cookbook/evaluation/evaluate-an-agent.mdx
  - phoenix/pyproject.toml
  - inspect_ai/src/inspect_ai/scorer/__init__.py
  - inspect_ai/src/inspect_ai/scorer/_match.py
  - inspect_ai/src/inspect_ai/log/_log.py
  - inspect_ai/src/inspect_ai/event/_tool.py
  - inspect_ai/docs/eval-logs.qmd
  - inspect_ai/docs/multiple-scorers.qmd
  - inspect_ai/LICENSE
  - agentops/README.md
  - agentops/agentops/semconv/tool.py
  - agentops/LICENSE
  - https://docs.agentops.ai/v1/concepts/sessions
  - deepeval/deepeval/metrics/tool_correctness/tool_correctness.py
  - deepeval/skills/deepeval/references/metrics.md
  - deepeval/LICENSE.md
  - semantic-conventions/CHANGELOG.md

---

## D3 — applications::pitfalls (HIGH)

**findingId**: `finding_376e79d5c2f1288e` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-14` · 2026-07-22T12:00:00Z

**한줄요약**: Agent CI poisons itself via LLM-judge flakiness, retry re-rolls, and self-reported tools; ooptdd needs receipt-bound flake_budget quorum + tool-result corroboration, not judge-on-critical-path.

**Root cause / gap**:
Agent CI fails when teams treat nondeterministic agent trajectories and LLM-as-judge scores as binary pytest asserts: temperature-0 judges still flip, retries re-roll hard inputs and shift pass rates, self-emitted gen_ai tool spans are trusted as outcome proof, and store-lag is collapsed to fail-then-rerun-until-green—training engineers to silence red CI and poisoning both merge gates and anonymized success stories.

**Recommendation**:
Ship a first-class agent-CI anti-poison profile on existing seams—not new product surface. (1) YAML profile `profiles/agent_ci_v1.yaml` composing only structural gates: `conforms` gen_ai ontology (closed_world + pin SEMCONV; RED if `execute_tool` lacks `gen_ai.tool.name`), `present`/`forbid`/`must_order` for required/forbidden tools and completion-after-result, `require_corroboration` binding each `gen_ai.execute_tool` to a subsequent tool-result or external probe of the side effect (self-report alone never satisfies present), `forbid_errors`, `require_signature` on CI receipts, and `threshold` weighted quorum over N independent attempts instead of retry-until-green. (2) New `@check("flake_budget")` predicate: for (gate_id, spec_hash, run_id) never OR-merge present across attempts; each attempt gets its own HMAC receipt link; aggregate verdict is present only if ≥k attempts independently reach evidence-tier ≥arrived with present; else inconclusive (store lag / partial arrival) or absent—not a silent pass. Emit attempt_id, flake_rate, evidence_tier, and receipt chain via CLI `_emit` JSON (and later JUnit). (3) Extend `ooptdd mutate` with flake mutants (drop one tool event, delay arrival past timeWindow, omit tool.result while keeping execute_tool) and require min-score so weakened gates that still green under poison fail mutation. (4) Document hard ban: LLM-judge metrics (DeepEval GEval, promptfoo model-graded, LangSmith feedback) may attach as soft external probes / non-blocking annotations only—never as the sole critical-path present/absent for tool contracts. (5) Public case-study template: ship gate YAML + signed receipt hash-chain + evidence-tier ladder + flake_rate; refuse anonymized green-only narratives without those artifacts.

**Alternatives**:
  - Put LLM-as-judge (DeepEval GEval / promptfoo model-graded / continuous agent eval) on the merge-critical path with temperature=0 — scales semantic checks but Microsoft Foundry and survey literature show residual score flip and inter-model disagreement; tradeoff is faster quality coverage vs non-reproducible CI and merge roulette.
  - Deterministic trace replay from recorded OTLP/JSONL only (no live store arrival) — eliminates agent nondeterminism for regression but abandons ooptdd's generator≠verifier and arrived/queryable_causal tiers; catches contract drift in fixtures, not production emission or store lag.
  - Statistical multi-run only (N≥10, pass-rate thresholds) without structural gen_ai gates — absorbs trajectory variance but is expensive, still blind to missing required tool attrs, and invites gaming by selecting favorable seeds; worse credibility than receipt-bound structural gates plus optional soft judges.
  - Infrastructure flake escape hatches that ignore non-test failures (e.g. CI flags that suppress worker/unhandled errors) — unblocks PRs short-term but trains teams to ignore red CI and hides real regressions; opposite of ooptdd's three-valued inconclusive discipline.

**Caveats**: Did not execute ooptdd itself in this cell (not present under competitors clones); recommendation maps to the 2026-07-22 audit description of existing gates (threshold, require_corroboration, conforms, require_signature, CLI _emit, mutate) without verifying current API names in source. OTel gen_ai docs are mid-migration (main semconv pages mark attributes deprecated/moved to semantic-conventions-genai); preset currency risk is real but exact post-1.30 attribute renames were not fully re-audited here. Microsoft judge study is synthetic location-domain data—generalization to all agent CI is partial. Anonymized case-study traps are reasoned from simple-evals prompt-sensitivity notes and industry 'Other Models (Reported)/unknown prompt' patterns rather than a single legal/ethics paper on vendor case studies. Residual hard limits ooptdd cannot remove: adversarial omission of bad events by the SUT, semantic hallucination without external ground truth, and true multi-valid trajectories that no structural gate can uniquely decide.

**References**:
  - https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/evaluating-ai-agents-can-llm%E2%80%91as%E2%80%91a%E2%80%91judge-evaluators-be-trusted/4480110
  - https://latitude.so/blog/how-to-choose-the-right-evaluation
  - https://eugeneyan.com/writing/llm-evaluators/
  - https://www.fiddler.ai/blog/opentelemetry-ai-observability-guide
  - https://opentelemetry.io/docs/specs/semconv/gen-ai/
  - https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
  - inspect_ai/docs/handling-errors.qmd
  - promptfoo/AGENTS.md
  - promptfoo/vitest.config.ts
  - semantic-conventions/model/gen-ai/deprecated/spans-deprecated.yaml
  - simple-evals/README.md
  - deepeval/docs/content/tutorials/summarization-agent/development.mdx
  - langsmith-sdk/python/README.md

---

## D4 — applications::trends-2026 (HIGH)

**findingId**: `finding_7cf928531483b80e` · agent `prom-prom16-ooptdd-oss-absorption-grok-20260722-15` · 2026-07-22T18:45:00Z

**한줄요약**: 2025–26 agent CI standardizes on OTel gen_ai trajectories; ooptdd should ship dual-version gen_ai presets, JUnit/markdown+HMAC receipt CI pack, not more LLM-judge evals.

**Root cause / gap**:
2025–2026 agent reliability practice has standardized on OTel GenAI telemetry as the interchange format (agent/tool spans, MCP, evaluation events) while CI credibility for small OSS tools comes from pytest/YAML-native quality gates, multi-format CI artifacts (JUnit), and public RED/GREEN proof—not from competing with DeepEval/Promptfoo on LLM-as-judge metrics. ooptdd already owns generator≠verifier positive-arrival + LTL3 + HMAC receipts + gen_ai ontology RED, but its preset is still pinned at SEMCONV 1.30.0-experimental while the ecosystem moved through v1.37–v1.41 (and split GenAI into semantic-conventions-genai at v1.42), report output is JSON-only (no JUnit/markdown), and there is no shipped agent-trajectory CI pack or receipt-as-CI-artifact story—so the niche is real but the adoption surface is incomplete.

**Recommendation**:
Ship a single Agent-Trajectory CI Reference Pack that reuses existing seams only: (1) Ontology preset registry—keep `otel-genai@1.30` frozen and add `otel-genai@post-1.37` (track semantic-conventions-genai schema_url) that requires gen_ai.operation.name in {execute_tool,invoke_agent,chat,...}, RED on execute_tool without gen_ai.tool.name, and accepts gen_ai.provider.name (successor of gen_ai.system); document dual-emission via OTEL_SEMCONV_STABILITY_OPT_IN. (2) Proof YAML gates under examples/: required_tool (present execute_tool where gen_ai.tool.name=X), forbid_tool (absent/forbid tool Y), must_order execute_tool→chat/completion within_s, completion-after-result trajectory, closed_world conforms RED missing required attrs, forbid_errors. (3) CLI _emit funnel—add junit.xml and markdown reporters mapping LTL3 present→pass, absent/forbid-violation→fail, inconclusive→skipped + system-out receipt hash. (4) CI recipe—pytest plugin + ooptdd mutate --min-score; upload HMAC receipt JSON as artifact; pack defaults require_signature=on (closes unsigned-when-key-present footgun for this pack). (5) Thin optional export only—post-verdict hook emits gen_ai.evaluation.result-shaped attributes (gen_ai.evaluation.name=ooptdd.<gate>, score.label=present|absent|inconclusive, score.value 1|0|null, explanation=gate id)—no DeepEval metric wrapper. Adoption story: instrument once with OTel gen_ai; ooptdd fails the PR if required tools never arrive in an external store; receipts prove independent verification. Credibility: publish the pack as a public agent-trajectory microbenchmark + mutate score badge + one case study (memory/jsonl local, OpenObserve optional).

**Alternatives**:
  - Full DeepEval ToolCorrectnessMetric / promptfoo assertion wrappers (tradeoff: faster mindshare, dilutes generator≠verifier and evidence-tier story; couples ooptdd to LLM-judge flakiness).
  - Compete as general LLM-eval framework with quality metrics (tradeoff: crowded market vs DeepEval/Promptfoo/Ragas; abandons arrival/LTL3 differentiation).
  - Only bump SEMCONV pin without CI pack (tradeoff: correctness currency but no adoption surface—JUnit/receipts/fixtures are what earned Promptfoo/DeepEval CI seats).
  - Full in-toto/SLSA test-result attestor + cosign (tradeoff: strong supply-chain narrative; higher design cost than HMAC receipt artifacts + optional later mapping to https://in-toto.io/attestation/test-result/v0.1).
  - OpenInference-only ontology preset (tradeoff: Phoenix-native; forks from OTel gen_ai vendor convergence Datadog/MLflow already ship).

**Caveats**: GenAI/MCP conventions remain Development-status with no public stabilization date; v1.42 moved gen_ai.* into open-telemetry/semantic-conventions-genai so clone docs under semantic-conventions/docs/gen-ai are stubs—preset tracking must follow the new repo/schema_url, not only this clone. Datadog marketing text mixes legacy labels (e.g. tool_call/agent_run) with v1.37+ attribute names—implement against OTel releases, not vendor blogs. Three-valued LTL3 inconclusive→JUnit skipped can hide store lag flakiness unless evidence-tier gate (arrived/queryable_causal) is required in the pack. Optional gen_ai.evaluation.* export is interoperability sugar, not a substitute for store readback. Credibility claims for Promptfoo star counts and production scale are vendor-asserted; simple-evals is deprecating new model rows (July 2025 notice)—use its transparency pattern, not its maintenance model.

**References**:
  - https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions
  - https://www.datadoghq.com/blog/llm-otel-semantic-convention/
  - https://opentelemetry.io/blog/2025/ai-agent-observability/
  - https://opentelemetry.io/blog/2026/genai-observability/
  - https://www.promptfoo.dev/docs/integrations/ci-cd/
  - https://github.com/in-toto/attestation/blob/v1.1.0/spec/predicates/test-result.md
  - semantic-conventions/CHANGELOG.md
  - semantic-conventions/docs/registry/attributes/gen-ai.md
  - semantic-conventions/model/gen-ai/deprecated/events-deprecated.yaml
  - deepeval/README.md
  - deepeval/deepeval/metrics/tool_correctness/tool_correctness.py
  - promptfoo/README.md
  - simple-evals/README.md
  - inspect_ai/README.md
  - mutmut/README.rst
  - openinference/spec/semantic_conventions.md
  - langfuse/README.md
  - phoenix/README.md
