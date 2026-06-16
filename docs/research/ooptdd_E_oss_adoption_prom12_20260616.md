# ooptdd — OSS Adoption Research (E shard)

> **Cycle**: `cycle-prom12-ooptdd-oss-20260616` · `/prom 12` · 2026-06-16
> **Question**: Which open-source projects / tools / standards can ooptdd borrow from, and for which component (verify / backend / gate / plugin / binding)?
> **Method**: 12-cell parallel web research over 6 axes (1 cell interrupted; covered by prior `D2`). Builds on the A–D prior cycle.
> **TL;DR**: ooptdd's *core thesis* (external-store readback + AST/sha256 `must_emit` binding + source-less-GREEN rejection for autonomous agents) is **genuinely novel** — no prior art has the triad. But almost every *surface* it exposes (gate YAML, verify matcher DSL, verdict lattice, query backend, event vocabulary, ontology validation) has a mature OSS standard it should align to instead of inventing.

---

## Consensus map (what to adopt, by component)

### `verify` — the assertion/verdict layer
| Source | License / maturity | Borrow | Priority |
|---|---|---|---|
| **LTL3** (Bauer–Leucker–Schallhart, TOSEM 2011) | academic, foundational | The `present/absent/inconclusive` lattice **is** LTL3 `{⊤,⊥,?}`. Cite it. Adopt **anticipatory emit** (⊥ as soon as no continuation can satisfy) + **monitorability** flag (spec that can stay `?` forever). Frame ooptdd honestly as *"LTL3 verdicts over a counting/past-time fragment"* — NOT full LTL. | **HIGH** (terminology) |
| **DejaVu** (past-time first-order LTL) | OSS, Scala, research | Past-time framing = "assert over what already arrived" → always conclusive, no future blocking. Most accurate theoretical label for ooptdd's model. | MEDIUM |
| **RTAMT** (Python STL/MTL online monitor) | OSS, pip, mature | **MTL bounded-interval operators** `F[a,b]`/`G[a,b]` are the principled form of clock-skew window + `heartbeat` (`G[0,T] event`). Online bounded-future algorithm = evaluate before full trace arrives. | MEDIUM (impl) |
| **pytest-structlog** | MIT, stable | **Matcher operator DSL** — `log.events >= [{...}]` (subset present), `log.has("evt", key=val)` partial-kwarg match, `log.count("evt")==3`. Most directly portable verify surface. | **HIGH** |
| **testfixtures.LogCapture** | MIT, mature | `check_present(order_matters=False)` = subset-in-any-order presence; fills the gap pytest-structlog's order-sensitive `>=` leaves. Should be ooptdd verify's default "present" mode (telemetry is unordered). | **HIGH** |
| **Inspect** (UK AISI) | MIT, mature | Verdict enum `CORRECT/INCORRECT/PARTIAL/NOANSWER` + `value_to_float` projection. `NOANSWER` ≈ `inconclusive`. Borrow the typed-verdict-+-projection pattern. | MEDIUM |
| **Hypothesis** | MPL-2.0, mature | Design vocabulary only: `@invariant`, `precondition()` (assert B only if A arrived), **shrink-to-minimal** → RCA reports the minimal failing event subsequence, not the whole dump. | LOW (dep) / MEDIUM (idea) |
| **Tracetest selector DSL** | MIT | CSS-style predicate selector `span[type=...]` + all/each quantifier semantics → richer `where`-filter than bare count. | HIGH (DSL) / LOW (infra) |

