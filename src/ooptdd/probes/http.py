"""HttpProbe — GET a fact from a separate HTTP service (a second collector, a ledger API, a DB
gateway). A reference adapter; the service is independent of the trace store, so it is honest
``separate_source=True`` corroboration. ``opener`` injection (mirroring the backend drivers) lets
tests exercise it without a network.
"""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping

from ..domain.ports import ProbeResult


class HttpProbe:
    """``selector``: a URL (str), or ``{url, json: "a.b", headers: {...}}`` to extract a field
    from a JSON response. A non-round-tripping request is ``reachable=False`` (inconclusive); with
    ``json`` set, a parse failure / missing field is a complete read of a None value."""

    def __init__(self, *, timeout: float = 10.0, opener=None):
        self.timeout = timeout
        self._open = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))

    def probe(self, kind, selector, cid) -> ProbeResult:
        if isinstance(selector, str):
            sel = {"url": selector}
        elif isinstance(selector, Mapping):
            sel = dict(selector)
        else:
            raise ValueError("http probe selector must be a URL string or mapping")
        url = sel.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("http probe selector requires a non-empty string `url`")
        raw_headers: object = sel.get("headers") or {}
        if not isinstance(raw_headers, Mapping):
            raise ValueError("http probe `headers` must be a string-to-string mapping")
        headers: dict[str, str] = {}
        for key, value in raw_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("http probe `headers` must be a string-to-string mapping")
            headers[key] = value
        ident = url  # the service URL read — comparable against the emit endpoint
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with self._open(req, timeout=self.timeout) as r:
                body = r.read().decode()
        except Exception:  # noqa: BLE001 — an unreachable service is inconclusive
            return ProbeResult(reachable=False, separate_source=True, derived_identity=ident)
        if sel.get("json"):
            try:
                value = json.loads(body)
            except json.JSONDecodeError:
                return ProbeResult(reachable=True, value=None, complete=False,
                                   separate_source=True, derived_identity=ident)
            for key in str(sel["json"]).split("."):
                value = value.get(key) if isinstance(value, dict) else None
            return ProbeResult(reachable=True, value=value,
                               separate_source=True, derived_identity=ident)
        return ProbeResult(reachable=True, value=body.strip(),
                           separate_source=True, derived_identity=ident)
