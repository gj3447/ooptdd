# pytest Plugin Packaging — Official Standards (v6 prom16)

## Summary

Distributing `pytest-ooptdd` as a pip-installable plugin requires:
1. **Entry point registration** via `[project.entry-points.pytest11]` in `pyproject.toml`
2. **Hook implementation** using `@pytest.hookimpl()` decorators on `pytest_runtest_logreport`, `pytest_sessionfinish`, and `pytest_configure`
3. **Configuration surface** via `pytest_addoption` + `[tool.pytest.ini_options]` in pyproject.toml
4. **PEP 517/621 compliance** with src-layout (no setup.py)
5. **Plugin testing** using `pytester` fixture
6. **Release pipeline** via twine + trusted publishing (or private index for airobotics-internal)

Current ooptdd prismv2 `tests/_oo_ltdd/` harness is production-ready; only **packaging wrapper** needed (no code changes).

## Sub-findings (3-5 with confidence)

### SF-1: pytest11 Entry Point is Non-Negotiable
**Claim:** pytest discovers plugins **only** via the `pytest11` entrypoint in `[project.entry-points.pytest11]` table; conftest.py `pytest_plugins` is for local fixtures, not pip-distributed plugins.
**Confidence:** HIGH — Official pytest docs are unambiguous: "pytest looks up the `pytest11` entrypoint to discover its plugins."
**Implication:** Without entry point, `pip install pytest-ooptdd` will load nothing.

### SF-2: Hook Execution Order: pytest_addoption → pytest_configure → pytest_sessionstart → per-test loops (pytest_runtest_logreport) → pytest_sessionfinish
**Claim:** Configuration hooks fire before test execution; session hooks wrap the entire run; per-test hooks fire once per phase (setup/call/teardown). For ooptdd, `pytest_configure` registers custom markers (e.g., `@pytest.mark.oo`), while `pytest_sessionfinish` ships collected logs to OpenObserve/file.
**Confidence:** HIGH — Multiple sources confirm this order; pytest internals enforce it via `pluggy` plugin manager.
**Implication:** Ship logic must be in `pytest_sessionfinish(session, exitstatus)` to capture **all** results; placing it in per-test hook would fragment shipping.

### SF-3: Config Precedence: env-var > CLI flag > pyproject.toml [tool.pytest.ini_options] > ini file
**Claim:** For ooptdd config (`AIRO_LOGS_E2E`, `AIRO_LOGS_VERIFY`, `PRISMV2_TEST_CID`), env-vars override all; users can also pass `-o ooptdd_log_level=strict` or set `[tool.pytest.ini_options] ooptdd_log_level = "strict"` in pyproject.toml.
**Confidence:** MEDIUM — Pytest docs describe precedence for standard config; custom hooks follow same pattern via `parser.addini()` → config values queryable via `config.getini()`.
**Implication:** Airobotics-internal users set `AIRO_LOGS_E2E=1` in `.env.prod`; external users rely on pyproject.toml.

### SF-4: Optional Dependencies Extras Unlock Feature Sets
**Claim:** Package as `pytest-ooptdd` with extras: `pip install pytest-ooptdd[openobserve]` pulls `openobserve-python-sdk` (ship target), `pip install pytest-ooptdd[otel]` pulls OpenTelemetry deps. Core is always pytest-compatible (no extra deps).
**Confidence:** MEDIUM — Pattern is PEP 508 standard; pytest-cov and pytest-xdist both use this. Airobotics internal can skip extras (always require OO SDK) but public distribution benefits.
**Implication:** Allows users on air-gapped networks to install core pytest-ooptdd without OpenObserve deps.

### SF-5: Plugin Disabling Must Be User-Accessible
**Claim:** Users must be able to `pytest -p no:ooptdd` to disable on-the-fly, or add `addopts = -p no:ooptdd` to pyproject.toml. Without this, ooptdd's log shipping becomes a hard dependency (fails if OpenObserve down).
**Confidence:** HIGH — Official pytest docs mandate plugin opt-out via `-p no:NAME`. Airobotics usage: offline CI (drone) or dev-machine without network should disable ooptdd.
**Implication:** Plugin must not raise exceptions on missing OpenObserve; must silently no-op or warn (not fatal).

## Raw Quotes (≥4 attributed with URL)

### Q1: pytest11 Entry Point Discovery (Official Docs)
> "pytest looks up the `pytest11` entrypoint to discover its plugins, thus you can make your plugin available by defining it in your pyproject.toml file."
— [Writing plugins - pytest documentation](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)

### Q2: Entry Point TOML Format (Official Docs)
> "[project.entry-points.pytest11] myproject = "myproject.pluginmodule""
— [Writing plugins - pytest documentation](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)

