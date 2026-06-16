# OOPTDD Adoption Design — Go-to-Market Strategy

## Summary

The standalone `airobotics-ailab/ooptdd` repository requires a **killer demo, docs roadmap, and phased release strategy** to establish positive-TDD (logs+correlation_id as ground truth + external arrival verification) as the canonical observability-testing pattern for agent-intensive systems. Core differentiator: **catches silent ingest loss** (the 22h undetected 401 bug) via read-back verification, not just write-confirm. Target: Python developers of distributed agents, LLM systems, and microservices where "test passed" ≠ "logs arrived."

## Sub-findings (3-5 with confidence)

### 1. Killer Demo Must Reduce Silent-Failure Category to <60s Runnable (HIGH confidence)
The demo should reproduce the **real 401 silent-loss bug** from prismv2/jg_bpc (22 hours of inspection tests logged locally, silently dropped server-side, only caught by manual oo.trace_cycle query). Concretely:
- **Setup**: Tiny event-emitting app (e.g., 5-test pytest suite calling a sync logger)
- **Spec**: YAML trace with 5 expected test outcomes (arrival + cardinality gates)
- **Fault injection**: Enable `skip_post=True` to simulate silent ingest loss while logger says "shipped OK"
- **Demo progression**: RED (gate count=0) → emit loop injected, gate GREEN (count=5) → "flip" skip_post=True, gate RED again
- **Technology**: In-memory backend (no external service) makes demo fully reproducible; ~2min runtime

This directly addresses the #1 adoption blocker: "show me this catches a real failure mode." Silent ingest is invisible to traditional pytest assertions.

