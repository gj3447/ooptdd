from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from ooptdd.ouroboros import (
    MAX_INTEROPERABLE_INTEGER,
    OUROBOROS_RECEIPT_SCHEMA,
    RECEIPT_VERSION,
    upcast_v1_receipt,
    validate_receipt,
)

ROOT = Path(__file__).resolve().parents[1]


def _legacy_fixture() -> dict:
    return {
        "schema_version": "symposium-ooptdd-receipt/v1",
        "template_only": False,
        "receipt_id": "legacy-receipt",
        "cycle_id": "legacy-cycle",
        "requirement_group": "R1",
        "spec": {"sha256": "1" * 64},
        "positive": {"charge_ratio": 1.0, "observed_verdict": "green"},
        "negative_oracle": {"observed_verdict": "red", "restored": True},
    }


def test_v1_upcast_preserves_the_parsed_object_and_never_completes_missing_proof():
    legacy = _legacy_fixture()
    first = upcast_v1_receipt(legacy)
    second = upcast_v1_receipt(copy.deepcopy(legacy))
    assert first == second
    assert first["schema_version"] == RECEIPT_VERSION
    assert first["status"] == "incomplete"
    assert first["legacy"]["source"] == legacy
    assert "initial_red_run_identity_and_artifact" in first["missing_obligations"]
    assert "restored_regreen_run" in first["missing_obligations"]
    assert first["integrity"]["value"] is None
    assert validate_receipt(first) == []


def test_v2_upcast_is_idempotent_and_defensively_copied():
    first = upcast_v1_receipt(_legacy_fixture())
    second = upcast_v1_receipt(first)
    assert second == first
    assert second is not first
    second["missing_obligations"].append("new")
    assert "new" not in first["missing_obligations"]


def test_v2_upcast_rejects_an_invalid_existing_v2_document():
    receipt = upcast_v1_receipt(_legacy_fixture())
    receipt["unexpected_authority_claim"] = {"trusted": True}
    with pytest.raises(ValueError, match="existing v2 receipt is invalid"):
        upcast_v1_receipt(receipt)


def test_runtime_validator_rejects_fields_outside_the_status_shape():
    receipt = upcast_v1_receipt(_legacy_fixture())
    receipt["unexpected_authority_claim"] = {"trusted": True}
    errors = "\n".join(validate_receipt(receipt))
    assert "extra=['unexpected_authority_claim']" in errors


@pytest.mark.parametrize("status", [[], {}, ["complete"]])
def test_runtime_validator_returns_errors_for_unhashable_status(status):
    errors = validate_receipt({"status": status})
    assert errors
    assert "status must be" in "\n".join(errors)


def test_runtime_validator_returns_errors_for_non_interoperable_generation():
    receipt = upcast_v1_receipt(_legacy_fixture())
    receipt["cycle"]["generation"] = MAX_INTEROPERABLE_INTEGER
    receipt["cycle"]["previous_receipt_sha256"] = "0" * 64
    errors = validate_receipt(receipt)
    assert errors
    assert "interoperable successor-budget" in "\n".join(errors)


def test_legacy_tampering_is_detected():
    receipt = upcast_v1_receipt(_legacy_fixture())
    receipt["legacy"]["source"]["cycle_id"] = "changed"
    assert "legacy.source_object_sha256 does not match" in "\n".join(validate_receipt(receipt))


def test_on_disk_ouroboros_schema_mirrors_package_constant():
    path = ROOT / "docs" / "schema" / "ouroboros-receipt-v2.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == OUROBOROS_RECEIPT_SCHEMA


def test_json_schema_declares_status_conditional_complete_fields():
    assert OUROBOROS_RECEIPT_SCHEMA["additionalProperties"] is False
    assert "validate_receipt" in OUROBOROS_RECEIPT_SCHEMA["$comment"]
    assert "run" in OUROBOROS_RECEIPT_SCHEMA["$defs"]
    complete_then = OUROBOROS_RECEIPT_SCHEMA["allOf"][0]["then"]
    assert {
        "material_lock",
        "oracle_boundary",
        "runs",
        "mutation",
        "findings",
        "lineage",
        "budget",
        "trace",
    } <= set(complete_then["required"])

    identifier_pattern = OUROBOROS_RECEIPT_SCHEMA["properties"]["receipt_id"]["pattern"]
    assert re.fullmatch(identifier_pattern, "receipt-1")
    assert re.fullmatch(identifier_pattern, "   ") is None
    assert re.fullmatch(identifier_pattern, "receipt\0bad") is None

    cycle_schema = OUROBOROS_RECEIPT_SCHEMA["$defs"]["cycle"]
    assert cycle_schema["properties"]["generation"]["maximum"] == (MAX_INTEROPERABLE_INTEGER - 1)
    [generation_rule] = cycle_schema["allOf"]
    assert generation_rule["if"]["properties"]["generation"] == {"const": 0}
    assert generation_rule["then"]["properties"]["previous_receipt_sha256"] == {"type": "null"}
    assert generation_rule["else"]["properties"]["previous_receipt_sha256"] == {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }
