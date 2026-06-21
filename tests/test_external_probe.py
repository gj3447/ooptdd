"""The `external:` check — the independent-oracle port (PROM07 roadmap, axis B part B).

It is the one verdict input that does NOT come from the system's own emit: it asserts against a
fact read from the TERRITORY (a DB row, a file, a second collector) via an ExternalProbe, so a
green there means more than self-consistency. Honesty: a MISSING probe is a loud misconfiguration
(never a silent green); an UNREACHABLE probe is inconclusive (never a strict fail).
"""
from ooptdd.domain.ports import ExternalProbe, ProbeResult
from ooptdd.engine.gate import evaluate_events


class _FakeProbe:
    """An ExternalProbe that returns a fixed ProbeResult — stands in for a real DB/fs/http probe."""

    def __init__(self, *, reachable=True, value=None, complete=True):
        self._r, self._v, self._c = reachable, value, complete
        self.calls: list = []

    def probe(self, kind, selector, cid):
        self.calls.append((kind, selector, cid))
        return ProbeResult(reachable=self._r, value=self._v, complete=self._c)


def _eval(rule, events, **kw):
    return evaluate_events({"expect": [rule]}, events, reachable=True, complete=True,
                           cid="c", **kw)


def test_fakeprobe_satisfies_the_protocol():
    assert isinstance(_FakeProbe(), ExternalProbe)


def test_external_no_probe_is_loud_misconfig_not_silent_green():
    # an external assertion with no probe configured must NOT pass silently
    res = _eval({"external": {"kind": "db_row", "selector": {}, "want": 42}}, [])
    assert res["ok"] is False
    assert res["checks"][0]["reason"] == "no_external_probe_configured"


def test_external_probe_value_match_is_green_and_highest_strength():
    p = _FakeProbe(value=42)
    res = _eval({"external": {"kind": "db_row", "selector": {"table": "pay"}, "want": 42}}, [],
                probe=p)
    assert res["ok"] is True
    assert res["scope"]["by_strength"] == {"external": 1}
    assert p.calls == [("db_row", {"table": "pay"}, "c")]  # the probe was asked, with the cid


def test_external_probe_value_mismatch_is_red():
    res = _eval({"external": {"kind": "db_row", "selector": {}, "want": 42}}, [],
                probe=_FakeProbe(value=7))
    assert res["ok"] is False


def test_external_tolerance_and_inequality():
    assert _eval({"external": {"kind": "x", "selector": {}, "want": 42.0, "tol": 0.1}}, [],
                 probe=_FakeProbe(value=42.05))["ok"] is True   # within tol
    assert _eval({"external": {"kind": "x", "selector": {}, "want": 42.0, "tol": 0.01}}, [],
                 probe=_FakeProbe(value=42.05))["ok"] is False  # outside tol
    assert _eval({"external": {"kind": "x", "selector": {}, "op": ">", "want": 10}}, [],
                 probe=_FakeProbe(value=11))["ok"] is True


def test_external_exists_check_when_no_want():
    assert _eval({"external": {"kind": "file", "selector": "/x"}}, [],
                 probe=_FakeProbe(value="present"))["ok"] is True
    assert _eval({"external": {"kind": "file", "selector": "/x"}}, [],
                 probe=_FakeProbe(value=None))["ok"] is False  # fact absent


def test_external_probe_unreachable_is_inconclusive_not_red():
    res = _eval({"external": {"kind": "db_row", "selector": {}, "want": 42}}, [],
                probe=_FakeProbe(reachable=False))
    assert res["ok"] is False
    assert res["probe_reachable"] is False
    assert res["checks"][0]["reason"] == "external_probe_unreachable"


def test_external_is_independent_of_self_emit():
    # the whole point: the external fact agrees (42) even though the system emitted NOTHING.
    # A self-emitted check would be `absent` here; the external check is green FROM THE TERRITORY.
    res = _eval({"external": {"kind": "db_row", "selector": {}, "want": 42}}, [],
                probe=_FakeProbe(value=42))
    assert res["ok"] is True
