import copy
import json
import xml.etree.ElementTree as ET

import pytest

from ooptdd.benchmark import (
    benchmark_gate_result,
    canonical_json,
    pass_hat_k,
    render_benchmark_junit,
    run_tier0_benchmark,
    validate_tier0_result,
)
from ooptdd.evidence_integrity import EvidenceIntegrityError


def test_tier0_positive_is_nonvacuous_and_honestly_scoped():
    result = run_tier0_benchmark(repetitions=3)
    assert result["passed"] is True
    assert result["tier"] == "tier0-mechanics"
    assert result["independent"] is False
    assert "not proof" in result["claim_boundary"]
    assert result["metrics"]["required_oracle_match_rate"]["value"] == 1.0
    mutation = result["metrics"]["M4_trajectory_mutation"]
    assert mutation["eligible"] == 5
    assert mutation["score_status"] == "measured"
    assert mutation["score"] == 1.0
    assert mutation["canary_survived"] is False


def test_two_runs_are_byte_identical():
    one = canonical_json(run_tier0_benchmark(repetitions=2))
    two = canonical_json(run_tier0_benchmark(repetitions=2))
    assert one == two
    assert json.loads(one)["passed"] is True


def test_seed_drives_distinct_repeated_case_variants():
    one = run_tier0_benchmark(seed=1, repetitions=4)
    other = run_tier0_benchmark(seed=999, repetitions=4)
    one_lag = next(row for row in one["scenarios"] if row["id"] == "lag-within-window")
    other_lag = next(row for row in other["scenarios"] if row["id"] == "lag-within-window")
    one_variants = [sample["case_parameters"] for sample in one_lag["samples"]]
    other_variants = [sample["case_parameters"] for sample in other_lag["samples"]]
    assert len({item["variant_id"] for item in one_variants}) == 4
    assert one_variants != other_variants


def test_same_manifest_fault_control_fails_only_load_bearing_mechanism():
    positive = run_tier0_benchmark(repetitions=2)
    negative = run_tier0_benchmark(
        repetitions=2,
        fault_injection="disable-confirm-rounds",
    )
    assert negative["passed"] is False
    assert positive["provenance"] == negative["provenance"]
    failed = [row["id"] for row in negative["scenarios"] if row["oracle_match_rate"] < 1.0]
    assert failed == ["late-offender-confirm"]
    assert negative["metrics"]["M2b_late_offender_catch_rate"]["value"] == 0.0


def test_consistently_rewritten_negative_summary_still_fails_deterministic_replay():
    forged = run_tier0_benchmark(
        repetitions=1,
        fault_injection="disable-confirm-rounds",
    )
    row = next(item for item in forged["scenarios"] if item["id"] == "late-offender-confirm")
    row["samples"][0]["observed"] = "absent"
    row["samples"][0]["oracle_match"] = True
    row["oracle_matches"] = 1
    row["oracle_match_rate"] = 1.0
    row["pass_hat_k"]["value"] = 1.0
    forged["metrics"]["M2b_late_offender_catch_rate"]["value"] = 1.0
    forged["metrics"]["required_oracle_match_rate"]["value"] = 1.0
    forged["passed"] = True
    with pytest.raises(EvidenceIntegrityError, match="deterministic replay"):
        validate_tier0_result(forged)


def test_junit_is_projection_and_has_no_failure_on_positive():
    result = run_tier0_benchmark(repetitions=1)
    projection = benchmark_gate_result(result)
    assert len(projection["checks"]) == len(result["scenarios"]) + len(result["conformance"])
    root = ET.fromstring(render_benchmark_junit(result))
    assert root.get("failures") == "0"
    assert root.get("skipped") == "1"
    outage = next(case for case in root.iter("testcase") if case.get("name") == "backend-outage")
    assert outage.find("skipped") is not None


def test_inconclusive_scenario_is_oracle_success_not_falsification():
    result = run_tier0_benchmark(repetitions=1)
    outage = next(row for row in result["scenarios"] if row["id"] == "backend-outage")
    [sample] = outage["samples"]
    assert sample["observed"] == "inconclusive"
    assert sample["oracle_match"] is True
    assert sample["junit_failures"] == 0


def test_independence_and_external_tier_are_measured_through_verify_path():
    result = run_tier0_benchmark(repetitions=1)
    rows = {row["id"]: row for row in result["scenarios"]}
    [dependent] = rows["dependent-store-demotion"]["samples"]
    [external] = rows["external-corroboration"]["samples"]
    assert dependent["dependent_store"] is True
    assert dependent["observed"] == "absent"
    assert external["dependent_store"] is False
    assert external["evidence_tier"] == "external_verdict"


def test_pass_hat_k_matches_tau_bench_combinatorial_estimator():
    assert pass_hat_k(20, 20, 8) == 1.0
    assert pass_hat_k(7, 20, 8) == 0.0
    assert pass_hat_k(9, 10, 2) == 0.8


def test_validator_recomputes_samples_metrics_and_file_bindings():
    result = run_tier0_benchmark(repetitions=1)
    assert validate_tier0_result(result)["passed"] is True

    forged_metric = copy.deepcopy(result)
    forged_metric["metrics"]["required_oracle_match_rate"]["value"] = 99.0
    with pytest.raises(EvidenceIntegrityError, match="metrics"):
        validate_tier0_result(forged_metric)

    forged_sample = copy.deepcopy(result)
    forged_sample["scenarios"][0]["samples"][0]["observed"] = "present"
    with pytest.raises(EvidenceIntegrityError, match="oracle_match"):
        validate_tier0_result(forged_sample)

    forged_binding = copy.deepcopy(result)
    forged_binding["provenance"]["files"]["manifest"] = "0" * 64
    with pytest.raises(EvidenceIntegrityError, match="provenance"):
        validate_tier0_result(forged_binding)

    forged_code = copy.deepcopy(result)
    forged_code["provenance"]["code_manifest"]["ooptdd/engine/monitor.py"] = "0" * 64
    with pytest.raises(EvidenceIntegrityError, match="provenance"):
        validate_tier0_result(forged_code)

    forged_mutation = copy.deepcopy(result)
    mutation_row = next(
        row for row in forged_mutation["scenarios"] if row["id"] == "trajectory-mutation"
    )
    mutation_row["samples"][0]["eligible"] = 0
    with pytest.raises(EvidenceIntegrityError, match="mutation observation"):
        validate_tier0_result(forged_mutation)
