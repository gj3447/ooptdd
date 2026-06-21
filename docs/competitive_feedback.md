# ooptdd competitive feedback

This note compares ooptdd against nearby agent testing, eval, and trace-based
testing tools checked out locally under `../GIT/`:

- Tracetest
- Arize Phoenix
- DeepEval
- promptfoo
- LangSmith SDK
- HoneyHive SDK
- Ragas
- OpenAI Evals
- simple-evals

The goal is not to copy their scope. The goal is to keep ooptdd's sharp edge
while closing the gaps that make it look smaller or less credible than adjacent
tools.

## Position

ooptdd is strongest when framed as:

> Positive arrival testing for logs and traces: the generator's claim is not
> trusted until an independent verifier reads the event back from the store.

That is a narrower claim than "LLM evaluation platform" or "AI observability",
but it is more falsifiable. Most adjacent tools evaluate outputs, traces, spans,
or model quality. ooptdd verifies that an expected runtime event actually landed
in an external store and turns that into a TDD gate.

## Strengths

### Conceptual sharpness

ooptdd has a crisp core:

- spec-first event contract
- real emission by the system under test
- external readback verifier
- three-valued verdict: present / absent / inconclusive
- generator != verifier
- YAML gate as the test artifact

This is clearer than broad "eval platform" positioning. It answers a specific
failure mode: a tool, agent, or service says "done" while the external evidence
is missing.

### Small engine

The codebase is much smaller than Tracetest, Phoenix, promptfoo, DeepEval, and
LangSmith SDK. That is useful for:

- vendoring into internal consumers
- auditing the core semantics
- keeping the dependency surface small
- making the zero-infra path work locally
- preserving a pytest-native workflow

This size is a product advantage if the project stays focused.

### Backend abstraction

The built-in backend registry already separates the core from storage:

- `memory`
- `openobserve`
- `otel`
- `clickhouse`
- `signoz`
- `victorialogs`
- third-party `ooptdd.backends` entry points

That keeps OpenObserve as a strong production target without making it a hard
dependency.

### Honesty about uncertainty

The `inconclusive` verdict is important. It prevents observability outages from
being mislabeled as product failures. Many test tools collapse "could not
observe" into pass/fail too early.

### Ontology layer

The file-first ontology catches event drift classes that flat event-count gates
miss:

- missing required attributes
- unknown or fabricated event types
- invalid enum/type/range values
- unexpected payload attributes when closed

This gives ooptdd a path from "did an event arrive?" to "did the right event
arrive with the right shape?" without becoming a full schema registry.

## Weaknesses

### Product surface is thin

Compared with Phoenix, LangSmith, HoneyHive, and Tracetest, ooptdd has no UI,
dashboard, trace viewer, annotation queue, experiment comparison, prompt
versioning, or alerting.

That is acceptable for a library, but it means ooptdd should not claim platform
parity. The positioning should stay focused on pytest/CI and external arrival
proof.

### Agent-specific evaluation is underdeveloped

DeepEval, Ragas, Phoenix, LangSmith, and HoneyHive offer agent or workflow
metrics such as:

- task completion
- tool correctness
- tool call accuracy
- argument correctness
- step efficiency
- plan adherence
- goal accuracy

ooptdd currently verifies emitted events, but it does not provide a rich agent
trajectory vocabulary out of the box.

### Trace/span querying is weaker than Tracetest

Tracetest has a mature trace-testing model around:

- trace retrieval
- span selectors
- span attribute checks
- timing checks
- transaction-level assertions
- CLI/server/agent architecture

ooptdd has event gates and ordering checks, but not a comparable selector
language over a span tree.

### Red-team and security workflows are missing

promptfoo has strong coverage for:

- prompt injection tests
- jailbreak strategies
- red-team plugins
- provider matrices
- CI security scans

ooptdd does not currently cover these workflows. It can verify security-relevant
events if the user emits them, but it does not generate or manage adversarial
cases.

### Memory default weakens the main claim

The `memory` backend is valuable for zero-infra testing, but it is not an
independent external judge. If users only see `memory`, they may conclude
ooptdd is just a structured in-process log assertion helper.

The docs should make the split explicit:

- `memory` proves the gate mechanics
- OpenObserve/ClickHouse/VictoriaLogs prove external arrival
- OTLP proves portable writing, not portable reading

### Maturity gap

The adjacent projects have larger ecosystems, more integrations, more tests,
and more operational surfaces. ooptdd is easier to understand, but looks less
battle-tested.

