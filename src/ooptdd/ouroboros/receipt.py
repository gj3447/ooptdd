"""Canonical, policy-bound generic completion receipts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .identity import canonical_json_bytes, digest_json
from .model import (
    ProtocolSnapshot,
    RevisionIdentity,
    TransitionRecord,
    _digest,
    _integer,
    _text,
)
from .ports import PolicyEvaluator


@dataclass(frozen=True)
class ProtocolReceipt:
    schema_version: str
    workflow_id: str
    material_revision: RevisionIdentity
    generation: int
    final_revision: int
    state: str
    policy_digest: str
    history: tuple[TransitionRecord, ...]
    content_digest: str

    def __post_init__(self) -> None:
        _text(self.schema_version, "receipt schema_version")
        _text(self.workflow_id, "receipt workflow_id")
        _text(self.state, "receipt state")
        _digest(self.policy_digest, "receipt policy_digest")
        _digest(self.content_digest, "receipt content_digest")
        _integer(self.generation, "receipt generation")
        _integer(self.final_revision, "receipt final_revision")
        if not isinstance(self.material_revision, RevisionIdentity):
            raise TypeError("receipt material_revision must be typed")
        if not isinstance(self.history, tuple) or not all(
            isinstance(item, TransitionRecord) for item in self.history
        ):
            raise TypeError("receipt history must be typed")
        if self.final_revision != len(self.history):
            raise ValueError("receipt final_revision must match history")
        if len({item.event_id for item in self.history}) != len(self.history):
            raise ValueError("receipt history event IDs must be unique")
        if any(item.policy_digest != self.policy_digest for item in self.history):
            raise ValueError("receipt history must bind its policy")
        if self.history and (
            self.history[-1].target != self.state
            or any(
                left.target != right.source
                for left, right in zip(self.history, self.history[1:], strict=False)
            )
        ):
            raise ValueError("receipt history must be contiguous and end at state")
        expected = digest_json(
            self.body(), scope="ouroboros-receipt", schema_version=self.schema_version
        ).value
        if self.content_digest != expected:
            raise ValueError("receipt content_digest mismatch")

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workflow_id": self.workflow_id,
            "material_revision": self.material_revision.to_dict(),
            "generation": self.generation,
            "final_revision": self.final_revision,
            "state": self.state,
            "policy_digest": self.policy_digest,
            "history": [vars(item) for item in self.history],
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes({**self.body(), "content_digest": self.content_digest})


def receipt_from_snapshot(
    snapshot: ProtocolSnapshot, evaluator: PolicyEvaluator
) -> ProtocolReceipt:
    definition = evaluator.definition
    if (
        evaluator.version != definition.evaluator_version
        or evaluator.policy_digest != definition.digest
        or snapshot.policy_digest != definition.digest
    ):
        raise ValueError("policy_mismatch")
    if snapshot.state not in definition.completion.terminal_states:
        raise ValueError("receipt_requires_terminal_state")
    history_errors = _definition_history_errors(snapshot.state, snapshot.history, definition)
    if history_errors:
        raise ValueError(history_errors[0])
    schema_version = "ouroboros-receipt/v1"
    body = {
        "schema_version": schema_version,
        "workflow_id": snapshot.workflow_id,
        "material_revision": snapshot.material_revision.to_dict(),
        "generation": snapshot.generation,
        "final_revision": snapshot.revision,
        "state": snapshot.state,
        "policy_digest": snapshot.policy_digest,
        "history": [vars(item) for item in snapshot.history],
    }
    content = digest_json(body, scope="ouroboros-receipt", schema_version=schema_version).value
    return ProtocolReceipt(
        schema_version,
        snapshot.workflow_id,
        snapshot.material_revision,
        snapshot.generation,
        snapshot.revision,
        snapshot.state,
        snapshot.policy_digest,
        snapshot.history,
        content,
    )


def validate_receipt(receipt: ProtocolReceipt, evaluator: PolicyEvaluator) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        definition = evaluator.definition
        expected = digest_json(
            receipt.body(), scope="ouroboros-receipt", schema_version=receipt.schema_version
        ).value
        if receipt.content_digest != expected:
            errors.append("content_digest mismatch")
        if (
            evaluator.version != definition.evaluator_version
            or evaluator.policy_digest != definition.digest
            or receipt.policy_digest != definition.digest
        ):
            errors.append("policy_digest mismatch")
        if receipt.state not in definition.completion.terminal_states:
            errors.append("receipt state is not terminal")
        if receipt.final_revision != len(receipt.history):
            errors.append("final_revision does not match history")
        errors.extend(_definition_history_errors(receipt.state, receipt.history, definition))
    except Exception as error:
        errors.append(f"receipt validation failed: {type(error).__name__}")
    return tuple(errors)


def _definition_history_errors(
    state: str, history: tuple[TransitionRecord, ...], definition: Any
) -> tuple[str, ...]:
    if not history:
        return (
            ()
            if state == definition.initial_state
            else ("empty history must remain at initial_state",)
        )
    errors: list[str] = []
    declared = {(item.source, item.event, item.target) for item in definition.transitions}
    if history[0].source != definition.initial_state:
        errors.append("history does not start at initial_state")
    if any((item.source, item.kind, item.target) not in declared for item in history):
        errors.append("history contains an undeclared transition")
    return tuple(errors)


def parse_receipt(data: bytes) -> ProtocolReceipt:
    """Parse only the exact canonical v1 wire shape."""
    if not isinstance(data, bytes):
        raise TypeError("receipt data must be bytes")
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("receipt must be UTF-8 JSON") from error
    required = {
        "schema_version",
        "workflow_id",
        "material_revision",
        "generation",
        "final_revision",
        "state",
        "policy_digest",
        "history",
        "content_digest",
    }
    if not isinstance(value, dict) or set(value) != required or canonical_json_bytes(value) != data:
        raise ValueError("receipt must be an exact canonical object")
    revision = value["material_revision"]
    if not isinstance(revision, dict) or set(revision) != {"namespace", "value"}:
        raise ValueError("receipt revision has the wrong shape")
    history = value["history"]
    fields = {"event_id", "kind", "source", "target", "intent_digest", "policy_digest"}
    if not isinstance(history, list) or not all(
        isinstance(item, dict) and set(item) == fields for item in history
    ):
        raise ValueError("receipt history has the wrong shape")
    return ProtocolReceipt(
        value["schema_version"],
        value["workflow_id"],
        RevisionIdentity(**revision),
        value["generation"],
        value["final_revision"],
        value["state"],
        value["policy_digest"],
        tuple(TransitionRecord(**item) for item in history),
        value["content_digest"],
    )


__all__ = ("ProtocolReceipt", "parse_receipt", "receipt_from_snapshot", "validate_receipt")