### `backend` — ship + query
| Source | License | Borrow | Priority |
|---|---|---|---|
| **"query portability is a myth"** | — | **CONFIRMED by 3 independent cells.** OTLP write is genuinely portable; read fragments hard. SQL family (OpenObserve/ClickHouse/SigNoz) is the only one that ports; LogQL is structurally hostile; TraceQL is trace-shaped + aggregation-gated. ooptdd's capability-honesty stance is correct. | — (validated) |
| **ClickHouse** | **Apache-2.0** | Best next query backend. `uniqExact` (exact cardinality) / `uniq` (HLL ~1.6% err). count/filter over wide-event schema is native. No AGPL friction. | **HIGH** |
| **SigNoz** | **MIT** | Best *integrated* next impl: OTLP-in (reuses ooptdd's otel write impl) + raw ClickHouse SQL out (reuses the OpenObserve SQL query layer). Bridges the two existing impls. | **HIGH** |
| **OpenObserve** (incumbent) | AGPL-3.0 | Keep. `approx_topk` (Space-Saving) as the cardinality primitive so gates don't OOM on `correlation_id`. ⚠ AGPL matters if ever embedded/redistributed. | KEEP |
| **Grafana Loki** | AGPL | Stays **unsupported** — justified. `correlation_id` is unbounded-cardinality = exactly Loki's forbidden anti-pattern. | LOW (don't add) |
| **OTel InMemorySpanExporter** | Apache-2.0 | Validates the zero-infra memory backend. **Load-bearing lesson: use `SimpleSpanProcessor` not `Batch` in tests** (batch buffering → flaky timing). **Flush-before-readback** to avoid false-absent. Swappable-exporter seam = same emit code runs hermetic or shipped. | MEDIUM–HIGH |
| **HMAC hash-chaining** (Schneier–Kelsey / Crosby–Wallach) | academic, HMAC=stdlib | Upgrade per-event HMAC → forward-secure **hash chain** (`mac_i=HMAC(k, entry_i‖mac_{i-1})`, `k_{i+1}=H(k_i)`). Detects deletion/reorder/backdating of receipts — agent can't silently drop an inconvenient event. Scope to xdist-controller-aggregated receipts (single writer). | MEDIUM |

### `gate` — YAML gate, RED→GREEN
| Source | License | Borrow | Priority |
|---|---|---|---|
| **OpenSLO v1** | **Apache-2.0**, stable | **Cheapest highest-value win.** Align gate YAML field names to a recognized standard: `op: lte\|gte\|lt\|gt` + `target`; `timeWindow` (rolling duration) = readback budget; `indicatorRef` (reusable indicator) = per-cid substitution template; `ratioMetric` (good/total) = "N of M expected events arrived". | **HIGH** |
| **Keptn quality gates** | Apache-2.0, CNCF | Split **`sli.yaml` (how to query) from `slo.yaml` (criteria)** — decouple "which trace to fetch" from "what is GREEN". Per-objective `pass`/`warning` tiers → maps to present/inconclusive/absent. Weighted `total_score` → quorum of expected events instead of all-or-nothing. `compare_with` prior runs → regression gating. trigger→returned-ID→fetch-result loop = ship→cid→readback. | **HIGH** |
| **promptfoo** | MIT, mature | YAML assertion DSL `{type, value, threshold, weight}` + test-level weighted threshold. Note its `latency`/`cost` are the only *non-content* assertion types — extend that idea to ooptdd's `trace-present`/`count`/`cardinality` custom types. `python`/`javascript` custom-assertion escape hatch = arbitrary backend query. | **HIGH** |
| **Pact** broker + can-i-deploy + pending-pacts | MIT, mature | Gate-as-**registry-query**: store (emitter-symbol-version × test-version × arrival-verdict) matrix; `can-i-deploy --to prod` = "every `must_emit` has a recorded GREEN for this build's sha against prod stream". **Pending pacts** = newly-added expectation doesn't break the build until it's passed once → solves "new event added but emitter not yet wired" → mark `inconclusive`/pending, not RED. **Provider states** = declare runtime precondition under which an event is expected (no false RED under wrong state). | **HIGH** |
| **backoff lib** (`on_predicate`) | MIT | Tiered timeouts as gate config (dev 30–60s / CI 10–20s / prod strict). **Distinguish timeout→`inconclusive` from observed-absent→`absent`.** Jitter to avoid thundering-herd on shared backend. | MEDIUM |

### `plugin` — pytest
| Source | License | Borrow | Priority |
|---|---|---|---|
| **DeepEval** | Apache-2.0, mature | `assert_test(case, metrics=[M(threshold=…)])` API (multi-metric, per-clause threshold, one assertion) + `deepeval test run` CLI wrapper around pytest. Closest structural analog; confirms ooptdd's pytest-item approach. | **HIGH** |
| **DeepEval `assert_tool_call` / promptfoo `trajectory:tool-sequence`** | — | Ordered trajectory check = ooptdd's `must_order`, as first-class pytest items. | HIGH |

