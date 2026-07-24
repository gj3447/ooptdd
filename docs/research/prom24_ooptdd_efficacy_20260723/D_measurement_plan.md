# Measurement plan — ooptdd 효능을 과장 없이 측정하는 법

> 이 문서는 결과표가 아니라 실행 계약이다. 미실행 칸을 0이나 1로 채우지 않는다.
>
> 현재 상태: Tier 0 `CODED` / final artifact pending; DeepEval CI
> `WIRED/PENDING`; Tier 1 `NOT MEASURED` (credentialed external readback과
> controlled-lag receipt 없음).

## 1. 사전등록된 질문

### Primary question

새 구현이 다음 evidence-integrity gap 네 개를 모두 닫는가?

1. trajectory-only mutation에서 `n=0, score=1.0`인 공허한 완벽 점수,
2. raw observation과 무관하게 top-level aggregate를 신뢰하는 경로,
3. source head/spec hash/dirty state를 고정하지 않는 측정,
4. DeepEval held-out artifact를 생성만 하고 CI에서 검증하지 않는 경로.

Primary metric은 `unresolved_evidence_integrity_gaps`다.

- exploratory baseline: `4`
- target: `0`
- kill: 하나라도 미해결, 또는 validator negative가 통과, 또는 restored replay가 실패.

### Novel target

결정론적 Tier 0의 `required_oracle_match_rate` target은 `1.0`이다. 기본 반복 수는 scenario당
20, fixed seed는 `20260723`이다. 각 `repeat=0..19`는 seed·scenario ID·repeat index에서
파생된 서로 다른 variant identity를 가지며, manifest/fixture hash에 고정된다. 이
target은 고정 panel의 mechanics를 채점하며 external arrival 효능이나 population
generalization을 대체하지 않는다.

## 2. 측정 전 lock

측정 시작 전 다음 값을 한 JSON에 고정하고 commit한다.

```json
{
  "schema": "ooptdd-efficacy-measurement-lock/v1",
  "candidate_git_head": "<40-hex candidate commit>",
  "candidate_dirty": false,
  "preregistration_sha256": "<64-hex>",
  "registration_repository": "https://github.com/gj3447/lakatotree",
  "benchmark_definition_sha256": "<64-hex>",
  "code_manifest_sha256": "<64-hex>",
  "manifest_sha256": "<64-hex>",
  "gate_spec_sha256": "<64-hex>",
  "events_sha256": "<64-hex>",
  "runner_sha256": "<64-hex compatibility projection>",
  "deepeval_spec_sha256": "<64-hex>",
  "deepeval_version": "4.0.7",
  "environment": {
    "python_implementation": "<implementation>",
    "python_version": "<major.minor.micro>",
    "pyyaml_version": "<version>",
    "platform_system": "<system>",
    "platform_machine": "<machine>",
    "byteorder": "<little-or-big>"
  },
  "seed": 20260723,
  "repetitions": 20,
  "tier": "tier0-mechanics"
}
```

`benchmark_definition_sha256`는 packaged fixture hash와 전체 packaged Python
`code_manifest`를 함께
바인딩한다. `runner_sha256`는 source-root script와의 호환 투영이지 전체 정의 hash를
대체하지 않는다. registration repository의 preregistration/lock blob은 측정 전에 commit·push되어
있어야 하며, byte replay는 lock의 정확한 Python/PyYAML/platform identity 안에서만 주장한다.
candidate SHA를 모른 채 측정하거나, 측정 후 같은 version의
fixture/code를 바꾸면 confirmatory run이 아니다. 새 version을 만들고 다시
preregister한다.

## 3. Tier 0 scenario contract

| ID | 주입/조건 | 기대 verdict/현상 | 지표 |
|---|---|---|---|
| T0-loss | 필수 event ship suppression | `absent` | M1 mechanics catch |
| T0-lag | declared visibility window 안에서 지연 후 도착 | `present`, false RED 아님 | M2a |
| T0-true-absent | declared visibility window가 끝난 후에도 event 없음 | `absent`, early-absence 아님 | C1 negative discrimination |
| T0-flap-control | late offender, `confirm_rounds=0` | 최초 green 유지 가능 | control only |
| T0-flap-confirm | 같은 offender, `confirm_rounds=1` | offender를 잡아 RED | M2b |
| T0-outage | backend unreachable | `inconclusive`, infra exit; oracle match로 overall pass 가능, JUnit testcase는 `skipped` | M3 |
| T0-dependent | Memory/dependent store + independence required | green demotion | C2a |
| T0-external-probe | distinct passing probe | `external_verdict` tier | C2b |
| T0-mutation | trajectory fixture semantic mutants | `n>=1`, all expected killed | M4 |

