"""V1 judge — the ontology gate must catch hallucination classes the flat gate misses.

Pre-registered metric (LakatosTree_ooptdd_ontology_20260616 / node V1-ontology-typed-gate):
  conformance_violations_caught >= 3, where each is a fixture the FLAT
  (event+count) gate marks GREEN but the ONTOLOGY gate marks RED.
Classes: (1) missing required attr, (2) bad enum value, (3) unknown event type
(closed-world drift). novel_prediction = predictive power the flat gate lacks.
"""
from ooptdd.backends import MemoryBackend, memory_reset
from ooptdd.domain.ontology import Ontology
from ooptdd.engine.gate import evaluate

ONTO = Ontology.from_dict({
    "event_types": {
        "payment_authorized": {"required": ["amount"],
                               "constraints": {"amount": {"type": "number", "min": 0}}},
        "order_finalized": {"constraints": {"status": {"enum": ["ok", "ng"]}}},
        "order_received": {},
    }
})


def _ship(events):
    memory_reset()
    b = MemoryBackend()
    b.ship([{**e, "cid": "c"} for e in events])
    return b


def _flat_green(b, expect):
    return evaluate(b, {"cid": "c", "expect": expect})["ok"]


def _onto_ok(b, expect):
    return evaluate(b, {"cid": "c", "expect": expect}, ontology=ONTO)["ok"]


# ── the three pre-registered classes: flat says GREEN, ontology says RED ──────
def test_class1_missing_required_attr():
    b = _ship([{"event": "payment_authorized"}])           # no `amount`
    assert _flat_green(b, [{"event": "payment_authorized", "op": ">=", "count": 1}]) is True
    assert _onto_ok(b, [{"conforms": "payment_authorized"}]) is False


def test_class2_bad_enum_value():
    b = _ship([{"event": "order_finalized", "status": "kinda"}])   # not in {ok,ng}
    assert _flat_green(b, [{"event": "order_finalized", "op": "==", "count": 1}]) is True
    assert _onto_ok(b, [{"conforms": "order_finalized"}]) is False


def test_class3_unknown_event_type_closed_world():
    b = _ship([{"event": "order_received"}, {"event": "quantum_flux"}])  # fabricated name
    # flat gate only checks what you listed -> the bogus event is invisible -> GREEN
    assert _flat_green(b, [{"event": "order_received", "op": ">=", "count": 1}]) is True
    # closed-world conformance over all events -> unknown type = drift -> RED
    assert _onto_ok(b, [{"conforms": "*", "closed_world": True}]) is False


def test_metric_three_classes_caught():
    """The pre-registered count: >=3 classes flat-GREEN but ontology-RED."""
    cases = [
        ([{"event": "payment_authorized"}], [{"conforms": "payment_authorized"}]),
        ([{"event": "order_finalized", "status": "kinda"}], [{"conforms": "order_finalized"}]),
        ([{"event": "order_received"}, {"event": "quantum_flux"}],
         [{"conforms": "*", "closed_world": True}]),
    ]
    caught = 0
    for events, conforms_expect in cases:
        b = _ship(events)
        flat_listed = [{"event": events[0]["event"], "op": ">=", "count": 1}]
        flat_green = _flat_green(b, flat_listed)
        onto_red = not _onto_ok(b, conforms_expect)
        if flat_green and onto_red:
            caught += 1
    assert caught >= 3, f"only {caught}/3 hallucination classes caught"


# ── guardrails: no false positives, and graceful without an ontology ──────────
def test_conforming_events_pass():
    b = _ship([{"event": "payment_authorized", "amount": 10},
               {"event": "order_finalized", "status": "ok"}])
    expect = [{"conforms": "payment_authorized"}, {"conforms": "order_finalized"}]
    assert _onto_ok(b, expect) is True


def test_conforms_without_ontology_is_red_not_crash():
    b = _ship([{"event": "payment_authorized", "amount": 10}])
    res = evaluate(b, {"cid": "c", "expect": [{"conforms": "payment_authorized"}]})  # no ontology
    assert res["ok"] is False  # asked to verify conformance but couldn't -> not a clean pass


def test_open_world_ignores_unknown():
    # default (open-world) conformance must NOT flag undeclared events
    b = _ship([{"event": "quantum_flux"}])
    assert _onto_ok(b, [{"conforms": "*"}]) is True  # closed_world not set -> tolerated


def test_ontology_from_file_offline(tmp_path):
    p = tmp_path / "onto.yaml"
    p.write_text("event_types:\n  ping:\n    required: [seq]\n")
    onto = Ontology.from_file(str(p))
    assert onto.get("ping").required == ["seq"]
    b = _ship([{"event": "ping"}])  # missing seq
    assert evaluate(b, {"cid": "c", "expect": [{"conforms": "ping"}]}, ontology=onto)["ok"] is False
