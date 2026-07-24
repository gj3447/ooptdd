# Axis B — 논문 14개 finding

각 finding은 논문의 결과를 ooptdd 수치로 전이하지 않는다. `흡수`는 실험 설계나 계약을
가져온다는 뜻이고, 논문 저자의 implementation을 복사한다는 뜻이 아니다.

## P01 — τ-bench: 한 번의 성공보다 최종 상태와 `pass^k`

**근거.** τ-bench는 대화의 마지막 자연어 답변만 보지 않고 final database state를
annotated goal state와 비교한다. 같은 task를 여러 번 시도했을 때 모두 성공할 신뢰도를
`pass^k`로 제시한다.

**흡수.** ooptdd는 self-reported "emit succeeded" 대신 외부 store의 최종 관측을 보고,
Tier 0/1 scenario를 반복한다. `pass@1`과 함께 보수적 repeated reliability `pass^k`를
계산한다. 모든 반복이 성공하지 않으면 1회 녹색을 효능으로 광고하지 않는다.

**caveat.** τ-bench의 simulated user와 domain policy evaluator를 가져오지 않는다.
ooptdd에 필요한 것은 final-state oracle과 반복 신뢰도다. **Confidence: HIGH.** `[P01]`

## P02 — ToolSandbox: milestone과 minefield를 같은 상태 공간에서 채점

**근거.** ToolSandbox는 stateful tools, implicit state dependencies, on-policy dialogue와
trajectory 중간/최종 milestone·minefield 평가를 결합한다.

**흡수.** required tool/order/argument는 milestone, forbidden tool/call은 minefield로
해석한다. 성공 경로만 있는 fixture는 충분하지 않다. 동일 manifest에 safe positive와
forbidden negative를 함께 두고, 중간 상태를 건너뛴 final text success를 통과시키지 않는다.

**caveat.** user simulator나 대화 품질평가를 core에 넣지 않는다. **Confidence: HIGH.**
`[P02]`

## P03 — AgentTrace: 구조화된 runtime trace는 필요조건이지 독립 oracle은 아니다

**근거.** AgentTrace는 agent runtime의 operational/cognitive/contextual surface를
구조화된 log로 포착해 observability와 accountability를 높이려 한다.

**흡수.** event identity, tool call, state change, provenance를 구조화해 보존한다. 그러나
producer가 만든 trace만으로 producer의 행위를 독립 검증했다고 부르지 않는다.
`BackendCaps.independent`, emit identity, source head, spec hash를 결과에 드러낸다.

**caveat.** 2026 preprint이며 ooptdd가 cognitive trace를 수집해야 한다는 근거로
확대해석하지 않는다. 구조화된 실행 provenance 원칙만 채택한다. **Confidence: MEDIUM.**
`[P03]`

## P04 — MR-Scout: absolute oracle가 약하면 관계를 채점한다

**근거.** MR-Scout는 기존 test case에서 metamorphic relation을 합성해 oracle problem을
완화한다. 핵심은 단일 출력의 절대 정답 대신 관련 입력/출력 사이 보존 관계를 oracle로
사용하는 것이다.

**흡수.** ooptdd의 기존 `metamorphic:` gate를 효능 프로그램의 보조 oracle로 유지한다.
예를 들어 duplicate emit의 idempotency, input scale과 arrived aggregate의 비례 관계처럼
결정론적으로 확인할 수 있는 관계를 사용한다.

**caveat.** MR 자동 합성기를 만들지 않는다. 검토된 관계만 fixture로 고정한다.
**Confidence: HIGH.** `[P04]`

## P05 — Google mutation at scale: mutant 수보다 relevance와 actionability

**근거.** Google 연구는 변경된 코드에 집중한 incremental mutation, 관련성 낮은 mutant
filtering, operator의 과거 성능에 기반한 selection으로 산업 규모에서 actionability를
높였다.