### `binding` — Longinus / KG, ontology, vocabulary
| Source | License | Borrow | Priority |
|---|---|---|---|
| **CloudEvents** (CNCF) | Apache-2.0, stable | Envelope floor for EventType: REQUIRED `id`/`source`/`specversion`/`type` (map `name`→`type`, `correlation_id`→`subject`/extension); OPTIONAL `time`/`dataschema` (point an event at its own payload schema); 7-type closed type system for attr enums. | **HIGH** |
| **JSON Schema 2020-12** | BSD spec, ubiquitous | **ooptdd's 3 drift classes map 1:1**: `required` → missing-attr RED; `enum` → wrong-enum RED; `additionalProperties:false` → unknown-event/attr (closed-world) RED. Can be the literal validator behind `EventType.validate`. | **HIGH** |
| **OTel GenAI semconv** (`gen_ai.*`) | Apache-2.0, **experimental** | Verify vocabulary: `gen_ai.operation.name` enum (`invoke_agent`/`execute_tool`/`chat`), `gen_ai.tool.name`, `gen_ai.agent.id`, `gen_ai.provider.name` (incl. `anthropic`). ⚠ **Development/experimental — version-pin.** Adopt the settled attribute/operation enums; treat event-over-Logs path as unstable. | **HIGH** (vocab) |
| **OTel Logs data model + OTLP** | Apache-2.0, **stable** | `trace_id`/`span_id` on log records = standard correlation key, strengthens binding beyond name-match to per-run correlation. OTLP = the canonical proof of "write portable". | **HIGH** |
| **OpenLLMetry** (traceloop) | Apache-2.0, mature | Auto-instrument Claude/agent calls → OTLP → ooptdd otel backend, zero hand-rolled emission. Use as the upstream emitter for **real-oo dogfooding**. Logs signal off-by-default-one-flag → mirror as opt-in. | MEDIUM |
| **Confluent Schema Registry compat rules / AsyncAPI** | CCL (non-OSI) / Apache-2.0 | Borrow the **compat-direction taxonomy** (BACKWARD/FORWARD/FULL/TRANSITIVE) as ontology-*evolution* rules — gate "did this EventType change safely?", not just "is this instance valid?". TRANSITIVE guards incremental drift across many edits. Don't take Schema Registry as a dependency (Kafka-centric, non-OSI). | LOW (dep) / MEDIUM (rules) |
| **Spring Cloud Contract** stubs-in-git | Apache-2.0 | Version contracts beside code with commit sha (lighter than a broker for KG-native setup); **generate the gate from the contract** so the binding *is* the test. | MEDIUM |
| **OTel semconv naming grammar** | CC-BY | Dotted namespace rule `ooptdd.<app>.<event>` + "don't reuse another namespace as prefix" → mechanically rejectable typo/collision drift. Borrow grammar only, not the full HTTP/DB convention set. | MEDIUM |

---

## Novelty / differentiator (Step 4 — no conflict, a positioning result)

Every prior-art system was probed for ooptdd's core claim. **None has the triad:**
1. **Receipt read back from an *external* telemetry store** (not in-process harness capture).
2. **`must_emit` bound to a committed source symbol via AST + sha256** (drift-proof, source-less GREEN rejected).
3. **Anti-self-deception for an *autonomous agent* that could fabricate success** + HMAC integrity.

- **Tracetest** — trusts the human test author; no KG/AST binding, no HMAC, no "source-less GREEN rejected".
- **DeepEval / promptfoo / ragas / Inspect** — judge LLM **output content** (LLM-as-judge, nondeterministic); capture in-process; no external readback, no source binding.
- **Anthropic/OpenAI "evals as unit tests" (2026)** — *legitimizing prior art* (trace/transcript-as-ground-truth is now mainstream first-party guidance) but still assumes the harness captures the trace and grades by rubric/judge — does **not** mandate external-store readback or source-symbol binding.
- **Keptn / OpenSLO** — gate *deployments* on aggregate production SLIs over a timeframe (coarse, statistical, post-deploy); no per-task agent receipt.
- **Pact** — request/response replay contracts, not async emission observed in production.

→ **Position ooptdd explicitly as the "verified-receipt, no-self-grading, provenance-enforced" hardening of the now-mainstream evals-as-tests practice.** That sentence is the README hook.

The only apparent tension (eval frameworks are nondeterministic LLM-judge vs ooptdd is deterministic emission-presence) is **not a conflict** — it is exactly the differentiator. ooptdd verifies *that the instrumented event fired*, not *whether the output was good*.

---

## Prioritized adoption recommendation (the actual deliverable)

