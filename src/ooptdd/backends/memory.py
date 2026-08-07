"""Instance-scoped in-memory backend.

Each backend owns a fresh store by default.  Callers that intentionally need several
backend handles to share state inject the same :class:`MemoryStore`; sharing is never a
process-global side effect.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock

from ..domain.settings import DEFAULT_ENV_KEYS, EnvironmentKeys
from .base import BackendCaps, QueryResult
from .settings import DEFAULT_LOCAL_BACKEND_SETTINGS, BackendSettings


@dataclass
class MemoryStore:
    """Mutable storage explicitly owned by a composition root or a backend instance."""

    _records: dict[str, list[tuple[int, int, dict]]] = field(
        default_factory=dict, init=False, repr=False
    )
    _next_sequence: int = field(default=0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def append(self, events: list[dict]) -> None:
        if not events:
            return
        if not all(isinstance(event, dict) for event in events):
            raise TypeError("events must contain mappings")
        captured = [deepcopy(event) for event in events]
        stored_us = int(time.time() * 1_000_000)
        with self._lock:
            for event in captured:
                sequence = self._next_sequence
                self._next_sequence += 1
                cid = event.get("cid") or event.get("correlation_id") or ""
                stored = event if "_emit_seq" in event else {**event, "_emit_seq": sequence}
                self._records.setdefault(cid, []).append((stored_us, sequence, stored))

    def read(self, cid: str, *, since_us: int, until_us: int) -> tuple[dict, ...]:
        with self._lock:
            records = tuple(self._records.get(cid, ()))
        return tuple(
            {**event, "_timestamp": stored_us, "_seq": sequence}
            for stored_us, sequence, event in records
            if since_us <= stored_us <= until_us
        )

    def clear(self) -> None:
        """Remove all records and restart ordering within this explicitly owned store."""

        with self._lock:
            self._records.clear()
            self._next_sequence = 0


def reset(target: MemoryStore | MemoryBackend | None = None) -> None:
    """Clear an explicitly supplied store or backend.

    With no target this is a compatibility no-op: fresh backends are already isolated.
    Supplying a target clears only that explicitly owned state.
    """

    if target is None:
        return
    store = target.store if isinstance(target, MemoryBackend) else target
    if not isinstance(store, MemoryStore):
        raise TypeError("reset target must be a MemoryStore or MemoryBackend")
    store.clear()


class MemoryBackend:
    """Queryable backend with explicit ownership and deterministic insertion order."""

    default_lookback_s = DEFAULT_LOCAL_BACKEND_SETTINGS.lookback_s
    default_future_buffer_s = DEFAULT_LOCAL_BACKEND_SETTINGS.future_buffer_s
    queryable = True
    caps = BackendCaps(
        queryable=True,
        paginates=False,
        supports_where=True,
        independent=False,
    )

    def __init__(
        self,
        *,
        drop: bool = False,
        service: str | None = None,
        store: MemoryStore | None = None,
        environment: Mapping[str, str] | None = None,
        env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
        settings: BackendSettings = DEFAULT_LOCAL_BACKEND_SETTINGS,
    ):
        if not isinstance(drop, bool):
            raise TypeError("drop must be a bool")
        if store is not None and not isinstance(store, MemoryStore):
            raise TypeError("store must be a MemoryStore")
        if not isinstance(settings, BackendSettings):
            raise TypeError("settings must be a BackendSettings value")
        self.drop = drop
        self.service = service
        self.store = store if store is not None else MemoryStore()
        self.default_lookback_s = settings.lookback_s
        self.default_future_buffer_s = settings.future_buffer_s
        # Accepted uniformly at the built-in factory boundary. This backend has no
        # environment-derived options, so it deliberately retains neither value.
        del environment, env_keys

    def ship(self, events: list[dict]) -> None:
        if self.drop:
            return
        self.store.append(events)

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        return QueryResult(
            reachable=True,
            events=self.store.read(cid, since_us=since_us, until_us=until_us),
        )


__all__ = ("MemoryBackend", "MemoryStore", "reset")
