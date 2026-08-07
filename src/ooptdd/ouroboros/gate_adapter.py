"""Typed bridge from :func:`ooptdd.engine.verify.verify_gate` to Ouroboros v2.

Receipt v2 deliberately keeps one compact, compatibility-stable ``RunEvidence`` record.
That record cannot faithfully inline every gate check: an aggregate GREEN can contain an
optional ``viol``, a pending miss, or a custom check with no kernel monitor verdict at all.
This module therefore writes a typed semantic projection to a separately versioned canonical
artifact, commits to the exact verifier value, and puts the artifact digest in the existing
receipt field.

The adapter authenticates no authority.  It freezes, validates, and binds the verifier's
claim; a detached attestation and an externally owned anchor are still required to prove
who produced the artifact or that a claimed independent source is actually trustworthy.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .identity import CANONICALIZATION, Digest, canonical_json_bytes, digest_json
from .model import (
    COMPLETION_EVIDENCE_TIERS,
    CycleIdentity,
    EvidenceTier,
    MaterialLock,
    MonitorVerdict,
    ObservationVerdict,
    OracleBoundary,
    RunEvidence,
    RunOutcome,
    RunRole,
)

GATE_EVIDENCE_VERSION = "ooptdd-ouroboros-gate-evidence/v1"
_ARTIFACT_SCOPE = "ouroboros-gate-evidence"
_VERIFICATION_SCOPE = "ouroboros-gate-verification-value"
_CHECK_SCOPE = "ouroboros-gate-check"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$")
_NONNEGATIVE_DECIMAL_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$"
)
_POSITIVE_DECIMAL_RE = re.compile(
    r"^(?:(?:0\.[0-9]*[1-9][0-9]*)|(?:[1-9][0-9]*(?:\.[0-9]+)?))"
    r"(?:e[+-]?[0-9]+)?$"
)

_EXPECTED_OUTCOME = {
    RunRole.INITIAL_RED: RunOutcome.RED,
    RunRole.POSITIVE: RunOutcome.GREEN,
    RunRole.NEGATIVE: RunOutcome.RED,
    RunRole.REGREEN: RunOutcome.GREEN,
}


def _closed_schema(
    properties: dict[str, Any], *, required: set[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(required if required is not None else properties),
        "properties": properties,
    }


_TEXT = {"type": "string", "minLength": 1, "pattern": "^(?=.*\\S)[^\\u0000]+$"}
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_NONNEGATIVE_DECIMAL = {
    "type": "string",
    "pattern": _NONNEGATIVE_DECIMAL_RE.pattern,
}
_POSITIVE_DECIMAL = {
    "type": "string",
    "pattern": _POSITIVE_DECIMAL_RE.pattern,
}
_DIGEST_SCHEMA = _closed_schema(
    {
        "algorithm": {"const": "sha256"},
        "scope": _TEXT,
        "canonicalization": _TEXT,
        "schema_version": _TEXT,
        "value": _SHA,
    }
)
_VERIFICATION_DIGEST_SCHEMA = _closed_schema(
    {
        "algorithm": {"const": "sha256"},
        "scope": {"const": _VERIFICATION_SCOPE},
        "canonicalization": {"const": CANONICALIZATION},
        "schema_version": {"const": GATE_EVIDENCE_VERSION},
        "value": _SHA,
    }
)

OUROBOROS_GATE_EVIDENCE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": (
        "https://github.com/gj3447/ooptdd/schema/"
        "ouroboros-gate-evidence-v1.schema.json"
    ),
    "title": "OOPTDD Ouroboros typed gate evidence",
    "$comment": (
        "Structural schema; validate_gate_evidence performs binding and aggregate checks."
    ),
    **_closed_schema(
        {
            "schema_version": {"const": GATE_EVIDENCE_VERSION},
            "binding": {"$ref": "#/$defs/binding"},
            "readback": {"$ref": "#/$defs/readback"},
            "aggregation": {"$ref": "#/$defs/aggregation"},
            "checks": {
                "type": "array",
                "items": {"$ref": "#/$defs/check"},
            },
            "oracle": {"$ref": "#/$defs/oracle"},
        }
    ),
    "$defs": {
        "digest": _DIGEST_SCHEMA,
        "verification_digest": _VERIFICATION_DIGEST_SCHEMA,
        "cycle": _closed_schema(
            {
                "cycle_id": _TEXT,
                "generation": {"type": "integer", "minimum": 0},
                "previous_receipt_sha256": {
                    "oneOf": [_SHA, {"type": "null"}],
                },
            }
        ),
        "oracle_boundary": _closed_schema(
            {
                "emit_identity": _TEXT,
                "read_identity": _TEXT,
                "separate_source": {"type": "boolean"},
                "corroborated": {"type": "boolean"},
            }
        ),
        "material_lock": _closed_schema(
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
        "binding": _closed_schema(
            {
                "cycle": {"$ref": "#/$defs/cycle"},
                "cycle_identity_sha256": _SHA,
                "role": {
                    "enum": ["initial_red", "positive", "negative", "regreen"]
                },
                "run_id": _TEXT,
                "artifact_namespace": _TEXT,
                "subject_outcome": {"enum": ["red", "green"]},
                "lifecycle_outcome": {"enum": ["red", "green", "inconclusive"]},
                "material_lock": {"$ref": "#/$defs/material_lock"},
                "material_lock_sha256": _SHA,
                "executed_source": {"$ref": "#/$defs/digest"},
                "oracle_boundary": {"$ref": "#/$defs/oracle_boundary"},
                "verification_value": {"$ref": "#/$defs/verification_digest"},
            }
        ),
        "arrival": _closed_schema(
            {
                "visibility_delay_ms": {"type": "integer", "minimum": 0},
                "waited_ms": {"type": "integer", "minimum": 0},
                "flushed": {"type": "boolean"},
                "extended_for_visibility": {"type": "boolean"},
                "confirm_rounds_run": {"type": "integer", "minimum": 0},
            }
        ),
        "readback": _closed_schema(
            {
                "cid": _TEXT,
                "verdict": {"enum": ["present", "absent", "inconclusive"]},
                "settlement": {"const": "bounded_final"},
                "reachable": {"type": "boolean"},
                "complete": {"type": "boolean"},
                "probe_reachable": {"type": "boolean"},
                "attempts": {"type": "integer", "minimum": 1},
                "arrival": {"$ref": "#/$defs/arrival"},
            }
        ),
        "aggregation": _closed_schema(
            {
                "mode": {"enum": ["all", "weighted_threshold"]},
                "ok": {"type": "boolean"},
                "gating_check_ids": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": _SHA,
                },
                "score": {"oneOf": [_NONNEGATIVE_DECIMAL, {"type": "null"}]},
                "threshold": {"oneOf": [_POSITIVE_DECIMAL, {"type": "null"}]},
            }
        ),
        "check": _closed_schema(
            {
                "check_id": _SHA,
                "ordinal": {"type": "integer", "minimum": 0},
                "label": _TEXT,
                "kind": _TEXT,
                "passed": {"type": "boolean"},
                "monitor": {
                    "oneOf": [
                        {"enum": ["sat", "viol", "pend"]},
                        {"type": "null"},
                    ]
                },
                "settled_at": {
                    "oneOf": [
                        {"type": "integer", "minimum": 0},
                        {"type": "null"},
                    ]
                },
                "optional": {"type": "boolean"},
                "pending": {"type": "boolean"},
                "tautological": {"type": "boolean"},
                "gating": {"type": "boolean"},
                "weight": _NONNEGATIVE_DECIMAL,
                "strength": _TEXT,
                "grounding": {"enum": ["derived-self", "corroborated"]},
                "charged": {"type": "boolean"},
            }
        ),
        "oracle": _closed_schema(
            {
                "evidence_tier": {
                    "enum": [
                        "local_pass",
                        "emitted",
                        "arrived",
                        "queryable_causal",
                        "external_verdict",
                    ]
                },
                "emit_backend": _TEXT,
                "emit_identity": _TEXT,
                "emit_independent": {
                    "oneOf": [{"type": "boolean"}, {"type": "null"}]
                },
                "read_identities": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": _TEXT,
                },
                "corroborated": {"type": "integer", "minimum": 0},
                "derived_self": {"type": "integer", "minimum": 0},
                "single_authority": {"type": "boolean"},
                "corroboration_enforced": {"type": "boolean"},
                "signature_enforced": {"type": "boolean"},
                "independent_store_enforced": {"type": "boolean"},
                "authenticated": {
                    "oneOf": [{"type": "boolean"}, {"type": "null"}]
                },
                "sampled": {"type": "boolean"},
            }
        ),
    },
}


@dataclass(frozen=True)
class GateEvidenceBundle:
    """Canonical artifact bytes plus the v2-compatible run record that binds them."""

    artifact_bytes: bytes
    artifact: Digest
    run: RunEvidence

    @property
    def document(self) -> dict[str, Any]:
        """Return a fresh decoded artifact so callers cannot mutate the frozen bundle."""

        value = json.loads(self.artifact_bytes)
        if not isinstance(value, dict):  # construction guarantees this; keep typing honest
            raise ValueError("gate evidence artifact root is not an object")
        return value

    @property
    def event_payload(self) -> dict[str, Any]:
        """Return the exact payload accepted by an Ouroboros run event.

        The event kind already determines the run role, so reducer payloads omit the redundant
        ``role`` field even though a sealed receipt includes it.
        """

        payload = self.run.to_dict()
        payload.pop("role")
        return payload


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{label} must be a non-empty string without NUL")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _number_text(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if value == 0:
        return "0"
    return repr(value).lower()


def _nonnegative_number_text(value: Any, label: str) -> str:
    text = _number_text(value, label)
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return text


def _unit_number_text(value: Any, label: str) -> str:
    text = _nonnegative_number_text(value, label)
    if value > 1:
        raise ValueError(f"{label} must be <= 1")
    return text


def _positive_unit_number_text(value: Any, label: str) -> str:
    text = _unit_number_text(value, label)
    if value <= 0:
        raise ValueError(f"{label} must be > 0")
    return text


def _projected_weighted_score(checks: list[dict[str, Any]]) -> float:
    """Mirror the gate's float aggregation while rejecting unusable numeric states."""

    try:
        weights = [float(check["weight"]) for check in checks]
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("projected gate weights must be finite numbers") from error
    if any(not math.isfinite(weight) or weight < 0 for weight in weights):
        raise ValueError("projected gate weights must be finite non-negative numbers")
    total_weight = sum(weights)
    passed_weight = sum(
        weight
        for check, weight in zip(checks, weights, strict=True)
        if check.get("passed") is True
    )
    if not math.isfinite(total_weight) or not math.isfinite(passed_weight):
        raise ValueError("projected gate weight totals must be finite")
    if total_weight <= 0:
        raise ValueError("projected weighted gate requires a positive total weight")
    score = passed_weight / total_weight
    if not math.isfinite(score):
        raise ValueError("projected gate score must be finite")
    return score


