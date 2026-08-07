from __future__ import annotations

from ooptdd.ouroboros import (
    CompletionEvidence,
    CompletionPolicy,
    ProtocolBudget,
    ProtocolDefinition,
    ProtocolEvent,
    ProtocolSnapshot,
    RecoveryPolicy,
    RevisionIdentity,
    TransitionRule,
    analyze_definition,
    step,
)


class Evaluator:
    version = "evaluator/v1"

    def __init__(self, definition: ProtocolDefinition) -> None:
        self.definition = definition
        self.policy_digest = definition.digest

    def validate_payload(self, name: str, version: str, payload: dict) -> bool:
        return True

    def evaluate_completion(self, version: str, payload: dict) -> CompletionEvidence:
        return CompletionEvidence(True)


def _definition(
    *,
    states: tuple[str, ...],
    events: tuple[str, ...],
    transitions: tuple[TransitionRule, ...],
    terminal_states: tuple[str, ...],
    recovery: tuple[tuple[str, str], ...] = (),
    initial_state: str = "initial",
) -> ProtocolDefinition:
    return ProtocolDefinition(
        name="conformance-fixture",
        version="fixture/v1",
        evaluator_version="evaluator/v1",
        states=states,
        events=events,
        initial_state=initial_state,
        transitions=transitions,
        completion=CompletionPolicy("completion/v1", terminal_states),
        recovery=RecoveryPolicy("recovery/v1", recovery),
    )


def _codes(definition: ProtocolDefinition) -> tuple[str, ...]:
    return tuple(item.code for item in analyze_definition(definition).findings)


def test_reachable_protocol_has_no_findings_and_analysis_is_deterministic():
    definition = _definition(
        states=("initial", "done"),
        events=("finish",),
        transitions=(TransitionRule("initial", "finish", "done"),),
        terminal_states=("done",),
    )

    first = analyze_definition(definition)
    second = analyze_definition(definition)
    assert first == second
    assert first.conformant
    assert first.reachable_states == ("initial", "done")
    assert first.reachable_transition_indexes == (0,)


def test_unreachable_states_and_transition_are_reported_in_declaration_order():
    definition = _definition(
        states=("initial", "done", "orphan", "orphan_done"),
        events=("finish",),
        transitions=(
            TransitionRule("initial", "finish", "done"),
            TransitionRule("orphan", "finish", "orphan_done"),
        ),
        terminal_states=("done", "orphan_done"),
    )

    report = analyze_definition(definition)
    assert report.unreachable_states == ("orphan", "orphan_done")
    assert _codes(definition) == (
        "unreachable_state",
        "unreachable_state",
        "unreachable_transition",
    )
    assert report.findings[-1].transition_index == 1


def test_terminal_outgoing_transition_is_dead_under_reducer_semantics():
    definition = _definition(
        states=("initial", "done", "after"),
        events=("finish", "continue"),
        transitions=(
            TransitionRule("initial", "finish", "done"),
            TransitionRule("done", "continue", "after"),
        ),
        terminal_states=("done",),
    )

    report = analyze_definition(definition)
    assert report.reachable_states == ("initial", "done")
    assert report.unreachable_states == ("after",)
    assert _codes(definition) == ("terminal_outgoing_transition", "unreachable_state")


def test_initial_terminal_without_outgoing_transition_is_conformant():
    definition = _definition(
        states=("initial",),
        events=("noop",),
        transitions=(),
        terminal_states=("initial",),
    )

    assert analyze_definition(definition).conformant


def test_recovery_allowlist_pair_must_name_a_declared_transition():
    definition = _definition(
        states=("initial", "recovery", "done"),
        events=("fail", "resume"),
        transitions=(TransitionRule("initial", "fail", "recovery"),),
        terminal_states=("done",),
        recovery=(("recovery", "resume"),),
    )

    assert "recovery_pair_without_transition" in _codes(definition)


