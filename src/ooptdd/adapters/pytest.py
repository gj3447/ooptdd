"""Optional pytest report adapter and session-arrival orchestration.

This is the sole home of pytest report fields, ``test_outcome``/``test_session`` event
names, build policy, and the build-ship-verify session workflow. Importing :mod:`ooptdd`
does not load or advertise this adapter; the pytest plugin imports it explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..domain.model import (
    SIG_ALG,
    build_event,
    sign_record,
    signature_status,
)
from ..domain.ports import Backend, Clock, Sleeper, backend_caps
from ..domain.settings import (
    DEFAULT_BACKEND,
    DEFAULT_CID_ENV,
    DEFAULT_ENV_KEYS,
    FALSE_VALUES,
    TRUE_VALUES,
    PollingSettings,
)
from ..engine.verify import poll_until_present

_OUTCOME_RANK: Mapping[str, int] = MappingProxyType({"failed": 2, "skipped": 1, "passed": 0})
PYTEST_VERIFY_MODES = frozenset({"off", "warn", "strict"})


@dataclass(frozen=True)
class PytestEnvironmentKeys:
    """Environment names owned only by the optional pytest adapter."""

    verify: str = DEFAULT_ENV_KEYS.adapter_verify
    enabled: str = DEFAULT_ENV_KEYS.adapter_enabled
    cid_env: str = DEFAULT_ENV_KEYS.adapter_cid_env


@dataclass(frozen=True)
class PytestAdapterSettings:
    """Immutable build-policy settings kept outside the framework runtime."""

    verify: str = "warn"
    enabled: str = "auto"
    cid_env: str = DEFAULT_CID_ENV

    def __post_init__(self) -> None:
        verify = str(self.verify).strip().lower()
        enabled = str(self.enabled).strip().lower() or "auto"
        cid_env = str(self.cid_env).strip()
        if verify not in PYTEST_VERIFY_MODES:
            raise ValueError(
                f"verify must be one of {sorted(PYTEST_VERIFY_MODES)}, got {self.verify!r}"
            )
        if enabled not in {"auto", *TRUE_VALUES, *FALSE_VALUES}:
            raise ValueError(f"enabled must be auto or a boolean value, got {self.enabled!r}")
        if not cid_env:
            raise ValueError("cid_env must be a non-empty string")
        object.__setattr__(self, "verify", verify)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "cid_env", cid_env)

    def is_enabled(self, backend: str) -> bool:
        if self.enabled != "auto":
            return self.enabled in TRUE_VALUES
        return backend == DEFAULT_BACKEND


def build_outcome_records(
    reports: list[dict],
    cid: str,
    *,
    service: str = "ooptdd.tests",
    meta: dict | None = None,
    signing_key: str | None = None,
) -> list[dict]:
    """Convert plain pytest report dictionaries into outcome and session events."""

    records: list[dict] = []
    for report in reports:
        outcome = report["outcome"]
        record = build_event(
            cid,
            "test_outcome",
            service=service,
            level="ERROR" if outcome == "failed" else "INFO",
            test=report["nodeid"],
            outcome=outcome,
            when=report.get("when", "call"),
            duration_s=round(float(report.get("duration", 0.0)), 4),
        )
        if outcome == "failed" and report.get("longrepr"):
            record["error"] = str(report["longrepr"])[:2000]
        records.append(record)

    by_test: dict[str, str] = {}
    for report in reports:
        previous = by_test.get(report["nodeid"])
        if previous is None or _OUTCOME_RANK.get(report["outcome"], 0) > _OUTCOME_RANK.get(
            previous, 0
        ):
            by_test[report["nodeid"]] = report["outcome"]
    passed = sum(outcome == "passed" for outcome in by_test.values())
    failed = sum(outcome == "failed" for outcome in by_test.values())
    skipped = sum(outcome == "skipped" for outcome in by_test.values())
    session = build_event(
        cid,
        "test_session",
        service=service,
        level="ERROR" if failed else "INFO",
        total=len(by_test),
        passed=passed,
        failed=failed,
        skipped=skipped,
        **(meta or {}),
    )
    if signing_key:
        session["sig"] = sign_record(session, signing_key)
        session["sig_alg"] = SIG_ALG
    records.append(session)
    return records


def build_session_start(
    cid: str,
    *,
    service: str = "ooptdd.tests",
    expected_total: int | None = None,
    meta: dict | None = None,
) -> dict:
    """Build the pytest collection heartbeat sent before test execution."""

    record = build_event(
        cid,
        "session_start",
        service=service,
        level="INFO",
        **(meta or {}),
    )
    if expected_total is not None:
        record["expected_total"] = expected_total
    return record


def verify_trace(
    backend: Backend,
    cid: str,
    *,
    expect_total: int | None = None,
    polling: PollingSettings | None = None,
    retries: int | None = None,
    delay: float | None = None,
    backoff: float | None = None,
    max_delay: float | None = None,
    lookback_s: int | None = None,
    future_buffer_s: int | None = None,
    confirm_rounds: int | None = None,
    confirm_delay_s: float | None = None,
    signing_key: str | None = None,
    require_signature: bool = False,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> dict:
    """Poll for the pytest ``test_session`` summary and validate its receipt."""

    state = {"saw_start": False}

    def evaluate_prefix(events, *, reachable, complete, queried_ok, attempt, final):
        if not state["saw_start"] and any(
            event.get("event") == "session_start" for event in events
        ):
            state["saw_start"] = True
        sessions = (
            [event for event in events if event.get("event") == "test_session"] if complete else []
        )
        if sessions:
            session = sessions[0]
            outcomes = sum(event.get("event") == "test_outcome" for event in events)
            declared = session.get("total")
            partial = isinstance(declared, int) and outcomes < declared
            sig_status = signature_status(session, signing_key)
            mismatch = expect_total is not None and declared != expect_total
            sig_bad = sig_status == "invalid" or (require_signature and sig_status != "valid")
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
                reasons.append(f"signature_required_but_{sig_status}")
            return {
                "ok": not reasons,
                "verdict": "present",
                "started": True,
                "sig_status": sig_status,
                "records": len(events),
                "outcomes": outcomes,
                "session": {
                    key: session.get(key)
                    for key in ("service", "passed", "failed", "total", "skipped")
                },
                "reasons": reasons,
            }
        if not final:
            return None
        if not queried_ok:
            verdict, reason = "inconclusive", "backend_unreachable_all_queries_failed"
        elif not reachable or not complete:
            verdict, reason = (
                "inconclusive",
                "last_read_unreachable_or_truncated_no_evidence",
            )
        elif state["saw_start"]:
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


def verify_policy(verdict: dict, mode: str) -> dict:
    """Map a pytest receipt verdict and plugin mode to a build decision."""

    if verdict.get("sig_status") == "invalid":
        return {
            "level": "error",
            "fail_build": True,
            "message": (
                "FAIL forged/tampered receipt - HMAC sig invalid "
                f"({verdict.get('reasons')}); a record with the wrong signing key "
                "reached the store."
            ),
        }
    if verdict.get("ok"):
        session = verdict.get("session", {})
        sig = verdict.get("sig_status")
        sig_note = f", sig={sig}" if sig and sig != "unsigned" else ""
        return {
            "level": "ok",
            "fail_build": False,
            "message": (
                f"OK arrival confirmed (session {session.get('passed')}/"
                f"{session.get('total')}, outcomes={verdict.get('outcomes')}, "
                f"{verdict.get('attempts')} attempt){sig_note}"
            ),
        }
    if verdict.get("verdict") == "inconclusive":
        return {
            "level": "warn",
            "fail_build": False,
            "message": (
                "WARN could not query the store (inconclusive: "
                f"{verdict.get('reasons')}) - observability infra unreachable, "
                "build unaffected even in strict."
            ),
        }
    fail = mode == "strict"
    mark = "FAIL" if fail else "WARN"
    return {
        "level": "error" if fail else "warn",
        "fail_build": fail,
        "message": (
            f"{mark} arrival NOT confirmed ({verdict.get('reasons')}) - "
            "silent ingest loss suspected"
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
    polling: PollingSettings | None = None,
    retries: int | None = None,
    delay: float | None = None,
    backoff: float | None = None,
    max_delay: float | None = None,
    confirm_rounds: int | None = None,
    confirm_delay_s: float | None = None,
    meta: dict | None = None,
    signing_key: str | None = None,
    require_signature: bool = False,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> dict:
    """Build, ship, verify, and apply pytest plugin build policy."""

    if not reports:
        return {"shipped": 0, "messages": [], "fail_build": False}
    try:
        records = build_outcome_records(
            reports,
            cid=cid,
            service=service,
            meta=meta or {},
            signing_key=signing_key,
        )
        backend.ship(records)
    except Exception as exc:
        return {
            "shipped": 0,
            "fail_build": False,
            "messages": [f"trace ship skipped ({type(exc).__name__}: {exc}); build unaffected"],
        }

    messages = [f"{len(reports)} test traces shipped (cid={cid})"]
    if mode == "off":
        return {"shipped": len(reports), "messages": messages, "fail_build": False}
    if not backend_caps(backend).queryable:
        name = type(backend).__name__
        if mode == "strict":
            messages.append(
                f"FAIL strict verify is impossible: backend {name} is write-only (no query "
                f"side) - pair it with a reader or use a queryable backend (cid={cid})"
            )
            return {"shipped": len(reports), "messages": messages, "fail_build": True}
        messages.append(
            f"WARN backend {name} is write-only - arrival NOT verified, ship-only "
            f"(strict would be a no-op here; cid={cid})"
        )
        return {"shipped": len(reports), "messages": messages, "fail_build": False}

    expected = len({report["nodeid"] for report in reports})
    try:
        result = verify_trace(
            backend,
            cid,
            expect_total=expected,
            polling=polling,
            retries=retries,
            delay=delay,
            backoff=backoff,
            max_delay=max_delay,
            confirm_rounds=confirm_rounds,
            confirm_delay_s=confirm_delay_s,
            signing_key=signing_key,
            require_signature=require_signature,
            clock=clock,
            sleeper=sleeper,
        )
        decision = verify_policy(result, mode)
        messages.append(decision["message"] + ("" if result.get("ok") else f" (cid={cid})"))
        return {
            "shipped": len(reports),
            "messages": messages,
            "fail_build": decision["fail_build"],
        }
    except Exception as exc:
        messages.append(
            f"verify ERROR ({type(exc).__name__}: {exc}) - harness bug in the gate path, "
            f"NOT an unreachable store; gate integrity unknown (cid={cid})"
        )
        return {
            "shipped": len(reports),
            "messages": messages,
            "fail_build": mode == "strict",
        }


__all__ = [
    "build_outcome_records",
    "build_session_start",
    "verify_trace",
    "verify_policy",
    "session_finish",
]
