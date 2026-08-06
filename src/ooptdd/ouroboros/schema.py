"""Receipt v2 construction, semantic replay, and conservative v1 wrapping."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .identity import (
    CANONICALIZATION,
    MAX_INTEROPERABLE_INTEGER,
    receipt_content_digest,
)
from .model import (
    RECEIPT_VERSION,
    CycleSnapshot,
    EventRecord,
    Phase,
    ProtocolBudget,
)

LEGACY_RECEIPT_VERSION = "symposium-ooptdd-receipt/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_COMPLETE_FIELDS = {
    "schema_version",
    "status",
    "receipt_id",
    "cycle",
    "material_lock",
    "oracle_boundary",
    "runs",
    "mutation",
    "findings",
    "lineage",
    "budget",
    "trace",
    "missing_obligations",
    "integrity",
}
_INCOMPLETE_FIELDS = {
    "schema_version",
    "status",
    "receipt_id",
    "cycle",
    "legacy",
    "missing_obligations",
    "integrity",
}
_DIGEST_FIELDS = {"algorithm", "scope", "canonicalization", "schema_version", "value"}
_CYCLE_FIELDS = {"cycle_id", "generation", "previous_receipt_sha256"}
_ORACLE_FIELDS = {"emit_identity", "read_identity", "separate_source", "corroborated"}
_RUN_FIELDS = {
    "role",
    "run_id",
    "artifact_namespace",
    "outcome",
    "observation",
    "monitor",
    "evidence_tier",
    "artifact",
    "material_lock_sha256",
    "executed_source",
}
_MUTATION_FIELDS = {
    "quarantine_namespace",
    "quarantine",
    "delta",
    "source_before",
    "source_after",
    "restored",
    "restored_source",
}
_FINDINGS_FIELDS = {"enumerated", "dispositions", "complete"}
_DISPOSITION_FIELDS = {
    "finding_id",
    "disposition",
    "bound_material_changed",
    "change_evidence",
}
_LINEAGE_FIELDS = {"predecessor_receipt_sha256", "successor_cycle_id"}
_BUDGET_FIELDS = {"max_steps", "max_generations", "steps_used"}
_TRACE_FIELDS = {"revision", "accepted_events"}
_EVENT_RECORD_FIELDS = {
    "cycle_id",
    "cycle_identity_sha256",
    "event_id",
    "kind",
    "payload_json",
    "intent_hash",
    "from_phase",
    "to_phase",
}
_INTEGRITY_FIELDS = {
    "algorithm",
    "scope",
    "canonicalization",
    "schema_version",
    "value",
}
_LEGACY_FIELDS = {
    "schema_version",
    "canonicalization",
    "source_object_sha256",
    "source",
}


def _closed_object(
    properties: dict[str, Any], *, required: set[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(required if required is not None else properties),
        "properties": properties,
    }


_NONEMPTY = {
    "type": "string",
    "minLength": 1,
    "pattern": "^(?=.*\\S)[^\\u0000]+$",
}
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_NULLABLE_SHA = {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"}
_DIGEST_SCHEMA = _closed_object(
    {
        "algorithm": {"const": "sha256"},
        "scope": _NONEMPTY,
        "canonicalization": _NONEMPTY,
        "schema_version": _NONEMPTY,
        "value": _SHA,
    }
)

OUROBOROS_RECEIPT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/gj3447/ooptdd/schema/ouroboros-receipt-v2.schema.json",
    "title": "OOPTDD Ouroboros generation receipt",
    "$comment": (
        "Structural schema; validate_receipt performs cross-field and reducer-replay validation."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "status",
        "receipt_id",
        "cycle",
        "missing_obligations",
        "integrity",
    ],
    "properties": {
        "schema_version": {"const": RECEIPT_VERSION},
        "status": {"enum": ["complete", "superseded", "incomplete"]},
        "receipt_id": _NONEMPTY,
        "cycle": {"$ref": "#/$defs/cycle"},
        "material_lock": {"$ref": "#/$defs/material_lock"},
        "oracle_boundary": {"$ref": "#/$defs/oracle_boundary"},
        "runs": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {"$ref": "#/$defs/run"},
        },
        "mutation": {"$ref": "#/$defs/mutation"},
        "findings": {"$ref": "#/$defs/findings"},
        "lineage": {"$ref": "#/$defs/lineage"},
        "budget": {"$ref": "#/$defs/budget"},
        "trace": {"$ref": "#/$defs/trace"},
        "legacy": {"$ref": "#/$defs/legacy"},
        "missing_obligations": {
            "type": "array",
            "uniqueItems": True,
            "items": _NONEMPTY,
        },
        "integrity": {"$ref": "#/$defs/integrity"},
    },
    "$defs": {
        "digest": _DIGEST_SCHEMA,
        "cycle": {
            **_closed_object(
                {
                    "cycle_id": _NONEMPTY,
                    "generation": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_INTEROPERABLE_INTEGER - 1,
                    },
                    "previous_receipt_sha256": _NULLABLE_SHA,
                }
            ),
            "allOf": [
                {
                    "if": {
                        "properties": {"generation": {"const": 0}},
                        "required": ["generation"],
                    },
                    "then": {"properties": {"previous_receipt_sha256": {"type": "null"}}},
                    "else": {"properties": {"previous_receipt_sha256": _SHA}},
                }
            ],
        },
        "material_lock": _closed_object(
            {
                "spec": {"$ref": "#/$defs/digest"},
                "verifier": {"$ref": "#/$defs/digest"},
                "source": {"$ref": "#/$defs/digest"},
                "environment": {"$ref": "#/$defs/digest"},
                "source_commit": {
                    "type": "string",
                    "pattern": "^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
                },
            }
        ),
        "oracle_boundary": _closed_object(
            {
                "emit_identity": _NONEMPTY,
                "read_identity": _NONEMPTY,
                "separate_source": {"type": "boolean"},
                "corroborated": {"type": "boolean"},
            }
        ),
        "run": _closed_object(
            {
                "role": {"enum": ["initial_red", "positive", "negative", "regreen"]},
                "run_id": _NONEMPTY,
                "artifact_namespace": _NONEMPTY,
                "outcome": {"enum": ["red", "green", "inconclusive"]},
                "observation": {"enum": ["present", "absent", "inconclusive"]},
                "monitor": {
                    "enum": ["sat", "viol", "pend"],
                    "description": (
                        "Caller-selected diagnostic monitor value; not lifecycle-authoritative."
                    ),
                },
                "evidence_tier": {
                    "enum": [
                        "local_pass",
                        "emitted",
                        "arrived",
                        "queryable_causal",
                        "external_verdict",
                    ]
                },
                "artifact": {"$ref": "#/$defs/digest"},
                "material_lock_sha256": _SHA,
                "executed_source": {"$ref": "#/$defs/digest"},
            }
        ),
        "mutation": _closed_object(
            {
                "quarantine_namespace": _NONEMPTY,
                "quarantine": {"$ref": "#/$defs/digest"},
                "delta": {"$ref": "#/$defs/digest"},
                "source_before": {"$ref": "#/$defs/digest"},
                "source_after": {"$ref": "#/$defs/digest"},
                "restored": {"const": True},
                "restored_source": {"$ref": "#/$defs/digest"},
            }
        ),
        "disposition": _closed_object(
            {
                "finding_id": _NONEMPTY,
                "disposition": {"enum": ["fixed", "deferred", "refuted", "accepted_risk"]},
                "bound_material_changed": {"type": "boolean"},
                "change_evidence": {"oneOf": [{"$ref": "#/$defs/digest"}, {"type": "null"}]},
            }
        ),
        "findings": _closed_object(
            {
                "enumerated": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": _NONEMPTY,
                },
                "dispositions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/disposition"},
                },
                "complete": {"const": True},
            }
        ),
        "lineage": _closed_object(
            {
                "predecessor_receipt_sha256": _NULLABLE_SHA,
                "successor_cycle_id": {"oneOf": [_NONEMPTY, {"type": "null"}]},
            }
        ),
        "budget": _closed_object(
            {
                "max_steps": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_INTEROPERABLE_INTEGER - 2,
                },
                "max_generations": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_INTEROPERABLE_INTEGER,
                },
                "steps_used": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_INTEROPERABLE_INTEGER - 2,
                },
            }
        ),
        "event_record": _closed_object(
            {
                "cycle_id": _NONEMPTY,
                "cycle_identity_sha256": _SHA,
                "event_id": _NONEMPTY,
                "kind": {
                    "enum": [
                        "size",
                        "lock",
                        "initial_red",
                        "green",
                        "quarantine",
                        "mutation_applied",
                        "negative_red",
                        "restore",
                        "regreen",
                        "enumerate_findings",
                        "dispose_finding",
                        "seal",
                        "interrupt",
                    ]
                },
                "payload_json": {"type": "string", "minLength": 2},
                "intent_hash": _SHA,
                "from_phase": {"type": "string", "minLength": 1},
                "to_phase": {"type": "string", "minLength": 1},
            }
        ),
        "trace": _closed_object(
            {
                "revision": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_INTEROPERABLE_INTEGER,
                },
                "accepted_events": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"$ref": "#/$defs/event_record"},
                },
            }
        ),
        "legacy": _closed_object(
            {
                "schema_version": {"const": LEGACY_RECEIPT_VERSION},
                "canonicalization": {"const": "legacy-parsed-json-object/v1"},
                "source_object_sha256": _SHA,
                "source": {"type": "object"},
            }
        ),
        "integrity": _closed_object(
            {
                "algorithm": {"const": "sha256"},
                "scope": {"const": "ouroboros-receipt-content"},
                "canonicalization": {"const": CANONICALIZATION},
                "schema_version": {"const": RECEIPT_VERSION},
                "value": _NULLABLE_SHA,
            }
        ),
    },
    "allOf": [
        {
            "if": {
                "properties": {"status": {"enum": ["complete", "superseded"]}},
                "required": ["status"],
            },
            "then": {
                "required": sorted(_COMPLETE_FIELDS),
                "not": {"required": ["legacy"]},
                "properties": {
                    "missing_obligations": {"maxItems": 0},
                    "integrity": {
                        "properties": {"value": _SHA},
                        "required": ["value"],
                    },
                },
            },
        },
        {
            "if": {
                "properties": {"status": {"const": "incomplete"}},
                "required": ["status"],
            },
            "then": {
                "required": sorted(_INCOMPLETE_FIELDS),
                "not": {
                    "anyOf": [
                        {"required": [field]}
                        for field in sorted(_COMPLETE_FIELDS - _INCOMPLETE_FIELDS)
                    ]
                },
                "properties": {
                    "missing_obligations": {"minItems": 1},
                    "integrity": {
                        "properties": {"value": {"type": "null"}},
                        "required": ["value"],
                    },
                },
            },
        },
    ],
}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _exact_object(
    value: Any,
    expected: set[str],
    label: str,
    errors: list[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        errors.append(f"{label} fields mismatch: missing={sorted(missing)}, extra={sorted(extra)}")
    return value


def _freeze_json(value: Any, path: str = "$", seen: set[int] | None = None) -> Any:
    """Take one plain-value snapshot of a possibly mutable JSON container."""

    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path}: non-finite numbers are not JSON")
        return value
    if isinstance(value, (dict, list)):
        active = seen if seen is not None else set()
        marker = id(value)
        if marker in active:
            raise ValueError(f"{path}: circular JSON container")
        active.add(marker)
        try:
            if isinstance(value, list):
                children = list(value)
                return [
                    _freeze_json(child, f"{path}[{index}]", active)
                    for index, child in enumerate(children)
                ]
            items = list(value.items())
            frozen: dict[str, Any] = {}
            for key, child in items:
                if type(key) is not str:
                    raise ValueError(f"{path}: object keys must be strings")
                if key in frozen:
                    raise ValueError(f"{path}: duplicate object key {key!r}")
                frozen[key] = _freeze_json(child, f"{path}.{key}", active)
            return frozen
        finally:
            active.remove(marker)
    raise ValueError(f"{path}: unsupported JSON value {type(value).__name__}")


def _plain_json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    try:
        frozen = _freeze_json(value)
    except (RuntimeError, ValueError) as error:
        raise ValueError(f"{label} cannot be snapshotted: {error}") from error
    if not isinstance(frozen, dict):  # defensive: root type was checked above
        raise ValueError(f"{label} must be a JSON object")
    return frozen


def _legacy_object_sha256(document: dict[str, Any]) -> str:
    """Fingerprint a parsed legacy JSON object, not unavailable original source bytes."""

    raw = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _snapshot_sections(snapshot: CycleSnapshot) -> dict[str, Any]:
    if snapshot.material_lock is None or snapshot.oracle is None:
        raise ValueError("a sealed generation requires material and oracle locks")
    if (
        snapshot.quarantine is None
        or not _text(snapshot.quarantine_namespace)
        or snapshot.mutation_delta is None
        or snapshot.mutated_source is None
        or snapshot.restored_source is None
    ):
        raise ValueError(
            "a sealed generation requires quarantine, mutation, and restoration identities"
        )
    return {
        "cycle": snapshot.identity.to_dict(),
        "material_lock": snapshot.material_lock.to_dict(),
        "oracle_boundary": snapshot.oracle.to_dict(),
        "runs": [run.to_dict() for run in snapshot.runs],
        "mutation": {
            "quarantine_namespace": snapshot.quarantine_namespace,
            "quarantine": snapshot.quarantine.to_dict(),
            "delta": snapshot.mutation_delta.to_dict(),
            "source_before": snapshot.material_lock.source.to_dict(),
            "source_after": snapshot.mutated_source.to_dict(),
            "restored": not snapshot.mutation_active,
            "restored_source": snapshot.restored_source.to_dict(),
        },
        "findings": {
            "enumerated": list(snapshot.finding_ids),
            "dispositions": [item.to_dict() for item in snapshot.dispositions],
            "complete": {item.finding_id for item in snapshot.dispositions}
            == set(snapshot.finding_ids),
        },
        "lineage": {
            "predecessor_receipt_sha256": snapshot.identity.previous_receipt_sha256,
            "successor_cycle_id": snapshot.successor_cycle_id,
        },
        "budget": {
            **snapshot.budget.to_dict(),
            "steps_used": snapshot.steps_used,
        },
        "trace": {
            "revision": snapshot.revision,
            "accepted_events": [event.to_dict() for event in snapshot.events],
        },
    }


def receipt_from_snapshot(snapshot: CycleSnapshot, *, receipt_id: str) -> dict[str, Any]:
    """Encode a completed generation and verify it by replay before returning it."""

    if not _text(receipt_id):
        raise ValueError("receipt_id must be a non-empty string without NUL")
    if snapshot.phase not in {Phase.COMPLETE, Phase.SUPERSEDED_BY_SUCCESSOR}:
        raise ValueError("only a complete or superseded generation can be receipted")
    status = "complete" if snapshot.phase is Phase.COMPLETE else "superseded"
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_VERSION,
        "status": status,
        "receipt_id": receipt_id,
        **_snapshot_sections(snapshot),
        "missing_obligations": [],
        "integrity": {
            "algorithm": "sha256",
            "scope": "ouroboros-receipt-content",
            "canonicalization": CANONICALIZATION,
            "schema_version": RECEIPT_VERSION,
            "value": None,
        },
    }
    receipt["integrity"]["value"] = receipt_content_digest(
        receipt, schema_version=RECEIPT_VERSION
    ).value
    errors = validate_receipt(receipt)
    if errors:
        raise ValueError("invalid sealed receipt: " + "; ".join(errors))
    return receipt


def _validate_cycle(value: Any, errors: list[str]) -> dict[str, Any]:
    cycle = _exact_object(value, _CYCLE_FIELDS, "cycle", errors)
    if not _text(cycle.get("cycle_id")):
        errors.append("cycle.cycle_id must be a non-empty string without NUL")
    generation = cycle.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        errors.append("cycle.generation must be a non-negative integer")
        return cycle
    if generation >= MAX_INTEROPERABLE_INTEGER:
        errors.append("cycle.generation leaves no interoperable successor-budget value")
    previous = cycle.get("previous_receipt_sha256")
    if generation == 0 and previous is not None:
        errors.append("generation zero cannot name a predecessor receipt")
    if generation > 0 and not _sha(previous):
        errors.append("successor generation requires previous_receipt_sha256")
    return cycle


def _validate_integrity(receipt: dict[str, Any], complete: bool, errors: list[str]) -> None:
    integrity = _exact_object(receipt.get("integrity"), _INTEGRITY_FIELDS, "integrity", errors)
    expected_metadata = {
        "algorithm": "sha256",
        "scope": "ouroboros-receipt-content",
        "canonicalization": CANONICALIZATION,
        "schema_version": RECEIPT_VERSION,
    }
    for key, expected in expected_metadata.items():
        if integrity.get(key) != expected:
            errors.append(f"integrity.{key} must be {expected!r}")
    value = integrity.get("value")
    if complete:
        if not _sha(value):
            errors.append("complete receipt integrity.value must be lowercase 64-hex")
            return
        try:
            actual = receipt_content_digest(receipt, schema_version=RECEIPT_VERSION).value
        except (TypeError, ValueError) as error:
            errors.append(f"receipt cannot be identity-hashed: {error}")
        else:
            if actual != value:
                errors.append("receipt integrity.value does not match its content")
    elif value is not None:
        errors.append("incomplete receipt integrity.value must be null")


def _validate_complete_shapes(receipt: dict[str, Any], errors: list[str]) -> None:
    _exact_object(
        receipt.get("material_lock"),
        {"spec", "verifier", "source", "environment", "source_commit"},
        "material_lock",
        errors,
    )
    _exact_object(receipt.get("oracle_boundary"), _ORACLE_FIELDS, "oracle_boundary", errors)

    runs = receipt.get("runs")
    if not isinstance(runs, list):
        errors.append("runs must be an array")
    else:
        for index, run in enumerate(runs):
            parsed = _exact_object(run, _RUN_FIELDS, f"runs[{index}]", errors)
            for key in ("artifact", "executed_source"):
                _exact_object(parsed.get(key), _DIGEST_FIELDS, f"runs[{index}].{key}", errors)

    mutation = _exact_object(receipt.get("mutation"), _MUTATION_FIELDS, "mutation", errors)
    for key in ("quarantine", "delta", "source_before", "source_after", "restored_source"):
        _exact_object(mutation.get(key), _DIGEST_FIELDS, f"mutation.{key}", errors)

    findings = _exact_object(receipt.get("findings"), _FINDINGS_FIELDS, "findings", errors)
    dispositions = findings.get("dispositions")
    if not isinstance(dispositions, list):
        errors.append("findings.dispositions must be an array")
    else:
        for index, item in enumerate(dispositions):
            parsed = _exact_object(
                item,
                _DISPOSITION_FIELDS,
                f"findings.dispositions[{index}]",
                errors,
            )
            evidence = parsed.get("change_evidence")
            if evidence is not None:
                _exact_object(
                    evidence,
                    _DIGEST_FIELDS,
                    f"findings.dispositions[{index}].change_evidence",
                    errors,
                )

    _exact_object(receipt.get("lineage"), _LINEAGE_FIELDS, "lineage", errors)
    _exact_object(receipt.get("budget"), _BUDGET_FIELDS, "budget", errors)
    trace = _exact_object(receipt.get("trace"), _TRACE_FIELDS, "trace", errors)
    records = trace.get("accepted_events")
    if not isinstance(records, list):
        errors.append("trace.accepted_events must be an array")
    else:
        for index, item in enumerate(records):
            _exact_object(
                item,
                _EVENT_RECORD_FIELDS,
                f"trace.accepted_events[{index}]",
                errors,
            )


def _replay_complete(receipt: dict[str, Any], errors: list[str]) -> None:
    """Rebuild the snapshot from full events and compare every semantic section."""

    cycle = receipt.get("cycle")
    budget_data = receipt.get("budget")
    trace = receipt.get("trace")
    if (
        not isinstance(cycle, dict)
        or not isinstance(budget_data, dict)
        or not isinstance(trace, dict)
    ):
        return
    try:
        budget = ProtocolBudget(
            max_steps=budget_data["max_steps"],
            max_generations=budget_data["max_generations"],
        )
        snapshot = CycleSnapshot.start(
            cycle["cycle_id"],
            budget,
            generation=cycle["generation"],
            previous_receipt_sha256=cycle["previous_receipt_sha256"],
        )
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"cycle/budget cannot initialize replay: {error}")
        return

    raw_records = trace.get("accepted_events")
    if not isinstance(raw_records, list):
        return
    from .reducer import step

    for index, raw_record in enumerate(raw_records):
        try:
            record = EventRecord.from_dict(raw_record)
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"trace.accepted_events[{index}] invalid: {error}")
            return
        if record.from_phase is not snapshot.phase:
            errors.append(f"trace.accepted_events[{index}].from_phase does not continue the trace")
            return
        try:
            result = step(snapshot, record.event)
        except (TypeError, ValueError) as error:
            errors.append(f"trace.accepted_events[{index}] cannot be reduced: {error}")
            return
        if not result.accepted or result.replayed:
            errors.append(
                f"trace.accepted_events[{index}] is not an accepted fresh transition: "
                f"{result.rejection_code}"
            )
            return
        if result.snapshot.events[-1] != record:
            errors.append(f"trace.accepted_events[{index}] does not match reducer output")
            return
        snapshot = result.snapshot

    expected_phase = (
        Phase.COMPLETE if receipt.get("status") == "complete" else Phase.SUPERSEDED_BY_SUCCESSOR
    )
    if snapshot.phase is not expected_phase:
        errors.append(
            f"replayed trace ends in {snapshot.phase.value!r}, expected {expected_phase.value!r}"
        )
        return
    try:
        expected_sections = _snapshot_sections(snapshot)
    except ValueError as error:
        errors.append(f"replayed snapshot cannot be receipted: {error}")
        return
    for key, expected in expected_sections.items():
        if receipt.get(key) != expected:
            errors.append(f"{key} does not match the reducer-replayed trace")


def _validate_complete(receipt: dict[str, Any], errors: list[str]) -> None:
    missing = receipt.get("missing_obligations")
    if not isinstance(missing, list) or missing:
        errors.append("sealed receipt missing_obligations must be an empty array")
    _validate_complete_shapes(receipt, errors)
    _replay_complete(receipt, errors)


def _validate_incomplete(receipt: dict[str, Any], errors: list[str]) -> None:
    missing = receipt.get("missing_obligations")
    if (
        not isinstance(missing, list)
        or not missing
        or any(not _text(item) for item in missing)
        or len(missing) != len(set(missing))
    ):
        errors.append("incomplete receipt requires unique, explicit missing_obligations")
    legacy = _exact_object(receipt.get("legacy"), _LEGACY_FIELDS, "legacy", errors)
    if legacy.get("schema_version") != LEGACY_RECEIPT_VERSION:
        errors.append(f"legacy.schema_version must be {LEGACY_RECEIPT_VERSION!r}")
    if legacy.get("canonicalization") != "legacy-parsed-json-object/v1":
        errors.append("legacy.canonicalization must identify parsed-object provenance")
    source = legacy.get("source")
    if not isinstance(source, dict):
        errors.append("legacy.source must preserve the parsed v1 object")
    else:
        try:
            actual = _legacy_object_sha256(source)
        except (TypeError, ValueError) as error:
            errors.append(f"legacy.source cannot be fingerprinted: {error}")
        else:
            if legacy.get("source_object_sha256") != actual:
                errors.append("legacy.source_object_sha256 does not match the preserved v1 object")


def validate_receipt(receipt: Any) -> list[str]:
    """Return structural, integrity, and reducer-replay errors without external I/O."""

    if not isinstance(receipt, dict):
        return ["receipt must be a JSON object"]
    try:
        receipt = _plain_json_object(receipt, "receipt")
    except ValueError as error:
        return [str(error)]
    errors: list[str] = []
    status = receipt.get("status")
    if isinstance(status, str) and status in {"complete", "superseded"}:
        _exact_object(receipt, _COMPLETE_FIELDS, "receipt", errors)
    elif status == "incomplete":
        _exact_object(receipt, _INCOMPLETE_FIELDS, "receipt", errors)
    else:
        errors.append("status must be complete, superseded, or incomplete")
    if receipt.get("schema_version") != RECEIPT_VERSION:
        errors.append(f"schema_version must be {RECEIPT_VERSION!r}")
    if not _text(receipt.get("receipt_id")):
        errors.append("receipt_id must be a non-empty string without NUL")
    _validate_cycle(receipt.get("cycle"), errors)
    complete = isinstance(status, str) and status in {"complete", "superseded"}
    _validate_integrity(receipt, complete, errors)
    if complete:
        _validate_complete(receipt, errors)
    elif status == "incomplete":
        _validate_incomplete(receipt, errors)
    return errors


def successor_from_receipt(receipt: dict[str, Any]) -> CycleSnapshot:
    """Construct the named successor only after predecessor receipt verification."""

    frozen = _plain_json_object(receipt, "predecessor receipt")
    errors = validate_receipt(frozen)
    if errors:
        raise ValueError("cannot start successor from invalid receipt: " + "; ".join(errors))
    if frozen.get("status") != "superseded":
        raise ValueError("only a superseded receipt can start a successor")
    cycle = frozen["cycle"]
    lineage = frozen["lineage"]
    budget_data = frozen["budget"]
    generation = cycle["generation"] + 1
    if generation >= budget_data["max_generations"]:
        raise ValueError("generation budget leaves no room for the named successor")
    return CycleSnapshot.start(
        lineage["successor_cycle_id"],
        ProtocolBudget(
            max_steps=budget_data["max_steps"],
            max_generations=budget_data["max_generations"],
        ),
        generation=generation,
        previous_receipt_sha256=frozen["integrity"]["value"],
    )


_V1_MISSING_OBLIGATIONS = [
    "authoritative_v2_receipt_hash",
    "bite_finding_enumeration_and_total_disposition",
    "environment_digest_locked_before_runs",
    "generation_lineage",
    "initial_red_run_identity_and_artifact",
    "mutation_delta_identity",
    "original_legacy_bytes_and_raw_digest",
    "phase_distinct_artifact_namespaces",
    "restored_regreen_run",
    "stale_artifact_quarantine_identity",
    "verifier_digest_locked_before_runs",
]


def upcast_v1_receipt(document: dict[str, Any]) -> dict[str, Any]:
    """Wrap a parsed v1 object as incomplete v2 without inferring missing proof.

    This preserves the parsed object value, not unavailable original JSON bytes.  Existing
    v2 input is copied only after it passes the same validator used by consumers.
    """

    frozen = _plain_json_object(document, "legacy receipt")
    if frozen.get("schema_version") == RECEIPT_VERSION:
        errors = validate_receipt(frozen)
        if errors:
            raise ValueError("existing v2 receipt is invalid: " + "; ".join(errors))
        return frozen
    if frozen.get("schema_version") != LEGACY_RECEIPT_VERSION:
        raise ValueError(f"only {LEGACY_RECEIPT_VERSION!r} can be upcast")
    legacy = frozen
    source_object_sha256 = _legacy_object_sha256(legacy)
    receipt_id = (
        legacy.get("receipt_id")
        if _text(legacy.get("receipt_id"))
        else f"legacy-{source_object_sha256}"
    )
    cycle_id = (
        legacy.get("cycle_id")
        if _text(legacy.get("cycle_id"))
        else f"legacy-{source_object_sha256}"
    )
    missing = list(_V1_MISSING_OBLIGATIONS)
    if legacy.get("template_only") is not False:
        missing.append("non_template_execution")
    return {
        "schema_version": RECEIPT_VERSION,
        "status": "incomplete",
        "receipt_id": receipt_id,
        "cycle": {
            "cycle_id": cycle_id,
            "generation": 0,
            "previous_receipt_sha256": None,
        },
        "legacy": {
            "schema_version": LEGACY_RECEIPT_VERSION,
            "canonicalization": "legacy-parsed-json-object/v1",
            "source_object_sha256": source_object_sha256,
            "source": legacy,
        },
        "missing_obligations": sorted(missing),
        "integrity": {
            "algorithm": "sha256",
            "scope": "ouroboros-receipt-content",
            "canonicalization": CANONICALIZATION,
            "schema_version": RECEIPT_VERSION,
            "value": None,
        },
    }


__all__ = [
    "LEGACY_RECEIPT_VERSION",
    "OUROBOROS_RECEIPT_SCHEMA",
    "receipt_from_snapshot",
    "successor_from_receipt",
    "upcast_v1_receipt",
    "validate_receipt",
]
