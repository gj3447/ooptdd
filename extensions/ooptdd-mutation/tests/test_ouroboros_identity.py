import pytest

from ooptdd_mutation.ouroboros import (
    MAX_INTEROPERABLE_INTEGER,
    CycleIdentity,
    ProtocolBudget,
    canonical_json_bytes,
    digest_json,
    raw_sha256,
)


def test_canonical_object_digest_ignores_insertion_order_but_not_unicode_bytes():
    left = {"b": [2, 1], "a": "é"}
    right = {"a": "é", "b": [2, 1]}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert digest_json(left, scope="test", schema_version="v1") == digest_json(
        right, scope="test", schema_version="v1"
    )
    assert canonical_json_bytes({"x": "é"}) != canonical_json_bytes({"x": "e\u0301"})


def test_raw_hash_preserves_newlines_and_stays_separate_from_object_identity():
    assert raw_sha256(b"line\n") != raw_sha256(b"line\r\n")
    digest = digest_json({"line": "x"}, scope="test", schema_version="v1")
    assert digest.canonicalization != "raw-bytes"


@pytest.mark.parametrize(
    "value",
    [1.25, float("nan"), float("inf"), {1: "non-string-key"}, {"x": object()}, 2**60],
)
def test_authoritative_identity_rejects_ambiguous_values(value):
    with pytest.raises(ValueError):
        canonical_json_bytes(value)


def test_domain_separation_changes_the_digest():
    value = {"same": "payload"}
    assert (
        digest_json(value, scope="event", schema_version="v1").value
        != digest_json(value, scope="receipt", schema_version="v1").value
    )


@pytest.mark.parametrize("field", ["scope", "schema_version"])
def test_domain_separation_labels_reject_nul(field):
    arguments = {"scope": "event", "schema_version": "v1"}
    arguments[field] = arguments[field] + "\0suffix"
    with pytest.raises(ValueError, match="NUL"):
        digest_json({"same": "payload"}, **arguments)


def test_canonical_key_order_is_unicode_code_point_order():
    # U+E000 sorts before U+10000 by scalar value; this pins the protocol's v1 rule.
    encoded = canonical_json_bytes({"\U00010000": 2, "\ue000": 1}).decode("utf-8")
    assert encoded == '{"\ue000":1,"\U00010000":2}'


def test_protocol_integer_bounds_leave_room_for_generation_and_recovery():
    ProtocolBudget(MAX_INTEROPERABLE_INTEGER - 2, MAX_INTEROPERABLE_INTEGER)
    CycleIdentity(
        "last-possible-generation",
        MAX_INTEROPERABLE_INTEGER - 1,
        "0" * 64,
    )
    with pytest.raises(ValueError, match="safety-tail"):
        ProtocolBudget(MAX_INTEROPERABLE_INTEGER - 1, 1)
    with pytest.raises(ValueError, match="successor-budget"):
        CycleIdentity("too-late", MAX_INTEROPERABLE_INTEGER, "0" * 64)
