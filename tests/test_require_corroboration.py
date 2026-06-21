"""`require_corroboration` — promote the single_authority SIGNAL to a GATE (PROM08 L2/L3).

When on (spec key or env OOPTDD_REQUIRE_CORROBORATION, default OFF), a gate whose every gating
check is the system's own self-report (zero separate-source corroboration) is NOT a clean pass:
ok=False, uncorroborated=True. Only a separate_source=True ExternalProbe counts as corroboration —
a probe re-reading the system's own store is relocation, not independence.
"""
from ooptdd.domain.ports import ProbeResult
from ooptdd.engine.gate import evaluate_events


class _Probe:
    def __init__(self, value, separate_source):
        self.value, self.separate_source = value, separate_source

    def probe(self, kind, selector, cid):
        return ProbeResult(reachable=True, value=self.value, separate_source=self.separate_source)


def _eval(spec, events, **kw):
    return evaluate_events(spec, events, reachable=True, complete=True, cid="c", **kw)


_SELF = {"expect": [{"event": "a", "op": ">=", "count": 1}]}
_EXT = {"expect": [{"external": {"kind": "db", "selector": {}, "want": 42}}]}


def test_off_by_default_a_self_consistency_gate_is_green():
    res = _eval(_SELF, [{"event": "a", "_timestamp": 1}])
    assert res["ok"] is True and res["uncorroborated"] is False
    assert res["oracle"]["enforced"] is False


def test_env_on_blocks_a_single_authority_gate(monkeypatch):
    monkeypatch.setenv("OOPTDD_REQUIRE_CORROBORATION", "1")
    res = _eval(_SELF, [{"event": "a", "_timestamp": 1}])
    assert res["ok"] is False and res["uncorroborated"] is True
    assert res["oracle"]["enforced"] is True


def test_spec_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("OOPTDD_REQUIRE_CORROBORATION", "1")
    res = _eval({**_SELF, "require_corroboration": False}, [{"event": "a", "_timestamp": 1}])
    assert res["ok"] is True and res["uncorroborated"] is False


def test_separate_source_external_satisfies(monkeypatch):
    monkeypatch.setenv("OOPTDD_REQUIRE_CORROBORATION", "1")
    res = _eval(_EXT, [], probe=_Probe(42, separate_source=True))
    assert res["ok"] is True
    assert res["oracle"]["corroborated"] == 1 and res["oracle"]["single_authority"] is False


def test_non_separate_source_probe_is_refused_as_corroboration(monkeypatch):
    monkeypatch.setenv("OOPTDD_REQUIRE_CORROBORATION", "1")
    res = _eval(_EXT, [], probe=_Probe(42, separate_source=False))
    assert res["ok"] is False and res["uncorroborated"] is True
    assert res["oracle"]["corroborated"] == 0
    assert res["checks"][0]["passed"] is True  # the check passed; it just doesn't CORROBORATE


def test_unreachable_probe_is_inconclusive_not_uncorroborated(monkeypatch):
    monkeypatch.setenv("OOPTDD_REQUIRE_CORROBORATION", "1")

    class _Unreachable:
        def probe(self, kind, selector, cid):
            return ProbeResult(reachable=False)

    res = _eval(_EXT, [], probe=_Unreachable())
    assert res["probe_reachable"] is False  # infra, not a corroboration misconfiguration
