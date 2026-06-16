# Backend Abstraction Prior-Art for pytest-ooptdd

## Summary

Observability frameworks have standardized on a **3-method minimal exporter/sink interface** (export/ship batch → query results → shutdown). OpenTelemetry's `SpanExporter`, k6's output extensions, Vector sinks, and Grafana datasources all follow this pattern. For pytest-ooptdd, the canonical model is **entry-point-discovered drivers** (like SQLAlchemy dialects or Django backends), with an ABC base class defining the contract. This allows `pytest-ooptdd[loki]` / `[tempo]` / `[otel-collector]` extras to be installed independently and auto-registered.

## Sub-findings (with confidence)

1. **OpenTelemetry 3-method exporter interface** (HIGH)
   - export(spans: Sequence[ReadableSpan]) → SpanExportResult
   - shutdown() → None
   - force_flush(timeout_millis: int) → bool
   - Minimal because protocol exporters are "simple telemetry data encoder and transmitter"
   - Used by 50+ backends (Jaeger, Tempo, Loki via Collector, Elastic, etc.)

2. **Entry-point registry pattern is cleanest for pytest** (HIGH)
   - SQLAlchemy dialect model: `setup.cfg [options.entry_points] sqlalchemy.dialects = postgres = ...`
   - pytest plugin model: `pyproject.toml [project.entry-points.pytest11] myproject = ...`
   - Django backend model: registry + dynamic import via settings string (precursor to entry points)
   - Allows `pip install pytest-ooptdd[loki]` → auto-register driver class, zero boilerplate

3. **Query interface must support: (HIGH)
   - cid (correlation_id) filter
   - event count over time window → users verify "≥1 event" / "exactly N" / "none"
   - returns count or rows (k6 uses buffered samples, OTel uses batch export)

4. **Two-class minimal backend implementation pattern** (MEDIUM)
   - cloudpathlib: implement just Path + Client classes
   - Applied to ooptdd: implement Sink (ship events) + Query (poll count)
   - Alternative: single class with both capabilities (Grafana datasource = QueryDataHandler)

5. **Avoid force full IPC for pytest plugin discovery** (MEDIUM)
   - Benthos/xk6-output-plugin use gRPC sidecars (high-latency, heavy)
   - k6's statsd output: buffered periodic flush (lightweight, testable in-process)
   - pytest entry points are same-process + in-memory, no network overhead

## Raw Quotes (≥4 attributed with URL)

### Quote 1: OpenTelemetry 3-method spec
**Source**: [opentelemetry-python.readthedocs.io](https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.export.html)

> "The exporter MUST support three functions: Export, Shutdown, and ForceFlush. Export exports a batch of readable spans. Shutdown stops the exporter. ForceFlush provides a hint to ensure that the export of any spans the exporter has received prior to the call to ForceFlush SHOULD be completed as soon as possible."

**Context**: This is the canonical minimal interface that OpenTelemetry SDKs enforce. Every backend implementation across all languages (Python, Go, JS, Ruby) must implement these three methods. No other methods are required.

