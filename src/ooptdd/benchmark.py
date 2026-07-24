"""Deterministic, mechanics-only arrival benchmark.

The benchmark deliberately uses injected clocks and local fakes.  It proves that the
verdict kernel distinguishes loss, bounded visibility lag, late offenders, outages,
dependent stores, independent corroboration, and non-vacuous trajectory mutations.  It
does *not* claim that a record arrived in an external observability store; that is Tier 1.

Canonical JSON is the evidence root.  JUnit and Markdown are projections of the same
scenario-oracle rows and never re-judge them.
"""
from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict
from importlib import resources
from pathlib import Path

from . import __version__
from .domain.ports import BackendCaps, ProbeResult, QueryResult, backend_caps
from .engine.gate import evidence_tier, load_gate
from .engine.verify import verify_gate
from .evidence_integrity import EvidenceIntegrityError
from .mutation import mutation_report
from .reports import to_junit_xml, to_markdown

SCHEMA = "ooptdd-arrival-benchmark/v0"
TIER = "tier0-mechanics"
CLAIM_BOUNDARY = (
    "Deterministic gate-mechanics evidence only; not proof that evidence arrived in an "
    "independent external store. Tier-1 evidence is required for an arrival claim."
)
_ARRIVAL_KEYS = (
    "visibility_delay_ms",
    "waited_ms",
    "flushed",
    "extended_for_visibility",
    "confirm_rounds_run",
)
_FIXTURE_PARTS = ("benchmark_fixtures", "arrival", "v0")
# Source-root scripts need a concrete argparse default. Public benchmark functions use
# ``None`` instead, which resolves through importlib.resources and remains zip-safe.
DEFAULT_FIXTURE_DIR = Path(__file__).with_name("benchmark_fixtures") / "arrival" / "v0"


def _package_resource(*parts: str):
    root = resources.files("ooptdd")
    for part in parts:
        root = root.joinpath(part)
    return root


def _fixture_root(fixture_dir: Path | None):
    if fixture_dir is not None:
        return Path(fixture_dir)
    return _package_resource(*_FIXTURE_PARTS)


def _load_fixture_gate(path) -> dict:
    # Traversable resources may not have a stable filesystem path (for example when
    # imported from a zip). Materialize only for the path-based YAML loader.
    with resources.as_file(path) as materialized:
        return load_gate(str(materialized))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return _sha256_bytes(path.read_bytes())


def _python_resources(root, prefix: tuple[str, ...] = ()):
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        relative = (*prefix, child.name)
        if child.is_dir():
            yield from _python_resources(child, relative)
        elif child.name.endswith(".py"):
            yield relative, child


def _code_manifest() -> dict[str, str]:
    """Portable identity of all packaged Python code that can affect a Tier-0 run."""
    package = resources.files("ooptdd")
    return {
        f"ooptdd/{'/'.join(parts)}": sha256_file(resource)
        for parts, resource in _python_resources(package)
    }


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


class FixedClock:
    """Epoch-like deterministic clock; no wall time leaks into the artifact."""

    def __init__(self, now_us: int = 2_026_072_300_000_000):
        self.us = now_us

    def now_us(self) -> int:
        return self.us


class AdvancingSleeper:
    def __init__(self, clock: FixedClock):
        self.clock = clock

    def __call__(self, seconds: float) -> None:
        self.clock.us += int(seconds * 1_000_000)


class StaticBackend:
    default_lookback_s = 3600
    default_future_buffer_s = 300
    caps = BackendCaps(queryable=True, independent=False)

    def __init__(self, events: list[dict] | None = None, *, reachable: bool = True):
        self.events = list(events or [])
        self.reachable = reachable

    def identity(self) -> str:
        return "tier0:static-dependent"

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        del since_us, until_us
        events = [event for event in self.events if event.get("cid") == cid]
        return QueryResult(reachable=self.reachable, events=events)


