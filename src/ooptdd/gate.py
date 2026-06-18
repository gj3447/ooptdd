"""Gate runner — evaluate a YAML trace spec against a backend.

A gate is the *Red* artifact: you write what you expect to observe before the
code emits it. It is plain data in your repo (the agent only proposes it; the
store is the judge), and it is intentionally count-based — existence and
cardinality, the assertions that are robust on eventually-consistent stores.

Spec format (``gates/*.yaml``)::

    cid_env: OOPTDD_CID        # or:  cid: a-literal-correlation-id
    service: myapp.tests       # optional, informational
    timeWindow: 1h             # optional rolling readback window (OpenSLO style:
                               #   30s/5m/2h/1d or bare seconds); default = backend's
    indicators:                # optional SLI layer — *how to select* (named, reusable)
      ng_cycles: {event: cycle, where: {verdict: NG}}
      done:      {event: cycle, where: {verdict: PASS}}
    expect:                    # the SLO layer — *what counts as green* (criteria)
      - event: test_session
        op: ">="              # symbolic (>= > == <= <) OR OpenSLO words (gte/gt/eq/lte/lt)
        count: 1
      - event: test_outcome
        op: gte                #   `target:` is an alias for `count:`
        target: 5
      - indicatorRef: ng_cycles # reuse a named indicator; criteria stay here
        op: eq
        target: 0
      - ratioMetric:           # good/total ratio (OpenSLO ratioMetric)
          good:  {indicatorRef: done}
          total: {event: cycle}
        op: gte
        target: 0.99
      - present:               # subset-present, ANY order (testfixtures order_matters=False);
          - {event: a}         #   each matcher must match >=1 event. The default "did these
          - {event: b, where: {station: A}}   #   happen?" check — order is NOT asserted.
      - must_order: [a, b, c]  # each must occur, first-occurrence times non-decreasing
      - absent:                # the negative wing — matching events must NOT occur (count 0).
          where: {level: ERROR}   #   the mirror of `present`. Offenders are surfaced so an
                               #   error log becomes a hard failure, not green-and-noisy.
      - event: optional_stream # optional: a threshold miss does NOT fail the gate,
        op: ">="               #   but it IS surfaced (and an unreachable store is still
        count: 1               #   INFRA, reported via `reachable`, never a clean pass)
        optional: true
    forbid_errors: true        # optional (spec-level): inject an implicit ERROR/CRITICAL
                               #   `absent` into the gate (default = env OOPTDD_FORBID_ERRORS;
                               #   set false here to opt a spec out). Levels via `error_levels:`.
    allow_errors:              # optional (spec-level) allowlist — these matched errors are
      - {event: zdf.drop}      #   exempt (known-benign), so they don't flip the gate.

Counting is done over the events the backend returns for ``cid`` — no
backend-specific query language, so the same gate runs on memory, OpenObserve, or
any future driver. ``where`` filters on arbitrary event fields (e.g. ``verdict``,
``level``) by partial-dict equality — only the listed keys must match, like
``pytest-structlog``'s ``log.has(evt, **ctx)``. ``must_order`` checks sequencing
using each event's ``_timestamp`` (store-receive time); ``present`` asserts a
subset occurred in *any* order (``testfixtures.check_present(order_matters=False)``).

The vocabulary (``op: gte``, ``target``, ``timeWindow``, ``indicators``/``indicatorRef``,
``ratioMetric``) is deliberately aligned with **OpenSLO** and **Keptn** SLO specs so
a gate reads like an SLO objective and the SLI ("how to query") is decoupled from the
SLO ("what is green") and reusable. Symbolic operators and ``count`` remain first-class —
the alignment is additive, the evaluation logic is unchanged.
"""
from __future__ import annotations

import operator
import os
import time

from .backends import Backend

# Symbolic comparison operators are native; the OpenSLO/Keptn word forms
# (gte/gt/eq/lte/lt[/ne]) are accepted as aliases so a spec can read like an SLO
# objective without changing evaluation.
_OPS = {
    ">=": operator.ge,
    ">": operator.gt,
    "==": operator.eq,
    "!=": operator.ne,
    "<=": operator.le,
    "<": operator.lt,
}
_OP_ALIASES = {
    "gte": ">=", "ge": ">=", "gt": ">", "eq": "==", "ne": "!=",
    "lte": "<=", "le": "<=", "lt": "<",
}
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _norm_op(op) -> str:
    """Map an OpenSLO word operator to its symbolic form; pass symbols through."""
    return _OP_ALIASES.get(str(op), str(op))


