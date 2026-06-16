# ooptdd Backend Abstraction & Portability: Official Standards

## Summary

To open-source ooptdd without OpenObserve lock-in, the observability backend must become pluggable via **OpenTelemetry (OTel)** as the portable write abstraction. The architecture splits into three layers:

1. **Write layer (OTLP)** – Standardized OTLP/gRPC (port 4317) or OTLP/HTTP (port 4318) for emitting log events, spans, and traces. All major backends (OpenObserve, Grafana Tempo/Loki, ClickHouse, Elasticsearch) accept OTLP natively.
2. **Query layer (backend-specific)** – No standard query protocol exists; each backend has its own query language (OpenObserve SQL, LogQL, TraceQL, ClickHouse SQL). The OTel Collector + adapter pattern abstracts away write-side differences; query adapters must remain backend-specific.
3. **Correlation layer (W3C Trace Context + custom attributes)** – W3C traceparent (trace_id/span_id) standardizes distributed correlation; custom attributes (e.g., correlation_id/cid) live in OTLP's attributes map or tracestate header.

## Sub-findings (3-5 with confidence)

1. **OTLP is the write-only standard; query remains backend-specific (HIGH confidence)**  
   OTLP/gRPC and OTLP/HTTP are stable (v1.10.0) for traces, metrics, and logs. Every major backend ingests OTLP natively. However, OTLP is a **write/export protocol only**—it does not define query, retrieval, or filtering semantics. Query languages diverge: OpenObserve uses SQL, Loki uses LogQL, ClickHouse uses SQL with different schema assumptions. This means the abstraction layer must use OTel Collector for write-path portability and maintain backend-specific query adapters for read-path verification.

2. **OTel Collector as the portability seam (HIGH confidence)**  
   The Collector (receivers → processors → exporters pipeline) abstracts application code from backend-specific transport. Applications emit OTLP to Collector; Collector routes to any backend via pluggable exporters. For ooptdd, the sink can target a single Collector endpoint; the operator configures exporters (OpenObserve, Loki, ClickHouse) independently. Switching backends requires no code change, only exporter config swap.

3. **Logs vs Spans: OTel Logs best models test outcome events (HIGH confidence)**  
   OTel deprecates Span.AddEvent() in favor of structured logs. Test outcome events (pass/fail, measurands, verdicts) fit the OTel Logs data model: each event is a LogRecord with timestamp, severity_number (e.g., ERROR for FAIL), structured attributes (test_name, result, measurand_values), and optional trace_id/span_id for correlation. This is more flexible than embedding events in spans and allows richer attribute types.

4. **W3C traceparent + tracestate for correlation_id propagation (HIGH confidence)**  
   W3C Trace Context defines traceparent (version-trace_id-span_id-flags) and tracestate (vendor-specific key=value). The correlation_id (cid) should map to the OTel trace_id in traceparent (not a custom attribute). If ooptdd needs separate cid semantics (e.g., business transaction ID), use tracestate with a namespace (e.g., `ooptdd=cid:<value>`). This ensures all backends correctly recognize correlation for automated trace linking.

5. **Custom attributes survive OTLP → backend if mapped correctly (MEDIUM confidence)**  
   OpenObserve, Loki, and ClickHouse all preserve custom OTLP attributes (from LogRecord.attributes map). However, backends may truncate, index, or deny unknown attributes. The risk is low if attributes follow OTel semantic conventions (e.g., `test.name`, `test.result`). For custom domains (e.g., `ooptdd.measurand`), verify post-ingestion that attributes arrive intact; some backends truncate attributes after a threshold (e.g., 32KB per log).

## Raw Quotes (≥4 attributed with URL)

1. **OTLP is write-only**  
   "OTLP functions as a **write-only protocol**—it defines telemetry export from clients to servers, not querying capabilities. The specification explicitly covers delivery mechanisms but does not address data retrieval patterns."  
   Source: https://opentelemetry.io/docs/specs/otlp/ (via WebFetch)

2. **OTel Logs embrace existing logging**  
   "Rather than designing a completely new API, it 'embraces existing logging solutions' while establishing standardized data models and integration mechanisms."  
   Source: https://opentelemetry.io/docs/specs/otel/logs/ (via WebFetch)

