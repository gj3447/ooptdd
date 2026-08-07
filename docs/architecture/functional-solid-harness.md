# Functional/SOLID architecture harness

This is an **L_IDE harness**: it constrains repository source changes before they merge. It is
not an application-agent runtime (`L_RT`) or a managed-cloud control plane (`L_MC`). Ouroboros
remains a caller-driven protocol module; this harness does not invent persistence, scheduling,
or effect-delivery guarantees for it.

The machine-readable authority is `functional-solid-contract.json`. The contract records the
current, honest functional-core/imperative-shell boundary. Direct syntactic purity checks are
opt-in by exact module name; a package prefix never opts in future files automatically:
the machine-enforced `purity_scope` is `direct-module-syntax-only`.

- the exact gate primitives, freeze/value/ontology objects, rules, and kernel modules plus
  the generic Ouroboros identity/model/port/receipt/reducer modules are the `pure_core`;
- `ooptdd.domain` owns inward abstractions;
- package facades, adapters, and the package API remain shells that compose concrete behavior
  outward. Separately distributed extensions compose through `ooptdd.sdk`; importing the generic
  protocol API does not load an external workflow distribution.

A leaf module may import only the package-root symbols explicitly listed in
`package_root_import_allowlist`; the current list contains only `__version__`. Every other
package-root import is an outward edge. `FA001` separately rejects root/leaf import cycles.

`scripts/check_functional_architecture.py` implements eight deterministic checks:

| Rule | Constraint |
|---|---|
| `FA000` | every shipped module is explicitly assigned to a reviewed layer |
| `FA001` | no package import cycles |
| `FA002` | dependencies follow the declared layer graph |
| `FA003` | opted-in modules avoid configured ambient imports/direct effect calls, except exact reviewed value-type imports |
| `FA004` | dataclass bindings are syntactically frozen; mutable globals and detected shared-state mutations are forbidden |
| `FA005` | every pure module has an explicit, non-growing per-path responsibility budget |
| `FA006` | environment-key definitions have one owner and ambient reads stay in declared composition shells |
| `FA007` | generic inward layers contain none of the contract-declared adapter/profile vocabulary |

Run it from the repository root:

```console
python scripts/check_functional_architecture.py
python scripts/check_functional_architecture.py --format json
pytest -q tests/test_functional_architecture_harness.py
```

The command exits `0` on success, `1` on architecture violations, and `2` when the contract
cannot be interpreted. JSON violations are stable records with `rule_id`, `path`, `line`,
`symbol`, `message`, and `remediation` fields.

## Honest claim boundary

Budgets labelled `pure_module` cover every exact pure module. The large budgets labelled
`non_pure_debt` are size ratchets over existing shell/facade debt, not purity claims or
endorsements of those sizes. They prevent silent physical growth while allowing later refactors
to lower the numbers. Changing a budget is a visible contract change. New exact pure modules fail
closed until they receive a `pure_module` budget.

AST checks cannot prove referential transparency, deep immutability, semantic substitutability,
or all five SOLID principles. The effect check is deliberately limited to configured import roots
and direct primitive names. FA003/FA004 are module-local and do not inspect the behavior of
imported callees, so they are not a transitive-purity proof; capability behavior remains a
semantic review concern. The canary
suite shows that the declared checks can falsify unclassified modules, planted cycles, outward
dependencies, direct ambient effects, mutable declarations, shared mutations, scattered
configuration, edge vocabulary, and budget overflow. FA007 scans comments and docstrings when
the contract requests it; its precise regexes deliberately avoid broad words that also have
legitimate generic meanings. An exact physical-line debt ratchet can still reject comments.

The gate kernel accepts injected `CheckFn` callables. It gives each handler isolated value
snapshots and validates the returned shape, but neither Python's type system nor this AST harness
can prove that an arbitrary callable is deterministic or effect-free. Handler determinism is a
caller-owned contract backed by focused tests and review, not a mechanically established purity
fact. Runtime determinism, replay, adapter conformance, and effect delivery remain the
responsibility of their dedicated tests and external evidence boundaries.
