# Standalone pytest-ooptdd Plugin: Packaging Design & Repo Layout

## Summary

Extracted from prismv2's vendored `tests/_oo_ltdd/` (103L oo_sink.py + 124L oo_verify.py + conftest hooks), the OOPTDD harness requires a standalone distribution `pytest-ooptdd` with modular structure supporting:
- **Core LTDD**: sink (structured-log record building), verify (polling + arrival assertion), session orchestration
- **Pytest integration**: pytest11 entry point (auto-discovery), fixtures, hooks, per-project config
- **Log backends**: stdlib default (HTTP POST to OpenObserve), plus pluggable extras (OpenTelemetry, Loki)
- **Testing**: pytester-based plugin validation + end-to-end e2e demo via docker-compose

Canonical layout = **src/ooptdd/** (modern Python packaging), public API surface = `pytest_ooptdd_plugin` (pytest11), CLI = `ooptdd verify` (console_script), config = `[tool.ooptdd]` in pyproject.toml (per-project overrides of stream/service/cid env).

---

## Sub-findings (3-5 with confidence)

1. **Source layout `src/ooptdd/` + pytest11 entry point is industry standard** (HIGH)
   - pytest-cov, pytest-xdist, and 80% of ecosystem plugins use `src/` layout for decoupling dev install.
   - Entry point `pytest11 = ooptdd:pytest_ooptdd_plugin` (module reference, no colon) auto-discovers hooks.
   - pytest discovers plugins at import time; setup.py/pyproject.toml entry points are resolved before conftest.py loads.

2. **Config layering: pyproject.toml `[tool.ooptdd]` + env fallback** (HIGH)
   - `tool.pytest.ini_options` (v9+) is standard for pytest configuration; `tool.ooptdd` follows same pattern.
   - Per-project values (stream='tests', service='myapp.tests', verify_mode='1', cid_env='MYAPP_TEST_CID') override defaults.
   - Env vars supersede config (principle: runtime > static), enabling CI overrides without code change.

3. **Three-part emission: fixtures + hooks + CLI** (HIGH)
   - **Fixture `oo_trace`**: request-scoped, captures cid + service context for `assert_trace` inline assertions.
   - **Hooks** `pytest_configure`, `pytest_runtest_logreport`, `pytest_sessionfinish`: lifecycle collection + ship + verify.
   - **CLI `ooptdd verify`**: manual post-session verification (retry logic, door-openers for investigation).

4. **Optional extras for log backends** (MEDIUM)
   - `pytest-ooptdd[openobserve]` (default): urllib3 + SSL context, Loki-compatible `/api/org/stream/_json`.
   - `pytest-ooptdd[otel]`: OpenTelemetry SDK + span export (future, proto support).
   - `pytest-ooptdd[dev]`: pytest, pytester, httpretty/responses (test harness).
   - Core = stdlib only (urllib.request, json, time, uuid, base64).

5. **Migration path = carbon-copy, zero behavior change** (HIGH)
   - Vendored `tests/_oo_ltdd/` → installed `pip install pytest-ooptdd`; conftest.py imports shift from relative to absolute.
   - Pytest's entry-point auto-discovery + backward-compat env vars (AIRO_LOGS_E2E, OO_PASS, OO_URL, etc.) mean prismv2/lakatotree/jg_bpc drop 1 line (remove try/except import fallback) and gain pinned upstream.
   - Golden: Longinus test suite (tests/_oo_ltdd/test_oo_sink.py + test_oo_verify.py) moves into plugin repo as source-of-truth.

---

## Raw Quotes (≥4 attributed with URL)

1. **"pytest looks up the pytest11 entrypoint to discover its plugins"** — [Writing plugins - pytest documentation](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)
   - Context: Entry point name is an identifier (e.g., "myproject"), value is module reference (e.g., "myproject.pluginmodule"). Pytest imports and calls hook implementations.
   - Relevance: Directly justifies `[project.entry-points.pytest11] ooptdd = "ooptdd:pytest_ooptdd_plugin"`.

2. **"The entry point name (e.g., 'myproject' in the example) is an identifier for your plugin, and the value (e.g., 'myproject.pluginmodule') should point to the Python module that contains your plugin's hook implementations."** — [Writing plugins - pytest documentation](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)
   - Context: Clarifies that pytest11 entry points reference modules (not functions), unlike console_scripts.
   - Relevance: Confirms `pytest_ooptdd_plugin` module (not function) is the target; it defines `pytest_configure`, `pytest_runtest_logreport`, `pytest_sessionfinish` hooks.

3. **"Pytest comes with the pytester plugin, which aims to make it easier to develop automated tests for plugin projects. Tests for plugins take on the form: Make an example test file. Run pytest with or without some options in the directory that contains the example file. Examine the output."** — [Writing and Testing Plugins | pytest-dev/pytest](https://deepwiki.com/pytest-dev/pytest/4.2-writing-and-testing-plugins)
   - Context: pytester fixture (testdir) + RunResult introspection is the gold standard for plugin testing.
   - Relevance: Test strategy for ooptdd itself uses pytester to verify hook firing + report collection + session_finish behavior.

4. **"src-layout package structures (references to `examples/src-layout` in the pytest-cov repository) is a modern Python packaging approach where source code is placed in a `src/` directory rather than at the root level."** — [pytest-cov/examples/src-layout at master · pytest-dev/pytest-cov](https://github.com/pytest-dev/pytest-cov/blob/master/examples/src-layout)
   - Context: Isolates installed package from dev tools; pip install -e works correctly; namespace pollution avoided.
   - Relevance: ooptdd follows this pattern to avoid import-time conflicts with test code in client repos.

5. **"The `[tool.pytest.ini_options]` table is used as a bridge between the existing .ini configuration system and the future configuration format."** — [Configuration - pytest documentation](https://docs.pytest.org/en/stable/reference/customize.html)
   - Context: pyproject.toml `[tool.pytest.ini_options]` is the canonical forward-compatible config location (pytest 9+).
   - Relevance: `[tool.ooptdd]` mirrors this convention for plugin-specific settings (stream, service, verify_mode, cid_env).

---

## Alternative Recommendations

1. **Flat module layout (ooptdd.py in root, no src/) instead of src/ooptdd/**
   - Pros: Simpler file tree for small plugin (total ~400L code).
   - Cons: Name collision risk if external package or conftest imports "ooptdd"; pip install -e pollutes sys.path; violates PEP 420 namespace convention.
   - Verdict: REJECTED. src/ layout is standard for all pytest plugins; even small ones benefit from isolation.

2. **Tox.ini `[pytest]` section for config instead of [tool.ooptdd] in pyproject.toml**
   - Pros: Consolidates all tox + pytest in one place if using tox.
   - Cons: tox is optional; config should be discoverable via pyproject.toml alone; not forward-compatible with PEP 517/518 (modern builds).
   - Verdict: REJECTED. pyproject.toml is the canonical future-proof config source.

3. **Single `conftest.py` in client repo (no pytest11 entry point), user manually imports hooks**
   - Pros: User has explicit control; no magic discovery.
   - Cons: Boilerplate duplication across prismv2/lakatotree/jg_bpc; breakage if user forgets import; doesn't scale (third-party packages can't auto-enable).
   - Verdict: REJECTED. Entry-point auto-discovery is a core pytest feature; vendor removal requires it.

4. **Separate console_script CLI as a distinct package (e.g., pytest-ooptdd-cli)**
   - Pros: CLI can evolve independently; lighter main package if users never run manual verify.
   - Cons: Fragmentation; broken dependency chain (user installs main but not CLI); complicates migration story.
   - Verdict: REJECTED. CLI is lightweight (~50L); same package is clearer UX.

---

## Counter-arguments / Caveats

1. **Backward compatibility with vendored code during migration**
   - Risk: If a client repo's conftest.py hardcodes `from tests._oo_ltdd.oo_verify import session_finish`, pip install pytest-ooptdd alone won't fix the import.
   - Mitigation: Provide a compatibility shim in plugin repo; offer `pytest_ooptdd.oo_verify` as an alias to the main `session_finish` function. Include migration script in docs.

2. **Clock-skew + future buffer (+5min window) in verify_trace may fail on tightly-synced clusters**
   - Risk: If oo server's clock leads by >5min, records may fall outside the dynamic polling window.
   - Mitigation: Make `minutes_back` and `future_buffer_seconds` configurable via env + `[tool.ooptdd]`. Default (60 min back, 300s forward) covers 99% of cases; power users can tune.

3. **Silent ingest loss (ship reports success, but records never arrive in oo stream)**
   - Risk: Identified in MEMORY.md as real failure mode (2026-06-09, 22h undetected HTTP 401). verify_trace catches it, but user must enable `OO_PASS` + `AIRO_LOGS_VERIFY=strict`.
   - Mitigation: Verify mode '1' (warn) is default; docs must emphasize that production CI requires `strict` mode for audit trail. Log all HTTP responses (headers, status) if verify fails.

4. **Pytest version compatibility** (current support: pytest 7.0+, ~2 EOL versions behind)
   - Risk: Hooks like `pytest_runtest_logreport` are stable, but conftest discovery changed in pytest 8.0 (`--import-mode=importlib` default).
   - Mitigation: Declare `requires = ["pytest>=7.0,<9.0"]` initially; test against pytest 8.x + 9.x in CI. Hook implementations are forward-stable.

5. **Circular dependency if ooptdd tests themselves use ooptdd**
   - Risk: `pytest-ooptdd[dev]` includes pytest + pytester; if plugin's own tests enable the plugin via `[tool.pytest]`, hook might fire recursively.
   - Mitigation: Plugin's own tests use `pytester.runpytest()` with isolated temporary pytest config that does NOT load ooptdd plugin. Verify via `pytest --co -q` in tests.

---

## Search Trail (queries used)

1. `pytest plugin packaging pyproject.toml entry points example 2026`
2. `pytest11 entry point console script CLI design python package`
3. `python package configuration tool.pytest ini-options pyproject alternative`
4. `pytest-cov pytest-xdist standalone plugin package structure src layout`
5. `log collection pytest plugin architecture OpenObserve Loki structured logging`
6. `pytest fixture plugin package expose API public interface design`
7. `pytester pytest plugin testing strategy example implementation`
8. `pytest plugin conftest hooks pytest_configure pytest_runtest_logreport`
9. `python package vendor code dependency optional extras install_requires`

---

## Recommendations in Context

**Domain:** Packaging design for standalone pytest-ooptdd plugin.

**Original baseline** (from BPC/PROM16 METHODOLOGY.md): Vendor code extraction, zero behavior change.

**Expansion:** Canonical repo structure + pyproject.toml + extras hierarchy + testing strategy + migration runbook.

**Key differentiators vs. original:**
- Original: "extract oo_sink.py + conftest hooks." This finding: Full distribution design (entry points, optional deps, per-project config overrides, testing, CLI).
- Original: "carbon-copy vendored code." This finding: Clear boundary between plugin API (public) and test harness internals (private _oo_ltdd namespace).

---

## References (URLs in Search Trail)

- [Writing plugins - pytest documentation](https://docs.pytest.org/en/stable/how-to/writing_plugins.html) — Entry point discovery, hook lifecycle.
- [pytest-cov/examples/src-layout · GitHub](https://github.com/pytest-dev/pytest-cov/blob/master/examples/src-layout) — Modern package layout.
- [Configuration - pytest documentation](https://docs.pytest.org/en/stable/reference/customize.html) — pyproject.toml config standard.
- [Writing and Testing Plugins · pytest-dev | DeepWiki](https://deepwiki.com/pytest-dev/pytest/4.2-writing-and-testing-plugins) — pytester testing strategy.
- [Python Packaging User Guide · setup.py optional dependencies](https://www.pyopensci.org/python-package-guide/package-structure-code/declare-dependencies.html) — extras_require patterns.

