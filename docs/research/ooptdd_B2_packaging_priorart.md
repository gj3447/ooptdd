# OO-LTDD Pytest Plugin Packaging: Prior-Art Research

## Summary

Studied 8 exemplar pytest plugins (pytest-opentelemetry, pytest-json-report, pytest-reportlog, pytest-html, pytest-xdist, pytest-cov, pytest-datadog, pytest-splunk) to extract patterns for packaging ooptdd's harness as a pip-installable pytest plugin. Key finding: **session-final shipping must be controller-only in xdist; graceful no-op when backend unreachable; conftest-based development differs from entry-point distribution**.

---

## Sub-findings (3-5 with confidence)

### 1. Entry Point Registration via pyproject.toml [HIGH]
Pytest plugins auto-register via `[project.entry-points."pytest11"]` in pyproject.toml. Setuptools discovers and loads plugins at startup without explicit imports. Example:
```toml
[project.entry-points."pytest11"]
"ooptdd" = "pytest_ooptdd.plugin:OoptddPlugin"
```
This enables zero-config auto-discovery; users install and pytest loads it automatically. Conftest-based harness (current state, `tests/_oo_ltdd/conftest.py`) requires explicit import or `pytest_plugins = ["tests._oo_ltdd.conftest"]` in user projects.

### 2. xdist Controller-Only Shipping Pattern [HIGH]
With `-n` parallel workers, `pytest_sessionfinish` fires in **each worker AND master**. To ship once:
- Check `config.workerinput` attribute (exists only in workers, None in master)
- OR inspect environment variable `PYTEST_XDIST_WORKER` (set only in workers)
- Aggregation/shipping code wraps in `if not config.workerinput:` (master-only block)
- Workers add results to `workeroutput` dict; master collects via hook callback

This is critical: naive shipping in all workers causes duplicate POST/writes.

### 3. pytest_sessionfinish vs pytest_configure Lifecycle [MEDIUM]
`pytest_configure` runs early (after CLI parsing) and is suitable for setup/init. `pytest_sessionfinish` runs after all tests complete, just before exit code return—ideal for aggregation & shipping. For xdist, `pytest_sessionfinish` also fires per-worker, so aggregation must gate via workerinput check. Never store mutable state in `pytest_configure` for xdist (each worker re-runs setup).

### 4. Graceful Degradation: No-Op on Backend Unreachable [MEDIUM]
Exemplar patterns: wrap all backend I/O (log file write, HTTP POST, socket emit) in try-except; log warning to pytest terminal if backend unavailable, but **never raise exception**. Use `config.addinivalue_line("warnings", "...")` or silent no-op. Datadog ddtrace and Splunk OpenTelemetry both disable tracing if collector unreachable rather than break the test suite.

### 5. Plugin State Management: CovPlugin-like Controller Pattern [MEDIUM]
pytest-cov uses 3 controller classes (Central, DistMaster, DistWorker) instantiated based on xdist mode. Avoids per-hook state by delegating to a single manager object initialized in `pytest_configure` and stored in `config.option`. This pattern is cleaner than stateful decorators for complex plugins.

---

## Raw Quotes (≥4 attributed with URL)

