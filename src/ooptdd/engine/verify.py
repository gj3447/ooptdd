"""The read half: poll a backend and turn what we see into a verdict.

This is where ooptdd earns its "positive" — `ship()` only *claims* delivery; here
we read the store back and assert the records exist.

The verdict is three-valued on purpose — the **LTL₃** semantics of Bauer, Leucker &
Schallhart (TOSEM 2011): ⊤ / ⊥ / ?. A monitor only ever sees a *prefix* of the trace,
so plain boolean truth is wrong (ooptdd evaluates a counting/past-time fragment of
LTL rather than full LTL):

    present       the expected record was observed                      (⊤ witness)
    absent        the query worked but the record never showed up        (⊥ — real
                  miss; this is the silent-ingest-loss signal)
    inconclusive  we could not query the store at all (unreachable)      (? — not
                  attributable to the observed system)

The polling concern is factored into :func:`poll_until_present`, a shape-agnostic loop
whose caller supplies a prefix evaluator. :func:`verify_gate` specializes it for arbitrary
event contracts. Specialized vocabulary and policy live in optional adapters.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

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
from ..domain.settings import PollingSettings
from .gate import evaluate_events
from .gate_values import CheckFn, GatePolicy
from .monitor import SAT, stream_key
from .polling import next_poll_delay, resolve_polling_settings

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
    polling: PollingSettings | None = None,
    retries: int | None = None,
    delay: float | None = None,
    backoff: float | None = None,
    max_delay: float | None = None,
    lookback_s: int | None = None,
    future_buffer_s: int | None = None,
    confirm_rounds: int | None = None,
    confirm_delay_s: float | None = None,
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
    waits ``min(delay*backoff**(n-1), max_delay)`` via the injected ``sleeper``, allowing
    deterministic callers to use a fake clock with no real delay.
    """
    polling = resolve_polling_settings(
        polling,
        retries=retries,
        delay=delay,
        backoff=backoff,
        max_delay=max_delay,
        confirm_rounds=confirm_rounds,
        confirm_delay_s=confirm_delay_s,
    )
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
    attempts = polling.retries
    last_events: list[Mapping[str, object]] = []
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
            events,
            reachable=res.reachable,
            complete=complete,
            queried_ok=queried_ok,
            attempt=attempt,
            final=final,
        )

    for attempt in range(1, attempts + 1):
        body = _read(attempt, final=False)
        if body is not None:
            body["attempts"] = attempt
            return _stamp_arrival(body, extended=False)
        if attempt < attempts:
            # The store may tell us when to return (429/503 Retry-After); honor it
            # rather than burning attempts inside the throttle window.
            pause = next_poll_delay(polling, attempt, retry_after_s=last_retry_after)
            sleeper(pause)
    # Blind-window guard: the budget is spent, but if the store answered and the total
    # wait has not yet covered the store's DECLARED visibility delay, a negative settle
    # would be judging inside the blind window — conflating ingestion lag with absence.
    # Extend once past
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
        last_events,
        reachable=last_reachable,
        complete=last_complete,
        queried_ok=queried_ok,
        attempt=attempts,
        final=True,
    )
    if body is None:
        raise RuntimeError("final prefix evaluation must return a terminal verdict")
    # Anti-flap confirm: a FINAL-path green passed on the last-read prefix but was
    # not irrevocable (else it would have early-settled above) — a late offender can
    # land right after that read. Re-read confirm_rounds extra times; any round that
    # is no longer green WINS. RED/inconclusive terminals need no re-proof.
    confirms_run = 0
    while body.get("ok") and confirms_run < polling.confirm_rounds:
        sleeper(polling.confirm_delay_s)
        confirms_run += 1
        confirmed = _read(attempts, final=True)
        if confirmed is None:
            raise RuntimeError("final prefix confirmation must return a terminal verdict")
        body = confirmed
    body["attempts"] = attempts
    return _stamp_arrival(body, extended=extended, confirms=confirms_run)


