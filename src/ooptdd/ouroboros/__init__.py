"""Generic, deterministic, bounded workflow kernel.

Domain workflows are explicit opt-in profiles. Importing this package does not load
any profile or adapter.
"""

from .identity import (
    CANONICALIZATION,
    MAX_INTEROPERABLE_INTEGER,
    Digest,
    canonical_json_bytes,
    digest_json,
    digest_raw,
    raw_sha256,
    receipt_content_digest,
)
from .model import (
    CompletionEvidence,
    CompletionPolicy,
    PayloadValidator,
    ProtocolBudget,
    ProtocolDefinition,
    ProtocolEvent,
    ProtocolSnapshot,
    RecoveryPolicy,
    RevisionIdentity,
    TransitionRecord,
    TransitionResult,
    TransitionRule,
)
from .ports import PolicyEvaluator, ReceiptStore, SnapshotStore
from .receipt import ProtocolReceipt, parse_receipt, receipt_from_snapshot, validate_receipt
from .reducer import advance, start_successor, step

__all__ = (
    "CANONICALIZATION",
    "MAX_INTEROPERABLE_INTEGER",
    "CompletionPolicy",
    "CompletionEvidence",
    "Digest",
    "PayloadValidator",
    "PolicyEvaluator",
    "ProtocolBudget",
    "ProtocolDefinition",
    "ProtocolEvent",
    "ProtocolReceipt",
    "ProtocolSnapshot",
    "RecoveryPolicy",
    "ReceiptStore",
    "RevisionIdentity",
    "SnapshotStore",
    "TransitionRecord",
    "TransitionResult",
    "TransitionRule",
    "advance",
    "canonical_json_bytes",
    "digest_json",
    "digest_raw",
    "raw_sha256",
    "receipt_content_digest",
    "parse_receipt",
    "receipt_from_snapshot",
    "start_successor",
    "step",
    "validate_receipt",
)
