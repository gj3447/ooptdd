from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ooptdd_mutation.ouroboros import (
    MAX_INTEROPERABLE_INTEGER,
    OUROBOROS_RECEIPT_SCHEMA,
    validate_receipt,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("status", [[], {}, ["complete"]])
def test_runtime_validator_returns_errors_for_unhashable_status(status):
    errors = validate_receipt({"status": status})
    assert errors
    assert "status must be" in "\n".join(errors)


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