def _freeze_json(value: Any, path: str = "$", active: set[int] | None = None) -> Any:
    """Snapshot the exact JSON value accepted from ``verify_gate`` without coercion."""

    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path}: non-finite number")
        return value
    if isinstance(value, (dict, list)):
        seen = active if active is not None else set()
        marker = id(value)
        if marker in seen:
            raise ValueError(f"{path}: circular JSON container")
        seen.add(marker)
        try:
            if isinstance(value, list):
                return [
                    _freeze_json(child, f"{path}[{index}]", seen)
                    for index, child in enumerate(list(value))
                ]
            frozen: dict[str, Any] = {}
            for key, child in list(value.items()):
                if type(key) is not str:
                    raise ValueError(f"{path}: object keys must be strings")
                frozen[key] = _freeze_json(child, f"{path}.{key}", seen)
            return frozen
        finally:
            seen.remove(marker)
    raise ValueError(f"{path}: unsupported JSON value {type(value).__name__}")


def _typed_identity(value: Any) -> list[Any]:
    """Losslessly tag JSON scalar types before strict protocol hashing.

    Gate results legitimately contain floats, while authoritative Ouroboros JSON forbids
    them.  Decimal text alone would collide with user strings, so every value receives an
    explicit type tag before the digest is computed.
    """

    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["boolean", value]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float:
        # This is an exact binary64 representation and retains the sign bit on zero.
        # Arithmetic projections intentionally normalize -0.0 elsewhere; the verifier
        # commitment must not, because it identifies the exact input value.
        return ["number", value.hex()]
    if type(value) is str:
        return ["string", value]
    if isinstance(value, list):
        return ["array", [_typed_identity(child) for child in value]]
    if isinstance(value, dict):
        return [
            "object",
            [[key, _typed_identity(value[key])] for key in sorted(value)],
        ]
    raise ValueError(f"unsupported verification identity value {type(value).__name__}")


def _digest_verification(value: dict[str, Any]) -> Digest:
    return digest_json(
        _typed_identity(value),
        scope=_VERIFICATION_SCOPE,
        schema_version=GATE_EVIDENCE_VERSION,
    )