class LagBackend(StaticBackend):
    def __init__(
        self,
        clock: FixedClock,
        events: list[dict],
        *,
        visible_after_us: int,
        visibility_ms: int,
    ):
        super().__init__(events)
        self.clock = clock
        self.visible_after_us = visible_after_us
        self.caps = BackendCaps(
            queryable=True,
            independent=False,
            query_visibility_delay_ms=visibility_ms,
        )
        self.flushes = 0

    def identity(self) -> str:
        return "tier0:lag-dependent"

    def force_flush(self) -> bool:
        self.flushes += 1
        return True

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        del since_us, until_us
        visible = self.events if self.clock.now_us() >= self.visible_after_us else []
        return QueryResult(
            reachable=True,
            events=[event for event in visible if event.get("cid") == cid],
        )


class LateOffenderBackend(StaticBackend):
    def __init__(self, *, offend_after_reads: int, required_event: str = "boot"):
        super().__init__()
        self.reads = 0
        self.offend_after_reads = offend_after_reads
        self.required_event = required_event

    def identity(self) -> str:
        return "tier0:late-offender-dependent"

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        del since_us, until_us
        self.reads += 1
        events = [{"cid": cid, "event": self.required_event, "_timestamp": 1}]
        if self.reads > self.offend_after_reads:
            events.append({"cid": cid, "event": "boom", "level": "ERROR", "_timestamp": 2})
        return QueryResult(reachable=True, events=events)


class IndependentProbe:
    def __init__(self, value: int = 42):
        self.value = value

    def probe(self, kind: str, selector: object, cid: str) -> ProbeResult:
        del kind, selector, cid
        return ProbeResult(
            reachable=True,
            value=self.value,
            separate_source=True,
            derived_identity="tier0:separate-ledger",
        )


def _event(cid: str, name: str = "boot", timestamp: int = 1) -> dict:
    return {"cid": cid, "event": name, "_timestamp": timestamp}


def _variant(seed: int, scenario_id: str, repeat: int) -> tuple[int, str]:
    digest = hashlib.sha256(f"{seed}:{scenario_id}:{repeat}".encode()).hexdigest()
    return int(digest[:16], 16), digest[:16]


def _caps_snapshot(backend) -> dict:
    return asdict(backend_caps(backend))


def _junit_failure_count(gate_result: dict) -> int:
    root = ET.fromstring(to_junit_xml(gate_result, suite="ooptdd.arrival.scenario"))
    return len(list(root.iter("failure")))


def _verification_sample(
    scenario_id: str,
    repeat: int,
    *,
    expected: str,
    backend,
    spec: dict,
    clock: FixedClock,
    retries: int = 1,
    delay: float = 0.1,
    confirm_rounds: int = 0,
    confirm_delay_s: float = 0.5,
    case_parameters: dict | None = None,
    probe=None,
) -> dict:
    cid = f"bench-{scenario_id}-{repeat:03d}"
    result = verify_gate(
        backend,
        cid,
        spec,
        retries=retries,
        delay=delay,
        confirm_rounds=confirm_rounds,
        confirm_delay_s=confirm_delay_s,
        clock=clock,
        sleeper=AdvancingSleeper(clock),
        probe=probe,
    )
    gate = result["gate"]
    arrival = {key: result["arrival"][key] for key in _ARRIVAL_KEYS}
    sample = {
        "repeat": repeat,
        "case_parameters": case_parameters or {},
        "expected": expected,
        "observed": result["verdict"],
        "oracle_match": result["verdict"] == expected,
        "arrival": arrival,
        "backend_caps": _caps_snapshot(backend),
        "dependent_store": bool(gate.get("dependent_store")),
        "evidence_tier": evidence_tier(gate),
        "junit_failures": _junit_failure_count(gate),
        "reachable": bool(gate.get("reachable")),
        "complete": bool(gate.get("complete", True)),
    }
    return sample


