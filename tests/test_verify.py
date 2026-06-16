"""The heart of ooptdd: the three-valued verdict and the policy on top of it."""
from ooptdd.backends import MemoryBackend
from ooptdd.backends.base import QueryResult
from ooptdd.model import build_outcome_records, build_session_start
from ooptdd.verify import session_finish, verify_policy, verify_trace


class _Unreachable:
    default_lookback_s = 60
    default_future_buffer_s = 0

    def ship(self, events):
        pass

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=False)


def test_present_when_session_shipped():
    b = MemoryBackend()
    b.ship(build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "when": "call"}],
        cid="c", service="x",
    ))
    v = verify_trace(b, "c", expect_total=1, retries=1)
    assert v["verdict"] == "present"
    assert v["ok"] is True


def test_absent_is_a_real_miss_not_inconclusive():
    # query reachable, but nothing stored (drop) -> ⊥ absent
    b = MemoryBackend(drop=True)
    b.ship(build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "when": "call"}], cid="c"))
    v = verify_trace(b, "c", expect_total=1, retries=1)
    assert v["verdict"] == "absent"
    assert v["ok"] is False


def test_inconclusive_when_store_unreachable():
    v = verify_trace(_Unreachable(), "c", expect_total=1, retries=1)
    assert v["verdict"] == "inconclusive"


def test_partial_loss_detected():
    # session says total=3 but only 1 outcome arrived -> not ok (partial loss)
    b = MemoryBackend()
    b.ship([
        {"cid": "c", "event": "test_session", "total": 3, "passed": 3, "service": "x"},
        {"cid": "c", "event": "test_outcome", "outcome": "passed"},
    ])
    v = verify_trace(b, "c", expect_total=3, retries=1)
    assert v["verdict"] == "present"
    assert v["ok"] is False
    assert any("partial_loss" in r for r in v["reasons"])


# ── #5 heartbeat: partial (started, summary lost) vs total (nothing) loss ──────
def test_build_session_start_shape():
    rec = build_session_start("c", service="svc", expected_total=4)
    assert rec["event"] == "session_start" and rec["cid"] == "c"
    assert rec["correlation_id"] == "c" and rec["cycle_id"] == "c"
    assert rec["service"] == "svc" and rec["expected_total"] == 4


def test_started_but_summary_lost_is_distinct_partial():
    # heartbeat arrived, summary did not -> absent but started=True, distinct reason
    b = MemoryBackend()
    b.ship([build_session_start("c", service="x", expected_total=3)])
    v = verify_trace(b, "c", expect_total=3, retries=1)
    assert v["verdict"] == "absent" and v["started"] is True
    assert v["reasons"] == ["session_started_but_summary_lost"]


def test_total_loss_has_no_start_flag():
    # nothing arrived at all -> absent, started=False, the original reason
    b = MemoryBackend(drop=True)
    b.ship([build_session_start("c", service="x")])  # dropped
    v = verify_trace(b, "c", retries=1)
    assert v["verdict"] == "absent" and v["started"] is False
    assert v["reasons"] == ["no_test_session_trace_after_poll"]


# ── #2 anti-fabrication: HMAC signature on the session summary ─────────────────
def _signed(cid, key):
    return build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "when": "call"}],
        cid=cid, service="x", signing_key=key,
    )


def test_signed_record_verifies_valid():
    b = MemoryBackend()
    b.ship(_signed("c", "k"))
    v = verify_trace(b, "c", expect_total=1, retries=1, signing_key="k")
    assert v["sig_status"] == "valid" and v["ok"] is True


def test_tampered_record_is_detected_and_not_ok():
    # genuine signed summary, then a field is tampered after signing -> sig recompute fails
    recs = _signed("c", "k")
    summary = next(r for r in recs if r["event"] == "test_session")
    summary["passed"] = 999  # forge a better-looking result, keep the old sig
    b = MemoryBackend()
    b.ship([summary])
    v = verify_trace(b, "c", retries=1, signing_key="k")
    assert v["sig_status"] == "invalid" and v["ok"] is False
    assert any("forgery" in r for r in v["reasons"])


def test_forgery_fails_build_even_in_warn():
    v = {"ok": False, "verdict": "present", "sig_status": "invalid",
         "reasons": ["sig_invalid_possible_forgery"]}
    assert verify_policy(v, "warn")["fail_build"] is True
    assert verify_policy(v, "strict")["fail_build"] is True


def test_unsigned_is_graceful_noop_when_not_required():
    # sender had no key -> unsigned; verifier with a key does NOT fail (transition-safe)
    b = MemoryBackend()
    b.ship(build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "when": "call"}], cid="c", service="x"))
    v = verify_trace(b, "c", expect_total=1, retries=1, signing_key="k")
    assert v["sig_status"] == "unsigned" and v["ok"] is True