def test_nonallowlisted_recovery_transition_is_dead_but_self_loop_is_valid():
    definition = _definition(
        states=("initial", "recovery", "done", "bypassed"),
        events=("fail", "retry", "resume", "skip"),
        transitions=(
            TransitionRule("initial", "fail", "recovery"),
            TransitionRule("recovery", "retry", "recovery"),
            TransitionRule("recovery", "resume", "done"),
            TransitionRule("recovery", "skip", "bypassed"),
        ),
        terminal_states=("done",),
        recovery=(("recovery", "retry"), ("recovery", "resume")),
    )

    report = analyze_definition(definition)
    assert report.reachable_states == ("initial", "recovery", "done")
    assert report.unreachable_states == ("bypassed",)
    denied = next(
        item for item in report.findings if item.code == "recovery_transition_not_allowlisted"
    )
    assert (denied.state, denied.event, denied.transition_index) == ("recovery", "skip", 3)


def test_invalid_event_and_replay_semantics_match_the_reducer():
    definition = _definition(
        states=("initial", "done"),
        events=("finish", "declared_elsewhere"),
        transitions=(TransitionRule("initial", "finish", "done"),),
        terminal_states=("done",),
    )
    evaluator = Evaluator(definition)
    snapshot = ProtocolSnapshot.initial(
        "workflow",
        RevisionIdentity("fixture", "one"),
        definition,
        ProtocolBudget(3, 1),
    )
    semantics = analyze_definition(definition).invalid_event_semantics

    unknown = step(snapshot, ProtocolEvent.create("u", "unknown", {}, definition), evaluator)
    unmatched = step(
        snapshot,
        ProtocolEvent.create("m", "declared_elsewhere", {}, definition),
        evaluator,
    )
    event = ProtocolEvent.create("f", "finish", {}, definition)
    complete = step(snapshot, event, evaluator)
    fresh_terminal = step(
        complete.snapshot,
        ProtocolEvent.create("late", "declared_elsewhere", {}, definition),
        evaluator,
    )
    replay = step(complete.snapshot, event, evaluator)
    conflict = step(
        complete.snapshot,
        ProtocolEvent.create("f", "finish", {"changed": True}, definition),
        evaluator,
    )

    assert f"reject:{unknown.rejection_code}" in semantics.fresh_rejection_precedence
    assert f"reject:{unmatched.rejection_code}" in semantics.fresh_rejection_precedence
    assert f"reject:{fresh_terminal.rejection_code}" == "reject:terminal_state"
    assert semantics.exact_replay == "accept:unchanged_snapshot"
    assert replay.accepted and replay.replayed and replay.snapshot is complete.snapshot
    assert semantics.conflicting_replay == f"reject:{conflict.rejection_code}"


def test_fresh_event_rejection_precedence_matches_the_reducer():
    definition = _definition(
        states=("initial", "done"),
        events=("finish",),
        transitions=(TransitionRule("initial", "finish", "done"),),
        terminal_states=("done",),
    )
    evaluator = Evaluator(definition)
    snapshot = ProtocolSnapshot.initial(
        "workflow",
        RevisionIdentity("fixture", "one"),
        definition,
        ProtocolBudget(1, 1),
    )
    complete = step(
        snapshot,
        ProtocolEvent.create("finish", "finish", {}, definition),
        evaluator,
    )
    rejected = step(
        complete.snapshot,
        ProtocolEvent.create("late", "undeclared", {}, definition),
        evaluator,
    )
    semantics = analyze_definition(definition).invalid_event_semantics

    assert semantics.fresh_rejection_precedence == (
        "reject:step_budget_exhausted",
        "reject:unknown_state",
        "reject:terminal_state",
        "reject:unknown_event",
        "reject:unknown_transition",
        "reject:invalid_payload",
        "reject:recovery_policy_denied",
        "reject:completion_policy_failed",
    )
    assert rejected.rejection_code == "step_budget_exhausted"
