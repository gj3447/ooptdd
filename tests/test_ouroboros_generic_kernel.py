from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError, replace

import pytest

from ooptdd.ouroboros import (
    CompletionEvidence,
    CompletionPolicy,
    PayloadValidator,
    PolicyEvaluator,
    ProtocolBudget,
    ProtocolDefinition,
    ProtocolEvent,
    ProtocolReceipt,
    ProtocolSnapshot,
    RecoveryPolicy,
    RevisionIdentity,
    TransitionRecord,
    TransitionRule,
    digest_json,
    parse_receipt,
    receipt_from_snapshot,
    start_successor,
    step,
    validate_receipt,
)


def _release_protocol(version: str = "release/v1") -> ProtocolDefinition:
    return ProtocolDefinition(
        name="release-approval",
        version=version,
        evaluator_version="evaluator/v1",
        states=("draft", "approved"),
        events=("approve",),
        initial_state="draft",
        transitions=(TransitionRule("draft", "approve", "approved", "approval"),),
        validators=(PayloadValidator("approval", "v1"),),
        completion=CompletionPolicy("v1", ("approved",), ("reviewer", "owner"), ("manifest",)),
        recovery=RecoveryPolicy("v1"),
    )


def _door_protocol() -> ProtocolDefinition:
    return ProtocolDefinition(
        name="door",
        version="door/v1",
        evaluator_version="evaluator/v1",
        states=("closed", "open"),
        events=("unlock",),
        initial_state="closed",
        transitions=(TransitionRule("closed", "unlock", "open"),),
        completion=CompletionPolicy("v1", ("open",)),
        recovery=RecoveryPolicy("v1"),
    )


class Evaluator:
    version = "evaluator/v1"

    def __init__(self, definition: ProtocolDefinition) -> None:
        self.definition = definition
        self.policy_digest = definition.digest

    def validate_payload(self, name: str, version: str, payload: dict) -> bool:
        return (
            (name, version) == ("approval", "v1")
            and set(payload) == {"authorities", "artifacts"}
            and isinstance(payload["authorities"], list)
            and isinstance(payload["artifacts"], list)
        )

    def evaluate_completion(self, version: str, payload: dict) -> CompletionEvidence:
        if self.definition.name == "door":
            return CompletionEvidence(version == "v1")
        return CompletionEvidence(
            version == "v1",
            tuple(payload.get("authorities", ())),
            tuple(payload.get("artifacts", ())),
        )


def _snapshot(definition: ProtocolDefinition, revision: str = "candidate") -> ProtocolSnapshot:
    return ProtocolSnapshot.initial(
        "workflow", RevisionIdentity("release", revision), definition, ProtocolBudget(3, 2)
    )


def _forged_receipt(
    definition: ProtocolDefinition, history: tuple[TransitionRecord, ...], state: str
) -> ProtocolReceipt:
    revision = RevisionIdentity("release", "candidate")
    body = {
        "schema_version": "ouroboros-receipt/v1",
        "workflow_id": "workflow",
        "material_revision": revision.to_dict(),
        "generation": 0,
        "final_revision": len(history),
        "state": state,
        "policy_digest": definition.digest,
        "history": [vars(item) for item in history],
    }
    content = digest_json(
        body, scope="ouroboros-receipt", schema_version="ouroboros-receipt/v1"
    ).value
    return ProtocolReceipt(
        "ouroboros-receipt/v1",
        "workflow",
        revision,
        0,
        len(history),
        state,
        definition.digest,
        history,
        content,
    )


