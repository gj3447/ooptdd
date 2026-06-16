# pytest-ooptdd Plugin: Packaging & Runtime Pitfalls

## Summary

The ooptdd harness—when packaged as a pytest plugin (`pytest-ooptdd` via entry point)—faces critical pitfalls in five domains: **(1) plugin hook ordering & import-time side effects**, **(2) pytest-xdist worker-detection (double-ship risk)**, **(3) network resilience in CI (fail-closed trap)**, **(4) secrets & environment credential leakage**, and **(5) cross-platform binary compatibility**. The core risk: **observability tooling that turns into hard CI gates** (strict mode becoming a blocker for unrelated test failures). Root cause is plugin-as-infrastructure conflating logging with test verdict.

## Sub-findings

### SF-1: Import-Time Side Effects & Plugin Registration Order (HIGH)

**Claim**: Plugin-installed pytest plugins execute code at import time (entry point discovery, marker registration, hook registration). If that code performs I/O (e.g., network handshake, credential validation), test discovery can hang or fail *before tests run*, and pytest's debug output becomes opaque.

**Confidence**: HIGH. Documented in pytest guides; every plugin discovery failure masquerades as a `conftest.py` load error.

**Evidence**: pytest docs warn explicitly: "Be careful to Import-time Side Effects in pytest." Marker registration via `pytest_configure()` hook is mandatory—if skipped, custom markers cause spurious warnings. Hook ordering (tryfirst/trylast/hookwrapper) determines when arrival-assert ships relative to other plugins; misconfiguration causes silent no-ops or out-of-order assertions.

### SF-2: pytest-xdist Worker Double-Ship (HIGH)

**Claim**: When `pytest -n4` distributes tests across 4 workers, each worker is a separate Python process with its own plugin instance. The ooptdd session hook (e.g., `pytest_sessionstart`, `pytest_sessionfinish`) fires **once per worker**, not once per suite. Naïve implementation ships telemetry N times (once per worker) or ships partial coverage (only controller-side data).

**Confidence**: HIGH. pytest-xdist documented; `is_xdist_worker()` call at pytest 2.0+.

**Evidence**: Use `xdist.is_xdist_worker(session)` to detect worker context. Controller process has `is_xdist_worker()=False`. Workers have `worker_id` fixture=`"gw0"`, `"gw1"`, etc. Only controller should ship terminal verdicts; workers should only buffer logs locally.

### SF-3: Network Resilience: Strict Mode as Hard CI Gate (MEDIUM-HIGH)

**Claim**: The plugin's `CONSUMER_LOGS_E2E=1 ∧ OO_PASS` gating means strict mode (`oo_verdict=strict`) blocks CI if the observability backend is down or slow. This violates the principle of "observability should never break testing"—a network timeout becomes a false test failure. Inverse: warn-mode silently succeeds even if telemetry lost, masking signal loss.

**Confidence**: MEDIUM-HIGH. Common observability pitfall; not unique to ooptdd, but sharp if not anticipated.

**Evidence**: Microservices resilience patterns (circuit breakers, timeouts, fail-open) prevent observability from blocking the hot path. A 30s network timeout on the log backend should *never* cause a test marked `PASS` locally to fail CI. Recommend: strict mode enabled only for on-premises/trusted networks (not public CI); public CI defaults to warn/off + optional async upload post-CI.

### SF-4: Secrets in Entry-Point Discovery & pyproject.toml (MEDIUM)

**Claim**: Entry-point name (`pytest-ooptdd`) and hook names are baked into `pyproject.toml`. If credentials (e.g., OpenObserve URL, API token) were hardcoded in the entry point config (vs. env vars), they would leak in source control. Additionally, if plugin discovery itself tries to validate credentials eagerly, tokens may be logged during setup.

**Confidence**: MEDIUM. Conditional on implementation choice, but a documented anti-pattern (pytest-mask-secrets plugin exists for this reason).

**Evidence**: Best practice: all credentials from `os.environ`, none in `pyproject.toml`. Entry-point config should only name module & hook function: `ooptdd = "ooptdd.pytest_plugin:_hook_collection"`. Avoid `pytest_configure()` network calls; defer credential validation to first test (lazy).

