"""Comparator ``where`` (op-dict) + the ``duration`` universal field-threshold check.

Grammar additions (F-study P0, minimal by design — NOT a selector DSL):

- ``where: {field: {op: gte, value: 100}}`` — a where value that is a dict with an
  ``op`` key compares instead of equality. Ops: the count-op set (symbols + OpenSLO
  words) plus ``contains``/``not_contains``. Fail-safe rules: a MISSING field never
  matches an op-dict (even ``ne``); ordering ops require numeric values.
- ``duration: {event: E, field: F, op: lte, target: N, where: {...}}`` — a UNIVERSAL
  claim over matched events: every event's ``F`` must satisfy ``op target``. LTL3
  semantics per the verified study: a violation latches VIOL immediately
  (irrevocable); satisfaction can NEVER latch mid-stream (a later matched event
  could still violate) — the check stays PEND and collapses at end-of-stream.
  Zero matched events is not a pass (``no_evidence``), mirroring the invariant
  monitor's no-evidence RED.
"""
from __future__ import annotations

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import evaluate_events
from ooptdd.engine.monitor import LiveMonitorSet, _matches, compile_check, run_monitor


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


# ── op-dict where: comparator matching ─────────────────────────────────────────
def test_where_scalar_equality_is_unchanged():
    assert _matches({"event": "a", "n": 5}, "a", {"n": 5})
    assert not _matches({"event": "a", "n": 5}, "a", {"n": 6})


def test_where_op_dict_numeric_compare():
    ev = {"event": "pay", "amount": 150}
    assert _matches(ev, "pay", {"amount": {"op": "gte", "value": 100}})
    assert _matches(ev, "pay", {"amount": {"op": "<", "value": 200}})
    assert not _matches(ev, "pay", {"amount": {"op": "lt", "value": 100}})


def test_where_op_dict_missing_field_never_matches_even_ne():
    # Fail-safe: an op-dict asserts about a field; an event WITHOUT the field cannot
    # satisfy the assertion — not even `ne`. (Otherwise an absent-wing op-dict would
    # silently stop matching offenders that dropped the field.)
    ev = {"event": "pay"}
    assert not _matches(ev, "pay", {"amount": {"op": "ne", "value": 0}})
    assert not _matches(ev, "pay", {"amount": {"op": "not_contains", "value": "x"}})


def test_where_op_dict_ordering_needs_numbers():
    assert not _matches({"event": "a", "n": "high"}, "a", {"n": {"op": "gte", "value": 3}})
    assert not _matches({"event": "a", "n": True}, "a", {"n": {"op": "gte", "value": 0}})


def test_where_op_dict_contains():
    ev = {"event": "log", "msg": "timeout while polling", "tags": ["slow", "retry"]}
    assert _matches(ev, "log", {"msg": {"op": "contains", "value": "timeout"}})
    assert _matches(ev, "log", {"tags": {"op": "contains", "value": "retry"}})
    assert not _matches(ev, "log", {"msg": {"op": "contains", "value": "deadlock"}})
    assert _matches(ev, "log", {"msg": {"op": "not_contains", "value": "deadlock"}})


def test_where_op_dict_unknown_op_is_loud():
    with pytest.raises(ValueError):
        _matches({"event": "a", "n": 1}, "a", {"n": {"op": "regex", "value": ".*"}})


def test_where_plain_dict_value_stays_literal_equality():
    # A dict value WITHOUT an `op` key is a literal payload comparison, as before.
    ev = {"event": "a", "payload": {"op_code": 1}}
    assert not _matches(ev, "a", {"payload": {"op_code": 2}})
    assert _matches(ev, "a", {"payload": {"op_code": 1}})


def test_where_op_dict_flows_through_count_and_absent(tmp_path):
    events = [
        {"event": "pay", "amount": 50},
        {"event": "pay", "amount": 150},
        {"event": "pay", "amount": 250},
    ]
    spec = {"cid": "c", "expect": [
        {"event": "pay", "where": {"amount": {"op": "gte", "value": 100}},
         "op": "==", "count": 2},
        {"absent": {"event": "pay", "where": {"amount": {"op": "gt", "value": 500}}}},
    ]}
    res = evaluate_events(spec, events, reachable=True)
    assert res["ok"] is True