### Metrics

- `M1_silent_loss_catch_rate = caught_loss / loss_trials`
- `M2a_false_red_rate = false_absent_under_declared_lag / lag_trials`
- `M2b_late_offender_catch_rate = caught_late_offender / confirm_trials`
- `M3_inconclusive_honesty_rate = correct_inconclusive_without_junit_failure / outage_trials`
- `outage_junit_projection`: oracle match로 overall pass해도 해당 testcase는 `skipped`/inconclusive
- `M4_mutation_score = killed / eligible`, 단 `eligible >= 1`
- `C1_arrival_stamp_conformance`: 모든 polling result에 arrival stamp, blind window 안 absent 없음
- `C2_independence_conformance`: dependent demotion과 separate-probe promotion을 각각 확인
- `required_oracle_match_rate = matched_required_rows / required_rows`

### Repeated reliability

scenario마다 고정 panel `n`개 중 `c`개가 oracle와 일치했을 때,
τ-bench식 all-success panel robustness를 다음처럼 계산한다.

```text
pass_hat_k = C(c, k) / C(n, k),  k = min(8, n)
```

`pass_hat_k`는 이 20회 frozen repetition panel 내의 all-`k` subset 결과 일관성이다.
일부 scenario는 동일한 invariant mechanic을 반복하므로 독립 시행이 아니다. 보지
못한 입력 분포의 합격 확률, 통계적 population generalization, production reliability로
해석하지 않는다. denominator가 정의되지 않으면 수치를 꾸미지 않고 unmeasured로
둔다.

### Determinism gate

같은 source/spec/manifest/seed로 positive run을 두 번 실행한다.

1. 모든 scenario는 `repeat=0..19`의 20개 sample을 가지고, `variant_id`는 서로
   다르며 seed에서 재계산 가능해야 한다. 의미 있는 factor가 있는 scenario만 그
   factor가 달라지고, 이 조건은 20개의 독립 semantic trial을 뜻하지 않는다.
2. 고정된 Python/PyYAML/platform identity 안에서 canonical JSON은 byte-identical이어야 한다.
3. JUnit/Markdown의 판정 행 수와 canonical JSON의 scenario 행 수가 맞아야 한다.
4. outage는 canonical oracle match를 통과해도 JUnit에서 반드시 skipped/inconclusive이어야 한다.
5. 현재 시각, PID, 임시경로, 무작위 UUID가 canonical body에 들어가면 실패다.

### Negative and restored replay

동일 manifest에서 `disable-confirm-rounds` fault를 주입한다.

1. positive: exit 0
2. injected negative: exit 1, 오직 flap-confirm 관련 oracle mismatch가 드러남
3. restored positive: exit 0, 첫 positive와 canonical 결과 동일

manifest나 expected verdict를 음성대조에 맞춰 바꾸면 invalid negative다.

## 4. Evidence-integrity negative battery

다음 한 항목씩 변조한 artifact는 validator exit 2여야 한다.

| 변조 | 기대 거부 이유 |
|---|---|
| stored aggregate만 1.0/99로 변경 | observation-derived mismatch |
| raw observation의 `matched` 변경 | aggregate mismatch |
| duplicate `(group,name)` 또는 case identity | duplicate observation |
| 다른 `source.git_head` | source binding mismatch |
| 다른 spec SHA-256 | spec binding mismatch |
| `source.dirty=true` | dirty measurement |
| positive artifact에 `fault_injected=true` | role/fault mismatch |
| registration보다 이른 measurement | chronology violation |
| negative 다음 restored 순서 역전 | chronology violation |

이 battery가 코드 경로를 실제로 실행해야 한다. 문서상 checklist만으로 gap을 닫았다고
세지 않는다.

## 5. Tier 1 — 실제 외부 store 측정

### 상태

**2026-07-23 PROM 조사 시점: NOT MEASURED.** 현 candidate에 바인딩된
credentialed external-store run/readback 영수증과 controlled ingest-lag 주입 영수증이 없다.
Tier 0 결과나 recording HTTP opener를 이 칸에 복사하지 않는다.

### 최소 환경

- candidate SHA가 고정된 ooptdd checkout,
- 별도 process/container/host의 OpenObserve,
- endpoint와 backend identity가 receipt에 남음,
- credential은 환경변수/secret로만 제공하고 artifact에 저장하지 않음,
- test cid와 time window를 run마다 고유하게 분리,
- cleanup과 query readback을 모두 기록.

### 실제 scenario

