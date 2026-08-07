"""Stable domain-separated identity primitives for framework extensions.

The implementation is shared with the generic Ouroboros protocol, but consumers do
not need to depend on that namespace or any workflow profile to hash explicit values.
"""

from .ouroboros.identity import (
    CANONICALIZATION,
    MAX_INTEROPERABLE_INTEGER,
    Digest,
    canonical_json_bytes,
    digest_json,
    digest_raw,
    raw_sha256,
)

__all__ = (
    "CANONICALIZATION",
    "MAX_INTEROPERABLE_INTEGER",
    "Digest",
    "canonical_json_bytes",
    "digest_json",
    "digest_raw",
    "raw_sha256",
)