### 2. Docs Tree Should Separate Concepts/Quickstart/Theory/Cookbook/FAQ (HIGH confidence)
The existing 16 research shards (A–D, E deepening) + METHODOLOGY.md contain expert-level depth but lack:
- **Concepts/**: 1-page each for "Why logs as spec?" / "What's correlation_id?" / "Why external verdict?" (onboarding)
- **Quickstart/**: "5-min setup" guide — copy oo_sink.py + oo_verify.py, write ONE gate YAML, pass conftest hook
- **Theory/**: Link to academic RV/LTL3/Dwyer patterns; link to E shard for practitioners who want math
- **Backends/**: Memory backend (docs), OpenObserve endpoint (docs + example .env), extensibility for Loki/Elastic
- **Cookbook/**: BPC cycle (worked example from E.5) + "detecting partial loss" + "temporal ordering without sequence" + "agent failure mode" (per E.7-③)
- **FAQ/**: "Why not Tracetest?" / "Why correlation_id not just trace ID?" / "Isn't this just OTel?" (competition table)

### 3. Repository Layout Should Embrace Minimalist Core + Pluggable Backends (HIGH confidence)
Structure (based on adoption friction + portability backbone from E.4):
```
ooptdd/
├── README.md (3-bullet pitch + 1-min demo)
├── METHODOLOGY.md (v1.1 canonical, unchanged import)
├── src/
│   └── ooptdd/
│       ├── __init__.py (public API: oo_sink, verify_trace, emit, session_finish)
│       ├── core.py (oo_sink.py origin)
│       ├── verify.py (oo_verify.py origin)
│       └── backends/
│           ├── memory.py (in-mem store for demo + offline tests)
│           ├── openobserve.py (prismv2 production)
│           └── [loki|elastic] (v0.3+ defer)
├── docs/
│   ├── concepts.md / quickstart.md / theory.md / cookbook.md
│   └── backends.md (+ reference)
├── examples/
│   ├── minimal_app.py (event emitter)
│   ├── gates/bpc_example.yaml
│   └── test_minimal.py (pytest fixture)
├── tests/
│   ├── test_core.py (unit)
│   ├── test_verify_fixture.py (integration)
│   └── test_end_to_end.py (multi-backend)
├── pyproject.toml
├── CHANGELOG.md (v0.1 / v0.2 / v0.3 roadmap)
└── LICENSE (Apache 2.0)
```
**Why minimalist**: Adoption friction = "I have to understand 16 shards + E deepening first." Core = {oo_sink, verify_trace, memory backend, conftest hook}. Everything else is extension, not blocker.

### 4. Phased Roadmap v0.1→v0.3 With Crisp Acceptance Criteria (MEDIUM-HIGH confidence)
- **v0.1 (internal clean core, 2026-06)**: core + memory backend + demo fixture. Accept: (a) demo runs <2min, (b) memory backend passes 50 unit tests, (c) no external deps except pytest (optional OO_URL), (d) docs/quickstart readable in 5min.
- **v0.2 (infra integration, 2026-07)**: OpenObserve + Otel-semantics + backends/openobserve.py. Accept: (a) oo-mcp integration test with real oo trace, (b) correlation_id + cycle_id propagation verified, (c) clock-skew window tests (per E.2), (d) docs/backends + docs/cookbook shipped.
- **v0.3 (ecosystem+announce, 2026-08)**: Loki/Elastic adapters + PyPI publish + public announcement. Accept: (a) 3 backends shipped, (b) <50 GitHub issues backlog (resolve P0+P1), (c) 10+ adoption case studies (internal teams), (d) blog post + talk outline ready.

**Acceptance is outcome-based, not feature-based.** v0.1 succeeds when new users write 3-line conftest + 1-gate YAML and demo runs, not when "code is clean."

### 5. Elevator Pitch & Competition Table (MEDIUM confidence)
**3-bullet pitch:**
- **For developers of distributed agents/microservices**: "Stop assuming 'test passed' means logs arrived. OOPTDD verifies the entire journey—from your app to external ground truth—catching silent failures like 22h of dropped ingest."
- **Killer differentiator**: Unlike Tracetest (UI-first, no pytest fixture) or plain caplog (single-process), ooptdd provides **outcome-based verification** (external read-back proof + correlation_id atomicity) with **zero external infra in dev** (memory backend).
- **3-letter hook**: "OO = Outcome + Observability." Your test passes, but did the log store receive it?

**Comparison rows (vs competitors):**

| Feature | ooptdd | pytest-opentelemetry | Tracetest | plain caplog | Langfuse-evals |
|---------|--------|---------------------|-----------|--------------|---|
| **Correlation_id fixture** | ✅ conftest hook | ✅ auto-export | ❌ | N/A (single-process) | ✅ |
| **External verdict verification** | ✅ (read-back proof) | ❌ (export-only) | ✅ (but UI/manual) | ❌ | ⚠️ (LLM-eval, not observability) |
| **Silent ingest loss detection** | ✅ (E.1 core) | ❌ (assumes store) | ❌ (assumes API) | ❌ | ❌ |
| **Offline demo (no infra)** | ✅ memory backend | ❌ | ❌ | ✅ | ❌ |
| **LTL3 temporal predicates** | ✅ (partial-order gate) | ❌ | ⚠️ (limited) | ❌ | ❌ |
| **pytest-native plugin** | ✅ sessionfinish hook | ✅ | ⚠️ (sidecar) | ✅ | ❌ (standalone) |
| **Agent loop protocol** | ✅ (E.7 oracle problem aware) | ❌ | ❌ | ❌ | ✅ (but GenAI semconv unstable) |
| **Backends (pluggable)** | ✅ memory/OO/[Loki/Elastic] | ✅ (OTel exporters) | ⚠️ (OTel ingestion only) | N/A | ⚠️ (proprietary) |

## Raw Quotes (≥4 attributed with URL)

1. **"Silent failures look like success … agentic systems fail with semantic errors not explicit exceptions"** — Arize blog, 2026. *Directly validates killer demo thesis: ingest success != outcome success.* [Source](https://arize.com/blog/best-ai-observability-tools-for-autonomous-agents-in-2026/)

2. **"The magic of distributed tracing is in conftest.py — every HTTP call automatically carries trace context, and pytest_runtest_makereport captures test failures"** — Pydantic Logfire, 2026. *Confirms ooptdd's session_finish hook strategy aligns with industry practice.* [Source](https://pydantic.dev/articles/tests-observability)

3. **"Three-valued semantics (true, false, inconclusive) is adequate for partial observation of a running system"** — Runtime Verification for LTL (Bauer–Leucker–Schallhart). *Academic validation for E.6's LTL3 foundation; "?" resolution via polling.* [Source](https://dl.acm.org/doi/abs/10.1145/2000799.2000800)

4. **"Trace-based testing verifies both operation outcome AND traces, catching bugs like broken context propagation that unit tests miss"** — OpenTelemetry official blog, 2023. *Endorses outcome+trace duality at core of ooptdd.* [Source](https://opentelemetry.io/blog/2023/testing-otel-demo/)

5. **"Peer recommendations drive 78% of developer tool discovery; adoption happens when developers see consistent improvements shaped by feedback"** — daily.dev GTM guide, 2025. *Shapes v0.1→v0.3 phased roadmap: demo → community feedback → v0.2/v0.3.* [Source](https://business.daily.dev/resources/developer-go-to-market-strategy-from-launch-to-adoption/)

6. **"Observability in 2026 shifts from tools to a unified telemetry standard enabling cross-signal correlation and automated workflows"** — CORE Systems blog, 2025. *Market context: ooptdd as "TDD-native observability standard."* [Source](https://core.cz/en/blog/2025/observability-opentelemetry-2026/)

## Alternative Recommendations

1. **Pure OTel-plugin approach** (instead of custom correlation_id): Adopt pytest-opentelemetry or similar, delegate trace propagation to W3C traceparent header. *Con*: Silent ingest loss (E.1) remains invisible because plugin trusts exporter; read-back verification is still external layer. *Trade-off*: Simpler adoption for teams already on OTel, but loses "catching silent failures" unique value.

2. **UI-first (Tracetest model)**: Ship with web dashboard + trace query UI before pytest plugin. *Con*: Higher onboarding friction; developer has to leave pytest. *Rationale*: Some teams prefer visual RCA. *Adoption*: Defer to v0.2+ if demand signals.

3. **Merge into prismv2 codebase** (instead of standalone repo): Keep oo_* under jg_bpc + bpc-specific docs. *Con*: No cross-project adoption; external teams see "BPC internal tool." *Decision*: v6 protocol requires standalone for reusability.

## Counter-arguments / Caveats

1. **"This is just OTel with extra gates"**: Partially true. ooptdd assumes OTel-exportable traces (oo_sink.py uses generic JSON envelope, E.4.1 carbon-copy between prismv2/lakatotree). Difference is *outcome-based verification* (read-back proof) + *agent-aware* silent-failure detection (E.7-③ oracle problem). OTel alone doesn't detect dropped ingest; ooptdd does. Marketing: "OTel for the test layer, with outcome verification built in."

2. **"LTL3 + Dwyer patterns are too academic for most developers"**: Valid. Docs must hide complexity: Quickstart says "write count>=1 gate" without mentioning LTL; Theory shard is optional. E.6's "EXISTENCE is prefix-monotone, ABSENCE is not" should be **cookbook rules of thumb**, not formal semantics.

3. **"In-memory backend limits real-world demo validity"**: True for production scenarios, but v0.1 demo goal is to show **concept**, not scale. Memory backend + fault-injection (skip_post=True) proves the thesis: "caught silent ingest loss." Real oo backend is v0.2.

4. **"Requires external .env coordination (OO_URL, OO_PASS)"**: Yes, but intentional (E.4.2 no-baked-defaults rule prevents production misconfiguration). Quickstart must show how to run offline with `export OO_PASS= OO_URL=` (noop mode) for first-time users.

5. **"Silent failures in agents are rare if you instrument well"** (counterargument to killer demo thesis): Observability is usually *insufficient*, not complete. The jg_bpc 22h case (401 on ingest endpoint) happened *despite* shipping logging code that thought it succeeded. E.7-④ "what falsifies the value claim" identifies this: "if strict-mode false-fail rate > silent-loss detection gain, method fails." Killer demo must show *measured* replay of 22h scenario at scale (defer to v0.2).

## Search Trail (queries used)

1. `pytest opentelemetry tracetest integration observability testing 2025 2026`
2. `log-based testing framework LTL temporal verification runtime verification`
3. `pytest plugin architecture distributed tracing correlation_id test fixture`
4. `go-to-market strategy Python open source library adoption roadmap demo`
5. `AI agent testing autonomous verification observability silent failures 2026`

## Design Decisions Rationale (First Commit v0.1)

**What to include v0.1:**
- ✅ `src/ooptdd/{core.py (oo_sink), verify.py (oo_verify), __init__.py}`
- ✅ `src/ooptdd/backends/memory.py` (in-mem store, fully functional)
- ✅ `examples/{minimal_app.py, test_minimal.py, gates/example.yaml}`
- ✅ `docs/{quickstart.md, concepts.md}` (500-line max each)
- ✅ `tests/{test_core.py, test_verify.py}` with 50 unit tests
- ✅ `METHODOLOGY.md` (import from PROM16 shard)
- ✅ `README.md` (3-bullet pitch + demo link + 1-line table)
- ✅ `pyproject.toml` with dev dependencies (pytest, pydantic)
- ✅ `.github/workflows/` (test + lint on push, no publish)

**What to defer (v0.2+):**
- ❌ `src/ooptdd/backends/openobserve.py` (v0.2)
- ❌ `docs/{theory.md, backends.md, faq.md}` (v0.2)
- ❌ `src/ooptdd/backends/{loki,elastic}.py` (v0.3)
- ❌ PyPI publish (v0.3)
- ❌ Public announcement / conference talk (v0.3)

**Why this split:**
- v0.1 must prove concept in isolation (demo runs, no external deps, internal teams try it)
- v0.2 validates production readiness (real oo integration, backfill docs)
- v0.3 is ecosystem maturity (multiple backends, public signaling)

## Concrete First-Commit Files

### File A: `README.md`
```markdown
# ooptdd — Outcome + Observability Test-Driven Development

Test passed ≠ Logs arrived. ooptdd verifies the full journey.

**Why?** Traditional pytest catches test assertions. ooptdd catches silent failures: 
your test passes, but your logs silently dropped (22h undetected in production). 
Read-back verification + correlation_id atomicity = outcome-based testing.

**For:** Developers of distributed agents, microservices, LLM systems.

**Get started in 5 min:**
```bash
pip install ooptdd  # v0.1 not yet published; git clone + pip install -e .
python examples/minimal_app.py  # tiny app emits 5 test outcomes
pytest examples/test_minimal.py  # RED (ingest not reached)
python -m ooptdd.verify --gates examples/gates/example.yaml  # GREEN after ingest
```

See [docs/quickstart.md](docs/quickstart.md) and [METHODOLOGY.md](METHODOLOGY.md).

## Features vs. Alternatives

| | ooptdd | Tracetest | pytest-opentelemetry | caplog |
|-|--------|-----------|----------------------|--------|
| Offline demo | ✅ | ❌ | ❌ | ✅ |
| Silent ingest detection | ✅ | ❌ | ❌ | ❌ |
| pytest fixture | ✅ | ⚠️ sidecar | ✅ | ✅ |
```

### File B: `src/ooptdd/__init__.py`
```python
"""ooptdd: Outcome + Observability TDD for distributed systems."""

from ooptdd.core import emit, oo_sink, ship  # Event emission
from ooptdd.verify import verify_trace, session_finish  # Verification
from ooptdd.backends.memory import MemoryStore  # Demo backend

__version__ = "0.1.0"
__all__ = ["emit", "oo_sink", "ship", "verify_trace", "session_finish", "MemoryStore"]
```

### File C: `examples/gates/example.yaml`
```yaml
# Minimal example: 5 test outcomes must arrive.
gates:
  - name: "test_outcomes_arrived"
    sql: "SELECT count(*) AS c FROM test_session WHERE correlation_id='${CID}'"
    result_key: "c"
    op: ">="
    threshold: 5
    description: "All 5 tests documented in backend (catches silent ingest loss)"
```

### File D: `pyproject.toml`
```toml
[project]
name = "ooptdd"
version = "0.1.0"
description = "Outcome + Observability TDD — catch silent failures in distributed systems"
requires-python = ">=3.10"
dependencies = ["pytest>=7.0", "pydantic>=2.0"]

[project.optional-dependencies]
openobserve = ["openobserve-python>=0.2.0"]  # v0.2+
dev = ["pytest-cov", "black", "mypy"]

[tool.pytest.ini_options]
python_files = ["test_*.py"]
```