def _run_verification_scenario(
    scenario_id: str,
    repeat: int,
    *,
    seed: int,
    fault_injection: str | None,
) -> dict:
    clock = FixedClock()
    variant, variant_id = _variant(seed, scenario_id, repeat)
    required_event = f"boot.variant-{variant % 7}"
    base_parameters = {"variant_id": variant_id, "required_event": required_event}
    positive_spec = {"expect": [{"event": required_event, "op": ">=", "count": 1}]}
    forbid_spec = {
        "expect": [
            {"event": required_event, "op": ">=", "count": 1},
            {"absent": [{"where": {"level": "ERROR"}}]},
        ]
    }

    if scenario_id == "silent-loss":
        return _verification_sample(
            scenario_id,
            repeat,
            expected="absent",
            backend=StaticBackend([]),
            spec=positive_spec,
            clock=clock,
            case_parameters={**base_parameters, "suppressed": True},
        )
    if scenario_id == "lag-within-window":
        cid = f"bench-{scenario_id}-{repeat:03d}"
        lag_ms = 200 + variant % 750
        backend = LagBackend(
            clock,
            [_event(cid, required_event)],
            visible_after_us=clock.us + lag_ms * 1000,
            visibility_ms=1000,
        )
        return _verification_sample(
            scenario_id,
            repeat,
            expected="present",
            backend=backend,
            spec=positive_spec,
            clock=clock,
            case_parameters={**base_parameters, "lag_ms": lag_ms, "visibility_ms": 1000},
        )
    if scenario_id == "truly-absent":
        visibility_ms = 500 + variant % 501
        backend = LagBackend(
            clock,
            [],
            visible_after_us=clock.us + visibility_ms * 1000,
            visibility_ms=visibility_ms,
        )
        return _verification_sample(
            scenario_id,
            repeat,
            expected="absent",
            backend=backend,
            spec=positive_spec,
            clock=clock,
            retries=3,
            delay=1.0,
            case_parameters={**base_parameters, "visibility_ms": visibility_ms},
        )
    if scenario_id in {"late-offender-control", "late-offender-confirm"}:
        confirm_rounds = int(scenario_id == "late-offender-confirm")
        if scenario_id == "late-offender-confirm" and fault_injection == "disable-confirm-rounds":
            confirm_rounds = 0
        confirm_delay_ms = 100 + variant % 900
        return _verification_sample(
            scenario_id,
            repeat,
            expected="present" if scenario_id == "late-offender-control" else "absent",
            backend=LateOffenderBackend(
                offend_after_reads=1,
                required_event=required_event,
            ),
            spec=forbid_spec,
            clock=clock,
            confirm_rounds=confirm_rounds,
            confirm_delay_s=confirm_delay_ms / 1000,
            case_parameters={
                **base_parameters,
                "confirm_delay_ms": confirm_delay_ms,
                "configured_confirm_rounds": confirm_rounds,
            },
        )
    if scenario_id == "backend-outage":
        return _verification_sample(
            scenario_id,
            repeat,
            expected="inconclusive",
            backend=StaticBackend([], reachable=False),
            spec=positive_spec,
            clock=clock,
            case_parameters={**base_parameters, "reachable": False},
        )
    if scenario_id == "dependent-store-demotion":
        cid = f"bench-{scenario_id}-{repeat:03d}"
        return _verification_sample(
            scenario_id,
            repeat,
            expected="absent",
            backend=StaticBackend([_event(cid, required_event)]),
            spec={**positive_spec, "require_independent_store": True},
            clock=clock,
            case_parameters={**base_parameters, "backend_independent": False},
        )
    if scenario_id == "external-corroboration":
        cid = f"bench-{scenario_id}-{repeat:03d}"
        probe_value = 1 + variant % 1000
        spec = {
            "require_independent_store": True,
            "expect": [
                {"event": required_event, "op": ">=", "count": 1},
                {"external": {"kind": "db_row", "selector": {}, "want": probe_value}},
            ],
        }
        return _verification_sample(
            scenario_id,
            repeat,
            expected="present",
            backend=StaticBackend([_event(cid, required_event)]),
            spec=spec,
            clock=clock,
            case_parameters={**base_parameters, "probe_value": probe_value},
            probe=IndependentProbe(probe_value),
        )
    raise ValueError(f"unknown verification scenario {scenario_id!r}")


