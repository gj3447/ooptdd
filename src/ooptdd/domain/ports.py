"""Domain ports — the abstractions the engine depends on, owned by the domain layer.

The :class:`Backend` Protocol is a *port* (hexagonal architecture / dependency inversion):
the engine (gate, verify) is written against this interface, and concrete drivers
(memory, OpenObserve, OTLP, …) are *adapters* that implement it in :mod:`ooptdd.backends`.
Keeping the port here — not in the adapter package — makes the dependency arrow
point engine → domain, never engine → a concrete adapter.

Beyond the backend, this module owns the small **value objects and ports the engine reads
against**: :class:`QueryResult` (an answer, with its completeness/reachability honesty),
:class:`TimeWindow` / :class:`QuerySpec` (a typed *query intent* instead of bare kwargs),
:class:`BackendCaps` (typed capabilities instead of ad-hoc ``getattr``), and the
:class:`Clock` port (injectable time, so callers can drive polling deterministically).
The bridge functions :func:`backend_caps` and :func:`fetch` let the
engine use the typed surface while every legacy two-method backend keeps working untouched.

A backend does exactly two required things: ``ship(events)`` (write) and
``query(cid, *, since_us, until_us)`` (read back, reporting whether the query itself was
*reachable* and *complete*). The interesting logic — the LTL₃ verdict, the polling window,
the clock-skew buffer — lives *above* the port, identical no matter where events land.

Two load-bearing honesty fields on :class:`QueryResult`:
  - ``reachable`` distinguishes "the store says no such event" (absent / ⊥) from "I could
    not even ask the store" (inconclusive / ?). Conflating them turns a blip into a flake.
  - ``complete`` distinguishes a full answer from a partial one (a paging/row cap was hit).
    A truncated read may undercount or hide an offender, so the verdict layer must refuse to
    treat ``complete=False`` as a clean pass — the same discipline as ``reachable``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from fractions import Fraction
from math import isfinite
from pathlib import PurePath
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

_IMMUTABLE_VALUE_TYPES = (
    str,
    bytes,
    bool,
    int,
    float,
    Decimal,
    Fraction,
    UUID,
    type(None),
)


class _FrozenEventSequence(tuple[Mapping[str, object], ...]):
    """Tuple storage with list-compatible equality for the legacy result surface."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return list(self) == other
        return tuple.__eq__(self, other)

    __hash__ = None  # type: ignore[assignment]


def _freeze_public_value(value: object, active: set[int] | None = None) -> object:
    """Capture a supported public value without retaining mutable caller aliases.

    The port layer accepts JSON-shaped values plus a small set of common immutable
    scalar types used by external probes. Unsupported or cyclic objects are rejected
    instead of being retained by reference.
    """

    seen = set() if active is None else active
    if type(value) is date or type(value) in _IMMUTABLE_VALUE_TYPES:
        return value
    if isinstance(value, PurePath) and type(value).__module__ == "pathlib":
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise ValueError("cannot capture a cyclic mapping")
        seen.add(identity)
        try:
            captured: dict[str, object] = {}
            for key, child in value.items():
                if not isinstance(key, str):
                    raise TypeError("captured mapping keys must be strings")
                captured[key] = _freeze_public_value(child, seen)
            return MappingProxyType(captured)
        finally:
            seen.remove(identity)
    if isinstance(value, list | tuple):
        identity = id(value)
        if identity in seen:
            raise ValueError("cannot capture a cyclic sequence")
        seen.add(identity)
        try:
            return tuple(_freeze_public_value(child, seen) for child in value)
        finally:
            seen.remove(identity)
    if isinstance(value, set | frozenset):
        identity = id(value)
        if identity in seen:
            raise ValueError("cannot capture a cyclic set")
        seen.add(identity)
        try:
            return frozenset(_freeze_public_value(child, seen) for child in value)
        finally:
            seen.remove(identity)
    raise TypeError(f"unsupported public value: {type(value).__name__}")


def _freeze_event(value: Mapping[str, object]) -> Mapping[str, object]:
    captured = _freeze_public_value(value)
    if not isinstance(captured, Mapping):  # defensive: value is checked by QueryResult
        raise TypeError("captured event must remain a mapping")
    return captured


