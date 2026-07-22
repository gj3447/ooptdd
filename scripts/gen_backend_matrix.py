"""Generate docs/backends.md from the drivers' declared BackendCaps.

The backend capability matrix is a positioning document — and a hand-written
matrix would itself be an uncorroborated claim, the exact failure mode this
library exists to kill. So the table is DERIVED: each row comes from the
driver class's ``caps`` (or its legacy ``queryable`` attribute, synthesized the
same way the engine does), and ``tests/test_docs_backend_matrix.py`` pins the
committed file to this generator's output. Change a driver's caps without
regenerating -> RED.

Run:  python scripts/gen_backend_matrix.py [--check]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ooptdd.backends import _BUILTINS, _load  # noqa: E402
from ooptdd.domain.ports import BackendCaps  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "docs" / "backends.md"

HEADER = """\
# Backend capability matrix

<!-- GENERATED FILE — do not edit. Regenerate: python scripts/gen_backend_matrix.py -->
<!-- Pinned by tests/test_docs_backend_matrix.py: caps drift without regeneration is RED. -->

Every row below is derived from the driver's declared `BackendCaps` — the code,
not this document, is the authority.

**Reading the columns** — the load-bearing one is *external judge*
(`caps.independent`): can this backend be the independent store that proves
arrival?

- `memory` / `jsonl` prove **gate mechanics** (zero-infra, in-process /
  same-host file) — a green there says your spec and events agree, not that
  anything arrived anywhere.
- `openobserve` (reference), `clickhouse`, `signoz`, `victorialogs` prove
  **arrival**: an independent, queryable store the process under test cannot
  rewrite in memory.
- `otel` proves **portable writing** only — OTLP has no read side; pair it
  with a queryable store for verification.
"""

FOOTER = """\

Third-party drivers register via the `ooptdd.backends` entry-point group and
should declare their own `caps`; an undeclared driver is synthesized as
`queryable=True` from the legacy attribute (see `ooptdd.domain.ports.backend_caps`).
"""


def _caps_of(cls) -> BackendCaps:
    caps = getattr(cls, "caps", None)
    if isinstance(caps, BackendCaps):
        return caps
    queryable = getattr(cls, "queryable", True)
    return BackendCaps(queryable=queryable, write_only=not queryable)


def _first_doc_line(cls) -> str:
    doc = (cls.__doc__ or "").strip().splitlines()
    if not doc:  # class undocumented -> the driver module's first docstring line
        doc = (sys.modules[cls.__module__].__doc__ or "").strip().splitlines()
    return doc[0].rstrip(".") if doc else ""


def render() -> str:
    yn = {True: "yes", False: "no"}
    rows = []
    for name in sorted(_BUILTINS):
        cls = _load(_BUILTINS[name])
        c = _caps_of(cls)
        rows.append(
            f"| `{name}` | {yn[not c.write_only and c.queryable]} "
            f"| {yn[c.queryable]} | {yn[c.independent and c.queryable]} "
            f"| {yn[c.paginates]} | {yn[c.supports_where]} "
            f"| {_first_doc_line(cls)} |"
        )
    table = (
        "\n| backend | write+read | queryable | external judge | complete-read paging "
        "| server-side filter | driver |\n"
        "|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )
    return HEADER + table + FOOTER


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = DOC.read_text() if DOC.exists() else ""
        if current != text:
            print("docs/backends.md is stale — run: python scripts/gen_backend_matrix.py")
            return 1
        print("docs/backends.md is current")
        return 0
    DOC.write_text(text)
    print(f"wrote {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
