"""pytest plugin — ship every test outcome and assert it arrived.

Registered via the ``pytest11`` entry point, so ``pip install ooptdd`` is enough;
no conftest wiring required. Behaviour:

* **Off is truly off.** When disabled the hooks return immediately — byte-for-byte
  identical run (a property the test suite checks).
* **xdist-safe.** Reports are collected wherever a test runs; shipping +
  verification happen only on the controller (``not config.workerinput``), so a
  ``-n 8`` run ships once, not eight times.
* **Fail-open.** A down backend never hangs or breaks the suite — verification
  defaults to ``warn``; only opt-in ``strict`` can fail the build, and only on a
  *real* miss (never on an unreachable store).

Config: ``[tool.ooptdd]`` in pyproject (see :mod:`ooptdd.config`), env overrides,
or the ini keys ``ooptdd_backend`` / ``ooptdd_service`` / ``ooptdd_verify`` /
``ooptdd_enabled`` / ``ooptdd_cid_env``.
"""
from __future__ import annotations

import os
import uuid

import pytest

from .backends import get_backend
from .config import Settings, from_mapping, load_pyproject
from .engine.verify import session_finish


def pytest_addoption(parser):
    group = parser.getgroup("ooptdd", "logs-as-spec test verification")
    group.addoption("--ooptdd", action="store_true", default=False,
                    help="force-enable ooptdd for this run")
    group.addoption("--no-ooptdd", action="store_true", default=False,
                    help="force-disable ooptdd for this run")
    for key, help_ in [
        ("ooptdd_backend", "backend name (memory|openobserve|otel|<entrypoint>)"),
        ("ooptdd_service", "service name stamped on events"),
        ("ooptdd_verify", "verify mode: off|warn|strict"),
        ("ooptdd_enabled", "auto|1|0"),
        ("ooptdd_cid_env", "env var holding the correlation id"),
    ]:
        parser.addini(key, help=help_, default=None)


def _settings_from_config(config) -> Settings:
    table: dict = {}
    for ini, field_ in [
        ("ooptdd_backend", "backend"),
        ("ooptdd_service", "service"),
        ("ooptdd_verify", "verify"),
        ("ooptdd_enabled", "enabled"),
        ("ooptdd_cid_env", "cid_env"),
    ]:
        val = config.getini(ini)
        if val:
            table[field_] = val
    pj = {}
    if getattr(config, "rootpath", None) is not None:
        pj = load_pyproject(str(config.rootpath / "pyproject.toml"))
    s = from_mapping({**pj, **table})
    if config.getoption("--ooptdd"):
        s.enabled = "1"
    if config.getoption("--no-ooptdd"):
        s.enabled = "0"
    return s


def pytest_configure(config):
    s = _settings_from_config(config)
    config._ooptdd_settings = s
    config._ooptdd_reports = []
    config._ooptdd_active = s.is_enabled()
    config._ooptdd_cid = os.getenv(s.cid_env) or f"pytest-{uuid.uuid4().hex[:12]}"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    config = item.config
    if not getattr(config, "_ooptdd_active", False):
        return
    report = outcome.get_result()
    if report.when not in ("setup", "call", "teardown"):
        return
    config._ooptdd_reports.append(
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


def pytest_collection_finish(session):
    """Ship a `session_start` heartbeat once collection is known (controller only).

    Best-effort: if the backend init/ship fails (e.g. unprovisioned store), swallow it —
    a heartbeat must never break collection. Its only job is to let verify distinguish
    'started but summary lost' from 'nothing arrived' if the summary is later dropped.
    """
    config = session.config
    if not getattr(config, "_ooptdd_active", False) or not _is_xdist_controller(config):
        return
    s: Settings = config._ooptdd_settings
    try:
        from .domain.model import build_session_start

        backend = get_backend(s.backend, service=s.service, **s.backend_options)
        backend.ship([build_session_start(
            config._ooptdd_cid, service=s.service, expected_total=len(session.items)
        )])
    except Exception:  # noqa: BLE001 — heartbeat is best-effort, never gates collection
        pass


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if not getattr(config, "_ooptdd_active", False):
        return
    if not _is_xdist_controller(config):
        return  # workers don't ship; the controller has all forwarded reports
    reports = getattr(config, "_ooptdd_reports", [])
    if not reports:
        return
    s: Settings = config._ooptdd_settings
    try:
        backend = get_backend(s.backend, service=s.service, **s.backend_options)
    except Exception as exc:
        _emit(config, [f"backend init failed ({exc}); skipping (build unaffected)"])
        return
    result = session_finish(
        backend,
        reports,
        config._ooptdd_cid,
        service=s.service,
        mode=s.mode,
        retries=s.retries,
        delay=s.delay,
        backoff=s.backoff,
        # signing key is CI-only: read from env, never config/code. Absent -> unsigned no-op.
        signing_key=os.getenv("OOPTDD_SIGNING_KEY"),
        require_signature=os.getenv("OOPTDD_REQUIRE_SIGNATURE", "") not in {"", "0", "false"},
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
