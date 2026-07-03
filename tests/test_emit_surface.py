"""A generic emit-side surface in core + gate-assertable service (audit gap-02).

Core shipped no generic envelope builder or Emitter, so consumers hand-rolled a per-verb flat-dict
envelope (12+ near-identical copies across bhgman/omd/apt-engine/p333/ooptdd-loop), correlation_keys
was reachable only via the undocumented ooptdd.model shim, and service drift was not assertable by
a gate. This pins build_event/Emitter at the root and the opt-in pin_service gate key.
"""
import threading

from ooptdd import MemoryBackend, evaluate, memory_reset
from ooptdd.domain.model import ENVELOPE_SPEC_VERSION, build_session_start, correlation_keys

_WIDE = dict(since_us=0, until_us=10 ** 19)


def _pin_spec(cid, pin="omd.tests"):
    return {"cid": cid, "expect": [{"event": "cycle", "op": ">=", "count": 2}], "pin_service": pin}


# ── GUARD 1: defect-characterization — green before AND after ────────────────────────
def test_correlation_keys_stays_exactly_three_aliases():
    """build_event must LAYER on this primitive, never redefine it or leak spec_version in."""
    assert correlation_keys("c") == {"cid": "c", "correlation_id": "c", "cycle_id": "c"}


# ── GUARD 2: the fix flips red -> green (new symbols lazy-imported) ───────────────────
def test_root_exports_the_emit_surface():
    import ooptdd
    from ooptdd.domain import model as d_model
    for name in ("build_event", "Emitter", "correlation_keys"):
        assert name in ooptdd.__all__ and hasattr(ooptdd, name)
    assert ooptdd.correlation_keys is d_model.correlation_keys  # no shadow copy


def test_build_event_shape_and_spec_version_consistency():
    from ooptdd import build_event
    ev = build_event("cid-1", "cycle", service="omd.tests", verdict="PASS")
    assert ev["cid"] == ev["correlation_id"] == ev["cycle_id"] == "cid-1"
    assert ev["service"] == "omd.tests" and ev["event"] == "cycle"
    assert ev["verdict"] == "PASS"  # arbitrary attrs pass through
    # one spec_version value, identical to the existing builders (no inconsistent duplication)
    assert ev["spec_version"] == ENVELOPE_SPEC_VERSION == build_session_start("c")["spec_version"]


def test_emitter_ships_and_a_gate_is_green():
    from ooptdd import Emitter
    memory_reset()
    b = MemoryBackend()
    em = Emitter(b, service="omd.tests")
    em.emit("cycle", "e-cid")
    em.emit("cycle", "e-cid")
    res = evaluate(b, {"cid": "e-cid", "expect": [{"event": "cycle", "op": ">=", "count": 2}]})
    assert res["ok"] is True
    stored = b.query("e-cid", **_WIDE).events
    assert all(r["service"] == "omd.tests" and "spec_version" in r for r in stored)


def test_emitter_does_not_deadlock_under_concurrency():
    from ooptdd import Emitter
    memory_reset()
    b = MemoryBackend()
    em = Emitter(b, service="load.svc")
    em.emit("tick", "seq")          # sequential double-emit: the lock must release and re-acquire
    em.emit("tick", "seq")

    def worker(i):
        for _ in range(50):
            em.emit("tick", f"conc-{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)  # no deadlock
    cids = ["seq", "conc-0", "conc-1", "conc-2", "conc-3"]
    total = sum(len(b.query(cid, **_WIDE).events) for cid in cids)
    assert total == 202  # 2 sequential + 4x50 concurrent, none lost


# ── GUARD 3: pin_service — no-false-alarm + revert-proof ─────────────────────────────
def _drifted_stream(cid, second_service=None, drop_service=False):
    from ooptdd import Emitter
    memory_reset()
    b = MemoryBackend()
    Emitter(b, service="omd.tests").emit("cycle", cid)
    if drop_service:
        b.ship([{**correlation_keys(cid), "event": "cycle"}])           # no service key at all
    else:
        Emitter(b, service=second_service or "omd.tests").emit("cycle", cid)
    return b


def test_pin_service_is_green_when_every_event_matches():
    b = _drifted_stream("p1")  # both from omd.tests
    assert evaluate(b, _pin_spec("p1"))["ok"] is True


def test_pin_service_reds_on_a_wrong_service():
    b = _drifted_stream("p2", second_service="rogue.svc")
    assert evaluate(b, _pin_spec("p2"))["ok"] is False


def test_pin_service_reds_on_a_missing_service():
    b = _drifted_stream("p3", drop_service=True)
    assert evaluate(b, _pin_spec("p3"))["ok"] is False


def test_without_pin_service_the_drift_is_invisible():
    """Proves pin_service is the discriminator, not the count check."""
    b = _drifted_stream("p4", second_service="rogue.svc")
    spec = {"cid": "p4", "expect": [{"event": "cycle", "op": ">=", "count": 2}]}
    assert evaluate(b, spec)["ok"] is True
