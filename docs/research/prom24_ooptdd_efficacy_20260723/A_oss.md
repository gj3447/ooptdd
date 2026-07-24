# Axis A — 유사 OSS 10개 finding

판정 표기는 `ABSORB`(core 기제), `BRIDGE`(얇은 외부 어댑터), `REFERENCE`
(비교·fixture만), `REJECT`(명시적 비목표)다. 모든 외부 프로젝트는 개념과 공개 계약만
참조했고 코드를 복사하지 않았다.

## O01 — Tracetest: 가장 가까운 비교자는 기능 목록보다 polling 의미론을 준다

**관측.** Tracetest는 OpenTelemetry trace를 대상으로 selector/assertion을 실행하고,
Polling Profile로 재조회 간격과 timeout을 설정한다. 공식 문서는 span이 즉시 완전하지
않음을 전제로 하지만 timeout을 넘으면 test fail로 설명한다. selector와 UI, trigger,
suite까지 포함하는 제품 범위는 ooptdd보다 훨씬 넓다.

**판정.** `REFERENCE + PARTIAL ABSORB`.

- 흡수: trace 도착은 즉시적이지 않다는 전제, bounded polling, 실제 trace/data assertion.
- ooptdd 차별점: backend visibility window 안에서는 `absent`로 닫지 않고 3치
  `inconclusive`를 보존한다.
- 거부: selector DSL 전체, trigger runner, UI, suite 관리, Tracetest runtime 복제.
- caveat: Cloud EOL과 OSS/상용 기능 분리는 통합 의존성을 낮게 유지해야 하는 추가 이유다.

**ooptdd 영향.** Tier 0/1의 lag scenario는 polling 횟수가 아니라
`waited_ms >= visibility_delay_ms` 불변식을 채점해야 한다. `[O01]`

## O02 — Inspect AI: 점수보다 재실행 가능한 log가 먼저다

**관측.** Inspect의 EvalLog는 task/model/config, samples, scores, errors, usage와 상태를
보관한다. generation을 다시 하지 않고 기존 log에 다른 scorer를 적용하거나 score를
append/overwrite할 수 있다. 공식 API는 log status를 확인한 뒤 sample/summary를 읽는
경로를 제공한다.

**판정.** `ABSORB`.

- raw observation이 정본이고 aggregate는 재계산 가능한 cache다.
- retry/rescore는 기존 row의 identity와 lineage를 보존해야 한다.
- failed/incomplete run을 successful metric과 섞지 않는다.

**이번 착지.** `evidence_integrity.py`가 row identity, source/spec binding, chronology를
검증하고 top-level metric을 raw observations에서 재계산한다. Inspect log 포맷 자체나
viewer는 흡수하지 않는다. `[O02]`

## O03 — promptfoo: canonical rich output과 CI projection을 분리한다

**관측.** promptfoo는 JSON과 대규모 결과용 JSONL을 제공하고, JUnit은 CI용 compact
projection으로 별도 출력한다. 공식 문서는 failed assertion을 `<failure>`, provider/runtime
error를 `<error>`로 분리하고, JUnit이 raw prompt/response/config를 의도적으로 생략한다고
밝힌다.

**판정.** `ABSORB + BRIDGE`.

- canonical JSON은 observation, provenance, 3치 verdict를 모두 가진다.
- JUnit/Markdown은 그 JSON을 다시 판정하지 않는 손실 투영이다.
- promptfoo command hook은 ooptdd JSON/exit code를 소비하는 얇은 wrapper로 충분하다.
- promptfoo의 provider matrix, browser viewer, LLM assertion 집합은 가져오지 않는다.

**측정 조건.** JSON과 JUnit이 다른 판정을 내리면 writer 버그로 RED 처리한다. `[O03]`

## O04 — DeepEval: custom metric은 유통 어댑터이지 새 oracle이 아니다

**관측.** DeepEval은 custom metric interface와 tool-correctness metric을 제공한다. tool
name/argument/ordering의 일부는 결정론적으로 비교할 수 있지만, task completion, plan
quality, reasonableness 계열은 model judge를 사용할 수 있다.

**판정.** `BRIDGE`.

- 흡수: `tool_calls`의 exact/subset/ordered, name/args 비교 vocabulary.
- 통합: ooptdd의 이미 나온 verdict를 DeepEval metric result로 투영하고 oracle agreement를
  점검한다.
- 거부: generic LLM-as-a-judge를 arrival oracle 또는 핵심 gate로 사용.

**정확한 지표명.** safe/dangerous/corrupt 3개가 기대 분류와 일치한 3/3은
`deepeval_oracle_agreement_rate=1.0`이다. 실제 성공 수와 동일하지 않으므로
`actual_successes`를 따로 기록한다. `[O04]`

## O05 — Phoenix: CODE annotation과 idempotent readback만 통합한다

**관측.** Phoenix annotation은 LLM/CODE/HUMAN annotator kind를 구분하고, `identifier`로
동일 `(name, spanId, identifier)`를 update-in-place할 수 있으며 annotation readback API를
제공한다. Phoenix 자체는 trace storage, evaluator, UI까지 제공한다.

**판정.** `BRIDGE`, core copy는 `REJECT`.

