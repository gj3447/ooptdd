# ooptdd — the methodology (LTDD)

> ooptdd = *oo positive-TDD* = LTDD (log-based TDD). This is the public,
> infrastructure-neutral writeup. The empirical backbone behind it (a multi-axis
> prior-art and design study) lives in [`docs/research/`](docs/research/).

## One line

**Logs and traces are the specification and the ground truth.** An agent's — or a
function's — "it worked" is not evidence. You write the expected event sequence
first, then judge the implementation by whether it actually emits that trace,
read back from an external store.

## The cycle (Red → Green → Refactor, re-pointed)

| step | classic TDD | LTDD |
|---|---|---|
| **Red** | write a failing unit test | write the expected event-trace spec (YAML: event sequence + count/threshold gates). It fails — nothing emits it yet. |
| **Green** | make the test pass | the implementation emits real structured events; `verify_trace` / the gate passes by reading the store. |
| **Refactor** | keep the test green | the same event contract still holds — *golden-trace regression*. |

Academic roots: **runtime verification** (judging a partial trace against a
temporal predicate — the formal basis of trace assertions) + **observability-driven
development** (instrumentation as assertions) + **property-based testing**
(invariants). In practice a simplified event sequence is enough; temporal-logic
notation stays as design vocabulary.

### What "three-valued" precisely means (and what ooptdd is *not*)

The verdict lattice — `present (⊤)` / `absent (⊥)` / `inconclusive (?)` — is exactly
the **LTL₃** three-valued semantics of Bauer, Leucker & Schallhart, *"Runtime
Verification for LTL and TLTL"* (ACM TOSEM 20(4), 2011). A monitor only ever sees a
finite, growing **prefix** of the trace, so plain boolean truth is wrong: `?` means
"this prefix neither satisfies nor falsifies the property yet." That is also why
`inconclusive` never fails the build — demoting "couldn't observe" to "falsified" is
how a network blip becomes a flaky test.

Be honest about the expressiveness, though. ooptdd evaluates a deliberately small
**fragment**, not full LTL:

- **counting / cardinality** (`count`, `op`, `ratioMetric`) — "≥ N of event X",
- **bounded-time windows** (`timeWindow`, and the readback poll) — a bounded `F`/`G`,
- **ordering** (`must_order`) — first-occurrence sequencing,
- evaluated **past-time** over what has already arrived (cf. first-order past-time LTL,
  DejaVu) — so a verdict is always reachable; we never block on the future.

This is a strict sub-logic of MTL + past-LTL under LTL₃ verdicts. The restriction is
intentional: it is decidable, needs no monitor synthesis, and is robust on
eventually-consistent stores. So the correct claim is **"LTL₃ verdicts over a
counting/past-time fragment"** — *not* "full LTL." For genuinely time-metric
properties (heartbeats, max inter-event gaps) the principled extension is MTL's
bounded-interval operators `F[a,b]` / `G[a,b]` (see Tier-3 `within`).

## 7 principles

1. **Spec-as-observability.** Before writing the test, write the expected event
   sequence per correlation id as data (YAML) in the repo. The spec file — not
   the code, and not the agent — is the judge.
2. **correlation_id is mandatory.** Mint one id per test/cycle and propagate it
   through every log and request boundary (contextvars for async; explicit
   hand-off across thread/queue boundaries).
3. **Assert on structured events only.** JSON envelopes (`metadata`:
   cid/ts/service/level + `payload`: event/attrs). Never assert on free-text log
   messages — that resurrects the oracle problem (refactoring the wording breaks
   the test).
4. **Ingest is asynchronous.** Flush, *then* query, with retries; suspect ingest
   loss first. (A real bug we hit: the assertion queried *before* the ship landed
   and always saw an empty result.)
5. **Assertion strength is gradual.** existence → order (only where ordering is
   deterministic; use sets/quorum for concurrent regions) → field values →
   invariants → causality. A total-order assertion from day one is a flake
   factory.
6. **Externalize the verdict — this is what defeats self-deception.**
   ① the gate spec is YAML in the repo (the agent only *proposes*);
   ② ingestion is done by an external collector the agent can't tamper with;
   ③ the generator ≠ the verifier;
   ④ a claim is checked against the trace *receipt*;
   ⑤ cross-check external state (exit code, diff). *AI agents lie about their
   work; outcome-based verification catches it.*
7. **Name the log-free zones.** Precise numeric regression (use snapshots /
   metrology), security (pre-emit redaction + dedicated tooling), concurrency
   races at µs resolution — do **not** verify these with log assertions.

## "Positive" — arrival assertion (witness vs judge)

`ship()` returning without raising only *reports* delivery. `verify_trace` polls
the store and **positively asserts the record exists**. The verdict is
three-valued (LTL3 `{⊤, ⊥, ?}`) because a test only ever sees a trace prefix:

- `present` (⊤) — the `test_session` record was observed.
- `absent` (⊥) — the query succeeded but the record never arrived → **silent
  ingest loss** suspected.
- `inconclusive` (?) — the query itself never succeeded (store unreachable) →
  unrelated to the system under test, so it must **never** fail the build, even
  in strict mode. Demoting `?` to `⊥` is exactly how a timeout becomes a flaky
  test.

EXISTENCE is prefix-monotone (it can close to ⊤); ABSENCE stays `?` forever —
which is the formal reason "total-order / absence assertions are a flake factory".

## Agent loop protocol

```
edit → run (pytest/harness) → query the store, aggregation-first
     → on error: jump from the event's anchor to the source location
     → fix → re-run … → gate GREEN → ship (a FAIL is recorded with its anchor)
```

- Multiple agents share **one** store of record; the orchestrator mints the cid,
  children propagate it.
- Give agents **context** (the test map / spec), not step-by-step orders.
- No ambient env inheritance — hand-offs pass explicit cid + flags only.

## 6 pitfalls (standing checklist)

cardinality bomb (no unique-value labels) · LLM token blowup (aggregate first) ·
correlation breakage (propagate across boundaries) · schema drift (version +
validate) · ingest loss/lag (heartbeat + health checks first) · auth sprawl
(dedicated ingest account + rotation).

## Adoption roadmap (new project)

- **P1 (~1 week):** structured envelope + cid propagation + central ingest.
- **P2 (~1 week):** test fixture / plugin + gate YAML + the post-run hook.
- **P3 (incremental):** golden-trace regression + source anchors + raise
  assertion strength.

Acceptance test for the whole thing: *can an agent root-cause a FAIL from the
store alone?*

## Honest caveats

No long-horizon (6-month+) operational data yet — this is same-day empirical plus
literature. OpenTelemetry GenAI semantic conventions are still maturing. Large
scale (1000+ events/s) is unproven here. Keep a human in the loop on the critical
path: ooptdd deliberately does **not** treat *absence of evidence* as automatic
failure (`inconclusive` ≠ fail) — that is a condition of correct use, not a
suggestion.