def _settled_green(result: dict) -> bool:
    """Is this GREEN gate result *irrevocable* over the prefix — i.e. safe to settle
    'present' on a NON-final poll?

    A non-final poll sees only a prefix of the trace. A gate that is ``ok`` over that
    prefix can still be flipped by later-arriving events whenever it carries an
    anti-monotone check: ``absent``/``forbid`` (incl. the injected ``forbid_errors``
    wing), an exact/upper-bound count (``==``/``<=``/``<``/``!=``), ``heartbeat``,
    ``ratioMetric``, ``invariant``, ``metamorphic``, ``conforms`` — all of which pass
    vacuously/provisionally on a violation-free-so-far prefix. Settling early there is
    a forgery path: the late violation never reaches the verdict.

    The kernel already answers monotonicity per check: LTL₃ ``SAT`` means "no extension
    of this prefix can falsify" (:data:`ooptdd.engine.monitor.SAT`), and only the
    monotone-positive automata (``>=``/``>`` counts, ``present``) ever latch it. So a
    prefix green is settled iff every gating check reports ``verdict == SAT``. A check
    without a kernel verdict (``external:``, custom ``@check`` predicates) is
    conservatively treated as revocable — fail-closed. Signature enforcement
    (``require_signature``) verifies the WHOLE hash chain, which a later off-chain event
    still breaks, so it forbids early settle as well.

    ``must_order`` checks latch SAT too, but their SAT is only
    valid for extensions appended in TIMESTAMP order — and the poller feeds prefixes in
    INGEST order. A later-ingested event carrying an EARLIER timestamp rewrites the
    first-occurrence map and can flip an ordered SAT to VIOL. An order check is therefore
    treated as revocable-by-reorder here regardless of its
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
    polling: PollingSettings | None = None,
    retries: int | None = None,
    delay: float | None = None,
    backoff: float | None = None,
    max_delay: float | None = None,
    lookback_s: int | None = None,
    future_buffer_s: int | None = None,
    confirm_rounds: int | None = None,
    confirm_delay_s: float | None = None,
    settle_early: bool = True,
    ontology=None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    probe=None,
    policy: GatePolicy | None = None,
    registry: Mapping[str, CheckFn] | None = None,
    strength_by_key: Mapping[str, str] | None = None,
) -> dict:
    """Poll until an *arbitrary* gate ``spec`` is satisfied for ``cid`` — generic
    arrival verification for arbitrary domain events.

    Each poll re-judges the freshly-queried prefix with the very same monitor dispatch the
    one-shot gate uses (:func:`ooptdd.engine.gate.evaluate_events`), so a verified arrival and
    a gate evaluation can never diverge. A non-final poll settles GREEN only when the green is
    *irrevocable* (every gating check latched LTL₃ SAT — see :func:`_settled_green`); a gate
    carrying any anti-monotone check (forbid/absent, exact counts, ...) waits for the final
    poll so a late-arriving violation still flips the verdict. Set ``settle_early=False``
    when a receipt adapter requires every outcome to come from the bounded-final poll rather
    than an irrevocable positive prefix. Returns ``{ok, verdict, settlement, gate, reasons,
    attempts}`` where ``verdict`` is present (gate GREEN), absent (reachable+complete but RED),
    or inconclusive (never reachable, or every read truncated); ``settlement`` is
    ``irrevocable_prefix`` or ``bounded_final``.
    """
    if not isinstance(settle_early, bool):
        raise TypeError("settle_early must be a boolean")
    emit_backend = type(backend).__name__
    emit_identity = backend_identity(backend)
    emit_caps = backend_caps(backend)

    def evaluate_prefix(events, *, reachable, complete, queried_ok, attempt, final):
        result = evaluate_events(
            spec,
            events,
            reachable=reachable,
            complete=complete,
            ontology=ontology,
            cid=cid,
            probe=probe,
            emit_backend=emit_backend,
            emit_identity=emit_identity,
            emit_independent=emit_caps.independent,
            emit_sampled=emit_caps.samples,
            policy=policy,
            registry=registry,
            strength_by_key=strength_by_key,
        )
        if not final:
            # Early settle ONLY on an irrevocable green: every gating check latched LTL₃
            # SAT (monotone-positive — no later event can falsify). A green that merely
            # has no violation YET (an anti-monotone check passing on the prefix) keeps
            # polling to the final attempt, so a late-arriving offender still flips it.
            return (
                {
                    "ok": True,
                    "verdict": "present",
                    "settlement": "irrevocable_prefix",
                    "gate": result,
                    "reasons": [],
                }
                if settle_early and _settled_green(result)
                else None
            )
        if result["ok"]:
            verdict = "present"
        elif (
            not result["reachable"]
            or not result.get("complete", True)
            or not result.get("probe_reachable", True)
        ):
            verdict = "inconclusive"  # unreachable store / truncated read / unreachable probe
        else:
            verdict = "absent"
        reasons = [
            (
                c.get("event")
                or c.get("must_order")
                or c.get("present")
                or c.get("absent")
                or c.get("conforms")
                or "check"
            )
            for c in result["checks"]
            if not c["passed"]
        ]
        return {
            "ok": result["ok"],
            "verdict": verdict,
            "settlement": "bounded_final",
            "gate": result,
            "reasons": [str(r) for r in reasons],
        }

    return poll_until_present(
        backend,
        cid,
        evaluate_prefix,
        polling=polling,
        retries=retries,
        delay=delay,
        backoff=backoff,
        max_delay=max_delay,
        lookback_s=lookback_s,
        future_buffer_s=future_buffer_s,
        confirm_rounds=confirm_rounds,
        confirm_delay_s=confirm_delay_s,
        clock=clock,
        sleeper=sleeper,
    )
