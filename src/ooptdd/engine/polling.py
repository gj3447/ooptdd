"""Pure helpers for the arrival polling policy.

The engine accepts the immutable :class:`PollingSettings` value as its narrow
policy interface.  Scalar keyword arguments remain as a migration surface for
existing callers, but they are resolved here once instead of being defaulted
independently by every entry point.
"""

from __future__ import annotations

from dataclasses import replace

from ..domain.settings import DEFAULT_POLLING, PollingSettings


def resolve_polling_settings(
    polling: PollingSettings | None = None,
    *,
    retries: int | None = None,
    delay: float | None = None,
    backoff: float | None = None,
    max_delay: float | None = None,
    confirm_rounds: int | None = None,
    confirm_delay_s: float | None = None,
) -> PollingSettings:
    """Return one validated policy from a policy object plus scalar overrides.

    ``polling`` is the preferred API.  Explicit legacy scalar values override
    it, while omitted values inherit from that object (or ``DEFAULT_POLLING``).
    The input object is never mutated.
    """

    base = polling or DEFAULT_POLLING
    return replace(
        base,
        retries=base.retries if retries is None else retries,
        delay=base.delay if delay is None else delay,
        backoff=base.backoff if backoff is None else backoff,
        max_delay=base.max_delay if max_delay is None else max_delay,
        confirm_rounds=(base.confirm_rounds if confirm_rounds is None else confirm_rounds),
        confirm_delay_s=(base.confirm_delay_s if confirm_delay_s is None else confirm_delay_s),
    )


def next_poll_delay(
    polling: PollingSettings,
    completed_attempt: int,
    *,
    retry_after_s: float | None = None,
) -> float:
    """Compute the bounded delay before the next attempt, without side effects."""

    pause = min(
        polling.delay * polling.backoff ** max(completed_attempt - 1, 0),
        polling.max_delay,
    )
    if retry_after_s is not None:
        pause = max(pause, float(retry_after_s))
    return pause


__all__ = ["next_poll_delay", "resolve_polling_settings"]
