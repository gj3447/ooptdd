"""Generic event envelopes and signing primitives with no external I/O.

Envelope shape (one JSON object per event)::

    {
      "cid": "...", "correlation_id": "...",                       # aliases
      "service": "myapp", "level": "application-defined severity",  # optional
      "event": "application-defined event type",
      ...event-specific fields...
    }

``cid`` is the compact canonical key and ``correlation_id`` is its descriptive
alias. Domain-specific identifiers may be added as ordinary payload fields.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from .ports import EventSink
from .settings import DEFAULT_SERVICE

# Signature values and backend annotations describe transport state rather than the
# application record. Every other field is authenticated, including fields unknown to
# this package, so adding a new payload attribute cannot silently escape the signature.
_SIGNATURE_TRANSPORT_FIELDS = frozenset({"sig", "sig_alg", "sig_chain", "prev_sig"})
SIG_ALG = "hmac-sha256-v2"


def correlation_keys(cid: str) -> dict:
    """The id under every alias a backend might index on."""
    return {"cid": cid, "correlation_id": cid}


# ── CloudEvents 1.0 floor ──────────────────────────────────────────────────────
# CloudEvents (CNCF) is the vendor-neutral event envelope standard. We don't adopt
# the whole spec — only its *required floor* (4 context attributes) — so an ooptdd
# event is recognizable to any CloudEvents-aware store/router without us reinventing
# id/source/type semantics. Mapping: event->type, service->source, cid->subject.
CE_SPECVERSION = "1.0"
CE_REQUIRED = ("id", "source", "specversion", "type")


def cloudevents_envelope(rec: dict, *, source: str | None = None) -> dict:
    """Project an ooptdd record onto the CloudEvents 1.0 floor (non-destructive copy).

    ``id`` is a deterministic content hash, so re-shipping the same record yields the
    same CloudEvents id (idempotent — no duplicate events on retry). ``source`` defaults
    to the record's ``service``; ``subject`` carries the correlation id.
    """
    src = source or rec.get("service") or DEFAULT_SERVICE
    cid = rec.get("cid") or rec.get("correlation_id")
    body = json.dumps(
        rec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode()
    out = dict(rec)
    out.update(
        {
            "id": hashlib.sha256(body).hexdigest()[:32],
            "source": str(src),
            "specversion": CE_SPECVERSION,
            "type": str(rec.get("event", "")),
        }
    )
    if cid is not None:
        out["subject"] = str(cid)
    return out


# ── ooptdd event-envelope wire contract ─────────────────────────────────────────
# A versioned, machine-readable schema for the envelope EVERY shipped record carries — distinct
# from CE_SPECVERSION (the CloudEvents floor above, which versions only the 4 CE context attrs).
# This is the single source of truth for all emitters. It is stamped into every
# builder record as ``spec_version``; serialized schema artifacts are mirrors of this
# in-package constant.
ENVELOPE_SPEC_VERSION = "1.0.0"
ENVELOPE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/gj3447/ooptdd/schema/envelope.schema.json",
    "title": "ooptdd event envelope",
    "type": "object",
    "required": ["spec_version", "cid", "correlation_id", "service", "event"],
    "properties": {
        "spec_version": {"const": ENVELOPE_SPEC_VERSION},
        "cid": {"type": "string"},
        "correlation_id": {"type": "string"},
        "service": {"type": "string"},
        "level": {"type": "string", "minLength": 1},
        "event": {"type": "string"},
    },
    # Records also carry event-specific payload (duration_s/error/total/sig/trace_id/…); the
    # envelope contract pins the carrier, not the payload — so extra keys are allowed.
    "additionalProperties": True,
}


def with_trace_context(rec: dict, trace_id: str, span_id: str | None = None) -> dict:
    """Attach W3C trace context (``trace_id``/``span_id``) to an event (non-destructive).

    OTel log records carry these so a log line joins to its span; ooptdd uses them as a
    standard correlation key alongside ``cid``, binding an emitted event to the exact
    run/span that produced it.
    """
    out = dict(rec)
    out["trace_id"] = str(trace_id)
    if span_id is not None:
        out["span_id"] = str(span_id)
    return out


def validate_cloudevents(rec: dict) -> list[str]:
    """Violations against the CloudEvents 1.0 floor (each required attr a non-empty
    string). Empty list = conforms. ``type`` must be present *and* non-empty — an event
    with no name is not a valid CloudEvent."""
    out: list[str] = []
    for k in CE_REQUIRED:
        v = rec.get(k)
        if not isinstance(v, str) or not v:
            out.append(f"missing/empty required CloudEvents attr '{k}'")
    return out


def _canonical(rec: dict) -> bytes:
    """Deterministic bytes for all application fields in one record.

    Signature transport fields and underscore-prefixed backend annotations are
    excluded because they may be attached after the application record is signed.
    """
    projection = {
        key: value
        for key, value in rec.items()
        if key not in _SIGNATURE_TRANSPORT_FIELDS and not key.startswith("_")
    }
    return json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode()


def sign_record(rec: dict, key: str) -> str:
    """HMAC-SHA256 of the canonical projection (hex)."""
    return hmac.new(key.encode(), _canonical(rec), hashlib.sha256).hexdigest()


def signature_status(rec: dict, key: str | None) -> str:
    """valid | invalid | unsigned | unverifiable.

    unsigned     no sig on the record (sender had no key)
    unverifiable sig present but the verifier has no key (can't judge — never a failure)
    valid/invalid  sig present and the verifier recomputed it (constant-time compare)
    """
    have = rec.get("sig")
    if not have:
        return "unsigned"
    if not key:
        return "unverifiable"
    return "valid" if hmac.compare_digest(have, sign_record(rec, key)) else "invalid"


# ── tamper-evident hash chain ──────────────────────────────────────────────────
# The single-record `sig` catches an edit to *that* record. A hash chain catches more:
# deletion and reordering of receipts too — a writer cannot silently drop an inconvenient
# event. Each record's MAC folds in the previous MAC (Schneier-Kelsey / Crosby-Wallach
# tamper-evident logging). With key evolution (k_{i+1}=H(k_i)) a leaked *current* key
# can't forge *earlier* receipts (forward security). Scope to one writer per stream
# because the chain needs a single ordered append.
_CHAIN_EXCLUDE = ("sig_chain", "prev_sig")


def _chain_canonical(rec: dict) -> bytes:
    proj = {k: v for k, v in rec.items() if k not in _CHAIN_EXCLUDE}
    return json.dumps(
        proj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode()


def _evolve(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def sign_chain(records: list[dict], key: str, *, evolve: bool = False) -> list[dict]:
    """Return copies of ``records`` carrying a tamper-evident hash chain.

    ``rec["sig_chain"] = HMAC(k_i, canonical(rec) || prev_mac)`` and ``rec["prev_sig"]``
    links to the previous record. ``evolve=True`` ratchets the key forward per record.
    """
    out: list[dict] = []
    prev, k = "", key
    for rec in records:
        r = dict(rec)
        mac = hmac.new(k.encode(), _chain_canonical(r) + prev.encode(), hashlib.sha256).hexdigest()
        r["prev_sig"] = prev
        r["sig_chain"] = mac
        out.append(r)
        prev = mac
        if evolve:
            k = _evolve(k)
    return out


def verify_chain(
    records: list[dict],
    key: str,
    *,
    evolve: bool = False,
    expect_len: int | None = None,
    expect_head: str | None = None,
) -> dict:
    """Verify a hash chain. Returns ``{ok, broken_index, reason}`` — ``broken_index`` is the
    first record whose previous-link or MAC fails (``None`` if intact). A mismatch means an
    edit, an *interior* deletion, or a reorder somewhere at or before that index.

    **Interior is not a hedge — it is the whole boundary.** Truncating the chain leaves a
    prefix that verifies perfectly, because a chain cannot testify to its own length: run a
    session, dislike the result, drop the trailing records, and every link still checks out
    (measured both with and without ``evolve``). The same holds for a wholesale re-signing
    by a key holder. These are not defects in the MAC; the information needed to refuse them
    is not in the records.

    ``expect_len`` / ``expect_head`` provide that information from an independent
    anchor, such as a commit or a peer's copy of the last MAC. A caller that requires
    truncation detection must always provide one of these expectations.
    """
    prev, k = "", key
    for i, rec in enumerate(records):
        if rec.get("prev_sig") != prev:
            return {
                "ok": False,
                "broken_index": i,
                "reason": "prev_link_mismatch_possible_deletion_or_reorder",
            }
        expect = hmac.new(
            k.encode(), _chain_canonical(rec) + prev.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(str(rec.get("sig_chain", "")), expect):
            return {"ok": False, "broken_index": i, "reason": "chain_mac_mismatch_possible_tamper"}
        prev = str(rec.get("sig_chain", ""))
        if evolve:
            k = _evolve(k)
    if expect_len is not None and len(records) < expect_len:
        return {
            "ok": False,
            "broken_index": len(records),
            "reason": "truncated_shorter_than_external_expectation",
        }
    if expect_head is not None and not hmac.compare_digest(prev, expect_head):
        return {
            "ok": False,
            "broken_index": max(len(records) - 1, 0),
            "reason": "head_mismatch_vs_external_anchor",
        }
    return {"ok": True, "broken_index": None, "reason": None}


def build_event(cid: str, event: str, *, service: str = DEFAULT_SERVICE, **attrs) -> dict:
    """The generic emit envelope (pure): one structured event under both correlation aliases,
    stamped with the wire ``spec_version``, plus a ``service`` and any event-specific ``attrs``.
    This is what a consumer ships instead of hand-rolling a flat dict per verb — the same shape the
    optional adapters produce, so one gate grammar reads them all."""
    return {
        **correlation_keys(cid),
        "spec_version": ENVELOPE_SPEC_VERSION,
        "service": service,
        "event": event,
        **attrs,
    }


@dataclass(frozen=True)
class Emitter:
    """Compatibility facade that binds an :class:`EventSink` to an event service.

    Synchronization and transport behavior belong to the injected adapter. The facade
    only builds an immutable call description and delegates one batch to the sink; it
    owns no lock, worker, retry policy, or process-global state.
    """

    _backend: EventSink
    service: str = DEFAULT_SERVICE

    def __post_init__(self) -> None:
        if not isinstance(self.service, str) or not self.service:
            raise ValueError("emitter service must be a non-empty string")
        if not isinstance(self._backend, EventSink):
            raise TypeError("emitter backend must implement EventSink")

    def build(self, event: str, cid: str, **attrs) -> dict:
        return build_event(cid, event, service=self.service, **attrs)

    def emit(self, event: str, cid: str, **attrs) -> dict:
        rec = self.build(event, cid, **attrs)
        self._backend.ship([rec])
        return rec
