"""The assertion-strength ladder, formalized: ``evidence_tier(result)`` grades how strong a
GREEN actually is, from ``local_pass`` (the fake-green floor) up to ``external_verdict`` (the
one rung whose input is not the system's own self-report). This is the code form of the
LakatoTree element ``elem-ooptdd-assert-strength-ladder`` (local pass → emitted → arrived →
queryable causal cycle → external verdict).
"""
from ooptdd.domain.ports import ProbeResult
from ooptdd.engine.gate import evaluate_events, evidence_tier


def _ev(event, i, **a):
    return {"cid": "c", "cycle_id": "c", "event": event, "_timestamp": i, **a}


def _tier(expect, events, **kw):
    res = evaluate_events({"expect": expect}, events, reachable=True, complete=True, cid="c", **kw)
    return evidence_tier(res)


def test_tier_local_pass_for_vacuous_gate():
    # nothing asserted -> the verdict proves only "the test ran"
    assert _tier([], []) == "local_pass"


def test_tier_local_pass_when_store_unreachable():
    res = evaluate_events({"expect": [{"event": "a", "op": ">=", "count": 1}]},
                          [], reachable=False, complete=True, cid="c")
    assert evidence_tier(res) == "local_pass"


def test_tier_emitted_when_only_absence_passes():
    # an `absent` check that fires on nothing is charged=0: named, not positively witnessed
    assert _tier([{"absent": [{"event": "boom"}]}], [_ev("a", 1)]) == "emitted"


def test_tier_arrived_on_positive_evidence():
    # a count check that actually sees its event -> charged -> the trace arrived
    assert _tier([{"event": "a", "op": ">=", "count": 1}], [_ev("a", 1)]) == "arrived"


def test_tier_queryable_causal_on_invariant():
    # a cross-event conservation relation holding -> value consistency, not just counts
    inv = {"invariant": {"left": {"reduce": "sum", "field": "amount", "event": "pay"},
                         "right": {"reduce": "sum", "field": "amount", "event": "ship"},
                         "op": "==", "tol": 0.0}}
    assert _tier([inv], [_ev("pay", 1, amount=42), _ev("ship", 2, amount=42)]) == "queryable_causal"


def test_tier_external_verdict_on_separate_source_corroboration():
    class _Probe:
        def probe(self, kind, selector, cid):
            return ProbeResult(reachable=True, value=42, separate_source=True)

    rule = {"external": {"kind": "db_row", "selector": {}, "want": 42}}
    assert _tier([rule], [], probe=_Probe()) == "external_verdict"


def test_tier_derived_self_external_is_only_arrived_not_external_verdict():
    # an external check the probe could reach but that is NOT separate_source is self-consistency
    # relocated, so it must NOT reach the external_verdict rung — it is at most `arrived`.
    class _Probe:
        def probe(self, kind, selector, cid):
            return ProbeResult(reachable=True, value=42, separate_source=False)

    rule = {"external": {"kind": "db_row", "selector": {}, "want": 42}}
    assert _tier([rule], [], probe=_Probe()) == "arrived"