def _run_mutation_sample(
    repeat: int,
    *,
    seed: int,
    fixture_dir,
    expected: dict,
) -> dict:
    spec = _load_fixture_gate(fixture_dir.joinpath("trajectory-gate.yaml"))
    events = json.loads(
        fixture_dir.joinpath("trajectory-events.json").read_text(encoding="utf-8")
    )
    report = mutation_report(events, spec)
    measured = (
        report["baseline_green"]
        and report["score_status"] == "measured"
        and report["eligible"] == expected["eligible"]
        and report["score"] == expected["score"]
        and report["canary_survived"] is expected["canary_survived"]
        and not report["survivors"]
    )
    return {
        "repeat": repeat,
        "case_parameters": {
            "variant_id": _variant(seed, "trajectory-mutation", repeat)[1],
            "fixture_sha256": sha256_file(fixture_dir.joinpath("trajectory-events.json")),
        },
        "expected": "measured",
        "observed": "measured" if measured else "mismatch",
        "oracle_match": measured,
        "baseline_green": report["baseline_green"],
        "canary_survived": report["canary_survived"],
        "eligible": report["eligible"],
        "mutation_ids": [row["mutation_id"] for row in report["mutations"]],
        "operators": [row["operator"] for row in report["mutations"]],
        "score": report["score"],
        "score_status": report["score_status"],
        "status_counts": report["status_counts"],
        "survivors": report["survivors"],
    }


def pass_hat_k(successes: int, attempts: int, k: int) -> float:
    """tau-bench reliability estimator P(all k draws succeed), without replacement."""
    if attempts < 1 or k < 1 or k > attempts:
        raise ValueError("pass_hat_k needs attempts >= k >= 1")
    if successes < k:
        return 0.0
    return math.comb(successes, k) / math.comb(attempts, k)


def _scenario_rollup(scenario_id: str, expected: str, samples: list[dict]) -> dict:
    successes = sum(1 for sample in samples if sample["oracle_match"])
    attempts = len(samples)
    k = min(8, attempts)
    return {
        "id": scenario_id,
        "expected": expected,
        "attempts": attempts,
        "oracle_matches": successes,
        "oracle_match_rate": successes / attempts,
        "pass_hat_k": {"k": k, "value": pass_hat_k(successes, attempts, k)},
        "samples": samples,
    }


def _rate(samples: list[dict], predicate) -> float:
    return sum(1 for sample in samples if predicate(sample)) / len(samples)


def _metrics(rows: list[dict]) -> dict:
    by_id = {row["id"]: row for row in rows}
    silent = by_id["silent-loss"]["samples"]
    lag = by_id["lag-within-window"]["samples"]
    control = by_id["late-offender-control"]["samples"]
    confirm = by_id["late-offender-confirm"]["samples"]
    outage = by_id["backend-outage"]["samples"]
    mutation = by_id["trajectory-mutation"]["samples"][0]
    all_samples = [sample for row in rows for sample in row["samples"]]
    return {
        "M1_silent_loss_catch_rate": {
            "value": _rate(silent, lambda sample: sample["observed"] == "absent"),
            "target": 1.0,
            "status": "mechanics_only",
        },
        "M2a_false_red_rate": {
            "value": _rate(lag, lambda sample: sample["observed"] == "absent"),
            "target": 0.0,
        },
        "M2b_late_offender_catch_rate": {
            "value": _rate(confirm, lambda sample: sample["observed"] == "absent"),
            "target": 1.0,
            "control_miss_rate": _rate(
                control, lambda sample: sample["observed"] == "present"
            ),
        },
        "M3_inconclusive_honesty_rate": {
            "value": _rate(
                outage,
                lambda sample: sample["observed"] == "inconclusive"
                and sample["junit_failures"] == 0,
            ),
            "target": 1.0,
        },
        "M4_trajectory_mutation": {
            "eligible": mutation["eligible"],
            "score": mutation["score"],
            "score_status": mutation["score_status"],
            "canary_survived": mutation["canary_survived"],
            "survivors": mutation["survivors"],
        },
        "required_oracle_match_rate": {
            "value": _rate(all_samples, lambda sample: sample["oracle_match"]),
            "target": 1.0,
        },
    }


