"""Gate mutation testing — prove a value-checking gate catches deviations and a
mere existence gate surfaces its blind spot.

From the ooptdd-oss prometheus cycle (A5, seed-ooptdd-negwing-mutant-allowlist-20260618).
"""
from __future__ import annotations

import pytest

from ooptdd.backends.memory import reset
from ooptdd.mutation import derive_mutations, mutation_report

_EVENTS = [
    {"event": "request.start"},
    {"event": "cycle", "verdict": "PASS"},
    {"event": "request.end"},
]


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def test_value_checking_gate_catches_every_mutation():
    # gate constrains the verdict value -> both drop and corrupt must flip it RED.
    spec = {"expect": [{"present": [{"event": "cycle", "where": {"verdict": "PASS"}}]}]}
    rep = mutation_report(_EVENTS, spec)
    assert rep["baseline_green"] is True
    assert rep["survivors"] == [] and rep["score"] == 1.0
    labels = {r["mutation"] for r in rep["mutations"]}
    assert any(m.startswith("drop:") for m in labels)
    assert "corrupt:cycle.verdict" in labels  # the value mutation was generated


def test_existence_only_gate_has_a_blind_spot():
    # gate only checks the event exists, not its verdict -> corrupting verdict survives.
    spec = {"expect": [{"present": [{"event": "cycle", "where": {"verdict": "PASS"}}]},
                       {"event": "cycle", "op": ">=", "count": 1}]}
    # build a deliberately weak gate: existence only, no value constraint
    weak = {"expect": [{"present": [{"event": "cycle"}]}]}
    rep = mutation_report(_EVENTS, weak)
    assert rep["baseline_green"] is True
    # dropping cycle is caught; there is no where-field to corrupt, so the only mutation
    # is the drop — a weak gate's blind spot shows up against a value-checking sibling.
    assert all(m["mutation"].startswith("drop:") for m in rep["mutations"])
    # contrast: the value gate generates the corrupt mutation the weak one cannot
    assert any(m.startswith("corrupt:") for m, _ in derive_mutations(_EVENTS, spec))


def test_corrupt_survives_when_gate_ignores_the_value():
    # a gate with a where on a DIFFERENT field than what carries signal: corrupting the
    # unconstrained field is not a should-catch, but corrupting the constrained one is.
    events = [{"event": "cycle", "verdict": "PASS", "station": "A"}]
    spec = {"expect": [{"present": [{"event": "cycle", "where": {"station": "A"}}]}]}
    rep = mutation_report(events, spec)
    # corrupt:cycle.station must be caught (gate constrains station)
    caught = {r["mutation"]: r["caught"] for r in rep["mutations"]}
    assert caught.get("corrupt:cycle.station") is True
    assert rep["survivors"] == []


def test_inject_error_mutation_caught_only_when_gate_forbids_errors(monkeypatch):
    spec = {"forbid_errors": True, "expect": [{"present": [{"event": "request.end"}]}]}
    rep = mutation_report(_EVENTS, spec)
    caught = {r["mutation"]: r["caught"] for r in rep["mutations"]}
    assert caught.get("inject_error") is True

    # without forbid_errors, the inject_error mutation isn't even generated
    spec2 = {"forbid_errors": False, "expect": [{"present": [{"event": "request.end"}]}]}
    monkeypatch.delenv("OOPTDD_FORBID_ERRORS", raising=False)
    labels = {m for m, _ in derive_mutations(_EVENTS, spec2)}
    assert "inject_error" not in labels


def test_baseline_not_green_is_flagged():
    spec = {"expect": [{"present": [{"event": "never.happens"}]}]}
    rep = mutation_report(_EVENTS, spec)
    assert rep["baseline_green"] is False  # inputs don't pass -> score is meaningless
