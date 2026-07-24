"""Gate mutation testing — prove a value-checking gate catches deviations and a
mere existence gate surfaces its blind spot.

From the ooptdd-oss prometheus cycle (A5, seed-ooptdd-negwing-mutant-allowlist-20260618).
"""
from __future__ import annotations

import pytest

from ooptdd.backends.memory import reset
from ooptdd.mutation import derive_mutations, mutation_report

_EVENTS = [
    {"event": "request.start"},
    {"event": "cycle", "verdict": "PASS"},
    {"event": "request.end"},
]


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def test_value_checking_gate_catches_every_mutation():
    # gate constrains the verdict value -> both drop and corrupt must flip it RED.
    spec = {"expect": [{"present": [{"event": "cycle", "where": {"verdict": "PASS"}}]}]}
    rep = mutation_report(_EVENTS, spec)
    assert rep["baseline_green"] is True
    assert rep["survivors"] == [] and rep["score"] == 1.0
    labels = {r["mutation"] for r in rep["mutations"]}
    assert any(m.startswith("drop:") for m in labels)
    assert "corrupt:cycle.verdict" in labels  # the value mutation was generated


def test_existence_only_gate_has_a_blind_spot():
    # gate only checks the event exists, not its verdict -> corrupting verdict survives.
    spec = {"expect": [{"present": [{"event": "cycle", "where": {"verdict": "PASS"}}]},
                       {"event": "cycle", "op": ">=", "count": 1}]}
    # build a deliberately weak gate: existence only, no value constraint
    weak = {"expect": [{"present": [{"event": "cycle"}]}]}
    rep = mutation_report(_EVENTS, weak)
    assert rep["baseline_green"] is True
    # dropping cycle is caught; there is no where-field to corrupt, so the only mutation
    # is the drop — a weak gate's blind spot shows up against a value-checking sibling.
    assert all(m["mutation"].startswith("drop:") for m in rep["mutations"])
    # contrast: the value gate generates the corrupt mutation the weak one cannot
    assert any(m.startswith("corrupt:") for m, _ in derive_mutations(_EVENTS, spec))


def test_corrupt_survives_when_gate_ignores_the_value():
    # a gate with a where on a DIFFERENT field than what carries signal: corrupting the
    # unconstrained field is not a should-catch, but corrupting the constrained one is.
    events = [{"event": "cycle", "verdict": "PASS", "station": "A"}]
    spec = {"expect": [{"present": [{"event": "cycle", "where": {"station": "A"}}]}]}
    rep = mutation_report(events, spec)
    # corrupt:cycle.station must be caught (gate constrains station)
    caught = {r["mutation"]: r["caught"] for r in rep["mutations"]}
    assert caught.get("corrupt:cycle.station") is True
    assert rep["survivors"] == []


def test_inject_error_mutation_caught_only_when_gate_forbids_errors(monkeypatch):
    spec = {"forbid_errors": True, "expect": [{"present": [{"event": "request.end"}]}]}
    rep = mutation_report(_EVENTS, spec)
    caught = {r["mutation"]: r["caught"] for r in rep["mutations"]}
    assert caught.get("inject_error") is True

    # without forbid_errors, the inject_error mutation isn't even generated
    spec2 = {"forbid_errors": False, "expect": [{"present": [{"event": "request.end"}]}]}
    monkeypatch.delenv("OOPTDD_FORBID_ERRORS", raising=False)
    labels = {m for m, _ in derive_mutations(_EVENTS, spec2)}
    assert "inject_error" not in labels


def test_baseline_not_green_is_flagged():
    spec = {"expect": [{"present": [{"event": "never.happens"}]}]}
    rep = mutation_report(_EVENTS, spec)
    assert rep["baseline_green"] is False  # inputs don't pass -> score is meaningless


