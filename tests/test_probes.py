"""External-oracle probes — the "make an adapter as you need it" toolchain.

A probe is one method, probe(kind, selector, cid) -> ProbeResult. CallableProbe wraps a function,
FileProbe / HttpProbe are reference adapters to genuinely-independent sources, and a registry +
entry-point makes them pluggable like backends. The payoff: a probe corroborates a
require_corroboration gate, so a green can mean more than the system agreeing with itself.
"""
import json

from ooptdd.domain.ports import ExternalProbe
from ooptdd.engine.gate import evaluate_events
from ooptdd.probes import CallableProbe, ProbeRegistry, get_probe
from ooptdd.probes.file import FileProbe
from ooptdd.probes.http import HttpProbe


def test_callable_probe_wraps_a_function():
    p = CallableProbe(lambda kind, sel, cid: 42, separate_source=True)
    assert isinstance(p, ExternalProbe)
    r = p.probe("x", {}, "c")
    assert r.reachable and r.value == 42 and r.separate_source is True


def test_callable_probe_exception_is_unreachable_not_a_crash():
    def boom(kind, sel, cid):
        raise RuntimeError("source down")
    assert CallableProbe(boom).probe("x", {}, "c").reachable is False


def test_file_probe_reads_text_json_and_existence(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"order": {"total": 42}}))
    (tmp_path / "receipt.txt").write_text("shipped\n")
    fp = FileProbe(root=str(tmp_path))
    assert fp.probe("file", "receipt.txt", "c").value == "shipped"
    assert fp.probe("file", {"path": "manifest.json", "json": "order.total"}, "c").value == 42
    assert fp.probe("file", {"path": "missing.txt", "exists": True}, "c").value is False
    assert fp.probe("file", "missing.txt", "c").reachable is False  # unreadable -> inconclusive
    assert fp.probe("file", "receipt.txt", "c").separate_source is True


def test_http_probe_with_injected_opener():
    class _Resp:
        def __init__(self, body):
            self._b = body.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._b

    ok = HttpProbe(opener=lambda req, timeout: _Resp('{"balance": 99}'))
    assert ok.probe("http", {"url": "http://svc/x", "json": "balance"}, "c").value == 99

    def boom(req, timeout):
        raise OSError("service down")
    assert HttpProbe(opener=boom).probe("http", "http://svc/x", "c").reachable is False


def test_registry_builtins_register_and_unknown():
    assert {"file", "http"} <= set(ProbeRegistry().names())
    reg = ProbeRegistry()
    reg.register("mine", lambda **o: CallableProbe(lambda k, s, c: 1))
    assert isinstance(reg.resolve("mine"), CallableProbe)
    try:
        reg.resolve("nope")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_get_probe_resolves_a_builtin(tmp_path):
    assert isinstance(get_probe("file", root=str(tmp_path)), ExternalProbe)


def test_file_probe_corroborates_a_require_corroboration_gate(tmp_path, monkeypatch):
    # the end-to-end answer: write/pick an adapter, point an external: check at it via a selector,
    # and it CORROBORATES a self-consistency-only gate — no per-call custom plumbing.
    (tmp_path / "ledger.json").write_text(json.dumps({"charged": 42}))
    monkeypatch.setenv("OOPTDD_REQUIRE_CORROBORATION", "1")
    spec = {"expect": [{"external": {"kind": "file",
                                     "selector": {"path": "ledger.json", "json": "charged"},
                                     "want": 42}}]}
    res = evaluate_events(spec, [], reachable=True, complete=True, cid="c",
                          probe=get_probe("file", root=str(tmp_path)))
    assert res["ok"] is True and res["oracle"]["corroborated"] == 1
    # the ledger disagreeing with the claim -> RED, from the TERRITORY
    (tmp_path / "ledger.json").write_text(json.dumps({"charged": 7}))
    res2 = evaluate_events(spec, [], reachable=True, complete=True, cid="c",
                           probe=get_probe("file", root=str(tmp_path)))
    assert res2["ok"] is False
