# PROM 16 REPORT — ooptdd 고도화: trace-based testing / agent-eval OSS 흡수

> cycle_id: `prom16-ooptdd-oss-absorption-grok-20260722` · 2026-07-22
> 실행자: **Grok CLI** (`grok-agent research`, 독립 프로세스 16, 병렬 4-way) — dispatch_mode=`GROK_CLI_N`, subagent_count=16, hswm_mode=off
> 매트릭스: 4 axis (implementation / integration / limitations / applications) × 4 lens (official-docs / alternatives / pitfalls / trends-2026), KG 씨앗 유도
> 입력 컨텍스트: 2026-07-22 코드 실측 self-map + 기존 KG 2사이클(6/18 OSS 흡수 A2–A14, 6/22-23 구현검증 V0–V7·R1–R7) 중복금지 주입 + GM 로컬 클론 18종 (`--cwd`)
> 수확: **16/16 FullFindingRecord, 전원 HIGH confidence, 참조 평균 ~17개(고유 http 소스 88개)**. KG 적재 16/16 (PromBatchWrite gate PASS)

---

## 0. 사전 지식 (이 사이클이 딛고 선 것)

- **self-map (코드 실측)**: competitive_feedback 로드맵 8항목 중 P1 agent vocabulary만 DONE(OTel gen_ai.* preset). selector·caps·OO경로·proof examples·report = PARTIAL, **eval-tool integration = 완전 MISSING**. 리포트 출력은 JSON뿐(JUnit XML/markdown 부재), `where`는 등호비교만.
- **기존 KG**: VictoriaLogs 드라이버·OpenSLO 게이트형은 이미 흡수·구현됨. Tracetest selector DSL 전체복제는 종전 사이클에서 이미 금지 판정.

## 1. Consensus (합의 — 3+ 셀 동의, KG `:ConsensusGroup` 6개)

| # | 합의 | 동의 셀 | 요지 |
|---|---|---|---|
| C1 | **VerdictExport = `_emit` 단일 깔때기 위 순수 투영** | 8 (A1·A3·A4·B1·B3·B4·C2·C4) | 신규 리포트 포맷·플랫폼 sink 전부를 canonical JSON verdict의 투영으로 `cli.py _emit`(cli.py:75-81 seam)에 부착. 플러그블 writer/sink 레지스트리(entry-points, lazy import, extras 패키징). JUnit XML + markdown 먼저. 리포트 로직 분기 금지 — rich JSON이 정본. |
| C2 | **LTL3 3치 보존 불변식** | 6 (A1·A3·A4·B3·B4·C3) | 어떤 export에서도 inconclusive를 pass/fail/스칼라로 붕괴시키지 않는다. 매핑 정책은 어댑터 생성 시 명시적 설정. |
| C3 | **gen_ai preset 듀얼트랙 버저닝** | 3 (D1·D4·B2) | `otel_gen_ai@1.30.0-experimental` 동결 유지 + post-1.37 신규 preset(gen_ai.system→**gen_ai.provider.name** rename, event→attribute 통합, `gen_ai.evaluation.*` 표면, agent/MCP spans). 듀얼 emission 마이그레이션 문서화. |
| C4 | **ArrivalPolicy 일급화** (카테고리 사망 교훈) | 3 (C1·C3·D3) | 대기예산 소진→절대 absent/fail 매핑 금지(기본 inconclusive + reason code). BackendCaps += `query_visibility_delay_ms`(VL 1000ms/CH async_insert 200-1000ms/OO memtable 5s) + `force_flush` 훅(VictoriaLogs `POST /internal/force_flush`는 공식 문서가 자동테스트용으로 권장) + `max_evidence_tier`. 근거: Tracetest Cloud EOL(2024-10-31)·Malabi 동일 archived — timeout=fail 설계가 TBT 카테고리를 죽였다. |
| C5 | **에이전트 CI 게이트 팩** | 4 (D2·D3·D4·B2) | `profiles/agent_ci_v1.yaml`: required/forbidden tool·completion-after-result·conforms(closed_world)·forbid_errors·require_signature·**retry-until-green 대신 N회 독립시도 threshold quorum**. + LLM-free `@check("tool_call_accuracy")` (Ragas ToolCallAccuracy 형태를 도착 이벤트 위에서). RED/GREEN 예제쌍. |
| C6 | **문법 성장은 comparator+duration까지만** | 3 (A2·A1·A4) | 공유 comparator 레지스트리(eq/ne/lt/lte/gt/gte/contains — tracetest `comparator/basic.go` 패턴)로 `where` 확장({field:{op,value}}) + `@check("duration")`(OpenSLO threshold 형태). span[...] selector DSL·json_path/regex는 명시적 비목표. |