def _conformance(rows: list[dict]) -> dict:
    by_id = {row["id"]: row for row in rows}
    verification = [
        sample
        for row in rows
        if row["id"] != "trajectory-mutation"
        for sample in row["samples"]
    ]
    arrival_stamp_complete = all(
        set(_ARRIVAL_KEYS) == set(sample["arrival"]) for sample in verification
    )
    no_early_absent = all(
        not (
            sample["observed"] == "absent"
            and sample["reachable"]
            and sample["arrival"]["waited_ms"]
            < sample["arrival"]["visibility_delay_ms"]
        )
        for sample in verification
    )
    dependent = by_id["dependent-store-demotion"]["samples"]
    external = by_id["external-corroboration"]["samples"]
    return {
        "C1_arrival_stamp": {
            "passed": arrival_stamp_complete and no_early_absent,
            "arrival_stamp_complete": arrival_stamp_complete,
            "no_absent_inside_declared_blind_window": no_early_absent,
        },
        "C2_independence": {
            "passed": all(sample["dependent_store"] for sample in dependent)
            and all(sample["evidence_tier"] == "external_verdict" for sample in external),
            "dependent_store_demoted": all(sample["dependent_store"] for sample in dependent),
            "separate_probe_reaches_external_verdict": all(
                sample["evidence_tier"] == "external_verdict" for sample in external
            ),
        },
    }


def _provenance(fixture_dir, manifest: dict) -> dict:
    fixture_paths = {
        "manifest": fixture_dir.joinpath("manifest.json"),
        "trajectory_events": fixture_dir.joinpath("trajectory-events.json"),
        "trajectory_gate": fixture_dir.joinpath("trajectory-gate.yaml"),
    }
    fixture_hashes = {name: sha256_file(path) for name, path in fixture_paths.items()}
    code_manifest = _code_manifest()
    code_manifest_sha256 = _sha256_bytes(_canonical_bytes(code_manifest))
    hashes = {
        **fixture_hashes,
        # Backward-compatible projection for source-root measurement scripts.
        "runner": code_manifest["ooptdd/benchmark.py"],
    }
    scenario_ids = [item["id"] for item in manifest["scenarios"]]
    return {
        "benchmark_definition_sha256": _sha256_bytes(
            _canonical_bytes(
                {
                    "code_manifest": code_manifest,
                    "fixture_files": fixture_hashes,
                }
            )
        ),
        "code_manifest": code_manifest,
        "code_manifest_sha256": code_manifest_sha256,
        "dataset_sha256": _sha256_bytes(_canonical_bytes(fixture_hashes)),
        "files": hashes,
        "item_ids_sha256": _sha256_bytes(_canonical_bytes(scenario_ids)),
    }


def tier0_provenance(*, fixture_dir: Path | None = None) -> dict:
    """Return the portable code-and-fixture identity used by Tier-0 and lock preflights."""
    fixture_root = _fixture_root(fixture_dir)
    manifest = json.loads(
        fixture_root.joinpath("manifest.json").read_text(encoding="utf-8")
    )
    return _provenance(fixture_root, manifest)


def _benchmark_passed(rows: list[dict], metrics: dict, conformance: dict, manifest: dict) -> bool:
    return (
        all(row["oracle_match_rate"] == 1.0 for row in rows)
        and metrics["M1_silent_loss_catch_rate"]["value"] == 1.0
        and metrics["M2a_false_red_rate"]["value"] == 0.0
        and metrics["M2b_late_offender_catch_rate"]["value"] == 1.0
        and metrics["M3_inconclusive_honesty_rate"]["value"] == 1.0
        and metrics["M4_trajectory_mutation"]["eligible"] >= 1
        and metrics["M4_trajectory_mutation"]["score"] == manifest["mutation"]["score"]
        and not metrics["M4_trajectory_mutation"]["canary_survived"]
        and all(item["passed"] for item in conformance.values())
    )