def test_unverifiable_when_verifier_has_no_key():
    b = MemoryBackend()
    b.ship(_signed("c", "k"))
    v = verify_trace(b, "c", expect_total=1, retries=1)  # verifier has no key
    assert v["sig_status"] == "unverifiable" and v["ok"] is True


def test_require_signature_rejects_unsigned():
    # enforcement on: an unsigned receipt is no longer acceptable (closes unsigned-forgery)
    b = MemoryBackend()
    b.ship(build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "when": "call"}], cid="c", service="x"))
    v = verify_trace(b, "c", expect_total=1, retries=1, signing_key="k", require_signature=True)
    assert v["sig_status"] == "unsigned" and v["ok"] is False
    assert any("signature_required" in r for r in v["reasons"])


def test_sign_record_is_deterministic_and_key_sensitive():
    from ooptdd.model import sign_record
    rec = {"cid": "c", "event": "test_session", "total": 1, "passed": 1, "failed": 0, "skipped": 0}
    assert sign_record(rec, "k") == sign_record(rec, "k")
    assert sign_record(rec, "k") != sign_record(rec, "other")


def test_policy_strict_fails_only_on_absent():
    absent = {"ok": False, "verdict": "absent", "reasons": ["x"]}
    incon = {"ok": False, "verdict": "inconclusive", "reasons": ["x"]}
    assert verify_policy(absent, "strict")["fail_build"] is True
    assert verify_policy(absent, "warn")["fail_build"] is False
    # inconclusive never fails the build, even strict (? is not ⊥)
    assert verify_policy(incon, "strict")["fail_build"] is False


def test_session_finish_strict_catches_silent_loss():
    reports = [{"nodeid": f"t::{i}", "outcome": "passed", "when": "call"} for i in range(5)]
    # 5 tests "shipped" but the backend silently drops them
    r = session_finish(MemoryBackend(drop=True), reports, "cid-loss",
                       mode="strict", retries=1)
    assert r["fail_build"] is True
    assert any("silent ingest loss" in m for m in r["messages"])


def test_session_finish_green_when_arrives():
    reports = [{"nodeid": f"t::{i}", "outcome": "passed", "when": "call"} for i in range(5)]
    r = session_finish(MemoryBackend(), reports, "cid-ok", mode="strict", retries=1)
    assert r["fail_build"] is False
    assert any("arrival confirmed" in m for m in r["messages"])


# ── SOLID P2 fixes (2026-06-16): no silent green from a write-only backend or a gate crash ──
class _WriteOnly:
    """A backend with no read side (e.g. OTLP/otel): queryable=False."""

    default_lookback_s = 60
    default_future_buffer_s = 0
    queryable = False

    def ship(self, events):
        pass

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=False)


class _CrashOnVerify:
    """A backend whose query() raises — a bug in the gate path, not an outage."""

    default_lookback_s = 60
    default_future_buffer_s = 0

    def ship(self, events):
        pass

    def query(self, cid, *, since_us, until_us):
        raise RuntimeError("boom in query")


def test_write_only_backend_strict_refuses_not_silent_green():
    # strict over a write-only backend must NOT pass silently (the bug); it refuses loudly.
    reports = [{"nodeid": "t::a", "outcome": "passed", "when": "call"}]
    r = session_finish(_WriteOnly(), reports, "cid", mode="strict", retries=1)
    assert r["fail_build"] is True
    assert any("write-only" in m and "strict" in m for m in r["messages"])


def test_write_only_backend_warn_surfaces_but_passes():
    reports = [{"nodeid": "t::a", "outcome": "passed", "when": "call"}]
    r = session_finish(_WriteOnly(), reports, "cid", mode="warn", retries=1)
    assert r["fail_build"] is False
    assert any("write-only" in m for m in r["messages"])


def test_verify_path_crash_is_surfaced_not_silent():
    reports = [{"nodeid": "t::a", "outcome": "passed", "when": "call"}]
    # warn: surfaced loudly as an ERROR (harness bug), build not failed
    rw = session_finish(_CrashOnVerify(), reports, "cid", mode="warn", retries=1)
    assert rw["fail_build"] is False
    assert any("verify ERROR" in m and "harness bug" in m for m in rw["messages"])
    # strict: a broken gate fails the build (enforcement you asked for is not working)
    rs = session_finish(_CrashOnVerify(), reports, "cid", mode="strict", retries=1)
    assert rs["fail_build"] is True
