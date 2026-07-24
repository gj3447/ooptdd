"""Tier-1 external-store benchmark: pure-function, projection, and proxy contracts.

No live store here: scenario oracles are fed synthetic receipt rows, the lock validator
and binding helpers are exercised directly, and the delaying proxy is measured against a
local in-process upstream. The live measurement itself is a scheduled-workflow run.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

from ooptdd.evidence_integrity import (
    EvidenceIntegrityError,
    measurement_environment,
    validate_measurement_lock,
    validate_tier1_measurement_lock,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_script(stem: str) -> ModuleType:
    module_name = f"_ooptdd_test_{stem}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / f"{stem}.py")
    assert spec is not None and spec.loader is not None
    scripts_path = str(SCRIPTS_DIR)
    sys.path.insert(0, scripts_path)
    try:
        module = importlib.util.module_from_spec(spec)
        # Register before exec: module-level @dataclass under deferred annotations
        # resolves its own module via sys.modules during decoration.
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_path)
    return module


T1 = _load_script("run_tier1_benchmark")

_HASH_FIELDS = (
    "preregistration_sha256",
    "benchmark_definition_sha256",
    "code_manifest_sha256",
    "manifest_sha256",
    "gate_spec_sha256",
    "events_sha256",
    "runner_sha256",
    "deepeval_spec_sha256",
)


def _tier1_lock(**updates) -> dict:
    lock = {
        "schema": "ooptdd-efficacy-measurement-lock/v1",
        "candidate_dirty": False,
        "candidate_git_head": "a" * 40,
        "seed": 20260723,
        "repetitions": 20,
        "tier": "tier1-external-store",
        "deepeval_version": "4.0.7",
        "registration_repository": "https://github.com/gj3447/lakatotree",
        "environment": measurement_environment(),
        **{field: "b" * 64 for field in _HASH_FIELDS},
    }
    lock.update(updates)
    return lock


# ── deterministic identities ──────────────────────────────────────────────────


def test_variant_id_matches_the_seeded_sha256_formula():
    expected = hashlib.sha256(b"20260723:T1-lag:7").hexdigest()[:16]
    assert T1.variant_id(20260723, "T1-lag", 7) == expected
    assert T1.variant_id(20260723, "T1-lag", 7) == T1.variant_id(20260723, "T1-lag", 7)


def test_variant_id_is_unique_across_scenarios_repeats_and_seeds():
    per_scenario = {T1.variant_id(1, "T1-lag", repeat) for repeat in range(20)}
    assert len(per_scenario) == 20
    across_scenarios = {T1.variant_id(1, declared["id"], 0) for declared in T1.SCENARIO_DEFS}
    assert len(across_scenarios) == len(T1.SCENARIO_DEFS)
    assert T1.variant_id(1, "T1-lag", 0) != T1.variant_id(2, "T1-lag", 0)


def test_run_plan_is_deterministic_with_unique_streams_and_cids():
    plan = T1.run_plan(20260723, 3)
    assert plan == T1.run_plan(20260723, 3)
    assert len(plan) == 15
    assert len({rep["variant_id"] for rep in plan}) == 15
    assert len({rep["stream"] for rep in plan}) == 15
    assert len({rep["cid"] for rep in plan}) == 15
    assert [rep["scenario_id"] for rep in plan[::3]] == [d["id"] for d in T1.SCENARIO_DEFS]


def test_lag_hold_stays_below_the_declared_visibility_window():
    for seed in (1, 7, 20260723):
        for repeat in range(20):
            hold = T1.lag_hold_ms(T1.variant_id(seed, "T1-lag", repeat))
            assert 1000 <= hold < T1.DECLARED_VISIBILITY_MS


# ── statistics ────────────────────────────────────────────────────────────────


def test_wilson_interval_edges_and_midpoint():
    perfect = T1.wilson_interval(20, 20)
    assert perfect["high"] == pytest.approx(1.0)
    assert 0.80 < perfect["low"] < 0.90
    none = T1.wilson_interval(0, 20)
    assert none["low"] == 0.0
    assert 0.10 < none["high"] < 0.25
    half = T1.wilson_interval(10, 20)
    assert half["low"] < 0.5 < half["high"]
    assert 0.25 < half["low"] < 0.40


@pytest.mark.parametrize(
    ("successes", "trials"),
    [(1, 0), (-1, 5), (6, 5), (True, 5), (1, True), (1.5, 5)],
)
def test_wilson_interval_rejects_undefined_denominators(successes, trials):
    with pytest.raises(ValueError):
        T1.wilson_interval(successes, trials)


def test_rate_rollup_tracks_the_target_side_and_pass_hat_k():
    caught = T1.rate_rollup([True] * 20, target=1.0)
    assert caught["value"] == 1.0
    assert caught["trials"] == 20
    assert caught["occurrences"] == 20
    assert caught["pass_hat_k"] == {"k": 8, "value": 1.0}
    assert caught["wilson_95"]["high"] == pytest.approx(1.0)

    clean = T1.rate_rollup([False] * 20, target=0.0)  # zero false REDs on a zero-target
    assert clean["value"] == 0.0
    assert clean["pass_hat_k"]["value"] == 1.0  # every panel subset was clean

    one_false_red = T1.rate_rollup([True] + [False] * 19, target=0.0)
    assert one_false_red["value"] == pytest.approx(0.05)
    assert one_false_red["pass_hat_k"]["value"] == pytest.approx(0.6)  # C(19,8)/C(20,8)

    with pytest.raises(ValueError, match="at least one trial"):
        T1.rate_rollup([], target=1.0)


def test_scenario_rollup_matches_the_tier0_convention():
    samples = [{"oracle_match": True}, {"oracle_match": False}]
    row = T1.scenario_rollup("T1-lag", "present", samples)
    assert row["attempts"] == 2
    assert row["oracle_matches"] == 1
    assert row["oracle_match_rate"] == 0.5
    assert row["pass_hat_k"] == {"k": 2, "value": 0.0}  # C(1,2)/C(2,2)
    assert row["samples"] is samples


# ── per-scenario oracle evaluation over synthetic receipts ────────────────────


def _loss_drop_sample(**patch):
    sample = {
        "observed": "absent",
        "ship_receipt": {"attempted": True},
        "readback": {"reachable": True, "rows": 1, "required_rows": 0},
    }
    sample.update(patch)
    return sample


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (_loss_drop_sample(), True),
        (_loss_drop_sample(observed="present"), False),  # a GREEN here is a fake green
        (_loss_drop_sample(readback={"reachable": True, "rows": 1, "required_rows": 1}), False),
        (_loss_drop_sample(readback={"reachable": False, "rows": 0, "required_rows": 0}), False),
        # secondary also missing -> total loss, not the isolated drop fault
        (_loss_drop_sample(readback={"reachable": True, "rows": 0, "required_rows": 0}), False),
    ],
)
def test_loss_drop_oracle(sample, expected):
    assert T1.sample_oracle_match("T1-loss-drop", sample) is expected


def _loss_401_sample(**patch):
    sample = {
        "observed": "absent",
        "ship_receipt": {"http_status": 401},
        "readback": {"reachable": True, "rows": 0, "required_rows": 0},
    }
    sample.update(patch)
    return sample


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (_loss_401_sample(), True),
        (_loss_401_sample(observed="present"), False),  # fake green
        (_loss_401_sample(ship_receipt={"http_status": None}), False),  # no 401 receipt
        (_loss_401_sample(readback={"reachable": True, "rows": 1, "required_rows": 1}), False),
    ],
)
def test_loss_401_oracle(sample, expected):
    assert T1.sample_oracle_match("T1-loss-401", sample) is expected


def _lag_sample(**patch):
    sample = {
        "observed": "present",
        "arrival": {"extended_for_visibility": True},
        "intermediate_absent_verdicts": 0,
    }
    sample.update(patch)
    return sample


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (_lag_sample(), True),
        (_lag_sample(observed="absent"), False),  # the false RED the metric hunts
        (_lag_sample(arrival={"extended_for_visibility": False}), False),  # no extension proof
        (_lag_sample(intermediate_absent_verdicts=1), False),  # early RED recorded
    ],
)
def test_lag_oracle(sample, expected):
    assert T1.sample_oracle_match("T1-lag", sample) is expected


def _outage_sample(**patch):
    sample = {"observed": "inconclusive", "exit_code": 2, "junit_failures": 0}
    sample.update(patch)
    return sample


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (_outage_sample(), True),
        (_outage_sample(observed="absent"), False),  # infra blip demoted to RED
        (_outage_sample(exit_code=1), False),  # exit ladder violation
        (_outage_sample(junit_failures=1), False),  # JUnit rendered a failure
    ],
)
def test_outage_oracle(sample, expected):
    assert T1.sample_oracle_match("T1-outage", sample) is expected


def _restore_sample(**patch):
    sample = {
        "observed": "present",
        "negative": {"observed": "absent"},
        "chronology_hold": True,
        "restored": {"readback": {"required_rows": 1}},
    }
    sample.update(patch)
    return sample


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (_restore_sample(), True),
        (_restore_sample(observed="absent"), False),  # restore did not restore
        (_restore_sample(negative={"observed": "present"}), False),  # negative never bit
        (_restore_sample(chronology_hold=False), False),  # restored before the negative
        (_restore_sample(restored={"readback": {"required_rows": 0}}), False),  # no rows
    ],
)
def test_restore_oracle(sample, expected):
    assert T1.sample_oracle_match("T1-restore", sample) is expected


def test_unknown_scenario_oracle_is_loud():
    with pytest.raises(ValueError, match="unknown Tier-1 scenario"):
        T1.sample_oracle_match("T1-magic", {})


# ── metric rollups from synthetic rows ────────────────────────────────────────


def _rows(*, lag_false_reds: int = 0, outage_honest: bool = True, restored: bool = True):
    def loss_samples():
        return [
            {"observed": "absent", "oracle_match": True}
            for _ in range(3)
        ]

    lag = [{"observed": "present", "false_red": False, "oracle_match": True} for _ in range(3)]
    for index in range(lag_false_reds):
        lag[index] = {"observed": "absent", "false_red": True, "oracle_match": False}
    outage = [
        {
            "observed": "inconclusive" if outage_honest else "absent",
            "exit_code": 2 if outage_honest else 1,
            "junit_failures": 0,
            "oracle_match": outage_honest,
        }
    ]
    restore = [
        {
            "observed": "present" if restored else "absent",
            "negative": {"observed": "absent"},
            "chronology_hold": True,
            "oracle_match": restored,
        }
    ]
    rows = []
    for scenario_id, expected, samples in [
        ("T1-loss-drop", "absent", loss_samples()),
        ("T1-loss-401", "absent", loss_samples()),
        ("T1-lag", "present", lag),
        ("T1-outage", "inconclusive", outage),
        ("T1-restore", "present", restore),
    ]:
        rows.append(T1.scenario_rollup(scenario_id, expected, samples))
    return rows


def test_metrics_recompute_headline_rates_from_samples():
    metrics = T1.build_metrics(_rows())
    assert metrics["silent_loss_catch_rate"]["value"] == 1.0
    assert metrics["silent_loss_catch_rate"]["trials"] == 6  # both loss wings
    assert metrics["lag_false_red_rate"]["value"] == 0.0
    assert metrics["outage_inconclusive_honesty_rate"]["value"] == 1.0
    assert metrics["restore_rate"]["value"] == 1.0
    for metric in metrics.values():
        assert set(metric["wilson_95"]) == {"low", "high", "z", "confidence"}
        assert 0.0 <= metric["wilson_95"]["low"] <= metric["wilson_95"]["high"] <= 1.0


def test_metrics_and_pass_flag_track_a_false_red():
    rows = _rows(lag_false_reds=1)
    metrics = T1.build_metrics(rows)
    assert metrics["lag_false_red_rate"]["value"] == pytest.approx(1 / 3)
    assert T1.benchmark_passed(rows, metrics) is False
    clean = T1.build_metrics(_rows())
    assert T1.benchmark_passed(_rows(), clean) is True


# ── JUnit / Markdown projections ──────────────────────────────────────────────


def _synthetic_result(*, outage_observed: str = "inconclusive", outage_passed: bool = True):
    rows = []
    for declared in T1.SCENARIO_DEFS:
        if declared["id"] == "T1-outage":
            samples = [{"observed": outage_observed, "oracle_match": outage_passed}]
            rate = 1.0 if outage_passed else 0.0
        else:
            samples = [{"observed": declared["expected"], "oracle_match": True}]
            rate = 1.0
        rows.append(
            {
                "id": declared["id"],
                "expected": declared["expected"],
                "attempts": 1,
                "oracle_matches": 1 if rate == 1.0 else 0,
                "oracle_match_rate": rate,
                "pass_hat_k": {"k": 1, "value": 1.0},
                "samples": samples,
            }
        )
    return {
        "benchmark_id": T1.BENCHMARK_ID,
        "seed": 20260723,
        "tier": T1.TIER,
        "claim_boundary": T1.CLAIM_BOUNDARY,
        "passed": all(row["oracle_match_rate"] == 1.0 for row in rows),
        "scenarios": rows,
    }


def test_junit_outage_row_is_skipped_never_failure():
    result = _synthetic_result()
    root = ET.fromstring(T1.render_tier1_junit(result))
    assert root.get("failures") == "0"
    assert len(list(root.iter("failure"))) == 0
    outage = next(case for case in root.iter("testcase") if case.get("name") == "T1-outage")
    skipped = outage.find("skipped")
    assert skipped is not None
    assert "INCONCLUSIVE" in skipped.get("message")
    assert outage.find("failure") is None
    assert len(list(root.iter("testcase"))) == len(T1.SCENARIO_DEFS)


def test_junit_oracle_miss_is_a_real_failure_not_a_skip():
    result = _synthetic_result(outage_observed="absent", outage_passed=False)
    assert result["passed"] is False
    root = ET.fromstring(T1.render_tier1_junit(result))
    assert root.get("failures") == "1"
    outage = next(case for case in root.iter("testcase") if case.get("name") == "T1-outage")
    assert outage.find("failure") is not None
    assert outage.find("skipped") is None


def test_markdown_projection_names_tier_and_all_scenarios():
    markdown = T1.render_tier1_markdown(_synthetic_result())
    assert T1.TIER in markdown
    for declared in T1.SCENARIO_DEFS:
        assert declared["id"] in markdown
    assert "PASS" in markdown.splitlines()[0]


# ── measurement-lock validation ───────────────────────────────────────────────


def test_tier1_lock_validates_and_tier0_validator_still_rejects_it():
    lock = _tier1_lock()
    assert validate_tier1_measurement_lock(lock) is lock
    with pytest.raises(EvidenceIntegrityError, match="tier0-mechanics"):
        validate_measurement_lock(lock)


def test_tier1_validator_rejects_tier0_tier():
    with pytest.raises(EvidenceIntegrityError, match="tier1-external-store"):
        validate_tier1_measurement_lock(_tier1_lock(tier="tier0-mechanics"))


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"candidate_dirty": True}, "clean"),
        ({"candidate_git_head": "not-a-head"}, "git object id"),
        ({"candidate_git_head": "A" * 40}, "git object id"),
        ({"preregistration_sha256": "0" * 63}, "preregistration_sha256"),
        ({"seed": True}, "seed"),
        ({"repetitions": 0}, "repetitions"),
        ({"environment": {}}, "environment"),
        ({"registration_repository": " "}, "registration_repository"),
    ],
)
def test_tier1_lock_reject_paths(patch, message):
    with pytest.raises(EvidenceIntegrityError, match=message):
        validate_tier1_measurement_lock(_tier1_lock(**patch))


def test_binding_violations_are_pure_and_fail_loud():
    lock = _tier1_lock()
    good = dict(head="a" * 40, dirty=False, prereg_sha256="b" * 64)
    assert T1.binding_violations(lock, **good) == []
    assert "dirty worktree" in T1.binding_violations(lock, **{**good, "dirty": True})[0]
    assert "binding mismatch" in T1.binding_violations(lock, **{**good, "head": "c" * 40})[0]
    assert "preregistration hash" in T1.binding_violations(
        lock, **{**good, "prereg_sha256": "d" * 64}
    )[0]


def test_preflight_mismatches_name_the_drifted_hash():
    provenance = {
        "benchmark_definition_sha256": "1" * 64,
        "code_manifest_sha256": "2" * 64,
        "files": {
            "manifest": "3" * 64,
            "gate_spec": "4" * 64,
            "events": "5" * 64,
            "runner": "6" * 64,
        },
    }
    lock = _tier1_lock(
        benchmark_definition_sha256="1" * 64,
        code_manifest_sha256="2" * 64,
        manifest_sha256="3" * 64,
        gate_spec_sha256="4" * 64,
        events_sha256="5" * 64,
        runner_sha256="6" * 64,
        deepeval_spec_sha256="7" * 64,
    )
    assert T1.preflight_binding_mismatches(
        lock, provenance=provenance, deepeval_spec_sha256="7" * 64
    ) == {}
    drifted = T1.preflight_binding_mismatches(
        lock, provenance=provenance, deepeval_spec_sha256="8" * 64
    )
    assert set(drifted) == {"deepeval_spec_sha256"}
    assert drifted["deepeval_spec_sha256"]["locked"] == "7" * 64


# ── the delaying proxy (in-process upstream, real sockets, no live store) ─────


class _RecordingUpstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _record_and_answer(self):
        self.server.arrivals.append(time.monotonic())  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        self.server.bodies.append(self.rfile.read(length) if length else b"")  # type: ignore[attr-defined]
        payload = b'{"code":200}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._record_and_answer()

    def do_POST(self):
        self._record_and_answer()


@pytest.fixture
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingUpstream)
    server.daemon_threads = True
    server.arrivals = []  # type: ignore[attr-defined]
    server.bodies = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_delay_proxy_holds_ingest_posts_for_at_least_t(upstream):
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"
    hold_ms = 400
    with T1.DelayedIngestProxy(upstream_url, hold_ms) as proxy:
        started = time.monotonic()
        request = urllib.request.Request(
            f"{proxy.url}/api/default/mystream/_json",
            data=b'[{"event":"order.shipped"}]',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert json.loads(response.read().decode()) == {"code": 200}
        upstream_arrival = upstream.arrivals[0]
        assert upstream_arrival - started >= (hold_ms / 1000) * 0.9
        assert upstream.bodies[0] == b'[{"event":"order.shipped"}]'
        assert proxy.measured_holds_ms and proxy.measured_holds_ms[0] >= hold_ms * 0.9
        assert proxy.relayed_requests == 1


def test_delay_proxy_relays_non_ingest_requests_without_the_hold(upstream):
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"
    with T1.DelayedIngestProxy(upstream_url, 2000) as proxy:
        started = time.monotonic()
        with urllib.request.urlopen(f"{proxy.url}/healthz", timeout=10) as response:
            assert response.status == 200
        assert time.monotonic() - started < 2.0  # no hold applied outside /_json
        assert proxy.measured_holds_ms == []


def test_delay_proxy_rejects_non_http_urls():
    with pytest.raises(ValueError, match="http://host:port"):
        T1.DelayedIngestProxy("not-a-url", 100)


# ── CLI shape (dry-run only; the live measurement is a scheduled run) ─────────


def test_dry_run_skips_store_contact_and_prints_the_plan(capsys):
    exit_code = T1.main(["--dry-run", "--seed", "1", "--repetitions", "2"])
    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tier"] == "tier1-external-store"
    assert summary["reps"] == 10
    assert summary["unique_variants"] == 10
    assert summary["unique_streams"] == 10
    assert summary["lock"] == {"locked": False}


def test_lock_and_preregistration_must_come_in_pairs(capsys):
    exit_code = T1.main(["--dry-run", "--lock", "lock.json"])
    assert exit_code == 2
    assert "together" in capsys.readouterr().err


def test_live_run_requires_an_output_path():
    with pytest.raises(SystemExit):
        T1.main(["--seed", "1", "--repetitions", "1"])