@dataclass(frozen=True)
class QueryResult:
    """Outcome of a single backend query.

    reachable: True iff the query round-trip succeeded (regardless of hits). False means
               the store was unreachable -> verdict stays `?` (inconclusive).
    events:    the matching event envelopes (dicts), newest-or-any order.
    complete:  True iff the backend returned *every* matching row for the window. False iff a
               paging/row cap was hit and the set is partial — incomplete evidence, which the
               verdict layer must not treat as a clean pass. A full read leaves this True.
    error:     None on a clean round-trip; else a short "TypeError: msg" attribution of WHY the
               query failed (a 401 vs a DNS failure vs an unconfigured store), so a reachable=False
               is diagnosable instead of an anonymous outage. Never gates the verdict — advisory.
    """

    reachable: bool
    events: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    complete: bool = True
    error: str | None = None
    #: typed diagnosis of WHY the query failed: rate_limited (429/503) / auth (401/403)
    #: / timeout (408, socket timeouts) / other. Advisory, like ``error``.
    error_kind: str | None = None
    #: parsed ``Retry-After`` seconds when the store throttled us — the poller honors it
    #: instead of burning retry attempts inside the throttle window.
    retry_after_s: float | None = None
    #: opaque continuation token from a BOUNDED ``query_spec`` page (``limit`` set and
    #: filled): pass it back as ``QuerySpec.cursor`` for the next page. None = exhausted.
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("reachable", "complete"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"query result {field_name} must be a bool")
        if not isinstance(self.events, Sequence) or isinstance(self.events, str | bytes):
            raise TypeError("query result events must be a sequence of mappings")
        if any(not isinstance(event, Mapping) for event in self.events):
            raise TypeError("query result events must be a sequence of mappings")
        for field_name in ("error", "error_kind", "next_cursor"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"query result {field_name} must be text or None")
        if self.retry_after_s is not None:
            if (
                isinstance(self.retry_after_s, bool)
                or not isinstance(self.retry_after_s, int | float)
                or not isfinite(self.retry_after_s)
                or self.retry_after_s < 0
            ):
                raise ValueError("query result retry_after_s must be finite and non-negative")
        object.__setattr__(
            self,
            "events",
            _FrozenEventSequence(_freeze_event(event) for event in self.events),
        )


# ── time: an injectable Clock port + a typed query window ───────────────────────


class Clock(Protocol):
    """The time port. The engine reads ``now_us()`` instead of calling ``time.time()`` so
    polling windows and retry loops are deterministic (and sleep-free) under a fake clock."""

    def now_us(self) -> int:
        """Current wall-clock time in epoch microseconds."""
        ...


class SystemClock:
    """The real clock — epoch microseconds from ``time.time()``. The default everywhere."""

    def now_us(self) -> int:
        return int(time.time() * 1_000_000)


@dataclass(frozen=True)
class TimeWindow:
    """A microsecond readback window ``[since_us, until_us]`` (store-receive time)."""

    since_us: int
    until_us: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (
                self.since_us,
                self.until_us,
            )
        ):
            raise TypeError("time window bounds must be integer microseconds")
        if self.since_us > self.until_us:
            raise ValueError("time window since_us must not exceed until_us")

    @classmethod
    def around_now(cls, clock: Clock, lookback_s: int, future_buffer_s: int) -> TimeWindow:
        """The window a poll uses: ``[now - lookback, now + future_buffer]``. The future
        buffer absorbs receive-time / clock-skew (a record stamped just after we start
        looking). Integer arithmetic identical to the historical inline computation."""
        now_us = clock.now_us()
        return cls(now_us - lookback_s * 1_000_000, now_us + future_buffer_s * 1_000_000)