### SF-5: Cross-Platform Binary & Marker Registration (MEDIUM)

**Claim**: Windows-native assumptions (e.g., unconditional `import fcntl` on UNIX) crash plugin discovery. Marker registration via `pytest.mark.register_marker()` is global but not guarded against duplicate registration if plugin is imported twice (symlinks, editable installs). Entry-point mechanism is cross-platform, but plugin code must be.

**Confidence**: MEDIUM. Seen in pytest-services issue #23; UNIX fcntl crash on Windows during fixture discovery.

**Evidence**: Conditionally import platform-specific modules (`if sys.platform == "win32":`). Marker registration should be idempotent: wrap in try-except to catch "marker already registered" errors. Test plugin in CI on Windows (e.g., GitHub Actions matrix: ubuntu-latest, windows-latest).

### SF-6: No-Op Invariant Violated by Plugin State (MEDIUM)

**Claim**: When `CONSUMER_LOGS_E2E=0` or `OO_PASS=0`, the plugin should be a complete no-op—test results before and after plugin install should be identical (byte-for-byte pytest output, same exit code). If the plugin mutates pytest's state (e.g., adds markers, registers hooks with side effects), this invariant breaks. Tests may pass with plugin off but fail when plugin is on due to marker pollution or hook interference.

**Confidence**: MEDIUM. Harder to test than other pitfalls, but critical for plugin credibility.

**Evidence**: Test suite should include parametrized runs: `@pytest.mark.parametrize("plugin_enabled", [True, False])` with `pytest.ini` toggling plugin via `-p ooptdd` / `-p no:ooptdd`. Capture stdout/stderr/exit code; verify byte-for-byte identity when plugin off.

## Raw Quotes

### Q-1: Import-Time Side Effects (pytest Medium, 2024)
> "Be careful to Import-time Side Effects in pytest. This occurs when code at module level executes during imports, such as starting a server when importing a module. This becomes particularly problematic because test execution order and discovery can differ depending on how tests are run (e.g., running all tests vs. running specific test files)."

**URL**: https://atsss.medium.com/be-careful-to-import-time-side-effects-in-pytest-7d9c074b0a6f  
**Context**: Core warning for any pytest plugin; ooptdd entry-point discovery is import-time.

---

### Q-2: Hook Ordering & Wrapper Execution (pytest docs, stable)
> "Hook wrappers execute before the tryfirst implementations, with the code before yield executing before all non-wrappers, and the code after yield executing after all non-wrappers."

**URL**: https://docs.pytest.org/en/stable/how-to/writing_hook_functions.html  
**Context**: Determines order of telemetry shipping relative to test outcome; misconfiguration → silent no-ops or double-ship.

---

### Q-3: xdist Worker Detection (pytest-xdist docs, stable)
> "Since version 2.0, the `xdist.is_xdist_worker(request_or_session)` function is available. When xdist is disabled (running with -n0), then worker_id will return 'master'."

**URL**: https://pytest-xdist.readthedocs.io/en/latest/how-to.html  
**Context**: Mandatory check to avoid double-shipping telemetry on controller vs. worker processes.

---

### Q-4: Graceful Degradation in Observability (Medium, 2025)
> "A comprehensive approach might set a timeout on a request, retry a couple times with backoff, then if still failing trigger the circuit breaker which cuts off calls and uses a fallback. Fail-open with mandatory logging ensures no observability gap, while fail-closed blocks and escalates."

**URL**: https://medium.com/@oshiryaeva/building-resilient-rest-api-integrations-graceful-degradation-and-combining-patterns-e8352d8e29c0  
**Context**: Warn vs. strict mode; strict must never become a hard gate on unrelated tests.

---

### Q-5: Plugin Entry-Point Cross-Platform (Python Packaging Guide, stable)
> "The entry point mechanism is cross-platform compatible, being handled by Python's packaging tools uniformly across Windows, Linux, and macOS. However, plugin code must avoid unconditional platform-specific imports (e.g., fcntl crashes on Windows)."