**흡수.** gate 의미론에서 직접 파생되는 operator만 eligible denominator에 넣고, operator
이름·stable ID·status를 결과에 남긴다. generic drop으로 의미가 바뀌지 않는 negative
trajectory rule은 drop 분모에서 제외하고 injection mutant를 생성한다.

**후속.** operator별 survivor history가 쌓인 뒤 저가치 operator budget을 줄인다. 지금은
샘플이 없으므로 자동 pruning하지 않는다. **Confidence: HIGH.** `[P05]`

## P06 — self-preference: 같은 계열 judge는 독립 검증이 아니다

**근거.** EMNLP 2025 연구는 LLM judge의 self-preference를 response quality와 분리해
측정해야 한다는 문제를 다룬다. 생성자와 judge의 관계가 점수 편향을 만들 수 있다는 것이
핵심 경고다.

**흡수.** DeepEval/Phoenix의 LLM-based score는 soft diagnostic 또는 외부 annotation일
뿐, ooptdd의 critical present/absent 판정을 단독으로 결정하지 않는다. 같은 모델 계열의
self-evaluation을 `independent=true`로 표시하지 않는다.

**결정.** ooptdd 자체 generic LLM judge 개발은 금지한다. **Confidence: HIGH.** `[P06]`

## P07 — On Randomness in Agentic Evals: temperature 0도 single-run은 부족하다

**근거.** 대규모 trajectory 반복 연구는 agentic evaluation 결과가 run 선택에 따라
변하며, pass@k/pass^k와 variance를 함께 보고해야 한다고 주장한다.

**흡수.** v0 benchmark는 fixed seed로 20회 반복하고 scenario별 성공 수, rate,
`pass^k`, 전체 oracle-match를 낸다. Tier 0의 결정론성 자체도 고정된 runtime
identity에서 같은 입력의 byte-identical
JSON 두 번으로 검사한다.

**caveat.** 논문의 구체적인 변동 폭을 ooptdd에 전이하지 않는다. **Confidence: HIGH.**
`[P07]`

## P08 — LiveBench: 공개된 고정 fixture는 시간이 지나면 탐색 데이터가 된다

**근거.** LiveBench는 최근 출처 기반 문제와 객관식/자동 채점 ground truth로 contamination
위험과 LLM-judge 의존을 줄인다.

**흡수.** 공개 demo fixture와 confirmatory held-out fixture를 구분하고, benchmark version과
manifest hash를 고정한다. 미래 v1에서는 일정 주기로 새 negative fixture를 추가하되 새
version으로만 비교한다.

**거부.** 실시간 leaderboard와 model/provider 순위표는 만들지 않는다. **Confidence:
HIGH.** `[P08]`

## P09 — AgentDojo: utility success와 forbidden effect를 따로 측정

**근거.** AgentDojo는 정상 task utility와 prompt-injection security를 분리하고, tool을
실행하는 동적 환경에서 공격/방어를 평가한다.

**흡수.** 하나의 최종 성공 boolean이 forbidden tool/action을 숨기지 못하도록 positive
milestone과 negative minefield를 별도 check로 기록한다. 안전 음성대조에서는 forbidden
tool/call injection이 반드시 RED여야 한다.

**거부.** 공격 prompt 생성기, adaptive red-team engine, security leaderboard는 ooptdd
scope가 아니다. 고정된 최소 negative fixture만 쓴다. **Confidence: HIGH.** `[P09]`

## P10 — AgentBoard: progress는 유용한 진단이지만 core gate는 아니다

**근거.** AgentBoard는 final success rate 외에 fine-grained progress rate와 여러 분석
차원을 제공한다.

**판정.** `REFERENCE`.

- milestone별 matched row는 실패 위치를 설명하는 diagnostic으로 유용하다.
- 그러나 풍부한 trajectory progress score를 ooptdd의 핵심 제품으로 만들지 않는다.
- core verdict는 deterministic required/forbidden/order/args/aggregate와 3치 의미론에
  머문다.

**Confidence: HIGH.** `[P10]`