# ── duration: universal field threshold ────────────────────────────────────────
def _dur_rule(**kw):
    body = {"event": "step", "field": "elapsed_s", "op": "lte", "target": 1.5}
    body.update(kw)
    return {"duration": body}


def test_duration_green_when_all_matched_satisfy():
    events = [{"event": "step", "elapsed_s": 0.3}, {"event": "step", "elapsed_s": 1.5},
              {"event": "other", "elapsed_s": 99}]
    res = run_monitor(compile_check(_dur_rule()), events, True)
    assert res["passed"] is True and res["got"] == 2 and res["violations"] == 0


def test_duration_one_over_threshold_is_red_with_offender():
    events = [{"event": "step", "elapsed_s": 0.3}, {"event": "step", "elapsed_s": 2.0}]
    res = run_monitor(compile_check(_dur_rule()), events, True)
    assert res["passed"] is False and res["violations"] == 1
    assert res["verdict"] == "viol" and res["settled_at"] == 1  # latched at the offender


def test_duration_never_latches_sat_mid_stream():
    # Universal claim: satisfaction is only knowable at end-of-stream. A passing
    # prefix must read PEND, not SAT (the verified LTL3 correction).
    bank = LiveMonitorSet.from_rules([_dur_rule()])
    bank.feed({"event": "step", "elapsed_s": 0.1})
    bank.feed({"event": "step", "elapsed_s": 0.2})
    assert bank.verdicts() == ["pend"]
    out = bank.collapse(True)[0]
    assert out["passed"] is True


def test_duration_no_matched_events_is_not_a_pass():
    res = run_monitor(compile_check(_dur_rule()), [{"event": "other"}], True)
    assert res["passed"] is False and res["no_evidence"] is True


def test_duration_missing_or_non_numeric_field_is_a_violation():
    # The check asserts about the field; a matched event that cannot prove it fails
    # closed (same discipline as the op-dict where, but here it IS an assertion).
    for bad in [{"event": "step"}, {"event": "step", "elapsed_s": "fast"}]:
        res = run_monitor(compile_check(_dur_rule()), [bad], True)
        assert res["passed"] is False and res["violations"] == 1


def test_duration_unreachable_read_is_never_a_pass():
    events = [{"event": "step", "elapsed_s": 0.1}]
    res = run_monitor(compile_check(_dur_rule()), events, False)
    assert res["passed"] is False


def test_duration_where_filter_and_gte_shape():
    # `gte` direction: e.g. "every replica count stayed >= 2"
    rule = {"duration": {"event": "scale", "where": {"pool": "web"},
                         "field": "replicas", "op": "gte", "target": 2}}
    events = [{"event": "scale", "pool": "web", "replicas": 3},
              {"event": "scale", "pool": "batch", "replicas": 0},
              {"event": "scale", "pool": "web", "replicas": 2}]
    res = run_monitor(compile_check(rule), events, True)
    assert res["passed"] is True and res["got"] == 2


def test_duration_requires_field_and_target():
    with pytest.raises(ValueError):
        compile_check({"duration": {"event": "step", "op": "lte", "target": 1}})
    with pytest.raises(ValueError):
        compile_check({"duration": {"event": "step", "field": "elapsed_s"}})


def test_duration_full_gate_integration():
    MemoryBackend().ship([
        {"cid": "d1", "event": "checkout", "elapsed_s": 0.4},
        {"cid": "d1", "event": "checkout", "elapsed_s": 0.9},
    ])
    spec = {"cid": "d1", "expect": [
        {"duration": {"event": "checkout", "field": "elapsed_s", "op": "lte",
                      "target": 1.0}},
    ]}
    events = MemoryBackend().query("d1", since_us=0, until_us=2**63 - 1).events
    res = evaluate_events(spec, events, reachable=True)
    assert res["ok"] is True
    chk = res["checks"][0]
    assert chk["passed"] is True and chk["got"] == 2
