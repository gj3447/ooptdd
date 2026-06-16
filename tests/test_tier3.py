"""Tier-3: MTL intervals (#10), HMAC hash-chaining (#11), ontology compat (#13)."""
from __future__ import annotations

import pytest

from ooptdd.backends.base import QueryResult
from ooptdd.gate import evaluate
from ooptdd.model import build_outcome_records, sign_chain, verify_chain
from ooptdd.ontology import Ontology, ontology_compat


class _Fixed:
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(self, events):
        self._events = events

    def ship(self, events):  # pragma: no cover
        pass

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=True, events=list(self._events))


def _ev(name, ts):
    return {"event": name, "_timestamp": ts}


US = 1_000_000  # µs per second


# ── #10 must_order within_s (bounded F[0,within]) ─────────────────────────────
def test_within_passes_when_gap_under_bound():
    b = _Fixed([_ev("a", 0), _ev("b", 2 * US)])
    res = evaluate(b, {"cid": "c1", "expect": [{"must_order": ["a", "b"], "within_s": 5}]})
    assert res["ok"] and res["checks"][0]["gaps_exceeded"] == []


def test_within_fails_when_gap_exceeds_bound():
    b = _Fixed([_ev("a", 0), _ev("b", 10 * US)])
    res = evaluate(b, {"cid": "c1", "expect": [{"must_order": ["a", "b"], "within_s": 5}]})
    assert not res["ok"] and res["checks"][0]["gaps_exceeded"] == ["a->b"]


def test_within_still_requires_order():
    b = _Fixed([_ev("a", 5 * US), _ev("b", 1 * US)])
    res = evaluate(b, {"cid": "c1", "expect": [{"must_order": ["a", "b"], "within_s": 100}]})
    assert not res["ok"] and res["checks"][0]["ordered"] is False


# ── #10 heartbeat (G[0,every_s] F event) ──────────────────────────────────────
def test_heartbeat_passes_when_beats_are_frequent():
    b = _Fixed([_ev("hb", 0), _ev("hb", 3 * US), _ev("hb", 6 * US)])
    res = evaluate(b, {"cid": "c1", "expect": [{"heartbeat": "hb", "every_s": 5}]})
    chk = res["checks"][0]
    assert res["ok"] and chk["beats"] == 3 and chk["max_gap_s"] == 3.0


def test_heartbeat_fails_on_silence_gap():
    b = _Fixed([_ev("hb", 0), _ev("hb", 20 * US)])
    res = evaluate(b, {"cid": "c1", "expect": [{"heartbeat": "hb", "every_s": 5}]})
    assert not res["ok"] and res["checks"][0]["max_gap_s"] == 20.0


def test_heartbeat_no_beat_is_red():
    b = _Fixed([_ev("other", 1)])
    res = evaluate(b, {"cid": "c1", "expect": [{"heartbeat": "hb", "every_s": 5}]})
    assert not res["ok"] and res["checks"][0]["reason"] == "no_beat"


def test_heartbeat_optional_miss_surfaced_not_gating():
    b = _Fixed([_ev("other", 1)])
    res = evaluate(b, {"cid": "c1", "expect": [
        {"heartbeat": "hb", "every_s": 5, "optional": True},
    ]})
    assert res["ok"] and res["optional_failed"] == ["heartbeat:hb@5.0s"]


# ── #11 HMAC hash chain ───────────────────────────────────────────────────────
def _records():
    return build_outcome_records(
        [{"nodeid": f"t::{i}", "outcome": "passed", "duration": 0.0, "when": "call"}
         for i in range(3)], cid="c1")


def test_chain_round_trips_intact():
    chained = sign_chain(_records(), "k")
    assert verify_chain(chained, "k")["ok"] is True
    assert all("sig_chain" in r and "prev_sig" in r for r in chained)


def test_chain_detects_record_edit():
    chained = sign_chain(_records(), "k")
    chained[1]["outcome"] = "failed"  # tamper a middle record
    res = verify_chain(chained, "k")
    assert res["ok"] is False and res["broken_index"] == 1
    assert "tamper" in res["reason"]


def test_chain_detects_deletion():
    chained = sign_chain(_records(), "k")
    del chained[1]  # drop an inconvenient receipt
    res = verify_chain(chained, "k")
    assert res["ok"] is False and "deletion_or_reorder" in res["reason"]


def test_chain_detects_reorder():
    chained = sign_chain(_records(), "k")
    chained[1], chained[2] = chained[2], chained[1]
    assert verify_chain(chained, "k")["ok"] is False


def test_chain_wrong_key_fails():
    chained = sign_chain(_records(), "k")
    assert verify_chain(chained, "wrong")["ok"] is False


def test_chain_key_evolution_round_trips():
    chained = sign_chain(_records(), "k", evolve=True)
    assert verify_chain(chained, "k", evolve=True)["ok"] is True
    # verifying an evolving chain without evolve must fail (keys diverge after rec 0)
    assert verify_chain(chained, "k", evolve=False)["ok"] is False


# ── #13 ontology evolution compatibility ──────────────────────────────────────
def _ont(types, closed=False):
    return Ontology.from_dict({"event_types": types, "closed_world": closed})


def test_backward_break_on_new_required_attr():
    old = _ont({"pay": {"required": ["amount"]}})
    new = _ont({"pay": {"required": ["amount", "currency"]}})
    r = ontology_compat(old, new, "backward")
    assert not r["compatible"] and "currency" in r["violations"][0]


def test_backward_ok_when_adding_optional():
    old = _ont({"pay": {"required": ["amount"]}})
    new = _ont({"pay": {"required": ["amount"], "constraints": {"note": {"type": "str"}}}})
    assert ontology_compat(old, new, "backward")["compatible"]


def test_forward_break_on_removed_required():
    old = _ont({"pay": {"required": ["amount", "currency"]}})
    new = _ont({"pay": {"required": ["amount"]}})
    r = ontology_compat(old, new, "forward")
    assert not r["compatible"] and "currency" in r["violations"][0]


def test_enum_shrink_breaks_backward_grow_breaks_forward():
    old = _ont({"s": {"constraints": {"status": {"enum": ["ok", "ng"]}}}})
    narrowed = _ont({"s": {"constraints": {"status": {"enum": ["ok"]}}}})
    widened = _ont({"s": {"constraints": {"status": {"enum": ["ok", "ng", "warn"]}}}})
    assert not ontology_compat(old, narrowed, "backward")["compatible"]
    assert ontology_compat(old, narrowed, "forward")["compatible"]
    assert not ontology_compat(old, widened, "forward")["compatible"]
    assert ontology_compat(old, widened, "backward")["compatible"]


def test_full_requires_both_directions():
    old = _ont({"pay": {"required": ["amount"]}})
    new = _ont({"pay": {"required": ["amount", "currency"]}})
    assert not ontology_compat(old, new, "full")["compatible"]


def test_closed_world_type_removal_breaks_backward():
    old = _ont({"a": {}, "b": {}}, closed=True)
    new = _ont({"a": {}}, closed=True)
    r = ontology_compat(old, new, "backward")
    assert not r["compatible"] and "'b'" in r["violations"][0]


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        ontology_compat(_ont({}), _ont({}), "sideways")
