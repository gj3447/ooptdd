import json
import subprocess
import sys

from ooptdd.adapters.pytest import build_outcome_records
from ooptdd.domain.model import ENVELOPE_SCHEMA, build_event, cloudevents_envelope, correlation_keys


def test_correlation_keys_carry_generic_aliases():
    k = correlation_keys("abc")
    assert k == {"cid": "abc", "correlation_id": "abc"}


def test_cycle_id_is_optional_user_payload_not_a_base_alias():
    event = build_event("abc", "work", level="INFO", cycle_id="user-cycle")

    assert event["cid"] == event["correlation_id"] == "abc"
    assert event["cycle_id"] == "user-cycle"
    assert "cycle_id" not in ENVELOPE_SCHEMA["required"]
    assert "cycle_id" not in ENVELOPE_SCHEMA["properties"]


def test_cloudevents_subject_uses_only_generic_correlation_fields():
    projected = cloudevents_envelope({"event": "work", "cycle_id": "domain-value"})

    assert "subject" not in projected


def test_generic_package_import_does_not_load_or_advertise_pytest_adapter():
    code = """
import json
import sys
import ooptdd
import ooptdd.domain.model as model
import ooptdd.engine.verify as verify
import ooptdd.model
import ooptdd.verify
print(json.dumps({
    "adapter_loaded": "ooptdd.adapters.pytest" in sys.modules,
    "root_exports": sorted(set(ooptdd.__all__) & {
        "build_outcome_records", "build_session_start", "verify_trace",
        "verify_policy", "session_finish"
    }),
    "domain_has_builder": hasattr(model, "build_outcome_records"),
    "engine_has_trace": hasattr(verify, "verify_trace"),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {
        "adapter_loaded": False,
        "root_exports": [],
        "domain_has_builder": False,
        "engine_has_trace": False,
    }


def test_session_tally_uses_distinct_nodeids():
    # one test with a passing call AND a failing teardown must count once, as failed.
    reports = [
        {"nodeid": "t::a", "outcome": "passed", "when": "call", "duration": 0.1},
        {
            "nodeid": "t::a",
            "outcome": "failed",
            "when": "teardown",
            "duration": 0.0,
            "longrepr": "boom",
        },
        {"nodeid": "t::b", "outcome": "passed", "when": "call", "duration": 0.2},
    ]
    recs = build_outcome_records(reports, cid="cid1", service="x")
    sessions = [r for r in recs if r["event"] == "test_session"]
    assert len(sessions) == 1
    s = sessions[0]
    assert s["total"] == 2  # distinct nodeids, not 3 phase reports
    assert s["failed"] == 1
    assert s["passed"] == 1
    assert s["service"] == "x"


def test_failed_outcome_preserves_truncated_error():
    reports = [
        {
            "nodeid": "t::a",
            "outcome": "failed",
            "when": "call",
            "duration": 0.0,
            "longrepr": "E" * 5000,
        }
    ]
    recs = build_outcome_records(reports, cid="c")
    outcome = next(r for r in recs if r["event"] == "test_outcome")
    assert outcome["level"] == "ERROR"
    assert len(outcome["error"]) == 2000  # truncated
