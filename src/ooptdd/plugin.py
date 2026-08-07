"""pytest plugin — ship every test outcome and assert it arrived.

This adapter is opt-in. Load it explicitly with ``-p ooptdd.plugin`` or declare
``pytest_plugins = ("ooptdd.plugin",)`` in the consuming suite. Behaviour:

* **Off is truly off.** When disabled the hooks return immediately — byte-for-byte
  identical run (a property the test suite checks).
* **xdist-safe.** Per-test reports are collected via ``pytest_runtest_logreport``,
  which fires on the controller for every forwarded report (and in-process when
  serial) — so the controller, the only node that ships + verifies, has the full
  set with or without ``-n``. A ``-n 8`` run ships once, not eight times — and,
  crucially, not *zero* times (collecting in ``pytest_runtest_makereport`` would
  only ever see the worker each test ran on, never the controller).
* **Fail-open.** A down backend never hangs or breaks the suite — verification
  defaults to ``warn``; only opt-in ``strict`` can fail the build, and only on a
  *real* miss (never on an unreachable store).

Framework config lives in ``[tool.ooptdd]``. Adapter-only policy lives in
``[tool.ooptdd.adapters.pytest]`` or the corresponding environment and ini keys.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from types import MappingProxyType

import pytest

from .adapters.pytest import (
    PytestAdapterSettings,
    PytestEnvironmentKeys,
    build_session_start,
    session_finish,
)
from .backends import MemoryBackend, MemoryStore, memory_reset
from .bootstrap import Runtime, compose_runtime
from .config import SETTING_DEFINITIONS, load_pyproject, resolve_signing_settings
from .domain.settings import DEFAULT_CID_ENV, DEFAULT_ENV_KEYS, FrameworkSettings

_PYTEST_SETTING_HELP: Mapping[str, str] = MappingProxyType(
    {
        "verify": "build policy: off|warn|strict",
        "enabled": "adapter activation: auto|1|0",
        "cid_env": "environment variable holding the correlation id",
    }
)
_PYTEST_ENV_KEYS = PytestEnvironmentKeys()


class OOPTDDPytestHooks:
    """Explicit composition seam for consumers of the opt-in pytest adapter."""

    @pytest.hookspec(firstresult=True)
    def pytest_ooptdd_runtime(self, config, runtime: Runtime) -> Runtime | None:
        """Return a replacement immutable runtime before extensions are activated.

        Implementations may inject backend registries or extension providers without
        mutating process-global framework state. Returning ``None`` keeps the supplied
        runtime.
        """


def pytest_addhooks(pluginmanager):
    """Register the adapter-owned runtime composition hook."""

    pluginmanager.add_hookspecs(OOPTDDPytestHooks)


def _ini_name(field: str) -> str:
    """Map generic setting names onto this adapter's configuration namespace."""

    return f"ooptdd_{field}"


@pytest.fixture
def ooptdd_memory_reset():
    """Yield an isolated in-memory backend and clear only its explicitly owned store."""

    store = MemoryStore()
    backend = MemoryBackend(store=store)
    yield backend
    memory_reset(store)


@pytest.fixture
def ooptdd_cid(monkeypatch, ooptdd_memory_reset, request):
    """A unique id exported through the runtime's resolved ``cid_env`` setting."""
    cid = f"test-{uuid.uuid4().hex[:12]}"
    adapter = getattr(request.config, "_ooptdd_adapter_settings", PytestAdapterSettings())
    cid_env = adapter.cid_env
    monkeypatch.setenv(cid_env, cid)
    return cid


def pytest_addoption(parser):
    group = parser.getgroup("ooptdd", "logs-as-spec test verification")
    group.addoption(
        "--ooptdd", action="store_true", default=False, help="force-enable ooptdd for this run"
    )
    group.addoption(
        "--no-ooptdd", action="store_true", default=False, help="force-disable ooptdd for this run"
    )
    for definition in SETTING_DEFINITIONS:
        parser.addini(_ini_name(definition.field), help=definition.help, default=None)
    for field, help_text in _PYTEST_SETTING_HELP.items():
        parser.addini(_ini_name(field), help=help_text, default=None)


