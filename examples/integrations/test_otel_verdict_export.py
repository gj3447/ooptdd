"""Verdict export — a gate's verdict as trace-world data (zero infra to try).

Run it:  pytest examples/integrations/test_otel_verdict_export.py -s

`emit_verdict_event` ships an envelope-conformant `ooptdd.verdict` event into
the same cid the gate judged (Phoenix vocabulary: annotator_kind=CODE), and
`verdict_span_attributes` yields flat attrs for `span.set_attributes(...)` —
no otel import required to build them.
"""
from __future__ import annotations

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import evaluate
from ooptdd.integrations import emit_verdict_event, verdict_span_attributes


def test_verdict_round_trips_into_the_trace():
    reset()
    b = MemoryBackend()
    cid = "otel-export-demo"
    b.ship([{"event": "boot", "cid": cid, "correlation_id": cid, "cycle_id": cid}])

    res = evaluate(b, {"cid": cid, "expect": [{"event": "boot", "op": "gte", "target": 1}]})
    ev = emit_verdict_event(b, res)
    assert ev["verdict"] == "present" and ev["spec_version"]  # envelope-conformant

    attrs = verdict_span_attributes(res)
    assert attrs["ooptdd.verdict"] == "present" and attrs["ooptdd.ok"] is True

    # the exported verdict is itself arrival-assertable — turtles, but honest ones
    res2 = evaluate(b, {"cid": cid, "expect": [
        {"event": "ooptdd.verdict", "where": {"verdict": "present"}, "op": "==", "count": 1},
    ]})
    assert res2["ok"]
    reset()
