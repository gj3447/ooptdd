"""The gate @check extension seam must have an enforced, honest contract (audit gap-11).

A custom check composed from @check metadata had an undocumented result contract: a result
dict missing 'passed' surfaced as a deep KeyError, while honesty aggregators
silently scored any custom-shaped result as uncharged / existence-only / unnamed, built-ins were
unoverridable. The explicit immutable registry validates 'passed' at dispatch and lets custom
checks opt into honesty accounting without import-time or process-global mutation.
"""

import pytest

import ooptdd.engine.gate as gate
from ooptdd.domain.ports import ProbeResult


def _spec(expect, **kw):
    return {"cid": "seam-cid", "expect": expect, **kw}


def _registry(*handlers, base=None):
    return gate.compose_check_registry(
        gate.checks_from(*handlers),
        base=gate.core_check_registry() if base is None else base,
    )


def _eval(spec, events, *handlers, registry=None):
    active = _registry(*handlers) if registry is None and handlers else registry
    return gate.evaluate_events(spec, events, reachable=True, cid="seam-cid", registry=active)


# ── (a) validate the 'passed' contract at the dispatch site ─────────────────────────


def test_missing_passed_raises_named_error_not_keyerror():
    @gate.check("seam_nopass")
    def _h(events, rule, ctx):
        return {"seam_nopass": True}  # forgot 'passed'

    with pytest.raises(ValueError) as ei:
        _eval(_spec([{"seam_nopass": 1}]), [], _h)
    msg = str(ei.value)
    assert "seam_nopass" in msg and "passed" in msg and ("_h" in msg or "handler" in msg)


def test_missing_passed_is_not_a_bare_keyerror():
    """Revert-proof: a no-op fix that lets the KeyError fly must not pass."""

    @gate.check("seam_nopass2")
    def _h(events, rule, ctx):
        return {"seam_nopass2": True}

    with pytest.raises(Exception) as ei:
        _eval(_spec([{"seam_nopass2": 1}]), [], _h)
    assert not isinstance(ei.value, KeyError)


def test_wellformed_custom_check_still_evaluates():
    """No-false-alarm: a valid custom result must sail through — kills a reject-all fake fix."""

    @gate.check("seam_ok")
    def _h(events, rule, ctx):
        return {"passed": True, "got": 1}

    res = _eval(_spec([{"seam_ok": "x"}]), [], _h)
    assert res["ok"] is True
    assert any(c.get("passed") for c in res["checks"])


def test_custom_check_keeps_the_live_probe_but_kernel_receives_only_its_result():
    calls = []

    class Probe:
        def probe(self, kind, selector, cid):
            calls.append((kind, selector, cid))
            return ProbeResult(reachable=True, value=selector["value"])

    probe = Probe()

    @gate.check("seam_probe")
    def _h(events, rule, ctx):
        assert ctx.probe is probe
        first = ctx.probe.probe("first", {"value": 20}, ctx.cid)
        second = ctx.probe.probe("second", {"value": 22}, ctx.cid)
        return {"passed": first.value + second.value == 42, "got": 2}

    result = gate.evaluate_events(
        _spec([{"seam_probe": True}]),
        [],
        reachable=True,
        probe=probe,
        registry=_registry(_h),
    )

    assert result["ok"] is True
    assert calls == [
        ("first", {"value": 20}, "seam-cid"),
        ("second", {"value": 22}, "seam-cid"),
    ]


def test_custom_external_override_controls_probe_calls_without_builtin_preprobe():
    calls = []

    class Probe:
        def probe(self, kind, selector, cid):
            calls.append((kind, selector, cid))
            return ProbeResult(reachable=True, value=42)

    @gate.check("external")
    def _override(events, rule, ctx):
        result = ctx.probe.probe("custom", {"owned": True}, ctx.cid)
        return {"passed": result.value == 42, "value": result.value}

    base = {key: value for key, value in gate.core_check_registry().items() if key != "external"}
    result = gate.evaluate_events(
        _spec([{"external": {"kind": "builtin", "selector": {"id": 7}, "want": 42}}]),
        [],
        reachable=True,
        probe=Probe(),
        registry=_registry(_override, base=base),
    )

    assert result["ok"] is True
    assert calls == [("custom", {"owned": True}, "seam-cid")]


# ── (b) let a custom check opt into the honesty accounting ──────────────────────────


def test_custom_check_declares_charged_strength_events_label():
    @gate.check("seam_rich")
    def _h(events, rule, ctx):
        return {"seam_rich": "x", "passed": True, "charged": True}

    res = _eval(
        _spec([{"seam_rich": "x", "strength": "value-pinned", "events": ["x"], "label": "rich-x"}]),
        [{"event": "x"}],
        _h,
    )
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

    res = _eval(_spec([{"seam_poor": "y", "label": "poor-y"}]), [{"event": "y"}], _h)
    assert res["scope"]["charged"] == 0
    assert "poor-y" in res["scope"]["uncharged"]


# ── (c) composition is immutable and isolated ──────────────────────────────────────


def test_decorator_is_metadata_only_until_explicit_composition():
    @gate.check("seam_u")
    def _h(events, rule, ctx):
        return {"passed": True}

    assert "seam_u" not in gate.CHECK_REGISTRY
    assert _registry(_h)["seam_u"] is _h
    assert "seam_u" not in gate.CHECK_REGISTRY


def test_core_registry_is_not_overridable_by_ambient_mutation():
    with pytest.raises(TypeError):
        gate.CHECK_REGISTRY["present"] = lambda *_: {"passed": True}


# ── shared regression guard: built-in behavior must be untouched (green before AND after) ──


def test_builtin_charged_strength_label_unchanged():
    res = _eval(
        _spec([{"present": [{"event": "a"}]}, {"absent": [{"where": {"level": "ERROR"}}]}]),
        [{"event": "a"}],
    )
    sc = res["scope"]
    assert sc["by_strength"].get("existence-only") == 1 and sc["by_strength"].get("forbid") == 1
    assert sc["charged"] == 1  # present saw 'a'; absent saw no offender (uncharged)
    assert gate._label({"present": ["a"], "missing": []}) == "present:a"
    assert gate._label({"absent": ["x"]}) == "absent:x"
