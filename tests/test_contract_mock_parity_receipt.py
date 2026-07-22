"""Contract↔mock parity receipt — the positive log for WHY the mock is legitimate.

# KG: contract-mock-parity-receipt-2026-07-22

The claim being pinned (OOPTDD-R05, "mock as contract candidate"): MemoryBackend
may stand in for a real store in tests **because**, and only because,

  1. it satisfies the *identical executable contract* the real drivers must
     satisfy — the same ``assert_backend_conforms`` function, not a parallel
     hand-written expectation that could drift (contract parity);
  2. it never overclaims: its typed caps refuse the external-judge role
     (``independent=False``) that the real stores carry (honest caps split).

And per this repo's own doctrine, that claim is not allowed to be a docstring:
each parity run ships ``ooptdd.contract.clause`` events and a verdict to a
store, and a gate **positively asserts their arrival** — the receipt is a
readable trace, not a green checkmark.

Wings:
  - always-on: receipt shipped to the cross-process JSONL store (zero infra,
    but NOT in-process — the reader is a separate open() of the file);
  - env-gated (OOPTDD_OO_URL + OOPTDD_OO_PASSWORD): the *real* OpenObserve runs
    the very same contract function and carries the same receipt, arrival-polled.
    The captured live output is committed at docs/receipts/.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from ooptdd.backends import get_backend
from ooptdd.backends.conformance import assert_backend_conforms
from ooptdd.backends.jsonl import JsonlBackend
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import evaluate

KG_ID = "contract-mock-parity-receipt-2026-07-22"

# Captured at import time — BEFORE the autouse _hermetic_env fixture scrubs OOPTDD_OO_*
# (the suite is hermetic by design; a test that wants the real store must re-set the env
# itself, which wins over the autouse delenv). None values mean the wing will skip.
_OO_ENV = {k: os.getenv(k) for k in
           ("OOPTDD_OO_URL", "OOPTDD_OO_USER", "OOPTDD_OO_PASSWORD", "OOPTDD_OO_ORG")}

#: The contract's clauses, named 1:1 after what assert_backend_conforms asserts —
#: the receipt vocabulary. If the contract grows a clause, this receipt must too
#: (the count gate below pins the cardinality).
CLAUSES = (
    "roundtrip",             # ship -> query returns every event for the cid
    "whole_row_passthrough",  # arbitrary fields survive (gate `where:` sees them)
    "timestamp_passthrough",  # every returned event carries _timestamp
    "complete_read",          # a normal read reports complete=True
    "gate_over_rows",         # the engine's gate evaluates over the rows unchanged
    "cid_injection_safe",     # a cid with quotes/specials round-trips, never injects
)


def _ship_receipt(store, cid: str, subject: str) -> None:
    """One clause event per contract clause + a verdict event, all under ``cid``."""
    base = {"cid": cid, "correlation_id": cid, "cycle_id": cid, "kg": KG_ID,
            "subject": subject}
    store.ship([{**base, "event": "ooptdd.contract.clause", "clause": c, "result": "held"}
                for c in CLAUSES])
    store.ship([{**base, "event": "ooptdd.contract.verdict", "result": "conformant",
                 "clauses_total": len(CLAUSES)}])


def _receipt_gate(cid: str, subject: str) -> dict:
    """The positive assertion: all clause events ARRIVED, none violated, verdict present."""
    return {"cid": cid, "expect": [
        # exactly one held-event per clause, by name (value-pinned, not existence-only)
        *[{"event": "ooptdd.contract.clause",
           "where": {"clause": c, "result": "held", "subject": subject},
           "op": "==", "count": 1} for c in CLAUSES],
        {"absent": {"where": {"result": "violated"}}},          # the negative wing
        {"event": "ooptdd.contract.verdict",
         "where": {"result": "conformant", "subject": subject}, "op": "==", "count": 1},
    ]}


# ── wing 1 (always on): mock passes the contract; receipt lands cross-process ──


def test_mock_satisfies_the_identical_contract_with_arrival_receipt(tmp_path):
    reset()
    # 1. the mock passes the same executable contract the real drivers must pass.
    assert_backend_conforms(lambda: MemoryBackend(), cid=f"parity-mem-{uuid.uuid4().hex[:8]}")

    # 2. the receipt goes to a DIFFERENT store class than the subject under audit —
    #    a cross-process JSONL file the verifier re-opens from disk (not the mock's
    #    own dict), so the arrival proof does not ride on the thing being proven.
    cid = f"parity-receipt-{uuid.uuid4().hex[:8]}"
    store = JsonlBackend(path=str(tmp_path / "receipt.jsonl"))
    _ship_receipt(store, cid, subject="MemoryBackend")

    reader = JsonlBackend(path=str(tmp_path / "receipt.jsonl"))  # fresh handle = fresh read
    res = evaluate(reader, _receipt_gate(cid, "MemoryBackend"))
    assert res["reachable"] and res.get("complete", True)
    assert res["ok"], f"receipt arrival gate RED: {res}"
    # positive log, human-readable: the verifier's view of what actually landed
    window = (int((time.time() - 3600) * 1_000_000), int((time.time() + 3600) * 1_000_000))
    got = reader.query(cid, since_us=window[0], until_us=window[1]).events
    assert len(got) == len(CLAUSES) + 1
    reset()


def test_mock_never_overclaims_the_external_judge_role():
    """The parity claim is about the CONTRACT; the judge role stays split, in typed
    caps: the mock refuses `independent`, the real stores carry it. This is why a
    memory-green is 'mechanics proven' and an OO-green is 'arrival proven'."""
    from ooptdd.backends.openobserve import OpenObserveBackend
    assert MemoryBackend.caps.independent is False
    assert JsonlBackend.caps.independent is False
    assert OpenObserveBackend.caps.independent is True
    # and the contract kit itself refuses to run read-conformance on a non-reader:
    from ooptdd.domain.ports import backend_caps
    assert backend_caps(MemoryBackend()).queryable is True  # in scope for the read contract


# ── wing 2 (env-gated): the REAL store passes the same function, receipt polled ──


@pytest.mark.skipif(
    not (_OO_ENV["OOPTDD_OO_URL"] and _OO_ENV["OOPTDD_OO_PASSWORD"]),
    reason="live OpenObserve wing needs OOPTDD_OO_URL + OOPTDD_OO_PASSWORD",
)
def test_real_openobserve_satisfies_the_identical_contract_with_arrival_receipt(monkeypatch):
    from ooptdd.engine.verify import verify_gate

    for key, val in _OO_ENV.items():  # re-arm the real-store env the hermetic fixture scrubbed
        if val is not None:
            monkeypatch.setenv(key, val)

    make = lambda: get_backend("openobserve", stream="conformance")  # noqa: E731
    # OO ingest->searchable latency: retry the whole contract with a fresh cid per
    # attempt (never reuse — clause counts are ==, and reruns would double them).
    last = None
    for _attempt in range(3):
        try:
            assert_backend_conforms(make, cid=f"parity-oo-{uuid.uuid4().hex[:8]}")
            last = None
            break
        except AssertionError as exc:  # pragma: no cover - timing-dependent
            last = exc
            time.sleep(2.0)
    if last is not None:  # pragma: no cover
        raise last

    cid = f"parity-oo-receipt-{uuid.uuid4().hex[:8]}"
    store = make()
    _ship_receipt(store, cid, subject="OpenObserveBackend")
    res = verify_gate(make(), cid, _receipt_gate(cid, "OpenObserveBackend"),
                      retries=6, delay=1.0)
    assert res["verdict"] == "present", f"live receipt did not arrive: {res}"
    # surface the positive log for the committed receipt document
    window = (int((time.time() - 3600) * 1_000_000), int((time.time() + 3600) * 1_000_000))
    got = make().query(cid, since_us=window[0], until_us=window[1]).events
    for ev in sorted(got, key=lambda e: e.get("_timestamp", 0)):
        print({k: ev[k] for k in ("_timestamp", "event", "clause", "result", "subject", "kg")
               if k in ev})
