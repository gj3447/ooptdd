"""Backend abstraction — the portability seam (adapter side).

The driver *contract* — the :class:`Backend` Protocol and :class:`QueryResult` — is a
domain **port**, defined in :mod:`ooptdd.domain.ports` so the engine can depend on it
without depending on any concrete adapter. This module re-exports it for the adapter
layer: concrete drivers (memory, openobserve, otel, …) import ``Backend``/``QueryResult``
from here, and third-party drivers register under the ``ooptdd.backends`` entry-point group.

Drivers are discovered three ways, in order:
  1. built-ins (memory, openobserve, otel)
  2. the ``ooptdd.backends`` entry-point group (``pip install`` a 3rd-party driver)
  3. an explicit instance passed in code
"""
from __future__ import annotations

from ..domain.ports import (
    DEFAULT_CAPS,
    Backend,
    BackendCaps,
    Clock,
    QueryResult,
    QuerySpec,
    SystemClock,
    TimeWindow,
    backend_caps,
    fetch,
)

__all__ = [
    "Backend",
    "QueryResult",
    "BackendCaps",
    "DEFAULT_CAPS",
    "QuerySpec",
    "TimeWindow",
    "Clock",
    "SystemClock",
    "backend_caps",
    "fetch",
    "raise_for_status",
    "HTTPStatusError",
    "classify_http_error",
]


class HTTPStatusError(OSError):
    """A non-2xx backend response, carrying the status (and ``Retry-After`` when the
    store sent one) so drivers can classify without string-parsing the message."""

    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"backend returned HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _retry_after_s(headers) -> float | None:
    try:
        raw = headers.get("Retry-After") if headers is not None else None
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None  # an HTTP-date Retry-After (rare) is ignored, not crashed on


def classify_http_error(exc) -> tuple[str | None, float | None]:
    """``(error_kind, retry_after_s)`` for a query exception — the typed diagnosis
    behind :class:`~ooptdd.domain.ports.QueryResult.error_kind`. 429/503 are
    ``rate_limited`` (with ``Retry-After`` when present), 401/403 ``auth``,
    408/socket timeouts ``timeout``, anything else ``other``."""
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(exc, "code", None)  # urllib.error.HTTPError
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None:
        retry_after = _retry_after_s(getattr(exc, "headers", None))
    if status in (429, 503):
        return "rate_limited", retry_after
    if status in (401, 403):
        return "auth", None
    if status == 408 or isinstance(exc, TimeoutError):
        return "timeout", None
    import socket
    if isinstance(exc, socket.timeout):
        return "timeout", None
    return "other", None


def raise_for_status(response) -> None:
    """Raise if an HTTP response carries a non-2xx status, so a dropped ingest/read surfaces
    as a *loud* failure (the caller downgrades a ship failure to a warning; a query failure
    becomes ``reachable=False``) instead of being silently treated as success. Tolerates a
    response object with no status (e.g. a test's mock opener) by treating it as success —
    real ``urllib`` responses always carry one and also raise on 4xx/5xx themselves."""
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        try:
            status = response.getcode()
        except Exception:
            status = None
    if status is not None and status >= 400:
        raise HTTPStatusError(status, _retry_after_s(getattr(response, "headers", None)))


# module-private alias used by the driver modules
_raise_for_status = raise_for_status
