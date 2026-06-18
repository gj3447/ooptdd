"""Negative-wing fault-injection regression — prove ``OOPTDD_FORBID_ERRORS`` catches
an injected fault's ERROR log end to end.

This is the Toxiproxy chaos pattern (A13 of the ooptdd-oss prometheus cycle
``cycle-prom14-ooptdd-oss-20260618``): inject a fault → the app logs the ERROR it hit →
the forbid wing must turn the gate RED and hand the offending log back. It guards the
wing against regressing into the old green-and-noisy behaviour.

Two layers:
  * **always-on** — a stand-in "app under fault" ships its good lifecycle events and,
    when a fault is injected, the ERROR it would hit. No external dependency.
  * **real Toxiproxy** — skipped unless the ``toxiproxy`` client *and* a running
    Toxiproxy server are present; then a real network toxic produces a real timeout
    whose ERROR the forbid wing catches.

# KG: seed-ooptdd-negwing-toxiproxy-regression-20260618, finding_ooptddoss_f048d8f43c68
"""
from __future__ import annotations

import json

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import evaluate

_CID = "fault-cid"
# A minimal gate: the good lifecycle must complete. The ERROR-forbid is injected by
# OOPTDD_FORBID_ERRORS (the env default), so the spec itself stays oblivious to errors —
# exactly how a real consumer gate looks.
_GATE = {"cid": _CID, "expect": [{"present": [{"event": "request.end"}]}]}


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


@pytest.fixture
def _forbid_errors(monkeypatch):
    monkeypatch.setenv("OOPTDD_FORBID_ERRORS", "1")


def _app_under_fault(backend, cid, *, inject_fault: bool) -> None:
    """Stand-in for a service under chaos: it always ships its good lifecycle events;
    when a fault is injected it *also* logs the ERROR it hit (what Toxiproxy would cause).
    The good events still complete — the cycle is green-*and*-erroring, the exact case
    the positive-only wing would have passed."""
    backend.ship([{"cid": cid, "event": "request.start"}])
    if inject_fault:
        backend.ship([{
            "cid": cid, "event": "upstream.call", "level": "ERROR",
            "error": "ConnectTimeout: toxiproxy latency toxic 5000ms exceeded deadline",
        }])
    backend.ship([{"cid": cid, "event": "request.end", "verdict": "PASS"}])


def test_clean_run_is_green(_forbid_errors):
    b = MemoryBackend()
    _app_under_fault(b, _CID, inject_fault=False)
    res = evaluate(b, _GATE)
    assert res["ok"] is True  # good events arrived, no ERROR -> GREEN


def test_injected_fault_error_turns_gate_red_and_surfaces(_forbid_errors):
    b = MemoryBackend()
    _app_under_fault(b, _CID, inject_fault=True)
    res = evaluate(b, _GATE)
    assert res["ok"] is False  # the injected fault's ERROR flips it via the forbid wing
    absent = [c for c in res["checks"] if "absent" in c][0]
    assert absent["violations"] == 1
    assert "toxiproxy latency toxic" in json.dumps(absent["offending"])


def test_allowlisted_fault_stays_green(_forbid_errors):
    # an operator declares this fault class benign for the run -> allowlist exempts it,
    # so a known-tolerated chaos error does not flip the gate.
    b = MemoryBackend()
    _app_under_fault(b, _CID, inject_fault=True)
    gate = {**_GATE, "allow_errors": [{"event": "upstream.call"}]}
    res = evaluate(b, gate)
    assert res["ok"] is True


# ── real Toxiproxy (skipped unless client + server present) ───────────────────
def _toxiproxy_or_skip():
    """Return a connected Toxiproxy client, or skip if the client package or a running
    server is absent — so CI stays green offline while real chaos runs when available."""
    toxiproxy_mod = pytest.importorskip("toxiproxy")
    server = toxiproxy_mod.Toxiproxy()
    try:
        if not server.running():
            pytest.skip("Toxiproxy server not running (start `toxiproxy-server`)")
    except Exception as exc:  # noqa: BLE001 — connection refused etc. = nothing to test
        pytest.skip(f"Toxiproxy server unreachable: {exc!r}")
    return server


def test_real_toxiproxy_timeout_is_caught_by_forbid_wing(_forbid_errors):
    import socket

    server = _toxiproxy_or_skip()
    server.destroy_all()
    # Point a proxy at a black-holed upstream and time it out, so a connect through the
    # proxy's listen port reliably fails — the network fault we want the app to log.
    proxy = server.create(
        name="ooptdd_negwing", listen="127.0.0.1:0", upstream="10.255.255.1:9",
    )
    proxy.add_toxic(type="timeout", attributes={"timeout": 200})  # ms
    listen_host, listen_port = proxy.listen.rsplit(":", 1)
    b = MemoryBackend()
    b.ship([{"cid": _CID, "event": "request.start"}])
    try:
        with socket.create_connection((listen_host, int(listen_port)), timeout=1.0) as sock:
            sock.settimeout(1.0)
            sock.sendall(b"GET / HTTP/1.0\r\n\r\n")
            data = sock.recv(64)  # timeout toxic -> connection stalls then drops
            if data == b"":
                raise ConnectionError("upstream dropped (timeout toxic)")
        injected_error = None
    except (TimeoutError, OSError) as exc:
        injected_error = f"{type(exc).__name__}: {exc}"
    finally:
        proxy.destroy()
    assert injected_error is not None, "expected the timeout toxic to fault the connection"
    # the app logs the real fault it hit; the forbid wing must catch it
    b.ship([{"cid": _CID, "event": "upstream.call", "level": "ERROR", "error": injected_error}])
    b.ship([{"cid": _CID, "event": "request.end", "verdict": "PASS"}])
    res = evaluate(b, _GATE)
    assert res["ok"] is False
    absent = [c for c in res["checks"] if "absent" in c][0]
    assert absent["violations"] == 1