## 2. Divergence (충돌 — KG `:Conflict` 1건, 해소됨)

- **JUnit에서 inconclusive 기본 매핑**: A3·A1 = `<error type="ooptdd.inconclusive">`(fail-closed) vs A4 = `<skipped>`(중립).
  **판정 (parent KARMA, `conflict-ooptdd-junit-inconclusive-mapping-20260722-resolution`)**: 모순이 아니라 대안관계 — **기본 fail-closed `<error>`** (CI 아티팩트는 조용히 초록이면 안 됨; gate 수준의 verify_policy inconclusive-never-fail과는 층위가 다름) + `--junit-inconclusive=skipped` opt-in. 양 날개 보존.

## 3. Open Questions (열린 것 — 열린 채로)

1. **D1 단독주장**: "GenAI semconv 정본이 core v1.42에서 별도 repo(semantic-conventions-genai)로 분리, schema URL `gen-ai-dev/1.42.0-dev`" — 단일소스. → `seed-conflict-genai-canon-relocation-verify-20260722` (EXPLORATION). Claude 워크플로의 semantic-conventions 클론 흡수 결과와 교차검증 후 C3 구현 착수.
2. **B2 단독주장**: OpenLLMetry가 zero-custom-emit SUT 소스로 성립(OpenInference 단독은 `OPENINFERENCE_ENABLE_GENAI_SEMCONV=true` 듀얼라이트 필요) → `seed-verify-openllmetry-zero-emit-recipe-20260722` (VERIFY). 실기동 RED/GREEN 레시피로 검증.
3. CTRF 채택 시점(A4는 "optional, 나중"), SARIF-for-tests는 기각 권고 — 재평가는 GH 생태계 변동 시.

## 4. 권장 후속 작업 (ActionPlan `plan-ooptdd-advancement-v2-20260722`, 씨앗 8개 READY)

| 우선순위 | 작업 | 씨앗 |
|---|---|---|
| **P0** | VerdictExport writer/sink 레지스트리 + JUnit XML·markdown writer + 매핑정책(fail-closed 기본) | `seed-rf-verdict-export-emit-funnel-20260722`, `seed-rf-junit-ltl3-mapping-policy-20260722` |
| **P1** | ArrivalPolicy + BackendCaps 가시성 메타데이터·force_flush | `seed-rf-arrival-policy-first-class-20260722` |
| **P1** | gen_ai preset 듀얼트랙 (단, relocation 검증 선행) | `seed-rf-genai-preset-dual-track-20260722` ← `seed-conflict-genai-canon-relocation-verify-20260722` |
| **P2** | 에이전트 CI 게이트 팩 + tool_call_accuracy | `seed-rf-agent-ci-gate-pack-20260722` |
| **P2** | comparator 레지스트리 + duration 체크 | `seed-rf-comparator-duration-minimal-20260722` |
| **P2** | eval 플랫폼 sink extras (DeepEval/promptfoo/LangSmith/Langfuse/Phoenix + `gen_ai.evaluation.result` OTel emitter) | (VerdictExport 씨앗에 포함) + `seed-verify-openllmetry-zero-emit-recipe-20260722` |

> **병합 예정**: 동시 진행된 Claude 워크플로(`ooptdd-oss-absorption`, 소스레벨 흡수 17 agents → 6테마 → 적대검증)의 F-리포트와 교차 병합 후 구현 착수. 본 사이클 결과는 웹+클론 하이브리드, F-리포트는 클론 소스 심독 — 상호 검증 관계.

---

### 산출물 맵

- 셀 원문: `A_implementation.md` / `B_integration.md` / `C_limitations.md` / `D_applications.md` (FullFindingRecord 16개 전문)
- 1차 소스: `SOURCES.md` (고유 http 88)
- KG: `:Lesson` 1 + `:ResearchFinding` 16 (provenance 3종 edge) + `:ConsensusGroup` 6 + `:Conflict/:ConflictResolution` 1 + `:SubagentTaskSpec` 씨앗 8 + `:ActionPlan` 1 + `:UpperWorldRef` 12 + `:PromBatchWrite` gate(16/16 PASS)
- 로컬 클론 둥지: `SYMPOSIUM/GIT/_GM_OOPTDD_COMPETITORS` → `/Volumes/GM/SYMPOSIUM-RESTORE/OOPTDD_COMPETITORS_20260722` (18 repos, ExFAT 콜드 읽기전용)
