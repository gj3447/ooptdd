"""Distribution honesty: typed marker present, version single-sourced, docs not lying (gap-01).

ooptdd is not published to any index — consumers install from source or vendor it — yet README
and the quickstart led with a bare ``pip install ooptdd`` that cannot work, and the package
shipped no ``py.typed`` marker so a consumer got no types. These guard the fixes and trip if
the docs regress to a bare install promise.
"""
import copy
import hashlib
import json
import re
from importlib import resources
from pathlib import Path

import pytest

from ooptdd.benchmark import (
    DEFAULT_FIXTURE_DIR,
    run_tier0_benchmark,
    tier0_provenance,
    validate_tier0_result,
)
from ooptdd.evidence_integrity import EvidenceIntegrityError

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "src" / "ooptdd"
_BENCHMARK_FIXTURE_HASHES = {
    "manifest.json": "fe577d73cea46e0b36aa24cb72d4cfce97dd1a7333981985f0f7339806ccfd72",
    "trajectory-events.json": "d4a34742112b0b293690ca97bc89801e3ecb6a6f814498d088a08d76d09bf0e3",
    "trajectory-gate.yaml": "5f194f18a4bec006c944226905eba2df552676b9a40f55fd28579d46dcd80edd",
}
_REQUIRED_LOAD_BEARING_MODULES = {
    "ooptdd/__init__.py",
    "ooptdd/benchmark.py",
    "ooptdd/backends/memory.py",
    "ooptdd/domain/model.py",
    "ooptdd/domain/ports.py",
    "ooptdd/engine/gate.py",
    "ooptdd/engine/monitor.py",
    "ooptdd/engine/trajectory.py",
    "ooptdd/engine/verify.py",
    "ooptdd/evidence_integrity.py",
    "ooptdd/mutation.py",
    "ooptdd/reports.py",
}


def _version_from(path: Path, key: str) -> str:
    m = re.search(rf'{key}\s*=\s*["\']([^"\']+)["\']', path.read_text())
    assert m, f"no {key} in {path}"
    return m.group(1)


def test_py_typed_marker_present_and_packaged():
    """PEP 561: ship the marker so a consumer of the installed/vendored package gets types."""
    assert (_PKG / "py.typed").exists(), "src/ooptdd/py.typed missing — consumers get no types"
    wheel_cfg = (_ROOT / "pyproject.toml").read_text()
    assert "src/ooptdd" in wheel_cfg, "wheel target must package src/ooptdd so py.typed ships"


def test_packaged_benchmark_fixtures_are_the_frozen_ssot():
    """Tier-0 must run from a wheel; root fixtures are only a development mirror."""
    assert DEFAULT_FIXTURE_DIR.is_dir()
    packaged = resources.files("ooptdd").joinpath("benchmark_fixtures", "arrival", "v0")
    mirror = _ROOT / "benchmarks" / "arrival" / "v0"

    for name, expected_hash in _BENCHMARK_FIXTURE_HASHES.items():
        packaged_bytes = packaged.joinpath(name).read_bytes()
        assert hashlib.sha256(packaged_bytes).hexdigest() == expected_hash
        assert (mirror / name).read_bytes() == packaged_bytes


def test_benchmark_provenance_binds_load_bearing_packaged_code():
    result = run_tier0_benchmark(repetitions=1)
    provenance = result["provenance"]
    assert tier0_provenance() == provenance
    code_manifest = provenance["code_manifest"]
    assert _REQUIRED_LOAD_BEARING_MODULES <= set(code_manifest)

    package = resources.files("ooptdd")
    source_python_files = {
        f"ooptdd/{path.relative_to(_PKG).as_posix()}"
        for path in _PKG.rglob("*.py")
    }
    assert set(code_manifest) == source_python_files
    for relative_path in sorted(source_python_files):
        resource = package
        for part in relative_path.removeprefix("ooptdd/").split("/"):
            resource = resource.joinpath(part)
        assert code_manifest[relative_path] == hashlib.sha256(resource.read_bytes()).hexdigest()

    code_manifest_bytes = (
        json.dumps(code_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    assert provenance["code_manifest_sha256"] == hashlib.sha256(code_manifest_bytes).hexdigest()

    definition_payload = {
        "code_manifest": code_manifest,
        "fixture_files": {
            key: provenance["files"][key]
            for key in ("manifest", "trajectory_events", "trajectory_gate")
        },
    }
    canonical = (
        json.dumps(definition_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    assert provenance["benchmark_definition_sha256"] == hashlib.sha256(canonical).hexdigest()

    forged = copy.deepcopy(result)
    forged["provenance"]["code_manifest"]["ooptdd/engine/verify.py"] = "0" * 64
    with pytest.raises(EvidenceIntegrityError, match="provenance"):
        validate_tier0_result(forged)

    forged_digest = copy.deepcopy(result)
    forged_digest["provenance"]["code_manifest_sha256"] = "0" * 64
    with pytest.raises(EvidenceIntegrityError, match="provenance"):
        validate_tier0_result(forged_digest)


def test_version_is_single_sourced():
    """pyproject and __init__ must agree — a duplicated, unchecked version silently skews."""
    pyproj = _version_from(_ROOT / "pyproject.toml", "version")
    dunder = _version_from(_PKG / "__init__.py", "__version__")
    assert pyproj == dunder, f"version skew: pyproject={pyproj} __init__={dunder}"


def test_docs_do_not_promise_a_bare_pip_install():
    """Doc-honesty tripwire: ooptdd is unpublished, so any `pip install ooptdd` line must be
    caveated (mention publishing) — a bare promise is false and must fail."""
    offenders = []
    for doc in (_ROOT / "README.md", _ROOT / "docs" / "quickstart.md"):
        for i, line in enumerate(doc.read_text().splitlines(), 1):
            if "pip install ooptdd" in line and "publish" not in line.lower():
                offenders.append(f"{doc.name}:{i}: {line.strip()}")
    assert not offenders, (
        "bare (uncaveated) `pip install ooptdd` promises:\n" + "\n".join(offenders))
