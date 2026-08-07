"""OpenObserve backend — the reference network driver.

Writes events to an OpenObserve stream (``POST /api/{org}/{stream}/_json``) and
reads them back with SQL (``POST /api/{org}/_search``). SQL is first-class here:
"count events where cid = X in a time window" maps cleanly onto it.

Configuration is supplied explicitly as constructor values or a captured environment
mapping. The backend never reads the ambient process environment.
"""

from __future__ import annotations

import base64
import json
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


def _decode_hits(raw: bytes) -> list[dict]:
    """Decode one search page, rejecting shape drift instead of treating it as no hits."""

    payload = json.loads(raw.decode())
    if not isinstance(payload, dict) or "hits" not in payload:
        raise ValueError("OpenObserve response must be an object with a hits array")
    hits = payload["hits"]
    if not isinstance(hits, list) or any(not isinstance(hit, dict) for hit in hits):
        raise ValueError("OpenObserve response hits must be an array of objects")
    return hits


class OpenObserveBackend:
    #: reads to completion across pages (the query loop below) over SQL — the
    #: reference network store: an independent, queryable, complete-read judge.
    #: Blind window: memtable/WAL persist path (ZO_MEM_PERSIST_INTERVAL default 5s) —
    #: a just-ingested record may be invisible to /_search for up to that interval.
    caps = BackendCaps(
        queryable=True,
        paginates=True,
        supports_where=True,
        independent=True,
        query_visibility_delay_ms=5000,
    )
    default_lookback_s = DEFAULT_BACKEND_SETTINGS.lookback_s
    default_future_buffer_s = DEFAULT_BACKEND_SETTINGS.future_buffer_s
    queryable = True  # SQL read side over /_search

    def __init__(
        self,
        *,
        stream: str = "events",
        org: str | None = None,
        base_url: str | None = None,
        url_env: str | None = None,
        user_env: str | None = None,
        password_env: str | None = None,
        timeout: float | None = None,
        page_size: int | None = None,
        max_rows: int | None = None,
        opener=None,
        service: str | None = None,
        environment: Mapping[str, str] | None = None,
        env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
        settings: BackendSettings = DEFAULT_BACKEND_SETTINGS,
    ):
        captured = {} if environment is None else dict(environment)
        self.url_env = url_env or env_keys.openobserve_url
        self.user_env = user_env or env_keys.openobserve_user
        self.password_env = password_env or env_keys.openobserve_password
        self.stream = stream
        self.org: str = org or captured.get(env_keys.openobserve_org) or "default"
        self.base_url = (base_url or captured.get(self.url_env, "")).rstrip("/")
        self._user = captured.get(self.user_env, "root")
        self._password = captured.get(self.password_env)
        self.service = service
        if not isinstance(settings, BackendSettings):
            raise TypeError("settings must be a BackendSettings value")
        self.default_lookback_s = settings.lookback_s
        self.default_future_buffer_s = settings.future_buffer_s
        self.timeout = settings.timeout(timeout)
        # Read-back is paged to completion (offset/size), never silently capped at one
        # page. `max_rows` is only a runaway guard for a pathologically huge cid; hitting
        # it sets QueryResult.truncated so the verdict layer refuses a clean pass rather
        # than undercounting in silence.
        self.page_size = settings.page_limit(page_size)
        self.max_rows = settings.row_limit(max_rows)
        if self.page_size > self.max_rows:
            raise ValueError("page_size must not exceed max_rows")
        # opener(request, timeout) injection lets tests exercise this driver
        # without a network.
        self._open = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))

    def _endpoint(self) -> tuple[str, str, str]:
        if not self.base_url:
            raise ValueError(
                f"{self.url_env} is required for the openobserve backend "
                f"(e.g. {self.url_env}=http://<host>:5080). No baked default."
            )
        if not self._password:
            raise ValueError(f"{self.password_env} is required (env-only secret).")
        auth = "Basic " + base64.b64encode(f"{self._user}:{self._password}".encode()).decode()
        return self.base_url, self.org, auth

    def identity(self) -> str:
        return sanitize_endpoint_identity(self.base_url) if self.base_url else type(self).__name__

    def ship(self, events: list[dict]) -> None:
        if not events:
            return
        base, org, auth = self._endpoint()
        req = urllib.request.Request(
            f"{base}/api/{org}/{self.stream}/_json",
            data=json.dumps(events).encode(),
            method="POST",
            headers={"Authorization": auth, "Content-Type": "application/json"},
        )
        with self._open(req, timeout=self.timeout) as r:
            # Surface a non-2xx so a dropped ingest is a *loud* ship failure (caught by the
            # caller as a warning), not a silent success.
            _raise_for_status(r)

    def query_spec(self, spec) -> QueryResult:
        """Typed read surface. Without ``limit``/``cursor`` this IS the live engine
        path — it delegates to the read-to-completion :meth:`query` byte-identically.
        With ``limit`` it serves ONE bounded page (opaque offset cursor in
        ``next_cursor``; ``complete=False`` while more pages remain) — the opt-in
        surface external consumers page with (see ``fetch_all_pages``)."""
        if spec.limit is None and spec.cursor is None:
            return self.query(
                spec.cid, since_us=spec.window.since_us, until_us=spec.window.until_us
            )
        try:
            base, org, auth = self._endpoint()
        except ValueError as exc:
            return QueryResult(reachable=False, error=f"{type(exc).__name__}: {exc}")
        try:
            offset = int(spec.cursor or 0)
        except ValueError:
            raise ValueError(
                f"openobserve cursor must be a decimal offset, got {spec.cursor!r}"
            ) from None
        size = int(spec.limit or self.page_size)
        safe_cid = spec.cid.replace("'", "''")
        sql = f"SELECT * FROM {self.stream} WHERE cid = '{safe_cid}'"
        body = json.dumps(
            {
                "query": {
                    "sql": sql,
                    "start_time": spec.window.since_us,
                    "end_time": spec.window.until_us,
                    "from": offset,
                    "size": size,
                }
            }
        ).encode()
        req = urllib.request.Request(
            f"{base}/api/{org}/_search",
            data=body,
            method="POST",
            headers={"Authorization": auth, "Content-Type": "application/json"},
        )
        try:
            with self._open(req, timeout=self.timeout) as r:
                _raise_for_status(r)
                hits = _decode_hits(r.read())
        except Exception as exc:
            kind, retry_after = classify_http_error(exc)
            return QueryResult(
                reachable=False,
                error=f"{type(exc).__name__}: {exc}",
                error_kind=kind,
                retry_after_s=retry_after,
            )
        for i, h in enumerate(hits):
            h["_seq"] = offset + i  # GLOBAL position, so cross-page ordering keys stay total
        next_cursor = str(offset + len(hits)) if len(hits) == size else None
        return QueryResult(
            reachable=True, events=hits, complete=next_cursor is None, next_cursor=next_cursor
        )

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        try:
            base, org, auth = self._endpoint()
        except ValueError as exc:
            return QueryResult(reachable=False, error=f"{type(exc).__name__}: {exc}")
        # SELECT * so whole rows come back: arbitrary fields (verdict, level, …) for gate
        # `where:` filters and `_timestamp` for `must_order`. The cid is a single-quoted SQL
        # string literal, so it is escaped by doubling embedded quotes — it can never break
        # out of the literal (injection-safe), matching the parameterized ClickHouse driver.
        safe_cid = cid.replace("'", "''")
        sql = f"SELECT * FROM {self.stream} WHERE cid = '{safe_cid}'"
        events: list[dict] = []
        offset = 0
        complete = True
        # Page to completion: OpenObserve caps a single response, so a cid with more events
        # than one page would otherwise be SILENTLY undercounted — the exact silent loss
        # this tool exists to catch. Loop on offset until a short page (no more rows).
        while True:
            body = json.dumps(
                {
                    "query": {
                        "sql": sql,
                        "start_time": since_us,
                        "end_time": until_us,
                        "from": offset,
                        "size": self.page_size,
                    }
                }
            ).encode()
            req = urllib.request.Request(
                f"{base}/api/{org}/_search",
                data=body,
                method="POST",
                headers={"Authorization": auth, "Content-Type": "application/json"},
            )
            try:
                with self._open(req, timeout=self.timeout) as r:
                    # A non-2xx search is a loud failure, not "0 hits": without this an error
                    # body lacking a `hits` key would read as an empty result set → a false
                    # `absent` (reachable=True, 0 events). Mirrors ship()'s _raise_for_status.
                    _raise_for_status(r)
                    hits = _decode_hits(r.read())
            except Exception as exc:
                # A failure mid-paging means we don't have the complete set: if we already
                # have some rows it's an incomplete read, else fully unreachable.
                err = f"{type(exc).__name__}: {exc}"
                kind, retry_after = classify_http_error(exc)
                if offset == 0:
                    return QueryResult(
                        reachable=False, error=err, error_kind=kind, retry_after_s=retry_after
                    )
                return QueryResult(
                    reachable=True,
                    events=events,
                    complete=False,
                    error=err,
                    error_kind=kind,
                    retry_after_s=retry_after,
                )
            for h in hits:
                h["_seq"] = len(events)  # deterministic tie-break: preserve server return order
                events.append(h)
            if len(hits) < self.page_size:
                break  # short page → the result set is exhausted (complete read)
            offset += len(hits)
            if offset >= self.max_rows:
                complete = False  # runaway guard hit — surfaced, never silent
                break
        return QueryResult(reachable=True, events=events, complete=complete)