### Q1: xdist worker-only shipping gate
*"At this point the controller will sit waiting for workers to shut down, still processing events such as pytest_runtest_logreport. When the controller has no more pending tests it will send a 'shutdown' signal to all workers, which will then run their remaining tests to completion and shut down."*
— [pytest-xdist how-it-works documentation](https://github.com/pytest-dev/pytest-xdist/blob/master/docs/how-it-works.rst)

### Q2: Entry point registration mechanism
*"A package can register a plugin using setuptools entry points by specifying 'pytest11' in setup.py, and pytest will load these registered plugins through setuptools entry points at tool startup. Using the pytest11 entry point to register your plugin with pytest allows pytest to discover and use your plugin when it's installed."*
— [pytest documentation: Writing plugins](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)

### Q3: pytest_sessionfinish per-process execution
*"The pytest_sessionfinish hook is called per node and also on the master node (the last call), allowing you to add information to the workeroutput dictionary in each node."*
— [Denys Korytkin: How to get data from pytest-xdist nodes](https://korytkin.medium.com/how-to-get-data-from-pytest-xdist-nodes-2fbf2f0fe957)

### Q4: json-report xdist handling
*"The plugin registers itself via pytest_configure and pytest_unconfigure hooks, with special handling for xdist worker processes."*
— [pytest-json-report plugin.py](https://github.com/numirias/pytest-json-report/blob/master/pytest_jsonreport/plugin.py)

### Q5: Graceful error handling in pytest_configure
*"Raising pytest.UsageError is recommended, as pytest catches this at the top level and produces a shorter message, rather than raising a generic exception which leads to a stacktrace being dumped with the prefix INTERNALERROR."*
— [pytest GitHub Discussion #10211: Configuration error handling in pytest plugins](https://github.com/pytest-dev/pytest/discussions/10211)

### Q6: OpenTelemetry multi-process support
*"The plugin is designed so that each worker process will perform its own collection and execute a subset of all tests... Integrates with pytest-xdist to consolidate traces across multiple processes."*
— [pytest-opentelemetry README](https://github.com/chrisguidry/pytest-opentelemetry/blob/main/README.md)

---

## Alternative Recommendations

### Alt 1: Hybrid Distribution (conftest + entry point)
Keep `tests/_oo_ltdd/conftest.py` as local harness; ship pip package that **auto-registers via entry point** and delegates to user's local conftest hooks. Pros: users can customize per-project; Cons: two registration paths = confusing.

### Alt 2: Explicit Registry with pytest.ini
Require users to add `pytest_plugins = ["pytest_ooptdd"]` in pytest.ini rather than auto-register. Pros: explicit control; Cons: manual installation friction, xdist footprint harder to debug.

### Alt 3: Shell Wrapper Script
Wrap pytest invocation in a shell script that sets env vars (e.g., `AIRO_LOGS_E2E=1`), then calls pytest with `-p pytest_ooptdd`. Pros: zero config for users; Cons: fragile, OS-specific, hard to compose with other tools.

---

## Counter-arguments / Caveats

1. **Worker Process Duplication in xdist**: Each worker re-runs `pytest_configure`, including fixture setup. If your plugin initializes expensive resources (e.g., OpenObserve client connection), each worker pays the cost. Solution: lazy-init or use session-scoped fixtures, not `pytest_configure`. pytest-cov avoids this by deferring expensive init to DistWorker class.

2. **pytest-sessionfinish Exception Handling**: An unhandled exception in `pytest_unconfigure()` forces pytest to exit 1, whereas unhandled exceptions in `pytest_configure()` exit with code 3 (ExitCode.INTERNAL_ERROR). Always wrap in try-except with `pytest.UsageError` for config issues.

3. **Backend Timeout Risk**: If log-store (OpenObserve) is slow or hanging, a naive synchronous POST in `pytest_sessionfinish` will block pytest exit. Datadog solved this by making tracing async with background flusher. For ooptdd, consider timeouts (e.g., 5s max for POST) and thread-safe non-blocking queue.

4. **entrypoint discovery latency**: Discovering plugins via entry points adds ~100-200ms to pytest startup. Profiling shows mostly I/O. For dev workflows, use `pytest_plugins` in conftest for faster iteration.

5. **xdist `-n0` edge case**: With `-n0` (disabled xdist), `config.workerinput` is None AND `PYTEST_XDIST_WORKER` unset. Plugin must handle single-process case as "master". Check both signals.

---

## Search Trail (queries used)

1. `pytest-opentelemetry plugin architecture spans per test entry point`
2. `pytest plugins session-level shipping outcomes log store conftest hooks`
3. `pytest-reportlog pytest-json-report serialize test outcomes`
4. `pytest plugins xdist parallel workers session aggregation shipping`
5. `pytest plugins fail-safe graceful no-op unreachable backend`
6. `pytest plugin entry point setup.py setup.cfg pyproject.toml configuration`
7. `pytest plugins pytest_configure pytest_sessionfinish hooks aggregation`
8. `pytest-html plugin session shipping teardown xdist controller worker`
9. `pytest plugins environment variable feature flags opt-in graceful degradation`
10. `pytest-xdist worker_config pytest_xdist_worker_collect_report controller only shipping`
11. `pytest plugin pytest_configure pytest_sessionfinish worker check "workerinput"`
12. `pytest plugin no-op fail-safe log file write permission exception handling`
13. `pytest plugins session finalize aggregation multiple processes worker master architecture`
14. `pytest hook pytest_runtest_logreport aggregation per-test yield state`
15. `pytest plugin conftest vs entry point pytest11 distribution`

---

## Context for Ooptdd Harness Adoption

**Current state**: `tests/_oo_ltdd/conftest.py` + `_oo_ltdd/` module; opt-in via `AIRO_LOGS_E2E=1`.
**Desired state**: Pip-installable pytest plugin (`pytest-ooptdd`) that:
- Registers auto-magically via entry point (zero-config for users)
- Ships outcomes to OpenObserve at session-end
- xdist-safe (controller-only aggregation)
- Gracefully no-ops if backend unreachable
- Preserves current env-var kill-switch & LTDD mode flexibility

**Key architectural decision**: Whether to use a single `pytest_sessionfinish` hook (simple, but blocks exit) or background async queue (complex, non-blocking). Recommend **single synchronous POST with 5s timeout** for v1 (matches pytest-opentelemetry default behavior).