def validate_tier0_result(
    result: dict,
    *,
    fixture_dir: Path | None = None,
) -> dict:
    """Recompute every rollup and file binding from raw scenario samples.

    The canonical JSON is allowed to cache aggregates, but no consumer needs to trust
    them. Any altered sample, summary, benchmark file hash, or top-level pass flag is
    rejected before the result can enter a LakatoTree evidence record.
    """
    if not isinstance(result, dict) or result.get("schema") != SCHEMA:
        raise EvidenceIntegrityError("invalid Tier-0 benchmark schema")
    fixture_dir = _fixture_root(fixture_dir)
    manifest = json.loads(
        fixture_dir.joinpath("manifest.json").read_text(encoding="utf-8")
    )
    if result.get("tier") != TIER or result.get("independent") is not False:
        raise EvidenceIntegrityError("Tier-0 result must remain mechanics-only and dependent")
    if result.get("provenance") != _provenance(fixture_dir, manifest):
        raise EvidenceIntegrityError("benchmark provenance does not match frozen files")

    rows = result.get("scenarios")
    if not isinstance(rows, list):
        raise EvidenceIntegrityError("scenarios must be a list")
    declared = manifest["scenarios"]
    if [row.get("id") for row in rows] != [item["id"] for item in declared]:
        raise EvidenceIntegrityError("scenario identities/order do not match the manifest")
    repetitions = result.get("repetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise EvidenceIntegrityError("repetitions must be a positive integer")
    seed = result.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise EvidenceIntegrityError("seed must be an integer")

    recomputed_rows = []
    for row, expected_decl in zip(rows, declared, strict=True):
        samples = row.get("samples")
        if not isinstance(samples, list) or len(samples) != repetitions:
            raise EvidenceIntegrityError(f"scenario {row.get('id')!r} sample count mismatch")
        if [sample.get("repeat") for sample in samples] != list(range(repetitions)):
            raise EvidenceIntegrityError(f"scenario {row.get('id')!r} repeat identities drifted")
        for sample in samples:
            expected_variant_id = _variant(seed, row["id"], sample.get("repeat"))[1]
            if (sample.get("case_parameters") or {}).get("variant_id") != expected_variant_id:
                raise EvidenceIntegrityError(
                    f"scenario {row.get('id')!r} variant is not seed-derived"
                )
            if sample.get("expected") != expected_decl["expected"]:
                raise EvidenceIntegrityError(
                    f"scenario {row.get('id')!r} sample expected oracle drifted"
                )
            should_match = sample.get("observed") == sample.get("expected")
            if sample.get("oracle_match") is not should_match:
                raise EvidenceIntegrityError(
                    f"scenario {row.get('id')!r} sample oracle_match is not observation-derived"
                )
            if row.get("id") == "trajectory-mutation":
                measured = (
                    sample.get("baseline_green") is True
                    and sample.get("score_status") == "measured"
                    and sample.get("eligible") == manifest["mutation"]["eligible"]
                    and sample.get("score") == manifest["mutation"]["score"]
                    and sample.get("canary_survived")
                    is manifest["mutation"]["canary_survived"]
                    and sample.get("survivors") == []
                    and sample.get("status_counts")
                    == {"killed": manifest["mutation"]["eligible"], "survived": 0}
                    and len(set(sample.get("mutation_ids") or []))
                    == manifest["mutation"]["eligible"]
                    and len(sample.get("operators") or [])
                    == manifest["mutation"]["eligible"]
                )
                if sample.get("observed") != ("measured" if measured else "mismatch"):
                    raise EvidenceIntegrityError(
                        "trajectory mutation observation is not raw-field-derived"
                    )
            elif (sample.get("backend_caps") or {}).get("independent") is not False:
                raise EvidenceIntegrityError(
                    f"Tier-0 scenario {row.get('id')!r} claimed an independent backend"
                )
        recomputed = _scenario_rollup(row["id"], expected_decl["expected"], samples)
        if row != recomputed:
            raise EvidenceIntegrityError(f"scenario {row['id']!r} rollup is not sample-derived")
        recomputed_rows.append(recomputed)

    metrics = _metrics(recomputed_rows)
    conformance = _conformance(recomputed_rows)
    if result.get("metrics") != metrics:
        raise EvidenceIntegrityError("stored benchmark metrics are not sample-derived")
    if result.get("conformance") != conformance:
        raise EvidenceIntegrityError("stored conformance is not sample-derived")
    passed = _benchmark_passed(recomputed_rows, metrics, conformance, manifest)
    if result.get("passed") is not passed:
        raise EvidenceIntegrityError("stored benchmark pass flag is not observation-derived")
    replayed = _execute_tier0_benchmark(
        fixture_dir=fixture_dir,
        seed=seed,
        repetitions=repetitions,
        fault_injection=result.get("fault_injection"),
    )
    if result != replayed:
        raise EvidenceIntegrityError(
            "benchmark artifact does not match an independent deterministic replay"
        )
    return {
        "metrics": metrics,
        "conformance": conformance,
        "passed": passed,
        "provenance": result["provenance"],
    }


def _execute_tier0_benchmark(
    *,
    fixture_dir: Path | None = None,
    seed: int | None = None,
    repetitions: int | None = None,
    fault_injection: str | None = None,
) -> dict:
    """Run the fixed Tier-0 battery and return timestamp-free canonical data."""
    fixture_dir = _fixture_root(fixture_dir)
    manifest = json.loads(
        fixture_dir.joinpath("manifest.json").read_text(encoding="utf-8")
    )
    seed = manifest["default_seed"] if seed is None else int(seed)
    repetitions = manifest["default_repetitions"] if repetitions is None else int(repetitions)
    if repetitions < 1:
        raise ValueError("repetitions must be >= 1")
    if fault_injection not in {None, "disable-confirm-rounds"}:
        raise ValueError(f"unknown fault injection {fault_injection!r}")

    rows = []
    for declared in manifest["scenarios"]:
        scenario_id = declared["id"]
        if scenario_id == "trajectory-mutation":
            samples = [
                _run_mutation_sample(
                    repeat,
                    seed=seed,
                    fixture_dir=fixture_dir,
                    expected=manifest["mutation"],
                )
                for repeat in range(repetitions)
            ]
        else:
            samples = [
                _run_verification_scenario(
                    scenario_id,
                    repeat,
                    seed=seed,
                    fault_injection=fault_injection,
                )
                for repeat in range(repetitions)
            ]
        rows.append(_scenario_rollup(scenario_id, declared["expected"], samples))

    metrics = _metrics(rows)
    conformance = _conformance(rows)
    passed = _benchmark_passed(rows, metrics, conformance, manifest)
    result = {
        "schema": SCHEMA,
        "benchmark_id": manifest["benchmark_id"],
        "benchmark_version": manifest["benchmark_version"],
        "fixture_version": manifest["fixture_version"],
        "ooptdd_version": __version__,
        "tier": TIER,
        "independent": False,
        "claim_boundary": CLAIM_BOUNDARY,
        "seed": seed,
        "repetitions": repetitions,
        "fault_injection": fault_injection,
        "provenance": _provenance(fixture_dir, manifest),
        "scenarios": rows,
        "metrics": metrics,
        "conformance": conformance,
        "passed": passed,
    }
    return result


def run_tier0_benchmark(
    *,
    fixture_dir: Path | None = None,
    seed: int | None = None,
    repetitions: int | None = None,
    fault_injection: str | None = None,
) -> dict:
    """Execute and independently replay-validate the fixed Tier-0 battery."""
    result = _execute_tier0_benchmark(
        fixture_dir=fixture_dir,
        seed=seed,
        repetitions=repetitions,
        fault_injection=fault_injection,
    )
    validate_tier0_result(result, fixture_dir=fixture_dir)
    return result


def benchmark_gate_result(result: dict) -> dict:
    """Honest scenario-oracle projection into the existing report renderer shape."""
    checks = [
        {
            "label": row["id"],
            "event": row["id"],
            "passed": row["oracle_match_rate"] == 1.0,
            "got": row["oracle_match_rate"],
            "want": 1.0,
            "observed": sorted({sample["observed"] for sample in row["samples"]}),
            "inconclusive": all(
                sample["observed"] == "inconclusive" for sample in row["samples"]
            ),
            "optional": False,
            "pending": False,
        }
        for row in result["scenarios"]
    ]
    checks.extend(
        {
            "label": name,
            "event": name,
            "passed": item["passed"],
            "optional": False,
            "pending": False,
        }
        for name, item in result["conformance"].items()
    )
    return {
        "cid": f"{result['benchmark_id']}-{result['fixture_version']}",
        "ok": result["passed"],
        "reachable": True,
        "complete": True,
        "checks": checks,
        "oracle": {"emit_identity": TIER},
    }


def render_benchmark_junit(result: dict) -> str:
    return to_junit_xml(benchmark_gate_result(result), suite="ooptdd.arrival-benchmark")


def render_benchmark_markdown(result: dict) -> str:
    header = (
        f"# ooptdd arrival benchmark — {'PASS' if result['passed'] else 'FAIL'}\n\n"
        f"> {result['claim_boundary']}\n\n"
    )
    return header + to_markdown(benchmark_gate_result(result))


def canonical_json(result: dict) -> str:
    return _canonical_bytes(result).decode()
