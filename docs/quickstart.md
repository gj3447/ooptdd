# Quickstart

Five minutes, no infrastructure.

## 1. Install

ooptdd is not on PyPI yet — install it from a sibling checkout (editable), as a
path dependency, or vendor a copy:

```bash
uv pip install -e path/to/ooptdd      # editable; auto-registers as a pytest plugin
# pyproject:  [tool.uv.sources]  ooptdd = { path = "path/to/ooptdd", editable = true }
# vendor:     python path/to/ooptdd/scripts/vendor_ooptdd.py <your-repo>
# (once published to PyPI:  pip install ooptdd)
```

It auto-registers as a pytest plugin. With the default `memory` backend there is
nothing to run and nothing to configure.

## 2. See the idea

```bash
git clone https://github.com/airobotics-ailab/ooptdd
cd ooptdd
pip install -e .
pytest examples/test_order_pipeline.py -s
```

`examples/app.py` is a toy pipeline that emits an event per step.
`examples/test_order_pipeline.py` shows the whole point in three tests:

- healthy backend → the gate is **GREEN** (events arrived);
- a backend that silently drops everything → the function *still returns `ok`*,
  but the gate goes **RED** — the silent loss a return-value test can't see;
- `verify_trace` returning `present` vs `absent` directly.

## 3. Use it on your code

Write a gate (the **Red** artifact) describing what you expect to observe:

```yaml
# gates/my_flow.yaml
cid_env: OOPTDD_CID
expect:
  - event: order_received
    op: "=="
    count: 1
  - event: order_shipped
    op: "=="
    count: 1
```

Assert on it:

```python
from ooptdd import MemoryBackend, evaluate, load_gate

def test_flow():
    backend = MemoryBackend()
    run_my_flow(backend, cid="abc")             # your code emits events
    gate = evaluate(backend, load_gate("gates/my_flow.yaml"))
    assert gate["ok"], gate["checks"]
```

## 4. Point it at a real store

```toml
# pyproject.toml
[tool.ooptdd]
backend = "openobserve"
service = "myapp.tests"
verify  = "warn"          # raise to "strict" to fail CI on silent loss
```

```bash
export OOPTDD_OO_URL=http://your-host:5080      # secrets: env only
export OOPTDD_OO_PASSWORD=…
pytest                                           # every run now ships + verifies
ooptdd verify <cid> --backend openobserve        # manual re-check
```

Next: [`../METHODOLOGY.md`](../METHODOLOGY.md) for the why, and
[`research/`](research/) for the design study.
