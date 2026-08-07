"""Caller-owned ports; the kernel performs no effects itself."""

from __future__ import annotations

from typing import Protocol

from .model import CompletionEvidence, Payload, ProtocolDefinition, ProtocolSnapshot


class PolicyEvaluator(Protocol):
    definition: ProtocolDefinition
    version: str
    policy_digest: str

    def validate_payload(self, name: str, version: str, payload: Payload) -> bool: ...

    def evaluate_completion(self, version: str, payload: Payload) -> CompletionEvidence: ...


class SnapshotStore(Protocol):
    def load(self, workflow_id: str) -> ProtocolSnapshot | None: ...
    def compare_and_swap(
        self, workflow_id: str, expected_revision: int, snapshot: ProtocolSnapshot
    ) -> bool: ...


class ReceiptStore(Protocol):
    def put(self, receipt_digest: str, receipt_json: bytes) -> None: ...
    def get(self, receipt_digest: str) -> bytes | None: ...


__all__ = ("PolicyEvaluator", "ReceiptStore", "SnapshotStore")
