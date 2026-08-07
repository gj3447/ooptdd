"""Append-only JSON Lines backend with deterministic, file-derived ordering.

The physical line ordinal is the durable sequence authority.  It remains stable across
backend instances and process restarts, so no process-global counter is required.  A
malformed record makes the read incomplete because the reader cannot prove that the record
was irrelevant to the requested correlation identifier.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping

from ..domain.settings import DEFAULT_ENV_KEYS, EnvironmentKeys
from .base import BackendCaps, QueryResult
from .settings import DEFAULT_LOCAL_BACKEND_SETTINGS, BackendSettings


class JsonlBackend:
    """Persistent queryable backend using one append-only UTF-8 JSONL file."""

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
        path: str | None = None,
        path_env: str | None = None,
        service: str | None = None,
        environment: Mapping[str, str] | None = None,
        env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
        settings: BackendSettings = DEFAULT_LOCAL_BACKEND_SETTINGS,
    ):
        captured = {} if environment is None else dict(environment)
        if not isinstance(settings, BackendSettings):
            raise TypeError("settings must be a BackendSettings value")
        resolved_path_env = path_env or env_keys.jsonl_path
        resolved_path = path or captured.get(resolved_path_env, "")
        self.path_env = resolved_path_env
        self.service = service
        self.default_lookback_s = settings.lookback_s
        self.default_future_buffer_s = settings.future_buffer_s
        if not resolved_path:
            raise ValueError(f"{resolved_path_env} or path= is required for the jsonl backend")
        self.path: str = resolved_path

    def identity(self) -> str:
        """Return a stable identity suitable for relocation detection."""

        return f"jsonl:{os.path.abspath(self.path)}"

    def ship(self, events: list[dict]) -> None:
        if not events:
            return
        stored_us = int(time.time() * 1_000_000)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as stream:
            for event in events:
                record = {"_stored_us": stored_us, "ev": dict(event)}
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        try:
            with open(self.path, encoding="utf-8") as stream:
                lines = stream.readlines()
        except FileNotFoundError:
            return QueryResult(reachable=True, events=())
        except UnicodeError as exc:
            return QueryResult(
                reachable=True,
                events=(),
                complete=False,
                error=f"malformed_jsonl_encoding: {type(exc).__name__}",
            )
        except OSError as exc:
            return QueryResult(
                reachable=False,
                complete=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        hits: list[dict] = []
        malformed_line: int | None = None
        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeError):
                malformed_line = line_number
                break
            if not isinstance(record, dict):
                malformed_line = line_number
                break
            stored_us = record.get("_stored_us")
            event = record.get("ev")
            if (
                not isinstance(stored_us, int)
                or isinstance(stored_us, bool)
                or not isinstance(event, dict)
            ):
                malformed_line = line_number
                break
            event_cid = event.get("cid") or event.get("correlation_id") or ""
            if event_cid == cid and since_us <= stored_us <= until_us:
                hits.append({**event, "_timestamp": stored_us, "_seq": line_number - 1})

        if malformed_line is not None:
            return QueryResult(
                reachable=True,
                events=tuple(hits),
                complete=False,
                error=f"malformed_jsonl_record:line={malformed_line}",
            )
        return QueryResult(reachable=True, events=tuple(hits), complete=True)


__all__ = ("JsonlBackend",)
