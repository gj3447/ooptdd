"""The CLI and pytest adapters share one immutable configuration snapshot."""

from __future__ import annotations

from argparse import Namespace
from dataclasses import FrozenInstanceError

import pytest

from ooptdd.bootstrap import compose_runtime
from ooptdd.cli import _runtime
from ooptdd.config import SETTING_DEFINITIONS, resolve_signing_settings
from ooptdd.engine.gate import check
from ooptdd.plugin import _ini_name, _runtime_from_config, pytest_addoption


@check(
    "example_order",
    strength="ordered",
    event_names=lambda rule: rule["example_order"],
)
def _example_order(events, rule, ctx):
    observed = [event.get("event") for event in events]
    expected = rule["example_order"]
    return {"passed": ctx.reachable and observed == expected, "charged": bool(events)}


def _example_checks():
    return {"example_order": _example_order}


def test_runtime_precedence_and_snapshot_immutability():
    environment = {
        "OOPTDD_BACKEND": "jsonl",
        "OOPTDD_RETRIES": "7",
        "OOPTDD_SIGNING_KEY": "secret",
    }
    runtime = compose_runtime(
        project={"backend": "openobserve", "retries": 3, "max_delay": 9},
        environment=environment,
        overrides={"retries": 2},
    )

    assert runtime.settings.backend == "jsonl"
    assert runtime.settings.retries == 2
    assert runtime.settings.max_delay == 9
    assert runtime.signing.key == "secret"

    environment["OOPTDD_BACKEND"] = "clickhouse"
    assert runtime.settings.backend == "jsonl"
    assert runtime.environment["OOPTDD_BACKEND"] == "jsonl"
    with pytest.raises(FrozenInstanceError):
        runtime.settings.backend = "memory"
    with pytest.raises(TypeError):
        runtime.environment["OOPTDD_BACKEND"] = "memory"


def test_runtime_repr_hides_captured_environment():
    runtime = compose_runtime(environment={"PRIVATE_TOKEN": "do-not-render"})

    assert "PRIVATE_TOKEN" not in repr(runtime)
    assert "do-not-render" not in repr(runtime)


def test_backend_options_are_deeply_snapshotted_and_immutable():
    options = {"headers": {"x-mode": "strict"}, "ports": [8123, 9000]}
    runtime = compose_runtime(
        project={"backend_options": options},
        environment={},
    )

    options["headers"]["x-mode"] = "changed"
    options["ports"].append(9999)

    assert runtime.settings.backend_options["headers"]["x-mode"] == "strict"
    assert runtime.settings.backend_options["ports"] == (8123, 9000)
    with pytest.raises(TypeError):
        runtime.settings.backend_options["headers"]["x-mode"] = "changed"


def test_invalid_security_boolean_fails_closed():
    with pytest.raises(ValueError, match="invalid security boolean"):
        resolve_signing_settings({"OOPTDD_REQUIRE_SIGNATURE": "definitely"})


def test_runtime_activates_only_explicit_extension_modules():
    called = []

    def first():
        called.append("first")
        return {"first": lambda events, rule, ctx: {"passed": True}}

    def second():
        called.append("second")
        return {"second": lambda events, rule, ctx: {"passed": True}}

    runtime = compose_runtime(
        project={"extensions": ["example.first", "example.second"]},
        environment={},
        extension_providers={"example.first": first, "example.second": second},
    )

    activated = runtime.activate_extensions()
    assert activated is not runtime
    assert {"first", "second"} <= set(activated.check_registry)
    assert called == ["first", "second"]


def test_extension_modules_follow_the_same_central_precedence():
    runtime = compose_runtime(
        project={"extensions": ["project.extension"]},
        environment={"OOPTDD_EXTENSIONS": "env.one, env.two"},
        overrides={"extensions": ["caller.extension", "caller.extension"]},
    )

    assert runtime.settings.extensions == ("caller.extension",)


def test_runtime_extension_snapshots_are_isolated_and_repeatable():
    runtime_a = compose_runtime(
        project={"extensions": ["example"]},
        environment={},
        extension_providers={"example": _example_checks},
    ).activate_extensions()
    runtime_b = compose_runtime(project={}, environment={})
    repeated = runtime_a.activate_extensions()

    assert "example_order" in runtime_a.check_registry
    assert "example_order" not in runtime_b.check_registry
    assert repeated.check_registry == runtime_a.check_registry
    assert repeated.check_registry is not runtime_a.check_registry


def test_runtime_evaluate_and_verify_use_owned_extension_snapshot():
    runtime = compose_runtime(
        project={"extensions": ["example"]},
        environment={},
        extension_providers={"example": _example_checks},
    ).activate_extensions()
    backend = runtime.backend()
    backend.ship(
        [
            {"cid": "c", "event": "first"},
            {"cid": "c", "event": "second"},
        ]
    )
    spec = {"cid": "c", "expect": [{"example_order": ["first", "second"]}]}

    assert runtime.evaluate(backend, spec)["ok"]
    verified = runtime.verify(backend, "c", spec, retries=1, sleeper=lambda _: None)
    assert verified["ok"]


def test_runtime_static_analysis_uses_owned_extension_snapshot():
    runtime = compose_runtime(
        project={"extensions": ["example"]},
        environment={},
        extension_providers={"example": _example_checks},
    ).activate_extensions()
    spec = {"expect": [{"example_order": ["first", "second"]}]}

    assert runtime.lint_spec(spec) == []
    fingerprint = runtime.strength_fingerprint(spec)
    assert fingerprint["by_strength"] == {"ordered": 1}


