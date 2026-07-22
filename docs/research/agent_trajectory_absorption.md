# Agent-trajectory vocabulary absorption — DeepEval / Phoenix

Survey date: 2026-07-22, against local clones of deepeval and arize-phoenix.
Implements `docs/WEAKNESS_RESOLUTION_PLAN.md` §6. Licensing: this repo is
AGPL-3.0-or-later; the sources surveyed are Apache-2.0 (DeepEval) and
Phoenix's licenses. **Concepts and published vocabulary only were absorbed —
no code was copied; every implementation in `ooptdd.engine.trajectory` is
original.**

## The dividing line

Both tools split cleanly into a deterministic core and an LLM-judge shell.
Only the deterministic core belongs in ooptdd (an LLM judge cannot be an
arrival oracle); the LLM-judge shell composes via the eval-platform bridge
(plan §4) instead.

### DeepEval (deepeval/metrics/)

| Metric | Paradigm | Disposition |
|---|---|---|
| `ToolCorrectnessMetric` (tool-calling score) | deterministic | **absorbed** → `tool_calls:` |
| `ArgumentCorrectnessMetric` | LLM-judge | bridge |
| `TaskCompletionMetric`, `PlanAdherenceMetric`, `PlanQualityMetric` | LLM-judge | bridge |
| `StepEfficiencyMetric` | LLM-judge | deterministic stand-in: tool-call count check + `aggregate:` token budget |
| `ToolUseMetric`, `MCPUseMetric`, `GoalAccuracyMetric` | LLM-judge | bridge |

What was absorbed from `ToolCorrectnessMetric` (tool_correctness.py):

- its three matching modes → `tool_calls.match`:
  `should_exact_match` → `exact` (positional; length mismatch = 0),
  default → `subset` (greedy best-match recall over expected tools),
  `should_consider_ordering` → `ordered` (weighted LCS / len(expected));
- `evaluation_params` (name-only vs +args) → `tool_calls.compare: [name, args]`;
- `_compare_dicts` Jaccard-weighted key overlap with nested-dict recursion →
  argument partial credit (literal args only; matcher mode is binary);
- the score-vs-threshold shape (`threshold`, `strict_mode`) → `op`/`target`.

Data-model correspondence: DeepEval `ToolCall{name, input_parameters, output}` /
`LLMTestCase.tools_called` vs `expected_tools` → here the *observed* side is the
`gen_ai.execute_tool` events that arrived in the store for the cid (name from
`gen_ai.tool.name`, args from `gen_ai.tool.call.arguments`, JSON-string tolerated).
That is the ooptdd twist: DeepEval scores the agent's self-reported call list;
we score what landed. `output` comparison was not absorbed (tool results live in
their own events; assert them with `present/where` or ontology constraints).

### Phoenix

| Surface | Paradigm | Disposition |
|---|---|---|
| `evals/pxi/evaluators/tools.py` `evaluate_tools_called` (required/forbidden/exact_match) | deterministic | **absorbed** → `tool_calls:` modes + `forbidden_tools:` |
| same file, matcher vocabulary (`_MATCHER_KEYS`) | deterministic | **absorbed** → arg matchers (`equals, contains_all, contains_any, not_contains, any, non_empty, absent, empty_or_absent, has_keys`) |
| `tool_call_count_within_limit` | deterministic | already expressible: `{event: gen_ai.execute_tool, op: lte, target: N}` |
| `forbidden_tool_call_args_match` | deterministic | expressible: `absent:` with `where`, or `tool_calls` matchers on a forbidden combination |
| trace rollups (`db/insertion/span.py` cumulative token/error counts) | deterministic | **absorbed (concept)** → `aggregate: {fn, attr, event, op, target}` |
| span-kind inference + GenAI→OpenInference map (`trace/gen_ai/conversion.py`) | deterministic | not absorbed — conversion vocabulary, not gate logic; we already speak OTel GenAI semconv natively |
| phoenix-evals classification evaluators (tool_selection, tool_invocation, tool_response_handling, …) | LLM-judge | bridge |
| eval-attach model (`SpanAnnotation.annotator_kind ∈ {LLM, CODE, HUMAN}`) | schema | informs the §4 bridge: ooptdd verdicts export as `CODE` annotations |

Phoenix has no first-class "trajectory convergence" evaluator; its notion is the
set/sequence comparison above (TRAJECT-Bench examples: matched/missing/extra tool
sets, parallel = order-agnostic, sequential = ordered). `tool_calls` `subset` vs
`ordered` map exactly onto parallel vs sequential trajectories.

## What landed (ooptdd.engine.trajectory)

- `tool_calls:` — expected-vs-arrived tool-call scoring; modes exact/subset/ordered;
  name-only or +args; Jaccard credit for literal args, binary matcher mode;
  strength `value-pinned`, charged iff tool events arrived.
- `forbidden_tools:` — negative wing over tool names; strength `forbid`,
  charged only when it saw an offender (mirrors `absent`).
- `aggregate:` — sum/max/min/avg of a numeric attr over the cid's events vs a
  budget; strength `threshold`; empty-set semantics: `sum` is vacuously within
  budget but uncharged, other fns report `aggregate_no_values` and fail.

All three register through the `@check` seam; the kernel is untouched. Tests:
`tests/test_trajectory_checks.py`. Runnable RED/GREEN pairs:
`examples/test_agent_trajectory.py`.

## Deliberately not absorbed

- LLM-judge scoring of any kind (task completion, plan adherence, argument
  *reasonableness*, tool selection quality) — not an arrival fact; bridge (§4).
- DeepEval `ToolCall.output` equality — assert tool results as their own events.
- Phoenix OpenInference conversion layer and UI/annotation storage — platform
  surface, out of lane.
- Embedding/vector trajectory similarity — neither tool has it; neither do we.
