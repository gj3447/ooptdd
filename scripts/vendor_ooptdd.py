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

# The whole small core. Consumers import a subset; vendoring all keeps it simple
# and the drift-check covers every file.
VENDOR_FILES = [
    "__init__.py", "model.py", "verify.py", "config.py", "plugin.py", "cli.py",
    "gate.py", "ontology.py",
    "backends/__init__.py", "backends/base.py", "backends/memory.py",
    "backends/openobserve.py", "backends/otel.py",
]

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src" / "ooptdd"


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
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "backends").mkdir(exist_ok=True)
    manifest = {"ooptdd_version": _version(), "files": {}}
    for rel in VENDOR_FILES:
        src = _SRC / rel
        text = src.read_text()
        (dest / rel).write_text(text)
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
