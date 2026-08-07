"""Strict, domain-separated identity primitives for the Ouroboros protocol.

The existing event helpers intentionally optimise for convenient telemetry.  Protocol
identity has a different job: it must fail closed when a value has no stable wire form.
This module therefore accepts only the interoperable JSON subset used by the protocol
and keeps raw-byte hashes distinct from canonical-object digests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

CANONICALIZATION = "ooptdd-canonical-json/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_INTEROPERABLE_INTEGER = (1 << 53) - 1


def _validate_domain_label(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if "\0" in value:
        raise ValueError(f"{label} must not contain NUL")


def _validate_json(value: Any, path: str = "$") -> None:
    """Reject values whose canonical identity is ambiguous across runtimes."""

    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if not -MAX_INTEROPERABLE_INTEGER <= value <= MAX_INTEROPERABLE_INTEGER:
            raise ValueError(f"{path}: integer exceeds the interoperable JSON range")
        return
    if isinstance(value, float):
        raise ValueError(f"{path}: floats are forbidden in authoritative identity values")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: object keys must be strings")
            _validate_json(child, f"{path}.{key}")
        return
    raise ValueError(f"{path}: unsupported identity value {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the protocol's exact UTF-8 canonical encoding.

    Object keys are sorted, insignificant whitespace is removed, Unicode is emitted as
    UTF-8 without normalization, and floats / non-JSON coercions are refused.
    """

    _validate_json(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return encoded.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError(f"value has no canonical protocol encoding: {error}") from error


def raw_sha256(data: bytes) -> str:
    """Hash exact bytes; no decoding, newline conversion, or canonicalization occurs."""

    if not isinstance(data, bytes):
        raise TypeError("raw_sha256 requires bytes")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Digest:
    """A digest plus the metadata required to interpret what it identifies."""

    algorithm: str
    scope: str
    canonicalization: str
    schema_version: str
    value: str

    def __post_init__(self) -> None:
        for field_name in ("algorithm", "scope", "canonicalization", "schema_version"):
            _validate_domain_label(getattr(self, field_name), f"digest {field_name}")
        if self.algorithm != "sha256":
            raise ValueError("only sha256 digests are supported")
        if _SHA256_RE.fullmatch(self.value) is None:
            raise ValueError("digest value must be lowercase 64-hex SHA-256")

    def to_dict(self) -> dict[str, str]:
        return {
            "algorithm": self.algorithm,
            "scope": self.scope,
            "canonicalization": self.canonicalization,
            "schema_version": self.schema_version,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Digest:
        if not isinstance(value, dict):
            raise ValueError("digest must be an object")
        required = {"algorithm", "scope", "canonicalization", "schema_version", "value"}
        if set(value) != required or not all(isinstance(value[key], str) for key in required):
            raise ValueError(f"digest fields must be exactly {sorted(required)} strings")
        return cls(**value)


def digest_json(value: Any, *, scope: str, schema_version: str) -> Digest:
    """Hash a canonical object under an explicit domain-separation prefix."""

    _validate_domain_label(scope, "digest scope")
    _validate_domain_label(schema_version, "digest schema_version")
    prefix = f"ooptdd\0{scope}\0{schema_version}\0{CANONICALIZATION}\0".encode()
    return Digest(
        algorithm="sha256",
        scope=scope,
        canonicalization=CANONICALIZATION,
        schema_version=schema_version,
        value=hashlib.sha256(prefix + canonical_json_bytes(value)).hexdigest(),
    )


def digest_raw(data: bytes, *, scope: str, schema_version: str) -> Digest:
    """Describe an exact-byte SHA-256 without conflating it with JSON identity."""

    _validate_domain_label(scope, "digest scope")
    _validate_domain_label(schema_version, "digest schema_version")
    return Digest(
        algorithm="sha256",
        scope=scope,
        canonicalization="raw-bytes",
        schema_version=schema_version,
        value=raw_sha256(data),
    )


def receipt_content_digest(document: dict[str, Any], *, schema_version: str) -> Digest:
    """Hash a receipt while excluding only its self-hash value.

    The rest of the integrity metadata remains covered, making the exclusion rule both
    narrow and testable.
    """

    candidate = copy.deepcopy(document)
    integrity = candidate.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("receipt integrity must be an object")
    integrity.pop("value", None)
    return digest_json(candidate, scope="ouroboros-receipt-content", schema_version=schema_version)


__all__ = (
    "CANONICALIZATION",
    "MAX_INTEROPERABLE_INTEGER",
    "Digest",
    "canonical_json_bytes",
    "digest_json",
    "digest_raw",
    "raw_sha256",
    "receipt_content_digest",
)
