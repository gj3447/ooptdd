"""Backend composition snapshots environment once and preserves extension contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ooptdd.backends import BackendRegistry, get_backend
from ooptdd.backends.clickhouse import ClickHouseBackend
from ooptdd.backends.jsonl import JsonlBackend
from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.domain.ports import EventSink, EventSource, QueryResult, backend_identity
from ooptdd.domain.settings import EnvironmentKeys


def test_network_backend_captures_environment_at_construction(monkeypatch):
    environment = {
        "CUSTOM_OO_URL": "http://first.example:5080/",
        "CUSTOM_OO_USER": "alice",
        "CUSTOM_OO_PASSWORD": "secret",
    }
    keys = EnvironmentKeys(
        openobserve_url="CUSTOM_OO_URL",
        openobserve_user="CUSTOM_OO_USER",
        openobserve_password="CUSTOM_OO_PASSWORD",
    )
    backend = OpenObserveBackend(environment=environment, env_keys=keys)

    environment["CUSTOM_OO_URL"] = "http://mutated.example"
    monkeypatch.setenv("CUSTOM_OO_URL", "http://ambient.example")

    assert backend_identity(backend) == "http://first.example:5080"
    assert backend._endpoint()[0] == "http://first.example:5080"


def test_missing_configuration_stays_missing_after_ambient_change(monkeypatch):
    monkeypatch.delenv("OOPTDD_CH_URL", raising=False)
    backend = ClickHouseBackend()
    monkeypatch.setenv("OOPTDD_CH_URL", "http://too-late.example")

    result = backend.query("cid", since_us=0, until_us=1)
    assert result.reachable is False
    assert backend_identity(backend) == "ClickHouseBackend"


def test_jsonl_requires_explicit_path_or_captured_mapping(monkeypatch, tmp_path):
    ambient_path = str(tmp_path / "ambient.jsonl")
    monkeypatch.setenv("OOPTDD_JSONL_PATH", ambient_path)

    with pytest.raises(ValueError, match="path= is required"):
        JsonlBackend()

    backend = JsonlBackend(environment={"OOPTDD_JSONL_PATH": ambient_path})
    assert backend.path == ambient_path


def test_registry_injects_snapshot_only_into_framework_builtins():
    memory = get_backend("memory", environment={"IGNORED": "value"})
    assert isinstance(memory, EventSink)
    assert isinstance(memory, EventSource)

    received = {}

    def extension_factory(*, service=None):
        received["service"] = service
        return object()

    registry = BackendRegistry()
    registry.register("extension", extension_factory)
    registry.resolve(
        "extension",
        service="external.service",
        environment={"PRIVATE": "snapshot"},
        env_keys=EnvironmentKeys(),
    )
    assert received == {"service": "external.service"}


def test_get_backend_uses_only_an_explicit_extension_registry():
    registry = BackendRegistry()
    registry.register("custom", lambda: object())

    assert get_backend("custom", registry=registry) is not None
    with pytest.raises(ValueError, match="unknown ooptdd backend"):
        get_backend("custom")


def test_builtin_constructors_reject_unknown_options():
    with pytest.raises(TypeError, match="unexpected_keyword"):
        get_backend("memory", unexpected_keyword=True)


def test_identity_does_not_follow_a_legacy_env_pointer(monkeypatch):
    class LegacyBackend:
        url_env = "LEGACY_BACKEND_URL"

    monkeypatch.setenv("LEGACY_BACKEND_URL", "http://ambient.example")
    assert backend_identity(LegacyBackend()) == "LegacyBackend"


def test_backend_identity_removes_url_secrets_from_driver_and_fallback():
    secret_url = "https://alice:password@example.test:8443/api/?token=secret#private"
    backend = OpenObserveBackend(base_url=secret_url)

    assert backend.identity() == "https://example.test:8443/api"
    assert backend_identity(backend) == "https://example.test:8443/api"

    class AttributeOnlyBackend:
        endpoint = secret_url

    assert backend_identity(AttributeOnlyBackend()) == "https://example.test:8443/api"


def test_query_result_fields_are_immutable():
    result = QueryResult(reachable=True)

    with pytest.raises(FrozenInstanceError):
        result.reachable = False
