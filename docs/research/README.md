# Research — repository evidence and historical design studies

This directory contains repository-only research. It is not installed with the
`ooptdd` wheel and does not define the public package identity. Some documents
record earlier proposals that framed ooptdd primarily as a TDD or pytest tool;
those passages remain historical evidence, while the current package is a
general importable event-contract framework with optional adapters.

The initial collection came from a structured 16-cell research cycle (4 axes ×
4 lenses) run on 2026-06-16 before the standalone project was assembled.

| | official / standards | prior-art / competition | pitfalls / limits | design recommendation |
|---|---|---|---|---|
| **A · Identity & positioning** | [A1](ooptdd_A1_identity_official.md) | [A2](ooptdd_A2_prior_art_landscape.md) | [A3](ooptdd_A3_identity_pitfalls.md) | [A4](ooptdd_A4_identity_design.md) |
| **B · Historical pytest-adapter packaging study** | [B1](ooptdd_B1_packaging_official.md) | [B2](ooptdd_B2_packaging_priorart.md) | [B3](ooptdd_B3_packaging_pitfalls.md) | [B4](ooptdd_B4_packaging_design.md) |
| **C · Backend abstraction & portability** | [C1](ooptdd_C1_backend_official.md) | [C2](ooptdd_C2_backend_prior_art.md) | [C3](ooptdd_C3_backend_pitfalls.md) | [C4](ooptdd_C4_backend_design.md) |
| **D · Prior-art, competition & adoption** | [D1](ooptdd_D1_adoption_official.md) | [D2](ooptdd_D2_competition_priorart.md) | [D3](ooptdd_D3_adoption_pitfalls.md) | [D4](ooptdd_D4_adoption_design.md) |

## What the study concluded (and how this repo reflects it)

**Identity (A).** The study established the runtime-verification and
positive-arrival mechanics. Its TDD and agent-loop framing is now one optional
application interpretation, not core policy. Current identity and semantic
boundaries live in `README.md` and `SEMANTICS.md`.

**Packaging (B).** The original recommendation to auto-register a `pytest11`
entry point has been superseded. `src/ooptdd/plugin.py` remains an explicit,
opt-in adapter (`pytest -p ooptdd.plugin`); installing the base wheel does not
alter pytest. Repository benchmarks and frozen fixtures live under
`benchmarks/`, outside `src/ooptdd` and outside the wheel.

**Backends (C).** **Write is portable, query is not** — OTLP standardises ingest,
but LogQL / TraceQL / SQL / ES-DSL diverge and Loki's low-cardinality label model
actively fights a per-cid label. So the `Backend` surface is a minimal 2 methods
(`ship`, `query`) and drivers declare honest capability; SQL stores
(OpenObserve / ClickHouse) are first-class, OTLP is write-only, Loki is
best-effort/unsupported for count-by-cid. A zero-infra `memory` backend is the
default. → `src/ooptdd/backends/`.

**Adoption (D).** Arrival polling, silent-loss detection, three-valued evidence,
and generator/verifier separation remain reusable mechanisms. pytest-native and
spec-first development claims are adapter/workflow findings rather than the
framework's base identity. License: AGPL-3.0-or-later.

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

핵심 수렴: VerdictExport 순수투영 레지스트리(단, `_emit` 라우팅 통일 선행) · 3치 판정 절대 보존 · ArrivalPolicy 일급화(TBT 카테고리 사망 교훈) · gen_ai preset 듀얼트랙(현재는 `ooptdd-genai` 배포판의 명시적 opt-in 확장).