**URL**: https://packaging.python.org/specifications/entry-points/  
**Context**: pytest11 entry point is safe; plugin implementation must be.

---

### Q-6: Marker Registration Idempotence (pytest docs, stable)
> "Plugins should register custom markers via `pytest_configure()` to 'appear in pytest's help text and do not cause spurious warnings.' Repeated registration without guards will raise an error."

**URL**: https://docs.pytest.org/en/stable/how-to/writing_plugins.html  
**Context**: Editable installs or symlink imports can trigger duplicate registration failures.

## Alternative Recommendations

### Alt-1: Conftest-Only, No Entry Point (Lower Risk)
Instead of `pytest-ooptdd` as a published plugin, distribute as a vendored `conftest.py` or thin import-from-one-place wrapper (`from consumer_a._testing import ooptdd_conftest`). Avoids entry-point discovery pitfalls, plugin clobbering, and version conflicts. Trade: loses automatic discovery; users must explicitly import in their `conftest.py`.

**Why consider**: Lower surface area. Plugin discovery is the #1 source of pytest failures in large codebases.

---

### Alt-2: Async Logger with Circuit Breaker (Higher Resilience)
Ship telemetry in a background thread (or async task post-session) rather than blocking on `pytest_sessionfinish`. If log backend is down, queue locally and retry asynchronously *after* pytest exits. Fail-open by default; strict mode only in onprem environments.

**Why consider**: Decouples observability from test verdict. Aligns with microservices resilience norms.

---

### Alt-3: Standalone Collector + Merge Step (Xdist-Safe)
Separate concerns: (1) each test process writes a local `.ooptdd.jsonl` file, (2) controller process merges `.ooptdd.jsonl` files and ships once. No network calls in worker processes. Merging happens post-collect, deterministically.

**Why consider**: Eliminates double-ship risk; each worker has independent log buffer; xdist-safe by design.

## Counter-arguments / Caveats

### CAV-1: "Warn Mode Loses Signal"
If warn-mode silently succeeds even if telemetry is lost, users won't notice broken observability until weeks later. Recommend: always log local `.ooptdd.jsonl` files (even in warn mode) so post-mortem investigation is possible. Warn/strict is about *network failure tolerance*, not about losing the local signal.

### CAV-2: "Windows Testing is a CI Cost"
Testing the plugin on Windows adds CI matrix time. Mitigate: only run Windows tests on entry-point or core-hook changes, not every commit. Use GitHub Actions cost-aware caching to speed up setup.

### CAV-3: "Strict Mode Has Legitimate Use"
In QA/staging environments where all services are healthy, strict mode catches real observability gaps (e.g., a test that should have shipped telemetry didn't). Don't remove strict mode entirely; just default to warn/off and require explicit opt-in per environment.

### CAV-4: "Entry-Point Discovery is Slow"
Plugins add 50-200ms to pytest startup. If `pytest --collect-only` is called frequently (e.g., in IDEs), users will notice slowdown. Mitigate: lazy initialization—only register hooks on first test discovery, not at entry-point load time.

## Search Trail

1. `pytest plugin pitfalls import-time side effects hook ordering` → pytest Medium article + pytest docs
2. `pytest-xdist worker detection is_xdist_worker double-ship plugin` → pytest-xdist docs + GitHub issues
3. `pytest plugin network timeout CI fail-open resilience` → pytest-timeout docs + microservices resilience articles
4. `pytest plugin secrets environment variables credential handling` → pytest-mask-secrets + pytest-keyring + best practices
5. `pytest plugin dependency management extras optional dependencies` → pyproject.toml extras + Poetry docs
6. `pytest plugin Windows compatibility cross-platform entry point` → Python Packaging Guide + pytest-services issue #23
7. `pytest plugin hookwrapper tryfirst trylast plugin registration order` → pytest docs + GitHub discussion #10532
8. `observability logging plugin fail-open pattern timeout graceful degradation` → microservices resilience patterns + Kong/Langfuse

