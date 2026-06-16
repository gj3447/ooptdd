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
      - event: optional_stream # optional: a threshold miss does NOT fail the gate,
        op: ">="               #   but it IS surfaced (and an unreachable store is still
        count: 1               #   INFRA, reported via `reachable`, never a clean pass)
        optional: true

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


def _label(chk: dict) -> str:
    """Human handle for a check (used to surface optional failures)."""
    if "conforms" in chk:
        return "conforms:" + str(chk["conforms"])
    if "must_order" in chk:
        return "must_order:" + ">".join(chk["must_order"])
    if chk.get("event"):
        return str(chk["event"])
    where = chk.get("where") or {}
    return "where:" + ",".join(f"{k}={v}" for k, v in where.items()) if where else "(any)"


def _eval_conforms(events: list[dict], rule: dict, ontology, reachable: bool) -> dict:
    """An ontology-conformance check: events (optionally of one type) must satisfy
    their EventType — required attrs present, value constraints hold; in closed-world
    an undeclared event name is drift. Needs an ontology; without one it cannot pass."""
    target = rule["conforms"]  # an EventType name, or "*" for all events
    if ontology is None:
        return {"conforms": target, "passed": False, "checked": 0,
                "violations": [{"problems": ["ontology_not_loaded "
                                "(set `ontology:` in the spec or pass ontology=)"]}],
                "unknown": []}
    from .ontology import check_conformance

    cw = rule.get("closed_world")
    res = check_conformance(events, ontology,
                            event_type=None if target == "*" else target, closed_world=cw)
    return {"conforms": target, "passed": reachable and res["passed"],
            "checked": res["checked"], "violations": res["violations"],
            "unknown": res["unknown"]}


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
    ontology=None,
) -> dict:
    """Run a gate spec once.

    Returns ``{ok, reachable, cid, checks:[...], optional_failed:[labels]}``. ``ok`` is
    true iff the store was reachable and every *required* check passed; optional checks
    that miss are listed in ``optional_failed`` but never flip ``ok``. ``reachable=False``
    (store unreachable / INFRA) keeps ``ok`` false regardless — that is not a clean pass.
    """
    cid = _resolve_cid(spec)
    if ontology is None and spec.get("ontology"):
        from .ontology import Ontology  # file-first; offline, no KG dependency
        ontology = Ontology.from_file(spec["ontology"])
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
    for rule in spec.get("expect", []):
        if "must_order" in rule:
            chk = _eval_must_order(res.events, rule["must_order"], res.reachable)
        elif "conforms" in rule:
            chk = _eval_conforms(res.events, rule, ontology, res.reachable)
        else:
            ev_name = rule.get("event")
            where = rule.get("where") or {}
            op = rule.get("op", ">=")
            want = int(rule.get("count", 1))
            got = sum(1 for ev in res.events if _matches(ev, ev_name, where))
            chk = {"event": ev_name, "where": where, "op": op,
                   "want": want, "got": got, "passed": res.reachable and _OPS[op](got, want)}
        chk["optional"] = bool(rule.get("optional", False))
        checks.append(chk)
    # #10: an *optional* check that fails (threshold miss) does NOT fail the gate; but a
    # query that never reached the store (reachable=False) is INFRA — surfaced separately
    # via `reachable` so it is never mistaken for a clean pass (CLI maps it to exit 2).
    required_ok = all(c["passed"] for c in checks if not c["optional"])
    return {
        "ok": res.reachable and required_ok,
        "reachable": res.reachable,
        "cid": cid,
        "checks": checks,
        "optional_failed": [_label(c) for c in checks if c["optional"] and not c["passed"]],
    }