def _resolve_adapter_settings(project, environment, overrides) -> PytestAdapterSettings:
    adapter_table = project.get("adapters", {}).get("pytest", {})
    if not isinstance(adapter_table, dict):
        raise TypeError("[tool.ooptdd.adapters.pytest] must be a table")
    values = {
        "verify": "warn",
        "enabled": "auto",
        "cid_env": DEFAULT_CID_ENV,
        **adapter_table,
    }
    for field in _PYTEST_SETTING_HELP:
        env_name = getattr(_PYTEST_ENV_KEYS, field)
        if env_name in environment:
            values[field] = environment[env_name]
    values.update(overrides)
    unknown = sorted(set(values) - set(_PYTEST_SETTING_HELP))
    if unknown:
        raise ValueError(f"unknown pytest adapter setting(s): {unknown}")
    return PytestAdapterSettings(**values)


def _runtime_from_config(config) -> Runtime:
    framework_overrides = {}
    for definition in SETTING_DEFINITIONS:
        value = config.getini(_ini_name(definition.field))
        if value not in (None, ""):
            framework_overrides[definition.field] = value
    adapter_overrides = {}
    for field in _PYTEST_SETTING_HELP:
        value = config.getini(_ini_name(field))
        if value not in (None, ""):
            adapter_overrides[field] = value
    if config.getoption("--ooptdd"):
        adapter_overrides["enabled"] = "1"
    if config.getoption("--no-ooptdd"):
        adapter_overrides["enabled"] = "0"
    project_path = (
        config.rootpath / "pyproject.toml"
        if getattr(config, "rootpath", None) is not None
        else "pyproject.toml"
    )
    project = load_pyproject(str(project_path))
    runtime = compose_runtime(project=project, overrides=framework_overrides)
    replacement = config.hook.pytest_ooptdd_runtime(config=config, runtime=runtime)
    if replacement is not None:
        if not isinstance(replacement, Runtime):
            raise TypeError("pytest_ooptdd_runtime must return Runtime or None")
        runtime = replacement
    runtime = runtime.activate_extensions()
    config._ooptdd_adapter_settings = _resolve_adapter_settings(
        project, runtime.environment, adapter_overrides
    )
    return runtime


def _settings_from_config(config) -> FrameworkSettings:
    """Compatibility view retained for consumers that exercised this helper."""

    return _runtime_from_config(config).settings


def pytest_configure(config):
    runtime = _runtime_from_config(config)
    s = runtime.settings
    adapter = config._ooptdd_adapter_settings
    config._ooptdd_runtime = runtime
    config._ooptdd_settings = s
    config._ooptdd_reports = []
    config._ooptdd_active = adapter.is_enabled(s.backend)
    config._ooptdd_cid = (
        runtime.environment.get(adapter.cid_env) or f"pytest-{uuid.uuid4().hex[:12]}"
    )
    config._ooptdd_backend = None
    if config._ooptdd_active:
        # Register the report collector only when active so "off is truly off" (no hook
        # registered at all when disabled). It collects via pytest_runtest_logreport — see
        # _ReportCollector for why that, not pytest_runtest_makereport, is what makes ooptdd
        # actually run under xdist.
        config.pluginmanager.register(_ReportCollector(config), "_ooptdd_report_collector")


class _ReportCollector:
    """Gather each test's report on whichever node *aggregates* results.

    The hook is ``pytest_runtest_logreport`` on purpose, not ``pytest_runtest_makereport``.
    makereport only ever fires where a test *executes* — under ``pytest-xdist`` that is the
    worker, never the controller — so the controller (the only node that ships + verifies,
    see :func:`pytest_sessionfinish`) collected nothing and a ``-n`` run silently shipped and
    verified *nothing*: a false green, the exact failure ooptdd exists to catch. logreport
    fires on the controller for every forwarded report (and once in-process when serial), so
    the report set is identical with or without ``-n``.
    """

    def __init__(self, config):
        self._config = config

    def pytest_runtest_logreport(self, report):
        if report.when not in ("setup", "call", "teardown"):
            return
        self._config._ooptdd_reports.append(
            {
                "nodeid": report.nodeid,
                "outcome": report.outcome,  # passed | failed | skipped
                "when": report.when,
                "duration": getattr(report, "duration", 0.0),
                "longrepr": str(report.longrepr) if report.failed else None,
            }
        )


