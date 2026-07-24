"""Observation-derived metrics and source/spec bindings fail closed."""
from __future__ import annotations

import copy

import pytest

from ooptdd.evidence_integrity import (
    EvidenceIntegrityError,
    deepeval_metrics,
    expected_deepeval_mismatch,
    measurement_environment,
    trajectory_metrics,
    validate_chronology,
    validate_deepeval_measurement,
    validate_deepeval_mismatch,
    validate_measurement,
    validate_measurement_lock,
)

HEAD = "a" * 40
SPEC = "b" * 64
GAPS = ("matcher_composition", "forbidden_tool_calls", "phoenix_annotation")


def _trajectory() -> dict:
    observations = [
        {"group": "matcher_composition", "name": "safe", "matched": True,
         "expected_ok": True, "observed_ok": True,
         "unsafe": False, "readback_count": 1},
        {"group": "forbidden_tool_calls", "name": "danger", "matched": True,
         "expected_ok": False, "observed_ok": False,
         "unsafe": True, "readback_count": 1},
        {"group": "phoenix_annotation", "name": "present", "matched": True,
         "expected_ok": True, "observed_ok": True,
         "unsafe": False},
    ]
    return {
        "measured_at": "2026-07-23T07:00:00+00:00",
        "source": {"git_head": HEAD, "dirty": False},
        "spec": {"sha256": SPEC},
        "fault_injected": False,
        "observations": observations,
        "metrics": trajectory_metrics(observations, gap_groups=GAPS),
    }


def _validate(record: dict):
    return validate_measurement(
        record,
        recompute=lambda rows: trajectory_metrics(rows, gap_groups=GAPS),
        expected_head=HEAD,
        expected_spec_sha256=SPEC,
        expected_fault_injected=False,
    )


def test_valid_measurement_is_recomputed_from_observations():
    computed = _validate(_trajectory())
    assert computed["cases_matched"] == 3
    assert computed["unresolved_mechanism_groups"] == 0


@pytest.mark.parametrize("tamper", ["metric", "observation", "head", "spec", "dirty"])
def test_tampered_summary_or_binding_is_rejected(tamper):
    record = copy.deepcopy(_trajectory())
    if tamper == "metric":
        record["metrics"]["cases_matched"] = 99
    elif tamper == "observation":
        record["observations"][0]["matched"] = False
    elif tamper == "head":
        record["source"]["git_head"] = "c" * 40
    elif tamper == "spec":
        record["spec"]["sha256"] = "d" * 64
    elif tamper == "dirty":
        record["source"]["dirty"] = True
    with pytest.raises(EvidenceIntegrityError):
        _validate(record)


def test_duplicate_observation_identity_is_rejected():
    record = _trajectory()
    record["observations"].append(copy.deepcopy(record["observations"][0]))
    with pytest.raises(EvidenceIntegrityError, match="duplicate observation"):
        _validate(record)


def test_deepeval_metric_name_does_not_confuse_agreement_with_success():
    rows = [
        {"name": "safe", "matched": True, "expected_score": 1.0,
         "observed_score": 1.0, "expected_success": True, "observed_success": True},
        {"name": "dangerous", "matched": True, "expected_score": 0.0,
         "observed_score": 0.0, "expected_success": False, "observed_success": False},
        {"name": "corrupt", "matched": True, "expected_score": 0.0,
         "observed_score": 0.0, "expected_success": False, "observed_success": False},
    ]
    metrics = deepeval_metrics(rows)
    assert metrics["deepeval_oracle_agreement_rate"] == 1.0
    assert metrics["actual_successes"] == 1


def test_matched_flags_are_recomputed_from_raw_expected_and_observed_values():
    trajectory = _trajectory()
    trajectory["observations"][0]["observed_ok"] = False
    with pytest.raises(EvidenceIntegrityError, match="producer-authored"):
        _validate(trajectory)

    rows = [
        {"name": "forged", "matched": True, "expected_score": 1.0,
         "observed_score": 0.0, "expected_success": True, "observed_success": False},
    ]
    with pytest.raises(EvidenceIntegrityError, match="producer-authored"):
        deepeval_metrics(rows)


def test_negative_then_restored_chronology_is_strict():
    negative = {"measured_at": "2026-07-23T07:01:00+00:00"}
    restored = {"measured_at": "2026-07-23T07:02:00+00:00"}
    validate_chronology("2026-07-23T07:00:00+00:00", negative, restored)
    with pytest.raises(EvidenceIntegrityError, match="chronology"):
        validate_chronology("2026-07-23T07:00:00+00:00", restored, negative)


