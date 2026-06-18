# ooptdd

**Logs and traces as the test specification — and the ground truth.**

> `ooptdd` = *oo positive-TDD* (also written **LTDD**, log-based TDD). A pytest
> plugin and a methodology for testing what your system **actually emitted**,
> read back from an external store, instead of trusting a return value — or an
> AI agent's "done!".

```
pip install ooptdd        # auto-registers as a pytest plugin, zero config
```

---

## The problem in one screen

A function returns `{"status": "ok"}`. The logs say `shipped OK`. CI is green.
But the events never landed in your store — a silent `401` dropped ingest and
nobody noticed for 22 hours. A return-value test is **green and blind**.

ooptdd refuses to believe the self-report. It reads the store back and
*positively asserts* the events arrived:

```python
from ooptdd.backends import MemoryBackend
from ooptdd.gate import evaluate, load_gate
from app import process_order            # your code

def test_order_is_actually_processed():
    backend = MemoryBackend()            # swap for OpenObserve/OTLP in prod
    result = process_order(backend, cid="order-42", items=3)

    assert result["status"] == "ok"      # the self-report (could be a lie)

    gate = evaluate(backend, load_gate("gates/order_pipeline.yaml"))
    assert gate["ok"]                    # the truth: the events arrived
```

Flip the backend to "drops everything" and the self-report still says `ok` — but
the gate goes **RED**. That is the bug a normal test can't see. Run the live
demo:

```
pytest examples/test_order_pipeline.py -s
```

## Why it's different

The cycle is TDD, re-pointed at observability:

| phase | ooptdd |
|---|---|
| **Red** | write the expected event-trace spec (a YAML gate). It fails — nothing emits it yet. |
| **Green** | the code emits structured events; a verifier **polls the store and asserts they arrived**. |
| **Refactor** | the same event contract still holds — *golden-trace regression*. |

**"Positive"** is the load-bearing word: `ship()` returning without an exception
is a *claim*, not proof. A separate verifier reads the store back. The verdict is
three-valued on purpose (LTL3):

- `present` — the trace was observed (✅)
- `absent` — the store answered, but the record never came (⊥, **silent loss**)
- `inconclusive` — we couldn't reach the store at all (?, *never* fails the build)

That last distinction is why ooptdd doesn't turn a network blip into a flaky
test.

## Where it sits

|  | self-report trusted | outcome verified |
|---|---|---|
| **static / design-time** | type checks, schemas | contract testing (Pact) |
| **dynamic / runtime** | plain unit asserts, `caplog` | **ooptdd** |

It's runtime verification (LTL3 / Dwyer property patterns) wearing TDD clothes,
with a practical async-ingest model on top. Closest neighbours —
`pytest-opentelemetry` (exports spans, trusts the backend), Tracetest (UI-first,
post-hoc), Langfuse-style evals (post-hoc agent traces). None combine
**spec-first Red + arrival polling + silent-loss detection + generator≠verifier +
pytest-native + gate-as-YAML**. That cell is ooptdd's.

## Backends (portability)

Write is portable (OTLP); **query is not** — LogQL/TraceQL/SQL/ES-DSL all differ,
so backends declare what they support honestly.

| backend | ship | query | status |
|---|---|---|---|
| `memory` | ✅ | ✅ | first-class — default, zero infra, used by the demo & this repo's own tests |
| `openobserve` | ✅ | ✅ (SQL) | first-class — reference network driver, env-only secrets |
| `otel` | ✅ (OTLP) | — | write-only; pair with a store-specific reader |
| `loki` / `elastic` / … | — | — | community drivers via the `ooptdd.backends` entry point |

Configure in `pyproject.toml` (secrets stay in the environment, never here):

```toml
[tool.ooptdd]
backend = "openobserve"
service = "myapp.tests"
verify  = "warn"          # off | warn | strict
```

```bash
# the openobserve backend reads these from the env only:
export OOPTDD_OO_URL=http://your-host:5080
export OOPTDD_OO_PASSWORD=…
```

## Plugin + CLI

Once installed, every `pytest` run ships its outcomes and asserts arrival
(`warn` by default — observation never overrides your verdict; opt into `strict`
to fail CI on a real silent loss). It is **xdist-safe** (ships once from the
controller) and a **true no-op when disabled** (`--no-ooptdd`).

```bash
ooptdd verify <cid> --backend openobserve   # manual re-check, exit 0/1/2
ooptdd gate gates/order_pipeline.yaml        # evaluate a gate spec
```

## Extending: custom check-predicates & ontology presets

Two registration seams let you grow the vocabulary **without editing the core**
(a string-keyed single-dispatch registry — the pluggy/hypothesis pattern):

```python
from ooptdd import check                     # the gate check-predicate seam

@check("spike")                              # a new gate keyword, registered from your conftest
def _spike(events, rule, ctx):
    n = sum(1 for e in events if e.get("event") == rule["spike"])
    return {"spike": rule["spike"], "got": n, "passed": ctx.reachable and n >= 1}
# now `expect: [{spike: boom}]` dispatches to your handler — evaluate() is untouched
```

```python
from ooptdd import Ontology                  # the preset-ontology seam (dependency-inverted)
Ontology.register_preset("my_vocab", my_ontology_factory)   # in your module
Ontology.builtin("my_vocab")                 # resolves it; built-ins (e.g. "gen_ai") self-register on `import ooptdd`
```

A duplicate predicate key raises at registration (loud, not silent). Presets require
importing the `ooptdd` package (which wires the shipped built-ins), not just a submodule.

## Status & honesty

`0.1.0`, extracted from internal harnesses (a service monorepo, a research
harness, and a PyQt field application) where the core has run in anger. No long-horizon (6-month+) operational data
yet. Hard **log-free zones** — do *not* use ooptdd for: precise numeric
regression (use snapshots/metrology), security redaction, or µs-scale concurrency
races. See [`METHODOLOGY.md`](METHODOLOGY.md) for the full theory, the 7
principles, the 6 pitfalls, and [`docs/research/`](docs/research/) for the
prior-art / competition / design study behind this repo.

## Related projects

- [`ooptdd-loop`](https://github.com/airobotics-ailab/ooptdd-loop) — the
  **application layer** built on this library: an agent-driven, positive-TDD
  *requirements loop* (declare requirements as trace gates + a Longinus binding,
  run until the events actually arrive **and** the binding points at real
  emitting source). It also carries the **KG-native I/O** (coverage & Longinus
  drift as graph queries) and an **MCP server** for driving the loop as agent
  tools. The dependency is one-way (`ooptdd-loop` → `ooptdd`); this library stays
  unaware of it and is what downstream consumers vendor.

## License

Apache-2.0.