def test_trajectory_negative_wings_get_semantic_injection_not_drop_mutants():
    events = [{"event": "gen_ai.execute_tool", "gen_ai.tool.name": "search"}]
    spec = {"expect": [
        {"forbidden_tools": ["delete_db"]},
        {"forbidden_tool_calls": {"name": "shell", "args": {"command": "rm"}}},
    ]}
    labels = [label for label, _ in derive_mutations(events, spec)]
    assert "inject_forbidden_tool:delete_db" in labels
    assert "inject_forbidden_call:0:shell" in labels
    assert "inject_forbidden_call_corrupt_args:shell" in labels
    assert not any(label.startswith("drop:") for label in labels)


def test_tool_call_trajectory_mutations_are_non_vacuous_and_killed():
    events = [
        {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "plan",
         "gen_ai.tool.call.arguments": {"scope": "repo"}},
        {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "write",
         "gen_ai.tool.call.arguments": {"path": "README.md"}},
    ]
    spec = {"expect": [
        {"tool_calls": {
            "expected": [
                {"name": "plan", "args": {"scope": "repo"}},
                {"name": "write", "args": {"path": "README.md"}},
            ],
            "match": "exact",
            "compare": ["name", "args"],
        }},
        {"forbidden_tool_calls": {"name": "shell", "args": {
            "command": {"contains_any": ["rm -rf", "git reset --hard"]},
        }}},
    ]}
    rep = mutation_report(events, spec)
    assert rep["baseline_green"] is True
    assert rep["n"] >= 6 and rep["eligible"] == rep["n"]
    assert rep["score_status"] == "measured"
    assert rep["score"] == 1.0 and rep["survivors"] == []
    assert rep["status_counts"] == {"killed": rep["n"], "survived": 0}
    assert all(row["mutation_id"] and row["status"] == "killed"
               for row in rep["mutations"])


def test_trajectory_mutation_honors_custom_event_and_attribute_names():
    events = [{"event": "mcp.call", "mcp.tool": "search", "mcp.args": {"q": "cats"}}]
    spec = {"expect": [{"tool_calls": {
        "expected": [{"name": "search", "args": {"q": "cats"}}],
        "compare": ["name", "args"],
        "event": "mcp.call",
        "name_attr": "mcp.tool",
        "args_attr": "mcp.args",
    }}]}
    rep = mutation_report(events, spec)
    assert rep["n"] == 2
    assert rep["score"] == 1.0


def test_matcher_aware_argument_mutants_do_not_accidentally_satisfy_the_gate():
    events = [{
        "event": "gen_ai.execute_tool",
        "gen_ai.tool.name": "shell",
        "gen_ai.tool.call.arguments": {"command": "git status"},
    }]
    spec = {"expect": [{"tool_calls": {
        "expected": [{"name": "shell", "args": {"command": {
            "non_empty": True,
            "not_contains": ["rm -rf"],
        }}}],
        "compare": ["name", "args"],
        "match": "exact",
    }}]}
    rep = mutation_report(events, spec)
    caught = {row["mutation"]: row["caught"] for row in rep["mutations"]}
    assert caught["corrupt_required_args:0:shell.command"] is True
    assert rep["survivors"] == []


def test_forbidden_witness_satisfies_all_composed_matcher_constraints():
    events = [{"event": "gen_ai.execute_tool", "gen_ai.tool.name": "search"}]
    spec = {"expect": [{"forbidden_tool_calls": {
        "name": "shell",
        "args": {"command": {
            "contains_any": ["safe", "danger"],
            "not_contains": ["safe"],
        }},
    }}]}
    mutations = dict(derive_mutations(events, spec))
    witness = mutations["inject_forbidden_call:0:shell"][-1]
    assert witness["gen_ai.tool.call.arguments"]["command"] == "danger"
    rep = mutation_report(events, spec)
    assert not rep["survivors"]


def test_ordered_three_call_gate_mutates_every_adjacent_pair():
    events = [
        {"event": "gen_ai.execute_tool", "gen_ai.tool.name": name}
        for name in ("a", "b", "c")
    ]
    spec = {"expect": [{"tool_calls": {
        "expected": ["a", "b", "c"],
        "match": "exact",
    }}]}
    labels = {label for label, _ in derive_mutations(events, spec)}
    assert "reorder_required_tools:0:a>b" in labels
    assert "reorder_required_tools:1:b>c" in labels


