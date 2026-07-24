# Absorption matrix — 무엇을 core에 넣고 무엇을 유통망에 남기는가

## 상태 어휘

| 상태 | 의미 |
|---|---|
| `ALREADY` | 이번 PROM 이전부터 코드와 테스트에 존재 |
| `CODED` | 이번 작업에서 코드와 좁은 테스트가 생김; 효능 측정과는 별개 |
| `WIRED/PENDING` | 코드와 CI step은 연결됐지만 현 candidate의 Actions/artifact receipt가 없음 |
| `PREREGISTERED` | 새 측정보다 먼저 metric·예측·kill condition을 외부에 고정 |
| `DEFERRED_TIER1` | 실제 외부 store/service 측정 전에는 결과 주장 금지 |
| `BRIDGE_NEXT` | core가 아니라 선택적 외부 어댑터 후속 작업 |
| `REJECTED` | 설계상 흡수 금지 |

## 결정표

| 기제 | 연구 근거 | 결정 | 현재 착지/증거 표면 | claim 경계 |
|---|---|---|---|---|
| OTel `gen_ai.*` ontology | O10 | `ALREADY` | `src/ooptdd/domain/semconv.py`, ontology tests | 자체 agent vocabulary 발명 아님 |
| required/forbidden/ordered tool trajectory | O04, O05, P02, P12 | `ALREADY` | `src/ooptdd/engine/trajectory.py`, trajectory tests | 도착한 구조만 판정; reasonableness 아님 |
| semantic trajectory mutants | O07, P05 | `CODED` | `src/ooptdd/mutation.py`, `tests/test_mutation.py`, `tests/test_trajectory_checks.py` | `n>=1`일 때만 score measured |
| stable mutant ID/status/eligible | O07, P05 | `CODED` | mutation report fields | timeout을 killed로 세지 않음 |
| observation-derived aggregate | O02, P01, P14 | `CODED` | `src/ooptdd/evidence_integrity.py` | top-level metric은 인증 근거가 아님 |
| source/spec/dirty/fault binding | O02, O09, P14 | `CODED` | `validate_measurement`, validator CLI/tests | 다른 head/spec 결과 재사용 금지 |
| duplicate sample and chronology guard | O02, P07 | `CODED` | evidence-integrity tests | registration < negative < restored 강제 |
| DeepEval agreement/success 분리 | O04, P06 | `WIRED/PENDING` | `deepeval_metrics`, validator, `.github/workflows/ci.yml` | Actions run URL + artifact hash 전에 CI 실측 주장 금지; 3/3 agreement != 3 successes |
| deterministic Tier 0 scenario harness | O08, P01, P02, P07, P13 | `CODED` | `src/ooptdd/benchmark_fixtures/arrival/v0/`, `src/ooptdd/benchmark.py`, runner/tests/CI | scenario당 고유 identity의 20회 deterministic repetition; 의미 있는 factor만 변화; final artifact 전에 measured 아님 |
| byte-identical canonical result | O03, O08, O09 | `CODED` | fixed seed/clock + canonical JSON validator | wall clock/PID/temp path 금지 |
| repeated reliability `pass_hat_k` | P01, P07 | `CODED` | scenario별 `C(c,k)/C(n,k)`, 기본 k=8 | frozen panel robustness만 표현; population generalization 아님 |
| outage oracle/JUnit 이중 의미 | O03, P13 | `CODED` | `benchmark_gate_result`, `to_junit_xml`, `tests/test_arrival_benchmark.py` | 전체 oracle는 match/pass 가능; testcase는 반드시 skipped/inconclusive |
| prospective LakatoTree programme | P01, P05, P14 | `PREREGISTERED` | `gj3447/lakatotree`, branch `feat/ooptdd-efficacy-programme`, prereg commit `8f6a804` | 이전 27-case는 exploratory |
| OpenObserve loss/lag/outage | O01, P01, P13, P14 | `DEFERRED_TIER1` | Tier 1 protocol만 존재 | credentialed readback과 controlled-lag receipt 전 headline 금지 |
| Phoenix CODE annotation + readback | O05 | `BRIDGE_NEXT` | retry-safe payload/readback 설계 | recording opener는 live proof 아님 |
| Langfuse typed score + idempotency | O06 | `BRIDGE_NEXT` | adapter contract 후보 | inconclusive를 0/false로 붕괴 금지 |
| promptfoo command/JUnit bridge | O03 | `BRIDGE_NEXT` | canonical JSON consumer 설계 | promptfoo가 ooptdd를 재판정하지 않음 |
| DeepEval custom metric wrapper | O04 | `WIRED/PENDING` | real 4.0.7 adapter test + CI step | 현 candidate Actions receipt 전에 CI 통과로 표기 금지; LLM judge는 soft diagnostic만 |
| live/rotating hidden fixtures | P08 | `BRIDGE_NEXT` | benchmark v1 후보 | 같은 version의 fixture 변경 금지 |
| backend cross-matrix | P11 | `BRIDGE_NEXT` | Tier 1 이후 | OpenObserve 하나를 먼저 닫음 |
| OMD/MCP orchestration | 사용자 경계 | `REJECTED` | 없음 | 단단해지기 전 효능 체인에 미포함 |
| generic LLM judge | P06, P14 | `REJECTED` | 없음 | arrival oracle 아님 |
| UI/dashboard/leaderboard | O01, O05, O06, P08, P10 | `REJECTED` | 없음 | CI artifact가 제품 표면 |
| adaptive red-team generator | P09 | `REJECTED` | 없음 | curated negative fixture만 허용 |
| dense trajectory quality metrics | P10 | `REJECTED` core / external diagnostic 허용 | 없음 | deterministic contract 범위 유지 |

