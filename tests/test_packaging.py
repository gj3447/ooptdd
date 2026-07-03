"""Distribution honesty: typed marker present, version single-sourced, docs not lying (gap-01).

ooptdd is not published to any index — consumers install from source or vendor it — yet README
and the quickstart led with a bare ``pip install ooptdd`` that cannot work, and the package
shipped no ``py.typed`` marker so a consumer got no types. These guard the fixes and trip if
the docs regress to a bare install promise.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "src" / "ooptdd"


def _version_from(path: Path, key: str) -> str:
    m = re.search(rf'{key}\s*=\s*["\']([^"\']+)["\']', path.read_text())
    assert m, f"no {key} in {path}"
    return m.group(1)


def test_py_typed_marker_present_and_packaged():
    """PEP 561: ship the marker so a consumer of the installed/vendored package gets types."""
    assert (_PKG / "py.typed").exists(), "src/ooptdd/py.typed missing — consumers get no types"
    wheel_cfg = (_ROOT / "pyproject.toml").read_text()
    assert "src/ooptdd" in wheel_cfg, "wheel target must package src/ooptdd so py.typed ships"


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
