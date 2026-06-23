"""The OTLP (write-only) backend, exercised through the write-only conformance kit.

Requires the ``otel`` extra; skipped otherwise. This is the regression guard that the shipped
OTLP driver actually ships under the *current* opentelemetry-sdk — the silent breakage that the
write-only conformance gap (R4) used to hide (the LogRecord import + emit API moved between SDK
releases, and nothing exercised the driver to catch it).
"""
import pytest

pytest.importorskip("opentelemetry.sdk._logs")

from ooptdd.backends.conformance import assert_writeonly_backend_conforms  # noqa: E402
from ooptdd.backends.otel import OtelBackend  # noqa: E402


class _OtelCapture:
    """Adapt an OTLP in-memory exporter to the capture-sink contract (``records`` as dicts):
    the OTLP driver puts the event name in the LogRecord ``body`` and scalar fields in
    ``attributes``, so reconstruct ``{**attributes, "event": body}``."""

    def __init__(self):
        try:
            from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter as _Exp
        except ImportError:  # older SDKs
            from opentelemetry.sdk._logs.export import InMemoryLogExporter as _Exp
        self.exporter = _Exp()

    @property
    def records(self):
        out = []
        for ld in self.exporter.get_finished_logs():
            lr = ld.log_record
            rec = dict(lr.attributes or {})
            rec.setdefault("event", lr.body)
            out.append(rec)
        return out


def test_otel_writeonly_backend_conforms():
    # the shipped OTLP write-only driver, wired to an in-memory exporter, honours the
    # write-only contract (export + payload fidelity, and an honest inconclusive read side).
    def harness():
        cap = _OtelCapture()
        backend = OtelBackend(service="acme.tests", exporter=cap.exporter, simple=True)
        return backend, cap

    assert_writeonly_backend_conforms(harness)
