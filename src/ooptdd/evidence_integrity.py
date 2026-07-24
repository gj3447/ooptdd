"""Observation-first validation for benchmark and LakatoTree evidence.

Aggregate fields are caches, not roots of truth.  This module recomputes them from
raw observation rows, validates source/spec bindings, and rejects duplicate samples or
post-hoc chronology.  It intentionally contains no verdict logic: evidence is validated
here and judged elsewhere.

The design absorbs Inspect AI's rescore-from-log boundary and the test-oracle rule that
the producer's summary must never certify itself.  It is dependency-free so CI and an
external judge can replay it without importing DeepEval, Phoenix, or LakatoTree.
"""
from __future__ import annotations

import copy
import hashlib
import math
import platform
import subprocess
import sys
from collections.abc import Callable, Iterable
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any


class EvidenceIntegrityError(ValueError):
    """The supplied evidence cannot support the aggregate claim."""


_LOCK_HASH_FIELDS = (
    "preregistration_sha256",
    "benchmark_definition_sha256",
    "code_manifest_sha256",
    "manifest_sha256",
    "gate_spec_sha256",
    "events_sha256",
    "runner_sha256",
    "deepeval_spec_sha256",
)


def measurement_environment() -> dict:
    """Exact runtime identity for confirmatory byte-replay claims."""
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "pyyaml_version": version("PyYAML"),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "byteorder": sys.byteorder,
    }


def _validate_lock(lock: Any, *, tier: str) -> dict:
    """Shared measurement-lock shape validation; only the accepted tier differs."""
    if not isinstance(lock, dict) or lock.get("schema") != "ooptdd-efficacy-measurement-lock/v1":
        raise EvidenceIntegrityError("measurement lock schema mismatch")
    if lock.get("candidate_dirty") is not False:
        raise EvidenceIntegrityError("measurement lock must bind a clean candidate")
    head = lock.get("candidate_git_head")
    if (
        not isinstance(head, str)
        or len(head) not in {40, 64}
        or any(char not in "0123456789abcdef" for char in head)
    ):
        raise EvidenceIntegrityError("candidate_git_head must be a lowercase git object id")
    seed = lock.get("seed")
    repetitions = lock.get("repetitions")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise EvidenceIntegrityError("measurement seed must be a non-negative integer")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise EvidenceIntegrityError("measurement repetitions must be a positive integer")
    if lock.get("tier") != tier:
        raise EvidenceIntegrityError(f"measurement tier must be {tier}")
    if not isinstance(lock.get("deepeval_version"), str) or not lock["deepeval_version"]:
        raise EvidenceIntegrityError("deepeval_version must be a non-empty string")
    if (
        not isinstance(lock.get("registration_repository"), str)
        or not lock["registration_repository"].strip()
    ):
        raise EvidenceIntegrityError("registration_repository must be a non-empty string")
    environment = lock.get("environment")
    expected_environment_keys = set(measurement_environment())
    if (
        not isinstance(environment, dict)
        or set(environment) != expected_environment_keys
        or not all(isinstance(value, str) and value for value in environment.values())
    ):
        raise EvidenceIntegrityError("measurement environment identity is incomplete")
    for field in _LOCK_HASH_FIELDS:
        value = lock.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise EvidenceIntegrityError(f"measurement lock {field} must be lowercase SHA-256")
    return lock


def validate_measurement_lock(lock: Any) -> dict:
    """Validate exact lock types before Python's bool/int equality can weaken a binding."""
    return _validate_lock(lock, tier="tier0-mechanics")


def validate_tier1_measurement_lock(lock: Any) -> dict:
    """Validate a Tier-1 (external-store) measurement lock.

    Identical bindings to :func:`validate_measurement_lock` — clean candidate, exact
    head/seed/repetition types, complete environment identity, lowercase SHA-256 hash
    fields, named registration repository — except the tier slot, which must read
    ``tier1-external-store``. A separate entry point on purpose: the Tier-0 validator is
    not weakened to accept Tier-1 evidence, and Tier-1 cannot smuggle in a mechanics-only
    claim.
    """
    return _validate_lock(lock, tier="tier1-external-store")


def _repository_identity(value: str) -> str:
    return value.strip().removesuffix("/").removesuffix(".git")


