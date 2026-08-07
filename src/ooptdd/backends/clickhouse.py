"""ClickHouse backend using the HTTP interface.

OTLP *write* is portable while *read* remains backend-specific. Of the common
read dialects, SQL ports cleanly across several stores. ClickHouse is a strong
fit for count/cardinality-with-filter contracts and is
**Apache-2.0** (no AGPL exposure, unlike OpenObserve/Loki). A self-hosted SigNoz
exposes the same ClickHouse tables, so this driver doubles as the SigNoz driver.

It speaks the ClickHouse **HTTP interface** and, like the OpenObserve driver,
selects whole rows (``SELECT *``) so the smart filtering (``where``, ``must_order``,
``present``, counts) stays in Python — identical for every backend. The query is a
**parameterized** statement (``{cid:String}``) so the correlation id can never break
out into SQL.

Configuration is supplied explicitly as constructor values or a captured environment
mapping. The backend never reads the ambient process environment.

Expected table (the consumer owns DDL; documented, not created here)::

    CREATE TABLE events (
      cid        String,
      event      String,
      _timestamp DateTime64(6) DEFAULT now64(6),
      data       String                                  -- the full JSON envelope
    ) ENGINE = MergeTree ORDER BY (cid, _timestamp);
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Mapping

from ..domain.settings import DEFAULT_ENV_KEYS, EnvironmentKeys
from .base import (
    BackendCaps,
    QueryResult,
    _raise_for_status,
    classify_http_error,
    sanitize_endpoint_identity,
)
from .settings import DEFAULT_BACKEND_SETTINGS, BackendSettings


class ClickHouseBackend:
    #: single bounded read (LIMIT max_rows+1 sentinel -> honest `complete`), no paging.
    #: Blind window: async_insert buffering (async_insert_busy_timeout_ms band, 200-1000ms)
    #: — a just-inserted row may not be SELECTable until the buffer flushes.
    caps = BackendCaps(
        queryable=True,
        paginates=False,
        supports_where=True,
        independent=True,
        query_visibility_delay_ms=1000,
    )
    default_lookback_s = DEFAULT_BACKEND_SETTINGS.lookback_s
    default_future_buffer_s = DEFAULT_BACKEND_SETTINGS.future_buffer_s

    def __init__(
        self,
        *,
        table: str = "events",
        database: str | None = None,
        base_url: str | None = None,
        url_env: str | None = None,
        user_env: str | None = None,
        password_env: str | None = None,
        timeout: float | None = None,
        max_rows: int | None = None,
        opener=None,
        service: str | None = None,
        environment: Mapping[str, str] | None = None,
        env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
        settings: BackendSettings = DEFAULT_BACKEND_SETTINGS,
    ):
        captured = {} if environment is None else dict(environment)
        self.url_env = url_env or env_keys.clickhouse_url
        self.user_env = user_env or env_keys.clickhouse_user
        self.password_env = password_env or env_keys.clickhouse_password
        self.table = table
        self.database = database or captured.get(env_keys.clickhouse_database, "default")
        self.base_url = (base_url or captured.get(self.url_env, "")).rstrip("/")
        self._user = captured.get(self.user_env, "default")
        self._password = captured.get(self.password_env)
        self.service = service
        if not isinstance(settings, BackendSettings):
            raise TypeError("settings must be a BackendSettings value")
        self.default_lookback_s = settings.lookback_s
        self.default_future_buffer_s = settings.future_buffer_s
        self.timeout = settings.timeout(timeout)
        # Bound a single read so an unbounded SELECT can't OOM; exceeding it surfaces
        # complete=False (incomplete evidence) instead of silently returning a subset.
        self.max_rows = settings.row_limit(max_rows)
        # opener(request, timeout) injection lets tests exercise this driver with no network.
        self._open = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))

    def _base(self) -> str:
        if not self.base_url:
            raise ValueError(
                f"{self.url_env} is required for the clickhouse backend "
                f"(e.g. {self.url_env}=http://<host>:8123). No baked default."
            )
        return self.base_url

    def _headers(self) -> dict:
        h = {}
        h["X-ClickHouse-User"] = self._user
        if self._password:
            h["X-ClickHouse-Key"] = self._password
        return h

    def identity(self) -> str:
        return sanitize_endpoint_identity(self.base_url) if self.base_url else type(self).__name__

    def _post(self, params: dict, body: bytes, headers: dict):
        url = f"{self._base()}/?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        return self._open(req, timeout=self.timeout)

    def ship(self, events: list[dict]) -> None:
        if not events:
            return
        # INSERT … FORMAT JSONEachRow: one JSON object per line. We carry the whole
        # envelope in `data` plus the two indexed columns the schema needs.
        rows = []
        for e in events:
            cid = e.get("cid") or e.get("correlation_id") or ""
            rows.append(
                json.dumps(
                    {"cid": cid, "event": e.get("event", ""), "data": json.dumps(e)},
                    ensure_ascii=False,
                )
            )
        body = (f"INSERT INTO {self.table} FORMAT JSONEachRow\n" + "\n".join(rows)).encode()
        headers = {**self._headers(), "Content-Type": "text/plain; charset=utf-8"}
        with self._post({"database": self.database}, body, headers) as r:
            _raise_for_status(r)  # a dropped INSERT must be a loud ship failure, not silent

    def query_spec(self, spec) -> QueryResult:
        """Typed read surface. Without ``limit``/``cursor`` this delegates to the
        read-to-completion :meth:`query`. With ``limit`` it serves ONE bounded page:
        ClickHouse has no cursor primitive, but ``LIMIT n OFFSET k`` over the driver's
        fixed ordering is exact, so the cursor is an opaque decimal offset."""
        if spec.limit is None and spec.cursor is None:
            return self.query(
                spec.cid, since_us=spec.window.since_us, until_us=spec.window.until_us
            )
        try:
            offset = int(spec.cursor or 0)
        except ValueError:
            raise ValueError(
                f"clickhouse cursor must be a decimal offset, got {spec.cursor!r}"
            ) from None
        limit = int(spec.limit or self.max_rows)
        return self._read(
            spec.cid, spec.window.since_us, spec.window.until_us, limit=limit, offset=offset
        )

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        """Read to completion (the legacy two-method contract, unchanged)."""
        return self._read(cid, since_us, until_us)

    def _read(
        self, cid: str, since_us: int, until_us: int, *, limit: int | None = None, offset: int = 0
    ) -> QueryResult:
        paged = limit is not None
        cap = self.max_rows if limit is None else limit
        try:
            base = self._base()  # noqa: F841 — validates config before the network call
        except ValueError as exc:
            return QueryResult(reachable=False, error=f"{type(exc).__name__}: {exc}")
        # SELECT * (whole rows) with a *parameterized* cid — injection-safe. Time-window
        # bounding is store-receive-stamped; the cid is the real discriminator (one cid per
        # run), matching the OpenObserve driver. FORMAT JSON yields {"data":[...]}.
        # LIMIT max_rows+1 bounds the read: if more than max_rows rows match we learn the set
        # is incomplete (surfaced as complete=False) instead of unbounded-loading or silently
        # returning a subset. The cid stays a parameter (injection-safe).
        sql = (
            f"SELECT * FROM {self.table} WHERE cid = {{cid:String}} "
            f"AND _timestamp >= fromUnixTimestamp64Micro({{since:Int64}}) "
            f"AND _timestamp <= fromUnixTimestamp64Micro({{until:Int64}}) "
            f"LIMIT {cap if paged else self.max_rows + 1}"
            f"{f' OFFSET {offset}' if paged else ''} FORMAT JSON"
        )
        params = {
            "database": self.database,
            "query": sql,
            "param_cid": cid,
            "param_since": since_us,
            "param_until": until_us,
            "default_format": "JSON",
        }
        headers = {**self._headers(), "Content-Type": "text/plain; charset=utf-8"}
        try:
            with self._post(params, b"", headers) as r:
                _raise_for_status(r)
                payload = json.loads(r.read().decode())
        except Exception as exc:
            kind, retry_after = classify_http_error(exc)
            return QueryResult(
                reachable=False,
                error=f"{type(exc).__name__}: {exc}",
                error_kind=kind,
                retry_after_s=retry_after,
            )
        if not isinstance(payload, dict) or "data" not in payload:
            return QueryResult(
                reachable=True,
                complete=False,
                error="ValueError: ClickHouse response must be an object with a data array",
            )
        data = payload["data"]
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            return QueryResult(
                reachable=True,
                complete=False,
                error="ValueError: ClickHouse response data must be an array of objects",
            )
        complete = len(data) <= cap
        events: list[dict] = []
        for row in data[:cap]:
            # rows carry the original envelope in `data`; unwrap it so `where`/counts see
            # the real fields. A row without `data` (custom schema) is passed through as-is.
            raw = row.get("data") if isinstance(row, dict) else None
            if isinstance(raw, str):
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    return QueryResult(
                        reachable=True,
                        events=events,
                        complete=False,
                        error="ValueError: ClickHouse row data must contain valid JSON",
                    )
                if not isinstance(ev, dict):
                    return QueryResult(
                        reachable=True,
                        events=events,
                        complete=False,
                        error="ValueError: ClickHouse row data must decode to an object",
                    )
            else:
                ev = dict(row)
            ev.setdefault("_timestamp", row.get("_timestamp") if isinstance(row, dict) else None)
            ev["_seq"] = offset + len(events)  # GLOBAL position: total across pages
            events.append(ev)
        next_cursor = str(offset + len(events)) if paged and len(events) == cap else None
        return QueryResult(
            reachable=True,
            events=events,
            complete=complete if not paged else next_cursor is None,
            next_cursor=next_cursor,
        )
