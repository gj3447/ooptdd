# SOURCES — PROM 24 ooptdd efficacy absorption

> 확인일: 2026-07-23. 프로젝트 공식 문서·공식 저장소·논문 원문 또는 공식 출판처만
> 채택했다. 블로그 요약, 비교 사이트, 검색 결과 요약은 finding 근거에서 제외했다.

## OSS·표준 1차 출처

### O01 — Tracetest

- [Tracetest official repository](https://github.com/kubeshop/tracetest)
- [Polling Profiles](https://docs.tracetest.io/concepts/polling-profiles)
- [Selectors](https://docs.tracetest.io/concepts/selectors)
- [Assertions](https://docs.tracetest.io/concepts/assertions)
- [Tracetest Cloud end-of-life announcement](https://tracetest.io/blog/end-of-life-announcement-for-tracetest-cloud)

### O02 — Inspect AI

- [Eval Logs](https://inspect.aisi.org.uk/eval-logs.html)
- [Scoring Workflow](https://inspect.aisi.org.uk/scoring-workflow.html)
- [Scoring](https://inspect.aisi.org.uk/scoring.html)
- [Log API reference](https://inspect.aisi.org.uk/reference/inspect_ai.log.html)

### O03 — promptfoo

- [Output Formats](https://www.promptfoo.dev/docs/configuration/outputs/)
- [Assertions and Metrics](https://www.promptfoo.dev/docs/configuration/expected-outputs/)
- [Official repository](https://github.com/promptfoo/promptfoo)

### O04 — DeepEval

- [Official repository](https://github.com/confident-ai/deepeval)
- [Custom Metrics](https://deepeval.com/docs/metrics-custom)
- [Tool Correctness](https://deepeval.com/docs/metrics-tool-correctness)
- [ToolCorrectnessMetric source](https://github.com/confident-ai/deepeval/blob/main/deepeval/metrics/tool_correctness/tool_correctness.py)

### O05 — Arize Phoenix

- [What is Phoenix?](https://arize.com/docs/phoenix)
- [Deterministic tool evaluator source](https://github.com/Arize-ai/phoenix/blob/main/evals/pxi/evaluators/tools.py)
- [Span Annotations](https://arize.com/docs/phoenix/sdk-api-reference/typescript/packages/phoenix-client/span-annotations)
- [Phoenix license](https://github.com/Arize-ai/phoenix/blob/main/LICENSE)

### O06 — Langfuse

- [Scores Data Model](https://langfuse.com/docs/evaluation/scores/data-model)
- [Scores via API/SDK](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk)
- [Code Evaluators](https://langfuse.com/docs/evaluation/evaluation-methods/code-evaluators)
- [Repository license boundary](https://github.com/langfuse/langfuse/blob/main/LICENSE)

### O07 — Stryker

- [Mutant states and metrics](https://stryker-mutator.io/docs/mutation-testing-elements/mutant-states-and-metrics/)
- [Mutation Testing Elements schema](https://github.com/stryker-mutator/mutation-testing-elements)

### O08 — Grafana k6

- [Scenarios](https://grafana.com/docs/k6/latest/using-k6/scenarios/)
- [Thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)
- [Test lifecycle](https://grafana.com/docs/k6/latest/using-k6/test-lifecycle/)
- [randomSeed API](https://grafana.com/docs/k6/latest/javascript-api/k6/random-seed/)

### O09 — OpenAI Evals

- [Official repository](https://github.com/openai/evals)
- [Build an eval](https://github.com/openai/evals/blob/main/docs/build-eval.md)
- [Run evals and JSONL logs](https://github.com/openai/evals/blob/main/docs/run-evals.md)

### O10 — OpenTelemetry GenAI semantic conventions

- [Generative AI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [GenAI attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
- [Semantic conventions GenAI repository](https://github.com/open-telemetry/semantic-conventions-genai)

## 논문 1차 출처

### P01 — τ-bench

- Shunyu Yao et al., [τ-bench: A Benchmark for Tool-Agent-User Interaction in
  Real-World Domains](https://arxiv.org/abs/2406.12045), 2024.
- [Official benchmark site](https://taubench.com/)

### P02 — ToolSandbox

- Jiarui Lu et al., [ToolSandbox: A Stateful, Conversational, Interactive Evaluation
  Benchmark for LLM Tool Use Capabilities](https://aclanthology.org/2025.findings-naacl.65/),
  Findings of NAACL 2025.
- [Official Apple research page](https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark)
- [Official artifact](https://github.com/apple/ToolSandbox)

### P03 — AgentTrace

- Adam AlSayyad et al., [AgentTrace: A Structured Logging Framework for Agent System
  Observability](https://arxiv.org/abs/2602.10133), 2026.

### P04 — MR-Scout

- Yuchi Tian et al., [MR-Scout: Automated Synthesis of Metamorphic Relations from
  Existing Test Cases](https://doi.org/10.1145/3656340), ACM TOSEM, 2024.
- [Author preprint](https://arxiv.org/abs/2304.07548)

### P05 — Practical mutation testing at Google

- Goran Petrović et al., [Practical Mutation Testing at Scale: A View from
  Google](https://research.google/pubs/practical-mutation-testing-at-scale-a-view-from-google/),
  IEEE TSE, 2021.

### P06 — LLM-judge self-preference

- Chen et al., [Beyond the Surface: Measuring Self-Preference in LLM
  Judgments](https://aclanthology.org/2025.emnlp-main.86/), EMNLP 2025.

### P07 — randomness in agentic evaluation

- [On Randomness in Agentic Evals](https://arxiv.org/abs/2602.07150), 2026.
- [Authors' released trajectory artifact](https://zenodo.org/records/18684664)

### P08 — LiveBench

- Colin White et al., [LiveBench: A Challenging, Contamination-Limited LLM
  Benchmark](https://proceedings.iclr.cc/paper_files/paper/2025/hash/e4a46394ba5378b3f9a186a5b4c650d1-Abstract-Conference.html),
  ICLR 2025.

### P09 — AgentDojo

- Edoardo Debenedetti et al., [AgentDojo: A Dynamic Environment to Evaluate Prompt
  Injection Attacks and Defenses for LLM Agents](https://papers.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html),
  NeurIPS 2024.
- [Official artifact and documentation](https://agentdojo.spylab.ai/)

### P10 — AgentBoard

- Chang Ma et al., [AgentBoard: An Analytical Evaluation Board of Multi-turn LLM
  Agents](https://openreview.net/forum?id=09Y7J22N9c), ICLR 2024.

### P11 — AgentBench

- Xiao Liu et al., [AgentBench: Evaluating LLMs as
  Agents](https://openreview.net/forum?id=zAdUB0aCTQ), ICLR 2024.
- [Official artifact](https://github.com/THUDM/AgentBench)

### P12 — ToolLLM

- Yujia Qin et al., [ToolLLM: Facilitating Large Language Models to Master 16000+
  Real-world APIs](https://proceedings.iclr.cc/paper_files/paper/2024/hash/28e50ee5b72e90b50e7196fde8ea260e-Abstract-Conference.html),
  ICLR 2024.

### P13 — three-valued runtime semantics

- Andreas Bauer, Martin Leucker, Christian Schallhart,
  [Comparing LTL Semantics for Runtime Verification](https://doi.org/10.1093/logcom/exq035),
  Journal of Logic and Computation, 2011.
- [Author manuscript](https://www.pspace.org/a/publications/JLC2010.pdf)

### P14 — test oracle problem

- Earl T. Barr et al., [The Oracle Problem in Software Testing: A
  Survey](https://discovery.ucl.ac.uk/id/eprint/1471263/), IEEE TSE 41(5), 2015.

## 출처 사용 제한

- 문서/논문에서 개념과 공개 vocabulary만 흡수했다. 소스 코드를 복사했다는 뜻이 아니다.
- 특히 Phoenix repository의 현재 root license는 ELv2이므로 API 상호운용과 공개 개념만
  사용하고 구현을 복제하지 않는다.
- 논문 수치는 해당 논문의 실험 결과일 뿐 ooptdd 효능 수치가 아니다.
- 이 목록에 없는 블로그·Reddit·벤더 비교표는 PROM 24 판정 근거가 아니다.