Specific visible gaps:

- fewer examples
- fewer backend drivers
- limited integration docs
- no compatibility matrix with real backend capabilities
- no public benchmark or adoption story
- limited agent-framework integration examples

## Recommended Roadmap

### P0: Clarify positioning

Update public docs to avoid ambiguity:

- ooptdd is not an OpenObserve wrapper, but OpenObserve is a first-class target.
- ooptdd is not a full eval platform.
- `memory` is for local mechanics, not the strongest proof mode.
- production-grade ooptdd should use an external queryable store.

Suggested one-liner:

> ooptdd is pytest-native positive-arrival testing for structured logs and
> traces: write the expected event contract, run the system, and verify the
> event arrived in an independent store.

### P1: Strengthen OpenObserve path

OpenObserve should feel like the reference production path, not an optional
afterthought.

Add:

- a full OpenObserve quickstart
- a docker-compose OpenObserve example
- a negative-wing demo for ingest auth failure
- a clear `warn` to `strict` migration guide
- screenshots or query examples showing records in OpenObserve
- health-check guidance before strict mode

### P1: Add backend capability matrix

Document each backend by behavior, not just name:

| Backend | write | query | external judge | ordering | field filters | timing | notes |
|---|---|---|---|---|---|---|---|
| memory | yes | yes | no | yes | yes | limited | local only |
| openobserve | yes | yes | yes | yes | yes | backend-dependent | reference network backend |
| clickhouse | yes | yes | yes | yes | yes | strong if schema stable | SQL family |
| victorialogs | yes | yes | yes | partial | partial | backend-dependent | log query backend |
| otel | yes | no | no | no | no | no | write-only transport |

This directly addresses the "does it need OO?" confusion.

### P1: Add agent event vocabulary

Add a built-in agent ontology preset with event types such as:

- `agent.run.started`
- `agent.plan.created`
- `agent.tool.selected`
- `agent.tool.called`
- `agent.tool.result`
- `agent.step.failed`
- `agent.run.completed`
- `agent.run.failed`

Then ship gates for:

- required tool call happened
- forbidden tool was not called
- tool args conform to schema
- run completed after required tool result
- no ERROR events in the run

This keeps ooptdd in its event-proof lane while covering agent workflows better.

### P2: Add trace/span selector subset

Do not copy Tracetest wholesale. Add a small selector layer that maps onto the
existing gate engine:

- `where` filters on event/span attributes
- `must_order` across filtered events
- `duration_s` threshold checks
- `within` bounded timing checks
- `conforms` against ontology

The project already has pieces of this. The next step is a documented,
stable gate grammar with examples.

### P2: Integrate with eval tools instead of replacing them

ooptdd should compose with eval platforms:

- export ooptdd verdicts as OpenTelemetry attributes
- emit a `test_session` / `ooptdd.verdict` event that Phoenix/LangSmith can show
- provide a DeepEval custom metric wrapper that checks ooptdd arrival
- provide promptfoo command hooks that run `ooptdd gate`

This turns competitors into integration surfaces.

### P2: Add proof-oriented examples

Add examples that demonstrate bugs ordinary tests miss:

- function returns OK but no log arrives
- OpenObserve auth misconfigured
- event name typo
- required field missing
- wrong tool called by an agent
- tool called with wrong argument type
- background job claims completion but worker never emits final event

Each example should have a RED and GREEN form.

### P3: Add lightweight report output

A UI is not necessary yet, but the CLI should produce useful artifacts:

- JSON verdict report
- markdown report
- JUnit XML option for CI
- linkable backend query / correlation id summary
- compact failure explanation with source anchor when available

This improves credibility without building a platform.

## What not to do

Do not turn ooptdd into a broad LLM eval framework. DeepEval, Ragas, Phoenix,
LangSmith, HoneyHive, promptfoo, and OpenAI Evals already cover that space.

Avoid:

- generic LLM-as-judge scoring as the core product
- dashboard-first development
- heavy provider matrices before the event-proof layer is stable
- copying Tracetest's entire trace selector model
- making OpenObserve mandatory
- hiding backend limitations behind a uniform API

## Strategic judgment

ooptdd is not currently the most feature-complete agent testing product. It is
much smaller and lacks platform features. Its advantage is a precise testing
claim that adjacent tools often treat as a secondary concern:

> The expected runtime evidence must arrive in an independent store.

Keep that as the center. Build enough integrations, examples, and backend
capability documentation that users understand when `memory` is sufficient and
when OpenObserve or another external backend is required.
