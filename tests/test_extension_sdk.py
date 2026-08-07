from __future__ import annotations

from collections.abc import Mapping

import pytest

from ooptdd import identity, sdk


def test_extension_sdk_exports_only_the_declared_contract():
    expected = {
        "EXTENSION_API_VERSION",
        "BackendCaps",
        "CheckContext",
        "CheckFn",
        "CheckRegistry",
        "ExtensionProvider",
        "GatePolicy",
        "ProbeResult",
        "QueryResult",
        "Runtime",
        "backend_caps",
        "check",
        "checks_from",
        "compile_check",
        "compose_check_registry",
        "compose_runtime",
        "detect_check_key",
        "evidence_tier",
        "evaluate_events",
        "load_gate",
        "matches_event",
        "resolve_gate_policy",
        "resolve_matcher",
        "to_junit_xml",
        "to_markdown",
        "verify_gate",
    }

    assert sdk.EXTENSION_API_VERSION == 1
    assert set(sdk.__all__) == expected
    assert all(hasattr(sdk, name) for name in expected)


def test_sdk_composes_a_metadata_only_check_without_global_registration():
    @sdk.check("custom", strength="value-pinned", event_names=lambda rule: (rule["event"],))
    def custom(events, rule, context):
        del context
        return {"passed": any(event.get("event") == rule["event"] for event in events)}

    registry = sdk.compose_check_registry(sdk.checks_from(custom))

    assert isinstance(registry, Mapping)
    assert registry["custom"] is custom
    with pytest.raises(TypeError):
        registry["other"] = custom


def test_identity_api_is_generic_and_domain_separated():
    first = identity.digest_json({"value": 1}, scope="example-a", schema_version="v1")
    second = identity.digest_json({"value": 1}, scope="example-b", schema_version="v1")

    assert first.value != second.value
    assert identity.Digest.from_dict(first.to_dict()) == first