def _want(rule: dict):
    """``target`` (OpenSLO) is an alias for ``count``; default 1."""
    if "target" in rule:
        return rule["target"]
    return rule.get("count", 1)


def duration_s(v) -> int | None:
    """Parse an OpenSLO-style rolling window: ``30s`` / ``5m`` / ``2h`` / ``1d`` /
    bare seconds (int or numeric string). ``None`` -> ``None`` (use backend default)."""
    if v is None:
        return None
    if isinstance(v, bool):  # guard: bool is an int subclass
        raise ValueError(f"invalid timeWindow: {v!r}")
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().lower()
    if s and s[-1] in _UNITS and s[:-1].isdigit():
        return int(s[:-1]) * _UNITS[s[-1]]
    return int(s)  # bare numeric string -> seconds


def load_gate(path: str) -> dict:
    import yaml  # PyYAML (declared dependency)

    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _matches(ev: dict, event: str | None, where: dict) -> bool:
    """An event matches when its name equals ``event`` (if given) and every ``where``
    field equals the event's value (partial-dict — unlisted keys ignored). ``event=None``
    matches any name."""
    if event is not None and ev.get("event") != event:
        return False
    return all(ev.get(k) == v for k, v in where.items())


def _resolve_matcher(m: dict, indicators: dict) -> tuple[str | None, dict]:
    """Resolve a matcher to ``(event, where)``, expanding an ``indicatorRef`` against the
    spec's ``indicators`` (SLI layer). Inline ``event``/``where`` override/extend the
    referenced indicator."""
    if "indicatorRef" in m:
        base = indicators.get(m["indicatorRef"], {})
        event = m.get("event", base.get("event"))
        where = {**(base.get("where") or {}), **(m.get("where") or {})}
        return event, where
    return m.get("event"), (m.get("where") or {})


def _count(events: list[dict], event: str | None, where: dict) -> int:
    return sum(1 for ev in events if _matches(ev, event, where))


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
    if "heartbeat" in chk:
        return f"heartbeat:{chk['heartbeat']}@{chk.get('every_s')}s"
    if "must_order" in chk:
        return "must_order:" + ">".join(chk["must_order"])
    if "present" in chk:
        return "present:" + ",".join(chk["present"])
    if "absent" in chk:
        return "absent:" + ",".join(chk["absent"])
    if "ratio" in chk:
        return f"ratio:{chk['ratio']}{chk.get('op', '')}{chk.get('want', '')}"
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


def _all_ts(events: list[dict], name: str) -> list[int]:
    """All ``_timestamp`` values (µs, sorted) among events named ``name``."""
    return sorted(
        ev["_timestamp"] for ev in events
        if ev.get("event") == name and ev.get("_timestamp") is not None
    )


def _eval_must_order(events: list[dict], seq: list, reachable: bool,
                     within_s: float | None = None) -> dict:
    """A sequencing check: every name must occur, and first-occurrence times must be
    non-decreasing in the listed order. Missing events fail (can't order an absence).

    ``within_s`` (MTL bounded-interval ``F[0,within_s]``, à la RTAMT): if set, every
    consecutive gap must also be ≤ ``within_s`` — "B follows A *within* the bound", not
    just eventually. A gap that exceeds it fails and is surfaced in ``gaps_exceeded``.
    """
    firsts = [(name, _first_ts(events, name)) for name in seq]
    missing = [name for name, ts in firsts if ts is None]
    ordered = not missing and all(
        firsts[i][1] <= firsts[i + 1][1] for i in range(len(firsts) - 1)
    )
    gaps_exceeded: list[str] = []
    if ordered and within_s is not None:
        bound_us = within_s * 1_000_000
        for i in range(len(firsts) - 1):
            if firsts[i + 1][1] - firsts[i][1] > bound_us:
                gaps_exceeded.append(f"{firsts[i][0]}->{firsts[i + 1][0]}")
    chk = {
        "must_order": list(seq),
        "missing": missing,
        "ordered": ordered,
        "firsts": {name: ts for name, ts in firsts},
        "passed": reachable and ordered and not gaps_exceeded,
    }
    if within_s is not None:
        chk["within_s"] = within_s
        chk["gaps_exceeded"] = gaps_exceeded
    return chk


def _eval_heartbeat(events: list[dict], rule: dict, reachable: bool) -> dict:
    """A liveness check (MTL ``G[0,every_s] F event``): the event must occur and never go
    silent longer than ``every_s`` between consecutive occurrences. ≥1 occurrence required
    (you can't have a heartbeat that never beat). Surfaces the worst observed gap."""
    name = rule["heartbeat"]
    every_s = float(rule["every_s"])
    ts = _all_ts(events, name)
    if not ts:
        return {"heartbeat": name, "every_s": every_s, "beats": 0,
                "max_gap_s": None, "passed": False, "reason": "no_beat"}
    gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    max_gap_us = max(gaps) if gaps else 0
    ok = max_gap_us <= every_s * 1_000_000
    return {"heartbeat": name, "every_s": every_s, "beats": len(ts),
            "max_gap_s": round(max_gap_us / 1_000_000, 6), "passed": reachable and ok}


