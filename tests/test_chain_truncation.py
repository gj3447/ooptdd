"""The chain cannot testify to its own length — pinned as a boundary, then closed.

``docs/ouroboros.md`` ends the meta-layer regress at a residual that "external anchors"
retire: commit hashes, signed receipts, review. This file is that claim brought down to
the evidence layer and measured, because the anchor layer had never been probed the way
the gate layer was.

Result (2026-08-05, both ``evolve`` modes): edits, interior deletions and reorders are
caught; **truncation and wholesale re-signing are not**. ``verify_chain``'s docstring
had promised "an edit, a deletion, or a reorder" without qualification, which overstates
what a self-contained chain can do — dropping the tail leaves a prefix in which every
link is genuinely valid.

Two kinds of test live here, and the difference is deliberate:

  * the *boundary* tests assert that truncation slips through with no external input.
    They are written to FAIL if someone ever makes the bare call detect it — at which
    point the claim in ouroboros.md should be revisited, not the test silenced. Asserting
    a hole as a hole is the point; a test that wished it closed would be the vacuity this
    repo exists to price.
  * the *closure* tests assert that the same attack dies once an external expectation is
    supplied, which is the only mechanism that can see it.
"""
from __future__ import annotations

import copy

import pytest

from ooptdd.domain.model import sign_chain, sign_record, verify_chain

KEY = "k" * 32
EVOLVE_MODES = [False, True]


@pytest.fixture
def chain():
    base = [{"cid": f"c{i}", "event": "check_failed", "n": i} for i in range(6)]
    return [dict(r, sig=sign_record(r, KEY)) for r in base]


def _signed(chain, evolve):
    signed = sign_chain(chain, KEY, evolve=evolve)
    # A battery whose control does not verify is measuring nothing.
    assert verify_chain(signed, KEY, evolve=evolve)["ok"]
    return signed


# ── what the chain does catch ────────────────────────────────────────────────────

@pytest.mark.parametrize("evolve", EVOLVE_MODES)
@pytest.mark.parametrize("name,mutate", [
    ("edit", lambda c: [dict(r, event="check_passed") if i == 1 else r
                        for i, r in enumerate(c)]),
    ("interior_delete", lambda c: c[:2] + c[3:]),
    ("reorder", lambda c: c[:2] + [c[3], c[2]] + c[4:]),
    ("strip_record_sig", lambda c: [{k: v for k, v in r.items() if k != "sig"} for r in c]),
])
def test_detected_without_any_external_input(chain, evolve, name, mutate):
    signed = _signed(chain, evolve)
    tampered = mutate(copy.deepcopy(signed))
    assert tampered != signed, f"{name} did not actually mutate anything — vacuous probe"
    assert not verify_chain(tampered, KEY, evolve=evolve)["ok"], name


# ── what it provably cannot catch ────────────────────────────────────────────────

@pytest.mark.parametrize("evolve", EVOLVE_MODES)
@pytest.mark.parametrize("keep", [5, 3, 1], ids=["drop_one", "drop_half", "keep_first"])
def test_truncation_is_invisible_to_a_bare_verify(chain, evolve, keep):
    """Boundary, asserted as a boundary. If this starts failing, ouroboros.md changed."""
    signed = _signed(chain, evolve)
    assert verify_chain(signed[:keep], KEY, evolve=evolve)["ok"], (
        "a truncated chain verifies because every surviving link is genuinely valid; "
        "the missing information is the length, and the length is not in the records"
    )


@pytest.mark.parametrize("evolve", EVOLVE_MODES)
def test_wholesale_resigning_is_invisible_to_a_bare_verify(chain, evolve):
    """A key holder can re-sign a rewritten history into a flawless chain."""
    rewritten = sign_chain([dict(r, event="check_passed") for r in chain], KEY, evolve=evolve)
    assert verify_chain(rewritten, KEY, evolve=evolve)["ok"]
    assert rewritten != _signed(chain, evolve)


# ── what closes them: an expectation from outside ────────────────────────────────

@pytest.mark.parametrize("evolve", EVOLVE_MODES)
def test_expect_len_catches_truncation(chain, evolve):
    signed = _signed(chain, evolve)
    r = verify_chain(signed[:-1], KEY, evolve=evolve, expect_len=len(signed))
    assert not r["ok"] and r["reason"] == "truncated_shorter_than_external_expectation"


@pytest.mark.parametrize("evolve", EVOLVE_MODES)
def test_expect_head_catches_truncation_and_rewrite(chain, evolve):
    signed = _signed(chain, evolve)
    anchor = signed[-1]["sig_chain"]                      # what a caller files elsewhere

    r = verify_chain(signed[:-1], KEY, evolve=evolve, expect_head=anchor)
    assert not r["ok"] and r["reason"] == "head_mismatch_vs_external_anchor"

    rewritten = sign_chain([dict(r_, event="check_passed") for r_ in chain], KEY, evolve=evolve)
    r = verify_chain(rewritten, KEY, evolve=evolve, expect_head=anchor)
    assert not r["ok"] and r["reason"] == "head_mismatch_vs_external_anchor"


@pytest.mark.parametrize("evolve", EVOLVE_MODES)
def test_expectations_do_not_fire_on_an_intact_chain(chain, evolve):
    """The other degenerate state. A check that flags everything certifies nothing."""
    signed = _signed(chain, evolve)
    assert verify_chain(signed, KEY, evolve=evolve,
                        expect_len=len(signed), expect_head=signed[-1]["sig_chain"])["ok"]
    # A longer-than-expected chain is growth, not truncation.
    assert verify_chain(signed, KEY, evolve=evolve, expect_len=len(signed) - 2)["ok"]


def test_empty_chain_against_an_anchor_is_not_silently_fine():
    """Deleting every record must not read as 'nothing to check'."""
    r = verify_chain([], KEY, expect_head="deadbeef")
    assert not r["ok"] and r["reason"] == "head_mismatch_vs_external_anchor"
    assert verify_chain([], KEY, expect_len=1)["reason"] == (
        "truncated_shorter_than_external_expectation")


def test_signature_is_backward_compatible(chain):
    """Existing callers pass neither expectation and must be unaffected."""
    signed = sign_chain(chain, KEY)
    assert verify_chain(signed, KEY) == {"ok": True, "broken_index": None, "reason": None}
