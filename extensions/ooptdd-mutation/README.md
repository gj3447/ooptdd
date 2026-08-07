# ooptdd-mutation

Optional, first-party mutation analysis and an OOPTDD-specific bounded mutation-cycle
profile for the general [`ooptdd`](../../README.md) event-contract framework.

Install this distribution only when the mutation workflow is wanted. Importing `ooptdd`
does not load, register, or expose this package.

```bash
# Published distributions
python -m pip install 'ooptdd>=0.6,<0.7' 'ooptdd-mutation>=0.1,<0.2'

# This monorepo checkout
python -m pip install ../.. ../ooptdd-trajectory .
```

```python
from ooptdd_mutation import mutation_report
from ooptdd_mutation.ouroboros import CycleSnapshot, EventKind, step
```

The extension depends on the base framework through ordinary public `ooptdd.*` imports.
Its predicate provider is explicit: pass `ooptdd_mutation.ooptdd_checks` to the runtime's
named-provider composition API; importing the package does not mutate a process-global
registry.

```python
from ooptdd.bootstrap import compose_runtime
from ooptdd_mutation import ooptdd_checks

runtime = compose_runtime(
    project={"extensions": ["mutation"]},
    environment={},
    extension_providers={"mutation": ooptdd_checks},
).activate_extensions()
```

## CLI

The extension installs one executable:

```bash
ooptdd-mutation mutate gate.yaml --events baseline.json --min-score 0.8 --json
ooptdd-mutation audit-rank gate.yaml --events baseline.json --report report.json
```

The commands transform only the supplied local event data. They neither connect to a
live backend nor alter production events. Exit `0` means the requested operation passed
or produced an explicitly ungraded report, exit `1` is a measured threshold failure,
and exit `2` means invalid or inconclusive evidence. Do not collapse exit `2` into a
green CI result. See the full [operator guide](../../docs/ci_mutation_gate.md).
