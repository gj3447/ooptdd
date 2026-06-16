"""Gate runner — evaluate a YAML trace spec against a backend.

A gate is the *Red* artifact: you write what you expect to observe before the
code emits it. It is plain data in your repo (the agent only proposes it; the
store is the judge), and it is intentionally count-based — existence and
cardinality, the assertions that are robust on eventually-consistent stores.

Spec format (``gates/*.yaml``)::

    cid_env: OOPTDD_CID        # or:  cid: a-literal-correlation-id
    service: myapp.tests       # optional, informational
    expect:
      - event: test_session
        op: ">="              # one of  >=  >  ==  <=  <
        count: 1
      - event: test_outcome
        op: ">="
        count: 5

Counting is done over the events the backend returns for ``cid`` — no
backend-specific query language, so the same gate runs on memory, OpenObserve, or
any future driver.
"""
from __future__ import annotations

import operator
import os
import time

from .backends import Backend

_OPS = {
    ">=": operator.ge,
    ">": operator.gt,
    "==": operator.eq,
    "<=": operator.le,
    "<": operator.lt,
}


def load_gate(path: str) -> dict:
    import yaml  # PyYAML (declared dependency)

    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _resolve_cid(spec: dict) -> str:
    if spec.get("cid"):
        return str(spec["cid"])
    env = spec.get("cid_env", "OOPTDD_CID")
    cid = os.getenv(env)
    if not cid:
        raise ValueError(f"gate needs a cid: set `cid:` in the spec or export {env}")
    return cid


def evaluate(
    backend: Backend,
    spec: dict,
    *,
    lookback_s: int | None = None,
    future_buffer_s: int | None = None,
) -> dict:
    """Run a gate spec once. Returns ``{ok, reachable, cid, checks:[...]}``."""
    cid = _resolve_cid(spec)
    lookback_s = backend.default_lookback_s if lookback_s is None else lookback_s
    future_buffer_s = (
        backend.default_future_buffer_s if future_buffer_s is None else future_buffer_s
    )
    now_us = int(time.time() * 1_000_000)
    res = backend.query(
        cid,
        since_us=now_us - lookback_s * 1_000_000,
        until_us=now_us + future_buffer_s * 1_000_000,
    )
    counts: dict[str, int] = {}
    for ev in res.events:
        counts[ev.get("event", "")] = counts.get(ev.get("event", ""), 0) + 1

    checks = []
    all_ok = res.reachable
    for rule in spec.get("expect", []):
        ev_name = rule["event"]
        op = rule.get("op", ">=")
        want = int(rule.get("count", 1))
        got = counts.get(ev_name, 0)
        passed = res.reachable and _OPS[op](got, want)
        all_ok = all_ok and passed
        checks.append(
            {"event": ev_name, "op": op, "want": want, "got": got, "passed": passed}
        )
    return {"ok": all_ok, "reachable": res.reachable, "cid": cid, "checks": checks}