def _eval_present(events: list[dict], matchers: list, indicators: dict, reachable: bool) -> dict:
    """A subset-present check (``testfixtures.check_present`` semantics): each matcher must
    match at least one event. Order is NOT asserted and extra events are ignored — the
    robust default for unordered, eventually-consistent telemetry."""
    labels, missing = [], []
    for m in matchers:
        event, where = _resolve_matcher(m, indicators)
        lbl = event or ("where:" + ",".join(f"{k}={v}" for k, v in where.items())) or "(any)"
        labels.append(lbl)
        if _count(events, event, where) < 1:
            missing.append(lbl)
    return {
        "present": labels,
        "missing": missing,
        "passed": reachable and not missing,
    }


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"} if v is not None else False


def _brief(ev: dict) -> dict:
    """A capped view of an offending event — enough for the RCA to name what leaked
    without dumping a whole payload into the verdict."""
    keys = ("event", "level", "message", "msg", "error", "exc", "_timestamp")
    return {k: ev[k] for k in keys if k in ev} or {"event": ev.get("event")}


def _eval_absent(events: list[dict], matchers: list, indicators: dict, reachable: bool,
                 *, allow: list | None = None) -> dict:
    """A forbid check — the mirror of ``present``: each matcher must match ZERO events.
    This is the negative wing (e.g. *no* ``ERROR``-level record for this cid). An event
    matching any ``allow`` (allowlist) matcher is exempt, so a known-benign error doesn't
    flip the gate. Offending events are surfaced (capped) so the RCA can name what leaked.
    """
    allow = allow or []
    allow_resolved = [_resolve_matcher(a, indicators) for a in allow]
    labels, offenders = [], []
    for m in matchers:
        event, where = _resolve_matcher(m, indicators)
        labels.append(event or ("where:" + ",".join(f"{k}={v}" for k, v in where.items()))
                      or "(any)")
        for ev in events:
            if _matches(ev, event, where) and not any(
                _matches(ev, a_ev, a_where) for a_ev, a_where in allow_resolved
            ):
                offenders.append(ev)
    return {
        "absent": labels,
        "violations": len(offenders),
        "offending": [_brief(o) for o in offenders[:5]],
        "passed": reachable and not offenders,
    }


