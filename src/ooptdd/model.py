"""Pure data shaping — no I/O, no pytest, no backend.

`build_outcome_records` turns a list of plain pytest report dicts into the
structured event envelopes ooptdd ships. Keeping this pure means it is trivially
unit-testable and identical across every backend.

Envelope shape (one JSON object per event)::

    {
      "cid": "...", "correlation_id": "...", "cycle_id": "...",   # 3 aliases
      "service": "myapp.tests", "level": "INFO|ERROR",
      "event": "test_outcome" | "test_session",
      ...event-specific fields...
    }

We emit the correlation id under three keys because different stores index
different column names; carrying all three keeps "one query by id" working no
matter which backend you point at.
"""
from __future__ import annotations

# When one test produces several phase reports (setup/call/teardown) we keep the
# most severe outcome for the session tally.
_RANK = {"failed": 2, "skipped": 1, "passed": 0}


def correlation_keys(cid: str) -> dict:
    """The id under every alias a backend might index on."""
    return {"cid": cid, "correlation_id": cid, "cycle_id": cid}


def build_outcome_records(
    reports: list[dict],
    cid: str,
    *,
    service: str = "ooptdd.tests",
    meta: dict | None = None,
) -> list[dict]:
    """pytest reports -> structured event records (pure function).

    ``reports``: ``[{nodeid, outcome, duration, when[, longrepr]}]``.
    Returns N per-phase ``test_outcome`` events (tracebacks preserved for RCA)
    plus exactly one ``test_session`` summary. The summary tally is computed over
    *distinct* nodeids so a teardown-failure can't double-count (a real bug we hit).
    """
    meta = meta or {}
    recs: list[dict] = []
    for r in reports:
        outcome = r["outcome"]
        rec = {
            **correlation_keys(cid),
            "service": service,
            "level": "ERROR" if outcome == "failed" else "INFO",
            "event": "test_outcome",
            "test": r["nodeid"],
            "outcome": outcome,
            "when": r.get("when", "call"),
            "duration_s": round(float(r.get("duration", 0.0)), 4),
        }
        if outcome == "failed" and r.get("longrepr"):
            rec["error"] = str(r["longrepr"])[:2000]
        recs.append(rec)

    by_test: dict[str, str] = {}  # nodeid -> most severe outcome
    for r in reports:
        prev = by_test.get(r["nodeid"])
        if prev is None or _RANK.get(r["outcome"], 0) > _RANK.get(prev, 0):
            by_test[r["nodeid"]] = r["outcome"]
    passed = sum(1 for o in by_test.values() if o == "passed")
    failed = sum(1 for o in by_test.values() if o == "failed")
    skipped = sum(1 for o in by_test.values() if o == "skipped")
    recs.append(
        {
            **correlation_keys(cid),
            "service": service,
            "level": "ERROR" if failed else "INFO",
            "event": "test_session",
            "total": len(by_test),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            **meta,
        }
    )
    return recs


def build_session_start(
    cid: str,
    *,
    service: str = "ooptdd.tests",
    expected_total: int | None = None,
    meta: dict | None = None,
) -> dict:
    """A heartbeat shipped *before* the run (pure function).

    If the ``test_session`` summary is later lost, the presence of this record lets
    ``verify_trace`` tell "the run started but its summary never arrived" (partial loss)
    from "nothing arrived at all" (total loss) — two very different RCA paths.
    """
    rec = {
        **correlation_keys(cid),
        "service": service,
        "level": "INFO",
        "event": "session_start",
        **(meta or {}),
    }
    if expected_total is not None:
        rec["expected_total"] = expected_total
    return rec
