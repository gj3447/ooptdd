"""Audit-ranking nDCG gate (POST_TIER1_ARC front A4): the pipeline refuses unaudited
rankings, and permuting an audited kill ranking goes RED.

Premise correction under test: nDCG is permutation-invariant within an equal relevance
grade, so on an all-killed report no permutation can move the number. The id-sequence
authentication layer (``verify_audit_ranking`` re-deriving ``derive_mutations`` and
recomputing every ``mutation_id``) is therefore the actual integrity defence; the nDCG
value only grades order among MIXED relevances. Both layers are measured here, including
the case where nDCG provably cannot see the tamper and the id layer catches it.
"""

from __future__ import annotations

import json
import math

import pytest
from ooptdd.backends.memory import reset
from ooptdd.engine.gate import load_gate

from ooptdd_mutation.analysis import (
    mutation_report,
    ndcg,
    ranked_kills,
    verify_audit_ranking,
)
from ooptdd_mutation.cli import main

# One kill + two survivors, kill first in derivation order: the present rule's drop
# mutant is caught; the tolerant subset trajectory (target 0.5) lets both rename
# mutants through (the same deterministic survivor mechanism as the A3 lock tests).
MIXED_SPEC = {
    "expect": [
        {"present": [{"event": "deploy"}]},
        {"tool_calls": {"expected": ["a", "b"], "match": "subset", "target": 0.5}},
    ]
}
MIXED_SPEC_TEXT = (
    "cid: mut-rank\n"
    "expect:\n"
    "  - present: [{event: deploy}]\n"
    "  - tool_calls: {expected: [a, b], match: subset, target: 0.5}\n"
)
MIXED_EVENTS = [
    {"event": "deploy"},
    {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "a"},
    {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "b"},
]

# nDCG of a two-item list whose single kill sits second: 1/log2(3), the exact measured
# value a survivor-above-kill promotion must produce at the top of the list.
SECOND_SLOT = round(1 / math.log2(3), 6)


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


# ── ndcg: textbook values and the undefined case ───────────────────────────────────────


def test_ndcg_textbook_values():
    assert ndcg([1, 0]) == 1.0  # kill first: ideal order
    assert ndcg([0, 1]) == SECOND_SLOT  # kill demoted one slot: strictly below 1.0
    assert ndcg([1, 1]) == 1.0  # homogeneous: no order information exists
    assert ndcg([0, 0, 1], k=2) == 0.0  # the kill fell outside the cutoff


def test_ndcg_is_none_when_idcg_is_zero():
    # UNDEFINED, never 0.0 or 1.0 — the caller must refuse, not fill.
    assert ndcg([0, 0]) is None
    assert ndcg([]) is None


# ── ranked_kills: the canonical ranked view ────────────────────────────────────────────


def test_ranked_kills_orders_kills_first_ties_by_canonical_position():
    report = {
        "mutations": [
            {
                "mutation_id": "s0",
                "mutation": "m0",
                "operator": "op",
                "status": "survived",
                "caught": False,
            },
            {
                "mutation_id": "k1",
                "mutation": "m1",
                "operator": "op",
                "status": "killed",
                "caught": True,
            },
            {
                "mutation_id": "k2",
                "mutation": "m2",
                "operator": "op",
                "status": "killed",
                "caught": True,
            },
        ]
    }
    view = ranked_kills(report)
    assert [(r["rank"], r["mutation_id"], r["relevance"]) for r in view] == [
        (1, "k1", 1),
        (2, "k2", 1),
        (3, "s0", 0),
    ]
    assert [r["position"] for r in view] == [1, 2, 0]  # stable: canonical order kept


# ── verify_audit_ranking: authentication before any number ─────────────────────────────


def test_verify_authenticates_the_real_artifact():
    report = mutation_report(MIXED_EVENTS, MIXED_SPEC)
    assert report["status_counts"] == {"killed": 1, "survived": 2}  # the fixture's shape
    res = verify_audit_ranking(report, MIXED_SPEC, MIXED_EVENTS)
    assert res["ok"] is True and res["reason"] is None
    assert res["ndcg"] == 1.0 and res["order_sensitive"] is True and res["n"] == 3


def test_verify_refuses_permuted_report_rows():
    report = mutation_report(MIXED_EVENTS, MIXED_SPEC)
    report["mutations"] = list(reversed(report["mutations"]))
    res = verify_audit_ranking(report, MIXED_SPEC, MIXED_EVENTS)
    assert res["ok"] is False and "re-derived" in res["reason"]
    assert res["ndcg"] is None  # a refused ranking never produces a number