- ooptdd verdict는 `CODE` annotation으로 보낸다.
- `identifier = ooptdd:<gate>:<spec_hash>:<version>`처럼 retry-safe key를 쓴다.
- POST 2xx만 성공으로 부르지 않고 readback으로 동일 label/score/identifier를 확인한다.
- Phoenix root repository가 ELv2인 현재 상태에서 implementation 복제는 하지 않는다.
- UI, trace DB, LLM evaluator를 ooptdd에 들이지 않는다.

**현 상태.** recording opener로 request shape를 확인한 기존 probe는 contract test일 뿐 live
Phoenix 효능 증거가 아니다. `[O05]`

## O06 — Langfuse: typed score envelope은 좋은 sink 계약이다

**관측.** Langfuse score는 trace/observation/session/dataset run 중 한 subject에 연결되고,
numeric/categorical/boolean/text type을 가진다. score ID는 idempotency key로 사용할 수 있다.
공식 문서는 deterministic code evaluator와 LLM-as-a-judge의 적용 영역을 구분한다.

**판정.** `BRIDGE CANDIDATE`.

- ooptdd 3치 verdict는 categorical label로 보존한다.
- `inconclusive`를 numeric zero나 boolean false로 붕괴시키지 않는다.
- score ID와 source/spec hash를 함께 보내고 GET readback을 요구한다.
- Langfuse experiment/UI/analytics를 복제하지 않는다.

Phoenix와 Langfuse는 경쟁 제품이 아니라 ooptdd 결과의 유통망이다. `[O06]`

## O07 — Stryker: mutation score의 분모부터 정직해야 한다

**관측.** Stryker는 killed, survived, no coverage, timeout, runtime/compile error, ignored 등
mutant 상태를 분리하고, mutation score를 detected/valid로 정의한다. timeout을 detected로
계산하는 정책도 명시한다.

**판정.** `ABSORB WITH SEMANTIC DIFFERENCE`.

- 흡수: stable mutant ID, operator, killed/survived, eligible denominator, status counts.
- ooptdd 차이: backend timeout/outage는 테스트가 mutant를 죽였다는 증거가 아니므로
  `inconclusive`다. Stryker의 timeout-as-detected를 그대로 가져오지 않는다.
- mutant 0개에서 100%를 주장하지 않고 `unmeasured`로 둔다.

**이번 착지.** 궤적 gate에 rename/corrupt/reorder/inject mutant가 생겨 `n>=1`의 실측
가능한 분모를 만든다. `[O07]`

## O08 — k6: benchmark를 named scenario와 threshold로 운용한다

**관측.** k6는 scenario별 executor, env, tag, schedule을 분리하고 threshold를 test의
pass/fail 조건으로 둔다. setup → scenario/VU → teardown lifecycle과 fixed random seed
표면도 제공한다.

**판정.** `ABSORB`.

- 고정 이름의 loss/lag/flap/outage/independence/mutation scenario를 manifest에 둔다.
- scenario별 expected verdict와 threshold를 판정 정본으로 고정한다.
- setup/cleanup 결과도 receipt에 넣어 fault가 다음 run에 누출되지 않게 한다.
- fixed seed와 fixed clock을 쓰고 결과에서 PID/현재 시각/임시 경로를 제거한다.

k6 engine을 의존성으로 들이는 것이 아니라 benchmark 운영 규율을 흡수한다. `[O08]`

## O09 — OpenAI Evals: dataset identity는 이름·split·version·hash의 곱이다

**관측.** OpenAI Evals는 registry data를 JSONL로 두고 eval을 `<name>.<split>.<version>`으로
등록한다. local JSONL log도 제공한다.

**판정.** `ABSORB`.

- benchmark fixture는 `name`, `split`, `version`, manifest SHA-256을 모두 가진다.
- candidate result는 source head와 같은 manifest hash에 묶인다.
- held-out이나 negative fixture를 바꾼 결과를 같은 benchmark version으로 비교하지 않는다.

Evals framework나 모델 호출 protocol을 내장할 필요는 없다. `[O09]`

## O10 — OpenTelemetry GenAI: 이미 채택한 어휘를 계속 정본으로 둔다

**관측.** OTel GenAI semantic conventions는 agent/tool/model operation에 표준 `gen_ai.*`
attribute와 span vocabulary를 제공한다. ooptdd의 ontology/semconv preset은 이 vocabulary를
사용한다.

**판정.** `ALREADY ABSORBED`.

- 자체 agent vocabulary를 새로 만들지 않는다.
- `gen_ai.execute_tool`에 필수 tool name이 없는 경우 ontology RED를 유지한다.
- upstream convention 변화는 versioned preset과 compatibility test로 받는다.
- Phoenix OpenInference 변환층이나 provider별 별칭을 core vocabulary로 만들지 않는다.

O10은 이번 PROM의 신규 기능이 아니라 이미 닫힌 약점을 확인한 control finding이다.
`[O10]`

## Axis A 합의

OSS에서 가져올 공통 최소치는 `raw observation → deterministic verdict → canonical JSON →
lossy sink projection → sink readback`이다. 이 선을 넘어서 LLM judge, UI, storage, selector
language, provider matrix를 흡수하면 ooptdd의 작은 검증 kernel과 3치 정직성이 약해진다.
