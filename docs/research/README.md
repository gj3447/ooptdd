# Research — the study behind this repo

This directory is the empirical backing for ooptdd's design. It is the output of
a structured 16-cell research cycle (4 axes × 4 lenses) run on
2026-06-16 before the standalone project was assembled: each shard is one cell,
web-researched and adversarially reviewed.

| | official / standards | prior-art / competition | pitfalls / limits | design recommendation |
|---|---|---|---|---|
| **A · Identity & positioning** | [A1](ooptdd_A1_identity_official.md) | [A2](ooptdd_A2_prior_art_landscape.md) | [A3](ooptdd_A3_identity_pitfalls.md) | [A4](ooptdd_A4_identity_design.md) |
| **B · Harness packaging (pytest plugin)** | [B1](ooptdd_B1_packaging_official.md) | [B2](ooptdd_B2_packaging_priorart.md) | [B3](ooptdd_B3_packaging_pitfalls.md) | [B4](ooptdd_B4_packaging_design.md) |
| **C · Backend abstraction & portability** | [C1](ooptdd_C1_backend_official.md) | [C2](ooptdd_C2_backend_prior_art.md) | [C3](ooptdd_C3_backend_pitfalls.md) | [C4](ooptdd_C4_backend_design.md) |
| **D · Prior-art, competition & adoption** | [D1](ooptdd_D1_adoption_official.md) | [D2](ooptdd_D2_competition_priorart.md) | [D3](ooptdd_D3_adoption_pitfalls.md) | [D4](ooptdd_D4_adoption_design.md) |

## What the study concluded (and how this repo reflects it)

**Identity (A).** ooptdd is runtime verification (LTL3 / Dwyer property patterns)
+ observability-driven development + the 2025–2026 "verify the agent's *outcome*,
not its self-report" idea, integrated and pointed at a TDD Red→Green→Refactor
loop. The 2×2 — static-contract (design-time) vs dynamic-trace (runtime),
self-report vs outcome-verified — places ooptdd in the runtime/outcome-verified
cell. → `README.md` positioning, `METHODOLOGY.md`.

**Packaging (B).** Ship as a `pytest11` entry-point plugin (zero-conftest);
aggregate per-test reports in `pytest_runtest_makereport` and ship once from the
**xdist controller** (`not config.workerinput`); fail-open with a timeout; warn by
default, strict opt-in; secrets env-only; "off == byte-identical run" as an
invariant. → `src/ooptdd/plugin.py`, tested in `tests/test_plugin.py`.

**Backends (C).** **Write is portable, query is not** — OTLP standardises ingest,
but LogQL / TraceQL / SQL / ES-DSL diverge and Loki's low-cardinality label model
actively fights a per-cid label. So the `Backend` surface is a minimal 2 methods
(`ship`, `query`) and drivers declare honest capability; SQL stores
(OpenObserve / ClickHouse) are first-class, OTLP is write-only, Loki is
best-effort/unsupported for count-by-cid. A zero-infra `memory` backend is the
default. → `src/ooptdd/backends/`.

**Adoption (D).** The whitespace ooptdd owns: **spec-first Red + arrival polling +
silent-ingest-loss detection + generator≠verifier + pytest-native + gate-as-YAML**
— no surveyed competitor fills every cell. The biggest adoption levers are a
zero-infra quickstart (memory backend) and a <60 s killer demo (the silent-loss
catch). The biggest risk is leaking the originating company's internal
infrastructure into the public package — explicitly scrubbed here. License:
Apache-2.0 (patent grant). → `examples/`, `README.md`, this repo's clean core.

## Honest limitations (carried forward from the study)

No long-horizon operational data; OTel GenAI semantic conventions still maturing;
large-scale (1000+ events/s) unproven; query-portability across backends is a
myth, not a feature. Hard log-free zones (precise numerics, security redaction,
µs races) are out of scope by design.

---

## F 시리즈 — OSS 소스레벨 흡수 (2026-07-22)

두 트랙 동시 진행, 상호 검증:

| 문서 | 트랙 | 내용 |
|---|---|---|
| [ooptdd_F_oss_absorption_20260722.md](ooptdd_F_oss_absorption_20260722.md) | 클론 소스 심독 (Claude 흡수 17 + Grok 종합 6·검증 42) | 경쟁/인접 OSS 18종 지형도 + 검증 생존 제안 29건 + 로드맵. 데이터: [ooptdd_F_proposals_verified_20260722.json](ooptdd_F_proposals_verified_20260722.json) |
| [prom16_grok_20260722/](prom16_grok_20260722/PROM_16_REPORT.md) | 웹+클론 하이브리드 PROM 16 (Grok 실행자) | 합의 6 · 충돌해소 1 · KG 씨앗 8 · 1차소스 88 |

핵심 수렴: VerdictExport 순수투영 레지스트리(단, `_emit` 라우팅 통일 선행) · 3치 판정 절대 보존 · ArrivalPolicy 일급화(TBT 카테고리 사망 교훈) · gen_ai preset 듀얼트랙(semconv-genai repo 분리 v1.42 확정).
