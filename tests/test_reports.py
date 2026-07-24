"""CI report renderers — JUnit XML / markdown from a gate result.

Pins: structure parses, gating failure -> <failure>, INFRA -> <skipped> (never a
failure — inconclusive must not be demoted by a CI badge), optional miss ->
<skipped>, markdown carries the re-verify command. Plus the CLI wiring.
"""
import xml.etree.ElementTree as ET

import ooptdd.engine.gate as gate
from ooptdd.reports import to_junit_xml, to_markdown

CID = "report-cid"


def _res(expect, events, *, reachable=True):
    return gate.evaluate_events({"cid": CID, "expect": expect}, events,
                                reachable=reachable, cid=CID,
                                emit_backend="MemoryBackend", emit_identity="memory:demo")


def _events(n=1):
    return [{"event": "boot", "cid": CID, "_timestamp": i} for i in range(n)]


def test_junit_green_and_red_counts():
    res = _res([{"event": "boot", "op": "gte", "target": 1},
                {"event": "never", "op": "gte", "target": 1}], _events())
    root = ET.fromstring(to_junit_xml(res))
    assert root.tag == "testsuite" and root.get("tests") == "2"
    assert root.get("failures") == "1" and root.get("skipped") == "0"
    fail_cases = [c for c in root.iter("testcase") if c.find("failure") is not None]
    assert len(fail_cases) == 1 and fail_cases[0].get("classname") == CID


def test_junit_infra_is_skipped_not_failure():
    res = _res([{"event": "boot", "op": "gte", "target": 1}], [], reachable=False)
    root = ET.fromstring(to_junit_xml(res))
    assert root.get("failures") == "0" and root.get("skipped") == "1"
    msg = next(root.iter("skipped")).get("message")
    assert "INCONCLUSIVE" in msg


def test_junit_optional_miss_is_skipped():
    res = _res([{"event": "boot", "op": "gte", "target": 1},
                {"event": "nice_to_have", "op": "gte", "target": 1, "optional": True}],
               _events())
    root = ET.fromstring(to_junit_xml(res))
    assert root.get("failures") == "0" and root.get("skipped") == "1"


def test_junit_carries_cid_and_backend_properties():
    res = _res([{"event": "boot", "op": "gte", "target": 1}], _events())
    root = ET.fromstring(to_junit_xml(res))
    props = {p.get("name"): p.get("value") for p in root.iter("property")}
    assert props["cid"] == CID and props["backend"] == "memory:demo"


def test_markdown_verdict_and_reverify_line():
    green = to_markdown(_res([{"event": "boot", "op": "gte", "target": 1}], _events()))
    assert "GREEN" in green and f"ooptdd verify {CID}" in green
    red = to_markdown(_res([{"event": "never", "op": "gte", "target": 1}], _events()))
    assert "RED" in red and "❌ fail" in red
    infra = to_markdown(_res([{"event": "boot"}], [], reachable=False))
    assert "INCONCLUSIVE" in infra and "store unreachable" in infra


# ── grill regressions (2026-07-22 adversarial review) ─────────────────────────


def test_pending_miss_is_skipped_never_failure():
    """A pending check is designed never to gate — rendering its miss as <failure>
    turned a GREEN gate CI-red the moment the report was uploaded (grill 2a)."""
    res = _res([{"event": "boot", "op": "gte", "target": 1},
                {"event": "future_contract", "op": "gte", "target": 1, "pending": True}],
               _events())
    assert res["ok"]
    root = ET.fromstring(to_junit_xml(res))
    assert root.get("failures") == "0" and root.get("skipped") == "1"
    md = to_markdown(res)
    assert "GREEN" in md and "❌" not in md and "pending-miss" in md


def test_suite_level_red_gets_a_synthetic_failure():
    """vacuous/uncorroborated/empty REDs have no failing check row — without a
    synthetic (gate) testcase the artifact reads all-green on a red verdict (2b)."""
    res = _res([], _events())  # empty expect -> suite-level red
    assert not res["ok"]
    root = ET.fromstring(to_junit_xml(res))
    assert root.get("failures") == "1"
    [case] = [c for c in root.iter("testcase") if c.get("name") == "(gate)"]
    assert case.find("failure") is not None
    assert "why red" in to_markdown(res)