## P11 — AgentBench: 다양한 환경보다 먼저 동일 계약의 재현성

**근거.** AgentBench는 여러 interactive environment에서 agent를 평가하는 통합 benchmark를
제공한다.

**흡수.** Tier 1 확장 시 OpenObserve 하나에서 통과한 계약을 ClickHouse,
VictoriaLogs, OTLP 등 지원 backend로 재실행하는 방향은 타당하다. 다만 backend마다
같은 `BackendCaps`와 honesty fields를 내야 한다.

**우선순위.** v0은 OpenObserve 한 외부 judge의 loss/lag/outage를 먼저 닫는다. 넓은
environment matrix는 그 뒤다. **Confidence: HIGH.** `[P11]`

## P12 — ToolLLM: solution path는 fixture가 될 수 있지만 neural evaluator는 아니다

**근거.** ToolLLM/ToolBench는 단일·다중 tool instruction과 solution path를 구성하고,
unseen APIs generalization을 평가한다.

**흡수.** 검토된 solution path를 `tool_calls` exact/ordered/subset fixture로 변환하고,
known-tool과 renamed/unseen-tool case를 분리한다. path를 만든 LLM의 서술을 oracle로
사용하지 않고 실제 arrived events와 비교한다.

**거부.** API retriever, tool-use model training, neural evaluator를 ooptdd에 넣지 않는다.
**Confidence: HIGH.** `[P12]`

## P13 — LTL runtime semantics: finite trace에서 unknown은 결함이 아니라 정보다

**근거.** runtime verification의 LTL semantics 연구는 finite prefix에서 참/거짓으로
아직 결정할 수 없는 상태를 다루기 위해 다치 논리를 비교한다.

**흡수.** ooptdd의 `present / absent / inconclusive`를 모든 reporter와 bridge에서
보존한다. unreachable/incomplete/probe-unavailable을 absent나 pass로 접지 않는다.

**측정.** outage scenario는 `inconclusive`와 infra exit를 내야 성공한 scenario다. JUnit의
`<failure>` 0개 조건과 별개로 harness-level infra 상태를 보존한다. **Confidence: HIGH.**
`[P13]`

## P14 — Oracle Problem survey: 여러 약한 oracle을 한 개의 가짜 강한 oracle로 합치지 않는다

**근거.** test oracle survey는 specification/contract, model, metamorphic relation 등
oracle automation 접근을 정리한다. 관측된 행동이 올바른지 결정하는 일이 test automation의
병목임을 지적한다.

**흡수.** ooptdd는 세 층을 구분한다.

1. self-emitted event와 같은-process readback,
2. independent external store arrival,
3. separate-source external probe/corroboration.

각 층의 provenance를 결과에 남기고 강도를 승격해 말하지 않는다. absolute oracle가 없는
곳에는 검토된 metamorphic relation을 쓰고, LLM judge로 빈칸을 덮지 않는다.

**Confidence: HIGH.** `[P14]`

## Axis B 합의와 충돌

### 합의

- final text보다 state/trajectory observation이 강하다.
- positive milestone과 negative minefield가 함께 있어야 한다.
- single-run scalar는 repeated reliability와 provenance 없이는 약하다.
- unknown/inconclusive는 손실 없이 보존해야 한다.
- independent evidence는 producer summary와 다른 관측 경로를 가져야 한다.

### 보존한 충돌

- AgentBoard식 dense process metric은 진단력을 높이지만 ooptdd core를 넓힌다. 따라서
  raw row와 실패 위치는 보존하되 새로운 quality score family는 외부 adapter에 둔다.
- AgentDojo는 adaptive attack generation의 가치를 보이지만 ooptdd의 "red-team 생성기
  금지"와 범위가 충돌한다. utility/security 분리와 고정 minefield만 흡수한다.
- Stryker는 timeout mutant를 detected로 보지만 ooptdd는 store timeout을 inconclusive로
  둔다. 대상 시스템과 관측 의미가 다르므로 의도적인 semantic divergence다.
