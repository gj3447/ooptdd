"""Drift check for the vendored ooptdd core (copied in by scripts/vendor_ooptdd.py).

RED the moment a vendored file is edited away from the committed manifest. To sync
with upstream, re-run ``python <ooptdd>/scripts/vendor_ooptdd.py <this-repo>`` —
the manifest changes show up as a git diff to review. Pure stdlib, offline.
"""
import hashlib
import json
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent           # …/_vendor
_MANIFEST = _VENDOR / "ooptdd_vendor_manifest.json"


def _normalized_sha256(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return hashlib.sha256(("\n".join(lines).rstrip("\n") + "\n").encode()).hexdigest()


def test_vendored_ooptdd_matches_manifest():
    manifest = json.loads(_MANIFEST.read_text())
    drifted = []
    for rel, want in manifest["files"].items():
        path = _VENDOR / "ooptdd" / rel
        assert path.exists(), f"vendored file missing: {rel} (re-vendor)"
        got = _normalized_sha256(path.read_text())
        if got != want:
            drifted.append(rel)
    assert not drifted, (
        f"vendored ooptdd drifted from manifest: {drifted}. "
        "Someone edited the vendored copy directly. Re-vendor from canonical: "
        "python <ooptdd>/scripts/vendor_ooptdd.py <this-repo>"
    )
