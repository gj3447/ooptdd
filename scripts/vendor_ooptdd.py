#!/usr/bin/env python3
"""Vendor the ooptdd core into a consumer repo + write a drift manifest.

Distribution model (see docs/MIGRATING_CONSUMERS.md): consumers do NOT pip-install
ooptdd. They vendor a copy of the small core into ``<consumer>/_vendor/ooptdd/``
and commit a manifest of normalized sha256s. A drift-check test (the template this
script also drops in) fails loudly if the vendored copy is edited away from the
manifest; re-running this script re-vendors from canonical and rewrites the
manifest, so a canonical change surfaces as a git diff.

Pure stdlib + cross-platform (the consumer_b field PC is Windows — no bash):

    python scripts/vendor_ooptdd.py /path/to/consumer
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src" / "ooptdd"


def vendor_files() -> list[str]:
    """Every ``.py`` under the core, as POSIX-relative paths — DERIVED from the tree, not a
    hand-maintained list. A hand list silently rots against a layout change: the 0.3.0
    engine/+domain/ split left the old flat list missing ``assertions.py`` and the whole
    ``engine/``+``domain/`` packages, so a re-vendor produced a copy that ``import ooptdd``
    could not even load. Walking the tree vendors the *whole* small core (the documented
    intent) and can never go out of sync with the real module layout. Sorted for a stable
    manifest/diff."""
    return sorted(
        p.relative_to(_SRC).as_posix()
        for p in _SRC.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def normalized_sha256(text: str) -> str:
    """Hash ignoring cosmetic diffs: strip trailing whitespace per line, \\n endings,
    single trailing newline. So a re-format that doesn't change content won't false-RED."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    norm = "\n".join(lines).rstrip("\n") + "\n"
    return hashlib.sha256(norm.encode()).hexdigest()


def _version() -> str:
    for ln in (_SRC / "__init__.py").read_text().splitlines():
        if ln.startswith("__version__"):
            return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return "0"


def vendor(consumer: Path) -> dict:
    dest = consumer / "_vendor" / "ooptdd"
    # Clean re-vendor: wipe the destination first so a module REMOVED upstream leaves no
    # orphan behind (an orphaned stale module would otherwise linger, fail the structural
    # drift guard, and — worse — still be importable). A from-scratch copy each run keeps the
    # vendored tree byte-identical to canonical.
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"ooptdd_version": _version(), "files": {}}
    for rel in vendor_files():
        src = _SRC / rel
        text = src.read_text()
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)  # engine/, domain/, backends/ …
        out.write_text(text)
        manifest["files"][rel] = normalized_sha256(text)
    (consumer / "_vendor" / "ooptdd_vendor_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    # drop the drift-check test next to the vendored package
    shutil.copyfile(_HERE / "templates" / "test_ooptdd_vendor_drift.py",
                    consumer / "_vendor" / "test_ooptdd_vendor_drift.py")
    return manifest


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: vendor_ooptdd.py <consumer-path>", file=sys.stderr)
        return 2
    consumer = Path(args[0]).resolve()
    if not consumer.is_dir():
        print(f"not a directory: {consumer}", file=sys.stderr)
        return 2
    m = vendor(consumer)
    print(f"vendored ooptdd {m['ooptdd_version']} ({len(m['files'])} files) "
          f"-> {consumer / '_vendor' / 'ooptdd'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