def test_verify_refuses_a_report_from_other_events():
    report = mutation_report(MIXED_EVENTS, MIXED_SPEC)
    foreign = [{**e} for e in MIXED_EVENTS]
    foreign[0] = {"event": "deploy", "extra": "field"}
    res = verify_audit_ranking(report, MIXED_SPEC, foreign)
    assert res["ok"] is False


def test_verify_refuses_rows_without_mutation_id():
    report = mutation_report(MIXED_EVENTS, MIXED_SPEC)
    del report["mutations"][0]["mutation_id"]
    res = verify_audit_ranking(report, MIXED_SPEC, MIXED_EVENTS)
    assert res["ok"] is False and "provenance" in res["reason"]


def test_verify_refuses_a_ranking_that_is_not_a_permutation():
    report = mutation_report(MIXED_EVENTS, MIXED_SPEC)
    ids = [r["mutation_id"] for r in report["mutations"]]
    dropped = verify_audit_ranking(report, MIXED_SPEC, MIXED_EVENTS, ids[:-1])
    foreign = verify_audit_ranking(
        report, MIXED_SPEC, MIXED_EVENTS, [*ids[:-1], "0000000000000000"]
    )
    assert dropped["ok"] is False and "permutation" in dropped["reason"]
    assert foreign["ok"] is False and "permutation" in foreign["reason"]


def test_verify_refuses_all_survivor_report_idcg_undefined():
    spec = {"expect": [{"tool_calls": {"expected": ["a", "b"], "match": "subset", "target": 0.5}}]}
    report = mutation_report(MIXED_EVENTS[1:], spec)
    assert report["status_counts"]["killed"] == 0  # nothing killed: nothing to rank
    res = verify_audit_ranking(report, spec, MIXED_EVENTS[1:])
    assert res["ok"] is False and "IDCG" in res["reason"] and res["ndcg"] is None


def test_all_killed_permutation_is_invisible_to_ndcg_but_the_id_layer_refuses_it():
    # The measured limit of the metric: on the all-killed report every permutation has
    # the same relevance list, so nDCG cannot move — order authority must come from the
    # id-sequence layer, and does.
    spec = {"expect": [{"present": [{"event": "cycle", "where": {"verdict": "PASS"}}]}]}
    events = [{"event": "cycle", "verdict": "PASS"}]
    report = mutation_report(events, spec)
    assert report["survivors"] == [] and report["n"] == 2  # drop + corrupt, both killed
    relevances = [r["relevance"] for r in ranked_kills(report)]
    assert ndcg(relevances) == ndcg(list(reversed(relevances))) == 1.0  # metric is blind
    report["mutations"] = list(reversed(report["mutations"]))
    res = verify_audit_ranking(report, spec, events)
    assert res["ok"] is False and "re-derived" in res["reason"]  # the id layer is not


# ── CLI surface ────────────────────────────────────────────────────────────────────────


def _materialize(tmp_path):
    """Write the mixed fixture to disk and generate its REAL report through the same
    load_gate path the CLI uses. bytes, not text mode (the Windows CRLF lesson)."""
    spec = tmp_path / "gate.yaml"
    spec.write_bytes(MIXED_SPEC_TEXT.encode())
    ev = tmp_path / "events.json"
    ev.write_bytes(json.dumps(MIXED_EVENTS).encode())
    report = mutation_report(MIXED_EVENTS, load_gate(str(spec)))
    rep = tmp_path / "report.json"
    rep.write_bytes(json.dumps(report).encode())
    return spec, ev, rep, report