def validate_registration_repository(lock: dict, preregistration: dict, receipt: dict) -> None:
    """Bind the published lock to the repository named before the candidate measurement."""
    sources = preregistration.get("sources")
    preregistered = sources.get("lakatotree_repository") if isinstance(sources, dict) else None
    actual = receipt.get("repository")
    locked = lock.get("registration_repository")
    named_repositories = (preregistered, actual, locked)
    if not all(isinstance(value, str) and value.strip() for value in named_repositories):
        raise EvidenceIntegrityError(
            "lock, preregistration, and git receipt must name the registration repository"
        )
    identities = {_repository_identity(value) for value in (preregistered, actual, locked)}
    if len(identities) != 1:
        raise EvidenceIntegrityError("registration repository does not match preregistered source")


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceIntegrityError(f"{field} must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceIntegrityError(f"{field} is not valid ISO-8601: {value!r}") from exc


def _unique_rows(observations: Any, keys: tuple[str, ...]) -> list[dict]:
    if not isinstance(observations, list) or not observations:
        raise EvidenceIntegrityError("observations must be a non-empty list")
    rows: list[dict] = []
    seen: set[tuple[Any, ...]] = set()
    for index, row in enumerate(observations):
        if not isinstance(row, dict):
            raise EvidenceIntegrityError(f"observation[{index}] must be an object")
        identity = tuple(row.get(key) for key in keys)
        if any(part is None for part in identity):
            raise EvidenceIntegrityError(
                f"observation[{index}] lacks stable identity fields {keys}"
            )
        if identity in seen:
            raise EvidenceIntegrityError(f"duplicate observation identity: {identity!r}")
        seen.add(identity)
        if not isinstance(row.get("matched"), bool):
            raise EvidenceIntegrityError(
                f"observation {identity!r} matched must be boolean"
            )
        rows.append(row)
    return rows


def trajectory_metrics(observations: Any, *, gap_groups: Iterable[str]) -> dict:
    """Recompute the historical trajectory qualification aggregates from rows."""
    rows = _unique_rows(observations, ("group", "name"))
    for row in rows:
        expected = row.get("expected_ok")
        observed = row.get("observed_ok")
        if not isinstance(expected, bool) or not isinstance(observed, bool):
            raise EvidenceIntegrityError(
                "trajectory observations require boolean expected_ok and observed_ok"
            )
        derived = observed is expected
        if row["matched"] is not derived:
            raise EvidenceIntegrityError(
                f"trajectory observation {(row['group'], row['name'])!r} matched is producer-"
                f"authored: {row['matched']!r} != derived {derived!r}"
            )
    gap_set = {str(group) for group in gap_groups}
    observed_groups = {str(row["group"]) for row in rows}
    missing_groups = sorted(gap_set - observed_groups)
    failed_groups = sorted(
        {str(row["group"]) for row in rows if not row["matched"]} | set(missing_groups)
    )
    unsafe = [row for row in rows if bool(row.get("unsafe"))]
    readback = [row for row in rows if "readback_count" in row]
    for row in readback:
        value = row["readback_count"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EvidenceIntegrityError("readback_count must be a non-negative integer")
    return {
        "unresolved_mechanism_groups": len(set(failed_groups) & gap_set),
        "unsafe_counterexample_detection_rate": (
            sum(bool(row["matched"]) for row in unsafe) / len(unsafe) if unsafe else 0.0
        ),
        "failed_groups": failed_groups,
        "missing_groups": missing_groups,
        "cases_total": len(rows),
        "cases_matched": sum(bool(row["matched"]) for row in rows),
        "memory_readback_cases": len(readback),
        "memory_readback_nonempty": sum(row["readback_count"] > 0 for row in readback),
    }


def deepeval_metrics(observations: Any) -> dict:
    """Recompute deterministic safe/dangerous/corrupt oracle agreement.

    The legacy artifact called this a pass rate even though dangerous and corrupt cases
    are expected to fail the metric.  Preserve that key for validation compatibility and
    expose the accurate name for new evidence.
    """
    rows = _unique_rows(observations, ("name",))
    derived_matches = []
    for row in rows:
        expected_score = row.get("expected_score")
        observed_score = row.get("observed_score")
        expected_success = row.get("expected_success")
        observed_success = row.get("observed_success")
        if (
            isinstance(expected_score, bool)
            or not isinstance(expected_score, (int, float))
            or not math.isfinite(float(expected_score))
            or isinstance(observed_score, bool)
            or not isinstance(observed_score, (int, float))
            or not math.isfinite(float(observed_score))
            or not isinstance(expected_success, bool)
            or not isinstance(observed_success, bool)
        ):
            raise EvidenceIntegrityError(
                "DeepEval observations require finite expected/observed scores and bool success"
            )
        derived = (
            float(observed_score) == float(expected_score)
            and observed_success is expected_success
        )
        if row["matched"] is not derived:
            raise EvidenceIntegrityError(
                f"DeepEval observation {row['name']!r} matched is producer-authored: "
                f"{row['matched']!r} != derived {derived!r}"
            )
        derived_matches.append(derived)
    matched = sum(derived_matches)
    rate = matched / len(rows)
    return {
        "deepeval_oracle_agreement_rate": rate,
        "actual_deepeval_trajectory_pass_rate": rate,
        "cases_total": len(rows),
        "cases_matched": matched,
        "actual_successes": sum(row.get("observed_success") is True for row in rows),
    }


def validate_measurement(
    record: dict,
    *,
    recompute: Callable[[Any], dict],
    expected_head: str,
    expected_spec_sha256: str,
    expected_fault_injected: bool | None = None,
    require_clean: bool = True,
) -> dict:
    """Validate source/spec identity and compare stored aggregates with recomputation."""
    if not isinstance(record, dict):
        raise EvidenceIntegrityError("measurement must be an object")
    _time(record.get("measured_at"), "measured_at")
    source = record.get("source")
    spec = record.get("spec")
    stored = record.get("metrics")
    if not isinstance(source, dict) or not isinstance(spec, dict) or not isinstance(stored, dict):
        raise EvidenceIntegrityError("measurement requires source, spec, and metrics objects")
    if source.get("git_head") != expected_head:
        raise EvidenceIntegrityError(
            f"source head mismatch: {source.get('git_head')!r} != {expected_head!r}"
        )
    if require_clean and source.get("dirty") is not False:
        raise EvidenceIntegrityError("source worktree was dirty at measurement time")
    if spec.get("sha256") != expected_spec_sha256:
        raise EvidenceIntegrityError(
            f"spec hash mismatch: {spec.get('sha256')!r} != {expected_spec_sha256!r}"
        )
    if expected_fault_injected is not None and (
        record.get("fault_injected") is not expected_fault_injected
    ):
        raise EvidenceIntegrityError("fault_injected mode does not match the locked role")

    computed = recompute(record.get("observations"))
    for key, value in computed.items():
        # New, more accurate aliases need not exist in a legacy artifact; every stored key
        # that claims the same aggregate must agree exactly.
        if key not in stored:
            continue
        if stored[key] != value:
            raise EvidenceIntegrityError(
                f"stored metric {key!r} is not observation-derived: "
                f"{stored[key]!r} != {value!r}"
            )
    required = {key for key in computed if key in stored}
    if not required:
        raise EvidenceIntegrityError("measurement stores none of the recomputable metrics")
    return computed


def validate_deepeval_measurement(
    record: dict,
    *,
    expected_head: str,
    expected_spec_sha256: str,
    expected_version: str,
) -> dict:
    """Validate a real DeepEval safe/dangerous/corrupt artifact from raw rows."""
    environment = record.get("environment") or {}
    if environment.get("deepeval") != expected_version:
        raise EvidenceIntegrityError(
            f"DeepEval version mismatch: {environment.get('deepeval')!r} "
            f"!= {expected_version!r}"
        )
    expected_oracles = {
        "safe": (1.0, True),
        "destructive": (0.0, False),
        "corrupt": (0.0, False),
    }
    observations = record.get("observations")
    if not isinstance(observations, list):
        raise EvidenceIntegrityError("DeepEval observations must be a list")
    observed_oracles = {
        row.get("name"): (row.get("expected_score"), row.get("expected_success"))
        for row in observations
        if isinstance(row, dict)
    }
    if observed_oracles != expected_oracles or len(observations) != len(expected_oracles):
        raise EvidenceIntegrityError(
            "DeepEval safe/destructive/corrupt oracle identities or expected values drifted"
        )
    failed_evaluations = [
        row.get("name") for row in observations
        if isinstance(row, dict) and row.get("error") is not None
    ]
    if failed_evaluations:
        raise EvidenceIntegrityError(
            f"DeepEval observations contain evaluation errors: {failed_evaluations}"
        )
    computed = validate_measurement(
        record,
        recompute=deepeval_metrics,
        expected_head=expected_head,
        expected_spec_sha256=expected_spec_sha256,
    )
    if computed["cases_total"] != 3:
        raise EvidenceIntegrityError(
            f"expected safe/dangerous/corrupt trio, got {computed['cases_total']} cases"
        )
    if computed["deepeval_oracle_agreement_rate"] != 1.0:
        raise EvidenceIntegrityError("DeepEval probe did not agree with every frozen oracle")
    return computed


def expected_deepeval_mismatch(record: dict) -> dict:
    """Return the one preregistered DeepEval negative-control transformation.

    The negative is not "any artifact that validation rejects". It must preserve every
    candidate byte-level field except the safe case's observed success bit. This keeps a
    malformed file, wrong source head, or version drift from impersonating the intended
    oracle-mismatch control.
    """
    if not isinstance(record, dict):
        raise EvidenceIntegrityError("DeepEval candidate must be an object")
    transformed = copy.deepcopy(record)
    observations = transformed.get("observations")
    if not isinstance(observations, list):
        raise EvidenceIntegrityError("DeepEval observations must be a list")
    safe = [row for row in observations if isinstance(row, dict) and row.get("name") == "safe"]
    if len(safe) != 1 or safe[0].get("observed_success") is not True:
        raise EvidenceIntegrityError(
            "DeepEval candidate must contain one successful safe observation"
        )
    safe[0]["observed_success"] = False
    return transformed


def validate_deepeval_mismatch(candidate: dict, negative: dict) -> None:
    """Require exact equality with the frozen one-field DeepEval transform."""
    if negative != expected_deepeval_mismatch(candidate):
        raise EvidenceIntegrityError(
            "DeepEval negative is not the exact safe.observed_success mismatch transform"
        )


def validate_chronology(registered_at: str, *records: dict) -> None:
    """Require registration < negative/control runs < restored positive in supplied order."""
    previous = _time(registered_at, "registered_at")
    for index, record in enumerate(records):
        measured = _time(record.get("measured_at"), f"records[{index}].measured_at")
        if measured <= previous:
            raise EvidenceIntegrityError(
                "measurement chronology is not strictly increasing after registration"
            )
        previous = measured


def validate_file_binding(path: str | Path, expected_sha256: str) -> None:
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise EvidenceIntegrityError(
            f"file hash mismatch for {Path(path)}: {actual} != {expected_sha256}"
        )


def _git(root: Path, *args: str, binary: bool = False):
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=not binary,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvidenceIntegrityError(f"git provenance check failed: {' '.join(args)}") from exc
    return completed.stdout if binary else completed.stdout.strip()


def _committed_file_receipt(path: Path, repository_root: Path, remote_heads: dict) -> dict:
    relative = path.resolve().relative_to(repository_root).as_posix()
    if _git(repository_root, "status", "--porcelain", "--", relative):
        raise EvidenceIntegrityError(f"prospective input is not clean in git: {relative}")
    commit = _git(repository_root, "log", "-1", "--format=%H", "--", relative)
    if not commit:
        raise EvidenceIntegrityError(f"prospective input has no containing commit: {relative}")
    committed = _git(repository_root, "show", f"{commit}:{relative}", binary=True)
    current = path.read_bytes()
    if committed != current:
        raise EvidenceIntegrityError(f"prospective input differs from committed blob: {relative}")
    published_refs = []
    for ref, head in sorted(remote_heads.items()):
        ancestor = subprocess.run(
            ["git", "-C", str(repository_root), "merge-base", "--is-ancestor", commit, head],
            capture_output=True,
        )
        if ancestor.returncode == 0:
            published_refs.append(ref)
    if not published_refs:
        raise EvidenceIntegrityError(
            f"prospective input commit is not published on a live origin branch: {relative}"
        )
    return {
        "path": relative,
        "commit": commit,
        "sha256": hashlib.sha256(current).hexdigest(),
        "published_refs": published_refs,
    }


def prospective_git_receipt(lock_path: str | Path, preregistration_path: str | Path) -> dict:
    """Prove the lock and preregistration existed as published git objects before a run.

    A file hash in a post-hoc sequence is insufficient: the same file could have been
    authored after seeing the result. This receipt binds both exact blobs to commits,
    verifies preregistration ancestry, and checks those commits against live ``origin``
    branch heads (``git ls-remote``), not merely a mutable local claim.
    """
    lock = Path(lock_path).resolve()
    prereg = Path(preregistration_path).resolve()
    lock_root = Path(_git(lock.parent, "rev-parse", "--show-toplevel")).resolve()
    prereg_root = Path(_git(prereg.parent, "rev-parse", "--show-toplevel")).resolve()
    if lock_root != prereg_root:
        raise EvidenceIntegrityError("lock and preregistration must share one git repository")
    origin = _git(lock_root, "remote", "get-url", "origin")
    raw_heads = _git(lock_root, "ls-remote", "--heads", "origin")
    remote_heads = {}
    for line in raw_heads.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            remote_heads[parts[1]] = parts[0]
    if not remote_heads:
        raise EvidenceIntegrityError("origin exposes no live branch heads for prospective proof")
    prereg_receipt = _committed_file_receipt(prereg, lock_root, remote_heads)
    lock_receipt = _committed_file_receipt(lock, lock_root, remote_heads)
    ancestry = subprocess.run(
        [
            "git", "-C", str(lock_root), "merge-base", "--is-ancestor",
            prereg_receipt["commit"], lock_receipt["commit"],
        ],
        capture_output=True,
    )
    if ancestry.returncode != 0:
        raise EvidenceIntegrityError("preregistration commit is not an ancestor of lock commit")
    return {
        "schema": "ooptdd-prospective-git-receipt/v1",
        "repository": origin,
        "preregistration": prereg_receipt,
        "measurement_lock": lock_receipt,
        "preregistration_is_ancestor": True,
    }
