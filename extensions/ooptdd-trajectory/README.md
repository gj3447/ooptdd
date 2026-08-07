# ooptdd-trajectory

Optional deterministic trajectory predicates for `ooptdd`.

```bash
python -m pip install 'ooptdd>=0.6,<0.7' 'ooptdd-trajectory>=0.1,<0.2'
```

The package never registers predicates during import. Pass its provider explicitly:

```python
from ooptdd.bootstrap import compose_runtime
from ooptdd_trajectory import ooptdd_checks

runtime = compose_runtime(
    project={"extensions": ["trajectory"]},
    environment={},
    extension_providers={"trajectory": ooptdd_checks},
).activate_extensions()
```

The distribution intentionally installs no CLI and performs no global registration.
