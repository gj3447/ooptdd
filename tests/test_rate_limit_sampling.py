"""Rate-limit honesty + sampled-store evidence cap (F-study verified deltas).

Rate limiting: a 429/503 was already honest at the VERDICT level (unreachable →
inconclusive, never a silent absent). The verified deltas are *diagnosability*
and *budget*: a typed ``error_kind`` on QueryResult (429 vs auth vs timeout vs
other), a parsed ``Retry-After``, and a poller that honors it instead of burning
retry attempts while throttled.

Sampling: a sampled store (head/tail sampling, dropping batch processors) can
prove SOME events arrived but not cross-event causal claims. ``BackendCaps.
samples=True`` caps the evidence-tier ladder at ``arrived`` for store-derived
rungs — ``external_verdict`` is untouched, because a passing separate-source
``external:`` check bypasses the sampled store entirely (that rung's whole point).
"""
from __future__ import annotations

import time
import urllib.error

import pytest

from ooptdd.backends.base import classify_http_error, raise_for_status
from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.domain.ports import BackendCaps, QueryResult
from ooptdd.engine.gate import evaluate, evidence_tier
from ooptdd.engine.verify import verify_gate


def _http_error(code, headers=None):
    return urllib.error.HTTPError("http://x", code, "msg", headers or {}, None)


# ── classification ─────────────────────────────────────────────────────────────
def test_classify_http_error_kinds():
    kind, ra = classify_http_error(_http_error(429, {"Retry-After": "7"}))
    assert kind == "rate_limited" and ra == 7.0
    assert classify_http_error(_http_error(503))[0] == "rate_limited"
    assert classify_http_error(_http_error(401))[0] == "auth"
    assert classify_http_error(_http_error(403))[0] == "auth"
    assert classify_http_error(_http_error(408))[0] == "timeout"
    assert classify_http_error(TimeoutError("t"))[0] == "timeout"
    assert classify_http_error(OSError("dns"))[0] == "other"


def test_raise_for_status_carries_status_and_retry_after():
    class R:
        status = 429
        headers = {"Retry-After": "3"}

    with pytest.raises(OSError) as exc:
        raise_for_status(R())
    kind, ra = classify_http_error(exc.value)
    assert kind == "rate_limited" and ra == 3.0


def test_openobserve_429_is_typed_not_anonymous(monkeypatch):
    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "pw")

    def opener(req, timeout):
        raise _http_error(429, {"Retry-After": "5"})

    oo = OpenObserveBackend(stream="s", org="o", opener=opener)
    res = oo.query("c1", since_us=0, until_us=1)
    assert res.reachable is False
    assert res.error_kind == "rate_limited" and res.retry_after_s == 5.0


# ── the poller honors Retry-After ──────────────────────────────────────────────
class ThrottledBackend:
    """First read: 429 with Retry-After=9. After the sleeper waited >=9s: events."""

    queryable = True
    default_lookback_s = 3600
    default_future_buffer_s = 300

    def __init__(self, clock):
        self.clock = clock
        self.start = clock.now_us()

    def query(self, cid, *, since_us, until_us):
        if self.clock.now_us() - self.start < 9_000_000:
            return QueryResult(reachable=False, error="HTTPError: 429",
                               error_kind="rate_limited", retry_after_s=9.0)
        return QueryResult(reachable=True, events=[{"cid": cid, "event": "a"}])


class FakeClock:
    def __init__(self):
        self.us = int(time.time() * 1_000_000)

    def now_us(self):
        return self.us


class AdvancingSleeper:
    def __init__(self, clock):
        self.clock, self.calls = clock, []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.us += int(seconds * 1_000_000)


SPEC = {"expect": [{"event": "a", "op": ">=", "count": 1}]}


def test_poller_sleeps_retry_after_instead_of_burning_attempts():
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)
    backend = ThrottledBackend(clock)
    res = verify_gate(backend, "c", SPEC, retries=2, delay=0.1,
                      clock=clock, sleeper=sleeper)
    # with plain backoff (0.1s, 0.2s) both attempts land inside the throttle window
    # and the verdict would be inconclusive; honoring Retry-After=9 the second
    # attempt reads clean.
    assert res["verdict"] == "present"
    assert any(s >= 9.0 for s in sleeper.calls)


# ── sampled-store evidence cap ─────────────────────────────────────────────────
def _tier_result(*, sampled, corroborated=0):
    return {
        "reachable": True, "complete": True, "ok": True,
        "sampled": sampled,
        "scope": {"asserts_anything": True, "charge_ratio": 1.0},
        "oracle": {"corroborated": corroborated},
        "checks": [{"passed": True, "strength": "invariant"}],
    }


def test_sampled_store_caps_causal_tier_at_arrived():
    assert evidence_tier(_tier_result(sampled=False)) == "queryable_causal"
    assert evidence_tier(_tier_result(sampled=True)) == "arrived"


def test_sampled_store_never_demotes_external_verdict():
    # the external rung's input bypasses the sampled store — corroboration survives
    assert evidence_tier(_tier_result(sampled=True, corroborated=1)) == "external_verdict"


def test_backend_caps_samples_defaults_false_and_evaluate_stamps_it():
    assert BackendCaps().samples is False

    class SampledBackend:
        queryable = True
        default_lookback_s = 3600
        default_future_buffer_s = 300
        caps = BackendCaps(queryable=True, samples=True)

        def query(self, cid, *, since_us, until_us):
            return QueryResult(reachable=True, events=[{"cid": cid, "event": "a"}])

    res = evaluate(SampledBackend(), {"cid": "s1", "expect": [
        {"event": "a", "op": ">=", "count": 1}]})
    assert res["sampled"] is True and res["ok"] is True
