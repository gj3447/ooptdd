# ooptdd

`ooptdd` is an importable event-contract framework. It evaluates arbitrary
structured event streams against explicit contracts and can verify that expected
events actually arrived in a queryable store.

The base package does not prescribe TDD, pytest, an agent workflow, or a domain
vocabulary. Applications choose their own lifecycle, event schema, backend,
policy, and extension providers.

## Install

The project is not published to PyPI yet. Install it from a checkout or path
dependency:

```bash
uv pip install -e path/to/ooptdd
# Once published: pip install ooptdd
```

The install provides the `ooptdd` Python package and CLI. It does not
automatically modify pytest runs or register domain-specific checks.

## Minimal library use

```python
from ooptdd import evaluate
from ooptdd.backends import MemoryBackend

backend = MemoryBackend()
backend.ship([
    {"cid": "order-42", "event": "order.accepted"},
    {"cid": "order-42", "event": "order.completed", "status": "ok"},
])

contract = {
    "cid": "order-42",
    "expect": [
        {"event": "order.accepted", "op": ">=", "count": 1},
        {
            "event": "order.completed",
            "where": {"status": "ok"},
            "op": "==",
            "count": 1,
        },
    ],
}

result = evaluate(backend, contract)
assert result["ok"], result["checks"]
```

The contract vocabulary operates on ordinary mappings. It is not tied to test
reports, GenAI telemetry, or a particular business domain.

## What the core provides

- A deterministic event-contract kernel and three-valued verdicts:
  `present`, `absent`, and `inconclusive`.
- Immutable runtime and gate policies captured at an application boundary.
- Backend ports plus memory, JSONL, OpenObserve, ClickHouse, VictoriaLogs, and
  write-only OTLP adapters.
- Bounded polling with backoff, visibility windows, confirmation rounds, and
  injected clock/sleeper ports.
- Custom check, backend, probe, and ontology seams.
- An experimental, deterministic Ouroboros protocol under
  `ooptdd.ouroboros`; it is a generic bounded evidence protocol, not a runner or
  development methodology.

`ship()` succeeding is only a delivery claim. `verify_gate()` queries the backend
and distinguishes a clean miss from infrastructure that could not be observed:

```python
from ooptdd import verify_gate

verdict = verify_gate(backend, "order-42", contract, retries=3, delay=0.1)
```

For real arrival evidence, use a queryable store outside the emitting process.
Memory and JSONL are useful for deterministic mechanics but do not establish an
independent authority. See [the threat model](docs/THREAT_MODEL.md).

## Configuration and embedding

Applications may use the immutable composition boundary:

```python
from ooptdd.sdk import compose_runtime

runtime = compose_runtime(
    project={"backend": "memory", "retries": 3},
    environment={},
    overrides={"delay": 0.1},
)
backend = runtime.backend()
```

Resolution is deterministic:

```
defaults < project mapping < captured environment < explicit overrides
```

Backend-specific values belong under `backend_options`. Secrets remain in the
captured environment and are kept separate from ordinary settings.

## Optional adapters and extensions

### pytest adapter

The pytest integration ships in `ooptdd.plugin`, but it is opt-in and is not a
package entry point. Install the adapter dependency and activate it explicitly:

```bash
uv pip install -e 'path/to/ooptdd[pytest]'
pytest -p ooptdd.plugin
```

Projects may instead add `pytest_plugins = ["ooptdd.plugin"]` to their own
`conftest.py`. The plugin is one consumer of the framework; pytest outcome names
are not part of the generic gate kernel.

### Domain extensions

Optional trajectory checks ship in the `ooptdd-trajectory` distribution. GenAI
event helpers, semantic conventions, and evaluation-platform bridges ship in
`ooptdd-genai`. Mutation analysis and its opinionated bounded-cycle profile ship in
`ooptdd-mutation`. None is a dependency of the base package, and importing an extension
is inert with respect to the gate registry. Install only the distribution you need and
inject predicate providers explicitly:

```python
from ooptdd.sdk import compose_runtime
from ooptdd_genai import execute_tool_event, gen_ai_ontology
from ooptdd_trajectory import ooptdd_checks

runtime = compose_runtime(
    project={"extensions": ["trajectory"]},
    environment={},
    extension_providers={"trajectory": ooptdd_checks},
).activate_extensions()
result = runtime.evaluate(backend, spec)
```

Third-party backends can register through the `ooptdd.backends` entry-point
group. Embedders inject third-party named predicate providers through
`compose_runtime(extension_providers={...})`; names absent from that immutable
catalog fail closed. The `@ooptdd.check(...)` decorator attaches metadata only;
compose decorated checks explicitly with `checks_from(...)` or a runtime registry.
Custom probes implement the small
`ExternalProbe` port.

## CLI

The CLI is another adapter over the same library:

```bash
ooptdd gate gates/order.yaml
ooptdd verify order-42 --backend openobserve
```

## Repository-only research

`benchmarks/`, `scripts/`, `docs/research/`, and frozen research fixtures belong
to this source repository. They measure and document the framework; they are not
installed as `ooptdd` modules and are not included in the wheel.

See [Quickstart](docs/quickstart.md), [semantics and design notes](SEMANTICS.md),
[backend capabilities](docs/backends.md), and [Ouroboros](docs/ouroboros.md).

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
