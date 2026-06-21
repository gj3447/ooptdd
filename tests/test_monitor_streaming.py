"""The kernel is a real streaming LTL₃/MTL monitor, not a relabeled batch count.

These lock the *anticipatory* behaviour: a monitor commits to ⊤/⊥ the instant the
verdict is inevitable and records the stream index where that happened, and the final
collapse still equals the count comparison (so gate semantics are unchanged).
"""
from __future__ import annotations

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import evaluate
from ooptdd.engine.monitor import (
    PEND,
    SAT,
    VIOL,
    AbsentMonitor,
    ConformsMonitor,
    CountMonitor,
    HeartbeatMonitor,
    OrderMonitor,
    PresentMonitor,
    run_monitor,
)


def _ev(name, ts=None, **kw):
    e = {"event": name, **kw}
    if ts is not None:
        e["_timestamp"] = ts
    return e


# ── count: >= is monotone-true, == / <= are monotone-false only ────────────────
def test_ge_latches_sat_at_the_nth_match():
    m = CountMonitor("a", {}, ">=", 3)
    stream = [_ev("a"), _ev("b"), _ev("a"), _ev("x"), _ev("a"), _ev("a")]
    verdicts = []
    for i, e in enumerate(stream):
        m.step(e, i)
        verdicts.append(m.verdict)
    # PEND until the 3rd 'a' (stream index 4), then permanently SAT.
    assert verdicts == [PEND, PEND, PEND, PEND, SAT, SAT]
    assert m.settled_at == 4
    assert m.collapse(True)["passed"] is True


def test_eq_never_settles_sat_but_collapses_pass():
    # "exactly 2": a prefix can always gain another match, so ⊤ is never inevitable.
    m = CountMonitor("a", {}, "==", 2)
    run_monitor(m, [_ev("a"), _ev("a")], True)
    assert m.verdict == PEND  # undecided over the prefix...
    assert m.collapse(True)["passed"] is True  # ...but collapses to the count comparison


def test_eq_latches_viol_when_exceeded():
    m = CountMonitor("a", {}, "==", 1)
    res = run_monitor(m, [_ev("a"), _ev("a")], True)
    assert m.verdict == VIOL and m.settled_at == 1
    assert res["passed"] is False and res["got"] == 2


def test_le_latches_viol_only_on_overflow():
    m = CountMonitor("a", {}, "<=", 2)
    run_monitor(m, [_ev("a"), _ev("a"), _ev("a")], True)
    assert m.verdict == VIOL and m.settled_at == 2


# ── absent: monotone-false, latches on the first offender ──────────────────────
def test_absent_latches_viol_at_first_offender():
    m = AbsentMonitor([{"where": {"level": "ERROR"}}], {})
    stream = [_ev("ok"), _ev("ok"), _ev("boom", level="ERROR"), _ev("ok")]
    for i, e in enumerate(stream):
        m.step(e, i)
    assert m.verdict == VIOL and m.settled_at == 2
    assert m.collapse(True)["violations"] == 1


def test_absent_stays_pend_then_collapses_pass_when_clean():
    m = AbsentMonitor([{"where": {"level": "ERROR"}}], {})
    res = run_monitor(m, [_ev("a"), _ev("b")], True)
    assert m.verdict == PEND and res["passed"] is True


# ── present: monotone-true, latches when every matcher has fired ───────────────
def test_present_latches_sat_when_all_matchers_seen():
    m = PresentMonitor([{"event": "a"}, {"event": "b"}], {})
    stream = [_ev("a"), _ev("c"), _ev("b")]
    for i, e in enumerate(stream):
        m.step(e, i)
    assert m.verdict == SAT and m.settled_at == 2


# ── must_order: SAT when all in order, VIOL on a timestamp inversion ───────────
def test_order_latches_sat_when_sequence_completes_in_order():
    m = OrderMonitor(["a", "b", "c"])
    run_monitor(m, [_ev("a", 1), _ev("b", 2), _ev("c", 3)], True)
    assert m.verdict == SAT


def test_order_latches_viol_on_inversion():
    m = OrderMonitor(["a", "b"])
    # b's first occurrence (ts 1) precedes a's (ts 2) → inevitable inversion.
    run_monitor(m, [_ev("b", 1), _ev("a", 2)], True)
    assert m.verdict == VIOL


def test_order_within_bound_latches_viol_on_over_gap():
    m = OrderMonitor(["a", "b"], within_s=1)
    run_monitor(m, [_ev("a", 0), _ev("b", 5_000_000)], True)  # 5s gap > 1s
    assert m.verdict == VIOL
    assert m.collapse(True)["gaps_exceeded"] == ["a->b"]


# ── heartbeat: VIOL once a silence exceeds the period ──────────────────────────
def test_heartbeat_latches_viol_on_long_silence():
    m = HeartbeatMonitor("beat", every_s=1)
    run_monitor(m, [_ev("beat", 0), _ev("beat", 500_000), _ev("beat", 3_000_000)], True)
    assert m.verdict == VIOL  # the 2.5s gap breaks liveness


def test_heartbeat_pend_then_pass_when_lively():
    m = HeartbeatMonitor("beat", every_s=1)
    res = run_monitor(m, [_ev("beat", 0), _ev("beat", 500_000)], True)
    assert m.verdict == PEND and res["passed"] is True


# ── conforms: VIOL on the first nonconforming event ────────────────────────────
def test_conforms_latches_viol_on_first_bad_event():
    from ooptdd.domain.ontology import Ontology

    onto = Ontology.from_dict({"event_types": {
        "pay": {"required": ["amount"]}}})
    m = ConformsMonitor("pay", onto)
    run_monitor(m, [_ev("pay", amount=1), _ev("pay")], True)  # 2nd lacks amount
    assert m.verdict == VIOL and m.settled_at == 1


# ── the streaming verdict is surfaced through evaluate() ───────────────────────
def test_evaluate_surfaces_verdict_and_settled_at():
    reset()
    b = MemoryBackend()
    b.ship([{"cid": "c", "event": "a"} for _ in range(3)])
    res = evaluate(b, {"cid": "c", "expect": [{"event": "a", "op": ">=", "count": 2}]})
    chk = res["checks"][0]
    assert chk["verdict"] == SAT and isinstance(chk["settled_at"], int)
    assert chk["passed"] is True
    reset()


# ── incrementality: an unreachable store still collapses to not-ok ─────────────
def test_collapse_honours_reachable():
    m = CountMonitor("a", {}, ">=", 1)
    res = run_monitor(m, [_ev("a")], reachable=False)
    assert m.verdict == SAT  # the property is satisfied over the prefix...
    assert res["passed"] is False  # ...but an unreachable store is never a clean pass
