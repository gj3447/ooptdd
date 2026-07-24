# PROM 24 REPORT — ooptdd 효능을 보이기 위한 연구개발 흡수

> cycle: `prom24-ooptdd-efficacy-absorption-20260723`  
> 조사일: 2026-07-23  
> 매트릭스: OSS 10 + papers 14  
> 증거 규칙: 공식 1차 출처만 채택; 현재 저장소의 코드 상태는 별도 실측

## 결론

ooptdd의 효능은 "LLM 답변을 더 잘 채점한다"로 증명하면 안 된다. 증명할 핵심은
다음과 같다.

> **필수 사건이 실제로 도착하지 않은 경우를 잡되, 수집 지연을 거짓 RED로 만들지
> 않고, 저장소를 볼 수 없으면 정직하게 inconclusive를 내며, 이 판별력이 gate
> mutation과 독립 증거로 재현되는가?**

PROM 24에서 반복해서 수렴한 흡수 축은 네 개다.

1. **Observation first.** Inspect의 재채점 가능한 로그, τ-bench의 최종 상태 oracle,
   ToolSandbox의 milestone/minefield가 모두 "요약 점수를 믿지 말고 관측에서 다시
   계산하라"는 방향으로 모인다.
2. **Non-vacuous mutation.** Stryker와 Google의 mutation 연구는 eligible denominator와
   안정된 operator가 없으면 점수가 장식이 된다고 말한다. 궤적 gate에는 drop이 아니라
   rename/corrupt/reorder/inject 계열 mutant가 필요하다.
3. **Repeated reliability.** τ-bench의 `pass^k`와 agentic eval randomness 연구는 한 번의
   녹색을 성능으로 부를 수 없게 만든다. v0는 고정 seed에서 scenario당 20회
   반복하고 각 sample에 고유 identity를 주며, 의미 있는 factor가 있는 scenario만
   그 factor를 변화시킨다. `pass_hat_k`는 고정 panel의 결과 일관성이지 독립 시행,
   population generalization, production 성공률이 아니다.
4. **Integration, not replacement.** promptfoo·DeepEval·Phoenix·Langfuse는 유통 표면이다.
   ooptdd의 3치 verdict를 얇게 투영하되 그들의 LLM judge, UI, 저장소를 내장하지 않는다.

## 24개 finding 요약

| ID | 근거 | 핵심 흡수 또는 경계 | 판정 |
|---|---|---|---|
| O01 | Tracetest | polling/trace assertions는 비교 기준; blind-window 이후에만 absent | 부분 흡수 |
| O02 | Inspect AI | 원시 eval log에서 재점수·재집계 | 흡수 |
| O03 | promptfoo | rich JSON/JSONL 정본, JUnit은 손실 투영 | 흡수 |
| O04 | DeepEval | 좁은 deterministic custom metric bridge | 통합 |
| O05 | Phoenix | CODE annotation, identifier upsert, readback | 통합 |
| O06 | Langfuse | typed score와 idempotent score identity | 통합 후보 |
| O07 | Stryker | mutant ID/status/eligible denominator | 흡수 |
| O08 | k6 | named scenario, threshold, lifecycle/cleanup | 흡수 |
| O09 | OpenAI Evals | dataset split/version identity와 JSONL registry | 흡수 |
| O10 | OTel GenAI | 표준 `gen_ai.*`를 어휘 정본으로 유지 | 이미 보유 |
| P01 | τ-bench | 최종 상태 oracle + `pass^k` | 흡수 |
| P02 | ToolSandbox | milestone/minefield와 상태 의존성 | 흡수 |
| P03 | AgentTrace | 실행 궤적의 구조화된 provenance | 원칙 흡수 |
| P04 | MR-Scout | absolute oracle가 약할 때 metamorphic relation | 이미 보유/확장 |
| P05 | Google mutation | 변경분·operator 품질 중심 mutation | 흡수 |
| P06 | self-preference 연구 | 자기/동족 LLM judge를 독립 증거로 부르지 않기 | 금지 강화 |
| P07 | agentic eval randomness | 반복·분산·고정 seed | 흡수 |
| P08 | LiveBench | 신선한/숨은 fixture로 오염 저항 | 후속 흡수 |
| P09 | AgentDojo | utility와 security를 분리한 음성대조 | fixture만 참고 |
| P10 | AgentBoard | 최종 성공 외 process progress 진단 | core 비채택 |
| P11 | AgentBench | 여러 환경에서 같은 계약 재실행 | Tier 1 확장 후보 |
| P12 | ToolLLM | tool path fixture와 unseen-tool 분리 | fixture 참고 |
| P13 | LTL runtime semantics | finite prefix의 true/false/unknown 보존 | 이미 보유 |
| P14 | test oracle survey | 독립 관측·계약·metamorphic oracle 조합 | 흡수 |

