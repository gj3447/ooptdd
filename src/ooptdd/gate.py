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
      - event: cycle           # `event` is optional; omit to match any event
        where: {verdict: NG}   # field-equality filter (count only matching events)
        op: "=="
        count: 0
      - must_order: [a, b, c]  # each must occur, first-occurrence times non-decreasing

Counting is done over the events the backend returns for ``cid`` — no
backend-specific query language, so the same gate runs on memory, OpenObserve, or
any future driver. ``where`` filters on arbitrary event fields (e.g. ``verdict``,
``level``), which is why the OpenObserve driver selects whole rows: the smart
filtering lives here, in Python, identically for every backend. ``must_order``
checks sequencing using each event's ``_timestamp`` (the store-receive time every
backend stamps), so "A precedes B precedes C within this cid" becomes config, not
a hand-written self-join.
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


def _matches(ev: dict, event: str | None, where: dict) -> bool:
    """An event matches a rule when its name equals ``event`` (if given) and every
    ``where`` field equals the event's value. ``event=None`` matches any name."""
    if event is not None and ev.get("event") != event:
        return False
    return all(ev.get(k) == v for k, v in where.items())


def _first_ts(events: list[dict], name: str) -> int | None:
    """Earliest ``_timestamp`` (µs) among events named ``name``; None if none carry one."""
    seen = [
        ev["_timestamp"]
        for ev in events
        if ev.get("event") == name and ev.get("_timestamp") is not None
    ]
    return min(seen) if seen else None


def _eval_must_order(events: list[dict], seq: list, reachable: bool) -> dict:
    """A sequencing check: every name must occur, and first-occurrence times must be
    non-decreasing in the listed order. Missing events fail (can't order an absence)."""
    firsts = [(name, _first_ts(events, name)) for name in seq]
    missing = [name for name, ts in firsts if ts is None]
    ordered = not missing and all(
        firsts[i][1] <= firsts[i + 1][1] for i in range(len(firsts) - 1)
    )
    return {
        "must_order": list(seq),
        "missing": missing,
        "ordered": ordered,
        "firsts": {name: ts for name, ts in firsts},
        "passed": reachable and ordered,
    }


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
    checks = []
    all_ok = res.reachable
    for rule in spec.get("expect", []):
        if "must_order" in rule:
            chk = _eval_must_order(res.events, rule["must_order"], res.reachable)
            all_ok = all_ok and chk["passed"]
            checks.append(chk)
            continue
        ev_name = rule.get("event")
        where = rule.get("where") or {}
        op = rule.get("op", ">=")
        want = int(rule.get("count", 1))
        got = sum(1 for ev in res.events if _matches(ev, ev_name, where))
        passed = res.reachable and _OPS[op](got, want)
        all_ok = all_ok and passed
        checks.append(
            {"event": ev_name, "where": where, "op": op,
             "want": want, "got": got, "passed": passed}
        )
    return {"ok": all_ok, "reachable": res.reachable, "cid": cid, "checks": checks}
