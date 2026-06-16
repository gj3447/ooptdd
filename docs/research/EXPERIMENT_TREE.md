# Experiment tree — ooptdd AI/KG/ontology-native evolution

Mirror of `LakatosTree_ooptdd_ontology_20260616` (Neo4j). The tree is the canon;
this doc is the human-readable shadow.

## Hard core (constitution — invariant unless falsified repeatedly)

1. logs/traces = spec & ground truth; GREEN only on positive arrival (read the store back).
2. verdict is 3-valued (LTL3): present / absent / inconclusive. `inconclusive`
   (store unreachable) **never** fails the build.
3. `off` = no-op and `memory` (offline) must always work — KG/ontology are **never**
   a hard dependency.
4. generator ≠ verifier: the agent cannot self-certify; the receipt comes from
   running the code and reading the store.
5. every added layer must catch a **real** failure the previous layer cannot
   (anti over-formalization / formal-cathedral).
6. secrets env-only; no internal coupling in the public core.

## Nodes

| tag | verdict | metric | what |
|---|---|---|---|
| **V0-kg-substrate** | `proof` | substrate loads & queryable | `ooptdd/ontology.py` (file-first) + KG `LabelConvention` (Ooptdd Requirement/Gate/EventType/Verdict) + `OoptddOntology` mirror. Enables V1; no metric of its own. |
| **V1-ontology-typed-gate** | `canonical_stage` (progressive) | **conformance_violations_caught = 3** (target ≥3) | `conforms: <EventType\|*>` gate check. Pre-registered prediction met: 3 hallucination classes the flat gate marks GREEN, the ontology gate marks RED. |
| **V2-kg-native-loop-io** | `CANONICAL` (progressive) | coverage + drift queryable, offline intact | Pluggable `KgStore` (InMemory offline / Neo4j prod). `write_run` persists `OoptddVerdict` + `ReferenceSite` (sha256 + baseline). coverage(spec)=done/total and drift(spec)=changed-sha both by query alone — proven in-memory **and** on the live workspace Neo4j (coverage cypher → total=4/done=4). Loop still runs with no KG store (hard-core #3). |

### V1 — the metric (pre-registered, then measured)

Baseline (flat event+count gate) catches **0** of these; the ontology gate catches **3**:

| class | fixture | flat | ontology |
|---|---|---|---|
| missing required attr | `payment_authorized` with no `amount` | GREEN | **RED** |
| bad enum value | `order_finalized status=kinda` (∉ {ok,ng}) | GREEN | **RED** |
| unknown event type | fabricated `quantum_flux` (closed-world) | GREEN | **RED** |

Proven by `tests/test_ontology.py::test_metric_three_classes_caught`. Dogfooded in
the loop: `ooptdd_loop/example/requirements_ontology.yaml` went RED on a
real missing-`amount`, then GREEN after the emitter was fixed (commit history:
ooptdd `b1b21f0`, ooptdd_loop `0f31e1a`).

The semantic chain this completes:

> requirement → (ontology) expects an **EventType** → (Longinus) emitted by a real
> **source symbol** → (arrival) **observed** in the store → (KG) verdict as a node.

A hallucination is caught at whichever link breaks: unknown/typo'd name or missing
attribute (ontology), absent emitter (Longinus), no log (arrival).

## Frontier (open questions)

- ✅ `OQ-ooptdd-ontology-catches-more` — **CLOSED by V1** (yes, ≥3 classes).
- `OQ-ooptdd-minimal-eventtype-schema` — minimal schema that earns its keep (current: required + enum/type/min/max).
- `OQ-ooptdd-closed-vs-open-world` — default is open-world; closed-world is opt-in per check (`closed_world: true`). Revisit if drift detection should be a project default.
- ✅ `OQ-ooptdd-ontology-location-offline` — **CLOSED by V2**: file-first canonical; KG mirror via pluggable `KgStore` (InMemory offline / Neo4j prod); projection = code→KG on each `write_run`; KG never a hard dep.
- `OQ-ooptdd-longinus-ontology-compose` — partially answered (chain above); full composition is V2/V4.

## Next branches (not yet run)

- **V2** KG-native loop I/O (requirements from KG, verdicts + ReferenceSite to KG; coverage/drift = cypher).
- **V3** AI-native MCP surface (`ooptdd-mcp`: list_requirements / run_loop / verify / rca / ontology_lookup / propose_gate).
- **V4** ontology reasoning (subsumption + preconditions derived from the ontology) — only if a real failure demands it.
