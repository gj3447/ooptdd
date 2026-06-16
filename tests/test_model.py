from ooptdd.model import build_outcome_records, correlation_keys


def test_correlation_keys_carry_three_aliases():
    k = correlation_keys("abc")
    assert k == {"cid": "abc", "correlation_id": "abc", "cycle_id": "abc"}


def test_session_tally_uses_distinct_nodeids():
    # one test with a passing call AND a failing teardown must count once, as failed.
    reports = [
        {"nodeid": "t::a", "outcome": "passed", "when": "call", "duration": 0.1},
        {"nodeid": "t::a", "outcome": "failed", "when": "teardown", "duration": 0.0,
         "longrepr": "boom"},
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
    reports = [{"nodeid": "t::a", "outcome": "failed", "when": "call",
                "duration": 0.0, "longrepr": "E" * 5000}]
    recs = build_outcome_records(reports, cid="c")
    outcome = next(r for r in recs if r["event"] == "test_outcome")
    assert outcome["level"] == "ERROR"
    assert len(outcome["error"]) == 2000  # truncated
