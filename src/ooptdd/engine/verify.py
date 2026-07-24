"""The read half: poll a backend and turn what we see into a verdict.

This is where ooptdd earns its "positive" — `ship()` only *claims* delivery; here
we read the store back and assert the records exist.

The verdict is three-valued on purpose — the **LTL₃** semantics of Bauer, Leucker &
Schallhart (TOSEM 2011): ⊤ / ⊥ / ?. A monitor only ever sees a *prefix* of the trace,
so plain boolean truth is wrong (ooptdd evaluates a counting/past-time *fragment* of
LTL, not full LTL — see METHODOLOGY.md "What three-valued precisely means"):

    present       the expected record was observed                      (⊤ witness)
    absent        the query worked but the record never showed up        (⊥ — real
                  miss; this is the silent-ingest-loss signal)
    inconclusive  we could not query the store at all (unreachable)      (? — not
                  the system-under-test's fault)

The polling concern (recompute the clock-skew window every attempt, back off, track
⊥-vs-? ) is factored into one generic arrival loop, :func:`poll_until_present`, that is
shape-agnostic: a caller supplies an ``evaluate_prefix`` callback that decides, from each
freshly-queried prefix, whether the thing it is waiting for has arrived. :func:`verify_trace`
(the pytest ``test_session`` summary) and :func:`verify_gate` (an *arbitrary* gate spec for
any domain events, by cid) are both thin specializations of it — the same loop, the same
LTL₃ mapping, the same generic streaming monitor underneath.

`verify_policy` then maps the verdict + mode to a build decision. Crucially,
`inconclusive` never fails the build even in strict mode: demoting an
infrastructure outage to a falsification is how "timeout" becomes a flaky test.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from ..domain.model import build_outcome_records, signature_status
from ..domain.ports import (
    Backend,
    Clock,
    QuerySpec,
    Sleeper,
    SystemClock,
    TimeWindow,
    backend_caps,
    backend_identity,
    fetch,
)
from .gate import evaluate_events
from .monitor import SAT, stream_key

#: A prefix evaluator: given the events queried this poll (stream-ordered) plus the poll
#: context, return a settled verdict body (a dict) to stop now, or None to keep polling.
#: Called once more with ``final=True`` after the last attempt so it can emit its terminal
#: (absent / inconclusive) body. The loop stamps ``attempts`` onto whatever it returns.
PrefixEvaluator = Callable[..., dict | None]


def poll_until_present(
    backend: Backend,
    cid: str,
    evaluate_prefix: PrefixEvaluator,
    *,
    retries: int = 4,
    delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 30.0,
    lookback_s: int | None = None,
    future_buffer_s: int | None = None,
    confirm_rounds: int = 0,
    confirm_delay_s: float = 1.0,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> dict:
    """Generic arrival loop: poll ``backend`` for ``cid`` until ``evaluate_prefix`` settles.

    Owns ONLY the polling concern. Each attempt recomputes ``now`` from the injected
    :class:`~ooptdd.domain.ports.Clock` and the readback window
    (``[now-lookback, now+future_buffer]`` — the future buffer absorbs receive-time / clock
    skew), reads the backend through the typed :func:`~ooptdd.domain.ports.fetch` shim,
    sorts the hits into stream order, and hands them to ``evaluate_prefix``. It tracks
    ``queried_ok`` (did *any* query round-trip succeed?) — the ⊥-absent vs ?-inconclusive
    discriminator — and passes it through. The first poll is immediate; between polls it
    waits ``min(delay*backoff**(n-1), max_delay)`` via the injected ``sleeper`` (so tests
    can drive it with a fake clock and no real delay).
    """
    clock = clock or SystemClock()
    sleeper = sleeper or time.sleep
    lookback_s = backend.default_lookback_s if lookback_s is None else lookback_s
    future_buffer_s = (
        backend.default_future_buffer_s if future_buffer_s is None else future_buffer_s
    )
    # ── arrival policy (the category-killer fix): the backend declares its blind
    # window; a flushable store gets one best-effort flush before the first read; and
    # ABSENT is never concluded while the wait is still inside the window (below).
    visibility_us = backend_caps(backend).query_visibility_delay_ms * 1000
    flush = getattr(backend, "force_flush", None)
    flushed = False
    if callable(flush):
        try:
            flushed = bool(flush())
        except Exception:
            flushed = False  # best-effort: a broken flush endpoint must not gate anything
    started_us = clock.now_us()

    def _stamp_arrival(body: dict, *, extended: bool, confirms: int = 0) -> dict:
        body["arrival"] = {
            "visibility_delay_ms": visibility_us // 1000,
            "waited_ms": max(0, (clock.now_us() - started_us) // 1000),
            "flushed": flushed,
            "extended_for_visibility": extended,
            "confirm_rounds_run": confirms,
        }
        return body

    queried_ok = False  # did *any* query round-trip succeed? (⊥ vs ? discriminator)
    attempts = max(retries, 1)
    last_events: list[dict] = []
    last_reachable = False
    last_complete = True
    last_retry_after = None  # store-sent Retry-After (throttled): honored below

    def _read(attempt: int, *, final: bool):
        nonlocal queried_ok, last_events, last_reachable, last_complete, last_retry_after
        window = TimeWindow.around_now(clock, lookback_s, future_buffer_s)
        res = fetch(backend, QuerySpec(cid=cid, window=window))
        queried_ok = queried_ok or res.reachable
        last_retry_after = getattr(res, "retry_after_s", None)
        events = sorted(res.events, key=stream_key)
        # getattr default keeps duck-typed/older result objects (no `complete` field) working.
        complete = getattr(res, "complete", True)
        last_events, last_reachable, last_complete = events, res.reachable, complete
        return evaluate_prefix(
            events, reachable=res.reachable, complete=complete,
            queried_ok=queried_ok, attempt=attempt, final=final,
        )

    for attempt in range(1, attempts + 1):
        body = _read(attempt, final=False)
        if body is not None:
            body["attempts"] = attempt
            return _stamp_arrival(body, extended=False)
        if attempt < attempts:
            pause = min(delay * backoff ** (attempt - 1), max_delay)
            if last_retry_after:
                # The store TOLD us when to come back (429/503 Retry-After): honor it
                # instead of burning the remaining attempts inside the throttle window.
                pause = max(pause, float(last_retry_after))
            sleeper(pause)
    # Blind-window guard: the budget is spent, but if the store answered and the total
    # wait has not yet covered the store's DECLARED visibility delay, a negative settle
    # would be judging inside the blind window — the exact conflation (ingestion lag
    # read as absence) that killed trace-based testing as a category. Extend once past
    # the window (bounded by the declaration, not by hope) and re-read.
    extended = False
    remaining_us = visibility_us - (clock.now_us() - started_us)
    if queried_ok and remaining_us > 0:
        extended = True
        sleeper(remaining_us / 1_000_000)
        body = _read(attempts, final=False)
        if body is not None:
            body["attempts"] = attempts
            return _stamp_arrival(body, extended=True)
    body = evaluate_prefix(
        last_events, reachable=last_reachable, complete=last_complete,
        queried_ok=queried_ok, attempt=attempts, final=True,
    )
    # Anti-flap confirm: a FINAL-path green passed on the last-read prefix but was
    # not irrevocable (else it would have early-settled above) — a late offender can
    # land right after that read. Re-read confirm_rounds extra times; any round that
    # is no longer green WINS. RED/inconclusive terminals need no re-proof.
    confirms_run = 0
    while body.get("ok") and confirms_run < max(confirm_rounds, 0):
        sleeper(confirm_delay_s)
        confirms_run += 1
        body = _read(attempts, final=True)
    body["attempts"] = attempts
    return _stamp_arrival(body, extended=extended, confirms=confirms_run)


def verify_trace(
    backend: Backend,
    cid: str,
    *,
    expect_total: int | None = None,
    retries: int = 4,
    delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 30.0,
    lookback_s: int | None = None,
    future_buffer_s: int | None = None,
    confirm_rounds: int = 0,
    confirm_delay_s: float = 1.0,
    signing_key: str | None = None,
    require_signature: bool = False,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> dict:
    """Poll ``backend`` for the pytest ``test_session`` trace of ``cid``.

    A thin specialization of :func:`poll_until_present`: the callback below holds the only
    pytest-specific knowledge (the ``test_session``/``test_outcome``/``session_start`` event
    names, the signature check, the outcome-count partial-loss check). Returns the same
    verdict dict shape it always has.
    """
    state = {"saw_start": False}

    def evaluate_prefix(events, *, reachable, complete, queried_ok, attempt, final):
        if not state["saw_start"] and any(
            h.get("event") == "session_start" for h in events
        ):
            state["saw_start"] = True  # heartbeat seen (partial-vs-total-loss discriminator)
        # A confident summary verdict needs a COMPLETE read (a truncated read may have
        # undercounted the outcomes). This branch runs on final AND non-final so a summary
        # that only lands on the last poll is still judged — not dropped to the absent path.
        sessions = [h for h in events if h.get("event") == "test_session"] if complete else []
        if sessions:
            s = sessions[0]
            outcomes = sum(1 for h in events if h.get("event") == "test_outcome")
            declared = s.get("total")
            partial = isinstance(declared, int) and outcomes < declared
            sig_status = signature_status(s, signing_key)
            # DEFINITIVE problems — a forged signature or an expect_total mismatch is not
            # transient and must settle NOW, never be given keep-polling grace. Computed
            # before the partial short-circuit: `total` is attacker-controlled, so a forger
            # could otherwise inflate it to force the partial branch and then rely on a store
            # flap to downgrade a detected sig_invalid to inconclusive/absent (grill F6
            # over-correction, caught in pre-commit verification).
            mismatch = expect_total is not None and declared != expect_total
            sig_bad = sig_status == "invalid" or (require_signature and sig_status != "valid")
            # partial loss on a NON-final poll may be transient — the summary can be indexed
            # before its outcomes (they arrive a poll later). Keep polling ONLY when partial
            # is the sole, transient issue; a definitive problem above settles immediately.
            if partial and not final and not mismatch and not sig_bad:
                return None
            reasons = []
            if partial:
                reasons.append(f"outcomes={outcomes}<session_total{declared}_partial_loss")
            if mismatch:
                reasons.append(f"total={declared}!=expect{expect_total}")
            if sig_status == "invalid":
                reasons.append("sig_invalid_possible_forgery")
            elif require_signature and sig_status != "valid":
                # enforcement on: an unsigned/unverifiable receipt is no longer acceptable
                # (closes the "post an unsigned green" evasion once all producers sign).
                reasons.append(f"signature_required_but_{sig_status}")
            return {
                "ok": not reasons,
                "verdict": "present",
                "started": True,  # a summary implies the run completed
                "sig_status": sig_status,
                "records": len(events),
                "outcomes": outcomes,
                "session": {
                    k: s.get(k) for k in ("service", "passed", "failed", "total", "skipped")
                },
                "reasons": reasons,
            }
        if not final:
            return None  # no confident summary yet — keep polling
        # final: no confident (complete) session summary ever arrived. Order matters —
        # a ⊥ absent requires the LAST read to be reachable AND complete: judging a stale
        # empty prefix from an earlier reachable poll while the last read was unreachable
        # is exactly how a premature empty read forged a strict ⊥ (grill F5). Unreachable /
        # truncated are both `inconclusive` (?); only a clean reachable+complete read that
        # still shows no summary is a real `absent` (⊥) that may fail strict.
        if not queried_ok:
            verdict, reason = "inconclusive", "backend_unreachable_all_queries_failed"
        elif not reachable or not complete:
            verdict, reason = "inconclusive", "last_read_unreachable_or_truncated_no_evidence"
        elif state["saw_start"]:
            # heartbeat arrived but the summary didn't — partial loss, distinct RCA path
            verdict, reason = "absent", "session_started_but_summary_lost"
        else:
            verdict, reason = "absent", "no_test_session_trace_after_poll"
        return {
            "ok": False,
            "verdict": verdict,
            "started": state["saw_start"],
            "records": 0,
            "outcomes": 0,
            "session": {},
            "reasons": [reason],
        }

    return poll_until_present(
        backend, cid, evaluate_prefix, retries=retries, delay=delay, backoff=backoff,
        max_delay=max_delay, lookback_s=lookback_s, future_buffer_s=future_buffer_s,
        confirm_rounds=confirm_rounds, confirm_delay_s=confirm_delay_s,
        clock=clock, sleeper=sleeper,
    )


def _settled_green(result: dict) -> bool:
    """Is this GREEN gate result *irrevocable* over the prefix — i.e. safe to settle
    'present' on a NON-final poll?

    A non-final poll sees only a prefix of the trace. A gate that is ``ok`` over that
    prefix can still be flipped by later-arriving events whenever it carries an
    anti-monotone check: ``absent``/``forbid`` (incl. the injected ``forbid_errors``
    wing), an exact/upper-bound count (``==``/``<=``/``<``/``!=``), ``heartbeat``,
    ``ratioMetric``, ``invariant``, ``metamorphic``, ``conforms`` — all of which pass
    vacuously/provisionally on a violation-free-so-far prefix. Settling early there is
    the forgery path the 2026-07-08 audit named (residual #1): the late violation never
    reaches the verdict.

    The kernel already answers monotonicity per check: LTL₃ ``SAT`` means "no extension
    of this prefix can falsify" (:data:`ooptdd.engine.monitor.SAT`), and only the
    monotone-positive automata (``>=``/``>`` counts, ``present``) ever latch it. So a
    prefix green is settled iff every gating check reports ``verdict == SAT``. A check
    without a kernel verdict (``external:``, custom ``@check`` predicates) is
    conservatively treated as revocable — fail-closed. Signature enforcement
    (``require_signature``) verifies the WHOLE hash chain, which a later off-chain event
    still breaks, so it forbids early settle as well.

    ⚠ ``must_order``/``trajectory`` (OrderMonitor) latch SAT too, but their SAT is only
    valid for extensions appended in TIMESTAMP order — and the poller feeds prefixes in
    INGEST order. A later-ingested event carrying an EARLIER timestamp rewrites the
    first-occurrence map and can flip an ordered SAT to VIOL (grill F1: a real early-settle
    forgery). So an order check is treated as revocable-by-reorder here regardless of its
    within-prefix SAT — a gate with any gating order check must poll to the final window.
    (The within-call batch verdict is unaffected: there the stream is timestamp-sorted.)
    """
    if not result["ok"]:
        return False
    if (result.get("oracle") or {}).get("signature_enforced"):
        return False
    return all(
        c.get("verdict") == SAT and "must_order" not in c  # order SAT is not reorder-stable
        for c in result["checks"]
        if not c.get("optional") and not c.get("pending") and not c.get("tautological")
    )


def verify_gate(
    backend: Backend,
    cid: str,
    spec: dict,
    *,
    retries: int = 4,
    delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 30.0,
    lookback_s: int | None = None,
    future_buffer_s: int | None = None,
    confirm_rounds: int = 0,
    confirm_delay_s: float = 1.0,
    ontology=None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    probe=None,
) -> dict:
    """Poll until an *arbitrary* gate ``spec`` is satisfied for ``cid`` — generic
    arrival verification for any domain events, not just the pytest summary.

    Each poll re-judges the freshly-queried prefix with the very same monitor dispatch the
    one-shot gate uses (:func:`ooptdd.engine.gate.evaluate_events`), so a verified arrival and
    a gate evaluation can never diverge. A non-final poll settles GREEN only when the green is
    *irrevocable* (every gating check latched LTL₃ SAT — see :func:`_settled_green`); a gate
    carrying any anti-monotone check (forbid/absent, exact counts, ...) waits for the final
    poll so a late-arriving violation still flips the verdict. Returns
    ``{ok, verdict, gate, reasons, attempts}`` where ``verdict`` is present (gate GREEN),
    absent (reachable+complete but RED), or inconclusive (never reachable, or every read
    truncated).
    """
    emit_backend = type(backend).__name__
    emit_identity = backend_identity(backend)
    emit_caps = backend_caps(backend)

    def evaluate_prefix(events, *, reachable, complete, queried_ok, attempt, final):
        result = evaluate_events(
            spec, events, reachable=reachable, complete=complete, ontology=ontology, cid=cid,
            probe=probe, emit_backend=emit_backend, emit_identity=emit_identity,
            emit_independent=emit_caps.independent, emit_sampled=emit_caps.samples,
        )
        if not final:
            # Early settle ONLY on an irrevocable green: every gating check latched LTL₃
            # SAT (monotone-positive — no later event can falsify). A green that merely
            # has no violation YET (an anti-monotone check passing on the prefix) keeps
            # polling to the final attempt, so a late-arriving offender still flips it.
            return {"ok": True, "verdict": "present", "gate": result, "reasons": []} \
                if _settled_green(result) else None
        if result["ok"]:
            verdict = "present"
        elif (not result["reachable"] or not result.get("complete", True)
              or not result.get("probe_reachable", True)):
            verdict = "inconclusive"  # unreachable store / truncated read / unreachable probe
        else:
            verdict = "absent"
        reasons = [
            (c.get("event") or c.get("must_order") or c.get("present")
             or c.get("absent") or c.get("conforms") or "check")
            for c in result["checks"] if not c["passed"]
        ]
        return {"ok": result["ok"], "verdict": verdict, "gate": result,
                "reasons": [str(r) for r in reasons]}

    return poll_until_present(
        backend, cid, evaluate_prefix, retries=retries, delay=delay, backoff=backoff,
        max_delay=max_delay, lookback_s=lookback_s, future_buffer_s=future_buffer_s,
        confirm_rounds=confirm_rounds, confirm_delay_s=confirm_delay_s,
        clock=clock, sleeper=sleeper,
    )


def verify_policy(v: dict, mode: str) -> dict:
    """verdict + mode -> build decision (pure). Single source of CI policy.

    mode: ``warn`` (default — observation never overrides the verdict),
          ``strict`` (a real miss fails the session),
          ``off`` (handled before calling).
    Returns ``{level, fail_build, message}``. Only ``strict`` + ``absent`` fails — except a
    *forged* receipt (``sig_status == "invalid"``) always fails, even in warn: catching a
    tampered green is a positive detection, not an inconclusive observation.
    """
    if v.get("sig_status") == "invalid":
        return {
            "level": "error",
            "fail_build": True,
            "message": (
                f"FAIL forged/tampered receipt - HMAC sig invalid ({v.get('reasons')}); "
                "a record with the wrong signing key reached the store."
            ),
        }
    if v.get("ok"):
        s = v.get("session", {})
        # D1 (signing visibility floor): name the signature posture on a GREEN when signing is in
        # play, so a valid green is attested and an unverifiable one is loud. Keyless zero-config
        # (`unsigned`) stays quiet — no signing intent, no banner noise; an unsigned receipt in a
        # keyed env is already RED (enforce-if-keyed), never a silent green.
        sig = v.get("sig_status")
        sig_note = f", sig={sig}" if sig and sig != "unsigned" else ""
        return {
            "level": "ok",
            "fail_build": False,
            "message": (
                f"OK arrival confirmed (session {s.get('passed')}/{s.get('total')}, "
                f"outcomes={v.get('outcomes')}, {v.get('attempts')} attempt){sig_note}"
            ),
        }
    if v.get("verdict") == "inconclusive":
        return {
            "level": "warn",
            "fail_build": False,
            "message": (
                f"WARN could not query the store (inconclusive: {v.get('reasons')}) - "
                "observability infra unreachable, build unaffected even in strict."
            ),
        }
    fail = mode == "strict"
    mark = "FAIL" if fail else "WARN"
    return {
        "level": "error" if fail else "warn",
        "fail_build": fail,
        "message": (
            f"{mark} arrival NOT confirmed ({v.get('reasons')}) - silent ingest loss suspected"
            + (" - strict: session fails (exit 1)" if fail else " - re-check: ooptdd verify <cid>")
        ),
    }


def session_finish(
    backend: Backend,
    reports: list[dict],
    cid: str,
    *,
    service: str = "ooptdd.tests",
    mode: str = "warn",
    retries: int = 4,
    delay: float = 1.0,
    backoff: float = 2.0,
    confirm_rounds: int = 0,
    confirm_delay_s: float = 1.0,
    meta: dict | None = None,
    signing_key: str | None = None,
    require_signature: bool = False,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> dict:
    """Orchestrate build -> ship -> verify -> policy. The plugin calls only this.

    A ship failure is a warning, never a build failure ("observation does not
    override the verdict"). ``mode='off'`` ships but skips verification.
    ``signing_key`` (env-sourced by the caller) HMAC-signs the shipped summary and is used
    to validate it on read-back, so a forged green receipt is caught.
    Returns ``{shipped, messages, fail_build}``.
    """
    if not reports:
        return {"shipped": 0, "messages": [], "fail_build": False}

    try:
        recs = build_outcome_records(
            reports, cid=cid, service=service, meta=meta or {}, signing_key=signing_key
        )
        backend.ship(recs)
    except Exception as exc:  # observation never breaks the build
        return {
            "shipped": 0,
            "fail_build": False,
            "messages": [f"trace ship skipped ({type(exc).__name__}: {exc}); build unaffected"],
        }

    msgs = [f"{len(reports)} test traces shipped (cid={cid})"]
    if mode == "off":
        return {"shipped": len(reports), "messages": msgs, "fail_build": False}

    if not backend_caps(backend).queryable:
        # A write-only backend (e.g. OTLP/otel) has no read side, so arrival can NOT be
        # verified — `strict` over it would otherwise pass silently every run, which is the
        # exact silent-green this tool exists to kill. Surface it loudly; refuse under strict
        # (you asked for enforcement the backend can't provide = a misconfiguration to fix).
        name = type(backend).__name__
        if mode == "strict":
            msgs.append(
                f"FAIL strict verify is impossible: backend {name} is write-only (no query "
                f"side) - pair it with a reader or use a queryable backend (cid={cid})"
            )
            return {"shipped": len(reports), "messages": msgs, "fail_build": True}
        msgs.append(
            f"WARN backend {name} is write-only - arrival NOT verified, ship-only "
            f"(strict would be a no-op here; cid={cid})"
        )
        return {"shipped": len(reports), "messages": msgs, "fail_build": False}

    n_total = len({r["nodeid"] for r in reports})
    try:
        v = verify_trace(
            backend, cid, expect_total=n_total, retries=retries, delay=delay,
            confirm_rounds=confirm_rounds, confirm_delay_s=confirm_delay_s,
            backoff=backoff, signing_key=signing_key, require_signature=require_signature,
            clock=clock, sleeper=sleeper,
        )
        verdict = verify_policy(v, mode)
        msgs.append(verdict["message"] + ("" if v.get("ok") else f" (cid={cid})"))
        return {"shipped": len(reports), "messages": msgs, "fail_build": verdict["fail_build"]}
    except Exception as exc:
        # A crash in the verify PATH is a harness bug, NOT an unreachable store — don't let it
        # masquerade as a clean run (silent green). Surface it as an error; under strict, a
        # broken gate must fail (you asked for enforcement you are not actually getting).
        msgs.append(
            f"verify ERROR ({type(exc).__name__}: {exc}) - harness bug in the gate path, "
            f"NOT an unreachable store; gate integrity unknown (cid={cid})"
        )
        return {"shipped": len(reports), "messages": msgs, "fail_build": mode == "strict"}
