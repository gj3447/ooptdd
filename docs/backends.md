# Backend capability matrix

<!-- GENERATED FILE — do not edit. Regenerate: python scripts/gen_backend_matrix.py -->
<!-- Pinned by tests/test_docs_backend_matrix.py: caps drift without regeneration is RED. -->

Every row below is derived from the driver's declared `BackendCaps` — the code,
not this document, is the authority.

**Reading the columns** — the load-bearing one is *external judge*
(`caps.independent`): can this backend be the independent store that proves
arrival?

- `memory` / `jsonl` prove **gate mechanics** (zero-infra, in-process /
  same-host file) — a green there says your spec and events agree, not that
  anything arrived anywhere.
- `openobserve` (reference), `clickhouse`, `signoz`, `victorialogs` prove
  **arrival**: an independent, queryable store the process under test cannot
  rewrite in memory.
- `otel` proves **portable writing** only — OTLP has no read side; pair it
  with a queryable store for verification.

| backend | write+read | queryable | external judge | complete-read paging | server-side filter | driver |
|---|---|---|---|---|---|---|
| `clickhouse` | yes | yes | yes | no | yes | ClickHouse backend — the permissively-licensed SQL driver (Tier-2 #6) |
| `jsonl` | yes | yes | no | no | yes | 파일(JSON Lines) 기반 영속 queryable backend. cid 는 동등 비교(=injection 불가) |
| `memory` | yes | yes | no | no | yes | A fake store that keeps events in a dict. Drop-in for CI and demos |
| `openobserve` | yes | yes | yes | yes | yes | OpenObserve backend — the reference network driver |
| `otel` | no | no | no | no | no | OTLP backend — the portable *write* path (best-effort, optional) |
| `signoz` | yes | yes | yes | no | yes | ClickHouse backend — the permissively-licensed SQL driver (Tier-2 #6) |
| `victorialogs` | yes | yes | yes | no | yes | VictoriaLogs backend — a schema-free log store driver |

Third-party drivers register via the `ooptdd.backends` entry-point group and
should declare their own `caps`; an undeclared driver is synthesized as
`queryable=True` from the legacy attribute (see `ooptdd.domain.ports.backend_caps`).
