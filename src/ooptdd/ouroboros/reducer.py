"""Pure transition decisions for caller-defined protocols."""

from __future__ import annotations

from dataclasses import replace

from .model import (
    CompletionEvidence,
    CompletionPolicy,
    ProtocolEvent,
    ProtocolSnapshot,
    RevisionIdentity,
    TransitionRecord,
    TransitionResult,
)
from .ports import PolicyEvaluator
from .receipt import receipt_from_snapshot


def _reject(snapshot: ProtocolSnapshot, code: str) -> TransitionResult:
    return TransitionResult(False, False, snapshot, code)


def step(
    snapshot: ProtocolSnapshot, event: ProtocolEvent, evaluator: PolicyEvaluator
) -> TransitionResult:
    """Apply one event; evaluator failures and policy drift fail closed."""
    try:
        definition = evaluator.definition
        digest = definition.digest
        if (
            evaluator.version != definition.evaluator_version
            or evaluator.policy_digest != digest
            or snapshot.policy_digest != digest
            or event.policy_digest != digest
        ):
            return _reject(snapshot, "policy_mismatch")
        prior = next((item for item in snapshot.history if item.event_id == event.event_id), None)
        if prior:
            if prior.intent_digest == event.intent_digest and prior.policy_digest == digest:
                return TransitionResult(True, True, snapshot)
            return _reject(snapshot, "event_identity_conflict")
        if snapshot.steps_used >= snapshot.budget.max_steps:
            return _reject(snapshot, "step_budget_exhausted")
        if snapshot.state not in definition.states:
            return _reject(snapshot, "unknown_state")
        if snapshot.state in definition.completion.terminal_states:
            return _reject(snapshot, "terminal_state")
        if event.kind not in definition.events:
            return _reject(snapshot, "unknown_event")
        rule = next(
            (
                item
                for item in definition.transitions
                if (item.source, item.event) == (snapshot.state, event.kind)
            ),
            None,
        )
        if rule is None:
            return _reject(snapshot, "unknown_transition")
        payload = event.payload
        if rule.validator:
            policy = next(item for item in definition.validators if item.name == rule.validator)
            if evaluator.validate_payload(policy.name, policy.version, payload) is not True:
                return _reject(snapshot, "invalid_payload")
        recovery_states = {state for state, _ in definition.recovery.allowed}
        if (
            snapshot.state in recovery_states
            and (snapshot.state, event.kind) not in definition.recovery.allowed
        ):
            return _reject(snapshot, "recovery_policy_denied")
        if rule.target in definition.completion.terminal_states:
            evidence = evaluator.evaluate_completion(definition.completion.version, payload)
            if not _completion_holds(definition.completion, evidence):
                return _reject(snapshot, "completion_policy_failed")
        record = TransitionRecord(
            event.event_id, event.kind, snapshot.state, rule.target, event.intent_digest, digest
        )
        updated = replace(
            snapshot,
            state=rule.target,
            revision=snapshot.revision + 1,
            steps_used=snapshot.steps_used + 1,
            history=(*snapshot.history, record),
        )
        return TransitionResult(True, False, updated)
    except Exception:
        return _reject(snapshot, "evaluator_failure")


def _completion_holds(policy: CompletionPolicy, evidence: object) -> bool:
    if not isinstance(evidence, CompletionEvidence) or not evidence.satisfied:
        return False
    return set(policy.required_authorities) <= set(evidence.authorities) and set(
        policy.required_artifacts
    ) <= set(evidence.artifacts)


advance = step


def start_successor(
    snapshot: ProtocolSnapshot, material_revision: RevisionIdentity, evaluator: PolicyEvaluator
) -> ProtocolSnapshot:
    """Start a local successor after structural terminal-receipt validation.

    This helper does not authenticate the predecessor or establish durable lineage.
    """

    definition = evaluator.definition
    if (
        evaluator.version != definition.evaluator_version
        or evaluator.policy_digest != definition.digest
        or snapshot.policy_digest != definition.digest
    ):
        raise ValueError("policy_mismatch")
    if snapshot.state not in definition.completion.terminal_states:
        raise ValueError("successor_requires_terminal_state")
    receipt_from_snapshot(snapshot, evaluator)
    generation = snapshot.generation + 1
    if generation >= snapshot.budget.max_generations:
        raise ValueError("generation_budget_exhausted")
    return ProtocolSnapshot.initial(
        snapshot.workflow_id, material_revision, definition, snapshot.budget, generation=generation
    )


__all__ = ("advance", "start_successor", "step")
