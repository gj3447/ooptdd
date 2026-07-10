"""strength_fingerprint must cover the enforcement wings (audit 2026-07-08, #3).

The fingerprint summarised only the positive `expect` strength, so turning OFF a required
wing — `require_signature`, `require_corroboration`, `forbid_errors` — or WIDENING the
`allow_errors` allowlist weakened the gate with an UNCHANGED fingerprint, and
`compare_strength` reported `weakened=False`. An agent could silently flip
`require_signature: true` to false with no strength regression. The fingerprint now carries
an `enforcement` profile and compare_strength diffs it.
"""
from __future__ import annotations

from ooptdd.engine.gate import compare_strength, strength_fingerprint

_E = [{"event": "a", "where": {"k": "v"}}]


def test_compare_strength_flags_removing_require_signature():
    base = strength_fingerprint({"expect": _E, "require_signature": True})
    weak = strength_fingerprint({"expect": _E})  # signature enforcement secretly dropped
    cmp = compare_strength(base, weak)
    assert cmp["weakened"] is True
    assert any("require_signature" in r for r in cmp["regressions"])


def test_compare_strength_flags_disabling_forbid_errors():
    cmp = compare_strength(
        strength_fingerprint({"expect": _E, "forbid_errors": True}),
        strength_fingerprint({"expect": _E, "forbid_errors": False}))
    assert cmp["weakened"] and any("forbid_errors" in r for r in cmp["regressions"])


def test_compare_strength_flags_widening_allow_errors():
    cmp = compare_strength(
        strength_fingerprint({"expect": _E, "allow_errors": [{"event": "e"}]}),
        strength_fingerprint({"expect": _E,
                              "allow_errors": [{"event": "e"}, {"event": "f"}]}))
    assert cmp["weakened"] and any("allow_errors" in r for r in cmp["regressions"])


def test_compare_strength_honest_adding_enforcement_not_weaker():
    # over-reach guard: turning enforcement ON (OFF->ON) is a STRENGTHENING, never a regression.
    cmp = compare_strength(
        strength_fingerprint({"expect": _E}),
        strength_fingerprint({"expect": _E, "require_corroboration": True}))
    assert cmp["weakened"] is False


def test_compare_strength_old_baseline_without_enforcement_no_false_flag():
    # backward-compat: a pre-fix fingerprint JSON has no `enforcement` key -> never false-flags.
    old = {"gating": 1, "by_strength": {"value-pinned": 1}, "min_threshold": 1.0, "score": 3.0}
    cur = strength_fingerprint({"expect": _E})
    assert compare_strength(old, cur)["weakened"] is False