| ID | 실제 fault | 기대 | 필수 영수증 |
|---|---|---|---|
| T1-loss-drop | shipper가 필수 emit을 생략 | `absent` | ship attempt + external query empty/readback |
| T1-loss-401 | 잘못된 auth를 fire-and-forget shipper가 삼킴 | `absent`, fake green 금지 | HTTP status와 store query |
| T1-lag | ingest proxy가 declared window 이하 지연 | false RED 0 | delay config, arrival stamp, final readback |
| T1-outage | store stop/unroutable endpoint | `inconclusive`, exit 2 | connection error classification |
| T1-restore | auth/store 복구 후 동일 spec replay | `present` | negative 이후 시각, query rows |

### Tier 1 pass criteria

- silent-loss catch rate `1.0`,
- lag false-RED rate `0.0`,
- outage inconclusive honesty `1.0`,
- restore rate `1.0`,
- 모든 run의 source/spec/manifest binding 유효,
- external backend identity가 MemoryBackend와 다름,
- negative → restored chronology 유효.

표본 20회는 v0 운영 floor이지 통계적 보편성 보증이 아니다. rate와 함께 trial count,
Wilson interval 또는 명시적 uncertainty를 보고한다.

## 6. LakatoTree evidence chain

새 canonical evidence는 `lakato-evidence-record/v1` 계약을 따른다. 최소 필드는 다음과 같다.

- programme와 conjecture identity,
- `registered_before_measurement=true`,
- predicted metric과 같은 이름의 measured metric,
- candidate/baseline/spec/manifest file hash,
- raw observation/data manifest provenance,
- negative oracle와 restored replay,
- harness identity와 실행 command,
- 서로 다른 primary/novel evidence hash.

judge response를 evidence producer가 hand-enter하지 않는다. evidence validation과 LakatoTree
judge invocation을 분리하고, judge가 읽은 file hash를 결과에 남긴다.

## 7. Platform bridge validation

core 효능 측정이 닫힌 뒤에만 실행한다.

DeepEval 4.0.7 custom metric, held-out artifact 생성, 재계산 validator는 CI YAML에
연결되어 있다. 하지만 현 candidate를 실행한 GitHub Actions URL과 업로드
artifact hash 전까지는 상태를 **`WIRED/PENDING`**으로 둔다.

| bridge | 입력 | 성공 조건 | 실패/보류 조건 |
|---|---|---|---|
| DeepEval | canonical ooptdd verdict | custom metric agreement, version pin | LLM judge가 core verdict를 덮음 |
| promptfoo | command/JSON result | exit ladder와 JSON/JUnit 일치 | JUnit만 남아 provenance 소실 |
| Phoenix | CODE span annotation | identifier upsert + GET readback | POST 2xx만 확인, live service 없음 |
| Langfuse | categorical score | stable score ID + subject/readback | inconclusive를 numeric 0으로 저장 |
| OTel export | verdict attributes/span | cid/spec hash/verdict 보존 | provider별 비표준 어휘만 존재 |

## 8. 보고 문구 계약

### final Tier 0 artifact까지 검증됐을 때만 허용

> 고정된 offline scenario에서 ooptdd의 loss/lag/flap/outage/independence 판정 기제가
> 결정론적으로 재현됐고, 궤적 mutation과 evidence tamper 음성대조를 통과했다.

### Tier 1 전에는 금지

> 실제 production/store에서 silent loss를 100% 잡는다.  
> 외부 도착 효능이 검증됐다.  
> Phoenix/OpenObserve 통합이 battle-tested다.

### Tier 1 후에도 필요한 한정

backend/version, scenario 수, fault 조건, repetitions, uncertainty, candidate SHA를 함께
기록한다. 다른 backend와 실제 production 분포로 일반화하지 않는다.

## 9. CI 상주 조건

- Tier 0은 PR마다 실행하고 canonical JSON/JUnit/Markdown을 artifact로 보관하도록
  workflow가 연결되어 있다. 현 candidate의 실제 Actions URL/artifact hash 전에는
  `TIER0_MEASURED`로 표기하지 않는다.
- DeepEval CI도 현 candidate Actions receipt가 생기기 전에는 `WIRED/PENDING`이다.
- measurement lock과 manifest hash mismatch는 test failure가 아니라 infra/evidence-invalid로
  구분해 exit 2로 막는다.
- Tier 1은 credential/container가 있는 scheduled workflow에서 실행한다. skip된 run을 green
  evidence로 세지 않는다.
- case study는 이 CI artifact URL과 hash를 가리켜야 한다. 만든 세션에서만 존재하는
  영수증은 adoption evidence가 아니다.
- OMD/MCP availability는 이 CI의 전제조건이 아니다.