def test_threshold_green_never_renders_red():
    """Quorum mode: ok=True with an absorbed miss must not emit <failure> (2b inverse)."""
    res = gate.evaluate_events(
        {"cid": CID, "threshold": 0.5,
         "expect": [{"event": "boot", "op": "gte", "target": 1},
                    {"event": "never", "op": "gte", "target": 1}]},
        _events(), reachable=True, cid=CID,
        emit_backend="MemoryBackend", emit_identity="memory:demo")
    # Assert the precondition outright rather than skip on it — a skip-guard would let a
    # future engine change (threshold flips to RED) silently vacate this test instead of
    # flagging drift (grill smell). 1 of 2 gating checks passed at threshold 0.5 -> GREEN.
    assert res["ok"], "threshold 0.5 with 1/2 gating checks passing must be GREEN"
    root = ET.fromstring(to_junit_xml(res))
    assert root.get("failures") == "0" and root.get("skipped") == "1"


def test_label_survives_rule_shaped_matcher_lists():
    """`ooptdd lint` crashed with TypeError on present:[{event: a}, ...] (grill 2d)."""
    from ooptdd.engine.gate import _label, lint_spec
    assert _label({"present": [{"event": "a"}, {"event": "b", "where": {"s": 1}}]}) \
        == "present:a,b"
    findings = lint_spec({"cid": CID, "expect": [
        {"present": [{"event": "a"}, {"event": "b"}]},
        {"absent": [{"where": {"level": "ERROR"}}]},
    ]})
    assert isinstance(findings, list)  # no TypeError


def test_gate_schema_cheatsheet_covers_every_registered_predicate():
    """The `ooptdd schema gate` cheat-sheet must not drift from the check registry
    (grill 1) — this is the guard the reviewer asked for."""
    import ooptdd.engine.gate as g
    from ooptdd.cli import _GATE_SCHEMA
    for key in g.CHECK_REGISTRY:
        assert key in _GATE_SCHEMA, f"predicate {key!r} missing from _GATE_SCHEMA cheat-sheet"
    for spec_key in ("pin_service", "require_signature", "require_corroboration"):
        assert spec_key in _GATE_SCHEMA


def test_probe_unreachable_renders_inconclusive_not_red():
    """grill HIGH-1: probe_reachable=False is INCONCLUSIVE (CLI exit 2), so the artifact
    must render skipped, never <failure> — the ?→⊥ demotion this library forbids."""
    res = _res([{"event": "boot", "op": "gte", "target": 1}], _events())
    res["probe_reachable"] = False
    res["ok"] = False
    root = ET.fromstring(to_junit_xml(res))
    assert root.get("failures") == "0" and root.get("skipped") == "1"
    assert "INCONCLUSIVE" in to_markdown(res)


def test_control_char_in_cid_keeps_junit_well_formed():
    """grill MEDIUM-5: a cid with an XML-illegal control char (arrivable via OOPTDD_CID)
    must not make the report un-parseable XML."""
    res = _res([{"event": "boot", "op": "gte", "target": 1}], _events())
    res["cid"] = "c\x01id"
    ET.fromstring(to_junit_xml(res))  # would raise ParseError pre-fix


def test_markdown_table_not_injectable_via_offender_names():
    """grill MEDIUM-5: an untrusted observed string with pipes/newlines must not forge
    table cells/rows."""
    res = _res([{"event": "boot", "op": "gte", "target": 1}], _events())
    res["ok"] = False
    res["checks"][0]["passed"] = False
    res["checks"][0]["offenders"] = ["rm | ✅ pass | injected\nnew row"]
    md = to_markdown(res)
    # the newline is collapsed (no forged extra ROW) and pipes are backslash-escaped
    # (rendered as literal, not cell separators — no forged extra CELL)
    injected_line = [ln for ln in md.splitlines() if "injected" in ln]
    assert len(injected_line) == 1  # single row, newline did not split it
    assert "new row" in injected_line[0]  # the "new row" text stayed inside the cell
    assert "\\|" in injected_line[0]  # offender pipes escaped, not raw separators