### Tier 1 — do now (cheap, high leverage, low risk)
1. **Align gate YAML to OpenSLO + Keptn vocabulary** — `op`/`target`/`timeWindow`/`indicatorRef`/`ratioMetric`, split SLI(query) from SLO(criteria). Rename fields; no new logic. *(gate)*
2. **Adopt JSON Schema as the literal `EventType.validate` engine** — `required`/`enum`/`additionalProperties:false` = ooptdd's exact 3 drift classes. *(binding/ontology)*
3. **Borrow pytest-structlog matcher DSL + testfixtures `order_matters=False`** for verify's present-mode surface. *(verify)*
4. **LTL3 / past-time honesty pass in METHODOLOGY.md** — cite Bauer–Leucker–Schallhart; state ooptdd is "LTL3 verdicts over a counting/past-time fragment" (not full LTL). Closes the A1/A3 honesty gap. *(verify/docs)*
5. **CloudEvents envelope floor** for EventType (`id`/`source`/`type`/`specversion` + `subject`=cid). *(binding)*

### Tier 2 — strong next steps
6. **ClickHouse (Apache-2.0) or SigNoz (MIT) as the next query backend** — reuses both existing impls (OTLP write + SQL query), permissive license, best cardinality primitives (`uniqExact`/`uniq`). Replace AGPL-exposure path. *(backend)*
7. **Pact-style gate-as-registry**: matrix + `can-i-deploy` + **pending/`inconclusive` instead of RED** for not-yet-wired emitters. Solves a real gate weak spot. *(gate/binding)*
8. **OTel GenAI semconv (version-pinned) as verify vocabulary** + `trace_id`/`span_id` (stable OTLP) as binding correlation key. *(binding/verify)*
9. **promptfoo `{type,value,threshold,weight}` assertion shape** + DeepEval `assert_test` + ordered `trajectory`/`must_order` as first-class pytest items. *(gate/plugin)*

### Tier 3 — opportunistic / research
10. **RTAMT MTL interval operators** (`F[a,b]`/`G[a,b]`) for window/heartbeat instead of ad-hoc windows. *(verify)*
11. **HMAC hash-chaining** (Schneier–Kelsey forward-secure) — upgrade integrity from per-record to tamper-evident chain. *(backend)*
12. **OpenLLMetry as the real-oo dogfood emitter** (auto-instrument Claude → OTLP → otel backend). *(binding/integration)*
13. **Schema Registry compat-direction taxonomy** as ontology-*evolution* rules (not as a dependency). *(binding)*
14. **OTel test-exporter discipline**: SimpleSpanProcessor + flush-before-readback for memory-backend determinism. *(backend)*

### Explicitly do NOT adopt
- **Grafana Loki** as a backend — `correlation_id` cardinality is structurally hostile (ooptdd's "unsupported" mark is justified).
- **Tracetest / Keptn server runtimes** — keep the DSL ideas, reject the heavy server+collector infra (contradicts zero-infra memory backend).
- **LLM-as-judge metrics** (ragas core) as ground truth — nondeterminism is the antithesis of ooptdd's design.
- **Confluent Schema Registry** as a dependency — Kafka-centric, non-OSI license.

---

## Sources
Tracetest (kubeshop, MIT) · Malabi (Aspecto, archived) · OTel InMemorySpanExporter / semconv / GenAI semconv / Logs data model / OTLP (CNCF, Apache-2.0) · OpenLLMetry (traceloop, Apache-2.0) · Bauer–Leucker–Schallhart LTL3 (TOSEM 2011) · RTAMT (nickovic) · JavaMOP/RV-Monitor · DejaVu (NASA/JPL) · Hypothesis (MPL-2.0) · pytest-structlog (MIT) · structlog.testing · testfixtures (MIT) · Pact (pact.io, MIT) · Spring Cloud Contract (Apache-2.0) · Langfuse / Arize Phoenix / LangSmith (see prior D2) · DeepEval (Apache-2.0) · promptfoo (MIT) · Inspect/inspect_ai (MIT) · ragas (Apache-2.0) · CloudEvents (CNCF) · JSON Schema 2020-12 · AsyncAPI v3 · Confluent Schema Registry · OpenObserve (AGPL-3.0) · ClickHouse (Apache-2.0) · Grafana Loki/Tempo (AGPL) · SigNoz (MIT) · Keptn (CNCF, Apache-2.0) · OpenSLO v1 (Apache-2.0) · backoff (MIT) · Crosby–Wallach / Schneier–Kelsey tamper-evident logging · Anthropic/OpenAI evals-as-tests guidance (2026).
