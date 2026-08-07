"""VictoriaLogs backend — a schema-free log store driver.

Writes events as JSON lines (``POST /insert/jsonline``) and reads them back with
LogsQL (``GET /select/logsql/query``). VictoriaLogs is Apache-2.0, schema-free, and
filters arbitrary fields cheaply by correlation id — a clean fit for ooptdd's
"fetch every event for this cid in a window" read pattern.

Configuration is supplied explicitly as constructor values or a captured environment
mapping. The backend never reads the ambient process environment. ``cid`` is the
default stream field used for efficient exact-match queries.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime

from ..domain.settings import DEFAULT_ENV_KEYS, EnvironmentKeys
from .base import (
    BackendCaps,
    QueryResult,
    _raise_for_status,
    classify_http_error,
    sanitize_endpoint_identity,
)
from .settings import DEFAULT_BACKEND_SETTINGS, BackendSettings


def _logsql_str(value: str) -> str:
    """Escape a value for a LogsQL double-quoted phrase (``field:="..."``): backslash first,
    then the double-quote. A cid containing a quote can then never break out of the filter
    or be silently mangled — the injection/breakage fix, mirroring ClickHouse's parameter
    binding and OpenObserve's quote-doubling."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_time_us(value) -> int | None:
    """Best-effort RFC3339 (VictoriaLogs ``_time``) -> epoch microseconds. Returns None
    on anything unparseable, so a single odd row never breaks must_order ordering."""
    if not isinstance(value, str) or not value:
        return None
    s = value.replace("Z", "+00:00")
    # fromisoformat rejects nanoseconds; truncate the fractional part to 6 digits.
    if "." in s:
        head, _, tail = s.partition(".")
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                rest = tail[i:]
                break
        s = f"{head}.{frac[:6]}{rest}"
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1_000_000)
    except ValueError:
        return None


