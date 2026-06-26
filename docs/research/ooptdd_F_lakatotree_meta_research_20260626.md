# ooptdd — LakatoTree-based meta research

> **Cycle**: local LakatoTree reading, 2026-06-26  
> **Question**: What does ooptdd look like when evaluated as a Lakatos-style
> research programme rather than only as a pytest plugin/library?  
> **Sources read**: this repo's `README.md`, `METHODOLOGY.md`,
> `docs/research/README.md`, `docs/research/EXPERIMENT_TREE.md`, and sibling
> workspace `<WORKSPACE>/PROJECT/PI/lakatotree` (`README.md`, `THEORY.md`,
> `docs/OOPTDD_R3_RECONCILE_HANDOFF_20260616.md`, vendored manifest/drift test).

## TL;DR verdict

`ooptdd` is best treated as the **verification stratum** inside a larger
LakatoTree programme:

- `ooptdd` answers: *did the named event evidence actually arrive in a queryable
  store, with a non-self-reported verdict?*
- `lakatotree` answers: *does that receipt support a pre-registered novel
  prediction in a research programme, with lineage, rival comparison, and
  abandonment rules?*

That makes `ooptdd` a strong lower-level receipt engine, not a complete
scientific-verdict engine. Its hard core is progressive as long as every new
layer catches a real self-deception or silent-loss failure that return-value
tests, in-process captures, or LLM self-reports miss.

## LakatoTree placement

| LakatoTree concept | ooptdd equivalent | Assessment |
|---|---|---|
| hard core | logs/traces as spec and ground truth; positive readback; generator != verifier; three-valued verdict | coherent and already encoded in `METHODOLOGY.md` and `EXPERIMENT_TREE.md` |
| protective belt | backend drivers, gate YAML vocabulary, ontology checks, probes, pytest plugin policy | healthy, but should remain replaceable; no backend or DSL is the theory itself |
| novel prediction | a pre-written gate predicts a specific event/trace contract before implementation | present, but should be made explicit in docs and examples as "prediction registration" |
| external measurement | store readback through `Backend.query`; `external:` probes for separate-source corroboration | strong for arrival, bounded for correctness; `external:` is the escape from single-authority green |
| rung/verdict | `evaluate(...)` result with scope/oracle/evidence tier | usable as LakatoTree evidence, but not itself a full progressive/degenerative verdict |
| Longinus/source binding | `must_emit` / source-symbol + sha drift discipline, plus vendored drift checks downstream | strategically important; without it, a source-less GREEN can still be a hallucination |
| rival programme | Tracetest, Keptn/OpenSLO, Pact, LLM eval frameworks, in-process log capture | existing A-E research already maps rivals; the missing comparison is at the "self-deception closure" level |

## Hard core restated

The ooptdd hard core should be worded as six falsifiable commitments:

1. A local return value is a claim, not evidence.
2. An event is evidence only after a verifier reads it back from the configured
   store.
3. Store-unreachable is `inconclusive`, never a build-failing `absent`.
4. The gate is registered before the implementation is accepted.
5. The event generator and verifier are different roles.
6. A stronger layer is justified only when it blocks a concrete fake-green class.

This wording matters because it prevents over-claiming. `ooptdd` does not prove a
system correct. It proves that a named, scoped, pre-registered trace contract was
observed, and it reports how much independent corroboration that observation has.

## Progressive results already earned

### P1: Silent ingest loss

**Prediction.** A function can return `ok` while telemetry never lands. A
readback gate should fail where a return-value test passes.

**Status.** Progressive. This is the README's killer demo and the core reason
for positive readback.

### P2: LTL3 honesty

**Prediction.** A finite trace monitor needs `present / absent / inconclusive`;
network or backend failure must not collapse into falsification.

**Status.** Progressive. `METHODOLOGY.md` now states the bounded counting /
past-time fragment honestly rather than claiming full LTL.

### P3: Ontology-typed gates

**Prediction.** Flat event-count gates miss at least three hallucination classes
that schema-aware gates catch: missing required fields, bad enum values, and
unknown closed-world event types.

**Status.** Progressive, mirrored in `EXPERIMENT_TREE.md` as V1 with
`conformance_violations_caught = 3`.

### P4: External corroboration boundary

**Prediction.** A GREEN based only on the system's own emitted stream is a
single-authority consistency claim; a separate-source probe is needed to rise to
external-verdict evidence.

**Status.** Progressive and important. The current oracle/evidence-tier language
turns a philosophical limitation into a visible runtime signal.

