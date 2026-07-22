# Why arrival testing — what the last category's failure modes taught this one

ooptdd did not invent the idea of asserting over emitted telemetry. A whole
category — **trace-based testing (TBT)** — tried it first, with more funding,
more features, and a real product surface. This page walks through what
happened to that category and maps each failure mode to the specific mechanism
in this codebase that exists because of it. Every ooptdd claim below cites
current source; every competitor claim cites the evidence it rests on, with its
caveats stated. Dates and repository states are **as of the 2026-07-22 research
cycle** ([`research/prom16_grok_20260722/`](research/prom16_grok_20260722/INDEX.md)).

This is not a victory lap — ooptdd is far smaller and less feature-complete
than these tools were (see [`competitive_feedback.md`](competitive_feedback.md)
§Strategic judgment, which says exactly that). It is a requirements list,
extracted from a category's post-mortem.

## The category record

### Tracetest — commercial Cloud EOL, dormant main

Tracetest was the most complete trace-based-testing product: a mature span
selector DSL, polling profiles, a CLI/server/agent architecture, a cloud
offering. The record, precisely stated:

- **Commercial Cloud EOL 2024-10-31** (tracetest.io blog announcement). The
  EOL wording itself says adoption did not justify continued investment.
- **Dormant main**: last commit to the OSS repository 2025-06-03 at the
  research snapshot — roughly 13.5 months idle.
- **Not dead, not archived.** The OSS repo carries no archive banner and this
  doc makes no claim beyond commercial EOL + dormancy
  ([`C_limitations.md`](research/prom16_grok_20260722/C_limitations.md) C3
  caveats state this explicitly).

The instructive part: the feature ooptdd conspicuously lacks — a rich
span-tree selector language — is the feature Tracetest had in abundance. It
was not enough, which is evidence the category's problem was somewhere else.

### malabi — archived, with the fatal function still readable

