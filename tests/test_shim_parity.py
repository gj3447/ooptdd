"""The import surface must be single and self-consistent (audit gap-07).

ooptdd has three import surfaces — the root package, the flat back-compat shims
(ooptdd.gate/verify/monitor/model/ontology/semconv), and the canonical engine.*/domain.*
modules. They had drifted: the root could not express the primary gate-honesty flow
(load_gate + evaluate + evidence_tier + the lint/strength/signing pures), so consumers split —
some import ooptdd.gate, some ooptdd.engine.gate — and nothing mechanically kept the surfaces in
sync. This pins the contract: the root is the single public surface, and every shim stays a
faithful subset of its canonical module.
"""
import types

import ooptdd
from ooptdd import gate, model, monitor, ontology, semconv, verify
from ooptdd.domain import model as d_model
from ooptdd.domain import ontology as d_ontology
from ooptdd.domain import semconv as d_semconv
from ooptdd.engine import gate as e_gate
from ooptdd.engine import monitor as e_monitor
from ooptdd.engine import verify as e_verify

# The primary flow a consumer needs from the ROOT — the load_gate+evaluate+evidence_tier path
# plus the anti-gaming pures and the signing primitives. This is the RED-first anchor: the root
# lacked these, so the most common flow raised ImportError from `from ooptdd import load_gate`.
ROOT_REQUIRED = {
    "load_gate", "evaluate", "evaluate_events", "evidence_tier", "EVIDENCE_TIERS",
    "green_banner", "lint_spec", "strength_fingerprint", "compare_strength",
    "sign_chain", "verify_chain", "memory_reset",
}

_SHIM_PAIRS = [
    (gate, e_gate), (verify, e_verify), (monitor, e_monitor),
    (model, d_model), (ontology, d_ontology), (semconv, d_semconv),
]


def _public(mod) -> set[str]:
    """Re-exportable names: non-underscore, excluding imported submodules."""
    return {
        n for n in vars(mod)
        if not n.startswith("_") and not isinstance(getattr(mod, n), types.ModuleType)
    }


def test_root_exports_the_primary_flow():
    """RED-first: the root package must expose the whole primary flow, not force a reach into
    ooptdd.gate / ooptdd.engine.gate. Fails until __init__ re-exports these."""
    missing_all = ROOT_REQUIRED - set(ooptdd.__all__)
    assert not missing_all, f"root __all__ is missing primary-flow names: {sorted(missing_all)}"
    not_importable = {n for n in ROOT_REQUIRED if not hasattr(ooptdd, n)}
    assert not not_importable, f"named in __all__ but not importable from root: {sorted(not_importable)}"


def test_root_all_entries_are_importable():
    """No phantom __all__ entry: everything the package advertises must resolve."""
    missing = [n for n in ooptdd.__all__ if not hasattr(ooptdd, n)]
    assert not missing, f"__all__ lists names that are not importable: {missing}"


def test_each_shim_is_a_faithful_subset_of_its_canonical_module():
    """A shim may re-export a subset, never a name its canonical module does not have — so a
    rename/removal upstream can't leave a shim silently exporting a stale symbol."""
    for shim, canon in _SHIM_PAIRS:
        extra = _public(shim) - _public(canon)
        assert not extra, f"{shim.__name__} exports names absent from {canon.__name__}: {sorted(extra)}"


def test_signing_primitives_reach_the_root_identically():
    """The signing pures the gate path now depends on must be the SAME objects at the root and in
    domain.model — no shadow copy."""
    assert ooptdd.sign_chain is d_model.sign_chain
    assert ooptdd.verify_chain is d_model.verify_chain
