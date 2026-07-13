#!/usr/bin/env python3
"""Sync consumer vendored copies to *committed* canonical (HEAD) — WIP-safe.

Unlike vendor_ooptdd.py (copies the working tree, so it bakes any uncommitted WIP)
and reconcile_consumers.py (also working-tree + rewrites tests), this reads every
core file from ``git show HEAD:...``. HEAD is the committed, stable state, so:

  * the committed SOLID-P2 "no silent green" fix (verify.py + backends) reaches
    every consumer NOW, and
  * files the concurrent worker has in-flight (gate.py/model.py/ontology.py WIP)
    are vendored at their *committed* value — i.e. exactly what consumers already
    have — so nothing in-flight is baked, and a later commit is a clean re-sync.

It preserves each consumer's existing convention (manifest path + hash mode) and
DOES NOT touch the drift-test files (leaves the concurrent worker's design alone).

    python scripts/sync_consumers_from_head.py            # dry-run
    python scripts/sync_consumers_from_head.py --apply
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

OOPTDD = Path(__file__).resolve().parent.parent

def vendor_files() -> list[str]:
    """Every .py under src/ooptdd at HEAD — auto-derived from git so a NEW canonical
    module (e.g. assertions.py / semconv.py added mid-sprint) can never be silently
    left out of the vendor set. All ooptdd modules are stdlib + intra-package at import
    time (no third-party), so vendoring the whole package is safe AND complete — which a
    hand-curated list is not (the byte-drift test is blind to a module it doesn't list)."""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "--", "src/ooptdd"],
        cwd=OOPTDD, capture_output=True, text=True, check=True).stdout
    pre = "src/ooptdd/"
    return sorted(ln[len(pre):] for ln in out.splitlines()
                  if ln.startswith(pre) and ln.endswith(".py"))

CONSUMERS = [
    {"name": "consumer-a",
     "vendor": Path("<WORKSPACE>/consumer-a/tests/_vendor/ooptdd"),
     "manifest": Path("<WORKSPACE>/consumer-a/tests/_vendor/ooptdd_manifest.json"),
     "hash": "raw"},        # consumer-a's test hashes raw bytes
    {"name": "consumer-b",
     "vendor": Path("<WORKSPACE>/consumer-b/_vendor/ooptdd"),
     "manifest": Path("<WORKSPACE>/consumer-b/_vendor/ooptdd_vendor_manifest.json"),
     "hash": "normalized"},
    {"name": "lakatotree",
     "vendor": Path("<WORKSPACE>/lakatotree/_vendor/ooptdd"),
     "manifest": Path("<WORKSPACE>/lakatotree/_vendor/ooptdd_vendor_manifest.json"),
     "hash": "normalized"},
]


def head_bytes(rel: str) -> bytes:
    return subprocess.run(["git", "show", f"HEAD:src/ooptdd/{rel}"],
                          cwd=OOPTDD, capture_output=True, check=True).stdout


def h(mode: str, data: bytes) -> str:
    if mode == "raw":
        return hashlib.sha256(data).hexdigest()
    text = data.decode()
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return hashlib.sha256(("\n".join(lines).rstrip("\n") + "\n").encode()).hexdigest()


def head_version() -> str:
    for ln in head_bytes("__init__.py").decode().splitlines():
        if ln.startswith("__version__"):
            return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return "0"


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    apply = "--apply" in args
    files = vendor_files()
    head_content = {rel: head_bytes(rel) for rel in files}
    print(f"sourcing from HEAD ({subprocess.run(['git','rev-parse','--short','HEAD'],cwd=OOPTDD,capture_output=True,text=True).stdout.strip()}), "
          f"mode={'APPLY' if apply else 'CHECK (no writes)'}\n")
    for c in CONSUMERS:
        vdir, mode = c["vendor"], c["hash"]
        if not vdir.parent.exists():
            print(f"  {c['name']:<11}: SKIP (consumer not found)"); continue
        changed = []
        for rel in files:
            vf = vdir / rel
            cur = vf.read_bytes() if vf.exists() else None
            if cur != head_content[rel]:
                changed.append(rel if cur is not None else rel + "(new)")
        # files removed upstream but still vendored locally must go (else stale module lingers)
        stale = sorted(str(p.relative_to(vdir)) for p in vdir.rglob("*.py")
                       if str(p.relative_to(vdir)) not in head_content)
        if apply and (changed or stale):
            manifest = {"ooptdd_version": head_version(), "files": {}}
            for rel in files:
                (vdir / rel).parent.mkdir(parents=True, exist_ok=True)
                (vdir / rel).write_bytes(head_content[rel])
                manifest["files"][rel] = h(mode, head_content[rel])
            for rel in stale:
                (vdir / rel).unlink()
            c["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            print(f"  {c['name']:<11}: synced {len(changed)} file(s)"
                  + (f", removed {len(stale)} stale" if stale else "") + f" <- HEAD  {changed}")
            continue
        if changed or stale:
            print(f"  {c['name']:<11}: would sync {len(changed)}" + (f" + remove {len(stale)} stale" if stale else "")
                  + f" file(s)  {changed}")
            continue
        print(f"  {c['name']:<11}: already == HEAD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
