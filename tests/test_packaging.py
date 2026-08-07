"""Build and installation contracts for the base and optional distributions."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 lane
    import tomli as tomllib

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "src" / "ooptdd"


@dataclass(frozen=True)
class DistributionSpec:
    name: str
    project_dir: Path
    import_name: str
    console_script: str | None = None


_DISTRIBUTIONS = (
    DistributionSpec("ooptdd", _ROOT, "ooptdd", "ooptdd"),
    DistributionSpec(
        "ooptdd-mutation",
        _ROOT / "extensions" / "ooptdd-mutation",
        "ooptdd_mutation",
        "ooptdd-mutation",
    ),
    DistributionSpec(
        "ooptdd-trajectory",
        _ROOT / "extensions" / "ooptdd-trajectory",
        "ooptdd_trajectory",
    ),
    DistributionSpec(
        "ooptdd-genai",
        _ROOT / "extensions" / "ooptdd-genai",
        "ooptdd_genai",
    ),
)
_EXTENSION_NAMES = {spec.name for spec in _DISTRIBUTIONS[1:]}
_FORBIDDEN_ARCHIVE_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".uv-cache",
    ".venv",
    "__pycache__",
}


def _config(spec: DistributionSpec) -> dict:
    with (spec.project_dir / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _version_from(path: Path, key: str) -> str:
    match = re.search(rf'{key}\s*=\s*["\']([^"\']+)["\']', path.read_text())
    assert match, f"no {key} in {path}"
    return match.group(1)


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory):
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - CI and normal development provide uv
        pytest.skip("uv is required for distribution integration checks")

    root = tmp_path_factory.mktemp("distributions")
    artifacts = {}
    for spec in _DISTRIBUTIONS:
        output = root / spec.name
        subprocess.run(
            [uv, "build", "--out-dir", str(output), str(spec.project_dir)],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = tuple(output.glob("*.whl"))
        sdists = tuple(output.glob("*.tar.gz"))
        assert len(wheels) == 1, (spec.name, wheels)
        assert len(sdists) == 1, (spec.name, sdists)
        artifacts[spec.name] = {"wheel": wheels[0], "sdist": sdists[0]}
    return artifacts


def _assert_no_generated_files(names: set[str]) -> None:
    for name in names:
        parts = set(Path(name).parts)
        assert not parts & _FORBIDDEN_ARCHIVE_PARTS, name
        assert not name.endswith((".pyc", ".pyo")), name


def test_distribution_metadata_and_compatibility_bounds():
    base = _config(_DISTRIBUTIONS[0])["project"]
    assert base["name"] == "ooptdd"
    assert base["version"] == "0.6.0"

    expected_dependencies = {
        "ooptdd-mutation": {"ooptdd>=0.6,<0.7", "ooptdd-trajectory>=0.1,<0.2"},
        "ooptdd-trajectory": {"ooptdd>=0.6,<0.7"},
        "ooptdd-genai": {"ooptdd>=0.6,<0.7"},
    }
    for spec in _DISTRIBUTIONS:
        project = _config(spec)["project"]
        assert project["name"] == spec.name
        if spec.name in _EXTENSION_NAMES:
            assert project["version"] == "0.1.0"
            assert set(project["dependencies"]) == expected_dependencies[spec.name]


def test_wheels_have_disjoint_import_names_and_expected_entry_points(built_distributions):
    for spec in _DISTRIBUTIONS:
        wheel = built_distributions[spec.name]["wheel"]
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            _assert_no_generated_files(names)
            assert f"{spec.import_name}/__init__.py" in names
            assert f"{spec.import_name}/py.typed" in names

            other_imports = {
                candidate.import_name for candidate in _DISTRIBUTIONS if candidate != spec
            }
            assert not any(
                name.startswith(f"{other}/") for other in other_imports for name in names
            )

            entry_point_files = [name for name in names if name.endswith("entry_points.txt")]
            entry_points = archive.read(entry_point_files[0]).decode() if entry_point_files else ""
            if spec.console_script:
                assert f"{spec.console_script} = " in entry_points
            else:
                assert "[console_scripts]" not in entry_points

            if spec.name == "ooptdd":
                removed_base_members = {
                    "ooptdd/benchmark.py",
                    "ooptdd/evidence_integrity.py",
                    "ooptdd/mutation.py",
                }
                assert not names & removed_base_members
                assert not any(
                    name.startswith(("ooptdd/extensions/", "ooptdd/integrations/"))
                    for name in names
                )
            elif spec.name == "ooptdd-mutation":
                assert "ooptdd_mutation/benchmarks/arrival/v0/manifest.json" in names
                assert "ooptdd_mutation/benchmarks/tier0.py" in names


def test_sdists_are_clean_and_base_does_not_vendor_extensions(built_distributions):
    for spec in _DISTRIBUTIONS:
        sdist = built_distributions[spec.name]["sdist"]
        with tarfile.open(sdist, "r:gz") as archive:
            names = {member.name for member in archive.getmembers()}
            readiness_docs = tuple(
                name
                for name in names
                if name.endswith("/docs/architecture/ooptdd-ouroboros-readiness-harness.md")
            )
            readiness_text = (
                archive.extractfile(readiness_docs[0]).read().decode("utf-8")
                if readiness_docs
                else ""
            )
        _assert_no_generated_files(names)
        assert any(name.endswith(f"/{spec.import_name}/__init__.py") for name in names)

        if spec.name == "ooptdd":
            assert sdist.stat().st_size < 2_000_000, "base sdist contains repository/cache bloat"
            assert len(readiness_docs) == 1
            assert "Repository checkout only" in readiness_text
            assert not any(
                name.endswith("/scripts/check_ooptdd_ouroboros_readiness.py") for name in names
            )
            assert not any("/extensions/" in name for name in names)
            assert not any(
                f"/{extension.import_name}/" in name
                for extension in _DISTRIBUTIONS[1:]
                for name in names
            )


def test_all_wheels_install_and_run_in_one_clean_environment(built_distributions, tmp_path):
    environment = tmp_path / "clean-env"
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run([uv, "venv", str(environment)], check=True, capture_output=True, text=True)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    wheels = [str(built_distributions[spec.name]["wheel"]) for spec in _DISTRIBUTIONS]
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), *wheels],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    code = """
import importlib
import importlib.metadata

