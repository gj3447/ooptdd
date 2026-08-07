# Event-contract semantics and design boundaries

This document describes the framework's semantics. It is not a required
development process. TDD, pytest, CI, agents, and other workflows are possible
consumers of the same event-contract API.

## Model

An evaluation is a deterministic function of explicit inputs:

```
verdict = judge(contract, observed_events, source_facts, policy, extensions)
```

The contract and events are ordinary structured values. Source facts describe
reachability, completeness, backend identity, independence, and sampling.
Policies and extension registries are immutable snapshots before they enter the
kernel. Files, environment variables, clocks, network queries, and plugin
discovery belong to outer adapters.

## Three-valued verdict

The framework preserves three distinct states:

- `present`: the required evidence was observed and the contract passed.
- `absent`: a reachable, complete read did not satisfy the contract.
- `inconclusive`: the evidence source was unreachable or incomplete.

This follows the useful operational distinction of LTL3 over a finite trace
prefix. `inconclusive` is not silently converted into falsification. A caller may
choose build or deployment policy separately from the evidence verdict.

The implemented monitor language is a bounded counting and past-time fragment,
not full LTL. It includes presence, absence, cardinality, ordering, bounded
windows, ratios, invariants, metamorphic checks, schemas, and registered custom
predicates.

## Positive arrival verification

An emitter returning successfully proves only that it attempted delivery.
Arrival verification queries through a backend read port and applies a bounded
`PollingSettings` value:

- the first read is immediate;
- retries use bounded exponential backoff;
- a backend-declared visibility delay can extend the final read once;
- optional confirmation rounds protect revocable `present` verdicts;
- clocks and sleepers are injected for deterministic replay.

The same policy flows through the generic poller, gate verification, trace
adapter, and session adapter. Runtime defaults live in the immutable settings
model rather than individual call sites.

## Mechanism and policy

The package separates reusable mechanism from application choices:

| Mechanism | Application policy |
|---|---|
| evaluate structured events | which event vocabulary to use |
| poll through a backend port | retry and confirmation values |
| report evidence state | how verdicts affect caller policy |
| register a check predicate | which extensions to import |
| expose source provenance | required independence/corroboration |

Environment and project configuration are captured once by
`ooptdd.bootstrap.compose_runtime()`. The kernel does not consult ambient state.

## Extension boundaries

The base import installs no domain vocabulary. Applications explicitly import
or register:

- custom checks through `check()`;
- ontology values through `Ontology.from_dict()` or `Ontology.from_file()`;
- backend drivers through `ooptdd.backends` entry points;
- probes through the `ExternalProbe` port;
- trajectory checks through the separately installed `ooptdd_trajectory` package;
- GenAI helpers through the separately installed `ooptdd_genai` package.

The pytest plugin is an optional adapter in `ooptdd.plugin`. It is not activated
by package installation. Repository benchmarks and evidence-integrity tooling
live outside `src/ooptdd` and are not wheel contents.

## Evidence honesty

A `present` verdict means the named observations satisfy the named contract. It does
not automatically prove that the emitting system performed the real-world
effect honestly. Both the events and contract may descend from one mistaken or
malicious authority.

The result therefore surfaces:

- backend and source identity;
- read reachability and completeness;
- independent-store capability;
- corroboration from a separate external probe;
- check strength, charge, and evidence tier;
- authentication status when signing is explicitly configured.

For stronger claims, query a store outside the emitter and bind critical checks
to a genuinely separate source. See [THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Appropriate and inappropriate uses

Event contracts work well for observable lifecycle completion, protocol
ordering, missing or duplicated records, bounded liveness, schema conformance,
and cross-event consistency.

Do not substitute event claims for direct measurement of precise numerics,
secret redaction, microsecond concurrency behavior, or an external business
effect. Those require specialized or territory-level oracles.

## Optional workflow interpretations

A team may write a contract before implementation and use RED/GREEN feedback,
or attach the framework to pytest, a deployment gate, a service monitor, or a
custom runner. These are adapter-level workflows. None changes the core
semantics or becomes required data inside the package.

The experimental `ooptdd.ouroboros` namespace similarly provides deterministic
state transitions and evidence contracts. It does not prescribe a development
methodology and does not contain a daemon, persistence service, or autonomous
execution loop.
