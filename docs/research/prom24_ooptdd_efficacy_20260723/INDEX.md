# PROM 24 — ooptdd 효능 검증과 흡수 지도

> 조사일: 2026-07-23  
> 범위: 유사 OSS 10개 관점 + 논문 14편 = 24개 finding  
> 소스 정책: 프로젝트 공식 문서·공식 저장소·논문 원문/출판처만 사용  
> 상태: Tier 0 `CODED` / final artifact pending; DeepEval CI
> `WIRED/PENDING`; Tier 1 **`MEASURED` (2026-07-24, 후술)**. Tier 1 측정
> 자체는 2026-07-24에 수행됐다 (이 문서는 07-23 조사 시점의 기록이다).

이 묶음의 결론은 간단하다. ooptdd가 직접 LLM 품질평가 제품을 다시 만들 이유는
없다. ooptdd가 독자적으로 강해져야 할 지점은 다음 네 가지다.

1. 도착한 원시 관측에서 지표를 다시 계산하는 evidence integrity,
2. 궤적 게이트에도 실제 mutant가 생기는 비공허 mutation testing,
3. loss/lag/flap/outage/독립성 기제를 반복 재현하는 결정론적 Tier 0,
4. 그 뒤에만 붙는 외부 저장소 Tier 1과 얇은 유통 어댑터다.

## 파일 지도

| 파일 | 내용 |
|---|---|
| [PROM_24_REPORT.md](PROM_24_REPORT.md) | 답부터 제시하는 종합 결론, 합의·충돌·R&D 순서 |
| [SOURCES.md](SOURCES.md) | 실제 사용한 1차 출처와 finding ID의 연결 |
| [A_oss.md](A_oss.md) | Tracetest, Inspect, promptfoo, DeepEval, Phoenix 등 OSS 10개 finding |
| [B_papers.md](B_papers.md) | τ-bench, ToolSandbox, LTL3, mutation testing 등 논문 14개 finding |
| [C_absorption_matrix.md](C_absorption_matrix.md) | 흡수/통합/보류/금지 판정과 코드·증거 표면 |
| [D_measurement_plan.md](D_measurement_plan.md) | 사전등록된 효능 지표, 음성대조, Tier 0/1 실행 계약 |

## 해석 규칙

- `CODED`는 코드와 좁은 테스트가 있다는 뜻이다. 효능이 외부에서 증명됐다는 뜻이
  아니다.
- `WIRED/PENDING`은 코드와 CI step이 존재하지만 현 candidate의 Actions run URL과
  artifact hash가 없다는 뜻이다. 현 DeepEval CI가 이 상태다.
- `PREREGISTERED`는 측정 전에 예측·kill condition이 LakatoTree에 고정됐다는 뜻이다.
- `TIER0_MEASURED`는 final positive/negative/restored artifact가 생성·검증된 뒤의
  결정론적 기제 시험이다. 고유 seed-derived identity를 가진 20회 repetition
  panel의 `pass_hat_k`도 결과 일관성일 뿐, 독립 시행·외부 도착 증거나 population
  generalization이 아니다.
- `TIER1_MEASURED`만 실제 외부 저장소 도착/지연/장애에 관한 headline claim을 허용한다.
- 기존 27-case 궤적 배터리와 DeepEval 3-case 결과는 탐색적 근거다. 새 효능 프로그램의
  사전등록 측정으로 소급하지 않는다.
- Tier 1은 조사 시점(07-23)엔 `NOT MEASURED`였으나, **2026-07-24 `MEASURED` 달성**:
  5개 시나리오×20 rep oracle match 1.0, catch 1.0 / false-RED 0.0 / inconclusive
  1.0 / restore 1.0 (candidate `b8fb4f4`, OO v0.14.7, seed 20260723, 영수증
  `gj3447/lakatotree` `ooptdd_receipts/ooptdd_tier1_arrival_20260724/`).

## 고정 비목표

- OMD 및 폐기된 OMD MCP를 이 측정·조정·판정 경로에 넣지 않는다.
- generic LLM-as-a-judge를 ooptdd의 핵심 판정기로 만들지 않는다.
- Phoenix/Langfuse/Tracetest의 UI·대시보드·저장소를 복제하지 않는다.
- red-team 입력 생성기를 만들지 않는다. 이미 고정된 음성대조 fixture만 쓴다.
- 풍부한 trajectory quality metric과 provider leaderboard를 제품 범위로 확장하지 않는다.

이 경계는 기능 부족이 아니라 측정 독립성을 지키기 위한 설계 제약이다.
