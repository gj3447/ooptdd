"""promptfoo python assert — passes only if the expected events ARRIVED.

promptfoo calls ``get_assert(output, context)``; we ignore the model output
(that is promptfoo's lane) and ask the store whether the run's side effects
landed. GradingResult keeps INFRA honest: an unreachable store is reported in
the reason, never disguised as a confident model failure.
"""
from __future__ import annotations

import os

from ooptdd import evaluate, get_backend


def get_assert(output: str, context: dict) -> dict:
    cid = os.getenv("OOPTDD_CID") or context.get("vars", {}).get("cid", "")
    spec = {
        "cid": cid,
        "expect": [
            {"event": "order.shipped", "op": "gte", "target": 1},
            {"absent": {"where": {"level": "ERROR"}}},
        ],
    }
    res = evaluate(get_backend(os.getenv("OOPTDD_BACKEND", "memory")), spec)
    if not res["reachable"] or not res.get("complete", True):
        return {"pass": False, "score": 0.0,
                "reason": "INCONCLUSIVE - store unreachable or readback truncated"}
    gating = [c for c in res["checks"] if not c.get("optional")]
    score = sum(1 for c in gating if c["passed"]) / len(gating) if gating else 0.0
    return {"pass": bool(res["ok"]), "score": score,
            "reason": "arrival confirmed" if res["ok"] else "expected events missing"}
