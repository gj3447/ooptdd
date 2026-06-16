"""Killer demo — "it returned ok, but did the work actually happen?"

Run it:  pytest examples/test_order_pipeline.py -s

No infrastructure required: everything uses the in-memory backend. The same test
runs unchanged against OpenObserve/OTLP by switching the backend.

The story (the real 22-hour bug that motivated ooptdd): a function reports
success, the logs say "shipped OK", and yet the events never landed. A normal
test that checks the return value is GREEN and blind. ooptdd reads the store back
and catches it.
"""
import os

import pytest
from app import process_order  # examples/ is on sys.path under pytest's prepend mode

from ooptdd.backends import MemoryBackend, memory_reset
from ooptdd.gate import evaluate, load_gate
from ooptdd.verify import verify_trace

GATE = load_gate(os.path.join(os.path.dirname(__file__), "gates", "order_pipeline.yaml"))


@pytest.fixture(autouse=True)
def _fresh():
    memory_reset()
    yield
    memory_reset()


def test_healthy_backend_is_green(monkeypatch):
    monkeypatch.setenv("OOPTDD_CID", "order-42")
    backend = MemoryBackend()

    result = process_order(backend, "order-42", items=3)
    assert result["status"] == "ok"  # the function's self-report

    # ooptdd's real assertion: the events arrived in the store.
    gate = evaluate(backend, GATE)
    assert gate["ok"], gate["checks"]


def test_silent_ingest_loss_is_caught(monkeypatch):
    monkeypatch.setenv("OOPTDD_CID", "order-43")
    # The backend accepts every ship() and silently drops it (simulating the
    # 401-that-nobody-noticed). The function STILL returns ok.
    backend = MemoryBackend(drop=True)

    result = process_order(backend, "order-43", items=3)
    assert result["status"] == "ok"  # <-- the lie a normal test would believe

    gate = evaluate(backend, GATE)
    assert gate["reachable"] is True       # the store answered...
    assert gate["ok"] is False             # ...but the events never arrived.
    # Every expected event is missing — exactly the failure a return-value test
    # cannot see.
    assert all(c["got"] == 0 for c in gate["checks"])


def test_verify_trace_verdicts(monkeypatch):
    # present vs absent, demonstrated directly.
    backend_ok = MemoryBackend()
    backend_ok.ship([{"cid": "x", "event": "test_session", "total": 1, "service": "s"}])
    assert verify_trace(backend_ok, "x", expect_total=1, retries=1)["verdict"] == "present"

    backend_lost = MemoryBackend(drop=True)
    backend_lost.ship([{"cid": "y", "event": "test_session", "total": 1}])
    assert verify_trace(backend_lost, "y", retries=1)["verdict"] == "absent"