### P5: Consumer drift detection

**Prediction.** Downstream vendored copies can become mixed snapshots; drift
must be detected without pretending the consumer is wrong.

**Status.** Progressive. LakatoTree's vendored manifest and drift test are a
good example of ooptdd's "receipt over claim" rule applied to dependency state.

## Degeneration risks

| Risk | Why it is degenerative | Guardrail |
|---|---|---|
| gate inflation | adding DSL surface without catching a new fake-green class | require every new gate feature to name the failure class it uniquely catches |
| self-consistency green | emit code and expected gate share the same wrong model | keep `oracle.single_authority`, `require_corroboration`, and evidence tiers visible |
| backend absolutism | treating one store/query language as core truth | keep `Backend` minimal and capability-honest; query portability remains false |
| source-less success | agent proposes a gate/event name that no real source symbol emits | make Longinus/source binding part of serious agent-loop adoption, not optional decoration |
| over-formalization | importing full temporal logic/KG machinery before it catches real failures | prefer bounded, testable fragments with concrete red fixtures |
| vendored drift confusion | editing downstream copies or syncing over active WIP | preserve manifest drift tests and coordinate re-vendor ownership before writes |

## Next LakatoTree-style experiments

These are phrased as pre-registered predictions so that future work can be
judged as progressive or degenerative.

### F1: Source-less GREEN rejection

**Prediction.** Given a gate that names an event with no bound source symbol,
plain arrival/count logic can be green or vacuous, but a Longinus-bound gate must
return a blocking verdict before acceptance.

**Measurement.** A test fixture with one valid bound emitter and one fabricated
event name. Target: fabricated event cannot produce a clean GREEN even if the
gate is otherwise syntactically valid.

**Status.** Implemented in the core gate layer as `require_source_bindings: true`
plus `source_bindings` / `must_emit` metadata. The implementation is deliberately
no-KG and stdlib-only: it resolves Python symbols by AST without importing the
target module, optionally hashes the symbol body, and reports failures in the
top-level `longinus` block. Arrival can pass while the final verdict is still
RED with `source_unbound=true`.

### F2: Separate-source corroboration promotion

**Prediction.** Two gates with identical event arrivals differ in evidence tier
when one has a separate-source `external:` probe and the other only re-reads the
emit backend.

**Measurement.** Assert `external_verdict` only for derived-distinct probe
identity; assert same-endpoint demotion for relocated/self probes.

### F3: Pact-style pending expectation

**Prediction.** A newly registered expectation should not create a false RED
before any implementation is intended to satisfy it; it should be visible as
pending/inconclusive until first real evidence exists.

**Measurement.** Registry matrix over `(emitter_sha, gate_sha, environment)` with
`pending -> first_passed -> required` transitions and no silent removal.

### F4: Backend capability falsification

**Prediction.** A backend that cannot query a required gate feature must return a
capability/inconclusive verdict, not approximate success.

**Measurement.** Run the same gate against memory, SQL-capable backend, and
write-only OTLP backend; assert only query-capable backends can produce a clean
arrival verdict.

### F5: Drift-safe consumer sync

**Prediction.** A consumer vendored copy can be reconciled from canonical ooptdd
without overwriting unrelated consumer WIP and with manifest drift returning to
green.

**Measurement.** Dry-run first, apply only owned files, run the consumer drift
test, and record commit/test evidence.

## Design recommendation

Keep `ooptdd` as a small, dependency-light receipt engine. Let LakatoTree own the
larger research-programme loop: pre-registration semantics, progressive versus
degenerative appraisal, rival comparisons, lineage, and abandonment rules.

The shared contract between them should be explicit:

```text
LakatoTree prediction
  -> ooptdd gate/spec
  -> implementation emits structured events
  -> ooptdd readback verdict + scope/oracle/evidence_tier
  -> LakatoTree rung / evidence record / branch credence update
```

This avoids two bad outcomes: making `ooptdd` too philosophical to adopt, or
making LakatoTree trust agent self-reports without a hard receipt layer.

## Open gaps

- The public README could state the LakatoTree relationship in one paragraph:
  ooptdd is the receipt/verifier substrate, not the full programme engine.
- Examples should distinguish "arrival evidence" from "territory correctness"
  more sharply; the `external:` probe story is the key bridge.
- The research tree should keep a living "failure class registry": every feature
  maps to the fake-green or silent-loss class it uniquely blocks.
- Consumer vendoring needs an ownership protocol before automated sync writes,
  as already noted in LakatoTree's R3 handoff.