def test_core_and_runtime_registries_are_immutable():
    from ooptdd.engine.gate import CHECK_REGISTRY

    runtime = compose_runtime(project={}, environment={})
    assert "aggregate" in runtime.check_registry
    with pytest.raises(TypeError):
        CHECK_REGISTRY["aggregate"] = lambda *_: {}
    with pytest.raises(TypeError):
        runtime.check_registry["aggregate"] = lambda *_: {}


def test_base_runtime_does_not_know_optional_extension_names():
    runtime = compose_runtime(project={"extensions": ["trajectory"]}, environment={})
    with pytest.raises(ValueError, match="unknown extension 'trajectory'"):
        runtime.activate_extensions()


@pytest.mark.parametrize(
    ("providers", "extensions", "error"),
    [
        ({}, ["unknown"], "unknown extension"),
        ({"bad": None}, ["bad"], "must be callable"),
        ({"bad": lambda: []}, ["bad"], "must return a mapping"),
        ({"bad": lambda: {"x": None}}, ["bad"], "must be callable"),
        ({"bad": lambda: {"present": lambda *_: {}}}, ["bad"], "duplicate"),
    ],
)
def test_runtime_extension_catalog_fails_closed(providers, extensions, error):
    runtime = compose_runtime(
        project={"extensions": extensions},
        environment={},
        extension_providers=providers,
    )
    with pytest.raises((TypeError, ValueError), match=error):
        runtime.activate_extensions()


def test_runtime_rejects_duplicate_predicates_across_named_extensions():
    def provider():
        return {"same": lambda *_: {"passed": True}}

    runtime = compose_runtime(
        project={"extensions": ["one", "two"]},
        environment={},
        extension_providers={"one": provider, "two": provider},
    )
    with pytest.raises(ValueError, match="duplicate check predicate 'same'"):
        runtime.activate_extensions()


class _FakeConfig:
    rootpath = None

    def __init__(self, ini=None, options=None):
        self._ini = ini or {}
        self._options = options or {}
        self.hook = _FakeHook()

    def getini(self, name):
        return self._ini.get(name)

    def getoption(self, name):
        return self._options.get(name, False)


class _FakeHook:
    def pytest_ooptdd_runtime(self, **_kwargs):
        return None


class _FakeParser:
    def __init__(self):
        self.ini = {}

    def getgroup(self, *_args):
        return self

    def addoption(self, *_args, **_kwargs):
        return None

    def addini(self, name, **kwargs):
        self.ini[name] = kwargs


def test_pytest_ini_definitions_include_complete_polling_policy(monkeypatch):
    definitions = {definition.field: definition for definition in SETTING_DEFINITIONS}
    assert {
        "retries",
        "delay",
        "backoff",
        "max_delay",
        "confirm_rounds",
        "confirm_delay_s",
    } <= definitions.keys()
    parser = _FakeParser()
    pytest_addoption(parser)
    expected = {_ini_name(definition.field) for definition in SETTING_DEFINITIONS}
    expected.update({"ooptdd_verify", "ooptdd_enabled", "ooptdd_cid_env"})
    assert set(parser.ini) == expected

    monkeypatch.setenv("OOPTDD_RETRIES", "8")
    runtime = _runtime_from_config(
        _FakeConfig(
            ini={
                "ooptdd_retries": "2",
                "ooptdd_max_delay": "0.5",
                "ooptdd_confirm_delay_s": "0.25",
            }
        )
    )
    assert runtime.settings.retries == 2
    assert runtime.settings.max_delay == 0.5
    assert runtime.settings.confirm_delay_s == 0.25


def test_pytest_force_flags_are_final_overrides(monkeypatch):
    monkeypatch.setenv("OOPTDD_ENABLED", "0")
    enabled_config = _FakeConfig(options={"--ooptdd": True})
    disabled_config = _FakeConfig(options={"--no-ooptdd": True})
    _runtime_from_config(enabled_config)
    _runtime_from_config(disabled_config)
    assert enabled_config._ooptdd_adapter_settings.is_enabled("clickhouse") is True
    assert disabled_config._ooptdd_adapter_settings.is_enabled("memory") is False


def test_pytest_adapter_policy_is_separate_from_framework_settings(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ooptdd]\nbackend = 'memory'\n"
        "[tool.ooptdd.adapters.pytest]\nverify = 'strict'\nenabled = 'off'\n"
        "cid_env = 'APP_CORRELATION_ID'\n",
        encoding="utf-8",
    )
    config = _FakeConfig()
    config.rootpath = tmp_path
    runtime = _runtime_from_config(config)
    adapter = config._ooptdd_adapter_settings
    assert not hasattr(runtime.settings, "verify")
    assert not hasattr(runtime.settings, "enabled")
    assert not hasattr(runtime.settings, "cid_env")
    assert adapter == adapter.__class__(
        verify="strict", enabled="off", cid_env="APP_CORRELATION_ID"
    )


def test_cli_only_overrides_values_the_user_actually_supplied(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ooptdd]\nretries = 6\ndelay = 0.75\nbackoff = 3\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OOPTDD_RETRIES", "5")

    inherited = _runtime(Namespace(retries=None, delay=None, backend=None))
    explicit = _runtime(Namespace(retries=2, delay=None, backend=None))

    assert inherited.settings.retries == 5
    assert inherited.settings.delay == 0.75
    assert inherited.settings.backoff == 3
    assert explicit.settings.retries == 2
    assert explicit.settings.delay == 0.75