3. **Span.AddEvent() deprecated in favor of logs**  
   "OTLP support for log-based events is already stable, and the Logs API can capture everything span events historically carried with richer metadata and more flexible export and filtering, with the tracing specification deprecating APIs such as Span.AddEvent in favor of emitting log-based events."  
   Source: https://opentelemetry.io/blog/2026/deprecating-span-events/ (via WebSearch)

4. **OTel Collector as portability seam**  
   "The pipeline model unlocks powerful capabilities: data transformation, routing, sampling, and vendor abstraction. The OpenTelemetry Collector offers a vendor-agnostic implementation of how to receive, process and export telemetry data and removes the need to run, operate, and maintain multiple agents/collectors."  
   Source: https://oneuptime.com/blog/post/2026-02-06-opentelemetry-collector-pipeline-model/ (via WebSearch)

5. **W3C Trace Context for distributed correlation**  
   "The W3C Trace Context defines a common, vendor-neutral way to propagate trace identity so that every component in the path can forward it, regardless of which tracing backend you use."  
   Source: https://www.dynatrace.com/knowledge-base/w3c-trace-context/ (via WebSearch)

## Alternative Recommendations

1. **Vendor-specific adapter pattern**: Instead of OTLP, emit directly to each backend's native API (e.g., OpenObserve REST, Grafana Loki HTTP), with adapter implementations per backend. **Tradeoff**: More coupling; easier per-backend feature access; harder to switch; 3-5 adapter implementations.

2. **Custom OTLP-like JSON envelope + HTTP POST**: Define ooptdd's own serialization format (not Protobuf) and POST to a configurable sink. **Tradeoff**: Full control; no ecosystem; backends require custom parser; harder to interop with standard tools.

3. **Logs-only (no spans/traces)**: Emit only OTel LogRecords, no Span objects. Simplify correlation to trace_id in attributes, skip tracestate. **Tradeoff**: Lost distributed-trace visualization; query-time join on cid more expensive; suitable if tests are single-process.

## Counter-arguments / Caveats

1. **Query portability is a myth**: OTLP solves write portability, not query. Moving from OpenObserve to ClickHouse requires rewriting all verification queries (OpenObserve SQL → ClickHouse SQL, different table schemas). Budget for query adapter maintenance per backend.

2. **OTel Collector adds operational burden**: Running Collector as a sidecar/service adds a new component. Collector misconfiguration (wrong exporter, dropped logs) is a new failure mode. For simple local testing (ooptdd on dev machine), direct OTLP to backend may be simpler.

3. **Custom attributes may truncate silently**: Some backends (e.g., ClickHouse) have hardcoded column limits. If ooptdd emits 200 custom attributes, ClickHouse may silently truncate to 64. Test with your backend; don't assume all attributes survive.

4. **Trace Context / cid mapping is not automatic**: If ooptdd uses cid as a custom attribute (not in OTLP traceparent), most backends won't recognize it for correlation. You must explicitly wire cid → OTLP trace_id or tracestate. If ooptdd's cid is not a UUID, you need a transformation step (Collector processor).

5. **OTel adoption is still rising (not universal)**: Older backends (Elasticsearch 7.x, Splunk legacy) may require custom OTLP parsers or don't support it at all. Check OTLP support before committing to a backend.

## Search Trail (queries used)

1. "OpenTelemetry OTLP logs traces portable standard 2026"
2. "W3C Trace Context traceparent tracestate correlation_id mapping"
3. "OTel Collector exporter OpenObserve Grafana Loki Tempo backend abstraction"
4. "observability query standard OTLP Perses Grafana datasource API"
5. "OpenTelemetry logs data model semantic conventions test tracing"
6. "OpenTelemetry SDK language logs sink emit event Python implementation"
7. "test outcome event observability OTLP span vs log semantic conventions test tracing"
8. "OpenObserve OTLP ingest logs query LogQL tracestate custom attributes 2026"
9. "OTLP write protocol query abstraction backend-specific LogQL Grafana ClickHouse SQL"
10. "OTel Collector configuration backend exporter multiple sinks abstraction layer"
