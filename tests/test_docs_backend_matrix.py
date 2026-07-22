"""The backend capability matrix must be code-derived, never hand-drifted.

docs/backends.md is generated from each driver's declared BackendCaps
(scripts/gen_backend_matrix.py). This test regenerates and diffs: change a
driver's caps without regenerating the doc -> RED. A hand-edited matrix is an
uncorroborated claim — the failure mode this library exists to kill.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _gen_module():
    spec = importlib.util.spec_from_file_location(
        "gen_backend_matrix", ROOT / "scripts" / "gen_backend_matrix.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_backend_matrix_doc_is_current():
    mod = _gen_module()
    doc = ROOT / "docs" / "backends.md"
    assert doc.exists(), "docs/backends.md missing — run scripts/gen_backend_matrix.py"
    assert doc.read_text() == mod.render(), (
        "docs/backends.md is stale — run: python scripts/gen_backend_matrix.py")


def test_every_builtin_declares_or_synthesizes_caps():
    """Every builtin resolves to a class whose caps are typed or honestly synthesized —
    and the write-only transport is never presented as an external judge."""
    mod = _gen_module()
    from ooptdd.backends import _BUILTINS, _load
    for name in _BUILTINS:
        caps = mod._caps_of(_load(_BUILTINS[name]))
        if caps.write_only:
            assert not (caps.independent and caps.queryable) or name != "otel"
    # the positioning invariants the docs lean on:
    from ooptdd.backends.memory import MemoryBackend
    from ooptdd.backends.jsonl import JsonlBackend
    from ooptdd.backends.openobserve import OpenObserveBackend
    assert MemoryBackend.caps.independent is False
    assert JsonlBackend.caps.independent is False
    assert OpenObserveBackend.caps.independent is True
    assert OpenObserveBackend.caps.paginates is True