def _digest_check(value: dict[str, Any]) -> Digest:
    return digest_json(
        value,
        scope=_CHECK_SCOPE,
        schema_version=GATE_EVIDENCE_VERSION,
    )


def _projected_evidence_tier(
    checks: list[dict[str, Any]],
    *,
    reachable: bool,
    sampled: bool,
) -> EvidenceTier:
    """Recompute the evidence ladder using only fields sealed in the artifact."""

    gating = [check for check in checks if check["gating"]]
    if not gating or not reachable:
        return EvidenceTier.LOCAL_PASS
    if any(
        check["passed"] and check["grounding"] == "corroborated"
        for check in gating
    ):
        return EvidenceTier.EXTERNAL_VERDICT
    if any(
        check["passed"]
        and isinstance(check["strength"], str)
        and check["strength"] in {"invariant", "metamorphic"}
        for check in gating
    ):
        return EvidenceTier.ARRIVED if sampled else EvidenceTier.QUERYABLE_CAUSAL
    if any(check["charged"] for check in gating):
        return EvidenceTier.ARRIVED
    return EvidenceTier.EMITTED


def _project_arrival(value: Any) -> dict[str, Any]:
    arrival = _mapping(value, "verification.arrival")
    required = {
        "visibility_delay_ms",
        "waited_ms",
        "flushed",
        "extended_for_visibility",
        "confirm_rounds_run",
    }
    if set(arrival) != required:
        raise ValueError(
            "verification.arrival fields must be exactly " + repr(sorted(required))
        )
    return {
        "visibility_delay_ms": _integer(
            arrival["visibility_delay_ms"], "arrival.visibility_delay_ms"
        ),
        "waited_ms": _integer(arrival["waited_ms"], "arrival.waited_ms"),
        "flushed": _boolean(arrival["flushed"], "arrival.flushed"),
        "extended_for_visibility": _boolean(
            arrival["extended_for_visibility"], "arrival.extended_for_visibility"
        ),
        "confirm_rounds_run": _integer(
            arrival["confirm_rounds_run"], "arrival.confirm_rounds_run"
        ),
    }