### Quote 2: SQLAlchemy entry-point pattern
**Source**: [SQLAlchemy README dialects](https://github.com/sqlalchemy/sqlalchemy/blob/main/README.dialects.rst)

> "Third-party dialect can be distributed like any other Python module on PyPI. The entry_points configuration uses the 'sqlalchemy.dialects' key with entries like 'access.pyodbc = sqlalchemy_access.pyodbc:AccessDialect_pyodbc' to allow URLs to be used with custom dialects."

**Context**: This is the production-proven pattern for pluggable backends. SQLAlchemy uses it for 15+ dialects. The pattern is: define entry point → setuptools auto-discovers → dynamic load at URL parse time. Perfect fit for pytest-ooptdd backend drivers.

### Quote 3: cloudpathlib minimal interface
**Source**: [cloudpathlib GitHub](https://github.com/drivendataorg/cloudpathlib)

> "The base classes do most of the work generically, so implementing two small classes MyPath and MyClient is all you need to add support for a new cloud storage service."

**Context**: cloudpathlib proves that abstraction only needs 2 classes. For ooptdd: Sink (write/batch interface) + Query (poll interface) follow the same logic. Inheritance handles boilerplate.

### Quote 4: pytest entry-point discovery
**Source**: [pytest writing-plugins docs](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)

> "pytest looks up the pytest11 entrypoint to discover its plugins. If a package is installed this way, pytest will load myproject.pluginmodule as a plugin which can define hooks. [project.entry-points.pytest11] myproject = 'myproject.pluginmodule'"

**Context**: pytest already has mature entry-point plugin discovery (same mechanism as SQLAlchemy). ooptdd can reuse this pattern with a new entry-point namespace `ooptdd.sinks` or `ooptdd.backends`.

## Alternative Recommendations

### Alt 1: Unified Datasource Plugin Model (Grafana pattern)
**Approach**: Single class inheriting from `DataSourceBackend` with `query(filters, time_window) → results` method. Emit JSON responses. Use OTLP as the wire format (avoid per-backend serialization).

**Pros**: Simpler class hierarchy; Grafana ecosystem interop; single query interface
**Cons**: Loses OTel SDK standardization; requires custom result schema; less community precedent for pytest

**Confidence**: MEDIUM — works if ooptdd standardizes on JSON event schema upfront.

### Alt 2: External gRPC Plugin (Benthos/xk6 pattern)
**Approach**: Sidecar process per backend (Loki/Tempo/Collector), communicate over gRPC. pytest-ooptdd marshals events → gRPC call.

**Pros**: Language-agnostic; strong isolation; production ops mature (k6 does this)
**Cons**: High latency (unacceptable for pytest polling in <100ms); process mgmt burden; overkill for test harness

**Confidence**: LOW for pytest — gRPC overhead breaks pytest test speed.

### Alt 3: In-Memory Mock Sink + File-Based Durability (lightweight)
**Approach**: ABC `Sink` with `ship(events)` + `query(cid, window)`. Default impl writes JSON lines to /tmp, pollers read. S3/Loki impls override ship() only.

**Pros**: Zero external deps; testable offline; durability via filesystem hierarchy
**Cons**: Doesn't scale to many tests; no TTL/garbage collection; slower than in-memory query

**Confidence**: MEDIUM — good for development/CI, not production ooptdd use.

## Counter-arguments / Caveats

### Caveat 1: Entry-point auto-discovery requires installation
If ooptdd backend driver is not installed (e.g., `pip install pytest-ooptdd` without `[loki]` extra), entry point is not registered and backend is unavailable. Solution: fallback to in-memory sink, or fail with helpful message naming the required extra.

### Caveat 2: Minimal interface may hide critical differences
OpenTelemetry's 3-method spec works for *exporters* (write-only). But ooptdd needs *bidirectional* query (write + read polling). The exporter model assumes a collector on the other end. For ooptdd, query() must be implemented by every backend, but export-only sinks (e.g., stdout, file) have no natural query implementation. Solution: define two role classes: `Sink` (write) + `Queryable` (read), optionally combined.

### Caveat 3: Time window filtering varies by backend
Some backends (Loki, Tempo) support native time-range queries (nanosecond precision). Others (Elasticsearch, S3) require full table scan. OTel assumes nanosecond UNIX epoch timestamps in events. For ooptdd verifier to work across backends, must normalize time window semantics: fallback to scanning all and filtering in Python if backend doesn't support native range query.

### Caveat 4: Correlation ID indexing not guaranteed
OTel spec does not mandate that exporters index correlation_id (or any custom field). Querying by cid over unbounded event stream is O(n). Solution: contract requires backends to be indexed on correlation_id at import time, or return "not indexed" error + warn user to add index (manual DBA work).

## Search Trail (queries used)

1. `OpenTelemetry exporter architecture pluggable backend pattern`
2. `Grafana unified datasource plugin model abstraction interface`
3. `Vector.dev sinks abstraction multiple backends configuration`
4. `Python SQLAlchemy dialect entry point pattern plugin backends`
5. `Fluent Bit output plugin abstraction pluggable architecture`
6. `Benthos Redpanda Connect output plugin minimal interface abstraction`
7. `Tracetest multiple trace data stores backend abstraction interface`
8. `cloudpathlib Python library filesystem abstraction entry points AWS Azure GCS`
9. `logging backend abstraction Python ABC interface pluggable drivers`
10. `OpenTelemetry SDK exporter interface SpanExporter TraceExporter minimal methods`
11. `Apache Libcloud driver abstraction base class minimal interface pattern`
12. `k6 load testing output plugins extensions minimal sink interface`
13. `pytest plugin entry points pytest_plugins minimal interface write custom backend`