@dataclass(frozen=True)
class QuerySpec:
    """A typed query *intent* handed to a backend, instead of loose kwargs: which cid, over
    what window, with an optional row ``limit`` / paging ``cursor`` / ``where`` filter. A
    backend that implements ``query_spec`` reads it directly; legacy backends are driven via
    :func:`fetch`, which translates it to the two-kwarg ``query`` call.

    ``cid`` + ``window`` are the **live read path**: the engine builds ``QuerySpec(cid, window)``
    (see ``engine/gate.py`` + ``engine/verify.py``) and every shipped backend honours them.
    ``limit`` / ``cursor`` / ``where`` are **reserved, forward-compat** fields — no shipped
    backend implements ``query_spec`` yet, so :func:`fetch` *drops* them on the legacy ``query``
    path. The engine must therefore never rely on server-side paging/filter a legacy driver
    can't honour; ``where`` in particular is filtered in Python by design (dialect-neutral and
    injection-safe — see ``BackendCaps.supports_where``). Pushing any of the three down is a
    per-driver ``query_spec`` opt-in, not a default."""

    cid: str
    window: TimeWindow
    limit: int | None = None  # reserved: server-side row cap (query_spec opt-in only)
    cursor: str | None = None  # reserved: server-side paging cursor (query_spec opt-in only)
    where: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.cid, str) or not self.cid:
            raise ValueError("query spec cid must be a non-empty string")
        if not isinstance(self.window, TimeWindow):
            raise TypeError("query spec window must be a TimeWindow")
        if self.limit is not None and (
            isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit <= 0
        ):
            raise ValueError("query spec limit must be a positive integer or None")
        if self.cursor is not None and not isinstance(self.cursor, str):
            raise TypeError("query spec cursor must be text or None")
        if self.where is not None:
            if not isinstance(self.where, Mapping):
                raise TypeError("query spec where must be a mapping or None")
            object.__setattr__(self, "where", _freeze_public_value(self.where))


# ── capabilities: typed, not ad-hoc getattr ─────────────────────────────────────


@dataclass(frozen=True)
class BackendCaps:
    """What a backend can do, as data instead of scattered ``getattr`` probes.

    queryable:     has a read side (False = write-only, e.g. OTLP; strict verify impossible).
    paginates:     reads to completion across pages (so ``complete`` is meaningful).
    supports_where: can filter server-side (informational; ooptdd filters in Python anyway).
    write_only:    convenience inverse of ``queryable`` for call sites that read positively.
    independent:   the read side is a separate store the observed process cannot rewrite
                   in-memory — the "external judge" positioning claim, as data. memory (same
                   process) and jsonl (same-host, author-writable file) are NOT independent:
                   they prove gate mechanics, not arrival. Defaults False; adapters must affirm
                   independence explicitly.
    samples:       the store (or its ingest pipeline) SAMPLES — head/tail sampling, a
                   dropping BatchSpanProcessor, etc. A sampled store can prove SOME events
                   arrived but not cross-event causal claims: the evidence-tier ladder caps
                   store-derived rungs at ``arrived`` (external corroboration is untouched).
    query_visibility_delay_ms: the store's OFFICIALLY documented ingest-to-queryable lag
                   (its blind window). The poller never concludes ABSENT while the total
                   wait is still inside this window — the arrival-policy guard that keeps
                   ingestion lag from masquerading as a RED. 0 = immediately visible.
    """

    queryable: bool = True
    paginates: bool = False
    supports_where: bool = False
    write_only: bool = False
    independent: bool = False
    query_visibility_delay_ms: int = 0
    samples: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "queryable",
            "paginates",
            "supports_where",
            "write_only",
            "independent",
            "samples",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"backend capability {field_name} must be a bool")
        if (
            isinstance(self.query_visibility_delay_ms, bool)
            or not isinstance(self.query_visibility_delay_ms, int)
            or self.query_visibility_delay_ms < 0
        ):
            raise ValueError("query visibility delay must be non-negative integer milliseconds")


DEFAULT_CAPS = BackendCaps()


@runtime_checkable
class EventSink(Protocol):
    """Narrow write port for emit-only consumers."""

    def ship(self, events: list[dict]) -> None:
        """Write a batch of event envelopes."""
        ...


@runtime_checkable
class EventSource(Protocol):
    """Narrow read port for verification consumers."""

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        """Read events for one correlation id and time window."""
        ...


@runtime_checkable
class FlushPort(Protocol):
    """Optional durability/visibility boundary exposed by some adapters."""

    def force_flush(self) -> bool:
        """Make accepted writes visible when supported."""
        ...