def _eval_ratio(events: list[dict], rule: dict, indicators: dict, reachable: bool) -> dict:
    """An OpenSLO ratioMetric: ``good / total`` compared to ``target`` with ``op``. A zero
    denominator can't form a ratio -> not a pass (surfaced via ``reason``)."""
    spec = rule["ratioMetric"]
    g_event, g_where = _resolve_matcher(spec.get("good", {}), indicators)
    t_event, t_where = _resolve_matcher(spec.get("total", {}), indicators)
    good = _count(events, g_event, g_where)
    total = _count(events, t_event, t_where)
    op = _norm_op(rule.get("op", ">="))
    want = float(_want(rule))
    if total == 0:
        return {"ratio": "good/total", "good": good, "total": 0, "value": None,
                "op": op, "want": want, "passed": False, "reason": "ratio_total_zero"}
    value = good / total
    return {"ratio": "good/total", "good": good, "total": total, "value": value,
            "op": op, "want": want, "passed": reachable and _OPS[op](value, want)}


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

    The readback window comes from ``lookback_s`` (arg) else the spec's ``timeWindow``
    (OpenSLO rolling window) else the backend default.
    """
    cid = _resolve_cid(spec)
    if ontology is None and spec.get("ontology"):
        from .ontology import Ontology  # file-first; offline, no KG dependency
        ontology = Ontology.from_file(spec["ontology"])
    indicators = spec.get("indicators") or {}
    if lookback_s is None:
        lookback_s = duration_s(spec.get("timeWindow", spec.get("time_window")))
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
    rules = list(spec.get("expect", []))
    # The negative wing: forbid ERROR/CRITICAL records for this cid. Default-ON via the
    # OOPTDD_FORBID_ERRORS env so consumers opt in without editing every spec; a spec can
    # override with `forbid_errors: false` (opt out) or exempt known-benign ones via
    # `allow_errors:`. Without it, a cycle whose good events all arrived but which also
    # logged an error reads as green-and-noisy — the field-error blind spot.
    fe = spec.get("forbid_errors")
    if fe is None:
        fe = _truthy(os.getenv("OOPTDD_FORBID_ERRORS"))
    if fe:
        levels = spec.get("error_levels") or ["ERROR", "CRITICAL"]
        rules.append({"absent": [{"where": {"level": lv}} for lv in levels],
                      "_auto": "forbid_errors"})
    checks = []
    for rule in rules:
        if "absent" in rule or "forbid" in rule:
            raw = rule.get("absent", rule.get("forbid"))
            matchers = raw if isinstance(raw, list) else [raw]
            chk = _eval_absent(res.events, matchers, indicators, res.reachable,
                               allow=spec.get("allow_errors"))
        elif "heartbeat" in rule:
            chk = _eval_heartbeat(res.events, rule, res.reachable)
        elif "must_order" in rule or "trajectory" in rule:
            # `trajectory` is the promptfoo/DeepEval word for an ordered tool/event
            # sequence — an alias for must_order (same first-occurrence sequencing).
            seq = rule.get("must_order") or rule.get("trajectory")
            chk = _eval_must_order(res.events, seq, res.reachable, within_s=rule.get("within_s"))
        elif "present" in rule:
            chk = _eval_present(res.events, rule["present"], indicators, res.reachable)
        elif "ratioMetric" in rule:
            chk = _eval_ratio(res.events, rule, indicators, res.reachable)
        elif "conforms" in rule:
            chk = _eval_conforms(res.events, rule, ontology, res.reachable)
        else:
            ev_name, where = _resolve_matcher(rule, indicators)
            op = _norm_op(rule.get("op", ">="))
            want = int(_want(rule))
            got = _count(res.events, ev_name, where)
            chk = {"event": ev_name, "where": where, "op": op,
                   "want": want, "got": got, "passed": res.reachable and _OPS[op](got, want)}
        chk["optional"] = bool(rule.get("optional", False))
        # Pact "pending pacts": a `pending` expectation is verified and surfaced but does
        # NOT gate the build — for an event whose emitter isn't wired yet. Once it passes,
        # drop the flag to promote it to a hard gate (see `pending_satisfied`).
        chk["pending"] = bool(rule.get("pending", False))
        chk["weight"] = float(rule.get("weight", 1.0))  # promptfoo per-assertion weight
        checks.append(chk)
    # A check gates only if it is neither optional (#10) nor pending (Pact). Optional/pending
    # misses are surfaced separately so a silently-degraded stream never reads as clean.
    gating = [c for c in checks if not c["optional"] and not c["pending"]]
    threshold = spec.get("threshold")
    if threshold is None:
        # all-or-nothing (default, unchanged): every gating check must pass.
        required_ok = all(c["passed"] for c in gating)
        score = None
    else:
        # promptfoo test-level threshold: pass iff the *weighted* pass-ratio meets it
        # (a quorum of expected events, not strict unanimity).
        wtot = sum(c["weight"] for c in gating)
        score = (sum(c["weight"] for c in gating if c["passed"]) / wtot) if wtot else 1.0
        required_ok = score >= float(threshold)
    # a store we never reached (reachable=False) is INFRA — never a clean pass (CLI exit 2).
    result = {
        "ok": res.reachable and required_ok,
        "reachable": res.reachable,
        "cid": cid,
        "checks": checks,
        "optional_failed": [_label(c) for c in checks if c["optional"] and not c["passed"]],
        "pending_failed": [_label(c) for c in checks if c["pending"] and not c["passed"]],
        "pending_satisfied": [_label(c) for c in checks if c["pending"] and c["passed"]],
    }
    if score is not None:
        result["score"] = score
        result["threshold"] = float(threshold)
    return result


def can_i_deploy(results: list[dict]) -> dict:
    """Pact ``can-i-deploy`` for ooptdd: may we ship, given a set of gate results?

    Yes iff every gate was reachable and ``ok``. ``pending`` checks never block (that is
    their purpose). Returns ``{deployable, blockers:[cid], inconclusive:[cid], pending:{cid:[..]}}``
    so a release step can fail closed on a real RED, hold on INFRA, and still see what is owed.
    """
    blockers = [r["cid"] for r in results if r["reachable"] and not r["ok"]]
    inconclusive = [r["cid"] for r in results if not r["reachable"]]
    pending = {r["cid"]: r["pending_failed"] for r in results if r.get("pending_failed")}
    return {
        "deployable": not blockers and not inconclusive,
        "blockers": blockers,
        "inconclusive": inconclusive,
        "pending": pending,
    }