def _is_xdist_controller(config) -> bool:
    return not hasattr(config, "workerinput")


def _backend_from_config(config):
    """Construct at most one backend for a pytest controller session."""

    backend = getattr(config, "_ooptdd_backend", None)
    if backend is None:
        backend = config._ooptdd_runtime.backend()
        config._ooptdd_backend = backend
    return backend


def _resolve_require_signature(env_value: str | None, signing_key: str | None) -> bool:
    """Enforce-if-keyed — close the "keyed verifier still greenlights an unsigned receipt"
    footgun. ``OOPTDD_SIGNING_KEY`` and ``OOPTDD_REQUIRE_SIGNATURE`` used to be independent, so
    a verifier that *had* a key but never set the require flag still accepted UNSIGNED receipts
    from any producer. Now, when the operator makes no explicit choice, a signature is required
    exactly when a signing key is configured — setting a key is itself the intent to reject
    unsigned receipts. An explicit ``OOPTDD_REQUIRE_SIGNATURE`` always wins either direction:
    ``0``/``false``/``no``/``off`` opts OUT even with a key; any other value opts IN even
    without a local key (verifier side). Keyless + no explicit choice stays lenient, so
    zero-config (the demo, this suite) is unbroken."""
    environment = {}
    if env_value is not None:
        environment[DEFAULT_ENV_KEYS.require_signature] = env_value
    if signing_key is not None:
        environment[DEFAULT_ENV_KEYS.signing_key] = signing_key
    return resolve_signing_settings(environment).require_signature


def pytest_collection_finish(session):
    """Ship a `session_start` heartbeat once collection is known (controller only).

    Best-effort: if the backend init/ship fails (e.g. unprovisioned store), swallow it —
    a heartbeat must never break collection. Its only job is to let verify distinguish
    'started but summary lost' from 'nothing arrived' if the summary is later dropped.
    """
    config = session.config
    if not getattr(config, "_ooptdd_active", False) or not _is_xdist_controller(config):
        return
    s: FrameworkSettings = config._ooptdd_settings
    try:
        backend = _backend_from_config(config)
        backend.ship(
            [
                build_session_start(
                    config._ooptdd_cid, service=s.service, expected_total=len(session.items)
                )
            ]
        )
    except Exception as exc:  # noqa: BLE001 — heartbeat is best-effort, never gates collection
        # Surface the swallow (it changes what a later 'summary lost' diagnosis can conclude) —
        # but never re-raise into collection.
        _emit(config, [f"ooptdd session_start heartbeat not shipped: {type(exc).__name__}: {exc}"])


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if not getattr(config, "_ooptdd_active", False):
        return
    if not _is_xdist_controller(config):
        return  # workers don't ship; the controller has all forwarded reports
    reports = getattr(config, "_ooptdd_reports", [])
    if not reports:
        return
    runtime: Runtime = config._ooptdd_runtime
    s = runtime.settings
    adapter = config._ooptdd_adapter_settings
    try:
        backend = _backend_from_config(config)
    except Exception as exc:
        _emit(config, [f"backend init failed ({exc}); skipping (build unaffected)"])
        return
    # signing key is CI-only: read from env, never config/code. Absent -> unsigned no-op.
    signing_key = runtime.signing.key
    result = session_finish(
        backend,
        reports,
        config._ooptdd_cid,
        service=s.service,
        mode=adapter.verify,
        polling=s.polling,
        signing_key=signing_key,
        # enforce-if-keyed: a configured key makes unsigned receipts a failure by default,
        # unless OOPTDD_REQUIRE_SIGNATURE explicitly opts out (and keyless stays lenient).
        require_signature=runtime.signing.require_signature,
    )
    _emit(config, result["messages"])
    if result["fail_build"] and exitstatus == 0:
        session.exitstatus = 1


def _emit(config, messages):
    tr = config.pluginmanager.get_plugin("terminalreporter")
    for m in messages:
        if tr is not None:
            tr.write_line(f"[ooptdd] {m}")
        else:  # pragma: no cover
            print(f"[ooptdd] {m}")