@runtime_checkable
class Backend(EventSink, EventSource, Protocol):
    """Minimal driver contract. Implement these two methods; that's a backend.

    A driver MAY additionally expose ``caps: BackendCaps`` and/or ``query_spec(spec)`` for the
    typed surface — both optional and read via :func:`backend_caps` / :func:`fetch`, so a
    plain two-method backend still structurally satisfies this Protocol.
    """

    #: Per-backend polling hints (seconds). Stores with slow ingest override these.
    default_lookback_s: int
    default_future_buffer_s: int
    #: False iff the backend has no read side (e.g. OTLP/otel is write-only). The verify
    #: layer cannot confirm arrival on such a backend, so `strict` over it is meaningless —
    #: callers surface that loudly rather than passing silently. Read via :func:`backend_caps`.
    queryable: bool = True


def backend_caps(backend) -> BackendCaps:
    """The single place capability logic lives: a backend's ``caps`` if it has one, else
    synthesized conservatively from the legacy ``queryable`` attribute. Missing capability
    metadata never implies an independent store; a backend must affirm that explicitly.
    Bridges old and new drivers so the engine never hand-rolls capability defaults again."""
    caps = getattr(backend, "caps", None)
    if isinstance(caps, BackendCaps):
        return caps
    queryable = getattr(backend, "queryable", True)
    return BackendCaps(queryable=queryable, write_only=not queryable)


def backend_identity(backend) -> str:
    """A best-effort, framework-DERIVED identity for WHERE a backend reads/writes — the basis for
    emit provenance (``oracle.emit_identity``) and for demoting an ``external:`` probe that re-reads
    this very endpoint (its ``separate_source`` claim then cannot be honest). Prefers a
    driver's own ``identity()``; else a captured ``endpoint``/``base_url`` attribute; else the
    class name (an in-process backend like memory has no endpoint to compare, so
    relocation *through* it is out of scope). This is NOT a security boundary — a backend or probe
    can still misreport — it only makes the common, honest cases comparable so a provably-same
    endpoint can be caught. Shared lineage or a colluding source cannot be discharged
    here; this function can only surface identities reported by the participants."""
    ident = getattr(backend, "identity", None)
    if callable(ident):
        try:
            got = ident()
        except Exception:  # noqa: BLE001 — identity is best-effort; it must never break a verdict
            got = None
        if got:
            return sanitize_endpoint_identity(str(got))
    for attr in ("endpoint", "base_url"):
        endpoint = getattr(backend, attr, None)
        if endpoint:
            return sanitize_endpoint_identity(str(endpoint))
    return type(backend).__name__


def sanitize_endpoint_identity(value: str) -> str:
    """Strip credentials and secret parameters from a stable URL identity.

    Non-URL identities such as ``jsonl:/path`` and class names are preserved.
    """

    normalized = value.rstrip("/")
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return normalized
    if not parsed.netloc:
        return normalized
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def fetch_all_pages(backend, spec: QuerySpec, *, max_rows: int | None = None) -> QueryResult:
    """Walk a ``query_spec`` backend's bounded pages (``spec.limit`` per page) to
    completion by following ``next_cursor``, concatenating events. ``max_rows`` is the
    runaway guard: hitting it returns ``complete=False`` with the unconsumed cursor —
    surfaced, never silent (the same honesty law as the drivers' internal caps).
    Requires a driver that implements ``query_spec`` (raises TypeError otherwise) —
    the legacy two-method contract has no paging to walk."""
    query_spec = getattr(backend, "query_spec", None)
    if not callable(query_spec):
        raise TypeError(
            f"{type(backend).__name__} does not implement query_spec — "
            "fetch_all_pages needs the paged read surface"
        )
    events: list[dict] = []
    cursor = spec.cursor
    while True:
        page = query_spec(replace(spec, cursor=cursor))
        if not page.reachable:
            return QueryResult(
                reachable=False,
                events=events,
                complete=False,
                error=page.error,
                error_kind=page.error_kind,
                retry_after_s=page.retry_after_s,
            )
        events.extend(page.events)
        cursor = page.next_cursor
        if cursor is None:
            return QueryResult(
                reachable=True, events=events, complete=page.complete, error=page.error
            )
        if max_rows is not None and len(events) >= max_rows:
            return QueryResult(
                reachable=True, events=events[:max_rows], complete=False, next_cursor=cursor
            )


