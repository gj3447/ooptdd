"""require_independent_store — make caps.independent a GATE, not dead data.

Grill A1 (2026-07-22 forgery review): a SUT that ships the gate's named events
to its own in-process store forges `ok=True` doing no work — and `caps.independent`
was declared on every backend but never consulted in the verdict path. This flag
promotes that signal to a verdict (see docs/THREAT_MODEL.md).
"""
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.domain.ports import BackendCaps
from ooptdd.engine.gate import evaluate, evaluate_events
from ooptdd.engine.verify import verify_gate

CID = "indep-cid"


def _spec(**kw):
    return {"cid": CID, "expect": [{"event": "boot", "op": "gte", "target": 1}], **kw}


def _ship(backend):
    backend.ship([{"event": "boot", "cid": CID, "correlation_id": CID, "cycle_id": CID}])


class _IndependentBackend(MemoryBackend):
    caps = BackendCaps(queryable=True, paginates=True, supports_where=True, independent=True)


def test_default_off_non_independent_store_still_green():
    """Opt-in: the flag must not change default behavior (memory green stays green)."""
    reset()
    b = MemoryBackend()
    _ship(b)
    assert evaluate(b, _spec())["ok"]
    reset()


def test_non_independent_store_without_corroboration_is_red():
    reset()
    b = MemoryBackend()  # caps.independent is False
    _ship(b)
    res = evaluate(b, _spec(require_independent_store=True))
    assert not res["ok"] and res["dependent_store"] is True
    reset()


def test_verify_path_also_enforces_non_independent_store():
    """The polling path must carry the same typed caps as one-shot evaluate()."""
    reset()
    b = MemoryBackend()
    _ship(b)
    res = verify_gate(b, CID, _spec(require_independent_store=True), retries=1, delay=0)
    assert res["verdict"] == "absent"
    assert res["gate"]["dependent_store"] is True
    reset()


def test_independent_store_passes_the_flag():
    reset()
    b = _IndependentBackend()
    _ship(b)
    res = evaluate(b, _spec(require_independent_store=True))
    assert res["ok"] and res["dependent_store"] is False
    reset()


def test_unknown_independence_never_invents_a_red():
    """A duck-typed backend with no caps → emit_independent=None → the flag cannot fire
    (never manufacture a RED from missing metadata)."""
    res = evaluate_events(
        _spec(require_independent_store=True),
        [{"event": "boot", "cid": CID, "_timestamp": 0}],
        reachable=True, cid=CID, emit_independent=None)
    assert res["ok"] and res["dependent_store"] is False


def test_env_var_also_arms_it(monkeypatch):
    monkeypatch.setenv("OOPTDD_REQUIRE_INDEPENDENT", "1")
    reset()
    b = MemoryBackend()
    _ship(b)
    assert not evaluate(b, _spec())["ok"]
    reset()


def test_corroboration_rescues_a_dependent_store():
    """The escape hatch the flag documents: a separate-source corroborated check makes
    even a non-independent store an honest pass."""
    class _Probe:
        def probe(self, kind, selector, cid):
            from ooptdd.domain.ports import ProbeResult
            return ProbeResult(value=1, reachable=True, separate_source=True,
                               derived_identity="external:ledger")
    reset()
    b = MemoryBackend()
    _ship(b)
    spec = _spec(require_independent_store=True, expect=[
        {"event": "boot", "op": "gte", "target": 1},
        {"external": {"kind": "row", "selector": {}, "want": 1}},
    ])
    res = evaluate(b, spec, probe=_Probe())
    assert res["oracle"]["corroborated"] >= 1
    assert res["dependent_store"] is False and res["ok"]
    reset()
