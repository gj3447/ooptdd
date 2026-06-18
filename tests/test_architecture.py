"""Architecture fitness gate — the `codegraph` closure, embedded as a zero-dep check.

This is the import-cycle fitness function from the ``codegraph`` tool (strongly-connected
components over the module IMPORTS graph) vendored here as a self-contained, stdlib-only
test, so ooptdd's CI carries no external dependency. The engine-dissection tool's design
turned back on ooptdd itself: a reintroduced import cycle fails the build.

The metric is a *fact about the source* (deterministic ast parse), not an opinion — the
trustworthy gate the whole deterministic-extractor thesis argues for. ``codegraph`` is the
richer standalone tool; this is its CI-embeddable core (same metric).

Two tests, by design:
  1. ``test_cycle_detector_actually_detects_cycles`` — proves the detector CAN find a
     cycle. A gate that can only ever return "0 cycles" is green-and-blind; this guards it.
  2. ``test_ooptdd_has_no_import_cycles`` — the actual gate, baseline 0 (the
     ``semconv ⇄ ontology`` cycle was broken by inverting the preset dependency).
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _discover(src: Path) -> dict[str, Path]:
    """{module_qualname: path} for every .py under ``src`` (a package keyed by its dir)."""
    out: dict[str, Path] = {}
    for p in sorted(src.rglob("*.py")):
        parts = list(p.relative_to(src).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = p
    return out


def _resolve_relative(pkg: str, level: int, mod: str | None) -> str:
    """Resolve a relative import's anchor to an absolute module prefix."""
    anchor = pkg.split(".") if pkg else []
    strip = level - 1
    if strip > 0:
        anchor = anchor[: max(0, len(anchor) - strip)]
    base = ".".join(anchor)
    if mod:
        return f"{base}.{mod}" if base else mod
    return base


def _module_imports(path: Path, module: str, modules: set[str]) -> set[str]:
    """Intra-package modules that ``module`` imports (module->module edges only)."""
    is_init = path.name == "__init__.py"
    pkg = module if is_init else (module.rsplit(".", 1)[0] if "." in module else "")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()

    def record(name: str) -> None:
        # map a dotted import name to a known module (longest matching prefix)
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in modules and cand != module:
                targets.add(cand)
                return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(pkg, node.level, node.module)
                if node.module:  # `from .module import ...` — edge to that module;
                    record(base)  # NOT for bare `from . import x` (base is the package)
                for alias in node.names:
                    record(f"{base}.{alias.name}" if base else alias.name)
            elif node.module:
                record(node.module)
                for alias in node.names:
                    record(f"{node.module}.{alias.name}")
    return targets


def _import_cycles(src: Path) -> list[list[str]]:
    """Import cycles = SCCs (size > 1) of the module IMPORTS graph, fully sorted.

    Iterative Tarjan (the same algorithm codegraph.fitness uses)."""
    disc = _discover(src)
    modules = set(disc)
    adj = {m: sorted(_module_imports(p, m, modules)) for m, p in disc.items()}

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    sccs: list[list[str]] = []

    for root in sorted(adj):
        if root in index:
            continue
        work: list[list] = [[root, 0]]
        while work:
            frame = work[-1]
            v, i = frame[0], frame[1]
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            if i < len(adj[v]):
                frame[1] = i + 1
                w = adj[v][i]
                if w not in index:
                    work.append([w, 0])
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
            else:
                if low[v] == index[v]:
                    comp: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    sccs.append(sorted(comp))
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
    return sorted(c for c in sccs if len(c) > 1)


def test_cycle_detector_actually_detects_cycles(tmp_path):
    # guard against a broken (always-empty) detector — the green-and-blind failure mode
    pkg = tmp_path / "src" / "p"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from p.b import B\n\n\nclass A:\n    pass\n")
    (pkg / "b.py").write_text("from p.a import A\n\n\nclass B:\n    pass\n")
    assert _import_cycles(tmp_path / "src") == [["p.a", "p.b"]]


def test_ooptdd_has_no_import_cycles():
    cycles = _import_cycles(SRC)
    assert cycles == [], f"import cycle(s) reintroduced: {cycles}"