names = {
    "ooptdd": "ooptdd",
    "ooptdd-mutation": "ooptdd_mutation",
    "ooptdd-trajectory": "ooptdd_trajectory",
    "ooptdd-genai": "ooptdd_genai",
}
for distribution, module in names.items():
    importlib.import_module(module)
    assert importlib.metadata.version(distribution)
assert callable(importlib.import_module("ooptdd_mutation").ooptdd_checks)
assert callable(importlib.import_module("ooptdd_trajectory").ooptdd_checks)
benchmark = importlib.import_module("ooptdd_mutation.benchmarks.tier0")
assert benchmark.DEFAULT_FIXTURE_DIR.joinpath("manifest.json").is_file()
assert benchmark.tier0_provenance()["benchmark_definition_sha256"]
"""
    subprocess.run([str(python), "-I", "-c", code], cwd=tmp_path, check=True)
    mutation_cli = scripts / ("ooptdd-mutation.exe" if os.name == "nt" else "ooptdd-mutation")
    subprocess.run([str(mutation_cli), "--help"], cwd=tmp_path, check=True)


def test_distribution_identity_is_general_and_pytest_is_opt_in():
    project = _config(_DISTRIBUTIONS[0])["project"]
    identity = " ".join(
        [project["description"], *project.get("keywords", []), *project.get("classifiers", [])]
    ).lower()
    assert "event-contract" in identity
    assert "runtime-verification" in identity
    assert "pytest11" not in project.get("entry-points", {})
    assert project["optional-dependencies"]["pytest"] == ["pytest>=7.0"]


def test_base_wheel_target_and_type_marker():
    wheel = _config(_DISTRIBUTIONS[0])["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel == {"packages": ["src/ooptdd"]}
    assert (_PKG / "py.typed").is_file()


def test_version_is_single_sourced():
    pyproject_version = _config(_DISTRIBUTIONS[0])["project"]["version"]
    package_version = _version_from(_PKG / "__init__.py", "__version__")
    assert pyproject_version == package_version


def test_docs_do_not_promise_an_unavailable_bare_install():
    offenders = []
    for doc in (_ROOT / "README.md", _ROOT / "docs" / "quickstart.md"):
        for line_number, line in enumerate(doc.read_text().splitlines(), 1):
            if "pip install ooptdd" in line and "publish" not in line.lower():
                offenders.append(f"{doc.name}:{line_number}: {line.strip()}")
    assert not offenders, "bare install promises:\n" + "\n".join(offenders)
