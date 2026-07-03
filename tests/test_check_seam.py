"""The gate @check extension seam must have an enforced, honest contract (audit gap-11).

A custom check registered via @check had an undocumented result contract: a result dict missing
'passed' surfaced as a deep KeyError, honesty aggregators (charged/strength/label/named-events)
silently scored any custom-shaped result as uncharged / existence-only / unnamed, built-ins were
unoverridable, and there was no unregister. This drove ooptdd-loop to fork the evaluator. The fix
is additive and guarded: validate 'passed' at the dispatch site, let a custom check opt into the
honesty accounting via optional keys, and add unregister — all without changing built-in behavior.
"""
import pytest

import ooptdd.engine.gate as gate


@pytest.fixture(autouse=True)
def _registry_snapshot():
    """Custom registrations / built-in overrides must never leak across tests."""
    saved = dict(gate.CHECK_REGISTRY)
    yield
    gate.CHECK_REGISTRY.clear()
    gate.CHECK_REGISTRY.update(saved)


def _spec(expect, **kw):
    return {"cid": "seam-cid", "expect": expect, **kw}


def _eval(spec, events):
    return gate.evaluate_events(spec, events, reachable=True, cid="seam-cid")


# ── (a) validate the 'passed' contract at the dispatch site ─────────────────────────

def test_missing_passed_raises_named_error_not_keyerror():
    @gate.check("seam_nopass")
    def _h(events, rule, ctx):
        return {"seam_nopass": True}  # forgot 'passed'

    with pytest.raises(ValueError) as ei:
        _eval(_spec([{"seam_nopass": 1}]), [])
    msg = str(ei.value)
    assert "seam_nopass" in msg and "passed" in msg and ("_h" in msg or "handler" in msg)


def test_missing_passed_is_not_a_bare_keyerror():
    """Revert-proof: a no-op fix that lets the KeyError fly must not pass."""
    @gate.check("seam_nopass2")
    def _h(events, rule, ctx):
        return {"seam_nopass2": True}

    with pytest.raises(Exception) as ei:
        _eval(_spec([{"seam_nopass2": 1}]), [])
    assert not isinstance(ei.value, KeyError)


def test_wellformed_custom_check_still_evaluates():
    """No-false-alarm: a valid custom result must sail through — kills a reject-all fake fix."""
    @gate.check("seam_ok")
    def _h(events, rule, ctx):
        return {"passed": True, "got": 1}

    res = _eval(_spec([{"seam_ok": "x"}]), [])
    assert res["ok"] is True
    assert any(c.get("passed") for c in res["checks"])


# ── (b) let a custom check opt into the honesty accounting ──────────────────────────

def test_custom_check_declares_charged_strength_events_label():
    @gate.check("seam_rich")
    def _h(events, rule, ctx):
        return {"seam_rich": "x", "passed": True, "charged": True}

    res = _eval(_spec([{"seam_rich": "x", "strength": "value-pinned",
                        "events": ["x"], "label": "rich-x"}]), [{"event": "x"}])
    sc = res["scope"]
    assert sc["by_strength"].get("value-pinned") == 1
    assert sc["charged"] == 1 and sc["charge_ratio"] == 1.0 and sc["uncharged"] == []
    assert sc["named_event_types"] == 1 and sc["unasserted_observed"] == []
    assert gate._label({"label": "rich-x"}) == "rich-x"


def test_custom_check_declaring_charged_false_stays_uncharged():
    """Revert-proof for _check_charged: a declared charged=False must NOT count as charged —
    kills a constant-True short-circuit."""
    @gate.check("seam_poor")
    def _h(events, rule, ctx):
        return {"seam_poor": "y", "passed": True, "charged": False}

    res = _eval(_spec([{"seam_poor": "y", "label": "poor-y"}]), [{"event": "y"}])
    assert res["scope"]["charged"] == 0
    assert "poor-y" in res["scope"]["uncharged"]


# ── (c) unregister ──────────────────────────────────────────────────────────────────

def test_unregister_removes_and_allows_reregister():
    assert hasattr(gate, "unregister")

    @gate.check("seam_u")
    def _h(events, rule, ctx):
        return {"passed": True}

    assert "seam_u" in gate.CHECK_REGISTRY
    assert gate.unregister("seam_u") is _h
    assert "seam_u" not in gate.CHECK_REGISTRY

    @gate.check("seam_u")  # must not raise the duplicate-key guard now
    def _h2(events, rule, ctx):
        return {"passed": True}

    assert gate.CHECK_REGISTRY["seam_u"] is _h2


def test_builtin_is_overridable_via_unregister():
    orig = gate.CHECK_REGISTRY["present"]
    try:
        assert gate.unregister("present") is orig

        @gate.check("present")  # re-register a stub without the duplicate-key ValueError
        def _stub(events, rule, ctx):
            return {"passed": True}

        assert gate.CHECK_REGISTRY["present"] is _stub
    finally:
        gate.CHECK_REGISTRY["present"] = orig


def test_unregister_missing_key_is_a_noop():
    """Revert-proof: a fake unregister that clears the dict must not pass."""
    n = len(gate.CHECK_REGISTRY)
    assert gate.unregister("never_registered_xyz") is None
    assert len(gate.CHECK_REGISTRY) == n


# ── shared regression guard: built-in behavior must be untouched (green before AND after) ──

def test_builtin_charged_strength_label_unchanged():
    res = _eval(_spec([{"present": [{"event": "a"}]},
                       {"absent": [{"where": {"level": "ERROR"}}]}]), [{"event": "a"}])
    sc = res["scope"]
    assert sc["by_strength"].get("existence-only") == 1 and sc["by_strength"].get("forbid") == 1
    assert sc["charged"] == 1  # present saw 'a'; absent saw no offender (uncharged)
    assert gate._label({"present": ["a"], "missing": []}) == "present:a"
    assert gate._label({"absent": ["x"]}) == "absent:x"