def test_cli_green_on_the_canonical_ranking(tmp_path, capsys):
    spec, ev, rep, _ = _materialize(tmp_path)
    rc = main(["audit-rank", str(spec), "--events", str(ev), "--report", str(rep), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ndcg"] == 1.0 and out["min_ndcg"] == 1.0 and out["n"] == 3
    assert out["order_sensitive"] is True and out["ranking_source"] == "report-order"
    assert [r["relevance"] for r in out["ranked"]] == [1, 0, 0]  # kills ranked first


def test_cli_permuted_ranking_is_the_measured_red_then_restores(tmp_path, capsys):
    # Negative oracle A: promote a survivor above the kill in the PUBLISHED ranking —
    # authenticated, order-sensitive, measured RED (exit 1) at min-ndcg 1.0. Then
    # restore the canonical order and measure GREEN again.
    spec, ev, rep, report = _materialize(tmp_path)
    ids = [r["mutation_id"] for r in report["mutations"]]  # [kill, survivor, survivor]
    ranking = tmp_path / "ranking.json"
    ranking.write_bytes(json.dumps([ids[1], ids[0], ids[2]]).encode())
    rc = main(
        [
            "audit-rank",
            str(spec),
            "--events",
            str(ev),
            "--report",
            str(rep),
            "--ranking",
            str(ranking),
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 1  # measured RED, not a refusal
    assert out["ndcg"] == SECOND_SLOT  # exactly 1/log2(3): the kill sits one slot low
    ranking.write_bytes(json.dumps(ids).encode())  # restore
    assert (
        main(
            [
                "audit-rank",
                str(spec),
                "--events",
                str(ev),
                "--report",
                str(rep),
                "--ranking",
                str(ranking),
            ]
        )
        == 0
    )


def test_cli_refuses_a_permuted_report_as_unaudited(tmp_path, capsys):
    # Negative oracle B: the REPORT rows are permuted — the id sequence no longer
    # re-derives, so the pipeline refuses to grade at all (exit 2, no number).
    spec, ev, rep, report = _materialize(tmp_path)
    report["mutations"] = list(reversed(report["mutations"]))
    rep.write_bytes(json.dumps(report).encode())
    rc = main(["audit-rank", str(spec), "--events", str(ev), "--report", str(rep)])
    err = capsys.readouterr().err
    assert rc == 2 and "RANKING REFUSED" in err and "re-derived" in err


def test_cli_refuses_an_all_survivor_report(tmp_path, capsys):
    spec = tmp_path / "gate.yaml"
    spec.write_bytes(
        b"cid: mut-rank\nexpect:\n  - tool_calls: {expected: [a, b], match: subset, target: 0.5}\n"
    )
    ev = tmp_path / "events.json"
    ev.write_bytes(json.dumps(MIXED_EVENTS[1:]).encode())
    report = mutation_report(MIXED_EVENTS[1:], load_gate(str(spec)))
    rep = tmp_path / "report.json"
    rep.write_bytes(json.dumps(report).encode())
    rc = main(["audit-rank", str(spec), "--events", str(ev), "--report", str(rep)])
    err = capsys.readouterr().err
    assert rc == 2 and "RANKING REFUSED" in err and "IDCG" in err


def test_cli_refuses_unmeasured_and_baseline_red_reports(tmp_path, capsys):
    spec, ev, rep, report = _materialize(tmp_path)
    rep.write_bytes(
        json.dumps(
            {
                "baseline_green": True,
                "canary_survived": False,
                "score_status": "unmeasured",
                "mutations": [],
            }
        ).encode()
    )
    assert main(["audit-rank", str(spec), "--events", str(ev), "--report", str(rep)]) == 2
    rep.write_bytes(json.dumps({**report, "baseline_green": False}).encode())
    assert main(["audit-rank", str(spec), "--events", str(ev), "--report", str(rep)]) == 2
    assert "RANKING REFUSED" in capsys.readouterr().err


def test_cli_lock_pins_the_spec_sha(tmp_path, capsys):
    import hashlib

    from ooptdd_mutation.analysis import LOCK_SCHEMA

    spec, ev, rep, _ = _materialize(tmp_path)
    lock = tmp_path / "lock.json"
    lock.write_bytes(
        json.dumps(
            {
                "schema": LOCK_SCHEMA,
                "min_score": 0.8,
                "gate_spec_sha256": hashlib.sha256(MIXED_SPEC_TEXT.encode()).hexdigest(),
            }
        ).encode()
    )
    rc = main(
        [
            "audit-rank",
            str(spec),
            "--events",
            str(ev),
            "--report",
            str(rep),
            "--lock",
            str(lock),
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["lock"]["gate_spec_sha256"].startswith(
        hashlib.sha256(MIXED_SPEC_TEXT.encode()).hexdigest()[:12]
    )
    spec.write_bytes((MIXED_SPEC_TEXT + "  - present: [{event: extra}]\n").encode())
    rc = main(
        ["audit-rank", str(spec), "--events", str(ev), "--report", str(rep), "--lock", str(lock)]
    )
    err = capsys.readouterr().err
    assert rc == 2 and "LOCK REFUSED" in err


def test_cli_nan_min_ndcg_is_refused(tmp_path, capsys):
    """`x < nan` is always False — a NaN threshold would silently disable the RED rung,
    so it is a setup refusal (exit 2), never a quiet pass."""
    spec, ev, rep, _ = _materialize(tmp_path)
    rc = main(
        ["audit-rank", str(spec), "--events", str(ev), "--report", str(rep), "--min-ndcg", "nan"]
    )
    err = capsys.readouterr().err
    assert rc == 2 and "NaN" in err