상세 근거와 caveat는 [A_oss.md](A_oss.md), [B_papers.md](B_papers.md)에 있다.

## 이번 구현에서 실제로 흡수한 것

### 1. 궤적 mutation을 비공허하게 만들기

`src/ooptdd/mutation.py`의 궤적 규칙은 이제 의미 없는 generic drop 대신 다음 semantic
operator를 파생한다.

- required tool rename,
- required arguments corrupt,
- ordered/exact sequence reorder,
- exact trajectory extra call injection,
- forbidden tool/call materialization,
- unreadable forbidden-call arguments injection.

각 행에는 stable `mutation_id`, `operator`, `status`, `eligible` denominator가 남는다.
mutant가 0개일 때 `score=1.0`을 성능으로 해석하지 않도록 `score_status=unmeasured`를
분리한다. 이 구현은 O07/P05를 흡수한다.

### 2. summary가 자신을 인증하지 못하게 만들기

`src/ooptdd/evidence_integrity.py`와
`scripts/validate_trajectory_evidence.py`는 raw observation에서 metric을 다시 계산하고,
중복 sample, source head/spec hash mismatch, dirty worktree, fault-mode mismatch, 사후적
chronology를 거부한다. 특히 기존 DeepEval 이름의 혼동을 분리해 다음을 동시에 기록한다.

- `deepeval_oracle_agreement_rate`: 기대 oracle과의 일치율,
- `actual_successes`: 실제 성공으로 관측된 case 수.

따라서 safe/dangerous/corrupt 세 경우가 기대대로 분류됐다는 3/3은 실제 작업 성공
3/3을 뜻하지 않는다. 이 구현은 O02/P01/P14를 흡수한다.

DeepEval 4.0.7 실행·artifact 재계산 step은 `.github/workflows/ci.yml`에 연결되어
있지만, 현 candidate의 Actions run URL과 artifact hash가 아직 없다. 따라서 현재
상태는 **`WIRED/PENDING`**이다. 로컬 실행과 workflow YAML은 실제 CI receipt를
대체하지 않는다.

### 3. 결정론적 Tier 0을 효능이 아니라 기제 증거로 두기

Tier 0은 고정된 manifest·seed·clock으로 loss, lag, late offender, outage,
dependent-store demotion, external-probe 승격, trajectory mutation을 반복한다. v0의
`repeat=0..19`는 `(seed, scenario_id, repeat)`에서 결정론적으로 파생된 고유
identity에 바인딩된다. 다만 outage/mutation처럼 동일 기제를 반복하는 행도 있으므로
20개의 독립적 semantic trial로 해석하지 않는다. canonical JSON이 판정 정본이며
JUnit/Markdown은 같은 결과의 순수 투영이어야 한다. outage의 기대
`inconclusive`는 전체 oracle를 통과시킬 수 있지만 JUnit testcase에서는 반드시
`<skipped>`로 남아야 한다. 동일한 source/spec/manifest/seed와 고정된
Python·PyYAML·platform identity에서 두 canonical JSON은 byte-identical이어야
하고, `confirm_rounds`를 끄는 주입은 같은 manifest 아래 명시적으로 실패해야 한다.