def test_duplicate_required_calls_keep_distinct_argument_mutation_targets():
    events = [
        {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "shell",
         "gen_ai.tool.call.arguments": {"command": command}}
        for command in ("git status", "git diff")
    ]
    spec = {"expect": [{"tool_calls": {
        "expected": [
            {"name": "shell", "args": {"command": "git status"}},
            {"name": "shell", "args": {"command": "git diff"}},
        ],
        "compare": ["name", "args"],
        "match": "exact",
    }}]}
    labels = {label for label, _ in derive_mutations(events, spec)}
    assert "corrupt_required_args:0:shell.command" in labels
    assert "corrupt_required_args:1:shell.command" in labels


def test_every_forbidden_list_entry_gets_an_eligible_semantic_mutant():
    events = [{"event": "gen_ai.execute_tool", "gen_ai.tool.name": "search"}]
    spec = {"expect": [
        {"forbidden_tools": ["delete_db", "shutdown"]},
        {"forbidden_tool_calls": [
            {"name": "shell", "args": {"command": {"contains_any": ["rm -rf"]}}},
            {"name": "curl", "args": {"url": {"contains_any": ["169.254.169.254"]}}},
        ]},
    ]}
    labels = {label for label, _events in derive_mutations(events, spec)}
    assert {"inject_forbidden_tool:delete_db", "inject_forbidden_tool:shutdown"} <= labels
    assert {"inject_forbidden_call:0:shell", "inject_forbidden_call:1:curl"} <= labels
    assert {
        "inject_forbidden_call_corrupt_args:shell",
        "inject_forbidden_call_corrupt_args:curl",
    } <= labels


def test_tolerant_trajectory_threshold_surfaces_a_surviving_required_tool_mutant():
    events = [
        {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "a"},
        {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "b"},
    ]
    spec = {"expect": [{"tool_calls": {
        "expected": ["a", "b"], "match": "subset", "target": 0.5,
    }}]}
    rep = mutation_report(events, spec)
    assert rep["baseline_green"] is True
    assert rep["score"] == 0.0
    assert set(rep["survivors"]) == {
        "rename_required_tool:0:a", "rename_required_tool:1:b",
    }


# ── the drop-all canary: dynamic vacuity cross-check ───────────────────────────
def test_canary_dies_on_a_gate_with_a_positive_expectation():
    events = [{"event": "a"}]
    spec = {"expect": [{"event": "a", "op": ">=", "count": 1}]}
    rep = mutation_report(events, spec)
    assert rep["canary_survived"] is False


def test_canary_survives_a_gate_with_no_gating_positive_expectation():
    # An absent-only gate passes on an EMPTY stream — dropping every event cannot
    # kill it. That is not "the harness is broken" (there is no external runner
    # here): it is the dynamic proof the gate asserts nothing positive — the
    # cross-check of the static vacuity lint.
    events = [{"event": "ok"}]
    spec = {"expect": [{"absent": {"where": {"level": "ERROR"}}}]}
    rep = mutation_report(events, spec)
    assert rep["canary_survived"] is True


def test_cli_canary_survival_is_the_inconclusive_rung(tmp_path, capsys):
    import json as _json

    from ooptdd.cli import main
    spec = tmp_path / "s.yaml"
    spec.write_text("expect:\n  - {absent: {where: {level: ERROR}}}\n", encoding="utf-8")
    ev = tmp_path / "e.json"
    ev.write_text(_json.dumps([{"event": "ok"}]), encoding="utf-8")
    rc = main(["mutate", str(spec), "--events", str(ev), "--min-score", "0.5"])
    capsys.readouterr()
    assert rc == 2  # vacuous-by-measurement — inconclusive, never a clean pass
