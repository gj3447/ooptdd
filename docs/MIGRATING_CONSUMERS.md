# Migrating in-house consumers onto `ooptdd`

> Status: 2026-06-16. Decisions locked with the user; execution is per-consumer (each is a
> *rewrite*, not a drop-in swap — the in-house twins predate the Backend-Protocol API).

## Locked decisions

1. **Migrate-first, then build.** Consolidate the scattered twins onto this repo *before* adding
   the open hardening items (#2/#5/#7/#10), so each is built once here, not N times across forks.
2. **Distribution = vendored core + drift-check** (not pip/private-index, not git+ssh). Each consumer
   vendors the small core it needs; a drift-detector test fails loudly if the vendored copy diverges
   from this repo. Rationale: zero infra, works in every env incl. the jg_bpc **Windows field PC**,
   no private-repo auth. (pip/git+ssh rejected: private-repo install auth in CI/field; index rejected:
   infra to stand up.)
3. **Sequence:** lakatotree (canary) → prismv2 → jg_bpc. Then build #7 → #10 → #5 → #2 *here*.
4. **`#1/#4/#9` are already in this repo** (cid gate, 3-valued verdict, exponential backoff). The
   parallel twin commits (prismv2 `1c40e26`, jg_bpc `521064c`) are now redundant hardening of
   soon-to-retire twins — they retire with the twins.

## The vendored + drift-check mechanism

- Canonical = `src/ooptdd/` in this repo. A consumer copies only the modules it imports into a
  `…/_vendor/ooptdd/` dir, plus one test:
  ```python
  # test_ooptdd_vendor_drift.py — fails (RED) the moment the vendored copy diverges from canonical.
  # Compares a normalized sha256 of each vendored file against a committed manifest
  # (ooptdd_vendor_manifest.json: {relpath: sha256, ooptdd_version}). Re-vendor to fix.
  ```
- Normalize before hashing (strip trailing whitespace / normalize line endings) so cosmetic diffs
  don't false-RED — see the design note: oo_sink normalized MATCHes across forks, oo_verify DRIFTs.
- A tiny `scripts/vendor_ooptdd.sh <consumer-path>` copies `src/ooptdd/{model,verify,config,plugin,cli,gate}.py`
  + `backends/{base,memory,openobserve}.py` and rewrites the manifest. Drift-check + re-vendor = the
  whole sync loop, no package install anywhere.

## API delta every consumer hits (twin → ooptdd)

| twin (old) | ooptdd (new) |
|---|---|
| `oo_sink.ship(records, opener=…)` | `backend.ship(events)` (Backend Protocol; `MemoryBackend` for tests, no opener) |
| `oo_verify.verify_trace(cid, opener=…)` | `verify_trace(backend, cid, …)` — backend first arg, returns `verdict` |
| `oo_sink.enabled()` (`AIRO_LOGS_E2E ∧ OO_PASS`) | `Settings.is_enabled()` (`OOPTDD_ENABLED` + backend); env `OOPTDD_OO_*` |
| `session_finish(reports, cid, shipper=, verifier=)` | `session_finish(backend, reports, cid, …)` |
| custom `conftest` hooks | the **pytest11 plugin auto-registers** — delete the hooks |

Env contract changes: `OO_URL/OO_PASS/AIRO_LOGS_E2E` → `OOPTDD_OO_URL/OOPTDD_OO_PASSWORD/OOPTDD_ENABLED`.
Graceful: where ooptdd (vendored) is absent, the plugin simply doesn't load → tests run, LTDD off.

## Per-consumer touchpoints (scouted 2026-06-16)

### 1. lakatotree (`/mnt/hdd/kjra/lakatotree`, branch `master`) — canary
- `lakatos/cli.py:284-298` — **production path** uses `oo_sink.ship()` + `oo_sink.enabled()`. Rewrite to a backend.
- `tests/conftest.py:15,46` — `oo_verify.session_finish(reports, cid, …)` → plugin (delete) or `session_finish(backend, …)`.
- `scripts/oo_positive_verify.py` — CLI wrapper → `ooptdd verify` CLI.
- Tests to rewrite: `test_oo_verify.py` (opener= API), `test_p7d_ops_robustness.py` (OPS-INIT-1 URL assertions → `OOPTDD_OO_URL`), `test_marquez_sink.py` (homolog pattern), `test_longinus_bindings.py` (**KG spans** `span_lakatotree_oo_sink/_conftest` — update Longinus bindings).
- ⚠ shared/concurrent repo — commit only own files; no rebase/reset of others' work.

### 2. prismv2 (`/mnt/hdd/kjra/prismv2`, branch `develop`)
- Delete `tests/_oo_ltdd/` (5 files). Remove `tests/conftest.py:96-153` hooks (keep the L78-94 alias shim — unrelated). Add `[tool.ooptdd]` to `pyproject.toml` (`backend="openobserve"`, `service="prismv2.tests"`, `verify="warn"`, `cid_env="PRISMV2_TEST_CID"`).
- **`prism_core/testing/airo_trace.py` (the `airo_l3` marker, 3 tests) is a SEPARATE concern** — it asserts the *production inspection cycle* event sequence, not test outcomes. Keep it; optionally have it call `ooptdd.verify_trace(backend, cid)` internally in a later pass. Not a blocker.

### 3. jg_bpc (`/mnt/hdd/kjra/3d_vision_jg_bpc`, branch `kjra`)
- `scripts/oo_gate.py` — **partially blocked**: this repo's gates filter on *fields* (`WHERE verdict='NG'`, `WHERE level='ERROR'`); ooptdd's `gate.py` counts by `event` only → those gates are **not expressible**. Either keep `oo_gate.py` for field-filter gates, or build **#11 (field-filter in ooptdd gate)** first. Pure event-count gates can migrate.
- `test/conftest.py` `oo_trace` fixture (per-test assertion) → ooptdd plugin / `verify_trace`.
- `scripts/gates/*.yaml` — convert event-count gates to ooptdd's `expect:` spec; field-filter gates now
  migrate too (the `where:` key landed — see #11 below).

## Repo-side items — ✅ DONE (built in this repo *before* migration, 2026-06-16)

Done first (on purpose: consumers migrate once, to a complete package). All TDD, 44 tests + ruff clean.

- **#11 field-filter** ✅ `19fc316` — `where: {field: value}` (+ optional `event`) in `gate.py`; OpenObserve
  `SELECT *` so whole rows come back. Unblocks jg_bpc's `WHERE verdict=…`/`level=…` gates.
- **#7 must_order** ✅ `10f3dce` — declarative `must_order: [a,b,c]`, checked in Python over returned
  events' `_timestamp` (memory backend now stamps it) — no per-backend SQL.
- **#10 optional** ✅ `516aa13` — per-check `optional:` (miss surfaced via `optional_failed`, not gating);
  unreachable store still ≠ pass; CLI WARN line.
- **#5 heartbeat** ✅ `7ebee26` — `model.build_session_start()` shipped at `pytest_collection_finish`
  (controller-only, best-effort); `verify_trace` `started` flag distinguishes partial vs total loss.
- **#2 anti-fabrication** ✅ `e6f5858` (code half) — HMAC-signed summary; `sig_status ∈
  valid/invalid/unsigned/unverifiable`; invalid ALWAYS fails (even warn); `require_signature` rejects
  unsigned; key = env `OOPTDD_SIGNING_KEY` (CI-only). Honest threat model documented (theater vs an
  agent that can read CI secrets). **Remaining (ops, not code):** dedicated write-only ingest account +
  provision the CI secret + run `require_signature` + `strict` for full enforcement.

## Remaining work

- **Consumer migration** (lakatotree → prismv2 → jg_bpc) per the touchpoints above — the per-consumer
  rewrite. This is the next phase.
- **#2 ops half** — ingest account + CI secret provisioning (escalate).