이 단계의 현재 상태는 **`CODED` / final measurement artifact pending**이다.
코드와 좁은 테스트를 통과해도 증명하는 것은 gate mechanics뿐이다.
MemoryBackend는 독립 외부 저장소가 아니므로 실제 arrival 효능 수치로
광고하지 않는다.

### 4. prospective LakatoTree

새 효능 프로그램은 구현 완료 후 결과를 끼워 맞추는 방식이 아니라, candidate 측정 전에
별도 canonical LakatoTree 저장소에 예측·metric·kill condition을 먼저 고정했다. 기존
27-case 결과는 exploratory baseline으로 남기고, 새 음성대조와 restored replay만 새
프로그램의 confirmatory evidence로 인정한다.

## 아직 증명하지 않은 것

**Tier 1 external arrival는 이 PROM 문서의 조사 시점에 `NOT MEASURED`이다.**
현 candidate에 바인딩된 외부-store credential/readback 영수증과 controlled-lag 주입
영수증이 없기 때문이다. 따라서 다음 주장은 보류한다.

- 실제 OpenObserve 네트워크에서 silent 401/drop catch rate가 1.0이다.
- 실제 ingest lag 안에서 false-RED rate가 0.0이다.
- 실제 저장소 outage가 항상 inconclusive와 exit 2로 귀결된다.
- Phoenix annotation이 live service에 idempotent하게 기록되고 readback된다.

이 값은 [D_measurement_plan.md](D_measurement_plan.md)의 Tier 1 프로토콜로만 채운다.
mock HTTP와 MemoryBackend 결과를 Tier 1로 승격하지 않는다.

## 흡수하지 않을 것

1. **OMD/폐기된 OMD MCP**: 아직 오류가 많은 조정 계층을 효능 판정 체인에 넣으면
   재현성과 source binding을 약화시킨다. 이번 경로에서 완전히 제외한다.
2. **Generic LLM judge**: answer quality, reasonableness, plan quality는 DeepEval/Phoenix 등의
   soft metric으로 붙일 수 있지만 ooptdd의 present/absent/inconclusive oracle이 아니다.
3. **UI/대시보드**: CI JSON/JUnit/Markdown이면 충분하다. Phoenix/Langfuse/Tracetest
   화면을 복제하지 않는다.
4. **Red-team 생성기**: AgentDojo에서 가져올 것은 utility/security 분리와 고정 fixture
   구조다. 공격 생성 제품을 만들지 않는다.
5. **풍부한 trajectory metric**: AgentBoard류 progress 분석은 진단용 외부 metric이다.
   ooptdd core는 required/forbidden/order/args/aggregate의 deterministic 계약에 머문다.

## R&D 실행 순서

| 단계 | 산출물 | 완료 조건 |
|---|---|---|
| R0 | prospective LakatoTree registration | 등록 시각이 모든 새 측정보다 앞섬 |
| R1 | mutation + evidence integrity | tamper/duplicate/head/spec/dirty 음성 테스트가 RED |
| R2 | Tier 0 deterministic benchmark | 동일 run byte-identical, injected negative만 RED, restored GREEN |
| R3 | candidate SHA measurement lock | source/spec/manifest hash가 측정 전에 고정 |
| R4 | Tier 1 OpenObserve | loss/lag/outage 실제 외부 readback, 독립성 표기 |
| R5 | distribution adapters | DeepEval/promptfoo/Phoenix/Langfuse thin bridge + readback |
| R6 | adoption receipt | CI 상주 case study, artifact link와 hash 제공 |

R4가 없으면 "기제가 맞다"까지 말할 수 있고 "실제 외부 도착 효능이 입증됐다"고는
말할 수 없다. 이 문장 자체가 이번 PROM의 가장 중요한 honesty gate다.
R2는 구현됐지만 final measurement artifact가 생성·검증되기 전이므로
`TIER0_MEASURED`로 승격하지 않는다. R5 중 DeepEval CI는 `WIRED/PENDING`이며,
이 Actions receipt 역시 R2/R4의 효능 측정을 대체하지 않는다.
