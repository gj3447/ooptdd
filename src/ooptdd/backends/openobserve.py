"""OpenObserve backend — the reference network driver.

Writes events to an OpenObserve stream (``POST /api/{org}/{stream}/_json``) and
reads them back with SQL (``POST /api/{org}/_search``). SQL is first-class here:
"count events where cid = X in a time window" maps cleanly onto it.

Configuration is **environment-only** — no URLs or credentials are ever baked
into code or config files (a published package must not ship someone's host).
Required: ``OOPTDD_OO_URL`` and ``OOPTDD_OO_PASSWORD``. Optional:
``OOPTDD_OO_USER`` (default ``root``), ``OOPTDD_OO_ORG`` (default ``default``).

Settings passed via ``[tool.ooptdd]`` may override the *names* of those env
vars or the org, but never carry secrets.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request

from .base import QueryResult


class OpenObserveBackend:
    default_lookback_s = 3600
    default_future_buffer_s = 300  # +5 min: absorb receive-time / clock-skew race

    def __init__(
        self,
        *,
        stream: str = "tests",
        org: str | None = None,
        url_env: str = "OOPTDD_OO_URL",
        user_env: str = "OOPTDD_OO_USER",
        password_env: str = "OOPTDD_OO_PASSWORD",
        timeout: float = 15.0,
        opener=None,
        **_ignored,
    ):
        self.stream = stream
        self.org = org or os.getenv("OOPTDD_OO_ORG", "default")
        self.url_env = url_env
        self.user_env = user_env
        self.password_env = password_env
        self.timeout = timeout
        # opener(request, timeout) injection lets tests exercise this driver
        # without a network.
        self._open = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))

    def _endpoint(self) -> tuple[str, str, str]:
        base = os.getenv(self.url_env, "")
        if not base:
            raise ValueError(
                f"{self.url_env} is required for the openobserve backend "
                f"(e.g. {self.url_env}=http://<host>:5080). No baked default."
            )
        pw = os.environ.get(self.password_env)
        if not pw:
            raise ValueError(f"{self.password_env} is required (env-only secret).")
        user = os.getenv(self.user_env, "root")
        auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        return base.rstrip("/"), self.org, auth

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
        with self._open(req, timeout=self.timeout):
            pass

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        try:
            base, org, auth = self._endpoint()
        except ValueError:
            return QueryResult(reachable=False)
        sql = (
            "SELECT event, passed, failed, total, skipped, service, test, outcome "
            f"FROM {self.stream} WHERE cycle_id = '{cid}'"
        )
        body = json.dumps(
            {"query": {"sql": sql, "start_time": since_us, "end_time": until_us, "size": 1000}}
        ).encode()
        req = urllib.request.Request(
            f"{base}/api/{org}/_search",
            data=body,
            method="POST",
            headers={"Authorization": auth, "Content-Type": "application/json"},
        )
        try:
            with self._open(req, timeout=self.timeout) as r:
                hits = json.loads(r.read().decode()).get("hits", [])
            return QueryResult(reachable=True, events=hits)
        except Exception:
            return QueryResult(reachable=False)
