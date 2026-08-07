# Quickstart

This quickstart uses `ooptdd` as a normal Python library. No test runner or
development methodology is required.

## 1. Install from a checkout

`ooptdd` is not published to PyPI yet:

```bash
uv pip install -e path/to/ooptdd
# Once published: pip install ooptdd
```

Installation does not auto-register the optional pytest adapter.

## 2. Evaluate an event contract

```python
from ooptdd import evaluate
from ooptdd.backends import MemoryBackend

backend = MemoryBackend()
backend.ship([
    {"cid": "job-7", "event": "job.started"},
    {"cid": "job-7", "event": "job.finished", "result": "accepted"},
])

spec = {
    "cid": "job-7",
    "expect": [
        {"present": [{"event": "job.started"}]},
        {
            "event": "job.finished",
            "where": {"result": "accepted"},
            "op": "==",
            "count": 1,
        },
    ],
}

result = evaluate(backend, spec)
assert result["ok"], result["checks"]
```

Replace the event names and fields with any domain vocabulary. The core treats
events as structured mappings.

## 3. Verify asynchronous arrival

`evaluate()` reads once. `verify_gate()` applies one immutable bounded-polling
policy and returns `present`, `absent`, or `inconclusive`:

```python
from ooptdd import verify_gate
from ooptdd.domain.settings import PollingSettings

policy = PollingSettings(retries=4, delay=0.25, backoff=2, max_delay=2)
verdict = verify_gate(backend, "job-7", spec, polling=policy)
```

Use an independent queryable backend when the result must prove external
arrival. The memory backend only demonstrates deterministic gate mechanics.

## 4. Use runtime composition

```toml
[tool.ooptdd]
backend = "openobserve"
service = "orders"
retries = 4

[tool.ooptdd.adapters.pytest]
verify = "warn"
```

```bash
export OOPTDD_OO_URL=http://your-host:5080
export OOPTDD_OO_PASSWORD=…
ooptdd verify job-7
```

`compose_runtime()` captures project values, environment, and explicit overrides
once. Core evaluation does not re-read ambient environment variables.

## Optional pytest adapter

To consume the framework from pytest, activate its adapter explicitly:

```bash
uv pip install -e 'path/to/ooptdd[pytest]'
pytest -p ooptdd.plugin
```

Alternatively put `pytest_plugins = ["ooptdd.plugin"]` in your project's
`conftest.py`. This integration is optional and is not the framework's base
identity.

Install `ooptdd-trajectory` and inject `ooptdd_trajectory.ooptdd_checks` into
`compose_runtime(extension_providers={"trajectory": ooptdd_checks})` before using
`extensions = ["trajectory"]`. Imports do not mutate a global registry. GenAI
event builders remain explicit imports from `ooptdd_genai` because they produce
values rather than gate predicates.

Next: [semantics and design boundaries](../SEMANTICS.md),
[backend capabilities](backends.md), and [the threat model](THREAT_MODEL.md).
