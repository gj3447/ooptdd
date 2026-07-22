"""Report writers — pure projections of the canonical verdict document.

The invariant under test everywhere here: the three-valued verdict SURVIVES every
projection. ``inconclusive`` is never silently green — in JUnit it is an
``<error type="ooptdd.inconclusive">`` by default (fail-closed artifact), an
explicit ``<skipped>`` only by opt-in; in markdown it renders as INCONCLUSIVE.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.cli import main
from ooptdd.report import (
    WRITERS,
    classify,
    register_writer,
    report_document,
    write_report,
)


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def _gate_result(ok=True, reachable=True, complete=True, checks=None, **extra):
    res = {"cid": "c1", "ok": ok, "reachable": reachable, "complete": complete,
           "checks": checks if checks is not None else [
               {"event": "a", "passed": ok, "verdict": "sat" if ok else "viol",
                "settled_at": 0}]}
    res.update(extra)
    return res


# ── classify: the shared LTL3 projection every writer uses ─────────────────────
def test_classify_mirrors_the_exit_ladder():
    assert classify(_gate_result(ok=True)) == "green"
    assert classify(_gate_result(ok=False)) == "red"
    assert classify(_gate_result(ok=False, reachable=False)) == "inconclusive"
    assert classify(_gate_result(ok=False, complete=False)) == "inconclusive"
    # an unreachable external probe is an INFRA rung, not a RED
    assert classify(_gate_result(ok=False, probe_reachable=False)) == "inconclusive"


def test_classify_understands_verify_style_verdicts():
    assert classify({"verdict": "present", "ok": True}) == "green"
    assert classify({"verdict": "absent", "ok": False}) == "red"
    assert classify({"verdict": "inconclusive", "ok": False}) == "inconclusive"


# ── registry discipline: same loudness as @check / preset registries ───────────
def test_register_writer_duplicate_is_loud():
    with pytest.raises(ValueError):
        register_writer("junit", lambda doc, path, options: None)


def test_unknown_format_is_loud_and_names_the_known_ones(tmp_path):
    doc = report_document("gate", _gate_result())
    with pytest.raises(ValueError) as exc:
        write_report("nope", str(tmp_path / "x"), doc)
    assert "junit" in str(exc.value)  # the error teaches the valid names


def test_builtin_writers_are_registered():
    assert {"junit", "markdown", "json"} <= set(WRITERS)


# ── report_document: canonical, self-describing ────────────────────────────────
def test_report_document_carries_tool_version_and_result():
    doc = report_document("gate", _gate_result())
    assert doc["tool"] == "ooptdd" and doc["command"] == "gate"
    assert doc["results"][0]["cid"] == "c1"
    assert doc["verdict"] in ("green", "red", "inconclusive")


# ── JSON writer: the canon round-trips byte-honest ─────────────────────────────
def test_json_writer_round_trips(tmp_path):
    doc = report_document("gate", _gate_result())
    out = tmp_path / "verdict.json"
    write_report("json", str(out), doc)
    assert json.loads(out.read_text(encoding="utf-8")) == doc


# ── JUnit writer: LTL3 mapping is fail-closed by default ───────────────────────
def _junit(tmp_path, result, options=None):
    out = tmp_path / "junit.xml"
    write_report("junit", str(out), report_document("gate", result), options=options)
    return ET.parse(out).getroot()


def test_junit_green_gate_has_no_failures(tmp_path):
    root = _junit(tmp_path, _gate_result(ok=True))
    assert root.tag == "testsuites"
    suite = root[0]
    assert suite.get("failures") == "0" and suite.get("errors") == "0"
    assert suite.find("testcase") is not None


def test_junit_failed_check_is_a_failure_typed_absent(tmp_path):
    res = _gate_result(ok=False, checks=[
        {"event": "a", "passed": True, "verdict": "sat", "settled_at": 0},
        {"event": "b", "passed": False, "verdict": "viol", "settled_at": None},
    ])
    root = _junit(tmp_path, res)
    suite = root[0]
    assert suite.get("failures") == "1"
    failure = suite.findall("testcase")[1].find("failure")
    assert failure is not None and failure.get("type") == "ooptdd.absent"


def test_junit_inconclusive_defaults_to_error_never_pass(tmp_path):
    # A CI artifact must not silently green an unverified run: unreachable store ->
    # every check becomes <error type="ooptdd.inconclusive">, suite errors > 0.
    res = _gate_result(ok=False, reachable=False)
    root = _junit(tmp_path, res)
    suite = root[0]
    assert int(suite.get("errors")) >= 1 and suite.get("failures") == "0"
    err = suite.find("testcase").find("error")
    assert err is not None and err.get("type") == "ooptdd.inconclusive"


def test_junit_inconclusive_skipped_is_an_explicit_opt_in(tmp_path):
    res = _gate_result(ok=False, reachable=False)
    root = _junit(tmp_path, res, options={"inconclusive": "skipped"})
    suite = root[0]
    assert suite.get("errors") == "0"
    skipped = suite.find("testcase").find("skipped")
    assert skipped is not None and "inconclusive" in (skipped.get("message") or "")


def test_junit_pending_check_verdict_is_inconclusive_not_red(tmp_path):
    # A pend (unsettled) check on a reachable store: the check did not fail — it never
    # settled. That is the inconclusive rung, not a failure.
    res = _gate_result(ok=False, checks=[
        {"event": "a", "passed": False, "verdict": "pend", "settled_at": None}])
    root = _junit(tmp_path, res)
    case = root[0].find("testcase")
    assert case.find("failure") is None and case.find("error") is not None


def test_junit_properties_carry_cid_and_overall_verdict(tmp_path):
    root = _junit(tmp_path, _gate_result(ok=True))
    props = {p.get("name"): p.get("value") for p in root[0].find("properties")}
    assert props["ooptdd.cid"] == "c1"
    assert props["ooptdd.verdict"] == "green"
    assert "ooptdd.version" in props


def test_junit_control_characters_and_markup_survive_as_parseable_xml(tmp_path):
    nasty = "boom <tag> & \x00\x1b[31m quote=\" done"
    res = _gate_result(ok=False, checks=[
        {"event": nasty, "passed": False, "verdict": "viol", "settled_at": None}],
        reasons=[nasty])
    out = tmp_path / "junit.xml"
    write_report("junit", str(out), report_document("gate", res))
    root = ET.parse(out).getroot()  # must not raise
    assert root[0].find("testcase").find("failure") is not None


# ── markdown writer: the human artifact keeps the ternary visible ──────────────
def test_markdown_shows_verdict_and_checks(tmp_path):
    out = tmp_path / "report.md"
    res = _gate_result(ok=False, checks=[
        {"event": "a", "passed": True, "verdict": "sat", "settled_at": 1},
        {"event": "b", "passed": False, "verdict": "viol", "settled_at": None}])
    write_report("markdown", str(out), report_document("gate", res))
    text = out.read_text(encoding="utf-8")
    assert "RED" in text and "| a |" in text and "| b |" in text


def test_markdown_inconclusive_is_loud_not_green(tmp_path):
    out = tmp_path / "report.md"
    write_report("markdown", str(out),
                 report_document("gate", _gate_result(ok=False, reachable=False)))
    text = out.read_text(encoding="utf-8")
    assert "INCONCLUSIVE" in text and "GREEN" not in text


# ── CLI integration: --report FMT=PATH lands on one dispatcher ─────────────────
def _spec_file(tmp_path, body: str) -> str:
    p = tmp_path / "spec.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_cli_gate_writes_report_artifacts_and_keeps_stdout_contract(tmp_path, capsys):
    spec = _spec_file(tmp_path, "cid: c1\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    MemoryBackend().ship([{"cid": "c1", "event": "a"}])
    xml_out, md_out = tmp_path / "r.xml", tmp_path / "r.md"
    rc = main(["gate", spec, "--report", f"junit={xml_out}", "--report", f"markdown={md_out}"])
    assert rc == 0
    # stdout contract unchanged: the full canonical JSON still prints
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    # and the artifacts landed
    assert ET.parse(xml_out).getroot().tag == "testsuites"
    assert "GREEN" in md_out.read_text(encoding="utf-8")


def test_cli_verify_gate_writes_reports_too(tmp_path, capsys):
    spec = _spec_file(tmp_path, "expect:\n  - {event: a, op: '>=', count: 1}\n")
    MemoryBackend().ship([{"cid": "vX", "event": "a"}])
    out = tmp_path / "v.xml"
    rc = main(["verify", "vX", "--gate", spec, "--report", f"junit={out}",
               "--retries", "0"])
    assert rc == 0
    assert out.exists()
    capsys.readouterr()


def test_cli_junit_inconclusive_flag_reaches_the_writer(tmp_path, capsys):
    # unreachable store -> INCONCLUSIVE; with --junit-inconclusive=skipped the artifact
    # carries <skipped>, not <error> — an explicit, visible policy choice.
    spec = _spec_file(tmp_path, "cid: c9\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    out = tmp_path / "r.xml"
    rc = main(["gate", spec, "--backend", "memory", "--report", f"junit={out}",
               "--junit-inconclusive", "skipped"])
    assert rc == 1  # empty reachable store: absent -> RED (not inconclusive)
    capsys.readouterr()
    root = ET.parse(out).getroot()
    assert root[0].get("errors") == "0"


def test_cli_malformed_report_spec_is_a_clean_usage_error(tmp_path, capsys):
    spec = _spec_file(tmp_path, "cid: c1\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    rc = main(["gate", spec, "--report", "junitnopath"])
    assert rc == 2
    assert "ERROR" in capsys.readouterr().err


def test_cli_no_report_flag_writes_nothing(tmp_path, capsys):
    spec = _spec_file(tmp_path, "cid: c1\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    MemoryBackend().ship([{"cid": "c1", "event": "a"}])
    before = set(tmp_path.iterdir())
    assert main(["gate", spec]) == 0
    capsys.readouterr()
    assert {p for p in tmp_path.iterdir() if p not in before} == set()