def test_root_import_does_not_load_an_opt_in_profile():
    program = """
import sys
import ooptdd.ouroboros
assert not any(name.startswith('ooptdd.ouroboros.profiles.') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", program], check=True)


def test_same_kernel_runs_two_workflows_with_vcs_agnostic_revision():
    release = _release_protocol()
    snapshot = _snapshot(release, "release-2026.08")
    event = ProtocolEvent.create(
        "approval-1",
        "approve",
        {"authorities": ["reviewer", "owner"], "artifacts": ["manifest"]},
        release,
    )
    result = step(snapshot, event, Evaluator(release))
    assert result.accepted and result.snapshot.state == "approved"
    assert result.snapshot.material_revision == RevisionIdentity("release", "release-2026.08")

    door = _door_protocol()
    result = step(
        _snapshot(door), ProtocolEvent.create("open-1", "unlock", {}, door), Evaluator(door)
    )
    assert result.accepted and result.snapshot.state == "open"


def test_unknown_and_invalid_inputs_fail_closed():
    definition = _release_protocol()
    evaluator = Evaluator(definition)
    snapshot = _snapshot(definition)
    unknown = ProtocolEvent.create("x", "undeclared", {}, definition)
    assert step(snapshot, unknown, evaluator).rejection_code == "unknown_event"
    invalid = ProtocolEvent.create("y", "approve", {"authorities": []}, definition)
    assert step(snapshot, invalid, evaluator).rejection_code == "invalid_payload"
    unknown_state = step(replace(snapshot, state="not-declared"), invalid, evaluator)
    assert unknown_state.rejection_code == "unknown_state"


def test_replay_and_evaluator_are_digest_bound():
    definition = _door_protocol()
    evaluator = Evaluator(definition)
    event = ProtocolEvent.create("event-1", "unlock", {}, definition)
    first = step(_snapshot(definition), event, evaluator)
    replay = step(first.snapshot, event, evaluator)
    assert replay.accepted and replay.replayed and replay.snapshot is first.snapshot
    evaluator.policy_digest = "0" * 64
    assert step(first.snapshot, event, evaluator).rejection_code == "policy_mismatch"


def test_completion_requirements_are_enforced_beyond_evaluator_satisfaction():
    definition = _release_protocol()
    event = ProtocolEvent.create(
        "approval",
        "approve",
        {"authorities": ["reviewer"], "artifacts": ["manifest"]},
        definition,
    )
    assert step(_snapshot(definition), event, Evaluator(definition)).rejection_code == (
        "completion_policy_failed"
    )


def test_evaluator_exceptions_fail_closed_without_mutating_snapshot():
    class Broken(Evaluator):
        def evaluate_completion(self, version: str, payload: dict) -> CompletionEvidence:
            raise RuntimeError("adapter failed")

    definition = _door_protocol()
    snapshot = _snapshot(definition)
    result = step(snapshot, ProtocolEvent.create("e", "unlock", {}, definition), Broken(definition))
    assert result.rejection_code == "evaluator_failure" and result.snapshot is snapshot


def test_public_values_reject_mutability_and_noncanonical_wire_values():
    definition = _door_protocol()
    with pytest.raises((TypeError, ValueError)):
        ProtocolDefinition(
            "bad",
            "v1",
            "e1",
            ["a"],
            ("go",),
            "a",
            (),
            CompletionPolicy("v1", ("a",)),
            RecoveryPolicy("v1"),
        )
    with pytest.raises(ValueError, match="canonical"):
        ProtocolEvent("e", "unlock", '{"z": 1}', definition.digest)
    with pytest.raises(FrozenInstanceError):
        definition.name = "changed"  # type: ignore[misc]


def test_generic_receipt_is_terminal_policy_bound_and_tamper_evident():
    definition = _door_protocol()
    evaluator: PolicyEvaluator = Evaluator(definition)
    complete = step(
        _snapshot(definition), ProtocolEvent.create("e", "unlock", {}, definition), evaluator
    ).snapshot
    receipt = receipt_from_snapshot(complete, evaluator)
    assert validate_receipt(receipt, evaluator) == ()
    assert parse_receipt(receipt.to_bytes()) == receipt
    with pytest.raises(ValueError, match="canonical"):
        parse_receipt(receipt.to_bytes() + b"\n")
    with pytest.raises(ValueError):
        replace(receipt, state="closed")
    successor = start_successor(complete, RevisionIdentity("release", "next"), evaluator)
    assert successor.generation == 1 and successor.history == ()


def test_self_digested_receipt_with_forged_transition_fails_definition_validation():
    definition = _door_protocol()
    forged_record = TransitionRecord(
        "forged", "bypass", "closed", "open", "1" * 64, definition.digest
    )
    forged = _forged_receipt(definition, (forged_record,), "open")
    assert validate_receipt(forged, Evaluator(definition)) == (
        "history contains an undeclared transition",
    )


def test_receipt_rejects_duplicate_history_and_noninitial_declared_path():
    definition = ProtocolDefinition(
        name="door",
        version="door/v2",
        evaluator_version="evaluator/v1",
        states=("closed", "middle", "open"),
        events=("unlock", "bypass"),
        initial_state="closed",
        transitions=(
            TransitionRule("closed", "unlock", "open"),
            TransitionRule("middle", "bypass", "open"),
        ),
        completion=CompletionPolicy("v1", ("open",)),
        recovery=RecoveryPolicy("v1"),
    )
    from_middle = TransitionRecord("event", "bypass", "middle", "open", "2" * 64, definition.digest)
    forged = _forged_receipt(definition, (from_middle,), "open")
    assert validate_receipt(forged, Evaluator(definition)) == (
        "history does not start at initial_state",
    )
    duplicate = TransitionRecord("event", "bypass", "open", "open", "3" * 64, definition.digest)
    with pytest.raises(ValueError, match="unique"):
        _forged_receipt(definition, (from_middle, duplicate), "open")


def test_empty_history_receipt_only_allows_an_initial_terminal_state():
    definition = _door_protocol()
    fabricated = _forged_receipt(definition, (), "open")
    assert validate_receipt(fabricated, Evaluator(definition)) == (
        "empty history must remain at initial_state",
    )
    fabricated_snapshot = ProtocolSnapshot(
        "workflow",
        RevisionIdentity("release", "candidate"),
        0,
        0,
        "open",
        definition.digest,
        ProtocolBudget(1, 1),
    )
    with pytest.raises(ValueError, match="empty history"):
        receipt_from_snapshot(fabricated_snapshot, Evaluator(definition))

    initially_terminal = ProtocolDefinition(
        name="already-done",
        version="done/v1",
        evaluator_version="evaluator/v1",
        states=("done",),
        events=("noop",),
        initial_state="done",
        transitions=(),
        completion=CompletionPolicy("v1", ("done",)),
        recovery=RecoveryPolicy("v1"),
    )
    empty = receipt_from_snapshot(_snapshot(initially_terminal), Evaluator(initially_terminal))
    assert empty.history == () and validate_receipt(empty, Evaluator(initially_terminal)) == ()


@pytest.mark.parametrize(
    ("authorities", "artifacts"),
    [(("reviewer",), ()), ((), ("manifest",))],
)
def test_initial_terminal_protocol_cannot_bypass_completion_evidence_requirements(
    authorities: tuple[str, ...], artifacts: tuple[str, ...]
):
    with pytest.raises(ValueError, match="initial terminal.*evidence requirements"):
        ProtocolDefinition(
            name="preapproved-release",
            version="release/v1",
            evaluator_version="evaluator/v1",
            states=("approved",),
            events=("noop",),
            initial_state="approved",
            transitions=(),
            completion=CompletionPolicy(
                "v1",
                ("approved",),
                required_authorities=authorities,
                required_artifacts=artifacts,
            ),
            recovery=RecoveryPolicy("v1"),
        )