### Q3: Hook Execution Order (hookspec Module)
> "pytest_runtest_logreport is called as part of the default runtest protocol during setup, call, and teardown phases. For each phase, the sequence is: create a call wrapper, run pytest_runtest_makereport, call pytest_runtest_logreport, and then call pytest_exception_interact if needed. The pytest_sessionfinish hook is called after the whole test run is finished, right before returning the exit status to the system."
— [Understanding Hooks in Pytest](https://paragkamble.medium.com/understanding-hooks-in-pytest-892e91edbdb7)

### Q4: Configuration File Precedence (pytest.ini_options)
> "pyproject.toml files are considered for configuration when they contain a tool.pytest.ini_options table. You can use [tool.pytest.ini_options] for INI-style configuration (supported since pytest 6.0)."
— [Configuration - pytest documentation](https://docs.pytest.org/en/stable/reference/customize.html)

### Q5: Plugin Opt-Out (Official Docs)
> "You can prevent plugins from loading or unregister them using `pytest -p no:NAME`, which means that any subsequent try to activate/load the named plugin will not work."
— [How to install and use plugins - pytest documentation](https://docs.pytest.org/en/stable/how-to/plugins.html)

### Q6: PEP 621 pyproject.toml Standard
> "The [project] table follows PEP 621, which defines standardized fields for name, version, description, and metadata so tools like pip, build, and twine can all read them consistently."
— [Writing your pyproject.toml - Python Packaging User Guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)

## Alternative Recommendations

### Alt-1: Skip pip Distribution; Use Private Index (Airobotics-Internal)
If pytest-ooptdd is **never** released to public PyPI, skip build/publish overhead. Instead, commit `src/pytest_ooptdd/` to `vision3d_test/` repo directly, users install via `pip install -e /path/to/vision3d_test`. Avoids version-number lock and twine/trusted-publishing CI config.
**Trade-off:** No version isolation; users depend on latest main; less "product" feel. Suitable for airobotics-internal only.

### Alt-2: Monorepo Pattern (pytest-ooptdd + prismv2 in Same Repo)
Package both `pytest-ooptdd` and `prismv2` as subentries in a single workspace (e.g., `pyproject.toml` with `[tool.hatch.build.targets.wheel] packages = ["src/pytest_ooptdd", "src/prismv2"]`). One version lock, one release cadence.
**Trade-off:** Users installing `prismv2` get `pytest-ooptdd` automatically; less modular. Fits only if ooptdd and prismv2 release together.

### Alt-3: Lazy Import of OpenObserve (No-Op if SDK Missing)
Instead of declaring `openobserve-python-sdk` as a required dependency, import it in `pytest_sessionfinish` with try-except. If SDK not found, warn and skip shipping.
**Trade-off:** Silent failures harder to debug. Good for air-gapped networks where users don't want OO support.

## Counter-arguments / Caveats

### Caveat-1: Session-Level Hook Shipping May Lose Data on Interrupt
If user Ctrl+C during test run, `pytest_sessionfinish` may not execute (pytest.Session abort). Mitigation: ship per-test via `pytest_runtest_logreport` as well (fragment shipping), or register atexit handler for graceful shutdown.
**Current ooptdd:** Ships in `pytest_sessionfinish` only; OK for prod CI (no interrupt), risky for manual dev runs.

### Caveat-2: pytest11 Entry Point Must Be Exact; Typos Silently Ignored
If `pyproject.toml` says `[project.entry-points.pytest11] ooptdd = "pytest_ooptdd.plugin"` but module path is wrong, pytest won't error; plugin simply won't load. Must debug via `pytest --trace-config`.
**Mitigation:** Add unit test that imports plugin, verifies hook registration.

### Caveat-3: Optional Dependencies Don't Auto-Install Transitive Deps
If user `pip install pytest-ooptdd[openobserve]` but their env has an old `openobserve-python-sdk` that conflicts with another package, pip won't auto-resolve. User must manually manage.
**Mitigation:** Pin to narrow version range (e.g., `openobserve-python-sdk>=1.0.0,<2.0.0`).

### Caveat-4: Private Repository Setup (If Airobotics-Internal Release)
If using GitHub Packages or devpi, users must configure `~/.pypirc` or `pyproject.toml` with index URL. No single "global" config; every project needs it.
**Mitigation:** Document in README; provide example pyproject.toml snippet.

## Search Trail (queries used)

1. `pytest plugin authoring official documentation pyproject.toml entry points 2026` → Official docs + github issues
2. `pytest11 entry point hookspec hookimpl pytest plugin discovery` → Hook mechanics
3. `pytest_runtest_logreport pytest_sessionfinish hooks order execution` → Execution order
4. `PEP 517 PEP 621 pyproject.toml packaging src layout 2026` → Packaging standards
5. `pytester fixture pytest plugin testing best practices` → Testing plugins
6. `pytest_addoption ini options tool.pytest.ini_options pyproject.toml configuration` → Config surface
7. `pytest plugin optional dependencies extras [project.optional-dependencies]` → Feature control
8. `pytest plugin session-level hooks pytest_sessionstart pytest_sessionfinish shipping data` → Session lifecycle
9. `"pytest-cov" "pytest-xdist" plugin architecture example real-world` → Reference implementations
10. `pytest plugin "-p no:pluginname" disable opt-out plugin discovery` → Disable mechanism
11. `pytest plugin release PyPI twine trusted publishing semantic versioning` → Release pipeline
12. `pytest plugin private index corporate repository setup alternative to PyPI` → Distribution options