def _project_checks(checks: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(checks, list):
        raise ValueError("verification.gate.checks must be an array")
    projected: list[dict[str, Any]] = []
    gating_ids: list[str] = []
    for ordinal, raw in enumerate(checks):
        check = _mapping(raw, f"verification.gate.checks[{ordinal}]")
        optional = _boolean(check.get("optional"), f"checks[{ordinal}].optional")
        pending = _boolean(check.get("pending"), f"checks[{ordinal}].pending")
        tautological = bool(check.get("tautological", False))
        if "tautological" in check:
            _boolean(check["tautological"], f"checks[{ordinal}].tautological")
        gating = not optional and not pending and not tautological
        monitor = check.get("verdict")
        if monitor is not None and (
            not isinstance(monitor, str)
            or monitor not in {item.value for item in MonitorVerdict}
        ):
            raise ValueError(f"checks[{ordinal}].verdict is not sat, viol, pend, or null")
        settled_at = check.get("settled_at")
        if settled_at is not None:
            settled_at = _integer(settled_at, f"checks[{ordinal}].settled_at")
        item = {
            "ordinal": ordinal,
            "label": _text(check.get("label"), f"checks[{ordinal}].label"),
            "kind": _text(check.get("kind"), f"checks[{ordinal}].kind"),
            "passed": _boolean(check.get("passed"), f"checks[{ordinal}].passed"),
            "monitor": monitor,
            "settled_at": settled_at,
            "optional": optional,
            "pending": pending,
            "tautological": tautological,
            "gating": gating,
            "weight": _nonnegative_number_text(
                check.get("weight"), f"checks[{ordinal}].weight"
            ),
            "strength": _text(check.get("strength"), f"checks[{ordinal}].strength"),
            "grounding": check.get("grounding"),
            "charged": _boolean(check.get("charged"), f"checks[{ordinal}].charged"),
        }
        if not isinstance(item["grounding"], str) or item["grounding"] not in {
            "derived-self",
            "corroborated",
        }:
            raise ValueError(
                f"checks[{ordinal}].grounding must be derived-self or corroborated"
            )
        check_id = _digest_check(item).value
        item = {"check_id": check_id, **item}
        projected.append(item)
        if gating:
            gating_ids.append(check_id)
    return projected, gating_ids


def adapt_gate_verification(
    *,
    verification: Mapping[str, Any],
    cycle: CycleIdentity,
    role: RunRole,
    run_id: str,
    artifact_namespace: str,
    subject_outcome: RunOutcome,
    material_lock: MaterialLock,
    executed_source: Digest,
    oracle: OracleBoundary,
) -> GateEvidenceBundle:
    """Freeze one bounded-final gate verification into typed Ouroboros run evidence.

    ``evidence_tier`` and ``observation`` are deliberately not accepted as caller inputs:
    they are recomputed from the gate result and readback verdict.  The subject outcome is
    explicit because a present artifact may itself report RED (initial/negative phases).
    Infrastructure uncertainty always becomes lifecycle ``inconclusive``.
    """

    if not isinstance(verification, Mapping):
        raise TypeError("verification must be a mapping returned by verify_gate")
    if not isinstance(cycle, CycleIdentity):
        raise TypeError("cycle must be a CycleIdentity")
    if not isinstance(role, RunRole):
        raise TypeError("role must be a RunRole")
    if not isinstance(subject_outcome, RunOutcome):
        raise TypeError("subject_outcome must be a RunOutcome")
    if subject_outcome is RunOutcome.INCONCLUSIVE:
        raise ValueError("subject_outcome must be the observed red or green subject result")
    if subject_outcome is not _EXPECTED_OUTCOME[role]:
        raise ValueError(
            f"{role.value} requires subject_outcome {_EXPECTED_OUTCOME[role].value!r}"
        )
    if not isinstance(material_lock, MaterialLock):
        raise TypeError("material_lock must be a MaterialLock")
    if not isinstance(executed_source, Digest):
        raise TypeError("executed_source must be a Digest")
    if role is not RunRole.NEGATIVE and executed_source != material_lock.source:
        raise ValueError(f"{role.value} must bind the locked baseline source")
    if role is RunRole.NEGATIVE and executed_source == material_lock.source:
        raise ValueError("negative must bind a mutated source, not the locked baseline")
    if not isinstance(oracle, OracleBoundary):
        raise TypeError("oracle must be an OracleBoundary")
    run_id = _text(run_id, "run_id")
    artifact_namespace = _text(artifact_namespace, "artifact_namespace")

    frozen = _freeze_json(dict(verification))
    verification_obj = _mapping(frozen, "verification")
    gate = _mapping(verification_obj.get("gate"), "verification.gate")
    top_ok = _boolean(verification_obj.get("ok"), "verification.ok")
    gate_ok = _boolean(gate.get("ok"), "verification.gate.ok")
    if top_ok != gate_ok:
        raise ValueError("verification.ok must equal verification.gate.ok")
    if verification_obj.get("settlement") != "bounded_final":
        raise ValueError(
            "typed receipt evidence requires settlement='bounded_final'; call "
            "verify_gate(..., settle_early=False)"
        )
    try:
        observation = ObservationVerdict(verification_obj.get("verdict"))
    except ValueError as error:
        raise ValueError("verification.verdict must be present, absent, or inconclusive") from error

    reachable = _boolean(gate.get("reachable"), "verification.gate.reachable")
    complete = _boolean(gate.get("complete", True), "verification.gate.complete")
    probe_reachable = _boolean(
        gate.get("probe_reachable", True), "verification.gate.probe_reachable"
    )
    infra_uncertain = not reachable or not complete or not probe_reachable
    checks, gating_ids = _project_checks(gate.get("checks"))
    gating_checks = [check for check in checks if check["gating"]]
    scope = _mapping(gate.get("scope"), "verification.gate.scope")
    expected_scope = {
        "total": len(checks),
        "gating": len(gating_ids),
        "optional": sum(1 for check in checks if check["optional"]),
        "pending": sum(1 for check in checks if check["pending"]),
    }
    for key, expected in expected_scope.items():
        if scope.get(key) != expected:
            raise ValueError(f"verification.gate.scope.{key} does not match projected checks")
    if scope.get("asserts_anything") != bool(gating_ids):
        raise ValueError("verification.gate.scope.asserts_anything does not match checks")
    if gate.get("vacuous") != (bool(checks) and not gating_ids):
        raise ValueError("verification.gate.vacuous does not match checks")

    threshold = gate.get("threshold")
    score = gate.get("score")
    if threshold is None:
        if score is not None:
            raise ValueError("all-mode aggregation cannot carry a score without a threshold")
        mode = "all"
        threshold_text = score_text = None
        required_ok = all(check["passed"] for check in gating_checks)
    else:
        if score is None:
            raise ValueError("weighted-threshold aggregation requires score and threshold")
        mode = "weighted_threshold"
        threshold_text = _positive_unit_number_text(
            threshold, "verification.gate.threshold"
        )
        score_text = _unit_number_text(score, "verification.gate.score")
        expected_score = _projected_weighted_score(gating_checks)
        if score_text != _number_text(expected_score, "projected gate score"):
            raise ValueError("verification.gate.score does not match projected checks")
        required_ok = score >= threshold

    gate_oracle = _mapping(gate.get("oracle"), "verification.gate.oracle")
    emit_backend = _text(
        gate_oracle.get("emit_backend"), "verification.gate.oracle.emit_backend"
    )
    emit_identity = _text(
        gate_oracle.get("emit_identity"), "verification.gate.oracle.emit_identity"
    )
    if emit_identity != oracle.emit_identity:
        raise ValueError("gate emit identity does not match the locked OracleBoundary")
    emit_independent = gate_oracle.get("emit_independent")
    if emit_independent is not None:
        emit_independent = _boolean(
            emit_independent, "verification.gate.oracle.emit_independent"
        )
    corroborated_checks = [
        (raw, projected)
        for raw, projected in zip(gate["checks"], checks, strict=True)
        if projected["gating"]
        and projected["passed"]
        and projected["grounding"] == "corroborated"
    ]
    read_identities = sorted(
        {
            raw["derived_identity"]
            for raw, _ in corroborated_checks
            if isinstance(raw.get("derived_identity"), str)
            and raw["derived_identity"].strip()
        }
    )
    corroborated = _integer(
        gate_oracle.get("corroborated"), "verification.gate.oracle.corroborated"
    )
    derived_self = _integer(
        gate_oracle.get("derived_self"), "verification.gate.oracle.derived_self"
    )
    if corroborated != len(corroborated_checks):
        raise ValueError("oracle.corroborated does not match passing gating external checks")
    if derived_self != len(gating_ids) - corroborated:
        raise ValueError("oracle.derived_self does not match the gating check projection")
    oracle_gating = _integer(
        gate_oracle.get("gating"), "verification.gate.oracle.gating"
    )
    if oracle_gating != len(gating_ids):
        raise ValueError("oracle.gating does not match the gating check projection")
    single_authority = _boolean(
        gate_oracle.get("single_authority"), "verification.gate.oracle.single_authority"
    )
    if single_authority != (bool(gating_ids) and corroborated == 0):
        raise ValueError("oracle.single_authority does not match the check projection")

    corroboration_enforced = _boolean(
        gate_oracle.get("enforced"), "verification.gate.oracle.enforced"
    )
    signature_enforced = _boolean(
        gate_oracle.get("signature_enforced"),
        "verification.gate.oracle.signature_enforced",
    )
    independent_store_enforced = _boolean(
        gate_oracle.get("independent_store_enforced"),
        "verification.gate.oracle.independent_store_enforced",
    )
    authenticated = gate.get("authenticated")
    if authenticated is not None:
        authenticated = _boolean(authenticated, "verification.gate.authenticated")
    expected_authenticated = signature_enforced and bool(gating_ids)
    if expected_authenticated and authenticated not in {True, False}:
        raise ValueError("signature-enforced evidence must report authentication status")
    if not expected_authenticated and authenticated is not None:
        raise ValueError("authentication status must be null when signatures are not evaluated")

    uncorroborated = corroboration_enforced and bool(gating_ids) and corroborated == 0
    unauthenticated = expected_authenticated and authenticated is not True
    dependent_store = (
        independent_store_enforced
        and bool(gating_ids)
        and emit_independent is False
        and corroborated == 0
    )
    if _boolean(gate.get("uncorroborated"), "verification.gate.uncorroborated") != uncorroborated:
        raise ValueError("verification.gate.uncorroborated does not match oracle posture")
    if _boolean(gate.get("unauthenticated"), "verification.gate.unauthenticated") != (
        unauthenticated
    ):
        raise ValueError("verification.gate.unauthenticated does not match oracle posture")
    if _boolean(gate.get("dependent_store"), "verification.gate.dependent_store") != (
        dependent_store
    ):
        raise ValueError("verification.gate.dependent_store does not match oracle posture")

    expected_gate_ok = (
        reachable
        and complete
        and bool(gating_ids)
        and required_ok
        and not uncorroborated
        and not unauthenticated
        and not dependent_store
    )
    if gate_ok != expected_gate_ok:
        raise ValueError("verification.gate.ok does not match checks and oracle posture")
    expected_observation = (
        ObservationVerdict.INCONCLUSIVE
        if infra_uncertain
        else ObservationVerdict.PRESENT
        if gate_ok
        else ObservationVerdict.ABSENT
    )
    if observation is not expected_observation:
        raise ValueError(
            "verification.verdict does not match final reachability and gate aggregation"
        )
    lifecycle_outcome = (
        RunOutcome.INCONCLUSIVE if infra_uncertain else subject_outcome
    )
    if not infra_uncertain:
        observed_subject = RunOutcome.GREEN if gate_ok else RunOutcome.RED
        if subject_outcome is not observed_subject:
            raise ValueError("subject_outcome does not match the bounded-final gate result")
        if subject_outcome is RunOutcome.RED and required_ok:
            raise ValueError("a RED subject requires a failed gating aggregation")

    sampled = _boolean(gate.get("sampled", False), "verification.gate.sampled")
    projected_tier = _projected_evidence_tier(
        checks,
        reachable=reachable,
        sampled=sampled,
    )
    tier = projected_tier
    if tier is EvidenceTier.EXTERNAL_VERDICT:
        if not oracle.corroborated or not oracle.is_independent:
            raise ValueError(
                "external_verdict requires a corroborated, distinct locked OracleBoundary"
            )
        if oracle.read_identity not in read_identities:
            raise ValueError(
                "external_verdict requires a bound derived read identity, not only "
                "separate_source=True"
            )
    if (
        lifecycle_outcome is not RunOutcome.INCONCLUSIVE
        and role in {RunRole.POSITIVE, RunRole.REGREEN}
    ):
        if observation is not ObservationVerdict.PRESENT:
            raise ValueError(f"{role.value} requires a present readback")
        if tier not in COMPLETION_EVIDENCE_TIERS:
            raise ValueError(f"{role.value} requires arrived-or-stronger evidence")

    document = {
        "schema_version": GATE_EVIDENCE_VERSION,
        "binding": {
            "cycle": cycle.to_dict(),
            "cycle_identity_sha256": cycle.fingerprint,
            "role": role.value,
            "run_id": run_id,
            "artifact_namespace": artifact_namespace,
            "subject_outcome": subject_outcome.value,
            "lifecycle_outcome": lifecycle_outcome.value,
            "material_lock": material_lock.to_dict(),
            "material_lock_sha256": material_lock.fingerprint,
            "executed_source": executed_source.to_dict(),
            "oracle_boundary": oracle.to_dict(),
            "verification_value": _digest_verification(verification_obj).to_dict(),
        },
        "readback": {
            "cid": _text(gate.get("cid"), "verification.gate.cid"),
            "verdict": observation.value,
            "settlement": "bounded_final",
            "reachable": reachable,
            "complete": complete,
            "probe_reachable": probe_reachable,
            "attempts": _integer(
                verification_obj.get("attempts"),
                "verification.attempts",
                minimum=1,
            ),
            "arrival": _project_arrival(verification_obj.get("arrival")),
        },
        "aggregation": {
            "mode": mode,
            "ok": gate_ok,
            "gating_check_ids": gating_ids,
            "score": score_text,
            "threshold": threshold_text,
        },
        "checks": checks,
        "oracle": {
            "evidence_tier": tier.value,
            "emit_backend": emit_backend,
            "emit_identity": emit_identity,
            "emit_independent": emit_independent,
            "read_identities": read_identities,
            "corroborated": corroborated,
            "derived_self": derived_self,
            "single_authority": single_authority,
            "corroboration_enforced": corroboration_enforced,
            "signature_enforced": signature_enforced,
            "independent_store_enforced": independent_store_enforced,
            "authenticated": authenticated,
            "sampled": sampled,
        },
    }
    errors = validate_gate_evidence(document)
    if errors:
        raise ValueError("constructed gate evidence is invalid: " + "; ".join(errors))
    artifact = digest_json(
        document,
        scope=_ARTIFACT_SCOPE,
        schema_version=GATE_EVIDENCE_VERSION,
    )
    run = RunEvidence(
        role=role,
        run_id=run_id,
        artifact_namespace=artifact_namespace,
        outcome=lifecycle_outcome,
        observation=observation,
        # A scalar cannot faithfully aggregate optional/pending/custom monitors.  Keep the
        # compatibility field explicitly non-authoritative while the artifact carries them all.
        monitor=MonitorVerdict.PEND,
        evidence_tier=tier,
        artifact=artifact,
        material_lock_sha256=material_lock.fingerprint,
        executed_source=executed_source,
    )
    return GateEvidenceBundle(canonical_json_bytes(document), artifact, run)


_ROOT_FIELDS = {"schema_version", "binding", "readback", "aggregation", "checks", "oracle"}
_BINDING_FIELDS = {
    "cycle",
    "cycle_identity_sha256",
    "role",
    "run_id",
    "artifact_namespace",
    "subject_outcome",
    "lifecycle_outcome",
    "material_lock",
    "material_lock_sha256",
    "executed_source",
    "oracle_boundary",
    "verification_value",
}
_READBACK_FIELDS = {
    "cid",
    "verdict",
    "settlement",
    "reachable",
    "complete",
    "probe_reachable",
    "attempts",
    "arrival",
}
_ARRIVAL_FIELDS = {
    "visibility_delay_ms",
    "waited_ms",
    "flushed",
    "extended_for_visibility",
    "confirm_rounds_run",
}
_AGGREGATION_FIELDS = {"mode", "ok", "gating_check_ids", "score", "threshold"}
_CHECK_FIELDS = {
    "check_id",
    "ordinal",
    "label",
    "kind",
    "passed",
    "monitor",
    "settled_at",
    "optional",
    "pending",
    "tautological",
    "gating",
    "weight",
    "strength",
    "grounding",
    "charged",
}
_ORACLE_FIELDS = {
    "evidence_tier",
    "emit_backend",
    "emit_identity",
    "emit_independent",
    "read_identities",
    "corroborated",
    "derived_self",
    "single_authority",
    "corroboration_enforced",
    "signature_enforced",
    "independent_store_enforced",
    "authenticated",
    "sampled",
}


def _exact(value: Any, fields: set[str], label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    if set(value) != fields:
        errors.append(
            f"{label} fields mismatch: missing={sorted(fields - set(value))}, "
            f"extra={sorted(set(value) - fields)}"
        )
    return value


def _valid_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def _valid_decimal(value: Any) -> bool:
    if not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        return False
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _valid_nonnegative_decimal(value: Any) -> bool:
    if not _valid_decimal(value) or _NONNEGATIVE_DECIMAL_RE.fullmatch(value) is None:
        return False
    return float(value) >= 0


def _valid_unit_decimal(value: Any) -> bool:
    return _valid_nonnegative_decimal(value) and float(value) <= 1


def _valid_positive_unit_decimal(value: Any) -> bool:
    return (
        _valid_unit_decimal(value)
        and _POSITIVE_DECIMAL_RE.fullmatch(value) is not None
        and float(value) > 0
    )


def _valid_nonnegative_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _validate_digest(value: Any, label: str, errors: list[str]) -> Digest | None:
    try:
        return Digest.from_dict(value)
    except (TypeError, ValueError) as error:
        errors.append(f"{label} is invalid: {error}")
        return None


def validate_gate_evidence(
    document: Any,
    *,
    expected_artifact: Digest | None = None,
    expected_run: RunEvidence | Mapping[str, Any] | None = None,
    expected_cycle: CycleIdentity | None = None,
    expected_oracle: OracleBoundary | None = None,
    expected_verification: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return closed-shape, binding, aggregate, and optional context errors.

    Passing the ``RunEvidence`` loaded from receipt v2 closes the artifact-to-receipt
    binding.  Passing the cycle, oracle, or original verification value additionally
    checks those external context boundaries; the artifact cannot authenticate them alone.
    """

    if not isinstance(document, dict):
        return ["gate evidence must be an object"]
    try:
        frozen = _freeze_json(document)
        canonical_json_bytes(frozen)
    except (TypeError, ValueError) as error:
        return [f"gate evidence has no canonical encoding: {error}"]
    errors: list[str] = []
    root = _exact(frozen, _ROOT_FIELDS, "gate evidence", errors)
    if root.get("schema_version") != GATE_EVIDENCE_VERSION:
        errors.append(f"schema_version must be {GATE_EVIDENCE_VERSION!r}")
    binding = _exact(root.get("binding"), _BINDING_FIELDS, "binding", errors)
    readback = _exact(root.get("readback"), _READBACK_FIELDS, "readback", errors)
    aggregation = _exact(
        root.get("aggregation"), _AGGREGATION_FIELDS, "aggregation", errors
    )
    oracle_doc = _exact(root.get("oracle"), _ORACLE_FIELDS, "oracle", errors)

    cycle: CycleIdentity | None = None
    material: MaterialLock | None = None
    executed_source: Digest | None = None
    boundary: OracleBoundary | None = None
    try:
        cycle_raw = binding.get("cycle")
        if not isinstance(cycle_raw, dict):
            raise ValueError("cycle must be an object")
        cycle_id = cycle_raw.get("cycle_id")
        generation = cycle_raw.get("generation")
        predecessor = cycle_raw.get("previous_receipt_sha256")
        if not isinstance(cycle_id, str):
            raise ValueError("cycle_id must be a string")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise ValueError("generation must be an integer")
        if predecessor is not None and not isinstance(predecessor, str):
            raise ValueError("previous_receipt_sha256 must be a string or null")
        cycle = CycleIdentity(
            cycle_id,
            generation,
            predecessor,
        )
        if set(cycle_raw) != {"cycle_id", "generation", "previous_receipt_sha256"}:
            raise ValueError("cycle fields are not closed")
        if binding.get("cycle_identity_sha256") != cycle.fingerprint:
            errors.append("binding.cycle_identity_sha256 does not match cycle")
    except (TypeError, ValueError) as error:
        errors.append(f"binding.cycle is invalid: {error}")
    try:
        material = MaterialLock.from_dict(binding.get("material_lock"))
        if binding.get("material_lock_sha256") != material.fingerprint:
            errors.append("binding.material_lock_sha256 does not match material_lock")
    except (TypeError, ValueError) as error:
        errors.append(f"binding.material_lock is invalid: {error}")
    executed_source = _validate_digest(
        binding.get("executed_source"), "binding.executed_source", errors
    )
    verification_digest = _validate_digest(
        binding.get("verification_value"), "binding.verification_value", errors
    )
    if verification_digest is not None and (
        verification_digest.scope != _VERIFICATION_SCOPE
        or verification_digest.canonicalization != CANONICALIZATION
        or verification_digest.schema_version != GATE_EVIDENCE_VERSION
    ):
        errors.append("binding.verification_value has the wrong domain separation")
    try:
        boundary = OracleBoundary.from_dict(binding.get("oracle_boundary"))
    except (TypeError, ValueError) as error:
        errors.append(f"binding.oracle_boundary is invalid: {error}")
        boundary = None

    role: RunRole | None = None
    subject: RunOutcome | None = None
    lifecycle: RunOutcome | None = None
    try:
        role = RunRole(binding.get("role"))
        subject = RunOutcome(binding.get("subject_outcome"))
        lifecycle = RunOutcome(binding.get("lifecycle_outcome"))
        if subject is not _EXPECTED_OUTCOME[role]:
            errors.append("binding.subject_outcome does not match role")
    except (TypeError, ValueError):
        errors.append("binding role/outcome vocabulary is invalid")
        role = None
        subject = None
        lifecycle = None
    if material is not None and executed_source is not None and role is not None:
        if role is RunRole.NEGATIVE:
            if executed_source == material.source:
                errors.append("negative executed_source must differ from the locked baseline")
        elif executed_source != material.source:
            errors.append(f"{role.value} executed_source must equal the locked baseline")
    for field in ("run_id", "artifact_namespace"):
        if not _valid_text(binding.get(field)):
            errors.append(f"binding.{field} must be non-empty text without NUL")

    arrival = _exact(readback.get("arrival"), _ARRIVAL_FIELDS, "readback.arrival", errors)
    for field in ("visibility_delay_ms", "waited_ms", "confirm_rounds_run"):
        if not _valid_nonnegative_integer(arrival.get(field)):
            errors.append(f"readback.arrival.{field} must be a non-negative integer")
    for field in ("flushed", "extended_for_visibility"):
        if not isinstance(arrival.get(field), bool):
            errors.append(f"readback.arrival.{field} must be boolean")
    for field in ("cid",):
        if not _valid_text(readback.get(field)):
            errors.append(f"readback.{field} must be non-empty text without NUL")
    for field in ("reachable", "complete", "probe_reachable"):
        if not isinstance(readback.get(field), bool):
            errors.append(f"readback.{field} must be boolean")
    if readback.get("settlement") != "bounded_final":
        errors.append("readback.settlement must be 'bounded_final'")
    attempts = readback.get("attempts")
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or attempts < 1
    ):
        errors.append("readback.attempts must be a positive integer")
    verdict = readback.get("verdict")
    infra = not all(
        readback.get(field) is True for field in ("reachable", "complete", "probe_reachable")
    )
    if verdict == "inconclusive":
        if not infra:
            errors.append("inconclusive readback requires an infrastructure uncertainty")
        if lifecycle is not RunOutcome.INCONCLUSIVE:
            errors.append("inconclusive readback requires lifecycle_outcome inconclusive")
    elif isinstance(verdict, str) and verdict in {"present", "absent"}:
        if infra:
            errors.append("present/absent readback cannot carry infrastructure uncertainty")
        if lifecycle is not subject:
            errors.append("conclusive readback requires lifecycle_outcome == subject_outcome")
    else:
        errors.append("readback.verdict vocabulary is invalid")

    raw_checks = root.get("checks")
    checks = raw_checks if isinstance(raw_checks, list) else []
    if not isinstance(raw_checks, list):
        errors.append("checks must be an array")
    normalized_checks: list[dict[str, Any]] = []
    check_ids: list[str] = []
    gating_ids: list[str] = []
    for ordinal, raw in enumerate(checks):
        check = _exact(raw, _CHECK_FIELDS, f"checks[{ordinal}]", errors)
        normalized_checks.append(check)
        check_id = check.get("check_id")
        if not isinstance(check_id, str) or _SHA256_RE.fullmatch(check_id) is None:
            errors.append(f"checks[{ordinal}].check_id must be lowercase 64-hex")
        else:
            check_ids.append(check_id)
        if set(check) == _CHECK_FIELDS:
            projected = {key: check[key] for key in _CHECK_FIELDS - {"check_id"}}
            if isinstance(check_id, str) and check_id != _digest_check(projected).value:
                errors.append(f"checks[{ordinal}].check_id does not match projected check")
        if check.get("ordinal") != ordinal:
            errors.append(f"checks[{ordinal}].ordinal must equal its array position")
        monitor = check.get("monitor")
        if monitor is not None and (
            not isinstance(monitor, str) or monitor not in {"sat", "viol", "pend"}
        ):
            errors.append(f"checks[{ordinal}].monitor vocabulary is invalid")
        settled_at = check.get("settled_at")
        if settled_at is not None and not _valid_nonnegative_integer(settled_at):
            errors.append(f"checks[{ordinal}].settled_at must be null or non-negative integer")
        for field in ("passed", "optional", "pending", "tautological", "gating", "charged"):
            if not isinstance(check.get(field), bool):
                errors.append(f"checks[{ordinal}].{field} must be boolean")
        policy_flags = [
            check.get("optional"),
            check.get("pending"),
            check.get("tautological"),
        ]
        if all(isinstance(flag, bool) for flag in policy_flags):
            expected_gating = not any(policy_flags)
            if check.get("gating") != expected_gating:
                errors.append(f"checks[{ordinal}].gating does not match policy flags")
        if check.get("gating") and isinstance(check_id, str):
            gating_ids.append(check_id)
        if not _valid_nonnegative_decimal(check.get("weight")):
            errors.append(
                f"checks[{ordinal}].weight must be canonical non-negative decimal text"
            )
        grounding = check.get("grounding")
        if not isinstance(grounding, str) or grounding not in {
            "derived-self",
            "corroborated",
        }:
            errors.append(f"checks[{ordinal}].grounding vocabulary is invalid")
        for field in ("label", "kind", "strength"):
            if not _valid_text(check.get(field)):
                errors.append(f"checks[{ordinal}].{field} must be non-empty text")
    checks = normalized_checks
    if len(check_ids) != len(set(check_ids)):
        errors.append("check_id values must be unique")
    aggregate_ids = aggregation.get("gating_check_ids")
    if (
        not isinstance(aggregate_ids, list)
        or any(
            not isinstance(item, str) or _SHA256_RE.fullmatch(item) is None
            for item in aggregate_ids
        )
        or len(aggregate_ids) != len(set(aggregate_ids))
    ):
        errors.append("aggregation.gating_check_ids must be unique lowercase 64-hex values")
    if aggregate_ids != gating_ids:
        errors.append("aggregation.gating_check_ids does not match checks")
    gating_checks = [check for check in checks if check.get("gating") is True]
    mode = aggregation.get("mode")
    required_ok: bool | None = None
    if mode == "all":
        if aggregation.get("score") is not None or aggregation.get("threshold") is not None:
            errors.append("all aggregation requires null score and threshold")
        required_ok = all(check.get("passed") is True for check in gating_checks)
    elif mode == "weighted_threshold":
        if not _valid_unit_decimal(
            aggregation.get("score")
        ) or not _valid_positive_unit_decimal(aggregation.get("threshold")):
            errors.append(
                "weighted_threshold requires score in [0, 1] and threshold in (0, 1]"
            )
        elif all(
            _valid_nonnegative_decimal(check.get("weight"))
            for check in gating_checks
        ):
            try:
                expected_score = _projected_weighted_score(gating_checks)
                expected_score_text = _number_text(expected_score, "projected score")
                if aggregation.get("score") != expected_score_text:
                    errors.append("aggregation.score does not match projected checks")
                required_ok = expected_score >= float(aggregation["threshold"])
            except ValueError as error:
                errors.append(str(error))
    else:
        errors.append("aggregation.mode vocabulary is invalid")
    if not isinstance(aggregation.get("ok"), bool):
        errors.append("aggregation.ok must be boolean")

    try:
        tier = EvidenceTier(oracle_doc.get("evidence_tier"))
    except (TypeError, ValueError):
        errors.append("oracle.evidence_tier vocabulary is invalid")
        tier = None
    for field in ("emit_backend", "emit_identity"):
        if not _valid_text(oracle_doc.get(field)):
            errors.append(f"oracle.{field} must be non-empty text")
    if boundary is not None and oracle_doc.get("emit_identity") != boundary.emit_identity:
        errors.append("oracle.emit_identity does not match the locked OracleBoundary")
    read_ids = oracle_doc.get("read_identities")
    if not isinstance(read_ids, list) or any(not _valid_text(item) for item in read_ids):
        errors.append("oracle.read_identities must be an array of non-empty text")
        read_ids = []
    elif read_ids != sorted(set(read_ids)):
        errors.append("oracle.read_identities must be sorted and unique")
    for field in ("corroborated", "derived_self"):
        value = oracle_doc.get(field)
        if not _valid_nonnegative_integer(value):
            errors.append(f"oracle.{field} must be a non-negative integer")
    for field in (
        "single_authority",
        "corroboration_enforced",
        "signature_enforced",
        "independent_store_enforced",
        "sampled",
    ):
        if not isinstance(oracle_doc.get(field), bool):
            errors.append(f"oracle.{field} must be boolean")
    for field in ("authenticated", "emit_independent"):
        if oracle_doc.get(field) is not None and not isinstance(oracle_doc.get(field), bool):
            errors.append(f"oracle.{field} must be boolean or null")
    corroborated = sum(
        1
        for check in checks
        if check.get("gating")
        and check.get("passed")
        and check.get("grounding") == "corroborated"
    )
    if oracle_doc.get("corroborated") != corroborated:
        errors.append("oracle.corroborated does not match checks")
    if oracle_doc.get("derived_self") != len(gating_ids) - corroborated:
        errors.append("oracle.derived_self does not match checks")
    if oracle_doc.get("single_authority") != (bool(gating_ids) and corroborated == 0):
        errors.append("oracle.single_authority does not match checks")
    signature_evaluated = (
        oracle_doc.get("signature_enforced") is True and bool(gating_ids)
    )
    authenticated = oracle_doc.get("authenticated")
    if signature_evaluated and authenticated not in {True, False}:
        errors.append("signature-enforced evidence must report authentication status")
    if not signature_evaluated and authenticated is not None:
        errors.append("authentication status must be null when signatures are not evaluated")

    sampled = oracle_doc.get("sampled") is True
    if all(set(check) == _CHECK_FIELDS for check in checks):
        projected_tier = _projected_evidence_tier(
            checks,
            reachable=readback.get("reachable") is True,
            sampled=sampled,
        )
        if tier is not projected_tier:
            errors.append("oracle.evidence_tier does not match projected checks")
    if tier is EvidenceTier.EXTERNAL_VERDICT:
        if boundary is None or not boundary.corroborated or not boundary.is_independent:
            errors.append("external_verdict lacks a corroborated independent OracleBoundary")
        elif boundary.read_identity not in read_ids:
            errors.append("external_verdict lacks its bound derived read identity")
    uncorroborated = (
        oracle_doc.get("corroboration_enforced") is True
        and bool(gating_ids)
        and corroborated == 0
    )
    unauthenticated = signature_evaluated and authenticated is not True
    dependent_store = (
        oracle_doc.get("independent_store_enforced") is True
        and bool(gating_ids)
        and oracle_doc.get("emit_independent") is False
        and corroborated == 0
    )
    if required_ok is not None:
        expected_ok = (
            readback.get("reachable") is True
            and readback.get("complete") is True
            and bool(gating_ids)
            and required_ok
            and not uncorroborated
            and not unauthenticated
            and not dependent_store
        )
        if aggregation.get("ok") != expected_ok:
            errors.append("aggregation.ok does not match checks and oracle posture")
    expected_verdict = (
        "inconclusive"
        if infra
        else "present"
        if aggregation.get("ok") is True
        else "absent"
    )
    if verdict != expected_verdict:
        errors.append("readback.verdict does not match reachability and aggregation")
    if not infra and subject is not None:
        expected_subject = (
            RunOutcome.GREEN if aggregation.get("ok") is True else RunOutcome.RED
        )
        if subject is not expected_subject:
            errors.append("binding.subject_outcome does not match the bounded-final gate result")
        if subject is RunOutcome.RED and required_ok is True:
            errors.append("a RED subject requires a failed gating aggregation")
    if role in {RunRole.POSITIVE, RunRole.REGREEN} and lifecycle is not RunOutcome.INCONCLUSIVE:
        if verdict != "present":
            errors.append(f"{role.value} requires a present readback")
        if tier not in COMPLETION_EVIDENCE_TIERS:
            errors.append(f"{role.value} requires arrived-or-stronger evidence")

    actual_artifact = digest_json(
        frozen,
        scope=_ARTIFACT_SCOPE,
        schema_version=GATE_EVIDENCE_VERSION,
    )
    if expected_artifact is not None:
        if not isinstance(expected_artifact, Digest):
            errors.append("expected_artifact must be a Digest")
        elif expected_artifact != actual_artifact:
            errors.append("expected artifact digest does not match gate evidence")

    if expected_cycle is not None:
        if not isinstance(expected_cycle, CycleIdentity):
            errors.append("expected_cycle must be a CycleIdentity")
        elif cycle != expected_cycle:
            errors.append("expected cycle does not match gate evidence")
    if expected_oracle is not None:
        if not isinstance(expected_oracle, OracleBoundary):
            errors.append("expected_oracle must be an OracleBoundary")
        elif boundary != expected_oracle:
            errors.append("expected oracle does not match gate evidence")
    if expected_verification is not None:
        if not isinstance(expected_verification, Mapping):
            errors.append("expected_verification must be a mapping")
        else:
            try:
                expected_value = _mapping(
                    _freeze_json(dict(expected_verification)),
                    "expected_verification",
                )
                expected_verification_digest = _digest_verification(expected_value)
                if verification_digest != expected_verification_digest:
                    errors.append("expected verification does not match gate evidence")
            except (TypeError, ValueError) as error:
                errors.append(f"expected_verification is invalid: {error}")

    if expected_run is not None:
        if isinstance(expected_run, RunEvidence):
            run_doc: Any = expected_run.to_dict()
        elif isinstance(expected_run, Mapping):
            try:
                run_doc = _freeze_json(dict(expected_run))
            except (TypeError, ValueError) as error:
                errors.append(f"expected_run is invalid: {error}")
                run_doc = None
        else:
            errors.append("expected_run must be RunEvidence or a mapping")
            run_doc = None
        if isinstance(run_doc, dict):
            expected_run_fields = {
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
            if set(run_doc) != expected_run_fields:
                errors.append("expected_run fields do not match RunEvidence")
            comparisons = {
                "role": binding.get("role"),
                "run_id": binding.get("run_id"),
                "artifact_namespace": binding.get("artifact_namespace"),
                "outcome": binding.get("lifecycle_outcome"),
                "observation": readback.get("verdict"),
                "monitor": MonitorVerdict.PEND.value,
                "evidence_tier": oracle_doc.get("evidence_tier"),
                "artifact": actual_artifact.to_dict(),
                "material_lock_sha256": binding.get("material_lock_sha256"),
                "executed_source": binding.get("executed_source"),
            }
            for field, expected in comparisons.items():
                if run_doc.get(field) != expected:
                    errors.append(f"expected_run.{field} does not match gate evidence")
    return errors


__all__ = [
    "GATE_EVIDENCE_VERSION",
    "OUROBOROS_GATE_EVIDENCE_SCHEMA",
    "GateEvidenceBundle",
    "adapt_gate_verification",
    "validate_gate_evidence",
]
