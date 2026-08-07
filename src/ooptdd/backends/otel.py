"""OTLP backend — the portable *write* path (best-effort, optional).

OpenTelemetry's OTLP is the one ingest protocol every major store accepts, so
emitting events as OTLP LogRecords is the strategic way to stay
backend-neutral *on write*. There is no portable *query* protocol — LogQL,
TraceQL, ES-DSL and SQL all differ — so this driver ships via OTLP but cannot,
by itself, read back. Pair it with a
store-specific reader, or use it only where you trust ingest.

Requires the ``otel`` extra (``pip install ooptdd[otel]``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry._logs import Logger

from ..domain.settings import DEFAULT_ENV_KEYS, DEFAULT_SERVICE, EnvironmentKeys
from .base import BackendCaps, QueryResult, sanitize_endpoint_identity
from .settings import DEFAULT_BACKEND_SETTINGS, BackendSettings


class OtelBackend:
    default_lookback_s = DEFAULT_BACKEND_SETTINGS.lookback_s
    default_future_buffer_s = DEFAULT_BACKEND_SETTINGS.future_buffer_s
    queryable = False  # OTLP is write-only — no read side, so arrival can't be verified here
    caps = BackendCaps(queryable=False, write_only=True, independent=False)

    def __init__(
        self,
        *,
        service: str = DEFAULT_SERVICE,
        endpoint: str | None = None,
        endpoint_env: str | None = None,
        simple: bool = False,
        exporter=None,
        environment: Mapping[str, str] | None = None,
        env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
        settings: BackendSettings = DEFAULT_BACKEND_SETTINGS,
    ):
        captured = {} if environment is None else dict(environment)
        if not isinstance(settings, BackendSettings):
            raise TypeError("settings must be a BackendSettings value")
        self.default_lookback_s = settings.lookback_s
        self.default_future_buffer_s = settings.future_buffer_s
        self.service = service
        self.endpoint_env = endpoint_env or env_keys.otel_endpoint
        captured_endpoint = (endpoint or captured.get(self.endpoint_env, "")).rstrip("/")
        self.endpoint = (
            captured_endpoint
            if not captured_endpoint or captured_endpoint.endswith("/v1/logs")
            else f"{captured_endpoint}/v1/logs"
        )
        # A batch processor exports asynchronously. ``simple=True`` selects the
        # synchronous processor for callers that require inline visibility.
        self.simple = simple
        # An injectable log-record exporter: the default (None) ships OTLP over the wire; tests
        # (and the write-only conformance kit) pass an in-memory exporter to capture what shipped.
        self._exporter = exporter
        self._logger: Logger | None = None

    def _ensure(self):
        if self._logger is not None:
            return
        try:
            from opentelemetry.sdk._logs import LoggerProvider
            from opentelemetry.sdk._logs.export import (
                BatchLogRecordProcessor,
                SimpleLogRecordProcessor,
            )
            from opentelemetry.sdk.resources import Resource
        except ImportError as exc:  # pragma: no cover - exercised only with extra
            raise RuntimeError("the otel backend needs `pip install ooptdd[otel]`") from exc
        exporter = self._exporter
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

            exporter = OTLPLogExporter(endpoint=self.endpoint)
        provider = LoggerProvider(resource=Resource.create({"service.name": self.service}))
        proc = SimpleLogRecordProcessor if self.simple else BatchLogRecordProcessor
        provider.add_log_record_processor(proc(exporter))
        # Instance-scoped logger, NOT the process-global set_logger_provider: a driver instance
        # owns its own provider so an injected exporter actually receives this instance's emits
        # (the global is a singleton — a second backend would silently emit to the first's).
        self._provider = provider
        self._logger = provider.get_logger(__name__)

    def ship(self, events: list[dict]) -> None:
        if not events:
            return
        # The endpoint is only needed for the default OTLP exporter; an injected exporter
        # (e.g. an in-memory one in conformance tests) ships nowhere over the wire.
        if self._exporter is None and not self.endpoint:
            raise ValueError(f"{self.endpoint_env} is required for the otel backend.")
        self._ensure()
        from opentelemetry._logs import SeverityNumber

        logger = self._logger
        if logger is None:
            raise RuntimeError("OTel logger initialization completed without a logger")
        scalar = (str, int, float, bool)
        for ev in events:
            sev = SeverityNumber.ERROR if ev.get("level") == "ERROR" else SeverityNumber.INFO
            # Modern logs API: emit the fields directly and let the SDK build the LogRecord. The
            # old ``emit(LogRecord(...))`` form and the ``LogRecord`` import location both moved
            # across opentelemetry-sdk releases, silently breaking this driver — now caught by the
            # write-only conformance test (tests/test_otel_backend.py).
            logger.emit(
                body=ev.get("event", "event"),
                severity_number=sev,
                attributes={k: v for k, v in ev.items() if isinstance(v, scalar)},
            )
        self._provider.force_flush()

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        # OTLP has no read side; queries are store-specific. Return inconclusive.
        return QueryResult(reachable=False)

    def identity(self) -> str:
        return sanitize_endpoint_identity(self.endpoint) if self.endpoint else type(self).__name__
