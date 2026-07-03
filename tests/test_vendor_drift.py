"""Vendor drift must catch a BEHIND vendor, not just a tampered one (audit gap-04).

The shipped drift template (scripts/templates/test_ooptdd_vendor_drift.py — the file
scripts/vendor_ooptdd.py copies into every consumer's _vendor/) checks the vendored copy
against a FROZEN manifest snapshot. That catches a local edit, but it is BEHIND-BLIND: a
vendor that still matches its own stale manifest stays green forever while canonical moves
on. That is exactly how the flagship consumer sat on a 0.3.0 snapshot, green, missing the
0.4.0 gate-honesty arc.

This drives the REAL shipped template against a synthetic consumer whose vendor matches its
manifest but LAGS a synthetic canonical, and asserts that (a) the manifest guards are indeed
blind to it and (b) the template ships a canonical-compare guard that catches it, skips
offline, and never false-positives in sync.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

TEMPLATE = (Path(__file__).resolve().parents[1]
            / "scripts" / "templates" / "test_ooptdd_vendor_drift.py")
_CANON_GUARD = "test_vendored_matches_canonical_when_present"


def _nsha(text: str) -> str:
    """Same normalized sha256 the template and vendor_ooptdd.py use."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return hashlib.sha256(("\n".join(lines).rstrip("\n") + "\n").encode()).hexdigest()


def _materialize(tmp_path: Path, vendor: dict[str, str], canonical: dict[str, str] | None):
    """Build a consumer _vendor/ (files + manifest matching them) and optionally a canonical
    checkout, then load the shipped template with its paths bound to this fixture."""
    vdir = tmp_path / "_vendor"
    (vdir / "ooptdd").mkdir(parents=True)
    files = {}
    for rel, text in vendor.items():
        f = vdir / "ooptdd" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        files[rel] = _nsha(text)          # manifest is a faithful snapshot of the vendor
    (vdir / "ooptdd_vendor_manifest.json").write_text(
        json.dumps({"ooptdd_version": "snap", "files": files}, indent=2, sort_keys=True) + "\n")

    canon_root = None
    if canonical is not None:
        canon_root = tmp_path / "canon"
        for rel, text in canonical.items():
            f = canon_root / "src" / "ooptdd" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)

    spec = importlib.util.spec_from_file_location("vendored_drift_under_test", TEMPLATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._VENDOR = vdir                                   # functions read these globals at call time
    mod._MANIFEST = vdir / "ooptdd_vendor_manifest.json"
    return mod, canon_root


def test_manifest_guards_are_behind_blind(tmp_path):
    """Characterization of the defect: a vendor that lags canonical but still matches its own
    manifest passes BOTH manifest guards — proving they cannot see 'canonical moved on'."""
    mod, _ = _materialize(
        tmp_path,
        vendor={"__init__.py": '__version__ = "0.3.0"\n'},
        canonical={"__init__.py": '__version__ = "0.4.0"\n'},   # canonical ahead
    )
    mod.test_vendored_ooptdd_matches_manifest()      # no raise: vendor == its own snapshot
    mod.test_vendored_tree_matches_manifest_set()    # no raise: file set unchanged


def test_template_ships_a_canonical_compare_guard(tmp_path):
    """The fix must exist in the file every consumer actually copies."""
    mod, _ = _materialize(tmp_path, vendor={"__init__.py": "x = 1\n"}, canonical=None)
    assert hasattr(mod, _CANON_GUARD), (
        f"shipped drift template has no {_CANON_GUARD} — new consumers are BEHIND-blind")


def test_canonical_guard_catches_a_behind_vendor(tmp_path, monkeypatch):
    mod, canon = _materialize(
        tmp_path,
        vendor={"__init__.py": '__version__ = "0.3.0"\n', "gate.py": "old = 1\n"},
        canonical={"__init__.py": '__version__ = "0.4.0"\n', "gate.py": "new = 2\n"},
    )
    monkeypatch.setenv("OOPTDD_CANONICAL", str(canon))
    with pytest.raises(AssertionError, match="(?i)behind"):
        getattr(mod, _CANON_GUARD)()


def test_canonical_guard_passes_when_in_sync(tmp_path, monkeypatch):
    """No false positive: vendor byte-equal to canonical must not raise."""
    same = {"__init__.py": '__version__ = "0.4.0"\n', "gate.py": "shared = 3\n"}
    mod, canon = _materialize(tmp_path, vendor=dict(same), canonical=dict(same))
    monkeypatch.setenv("OOPTDD_CANONICAL", str(canon))
    getattr(mod, _CANON_GUARD)()      # no raise


def test_canonical_guard_skips_offline(tmp_path, monkeypatch):
    """Offline (no reachable canonical) the guard skips — the manifest guards still cover
    local integrity, so an offline CI is honest, not falsely green or falsely red."""
    mod, _ = _materialize(tmp_path, vendor={"__init__.py": "x = 1\n"}, canonical=None)
    monkeypatch.delenv("OOPTDD_CANONICAL", raising=False)
    with pytest.raises(pytest.skip.Exception):
        getattr(mod, _CANON_GUARD)()
