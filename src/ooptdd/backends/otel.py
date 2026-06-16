"""OTLP backend — the portable *write* path (best-effort, optional).

OpenTelemetry's OTLP is the one ingest protocol every major store accepts, so
emitting events as OTLP LogRecords is the strategic way to stay
backend-neutral *on write*. The catch (see ``docs/research`` C1/C3): there is no
portable *query* protocol — LogQL, TraceQL, ES-DSL and SQL all differ — so this
driver ships via OTLP but cannot, by itself, read back. Pair it with a
store-specific reader, or use it only where you trust ingest.

Requires the ``otel`` extra (``pip install ooptdd[otel]``).
"""
from __future__ import annotations

import os

from .base import QueryResult


class OtelBackend:
    default_lookback_s = 3600
    default_future_buffer_s = 300

    def __init__(
        self,
        *,
        service: str = "ooptdd.tests",
        endpoint_env: str = "OTEL_EXPORTER_OTLP_ENDPOINT",
        **_ignored,
    ):
        self.service = service
        self.endpoint_env = endpoint_env
        self._logger = None

    def _ensure(self):
        if self._logger is not None:
            return
        try:
            from opentelemetry._logs import get_logger, set_logger_provider
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            from opentelemetry.sdk._logs import LoggerProvider
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from opentelemetry.sdk.resources import Resource
        except ImportError as exc:  # pragma: no cover - exercised only with extra
            raise RuntimeError(
                "the otel backend needs `pip install ooptdd[otel]`"
            ) from exc
        provider = LoggerProvider(resource=Resource.create({"service.name": self.service}))
        provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        set_logger_provider(provider)
        self._provider = provider
        self._logger = get_logger(__name__)

    def ship(self, events: list[dict]) -> None:
        if not events:
            return
        if not os.getenv(self.endpoint_env):
            raise ValueError(f"{self.endpoint_env} is required for the otel backend.")
        self._ensure()
        from opentelemetry._logs import SeverityNumber
        from opentelemetry.sdk._logs import LogRecord

        scalar = (str, int, float, bool)
        for ev in events:
            sev = SeverityNumber.ERROR if ev.get("level") == "ERROR" else SeverityNumber.INFO
            self._logger.emit(
                LogRecord(
                    body=ev.get("event", "event"),
                    severity_number=sev,
                    attributes={k: v for k, v in ev.items() if isinstance(v, scalar)},
                )
            )
        self._provider.force_flush()

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        # OTLP has no read side; queries are store-specific. Return inconclusive.
        return QueryResult(reachable=False)