malabi (Aspecto's in-process TBT library for Node) is the clean autopsy,
because its killer is a single readable function. In
`packages/malabi/src/remote-runner-integration/fetch-remote-telemetry.ts`:

- lines 33–35: the catch block — a failed telemetry fetch logs
  `'error while fetching remote telemetry'` and falls through;
- line 36: `return initRepository([]);` — the caller receives an **empty span
  repository**.

So when the collector is down, every assertion runs against an empty
repository: absence checks **pass** (could-not-observe silently collapsed into
nothing-observed) and presence checks read as product failures. There is no
third value; the verifier cannot report its own blindness.

The repository is archived (2024-10-31, the same day as the Tracetest Cloud
EOL, after the SmartBear acquisition of Aspecto orphaned it — C_limitations C3).
Last commit 2024-05-16; the project never left version `0.0.7-alpha.1`
(`lerna.json`).

## What actually killed the category

Per the C3 finding in
[`C_limitations.md`](research/prom16_grok_20260722/C_limitations.md), the
category died from three compounding causes — and notably *not* from missing
selector features:

1. **timeout = fail.** Wait-budget exhaustion was mapped to assertion failure.
   Ingestion lag (a store property) surfaced as flaky RED (a product verdict),
   and teams learned to ignore their gates — the one outcome a testing tool
   cannot survive.
2. **incomplete-trace-as-ready.** Pollers used heuristics like span-count
   stability to declare a partial trace "ready", then judged the prefix as if
   it were the whole trace.
3. **commercial non-adoption of the platform path.** The server/agent/UI/cloud
   surface never earned adoption proportional to its cost — Tracetest's own
   EOL wording is the primary evidence.

Caveat, carried over honestly from the research: the evidence for (1) and (2)
is architectural (poller source, polling-profile docs, EOL wording), not a
large corpus of user complaint threads. The mechanism is readable in the code;
the causal weighting is inference.

## ooptdd's answer, in code that exists

Each killer maps to a landed mechanism. File:line references are to the
current tree.

### 1. The blind window is declared, not hoped about

Every store has an ingest-to-queryable lag. ooptdd makes the backend *declare*
it as typed data instead of letting a retry loop guess:

- `BackendCaps.query_visibility_delay_ms`
  ([`src/ooptdd/domain/ports.py:130-157`](../src/ooptdd/domain/ports.py); the
  field's docstring at :145-148 states the invariant: *the poller never
  concludes ABSENT while the total wait is still inside this window*). Values
  come from each store's own documentation, per driver:
  - ClickHouse: 1000 ms — the `async_insert` busy-timeout band
    ([`src/ooptdd/backends/clickhouse.py:45-46`](../src/ooptdd/backends/clickhouse.py))
  - OpenObserve: 5000 ms — the memtable/WAL persist interval
    ([`src/ooptdd/backends/openobserve.py:30-31`](../src/ooptdd/backends/openobserve.py))
  - VictoriaLogs: 1000 ms
    ([`src/ooptdd/backends/victorialogs.py:65-66`](../src/ooptdd/backends/victorialogs.py))
- A flushable store gets one best-effort flush before the first read
  ([`src/ooptdd/engine/verify.py:94-100`](../src/ooptdd/engine/verify.py); a
  broken flush endpoint never gates anything). VictoriaLogs implements it via
  the `POST /internal/force_flush` endpoint its docs recommend for automated
  tests ([`victorialogs.py:128-138`](../src/ooptdd/backends/victorialogs.py)).
- The **blind-window guard**: when the retry budget is spent but the store
  answered and the total wait has not yet covered the declared visibility
  delay, the poller extends once past the window — bounded by the declaration,
  not by hope — and re-reads before any negative settle
  ([`engine/verify.py:147-160`](../src/ooptdd/engine/verify.py)). Wait
  exhaustion inside the blind window can therefore never produce `absent`.
  This is killer #1's fix as a code path, not a docs promise.

### 2. A prefix is never judged as the whole trace

Killer #2 was semantic: treating "the reads stopped changing" as "the trace is
complete". ooptdd's poller never has a "ready" heuristic to get wrong, because
the verdict semantics carry the prefix-ness:

- `absent` (⊥) requires the *last* read to be reachable **and** complete;
  unreachable or truncated final reads are `inconclusive`
  ([`engine/verify.py:258-272`](../src/ooptdd/engine/verify.py) for the pytest
  summary, [:378-384](../src/ooptdd/engine/verify.py) for arbitrary gates).
- A non-final poll settles GREEN early **only when the green is irrevocable**
  — every gating check latched LTL₃ SAT, meaning no extension of the prefix
  can falsify it ([`_settled_green`, engine/verify.py:291-329](../src/ooptdd/engine/verify.py)).
  A gate carrying any anti-monotone check (`forbid`/`absent`, exact or
  upper-bound counts, ordering) waits for the final window, so a late-arriving
  violation still flips the verdict.
- `confirm_rounds` anti-flap: a green that settled on the final read (i.e. was
  never irrevocable) can be re-read N extra rounds; any round that is no
  longer green wins ([`engine/verify.py:169-173`](../src/ooptdd/engine/verify.py)).

### 3. Timeout is not failure — the third value never fails the build

malabi had two verdicts and chose the wrong one when blind. ooptdd's verdict
lattice is three-valued (LTL₃ — see
[`METHODOLOGY.md`](../METHODOLOGY.md) "What three-valued precisely means"),
and the build policy enforces the honesty:

- `verify_policy` maps `inconclusive` to a warning with `fail_build: False` —
  "observability infra unreachable, build unaffected **even in strict**"
  ([`engine/verify.py:436-444`](../src/ooptdd/engine/verify.py)). Only
  `strict` + `absent` fails (:445-454). The one exception cuts the other way:
  a *forged* receipt (invalid HMAC) always fails, even in `warn` (:411-419) —
  catching tampering is a positive detection, not an observation gap.
- The runnable proof is
  [`examples/openobserve_demo/demo_inconclusive.py`](../examples/openobserve_demo/demo_inconclusive.py):
  it points the verifier at a dead endpoint and asserts the verdict is
  `inconclusive`, never `absent` — the exact scenario where malabi's tests
  went green over an empty repository. Run it; this page deliberately does not
  duplicate it.

### 4. And the commercial killer: stay a library

Killer #3 is a product-shape lesson, not a code path. ooptdd's response is
subtraction: no server, no agent, no UI, no cloud, no span-tree selector DSL,
no LLM-as-judge scoring — the explicit anti-goals in
[`competitive_feedback.md`](competitive_feedback.md) §What not to do. The
product surface is a pytest plugin, a CLI, and YAML gates in the consumer's
repo. Whether that shape earns adoption is an open question, answered so far
only by [`case_studies.md`](case_studies.md) — three real consumers, described
with the same wiring honesty this page tries to hold (each case states where
the receipt actually runs, and only one clears the "blocking CI" bar).

## What this page does not claim

- That Tracetest OSS is dead or archived — it is not; the claim is commercial
  Cloud EOL plus a dormant main.
- That ooptdd is more capable than these tools were. It is smaller, younger,
  and has fewer features; its bet is that the category died of *semantics*,
  and that getting the semantics right matters more than the feature list.
- That the three-killer analysis is proven from user telemetry. It is an
  architectural reading of code, docs, and the vendors' own words.

## The positioning, in one falsifiable line

From [`competitive_feedback.md`](competitive_feedback.md) §Strategic judgment:

> The expected runtime evidence must arrive in an independent store.

Everything above is machinery for refusing to lie about that sentence when its
truth cannot yet be known.