def test_missing_required_trajectory_group_is_unresolved_not_silently_absent():
    rows = _trajectory()["observations"]
    rows = [row for row in rows if row["group"] != "forbidden_tool_calls"]
    metrics = trajectory_metrics(rows, gap_groups=GAPS)
    assert metrics["missing_groups"] == ["forbidden_tool_calls"]
    assert metrics["unresolved_mechanism_groups"] == 1


def test_deepeval_validator_pins_case_names_and_oracles():
    rows = [
        {"name": "safe", "matched": True, "expected_score": 1.0,
         "observed_score": 1.0, "expected_success": True, "observed_success": True},
        {"name": "destructive", "matched": True, "expected_score": 0.0,
         "observed_score": 0.0, "expected_success": False, "observed_success": False},
        {"name": "corrupt", "matched": True, "expected_score": 0.0,
         "observed_score": 0.0, "expected_success": False, "observed_success": False},
    ]
    record = {
        "measured_at": "2026-07-23T07:00:00Z",
        "source": {"git_head": HEAD, "dirty": False},
        "spec": {"sha256": SPEC},
        "environment": {"deepeval": "4.0.7"},
        "observations": rows,
        "metrics": {"cases_total": 3, "cases_matched": 3,
                    "actual_deepeval_trajectory_pass_rate": 1.0},
    }
    computed = validate_deepeval_measurement(
        record,
        expected_head=HEAD,
        expected_spec_sha256=SPEC,
        expected_version="4.0.7",
    )
    assert computed["deepeval_oracle_agreement_rate"] == 1.0
    record["observations"][0]["expected_success"] = False
    with pytest.raises(EvidenceIntegrityError, match="oracle identities"):
        validate_deepeval_measurement(
            record,
            expected_head=HEAD,
            expected_spec_sha256=SPEC,
            expected_version="4.0.7",
        )


def test_deepeval_zero_score_from_evaluation_error_is_not_oracle_agreement():
    rows = [
        {"name": "safe", "matched": True, "expected_score": 1.0,
         "observed_score": 1.0, "expected_success": True, "observed_success": True},
        {"name": "destructive", "matched": True, "expected_score": 0.0,
         "observed_score": 0.0, "expected_success": False, "observed_success": False,
         "error": "backend exploded"},
        {"name": "corrupt", "matched": True, "expected_score": 0.0,
         "observed_score": 0.0, "expected_success": False, "observed_success": False},
    ]
    record = {
        "measured_at": "2026-07-23T07:00:00Z",
        "source": {"git_head": HEAD, "dirty": False},
        "spec": {"sha256": SPEC},
        "environment": {"deepeval": "4.0.7"},
        "observations": rows,
        "metrics": {"cases_total": 3, "cases_matched": 3,
                    "actual_deepeval_trajectory_pass_rate": 1.0},
    }
    with pytest.raises(EvidenceIntegrityError, match="evaluation errors"):
        validate_deepeval_measurement(
            record,
            expected_head=HEAD,
            expected_spec_sha256=SPEC,
            expected_version="4.0.7",
        )


def test_deepeval_negative_is_exactly_one_frozen_observation_flip():
    candidate = {
        "observations": [
            {"name": "safe", "observed_success": True, "matched": True},
            {"name": "destructive", "observed_success": False, "matched": True},
        ],
        "source": {"git_head": HEAD},
    }
    negative = expected_deepeval_mismatch(candidate)
    validate_deepeval_mismatch(candidate, negative)
    assert negative["observations"][0]["observed_success"] is False
    assert candidate["observations"][0]["observed_success"] is True

    malformed = copy.deepcopy(negative)
    malformed["source"]["git_head"] = "wrong"
    with pytest.raises(EvidenceIntegrityError, match="exact"):
        validate_deepeval_mismatch(candidate, malformed)


def test_measurement_lock_rejects_bool_numeric_coercion():
    lock = {
        "schema": "ooptdd-efficacy-measurement-lock/v1",
        "candidate_dirty": False,
        "candidate_git_head": "a" * 40,
        "seed": 1,
        "repetitions": 20,
        "tier": "tier0-mechanics",
        "deepeval_version": "4.0.7",
        "registration_repository": "https://github.com/gj3447/lakatotree",
        "environment": measurement_environment(),
        **{field: "b" * 64 for field in (
            "preregistration_sha256",
            "benchmark_definition_sha256",
            "code_manifest_sha256",
            "manifest_sha256",
            "gate_spec_sha256",
            "events_sha256",
            "runner_sha256",
            "deepeval_spec_sha256",
        )},
    }
    validate_measurement_lock(lock)
    forged = copy.deepcopy(lock)
    forged["repetitions"] = True
    with pytest.raises(EvidenceIntegrityError, match="repetitions"):
        validate_measurement_lock(forged)
