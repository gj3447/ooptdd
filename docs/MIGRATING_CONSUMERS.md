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

### Making absence RED (required-presence lanes)

Fail-open is the right *default*, but it has a trap: because receipts guard with
`pytest.importorskip("ooptdd…")`, a missing vendored copy, a fail-open install, or **a `.venv`
rebuilt without ooptdd** turns every receipt into a SKIP — so a CI lane can report green having
verified nothing. There is no signal that the substrate went missing. For the lanes that MUST
have receipts, make absence loud with **either**:

- **Force the plugin** — add `-p ooptdd.plugin` to that lane's pytest invocation (or `addopts`).
  pytest fails at startup if `ooptdd.plugin` can't be imported, so absence is a hard error, not a
  skip. (Do NOT combine with `-p no:ooptdd`, which is for suppressing the *dev-box auto-ship*.)
- **Drop in the canary** — copy `scripts/templates/conftest_ooptdd_required.py` next to your
  receipts (or merge its body into an existing `conftest.py`) and set `OOPTDD_REQUIRED` on the
  required lanes:

  ```
  OOPTDD_REQUIRED=1                              # require `ooptdd`
  OOPTDD_REQUIRED=ooptdd.backends,ooptdd_loop    # require exactly these
  ```

  It imports the named modules at collection time, so absence aborts the session. It is a no-op
  when the env is unset, so it is safe to commit everywhere. See
  `tests/test_required_presence.py` for the three states it guarantees (absent+unset → skip-green;
  absent+required → red; present+required → runs).

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

## ★ Canonical wiring pattern (resolved 2026-06-16) — vendored ≠ pytest11 auto-register

A trap surfaced during the canary: **decision #2 (vendored, no pip) and "delete the conftest hooks
→ the pytest11 plugin auto-registers" are in tension.** Entry-point plugins (`pytest11`) only load
for *pip-installed distributions*. A vendored copy under `_vendor/` is NOT installed, so it never
auto-registers. Worse, on a dev box where ooptdd *is* pip-installed, that installed plugin
auto-ships — so a naive vendored consumer would either (a) have no LTDD in the field, or (b)
double-ship in dev. The resolved canonical pattern (used by prismv2 + jg_bpc):

1. **Disable the auto-plugin**: `addopts = … -p no:ooptdd` in the consumer's pytest config. The dev
   pip-installed copy can no longer auto-ship; the vendored library is the single source in *every*
   env (dev / CI / Windows field PC).
2. **Wire the vendored core as a library** in `conftest.py` (root, or `tests/` if that is on
   `testpaths`): `sys.path.insert(0, "<_vendor>")`, `import ooptdd`, then manual
   `pytest_runtest_logreport` (collect) + `pytest_sessionfinish` (`ooptdd.session_finish(backend, …)`).
   Backend = `openobserve` when an oo target is set, else zero-infra `memory`.
3. **Gate on an explicit opt-in** (`AIRO_LOGS_E2E=1` or `OOPTDD_ENABLED`), NOT on `OO_URL` presence —
   a dev shell that merely exports `OO_URL` must not auto-ship every run.
4. **Make `_vendor`'s drift-check collectable**: add `_vendor` to `testpaths` (jg_bpc) or place it
   in the test dir (prismv2). It REDs the moment the vendored copy diverges from canonical.
5. **Separate concern stays put**: a *per-test domain-event* assertion (prismv2 `airo_trace.py`,
   jg_bpc `oo_trace` fixture + `airo_log_sink`) asserts the production event sequence, not test
   outcomes — keep it; it is NOT a twin of ooptdd. Optional later: delegate its ship/query to a
   vendored `OpenObserveBackend` to dedup network code (lakatotree did this for its `oo_sink`).

## Migration status (2026-06-16)

- **prismv2** ✅ MIGRATED (develop) — `tests/_vendor/ooptdd` + `-p no:ooptdd` + conftest library hooks.
- **lakatotree** ✅ FUNCTIONAL (branch `ooptdd-migration`) — canary proven RED→GREEN through real oo
  (`verify=strict`). Two commits: conftest/cli onto ooptdd + the twin (`oo_sink`/`oo_verify`) made a
  thin delegator to vendored core. ⚠ Reconcile-to-pattern + merge to master deferred: a parallel
  `server/` refactor is mid-flux in the working tree; my early conftest used dev=pip/field=vendored
  (the pre-pattern shape) — re-point it at the §canonical pattern when the concurrency settles.
- **jg_bpc** ✅ MIGRATED (branch `kjra`) — `_vendor/ooptdd` (additive) + session-level LTDD via the
  vendored library + `-p no:ooptdd`. `oo_trace`/`airo_log_sink` kept (separate concern). `scripts/oo_gate.py`
  kept: it is a raw-SQL aggregate gate runner (arbitrary SQL + `${CID}`), NOT expressible in ooptdd's
  declarative event/where/count model — complementary, not a twin.

## Known engine issues (SOLID adversarial review 2026-06-16, both P2, no blockers)

- **otel + strict = silent no-op gate**: `OtelBackend.query()` is write-only (`reachable=False`), so
  `verify=strict` over otel never fails — the silent-green ooptdd fights, one layer up. Documented but
  unenforced; should loudly WARN/refuse when strict meets a write-only backend.
- **`session_finish` swallows all verify exceptions** (`verify.py`): a bug in the gate path becomes an
  invisible green. Fail-open is deliberate, but a harness-internal error should be distinguishable
  from an unreachable store.

## Remaining work

- **lakatotree** — reconcile conftest to the §canonical pattern + merge `ooptdd-migration` to master
  (after the parallel `server/` refactor lands; the twin-delegator commit flagged "do not merge until
  the two parallel migrations are reconciled").
- **Engine P2 fixes** above — fix in canonical, then re-vendor each consumer (drift tests will RED until
  re-vendored: `python scripts/vendor_ooptdd.py <consumer>`). Coordinate because it touches all repos.
- **#2 ops half** — ingest account + CI secret provisioning (escalate).
