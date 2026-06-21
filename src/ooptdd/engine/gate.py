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
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.ports import Backend, Clock, QuerySpec, SystemClock, TimeWindow, fetch
from .monitor import (  # the evaluation kernel
    _matches,  # noqa: F401  re-exported for backward compat (ooptdd.mutation)
    _norm_op,
    _resolve_matcher,  # noqa: F401  re-exported for backward compat (ooptdd.mutation)
    compile_check,
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
    if "invariant" in chk:
        inv = chk["invariant"]
        return "invariant:" + (inv if isinstance(inv, str) else "expr")
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


# ---- registered check handlers (thin adapters over the kernel's compile_check) ---- #
# Every built-in handler compiles its rule to the right Monitor via the kernel's single
# source of truth (compile_check) and drives it over the (already stream-ordered) events.
# The batch path here and the live path (monitor.LiveMonitorSet) therefore share one
# rule->automaton compiler and can never diverge. Custom @check predicates (user-registered)
# remain free to return their own dicts — they are a gate-layer seam, not kernel monitors.


def _run(rule: dict, events: list, ctx: CheckCtx) -> dict:
    monitor = compile_check(rule, indicators=ctx.indicators, ontology=ctx.ontology,
                            allow=ctx.allow_errors)
    return run_monitor(monitor, events, ctx.reachable)


@check("absent")
def _check_absent(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("heartbeat")
def _check_heartbeat(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("must_order")
def _check_must_order(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("present")
def _check_present(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("ratioMetric")
def _check_ratio(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("conforms")
def _check_conforms(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("invariant")
def _check_invariant(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


def _eval_count(events: list, rule: dict, ctx: CheckCtx) -> dict:
    """The default check (no predicate keyword): a :class:`CountMonitor` over the rule's
    event/where compared with op/target. The documented fallback when no registered
    predicate key is present."""
    return _run(rule, events, ctx)


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
    ("invariant", "invariant"),
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


# ---- strength / scope signal (honesty, not an oracle) ----------------------- #
# A GREEN gate must not be misread as "the system is correct": it only proves the events the
# author NAMED arrived with the asserted shape. `_strength` classifies a check's discriminating
# power (from the rule alone), so a gate can self-report HOW HARD it asserted — an all
# `existence-only` gate proved tokens were emitted, pinned no field, ordered nothing, forbade
# nothing. Higher strength is still author-vs-author (the `where` value descends from the same
# mental model) — a harder self-check, NOT an external oracle (see METHODOLOGY "log-free zones").
_STRENGTH_BY_KEY = {
    "absent": "forbid", "must_order": "ordered", "ratioMetric": "ratio",
    "heartbeat": "liveness", "conforms": "conformance", "invariant": "invariant",
}


def _strength(rule: dict) -> str:
    """Discriminating-power class of a check (pure, total over every registry key + the default
    count). Low→high: existence-only < bounded < value-pinned/ordered/forbid/threshold <
    ratio/liveness/conformance."""
    key = _detect_check_key(rule)
    if key in _STRENGTH_BY_KEY:
        return _STRENGTH_BY_KEY[key]
    if key == "present":
        ms = rule.get("present") or []
        return "value-pinned" if any(m.get("where") for m in ms) else "existence-only"
    # default count check (and any custom predicate without a richer shape)
    if rule.get("where"):
        return "value-pinned"
    if rule.get("threshold") is not None:
        return "threshold"
    tight = _norm_op(rule.get("op", ">=")) in ("==", "!=", "<=", "<")
    return "bounded" if tight else "existence-only"


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
    clock: Clock | None = None,
) -> dict:
    """Run a gate spec once: read the backend, then judge the events.

    Returns ``{ok, reachable, complete, cid, checks:[...], optional_failed:[labels]}``.
    ``ok`` is true iff the store was reachable, the read was complete, and every *required*
    check passed; optional checks that miss are in ``optional_failed`` but never flip ``ok``.
    ``reachable=False`` (store unreachable / INFRA) and ``complete=False`` (truncated read)
    each keep ``ok`` false regardless — neither is a clean pass.

    Each check is evaluated by a streaming monitor (:mod:`ooptdd.monitor`) fed the event
    prefix in store-timestamp order; the per-check dict carries the three-valued ``verdict``
    and the ``settled_at`` stream index alongside the collapsed ``passed``.

    The readback window comes from ``lookback_s`` (arg) else the spec's ``timeWindow``
    (OpenSLO rolling window) else the backend default. ``clock`` (a :class:`Clock`) is
    injectable so the window is deterministic under test; it defaults to the system clock.
    This function owns the *read*; :func:`evaluate_events` owns the *judgement* and is the
    seam the arrival-poller (:func:`ooptdd.engine.verify.verify_gate`) reuses per poll.
    """
    cid = _resolve_cid(spec)
    if ontology is None and spec.get("ontology"):
        from ..domain.ontology import Ontology  # file-first; offline, no KG dependency
        ontology = Ontology.from_file(spec["ontology"])
    if lookback_s is None:
        lookback_s = duration_s(spec.get("timeWindow", spec.get("time_window")))
    lookback_s = backend.default_lookback_s if lookback_s is None else lookback_s
    future_buffer_s = (
        backend.default_future_buffer_s if future_buffer_s is None else future_buffer_s
    )
    window = TimeWindow.around_now(clock or SystemClock(), lookback_s, future_buffer_s)
    res = fetch(backend, QuerySpec(cid=cid, window=window))
    # getattr default keeps duck-typed/older result objects (no `complete` field) working.
    return evaluate_events(
        spec, res.events, reachable=res.reachable,
        complete=getattr(res, "complete", True), ontology=ontology, cid=cid,
    )


def evaluate_events(
    spec: dict,
    events: list[dict],
    *,
    reachable: bool,
    complete: bool = True,
    ontology=None,
    cid: str | None = None,
) -> dict:
    """Judge an already-fetched event set against a gate spec (no I/O).

    This is the post-read half of :func:`evaluate`, split out so the same monitor dispatch
    runs over a one-shot query *and* over each freshly-polled prefix in the arrival loop —
    a verified arrival and a gate evaluation can therefore never diverge. ``reachable`` /
    ``complete`` come from the :class:`~ooptdd.domain.ports.QueryResult`; a not-reachable or
    not-complete read is never a clean pass.
    """
    cid = cid if cid is not None else _resolve_cid(spec)
    indicators = spec.get("indicators") or {}
    # Consume events in store-timestamp order so the monitors' first-occurrence /
    # sequencing / liveness automata see the true arrival stream.
    events = sorted(events, key=stream_key)
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
    # check). A check passes only over a *clean* read — reachable AND complete; a truncated
    # read (complete=False) is incomplete evidence and gates every check, exactly like an
    # unreachable store. The top-level result still reports the true reachable/complete.
    ctx = CheckCtx(reachable=reachable and complete, indicators=indicators,
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
        chk["strength"] = _strength(rule)  # discriminating-power class (signal, not an oracle)
        checks.append(chk)
    # A check gates only if it is neither optional (#10) nor pending (Pact). Optional/pending
    # misses are surfaced separately so a silently-degraded stream never reads as clean.
    gating = [c for c in checks if not c["optional"] and not c["pending"]]
    # A gate must assert something that can FAIL to be a clean pass. The old `bool(checks)` guard
    # only caught ZERO checks; a gate whose every check is optional/pending (gating==0) ALSO
    # asserts nothing that can fail and must equally not be GREEN — this is the agent-loop's free
    # weakening move (mark-optional / mark-pending to turn a gate green). `vacuous` is the reason.
    asserts_anything = bool(gating)
    vacuous = bool(checks) and not asserts_anything
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
    # A store we never reached (reachable=False) is INFRA and a truncated read
    # (complete=False) is incomplete evidence — neither is ever a clean pass (CLI exit 2). And a
    # gate that asserts nothing GATING (empty expect, or every check optional/pending) is never a
    # clean pass either (`asserts_anything`, above). `scope` reports what — and how hard — this
    # verdict actually asserted, so GREEN cannot be misread as "the system is correct": it is a
    # closed-world claim over the events the author NAMED, not over un-named behavior.
    result = {
        "ok": reachable and complete and asserts_anything and required_ok,
        "reachable": reachable,
        "complete": complete,
        "vacuous": vacuous,
        "cid": cid,
        "checks": checks,
        "scope": {
            "gating": len(gating),
            "optional": sum(1 for c in checks if c["optional"]),
            "pending": sum(1 for c in checks if c["pending"]),
            "total": len(checks),
            "asserts_anything": asserts_anything,
            "by_strength": dict(Counter(c["strength"] for c in gating)),
        },
        "optional_failed": [_label(c) for c in checks if c["optional"] and not c["passed"]],
        "pending_failed": [_label(c) for c in checks if c["pending"] and not c["passed"]],
        "pending_satisfied": [_label(c) for c in checks if c["pending"] and c["passed"]],
    }
    if score is not None:
        result["score"] = score
        result["threshold"] = float(threshold)
    return result


def green_banner(result: dict) -> str:
    """One honest line for a GREEN gate: WHAT (scope) and HOW HARD (strength) it actually
    asserted, so green is not read as "the system is correct". Pure — shared by the CLI."""
    sc = result.get("scope", {})
    bys = sc.get("by_strength") or {}
    profile = " ".join(f"{k}={v}" for k, v in sorted(bys.items())) or "none"
    line = (
        f"GREEN closed-world over {sc.get('total', 0)} named expectation(s): "
        f"{sc.get('gating', 0)} gating, {sc.get('optional', 0)} optional, "
        f"{sc.get('pending', 0)} pending [by-strength: {profile}]. Certifies the named events "
        "ARRIVED with the asserted shape; does NOT certify the system is correct (un-named "
        f"behavior is unobserved). (cid={result.get('cid')})"
    )
    if sc.get("gating") and set(bys) <= {"existence-only"}:
        line += (" WARNING: every gating check is existence-only — proves tokens were emitted, "
                 "not that they had any effect.")
    return line


def lint_spec(spec: dict) -> list[dict]:
    """Static, offline strength audit of a gate spec — the "pseudo-tested gate" detector, run
    BEFORE any events, so a vacuously-satisfiable gate is caught at author time, not after a green
    run. Pure. Returns findings ``[{code, severity, label, message}]`` (``high`` = vacuous/blocking,
    ``medium`` = weak):

    - **VAC0** no expectations at all (`expect:` empty).
    - **VAC1** zero *gating* checks — every check optional/pending; the gate can never fail.
    - **VAC2** `threshold < 1.0` with no `justification:` — a quorum that licenses silent drops.
    - **VAC3** a gating `existence-only` check — proves a token arrived, pins no field/order/forbid.
    """
    rules = list(spec.get("expect", []))
    if not rules:
        return [{"code": "VAC0", "severity": "high", "label": "(spec)",
                 "message": "empty `expect:` — gate declares no expectations, asserts nothing."}]
    out: list[dict] = []
    gating = [r for r in rules if not r.get("optional") and not r.get("pending")]
    if not gating:
        out.append({"code": "VAC1", "severity": "high", "label": "(spec)",
                    "message": "no gating checks — every check is optional/pending; the gate can "
                               "never fail (vacuous). Mark at least one check gating."})
    t = spec.get("threshold")
    if t is not None and float(t) < 1.0 and not spec.get("justification"):
        out.append({"code": "VAC2", "severity": "high", "label": "(spec)",
                    "message": f"threshold {t} < 1.0 silently licenses dropping up to "
                               f"{(1 - float(t)) * 100:.0f}% of expectations every run; add a "
                               "`justification:` field if this quorum is intentional."})
    for i, r in enumerate(gating):
        if _strength(r) == "existence-only":
            out.append({"code": "VAC3", "severity": "medium", "label": _label(r),
                        "message": f"check #{i} ({_label(r)}) is existence-only — proves a token "
                                   "arrived, pins no field/order/forbid. Add a `where`, "
                                   "`must_order`, `absent`, or `invariant` to discriminate."})
    return out


def can_i_deploy(results: list[dict]) -> dict:
    """Pact ``can-i-deploy`` for ooptdd: may we ship, given a set of gate results?

    Yes iff every gate was reachable, complete, and ``ok``. ``pending`` checks never block
    (that is their purpose). A gate that was reachable-but-RED is a hard blocker; one that
    was unreachable OR read incompletely (truncated) is inconclusive — an INFRA hold, not a
    clean pass. Returns ``{deployable, blockers:[cid], inconclusive:[cid], pending:{cid:[..]}}``.
    """
    def _incomplete(r: dict) -> bool:
        return not r["reachable"] or not r.get("complete", True)

    blockers = [r["cid"] for r in results if r["reachable"] and r.get("complete", True)
                and not r["ok"]]
    inconclusive = [r["cid"] for r in results if _incomplete(r)]
    pending = {r["cid"]: r["pending_failed"] for r in results if r.get("pending_failed")}
    return {
        "deployable": not blockers and not inconclusive,
        "blockers": blockers,
        "inconclusive": inconclusive,
        "pending": pending,
    }
