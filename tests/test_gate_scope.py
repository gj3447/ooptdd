"""The honesty / charge-coverage signal.

A GREEN gate now reports WHAT and HOW HARD it asserted (``scope`` + per-check ``strength``), so
green is not misread as "the system is correct"; and a gate that asserts nothing GATING (every
check optional/pending) is ``vacuous`` — never a clean pass. These are signals, not an oracle:
higher strength is still author-vs-author (the spec value descends from the same mental model).
"""
from ooptdd.engine.gate import CHECK_REGISTRY, _strength, evaluate_events, green_banner


def _ev(name, ts=1, **f):
    return {"event": name, "_timestamp": ts, **f}


def _eval(expect, events):
    return evaluate_events({"expect": expect}, events, reachable=True, complete=True, cid="c")


def test_scope_block_counts_dispositions():
    res = _eval(
        [{"event": "a", "op": ">=", "count": 1},
         {"event": "b", "op": ">=", "count": 1, "optional": True},
         {"event": "c", "op": ">=", "count": 1, "pending": True}],
        [_ev("a")],
    )
    sc = res["scope"]
    assert (sc["gating"], sc["optional"], sc["pending"], sc["total"]) == (1, 1, 1, 3)
    assert sc["asserts_anything"] is True


def test_existence_only_gate_self_reports_zero_strength_and_warns():
    res = _eval([{"event": "a", "op": ">=", "count": 1}, {"event": "b", "op": ">=", "count": 1}],
                [_ev("a"), _ev("b")])
    assert res["ok"] is True
    assert res["scope"]["by_strength"] == {"existence-only": 2}
    banner = green_banner(res)
    assert "existence-only" in banner and "WARNING" in banner


def test_value_pinned_and_ordered_strength_visible():
    res = _eval(
        [{"event": "a", "where": {"k": "v"}, "op": ">=", "count": 1}, {"must_order": ["a", "b"]}],
        [_ev("a", 1, k="v"), _ev("b", 2)],
    )
    assert res["ok"] is True
    assert res["scope"]["by_strength"] == {"value-pinned": 1, "ordered": 1}
    assert "WARNING" not in green_banner(res)  # not all existence-only


def test_all_optional_gate_is_vacuous_not_green():
    res = _eval([{"event": "a", "op": ">=", "count": 1, "optional": True}], [_ev("a")])
    assert res["ok"] is False and res["vacuous"] is True and res["scope"]["gating"] == 0


def test_all_pending_on_empty_store_is_vacuous():
    # the confirmed residual false-green: all-pending over an EMPTY store used to return ok=True
    res = _eval([{"event": "x", "op": ">=", "count": 1, "pending": True}], [])
    assert res["ok"] is False and res["vacuous"] is True


def test_mixed_gate_stays_green_and_non_vacuous():
    res = _eval([{"event": "a", "op": ">=", "count": 1},
                 {"event": "b", "op": ">=", "count": 1, "optional": True}], [_ev("a")])
    assert res["ok"] is True and res["vacuous"] is False


def test_strength_is_total_over_registry_and_default():
    known = {"existence-only", "bounded", "value-pinned", "ordered", "forbid", "ratio",
             "liveness", "conformance", "threshold"}
    probes = {
        "absent": {"absent": [{"where": {"level": "ERROR"}}]},
        "heartbeat": {"heartbeat": "hb", "every_s": 5},
        "must_order": {"must_order": ["a", "b"]},
        "present": {"present": [{"event": "a"}]},
        "ratioMetric": {"ratioMetric": {"good": {"event": "a"}, "total": {"event": "b"}}},
        "conforms": {"conforms": "t"},
    }
    for rule in probes.values():
        assert _strength(rule) in known        # never KeyError / 'CUSTOM'
    assert _strength({"event": "a"}) in known  # default count
    assert _strength({"event": "a", "where": {"k": 1}}) == "value-pinned"
    assert _strength({"event": "a", "op": "==", "count": 1}) == "bounded"
    # every built-in registry key resolves (probes covers the canonical keys)
    assert set(probes) <= set(CHECK_REGISTRY)


def test_green_banner_is_pure_and_renders_none():
    res = _eval([{"event": "a", "op": ">=", "count": 1}], [_ev("a")])
    assert green_banner(res) == green_banner(res)  # pure / deterministic
    assert "closed-world over" in green_banner(res)
    assert "does NOT certify the system is correct" in green_banner(res)
    empty = {"scope": {"total": 0, "gating": 0, "optional": 0, "pending": 0, "by_strength": {}},
             "cid": "c"}
    assert "[by-strength: none]" in green_banner(empty)