## 핵심 데이터 흐름

```text
raw arrived observations
        |
        v
source/spec/fault/identity validation
        |
        v
deterministic LTL3 verdict + recomputed metrics
        |
        +--> canonical JSON (정본)
        |
        +--> JUnit / Markdown (순수 투영)
        |
        +--> DeepEval / promptfoo / Phoenix / Langfuse (선택 bridge)
                         |
                         v
                    sink readback receipt
```

외부 platform이 verdict를 바꾸거나, reporter가 raw observation 없이 새 score를 만들거나,
동일 프로세스의 producer summary가 독립 증거로 승격되는 경로는 허용하지 않는다.

## 네 가지 실제 흡수와 서로 다른 증명 강도

### A. 비공허 mutation — `CODED`

궤적-only gate에서도 rename/corrupt/reorder/inject operator가 eligible mutant를 만든다.
검사해야 할 최소 조건은 `baseline_green=true`, `n>=1`, `score_status=measured`,
`canary_survived=false`다. score만 단독으로 배지화하지 않는다.

### B. observation-first evidence — `CODED`

aggregate field 위조, raw row 변조, duplicate row, 잘못된 head/spec, dirty measurement가 모두
validator에서 fail closed해야 한다. validator에는 LakatoTree verdict 로직이 없고 evidence
정합성만 다룬다.

### C. deterministic Tier 0 — `CODED`

fixed manifest/fixture와 `(seed, scenario_id, repeat)`에 바인딩된 scenario당 20개의
서로 다른 variant로 polling/confirmation/3치/independence/mutation 기제를 재현한다.
`pass_hat_k`는 이 panel의 결과 일관성이지 독립 시행이나 보지 못한 population으로의 일반화가 아니다.
outage의 기대 ?는 benchmark oracle match에 포함되어 전체를 통과시킬 수 있지만,
JUnit testcase는 `skipped`/inconclusive로 남아야 한다. Tier 0의 GREEN은 "이 기제가
결정론적으로 동작한다"는 주장만 지지하며, external network arrival와 store-specific
lag 수치는 지지하지 않는다. final measurement artifact 전 상태는 `CODED`다.

### D. prospective LakatoTree — `PREREGISTERED`

예측과 kill condition을 candidate 측정 전에 외부 canonical repository에 먼저 기록했다.
다음 단계는 candidate SHA와 manifest/spec hash를 measurement lock으로 고정한 뒤
negative → restored positive를 순서대로 실행하는 것이다. judge는 raw evidence를 검증한 뒤
별도 단계에서만 verdict를 산출한다.

## 배포 전 승격 규칙

1. `CODED`는 runner/tests/CI가 있다는 뜻이다. prospectively locked
   positive-negative-restored artifact와 외부 judge 전에는 `TIER0_MEASURED`로 부르지 않는다.
2. `TIER0_MEASURED`는 `TIER1_MEASURED`로 자동 승격되지 않는다.
3. bridge는 POST 성공만으로 완료가 아니다. idempotency identity와 readback이 있어야 한다.
4. LakatoTree judgment는 사전등록·source binding·negative oracle 중 하나라도 빠지면
   confirmatory claim으로 쓰지 않는다.
5. `WIRED/PENDING`은 현 candidate의 GitHub Actions run URL과 업로드 artifact hash가
   확인되어야만 CI-verified 상태로 승격한다.
6. OMD는 이 표의 승격 요건에 포함하지 않는다.