def fetch(backend, spec: QuerySpec, clock: Clock | None = None) -> QueryResult:
    """Read a backend through one typed entry point regardless of its generation: use
    ``query_spec(spec)`` if the driver implements it, else translate the :class:`QuerySpec`
    into the legacy ``query(cid, since_us=, until_us=)`` call. This shim lets every
    existing backend keep working while the engine speaks ``QuerySpec``."""
    query_spec = getattr(backend, "query_spec", None)
    if callable(query_spec):
        return query_spec(spec)
    return backend.query(spec.cid, since_us=spec.window.since_us, until_us=spec.window.until_us)


# ── the independent-oracle port (breaks self-consistency) ───────────────────────
# Every other verdict input is the system's own emit, read back from a store the system writes —
# so a green proves self-CONSISTENCY, not correctness. An ExternalProbe is the one input that does
# NOT come from the trace: it reads a fact from the TERRITORY (a DB row, a file, a second
# collector). An `external:` gate check asserts against it, so a green there means more than the
# system agreeing with itself. It mirrors QueryResult's honesty fields so the engine treats a
# missing probe as a loud misconfiguration (never a silent green) and an unreachable probe as
# inconclusive (never a strict fail) — extending, not bypassing, the reachable/complete lattice.


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one external-state probe.

    reachable: True iff the probe round-trip succeeded (regardless of value). False -> `?`.
    value:     the external fact, or None if absent / not extracted. The functional gate
               captures JSON-like mappings/containers plus immutable Decimal, Fraction,
               date, UUID, and PurePath values. Adapters must normalize other domain objects;
               unsupported values fail loudly instead of retaining a mutable alias.
    complete:  True iff the probe read the full fact (no truncation).
    """

    reachable: bool
    value: object = None
    complete: bool = True
    #: The probe author's DECLARATION that the fact comes from a genuinely separate source of
    #: truth (a different store/service/filesystem), not the same store the system wrote — only a
    #: separate_source=True probe counts as independent CORROBORATION (closes the relocation hole:
    #: a probe re-reading the system's own store is self-consistency moved one layer out). ooptdd
    #: trusts this declaration; it cannot itself prove a source is independent.
    separate_source: bool = False
    #: A framework-COMPARABLE identity for WHERE this fact was actually read (a path / URL / DSN).
    #: Unlike ``separate_source`` (a bare claim), this is a value the engine can check: if it equals
    #: the emit backend's identity, the probe demonstrably re-read the system's own endpoint, so the
    #: ``separate_source`` claim is provably false and is DEMOTED (relocation, not independence).
    #: The demotion is ASYMMETRIC — a derived identity can only FALSIFY a declared True, never
    #: promote a missing one — so a genuinely-separate source whose identity the probe cannot
    #: report (``None``) keeps its declared bool. Reference probes (file/http) report it.
    derived_identity: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("reachable", "complete", "separate_source"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"probe result {field_name} must be a bool")
        if self.derived_identity is not None and not isinstance(self.derived_identity, str):
            raise TypeError("probe result derived_identity must be text or None")
        object.__setattr__(self, "value", _freeze_public_value(self.value))


@runtime_checkable
class ExternalProbe(Protocol):
    """The independent-oracle port. Optional everywhere (default None): the engine treats a
    missing probe as ``no_external_probe_configured`` (loud, never a silent green) and an
    unreachable probe as inconclusive."""

    def probe(self, kind: str, selector: object, cid: str) -> ProbeResult:
        """Resolve ``(kind, selector)`` for ``cid`` to a :class:`ProbeResult`. ``kind`` names the
        fact family the probe understands (``db_row`` / ``file`` / ``http`` / …); ``selector`` is
        the probe-specific locator — NOT the system's own emitted event."""
        ...


#: A sleeper is the injectable counterpart to the Clock for the retry loop's waits.
Sleeper = Callable[[float], None]