class VictoriaLogsBackend:
    #: single LogsQL read; exact-field filter server-side, no paging loop.
    #: Blind window: ingested data becomes searchable within ~1s (docs), and the docs
    #: recommend POST /internal/force_flush for automated tests — see force_flush().
    caps = BackendCaps(
        queryable=True,
        paginates=False,
        supports_where=True,
        independent=True,
        query_visibility_delay_ms=1000,
    )
    default_lookback_s = DEFAULT_BACKEND_SETTINGS.lookback_s
    default_future_buffer_s = DEFAULT_BACKEND_SETTINGS.future_buffer_s
    queryable = True  # LogsQL read side over /select/logsql/query

    def __init__(
        self,
        *,
        base_url: str | None = None,
        url_env: str | None = None,
        user_env: str | None = None,
        password_env: str | None = None,
        stream_field: str = "cid",
        timeout: float | None = None,
        max_rows: int | None = None,
        opener=None,
        service: str | None = None,
        environment: Mapping[str, str] | None = None,
        env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
        settings: BackendSettings = DEFAULT_BACKEND_SETTINGS,
    ):
        captured = {} if environment is None else dict(environment)
        self.url_env = url_env or env_keys.victorialogs_url
        self.user_env = user_env or env_keys.victorialogs_user
        self.password_env = password_env or env_keys.victorialogs_password
        self.base_url = (base_url or captured.get(self.url_env, "")).rstrip("/")
        self._user = captured.get(self.user_env, "")
        self._password = captured.get(self.password_env)
        self.stream_field = stream_field
        self.service = service
        if not isinstance(settings, BackendSettings):
            raise TypeError("settings must be a BackendSettings value")
        self.default_lookback_s = settings.lookback_s
        self.default_future_buffer_s = settings.future_buffer_s
        self.timeout = settings.timeout(timeout)
        # LogsQL streams all matches; this bounds how many we ingest so a pathological cid
        # can't OOM. Exceeding it surfaces complete=False rather than silently dropping rows.
        self.max_rows = settings.row_limit(max_rows)
        # opener(request, timeout) injection lets tests exercise this driver offline.
        self._open = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))

    def _base(self) -> str:
        if not self.base_url:
            raise ValueError(
                f"{self.url_env} is required for the victorialogs backend "
                f"(e.g. {self.url_env}=http://<host>:9428). No baked default."
            )
        return self.base_url

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/x-ndjson"}
        if self._password:  # auth is optional — VictoriaLogs is often run without it
            headers["Authorization"] = (
                "Basic " + base64.b64encode(f"{self._user}:{self._password}".encode()).decode()
            )
        return headers

    def identity(self) -> str:
        return sanitize_endpoint_identity(self.base_url) if self.base_url else type(self).__name__

    def ship(self, events: list[dict]) -> None:
        if not events:
            return
        base = self._base()
        # JSON lines; cid becomes a stream field and event becomes _msg.
        body = "\n".join(json.dumps(e) for e in events).encode()
        params = urllib.parse.urlencode(
            {"_stream_fields": self.stream_field, "_msg_field": "event"}
        )
        req = urllib.request.Request(
            f"{base}/insert/jsonline?{params}",
            data=body,
            method="POST",
            headers=self._headers(),
        )
        with self._open(req, timeout=self.timeout) as r:
            _raise_for_status(r)  # a dropped ingest must be a loud ship failure, not silent

    def force_flush(self) -> bool:
        """``POST /internal/force_flush`` — the endpoint VictoriaLogs documents for making
        just-ingested data searchable in automated tests. Best-effort: the poller treats a
        failure as "not flushed", never as a verdict."""
        req = urllib.request.Request(
            f"{self._base()}/internal/force_flush",
            data=b"",
            method="POST",
            headers=self._headers(),
        )
        with self._open(req, timeout=self.timeout) as r:
            _raise_for_status(r)
            getattr(r, "read", lambda: b"")()
        return True

    def query_spec(self, spec) -> QueryResult:
        """Typed read surface — **limit-only** by design. LogsQL streams every match
        with no paging primitive, so there is no cursor to synthesize honestly: a
        ``cursor`` is refused loudly rather than faked, and a FILLED limit reports
        ``complete=False`` (there may be more rows, unknowably). Without limit or
        cursor this delegates to the read-to-completion :meth:`query`."""
        if spec.cursor is not None:
            raise ValueError(
                "victorialogs has no paging cursor (LogsQL streams all matches); "
                "use limit-only query_spec, or the read-to-completion query()"
            )
        if spec.limit is None:
            return self.query(
                spec.cid, since_us=spec.window.since_us, until_us=spec.window.until_us
            )
        return self._read(
            spec.cid, spec.window.since_us, spec.window.until_us, limit=int(spec.limit)
        )

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        """Read to completion (the legacy two-method contract, unchanged)."""
        return self._read(cid, since_us, until_us)

    def _read(
        self, cid: str, since_us: int, until_us: int, limit: int | None = None
    ) -> QueryResult:
        try:
            base = self._base()
        except ValueError as exc:
            return QueryResult(reachable=False, error=f"{type(exc).__name__}: {exc}")
        # LogsQL: exact field match on the correlation id. start/end are unix seconds
        # (VictoriaLogs accepts fractional). SELECT-* equivalent: no field projection, so
        # whole rows come back for the Python-side gate `where:` filters.
        logsql = f'{self.stream_field}:="{_logsql_str(cid)}"'
        if limit is not None:
            logsql += f" | limit {int(limit)}"  # LogsQL pipe: the only bound it offers
        params = urllib.parse.urlencode(
            {
                "query": logsql,
                "start": f"{since_us / 1_000_000:.6f}",
                "end": f"{until_us / 1_000_000:.6f}",
            }
        )
        headers = {k: v for k, v in self._headers().items() if k == "Authorization"}
        req = urllib.request.Request(
            f"{base}/select/logsql/query?{params}", method="GET", headers=headers
        )
        try:
            with self._open(req, timeout=self.timeout) as r:
                _raise_for_status(r)
                payload = r.read().decode()
        except Exception as exc:
            kind, retry_after = classify_http_error(exc)
            return QueryResult(
                reachable=False,
                error=f"{type(exc).__name__}: {exc}",
                error_kind=kind,
                retry_after_s=retry_after,
            )
        events: list[dict] = []
        complete = True
        for line in payload.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                return QueryResult(
                    reachable=True,
                    events=events,
                    complete=False,
                    error="ValueError: VictoriaLogs response contains malformed JSON",
                )
            if not isinstance(row, dict):
                return QueryResult(
                    reachable=True,
                    events=events,
                    complete=False,
                    error="ValueError: VictoriaLogs response rows must be objects",
                )
            if "_timestamp" not in row:
                ts = _parse_time_us(row.get("_time"))
                if ts is not None:
                    row["_timestamp"] = ts
            row["_seq"] = len(events)  # deterministic tie-break: preserve server return order
            events.append(row)
            if len(events) >= self.max_rows:
                complete = False  # ceiling hit — surfaced, never a silent subset
                break
        if limit is not None and len(events) >= limit:
            # a FILLED limit proves nothing about what lies beyond it, and LogsQL has no
            # cursor to ask with — so the read is honestly incomplete, never "exhausted".
            complete = False
        return QueryResult(reachable=True, events=events, complete=complete)
