"""The read half: poll a backend and turn what we see into a verdict.

This is where ooptdd earns its "positive" — `ship()` only *claims* delivery; here
we read the store back and assert the records exist.

The verdict is three-valued on purpose (LTL3: ⊤ / ⊥ / ?). A test harness only
ever sees a *prefix* of the trace, so plain boolean truth is wrong:

    present       the test_session record was observed                 (⊤ witness)
    absent        the query worked but the record never showed up       (⊥ — real
                  miss; this is the silent-ingest-loss signal)
    inconclusive  we could not query the store at all (unreachable)     (? — not
                  the system-under-test's fault)

`verify_policy` then maps the verdict + mode to a build decision. Crucially,
`inconclusive` never fails the build even in strict mode: demoting an
infrastructure outage to a falsification is how "timeout" becomes a flaky test.
"""
from __future__ import annotations

import time

from .backends import Backend


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
) -> dict:
    """Poll ``backend`` for the ``test_session`` trace of ``cid``.

    The time window is **recomputed every poll** and extended into the future by
    ``future_buffer_s`` — a record whose store timestamp lands *after* we started
    looking (receive-time / clock skew) would otherwise be missed forever.

    Sleep between attempts = ``min(delay * backoff**(n-1), max_delay)``; the first
    poll is immediate, so fast traces are caught at 0 s and only slow ingest waits.
    """
    lookback_s = backend.default_lookback_s if lookback_s is None else lookback_s
    future_buffer_s = (
        backend.default_future_buffer_s if future_buffer_s is None else future_buffer_s
    )
    queried_ok = False  # did *any* query round-trip succeed? (⊥ vs ? discriminator)

    attempts = max(retries, 1)
    for attempt in range(1, attempts + 1):
        now_us = int(time.time() * 1_000_000)
        res = backend.query(
            cid,
            since_us=now_us - lookback_s * 1_000_000,
            until_us=now_us + future_buffer_s * 1_000_000,
        )
        if res.reachable:
            queried_ok = True
        hits = res.events
        sessions = [h for h in hits if h.get("event") == "test_session"]
        if sessions:
            s = sessions[0]
            outcomes = sum(1 for h in hits if h.get("event") == "test_outcome")
            reasons = []
            if expect_total is not None and s.get("total") != expect_total:
                reasons.append(f"total={s.get('total')}!=expect{expect_total}")
            if expect_total is not None and outcomes < expect_total:
                reasons.append(f"outcomes={outcomes}<total{expect_total}_partial_loss")
            return {
                "ok": not reasons,
                "verdict": "present",
                "attempts": attempt,
                "records": len(hits),
                "outcomes": outcomes,
                "session": {
                    k: s.get(k) for k in ("service", "passed", "failed", "total", "skipped")
                },
                "reasons": reasons,
            }
        if attempt < attempts:
            time.sleep(min(delay * backoff ** (attempt - 1), max_delay))

    verdict = "absent" if queried_ok else "inconclusive"
    reason = (
        "no_test_session_trace_after_poll"
        if queried_ok
        else "backend_unreachable_all_queries_failed"
    )
    return {
        "ok": False,
        "verdict": verdict,
        "attempts": attempts,
        "records": 0,
        "outcomes": 0,
        "session": {},
        "reasons": [reason],
    }


def verify_policy(v: dict, mode: str) -> dict:
    """verdict + mode -> build decision (pure). Single source of CI policy.

    mode: ``warn`` (default — observation never overrides the verdict),
          ``strict`` (a real miss fails the session),
          ``off`` (handled before calling).
    Returns ``{level, fail_build, message}``. Only ``strict`` + ``absent`` fails.
    """
    if v.get("ok"):
        s = v.get("session", {})
        return {
            "level": "ok",
            "fail_build": False,
            "message": (
                f"OK arrival confirmed (session {s.get('passed')}/{s.get('total')}, "
                f"outcomes={v.get('outcomes')}, {v.get('attempts')} attempt)"
            ),
        }
    if v.get("verdict") == "inconclusive":
        return {
            "level": "warn",
            "fail_build": False,
            "message": (
                f"WARN could not query the store (inconclusive: {v.get('reasons')}) — "
                "observability infra unreachable, build unaffected even in strict."
            ),
        }
    fail = mode == "strict"
    mark = "FAIL" if fail else "WARN"
    return {
        "level": "error" if fail else "warn",
        "fail_build": fail,
        "message": (
            f"{mark} arrival NOT confirmed ({v.get('reasons')}) — silent ingest loss suspected"
            + (" — strict: session fails (exit 1)" if fail else " — re-check: ooptdd verify <cid>")
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
    meta: dict | None = None,
) -> dict:
    """Orchestrate build -> ship -> verify -> policy. The plugin calls only this.

    A ship failure is a warning, never a build failure ("observation does not
    override the verdict"). ``mode='off'`` ships but skips verification.
    Returns ``{shipped, messages, fail_build}``.
    """
    from .model import build_outcome_records

    if not reports:
        return {"shipped": 0, "messages": [], "fail_build": False}

    try:
        recs = build_outcome_records(reports, cid=cid, service=service, meta=meta or {})
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

    n_total = len({r["nodeid"] for r in reports})
    try:
        v = verify_trace(
            backend, cid, expect_total=n_total, retries=retries, delay=delay, backoff=backoff
        )
        verdict = verify_policy(v, mode)
        msgs.append(verdict["message"] + ("" if v.get("ok") else f" (cid={cid})"))
        return {"shipped": len(reports), "messages": msgs, "fail_build": verdict["fail_build"]}
    except Exception as exc:
        msgs.append(f"verify skipped ({type(exc).__name__}: {exc})")
        return {"shipped": len(reports), "messages": msgs, "fail_build": False}
