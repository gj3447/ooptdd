"""Tier-1 #2 (JSON-Schema-equivalent EventType) + #5 (CloudEvents floor)."""

from __future__ import annotations

import pytest

from ooptdd.adapters.pytest import build_outcome_records
from ooptdd.domain.model import CE_REQUIRED, cloudevents_envelope, validate_cloudevents
from ooptdd.domain.ontology import EventType, Ontology, check_conformance


# ── #2: additionalProperties:false → unexpected-attr drift ────────────────────
def test_additional_properties_false_flags_unexpected_payload_attr():
    et = EventType(
        name="pay",
        required=["amount"],
        constraints={"amount": {"type": "number"}},
        additional_properties=False,
    )
    # `amonut` is a typo'd attribute — a flat gate counts the event by name and goes GREEN.
    assert et.validate({"event": "pay", "amount": 5, "amonut": 9}) == [
        "unexpected attr 'amonut' (additionalProperties:false)"
    ]


def test_additional_properties_false_allows_envelope_keys():
    et = EventType(name="pay", required=["amount"], additional_properties=False)
    ev = {
        "event": "pay",
        "amount": 5,
        "cid": "c1",
        "service": "x",
        "level": "INFO",
        "_timestamp": 1,
        "sig": "ab",
        "correlation_id": "c1",
    }
    assert et.validate(ev) == []  # transport plumbing is never "unexpected"


def test_additional_properties_true_is_default_and_open():
    et = EventType(name="pay", required=["amount"])
    assert et.additional_properties is True
    assert et.validate({"event": "pay", "amount": 5, "anything": "ok"}) == []


def test_from_dict_parses_additional_properties():
    ont = Ontology.from_dict(
        {
            "event_types": {
                "pay": {"required": ["amount"], "additional_properties": False},
            }
        }
    )
    assert ont.get("pay").additional_properties is False


def test_closed_attr_drift_is_red_via_check_conformance():
    ont = Ontology.from_dict(
        {
            "event_types": {
                "pay": {"required": ["amount"], "additional_properties": False},
            }
        }
    )
    res = check_conformance([{"event": "pay", "amount": 1, "bogus": 2}], ont)
    assert not res["passed"]
    assert "unexpected attr 'bogus'" in res["violations"][0]["problems"][0]


def test_closed_world_has_no_implicit_framework_prefix_exemption():
    ontology = Ontology.from_dict({"closed_world": True, "event_types": {"known": {}}})

    result = check_conformance([{"event": "ooptdd.undeclared"}], ontology)

    assert result["passed"] is False
    assert result["unknown"] == ["ooptdd.undeclared"]


def test_carrier_exclusion_is_exact_and_explicit():
    ontology = Ontology.from_dict({"closed_world": True, "event_types": {}})
    events = [{"event": "carrier.control"}, {"event": "carrier.control.child"}]

    result = check_conformance(
        events, ontology, excluded_event_types=frozenset({"carrier.control"})
    )

    assert result["passed"] is False
    assert result["unknown"] == ["carrier.control.child"]


@pytest.mark.parametrize(
    "definition",
    [
        {"unknown": True},
        {"closed_world": "yes"},
        {"event_types": []},
        {"event_types": {"a": {"unknown": True}}},
        {"event_types": {"a": {"required": ["x", "x"]}}},
        {"event_types": {"a": {"constraints": {"x": {"type": "mystery"}}}}},
        {"event_types": {"a": {"constraints": {"x": {"pattern": ".*"}}}}},
        {"event_types": {"a": {"additional_properties": "false"}}},
    ],
)
def test_ontology_definition_rejects_unknown_or_malformed_schema(definition):
    with pytest.raises((TypeError, ValueError)):
        Ontology.from_dict(definition)


# ── #5: CloudEvents 1.0 floor ─────────────────────────────────────────────────
def test_cloudevents_floor_present_after_projection():
    recs = build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "duration": 0.1, "when": "call"}],
        cid="c1",
        service="svc",
    )
    ce = cloudevents_envelope(recs[-1])  # the test_session summary
    assert validate_cloudevents(ce) == []
    assert ce["type"] == "test_session" and ce["source"] == "svc" and ce["subject"] == "c1"
    assert ce["specversion"] == "1.0" and len(ce["id"]) == 32


def test_cloudevents_id_is_deterministic():
    rec = {"event": "x", "service": "s", "cid": "c1", "n": 1}
    assert cloudevents_envelope(rec)["id"] == cloudevents_envelope(rec)["id"]


def test_cloudevents_projection_is_nondestructive():
    rec = {"event": "x", "service": "s", "cid": "c1", "payload": 9}
    ce = cloudevents_envelope(rec)
    assert ce["payload"] == 9 and rec.get("id") is None  # original untouched


def test_validate_cloudevents_rejects_empty_type():
    bad = {"id": "x", "source": "s", "specversion": "1.0", "type": ""}
    assert validate_cloudevents(bad) == ["missing/empty required CloudEvents attr 'type'"]


def test_ce_required_is_the_spec_floor():
    assert CE_REQUIRED == ("id", "source", "specversion", "type")
