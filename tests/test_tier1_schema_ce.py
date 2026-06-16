"""Tier-1 #2 (JSON-Schema-equivalent EventType) + #5 (CloudEvents floor)."""
from __future__ import annotations

from ooptdd.model import (
    CE_REQUIRED,
    build_outcome_records,
    cloudevents_envelope,
    validate_cloudevents,
)
from ooptdd.ontology import EventType, Ontology, check_conformance


# ── #2: additionalProperties:false → unexpected-attr drift ────────────────────
def test_additional_properties_false_flags_unexpected_payload_attr():
    et = EventType(name="pay", required=["amount"],
                   constraints={"amount": {"type": "number"}}, additional_properties=False)
    # `amonut` is a typo'd attribute — a flat gate counts the event by name and goes GREEN.
    assert et.validate({"event": "pay", "amount": 5, "amonut": 9}) == [
        "unexpected attr 'amonut' (additionalProperties:false)"
    ]


def test_additional_properties_false_allows_envelope_keys():
    et = EventType(name="pay", required=["amount"], additional_properties=False)
    ev = {"event": "pay", "amount": 5, "cid": "c1", "service": "x", "level": "INFO",
          "_timestamp": 1, "sig": "ab", "correlation_id": "c1", "cycle_id": "c1"}
    assert et.validate(ev) == []  # transport plumbing is never "unexpected"


def test_additional_properties_true_is_default_and_open():
    et = EventType(name="pay", required=["amount"])
    assert et.additional_properties is True
    assert et.validate({"event": "pay", "amount": 5, "anything": "ok"}) == []


def test_from_dict_parses_additional_properties():
    ont = Ontology.from_dict({"event_types": {
        "pay": {"required": ["amount"], "additional_properties": False},
    }})
    assert ont.get("pay").additional_properties is False


def test_closed_attr_drift_is_red_via_check_conformance():
    ont = Ontology.from_dict({"event_types": {
        "pay": {"required": ["amount"], "additional_properties": False},
    }})
    res = check_conformance([{"event": "pay", "amount": 1, "bogus": 2}], ont)
    assert not res["passed"]
    assert "unexpected attr 'bogus'" in res["violations"][0]["problems"][0]


# ── #5: CloudEvents 1.0 floor ─────────────────────────────────────────────────
def test_cloudevents_floor_present_after_projection():
    recs = build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "duration": 0.1, "when": "call"}],
        cid="c1", service="svc",
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