def test_cli_gate_report_junit(tmp_path, monkeypatch):
    from ooptdd.backends.memory import MemoryBackend, reset
    from ooptdd.cli import main
    reset()
    MemoryBackend().ship([{"event": "boot", "cid": "cli-rep", "correlation_id": "cli-rep",
                           "cycle_id": "cli-rep"}])
    spec = tmp_path / "g.yaml"
    spec.write_text("cid: cli-rep\nexpect:\n  - {event: boot, op: gte, target: 1}\n")
    out = tmp_path / "junit.xml"
    monkeypatch.chdir(tmp_path)  # no pyproject -> default memory backend
    rc = main(["gate", str(spec), "--backend", "memory",
               "--report", "junit", "--report-out", str(out)])
    assert rc == 0
    root = ET.fromstring(out.read_text())
    assert root.get("failures") == "0" and root.get("tests") == "1"
    reset()


# ── the fail-closed INFRA policy (opt-in; the study's resolved mapping conflict) ──
def test_junit_inconclusive_error_mode_is_fail_closed_opt_in():
    # Both wings preserved: default keeps ? as <skipped>; --junit-inconclusive=error
    # renders <error type="ooptdd.inconclusive"> — an infra rung, still never a
    # <failure> (? must not be demoted to falsified even when fail-closed).
    res = _res([{"event": "boot", "op": "gte", "target": 1}], [], reachable=False)
    root = ET.fromstring(to_junit_xml(res, inconclusive="error"))
    assert root.get("errors") == "1" and root.get("failures") == "0"
    assert root.get("skipped") == "0"
    err = next(root.iter("error"))
    assert err.get("type") == "ooptdd.inconclusive" and "INCONCLUSIVE" in err.get("message")


def test_junit_inconclusive_policy_unknown_value_is_loud():
    import pytest
    res = _res([{"event": "boot", "op": "gte", "target": 1}], [], reachable=False)
    with pytest.raises(ValueError):
        to_junit_xml(res, inconclusive="pass")


def test_check_level_inconclusive_marker_cannot_hide_a_red():
    result = {
        "cid": "marker-red",
        "ok": False,
        "reachable": True,
        "complete": True,
        "checks": [{
            "event": "required",
            "passed": False,
            "inconclusive": True,
            "optional": False,
            "pending": False,
        }],
        "oracle": {"emit_identity": "test"},
    }
    root = ET.fromstring(to_junit_xml(result))
    assert root.get("failures") == "1"
    assert root.get("skipped") == "0"


def test_junit_error_mode_does_not_touch_green_or_red(tmp_path):
    green = _res([{"event": "boot", "op": "gte", "target": 1}], _events())
    red = _res([{"event": "never", "op": "gte", "target": 1}], _events())
    g_root = ET.fromstring(to_junit_xml(green, inconclusive="error"))
    r_root = ET.fromstring(to_junit_xml(red, inconclusive="error"))
    assert g_root.get("errors") == "0" and g_root.get("failures") == "0"
    assert r_root.get("errors") == "0" and r_root.get("failures") == "1"


def test_cli_junit_inconclusive_flag(tmp_path, capsys, monkeypatch):
    from ooptdd.cli import main
    spec = tmp_path / "spec.yaml"
    spec.write_text("cid: cli-inc\nexpect:\n  - {event: a, op: '>=', count: 1}\n",
                    encoding="utf-8")
    out = tmp_path / "r.xml"
    # openobserve without env -> constructing succeeds but query is unreachable -> INFRA
    monkeypatch.setenv("OOPTDD_OO_URL", "http://127.0.0.1:1")  # closed port
    rc = main(["gate", str(spec), "--backend", "openobserve",
               "--report", "junit", "--report-out", str(out),
               "--junit-inconclusive", "error"])
    capsys.readouterr()
    assert rc == 2  # INFRA rung unchanged by the report policy
    root = ET.parse(out).getroot()
    assert root.get("errors") >= "1"
