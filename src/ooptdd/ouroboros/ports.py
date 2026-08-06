"""Caller-owned ports around the pure Ouroboros reducer.

The protocol module defines effect intent and receipt boundaries, but it owns no threads,
database, filesystem, or retry loop.  Adapters decide how to make these ports durable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .model import CycleSnapshot, EffectIntent


@dataclass(frozen=True)
class EffectResult:
    effect_id: str
    applied: bool
    detail: str = ""


class EffectSink(Protocol):
    """Execute or deduplicate an effect using ``effect.effect_id`` as the key."""

    def apply(self, effect: EffectIntent) -> EffectResult: ...


class SnapshotStore(Protocol):
    """Optional state authority supplied by a future runner, not by this module."""

    def load(self, cycle_id: str) -> CycleSnapshot | None: ...

    def compare_and_swap(
        self,
        cycle_id: str,
        expected_revision: int,
        snapshot: CycleSnapshot,
    ) -> bool: ...


class ReceiptStore(Protocol):
    """Persist a completed receipt by its authoritative content hash."""

    def put(self, receipt_sha256: str, receipt_json: bytes) -> None: ...

    def get(self, receipt_sha256: str) -> bytes | None: ...


__all__ = ["EffectResult", "EffectSink", "ReceiptStore", "SnapshotStore"]
