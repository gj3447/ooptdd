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

### GREEN is a closed-world consistency claim, not a correctness claim

A verdict is `f(emitted_events, spec)` where **both inputs descend from one authority** — the
code you wrote *to emit* and the spec you wrote *to expect*. There is no second, independently
grounded input, so ooptdd is a **derived/pseudo-oracle** (Weyuker 1982): it detects
*disagreement* between emission and expectation, and is **structurally blind to any error common
to both** (a wrong understanding produces a wrong emit *and* a matching wrong/absent expectation —
GREEN). Concretely, GREEN means *“the events I **named** arrived with the asserted shape,”* never
*“the system is correct.”* To keep this honest the gate result carries a `scope` block (`gating`/
`optional`/`pending` counts + per-check `strength`: existence-only < bounded < value-pinned/
ordered/forbid < ratio/liveness/conformance) and the CLI prints it on green. Two rules follow:

- **`asserts_anything` (≥1 *gating* check) is necessary, not sufficient.** A gate whose every
  check is `optional`/`pending` asserts nothing that can fail — it is `vacuous`, never a clean
  pass (this closes the cheapest way to fake green: mark the last check optional/pending). But a
  non-vacuous gate is still only a *closed-world* claim over the events you named; an
  un-instrumented path emits nothing and is simply *outside* the verdict — not present, not
  absent, not inconclusive.
- **Higher `strength` is a harder *self*-check, not an external oracle.** `value-pinned` means a
  `where` field matched — but that field value descends from the *same mental model* as the emit
  (`where: {residual: 0.0}` against a stub that emits `0.0` is green forever). For the *effect*
  behind an event (a payment actually moved, a measurement is in tolerance), you must leave ooptdd
  and assert against the **territory** directly (principle 6 ⑤; the numeric/security/concurrency
  log-free zones of principle 7) — an external oracle ooptdd does not have by construction.

Two tools push against (without escaping) this boundary. **`invariant`** asserts a *relation
between events* — `sum(amount@payment) == sum(amount@shipment)`, `count(request) == count(response)`
within a tolerance — the first check that rises above token-counting toward value *consistency*,
and it kills the emit-without-effect green the moment you assert it (a `payment_authorized` with no
`amount` yields `invariant_no_evidence`, RED). It is still **intra-trace, single-authority**: it
catches inconsistency *between* the system's own events, not event-vs-territory. **`ooptdd lint
<spec>`** is a static, offline audit that refuses a vacuously-satisfiable gate *before* any run —
no gating checks, a `threshold < 1` quorum without justification, or an existence-only gating check
— so a weak gate is caught at author time, not after a green.

One check *does* escape the boundary. **`external:`** is the single verdict input that is **not**
the system's own emit: it asserts against a fact read from the **territory** through an
`ExternalProbe` port — a DB row, a file, a second collector (reference adapters `FileProbe` /
`HttpProbe` / `CallableProbe` in `ooptdd.probes`, resolved like backend drivers; write your own in
five lines). Honesty is held on both ends: a *missing* probe is a loud misconfiguration (never a
silent green), an *unreachable* one is `inconclusive` (never a strict fail), and — the load-bearing
rule — a probe only counts as **corroboration** when it declares `separate_source=True`: a genuinely
different store / service than the one the system wrote its trace to (a probe re-reading the
system's own store is *relocation*, not independence). Corroboration is an *achievement*, not a
check kind — an `external:` check the probe could not reach, or that *refuted* the system,
corroborates nothing.

`separate_source` is **checked, not merely trusted**. The framework derives WHERE it actually
wrote (`oracle.emit_identity` — a driver's own `identity()` or its resolved endpoint URL) and a
probe reports WHERE it actually read (`ProbeResult.derived_identity` — the file path / service URL;
the reference `FileProbe` / `HttpProbe` fill it in). When the two are equal, the probe demonstrably
re-read the system's own endpoint, so the `separate_source` claim is provably false and is
**demoted** to `derived_self` (surfaced as `demoted_same_endpoint` on the check and `oracle.relocated`).
The check is *asymmetric* — a derived identity can only **falsify** a declared `True`, never promote
a missing one — so an honest source whose identity can't be derived keeps its declared bool. This is
not a security boundary (a `CallableProbe` can still report any identity, and shared data lineage —
a read-replica, a mirrored ingest — survives every check); the *irreducible residue* is that
independence ultimately anchors in a source ooptdd cannot prove, only **name and surface**.

This makes the single-authority boundary **measurable** and the green **never silent**. Every gate
result carries an `oracle` block: how many gating checks are `corroborated` (separate-source
`external:`) vs `derived_self`, `single_authority` when *zero* are independently corroborated — the
meta-blind-spot named, a green where the system only agrees with itself — plus `emit_backend` /
`emit_identity` so a reviewer sees *whose* self-agreement it is without reading the spec.
**`require_corroboration`** (spec key / `OOPTDD_REQUIRE_CORROBORATION`) promotes that signal to a
*gate*: with it on, a single-authority green is RED (`uncorroborated`) — a fixable misconfiguration,
add a separate-source `external:`.

Two further signals keep a green honest about *how much it saw*. **Charge** (`scope.charged` /
`charge_ratio` / `uncharged`) counts how many gating checks actually *saw* matching evidence rather
than passing on absence/emptiness (an `absent` that fired on nothing, an exists-check over an empty
store) — orthogonal to strength and to stream-coverage (which counts how many *arrived* event-types
the gate even names). And **`metamorphic`** joins `invariant` as a second intra-trace, oracle-free
consistency check: a relation between two reductions over two matched subsets of the same stream
(`sum(amount@A) == k · sum(amount@B)`), `metamorphic_no_evidence` → RED on a no-data run.

All of these honesty fields roll up into one computable read: **`evidence_tier(result)`** grades a
verdict on a five-rung assertion-strength ladder — `local_pass` < `emitted` < `arrived` <
`queryable_causal` < `external_verdict` — by the strongest *kind* of evidence it actually mustered. It
is the formal, per-verdict answer to "what prevents a fake green": a green that only reaches
`local_pass` (vacuous or unreachable) or `emitted` (events named but `charge_ratio == 0`) is loudly
weak, while `arrived` (positive charge), `queryable_causal` (a passing `invariant`/`metamorphic`
relation), and `external_verdict` (a *separate-source* corroboration) climb toward real strength. It
grades the evidence on offer, not correctness — and it keeps the single-authority boundary honest: only
the top rung needs an oracle that is not the system's own emit, so a non-`separate_source` `external:`
check is self-consistency relocated and reaches only `arrived`, never `external_verdict`.
