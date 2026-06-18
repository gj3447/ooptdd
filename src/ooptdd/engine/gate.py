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

Evaluation is **streaming**: each check compiles to an LTL₃/MTL monitor automaton
(:mod:`ooptdd.monitor`) that is fed the event prefix in store-timestamp order and reports
a three-valued verdict (``sat``/``viol``/``pend``) plus the index at which it settled. The
final collapsed pass/fail is identical to the historical count comparison; what the gate
gains is a real incremental monitor with anticipatory verdicts, surfaced per check as
``verdict``/``settled_at``.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.ports import Backend
from .monitor import (  # the evaluation kernel
    AbsentMonitor,
    ConformsMonitor,
    CountMonitor,
    HeartbeatMonitor,
    OrderMonitor,
    PresentMonitor,
    RatioMonitor,
    _matches,  # noqa: F401  re-exported for backward compat (ooptdd.mutation)
    _norm_op,
    _resolve_matcher,  # noqa: F401  re-exported for backward compat (ooptdd.mutation)
    _want,
    run_monitor,
    stream_key,
)

# ---- check-predicate registry (the extension seam) -------------------------- #
# Each gate check kind (present/absent/conforms/...) is a handler registered under its
# spec keyword, not a branch in a central if-elif. New predicates register via
# ``@check("<key>")`` WITHOUT editing ``evaluate()`` — the pluggy/hypothesis registration
# pattern (a string-keyed single-dispatch table), absorbed here. The registry is also a
# structural-assertion surface: every dispatched key must resolve to a registered handler.


@dataclass(frozen=True)
class CheckCtx:
    """Cross-cutting context a check handler may need — built once per :func:`evaluate`
    call, so handlers take ``(events, rule, ctx)`` instead of 4-6 positional args."""

    reachable: bool
    indicators: dict
    ontology: object | None = None
    allow_errors: list | None = None


CheckFn = Callable[[list, dict, CheckCtx], dict]
CHECK_REGISTRY: dict[str, CheckFn] = {}


def check(*keys: str) -> Callable[[CheckFn], CheckFn]:
    """Register a check handler under one or more spec keywords. Decoration-time only (a
    dict insert, no I/O). A duplicate key raises — guarding the silent-overwrite failure."""
    def deco(fn: CheckFn) -> CheckFn:
        for k in keys:
            if k in CHECK_REGISTRY:
                raise ValueError(f"duplicate check predicate {k!r}")
        for k in keys:
            CHECK_REGISTRY[k] = fn
        return fn
    return deco


_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


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


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"} if v is not None else False


# ---- registered check handlers (thin adapters that compile a rule to a monitor) ---- #
# Each handler builds the monitor automaton for its predicate and drives it over the
# (already stream-ordered) events via run_monitor. The evaluation logic lives in
# ``ooptdd.monitor``; these wrappers only adapt the (events, rule, ctx) seam to it.


@check("absent")
def _check_absent(events: list, rule: dict, ctx: CheckCtx) -> dict:
    raw = rule.get("absent", rule.get("forbid"))  # `forbid` is a synonym for `absent`
    matchers = raw if isinstance(raw, list) else [raw]
    return run_monitor(
        AbsentMonitor(matchers, ctx.indicators, allow=ctx.allow_errors),
        events, ctx.reachable,
    )


@check("heartbeat")
def _check_heartbeat(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return run_monitor(
        HeartbeatMonitor(rule["heartbeat"], rule["every_s"]), events, ctx.reachable
    )


@check("must_order")
def _check_must_order(events: list, rule: dict, ctx: CheckCtx) -> dict:
    # `trajectory` is the promptfoo/DeepEval word for an ordered sequence — an alias.
    seq = rule.get("must_order") or rule.get("trajectory")
    return run_monitor(
        OrderMonitor(seq, within_s=rule.get("within_s")), events, ctx.reachable
    )


@check("present")
def _check_present(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return run_monitor(
        PresentMonitor(rule["present"], ctx.indicators), events, ctx.reachable
    )


@check("ratioMetric")
def _check_ratio(events: list, rule: dict, ctx: CheckCtx) -> dict:
    spec = rule["ratioMetric"]
    return run_monitor(
        RatioMonitor(spec.get("good", {}), spec.get("total", {}),
                     _norm_op(rule.get("op", ">=")), float(_want(rule)), ctx.indicators),
        events, ctx.reachable,
    )


@check("conforms")
def _check_conforms(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return run_monitor(
        ConformsMonitor(rule["conforms"], ctx.ontology, closed_world=rule.get("closed_world")),
        events, ctx.reachable,
    )


def _eval_count(events: list, rule: dict, ctx: CheckCtx) -> dict:
    """The default check (no predicate keyword): a :class:`CountMonitor` over the rule's
    event/where compared with op/target. The documented fallback when no registered
    predicate key is present."""
    ev_name, where = _resolve_matcher(rule, ctx.indicators)
    return run_monitor(
        CountMonitor(ev_name, where, _norm_op(rule.get("op", ">=")), int(_want(rule))),
        events, ctx.reachable,
    )


# Ordered (spec_key -> canonical registry key) probes: preserve the historical if-elif
# priority and the keyword synonyms (forbid->absent, trajectory->must_order). Probed
# before the generic registry scan so built-in precedence is deterministic even for a
# (degenerate) multi-keyword rule.
_KEY_PROBES = (
    ("absent", "absent"), ("forbid", "absent"),
    ("heartbeat", "heartbeat"),
    ("must_order", "must_order"), ("trajectory", "must_order"),
    ("present", "present"),
    ("ratioMetric", "ratioMetric"),
    ("conforms", "conforms"),
)


def _detect_check_key(rule: dict) -> str | None:
    """The registry key for ``rule`` (``None`` -> the default count check). Built-in keys
    win in historical order; an externally-registered custom key is matched after."""
    for spec_key, canon in _KEY_PROBES:
        if spec_key in rule:
            return canon
    for key in CHECK_REGISTRY:
        if key in rule:
            return key
    return None


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

    Each check is evaluated by a streaming monitor (:mod:`ooptdd.monitor`) fed the event
    prefix in store-timestamp order; the per-check dict carries the three-valued
    ``verdict`` and the ``settled_at`` stream index alongside the collapsed ``passed``.

    The readback window comes from ``lookback_s`` (arg) else the spec's ``timeWindow``
    (OpenSLO rolling window) else the backend default.
    """
    cid = _resolve_cid(spec)
    if ontology is None and spec.get("ontology"):
        from ..domain.ontology import Ontology  # file-first; offline, no KG dependency
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
    # Consume events in store-timestamp order so the monitors' first-occurrence /
    # sequencing / liveness automata see the true arrival stream.
    events = sorted(res.events, key=stream_key)
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
    # Dispatch each rule through the check-predicate registry (the extension seam):
    # detect the rule's predicate keyword, look up its handler (else the default count
    # check). The uniform post-stamp (optional/pending/weight) applies to every check.
    ctx = CheckCtx(reachable=res.reachable, indicators=indicators,
                   ontology=ontology, allow_errors=spec.get("allow_errors"))
    checks = []
    for rule in rules:
        key = _detect_check_key(rule)
        handler = CHECK_REGISTRY[key] if key is not None else _eval_count
        chk = handler(events, rule, ctx)
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
