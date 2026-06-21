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


# ── stream charge-coverage (the measured closed-world gap) ─────────────────────
def test_stream_coverage_surfaces_arrived_unobserved_event_types():
    # 3 event types arrive; the gate names only `a` -> coverage 1/3, 2 arrived UNOBSERVED
    res = _eval([{"event": "a", "op": ">=", "count": 1}], [_ev("a"), _ev("b"), _ev("c")])
    sc = res["scope"]
    assert sc["observed_event_types"] == 3 and sc["named_event_types"] == 1
    assert sorted(sc["unasserted_observed"]) == ["b", "c"]
    assert abs(sc["stream_coverage"] - 1 / 3) < 1e-9
    banner = green_banner(res)
    assert "Stream-coverage: 1/3" in banner and "UNOBSERVED: b,c" in banner


def test_stream_coverage_full_when_gate_names_all_arrived():
    res = _eval([{"present": [{"event": "a"}, {"event": "b"}]}], [_ev("a"), _ev("b")])
    sc = res["scope"]
    assert sc["stream_coverage"] == 1.0 and sc["unasserted_observed"] == []


def test_stream_coverage_none_when_nothing_arrived():
    res = _eval([{"event": "a", "op": ">=", "count": 1, "pending": True}], [])
    assert res["scope"]["stream_coverage"] is None  # no events -> no coverage, no crash


# ── oracle provenance (single-authority boundary made visible) ─────────────────
def test_oracle_single_authority_when_all_self_emitted():
    res = _eval([{"event": "a", "where": {"k": "v"}, "op": ">=", "count": 1}], [_ev("a", 1, k="v")])
    assert res["oracle"]["single_authority"] is True
    assert res["oracle"]["corroborated"] == 0 and res["oracle"]["derived_self"] == 1
    assert "single authority" in green_banner(res)


def test_oracle_corroborated_only_by_a_separate_source_external_check():
    class _P:
        def __init__(self, separate):
            self.separate = separate

        def probe(self, kind, selector, cid):
            from ooptdd.domain.ports import ProbeResult
            return ProbeResult(reachable=True, value=42, separate_source=self.separate)

    spec = {"expect": [{"external": {"kind": "x", "selector": {}, "want": 42}}]}
    # a separate-source probe corroborates
    res = evaluate_events(spec, [], reachable=True, complete=True, cid="c", probe=_P(True))
    assert res["oracle"]["corroborated"] == 1 and res["oracle"]["single_authority"] is False
    assert "independently corroborated" in green_banner(res)
    # a probe re-reading the system's own store does NOT (relocation, not independence)
    res2 = evaluate_events(spec, [], reachable=True, complete=True, cid="c", probe=_P(False))
    assert res2["oracle"]["corroborated"] == 0 and res2["oracle"]["single_authority"] is True


# ── charge-ratio (evidenced vs absence-passing) ───────────────────────────────
def test_charge_ratio_distinguishes_evidenced_from_absence_passing():
    res = _eval([{"event": "a", "op": ">=", "count": 1},        # matched -> charged
                 {"absent": [{"where": {"level": "ERROR"}}]}],   # nothing matched -> uncharged-pass
                [_ev("a", 1)])
    sc = res["scope"]
    assert sc["charged"] == 1 and sc["gating"] == 2 and abs(sc["charge_ratio"] - 0.5) < 1e-9
    assert "Charge: 1/2" in green_banner(res)
