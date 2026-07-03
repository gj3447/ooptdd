"""RED-side toolchain: stable check identity + programmatic diagnosis (audit gap-19).

Check results carried no stable `kind`, so a RED consumer had to string-match the raw shape
(and the default count check has no self-identifying payload key at all). There was no public
failed_checks(), no assert_gate_red, no explain — the RED-diagnosis kernel lived in ooptdd-loop.
This pins a stable kind on every check plus the diagnosis helpers.
"""
import pytest

from ooptdd.backends import MemoryBackend, memory_reset
from ooptdd.domain.model import correlation_keys
from ooptdd.engine.gate import CHECK_REGISTRY, check, evaluate


@pytest.fixture(autouse=True)
def _reset_store():
    memory_reset()
    yield
    memory_reset()


def _ship(cid, *events):
    MemoryBackend().ship([{**correlation_keys(cid), **e} for e in events])


# ── GUARD 1: defect-characterization — green before AND after (module-top imports only) ──
def test_default_count_check_has_no_self_identifying_payload_key():
    """The exact reason string-matching a check is unreliable: the default count check exposes
    event/where/op/want/got/passed but nothing literally named 'count'."""
    _ship("c1", {"event": "noise"})
    res = evaluate(MemoryBackend(),
                   {"cid": "c1", "expect": [{"event": "pay", "op": ">=", "count": 1}]})
    c = res["checks"][0]
    assert res["ok"] is False and c["passed"] is False
    assert "count" not in c


# ── GUARD 2: the fix flips red -> green (new symbols lazy-imported) ───────────────────
def test_every_check_carries_a_stable_kind():
    from ooptdd.engine.gate import failed_checks
    _ship("c2", {"event": "noise"})
    res = evaluate(MemoryBackend(), {"cid": "c2", "expect": [
        {"must_order": ["x", "y"]},
        {"event": "pay", "op": ">=", "count": 1},
    ]})
    assert {c["kind"] for c in res["checks"]} == {"must_order", "count"}
    assert {c["kind"] for c in failed_checks(res)} == {"must_order", "count"}


def test_custom_check_kind_is_its_registry_key():
    from ooptdd.engine.gate import failed_checks  # noqa: F401 — imported to prove the fix loaded

    @check("seam_kind")
    def _h(events, rule, ctx):
        return {"passed": False}

    try:
        res = evaluate(MemoryBackend(), {"cid": "c3", "expect": [{"seam_kind": "v"}]})
        assert res["checks"][0]["kind"] == "seam_kind"
    finally:
        CHECK_REGISTRY.pop("seam_kind", None)


def test_failed_checks_excludes_optional_and_pending():
    from ooptdd.engine.gate import failed_checks
    _ship("c4", {"event": "noise"})
    res = evaluate(MemoryBackend(), {"cid": "c4", "expect": [
        {"event": "pay", "op": ">=", "count": 1},                      # gating miss
        {"event": "opt", "op": ">=", "count": 1, "optional": True},    # optional miss
    ]})
    fails = failed_checks(res)
    assert len(fails) == 1 and fails[0]["kind"] == "count"


# ── GUARD 3: no-false-alarm / revert-proof ───────────────────────────────────────────
def test_assert_gate_red_returns_on_red_and_raises_on_green():
    from ooptdd.assertions import TraceAssertionError, assert_gate_red
    _ship("c5", {"event": "a"})
    with pytest.raises(TraceAssertionError):  # GREEN gate must not satisfy "assert red"
        assert_gate_red({"cid": "c5", "expect": [{"event": "a", "op": ">=", "count": 1}]},
                        backend=MemoryBackend())
    res = assert_gate_red({"cid": "c5", "expect": [{"event": "missing", "op": ">=", "count": 1}]},
                          backend=MemoryBackend())
    assert res["ok"] is False


def test_explain_names_the_failing_checks_by_kind():
    from ooptdd.assertions import explain
    _ship("c6", {"event": "noise"})
    res = evaluate(MemoryBackend(),
                   {"cid": "c6", "expect": [{"event": "pay", "op": ">=", "count": 1}]})
    line = explain(res)
    assert line.startswith("RED") and "count" in line
