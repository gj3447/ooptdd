# Contributing to ooptdd

Thanks for looking. ooptdd is small on purpose — the value is in the discipline,
not the line count — so contributions that keep the core tiny are the most
welcome.

## Dev setup

```bash
python -m pip install -e ".[dev]"
pytest -q                 # full suite, in-memory backend, no infra needed
pytest -q -n 2            # confirm the xdist ship-once invariant
ruff check src tests examples
```

## Ground rules

- **No secrets, no internal hosts in code.** URLs and credentials are read from
  the environment only — never baked into source, config tables, or tests.
- **Keep the `Backend` surface at two methods** (`ship`, `query`). Backend-specific
  cleverness goes in the driver; the verdict logic stays in `verify.py`.
- **Off must stay off.** The plugin disabled must produce a byte-identical run;
  there's a test for it, keep it green.
- **A new backend is a new package**, not a core dependency. Register it under the
  `ooptdd.backends` entry point and declare its capabilities honestly (which of
  ship/query it supports, and the count-by-cid limits).
- Match the surrounding style; `ruff` is the arbiter.

## Adding a backend

Implement two methods plus the two `default_*` polling hints:

```python
class MyBackend:
    default_lookback_s = 3600
    default_future_buffer_s = 300
    def ship(self, events: list[dict]) -> None: ...
    def query(self, cid, *, since_us, until_us) -> QueryResult: ...
```

Return `QueryResult(reachable=False)` when you cannot reach the store — that maps
to an `inconclusive` verdict (never a build failure), which is the whole point.

## Reporting

Use GitHub issues. For anything security-related, see `SECURITY.md`.
