"""Generic ``ooptdd`` command line for event-contract operations.

    ooptdd verify <cid> --gate spec.yaml [--backend memory]
    ooptdd gate <spec.yaml> [--backend memory] [--report md] [--report-out path]
    ooptdd ontology check <onto.yaml> --events events.json [--event-type T] [--closed-world]
    ooptdd ontology compat <old.yaml> <new.yaml> [--mode backward|forward|full]
    ooptdd backends list
    ooptdd backends doctor [--backend B]
    ooptdd monitor <spec.yaml> [--backend B]
    ooptdd verify-chain --records records.json --key-env OOPTDD_SIGNING_KEY [--evolve]
    ooptdd schema [gate|ontology]
    ooptdd version

The command only exposes domain-neutral operations. Optional adapters and
specialized analysis commands have their own explicit module entry points.

Settings come from ``[tool.ooptdd]`` in the working-directory ``pyproject.toml``, overridden
by environment variables and the flags below. Exit codes are 0 for a satisfied contract,
1 for a conclusive violation, and 2 for inconclusive evidence or invalid usage. Human
summaries go to stderr; ``--json`` machine output goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .backends import BackendRegistry
from .bootstrap import compose_runtime
from .config import SETTING_DEFINITIONS
from .domain.model import ENVELOPE_SCHEMA, signature_status, verify_chain
from .domain.ontology import Ontology, check_conformance, ontology_compat
from .domain.ports import backend_caps
from .domain.settings import DEFAULT_ENV_KEYS
from .engine.gate import (
    compare_strength,
    load_gate,
    resolve_gate_policy,
)


def _runtime_overrides(args) -> dict[str, object]:
    """Only explicit CLI values override project and environment settings."""

    return {
        definition.field: value
        for definition in SETTING_DEFINITIONS
        if (value := getattr(args, definition.field, None)) is not None
    }


def _runtime(args):
    runtime = compose_runtime(overrides=_runtime_overrides(args))
    return runtime.activate_extensions()


def _backend(args):
    return _runtime(args).backend()


def _exit(ok: bool, reachable: bool, complete: bool = True) -> int:
    """Map contract evidence onto the stable CLI exit-code contract."""
    if ok:
        return 0
    if not reachable or not complete:
        return 2
    return 1


def _emit(payload: dict, args, human: str, level: str = "info") -> None:
    """JSON to stdout under ``--json``; otherwise a one-line human summary to stderr."""
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(human, file=sys.stderr)


def _load_json_file(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_probe(spec: dict):
    """A spec may name an external-oracle probe (``probe: {name: file, root: ...}``); resolve it
    via the probe registry so an ``external:`` check is usable from the CLI. None if absent."""
    p = spec.get("probe")
    if isinstance(p, dict) and p.get("name"):
        from .probes import get_probe

        return get_probe(p["name"], **{k: v for k, v in p.items() if k != "name"})
    return None


# Verify an arbitrary gate specification.
def _cmd_verify(args) -> int:
    runtime = _runtime(args)
    backend = runtime.backend()
    polling = runtime.settings.polling
    gate = load_gate(args.gate)
    policy = resolve_gate_policy(gate, runtime.environment, env_keys=runtime.env_keys)
    res = runtime.verify(
        backend,
        args.cid,
        gate,
        polling=polling,
        probe=_resolve_probe(gate),
        policy=policy,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    verdict = res["verdict"]
    message = {
        "present": "SATISFIED - arrival confirmed",
        "absent": "VIOLATED - expected events did not arrive",
        "inconclusive": "INCONCLUSIVE - store unreachable",
    }[verdict]
    print(f"{message}: {res.get('reasons')}", file=sys.stderr)
    return _exit(res["ok"], verdict != "inconclusive")


def _cmd_gate(args) -> int:
    runtime = _runtime(args)
    backend = runtime.backend()
    spec = load_gate(args.spec)
    policy = resolve_gate_policy(spec, runtime.environment, env_keys=runtime.env_keys)
    res = runtime.evaluate(
        backend,
        spec,
        probe=_resolve_probe(spec),
        policy=policy,
        environ=runtime.environment,
        env_keys=runtime.env_keys,
    )
    if getattr(args, "report", None):
        from .reports import RENDERERS

        rendered = RENDERERS[args.report](res)
        if getattr(args, "report_out", None):
            with open(args.report_out, "w", encoding="utf-8") as fh:
                fh.write(rendered)
            print(f"report ({args.report}) -> {args.report_out}", file=sys.stderr)
        else:
            print(rendered)
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    if res.get("optional_failed"):
        print(
            f"WARN - optional checks failed (not gating): {res['optional_failed']}", file=sys.stderr
        )
    if res["ok"]:
        print(f"SATISFIED - contract passed (score={res.get('score')})", file=sys.stderr)
        return 0
    if not res["reachable"]:
        print("INCONCLUSIVE - store unreachable", file=sys.stderr)
        return 2
    if not res.get("complete", True):
        print("INCONCLUSIVE - readback truncated (incomplete evidence)", file=sys.stderr)
        return 2
    if not res.get("probe_reachable", True):
        print("INCONCLUSIVE - external probe unreachable", file=sys.stderr)
        return 2
    if res.get("vacuous"):
        print(
            "VIOLATED - vacuous gate: every check is optional/pending, nothing can fail "
            "(mark at least one check gating)",
            file=sys.stderr,
        )
        return 1
    if res.get("uncorroborated"):
        print(
            "VIOLATED - uncorroborated: every gating check is the system's own self-report "
            "(no separate-source external: corroboration); require_corroboration on",
            file=sys.stderr,
        )
        return 1
    print("VIOLATED - gate failed", file=sys.stderr)
    return 1


def _cmd_lint(args) -> int:
    runtime = _runtime(args)
    findings = runtime.lint_spec(load_gate(args.spec))
    if getattr(args, "json", False):
        print(json.dumps({"vacuity": findings}, ensure_ascii=False, indent=2))
    for f in findings:
        print(f"  [{f['severity']}] {f['code']} {f['label']}: {f['message']}", file=sys.stderr)
    high = [f for f in findings if f["severity"] == "high"]
    if high:
        print(
            f"VACUOUS - {len(high)} blocking finding(s); the gate is weak by construction",
            file=sys.stderr,
        )
        return 1
    print(
        "OK - no vacuity findings"
        if not findings
        else f"WARN — {len(findings)} strength finding(s)",
        file=sys.stderr,
    )
    return 0


def _cmd_strength(args) -> int:
    runtime = _runtime(args)
    fp = runtime.strength_fingerprint(load_gate(args.spec))
    if args.write:
        with open(args.write, "w", encoding="utf-8") as fh:
            json.dump(fp, fh, indent=2)
    if args.baseline:
        cmp = compare_strength(_load_json_file(args.baseline), fp)
        _emit(
            {"fingerprint": fp, **cmp},
            args,
            ("WEAKENED — " + "; ".join(cmp["regressions"]))
            if cmp["weakened"]
            else f"OK — strength held (score {fp['score']} >= baseline {cmp['baseline_score']})",
        )
        return 1 if cmp["weakened"] else 0
    _emit(
        fp,
        args,
        f"strength score={fp['score']} gating={fp['gating']} "
        f"by_strength={fp['by_strength']} min_threshold={fp['min_threshold']}",
    )
    return 0


def _cmd_ontology(args) -> int:
    if args.onto_cmd == "check":
        onto = Ontology.from_file(args.ontology)
        res = check_conformance(
            _load_json_file(args.events),
            onto,
            event_type=args.event_type,
            closed_world=args.closed_world,
        )
        _emit(
            res,
            args,
            f"{'PASS' if res['passed'] else 'FAIL'} — checked={res['checked']} "
            f"violations={len(res['violations'])} unknown={res['unknown']}",
        )
        return 0 if res["passed"] else 1
    # compat
    old, new = Ontology.from_file(args.old), Ontology.from_file(args.new)
    res = ontology_compat(old, new, mode=args.mode)
    _emit(
        res,
        args,
        f"{'COMPATIBLE' if res['compatible'] else 'INCOMPATIBLE'} "
        f"({res['mode']}) — {res['violations']}",
    )
    return 0 if res["compatible"] else 1


def _cmd_backends(args) -> int:
    if args.be_cmd == "list":
        names = BackendRegistry().names()
        _emit({"backends": names}, args, "backends: " + ", ".join(names))
        return 0
    # doctor: construct + probe reachability of the configured backend
    try:
        backend = _backend(args)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)}, args, f"ERROR — {exc}")
        return 1
    caps = backend_caps(backend)
    info = {
        "backend": type(backend).__name__,
        "queryable": caps.queryable,
        "write_only": caps.write_only,
        "default_lookback_s": getattr(backend, "default_lookback_s", None),
        "default_future_buffer_s": getattr(backend, "default_future_buffer_s", None),
    }
    if not caps.queryable:
        info["reachable"] = None  # write-only: nothing to probe
        _emit(
            info,
            args,
            f"WRITE-ONLY — {info['backend']} has no read side (strict verify impossible)",
        )
        return 0
    res = backend.query("__ooptdd_doctor_probe__", since_us=0, until_us=1)
    info["reachable"] = res.reachable
    info["error"] = getattr(res, "error", None)  # WHY it failed (401 vs DNS vs unconfigured)
    human = (
        f"{'OK' if res.reachable else 'UNREACHABLE'} — {info['backend']} reachable={res.reachable}"
    )
    if info["error"]:
        human += f" ({info['error']})"
    _emit(info, args, human)
    return 0 if res.reachable else 2


def _cmd_monitor(args) -> int:
    # single-shot: surface the per-check streaming verdict/settled_at the kernel already
    # produces inside evaluate. A bounded re-poll/watch belongs to an application-owned
    # orchestrator, not the stateless core.
    runtime = _runtime(args)
    backend = runtime.backend()
    spec = load_gate(args.spec)
    res = runtime.evaluate(
        backend,
        spec,
        policy=resolve_gate_policy(spec, runtime.environment, env_keys=runtime.env_keys),
        environ=runtime.environment,
        env_keys=runtime.env_keys,
    )
    view = {
        "cid": res["cid"],
        "ok": res["ok"],
        "reachable": res["reachable"],
        "complete": res.get("complete", True),
        "checks": [
            {
                "label": c.get("label")
                or c.get("event")
                or next(
                    (
                        k
                        for k in (
                            "present",
                            "absent",
                            "must_order",
                            "conforms",
                            "heartbeat",
                            "ratio",
                            "invariant",
                            "aggregate",
                        )
                        if k in c
                    ),
                    "check",
                ),
                "verdict": c.get("verdict"),
                "settled_at": c.get("settled_at"),
                "passed": c["passed"],
            }
            for c in res["checks"]
        ],
    }
    if getattr(args, "json", False):
        print(json.dumps(view, ensure_ascii=False, indent=2))
    else:
        for c in view["checks"]:
            print(f"  {c['verdict']:5} settled@{c['settled_at']}  {c['label']}", file=sys.stderr)
    return _exit(res["ok"], res["reachable"], res.get("complete", True))


def _cmd_verify_chain(args) -> int:
    runtime = _runtime(args)
    records = _load_json_file(args.records)
    key = runtime.environment.get(args.key_env)
    if not key:
        print(
            f"ERROR - signing key env {args.key_env} is unset (secrets are env-only)",
            file=sys.stderr,
        )
        return 2
    if args.single:
        statuses = [signature_status(r, key) for r in records]
        ok = all(s == "valid" for s in statuses)
        _emit({"ok": ok, "statuses": statuses}, args, f"{'OK' if ok else 'TAMPER'} — {statuses}")
        return 0 if ok else 1
    res = verify_chain(records, key, evolve=args.evolve)
    _emit(
        res,
        args,
        f"{'OK' if res['ok'] else 'TAMPER'} — broken_index={res['broken_index']} ({res['reason']})",
    )
    return 0 if res["ok"] else 1


_GATE_SCHEMA = """gate spec (gates/*.yaml) — keys:
  expect:                       # the list of checks
    - {event: NAME, op: ">="|">"|"=="|"!="|"<="|"<"|gte|gt|eq|ne|lte|lt, count|target: N}
    - {event: NAME, where: {field: value, ...}}      # partial-dict field filter
    #   a where value may be an op-dict: {field: {op: gte|gt|eq|ne|lte|lt|contains|
    #   not_contains, value: V}} — missing field never matches (fail-safe)
    - {duration: {event: E, field: elapsed_s, op: lte, target: 1.5, where: {...}}}
    #   UNIVERSAL threshold: every matched event's field must satisfy; 0 matches != pass
    - {present: [{event: A}, {event: B, where: {...}}]}   # subset, any order
    - {absent: {where: {level: ERROR}}}              # forbid wing (a.k.a. forbid:)
    - {must_order: [a, b, c], within_s: S}           # sequencing
    - {heartbeat: NAME, every_s: S, edge_silence: false}  # liveness (inter-beat; opt-in
                                                     #   edge_silence policies start/end silence)
    - {ratioMetric: {good: {...}, total: {...}}, op: gte, target: 0.99}
    - {invariant: {left: {reduce: sum, field: amount, event: A},   # cross-event conservation
                   right: {reduce: count|sum|min|max|last, field: F, event: B},
                   op: "==", tol: 0.01}}
    - {metamorphic: {relation: equal|scaled|subset|monotone|idempotent,  # oracle-FREE relation
                     a: {event: A}, b: {event: B}, reduce: sum, field: F, factor: 2, tol: 0.01}}
    - {external: {kind: db_row, selector: {...}, op: "==", want: 42}}  # INDEPENDENT oracle (not
                                                     #   the trace) — needs evaluate(probe=...)
    - {conforms: EVENTTYPE, closed_world: true}      # ontology conformance
    - {indicatorRef: NAME}  with top-level indicators: {NAME: {event:.., where:..}}
    - {aggregate: {fn: sum|max|min|avg, attr: F, target: N, event: E}}  # rollup budget
  optional: true / pending: true / weight: N    (per-check modifiers)
  cid: ... | cid_env: OOPTDD_CID | timeWindow: 1h | threshold: 0.9
  require_corroboration: true    # single-authority evidence without an external source violates
  require_independent_store: true # a non-independent store requires external corroboration
  pin_service: NAME              # events must carry this service (provenance pin)
  require_signature: true        # events must carry a valid HMAC chain (see verify-chain)
  forbid_errors: true | error_levels: [ERROR, CRITICAL] | allow_errors: [{event: ..}]
"""
_ONTOLOGY_SCHEMA = """ontology file (yaml) — shape:
  closed_world: true|false        # an undeclared in-scope event name is drift
  event_types:
    EVENTNAME:
      required: [attr, ...]        # attrs that must be present
      constraints: {attr: {enum: [...], type: number|int|str|bool, min: N, max: N}}
      additional_properties: false # forbid undeclared payload attrs
"""


def _cmd_schema(args) -> int:
    if args.kind == "envelope":  # the machine-readable wire contract — emit the schema doc itself
        print(json.dumps(ENVELOPE_SCHEMA, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    text = _ONTOLOGY_SCHEMA if args.kind == "ontology" else _GATE_SCHEMA
    if getattr(args, "json", False):
        print(json.dumps({"kind": args.kind, "doc": text}, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


def _add_json(p):
    p.add_argument("--json", action="store_true", help="machine-readable JSON on stdout")


def build_parser() -> argparse.ArgumentParser:
    """Build the generic parser without importing optional adapters or extensions."""

    p = argparse.ArgumentParser(
        prog="ooptdd",
        description="event-contract verification framework",
    )
    p.add_argument("--version", action="version", version=f"ooptdd {__version__}")
    p.add_argument(
        "--extension",
        dest="extensions",
        action="append",
        help="activate a named extension provider (repeatable)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="verify a gate spec for a correlation id")
    v.add_argument("cid")
    v.add_argument("--gate", required=True, help="gate specification to verify")
    v.add_argument("--backend")
    v.add_argument("--retries", type=int, default=None)
    v.add_argument("--delay", type=float, default=None)
    v.add_argument("--backoff", type=float, default=None)
    v.add_argument("--max-delay", dest="max_delay", type=float, default=None)
    v.add_argument("--confirm-rounds", dest="confirm_rounds", type=int, default=None)
    v.add_argument("--confirm-delay", dest="confirm_delay_s", type=float, default=None)
    v.set_defaults(func=_cmd_verify)

    g = sub.add_parser("gate", help="evaluate a YAML gate spec")
    g.add_argument("spec")
    g.add_argument("--backend")
    g.add_argument(
        "--report",
        choices=["md"],
        help="render a markdown report instead of raw JSON",
    )
    g.add_argument("--report-out", help="write the report to this path (default: stdout)")
    g.set_defaults(func=_cmd_gate)

    ln = sub.add_parser("lint", help="static strength audit of a gate spec (catch vacuous gates)")
    ln.add_argument("spec")
    _add_json(ln)
    ln.set_defaults(func=_cmd_lint)

    st = sub.add_parser("strength", help="gate strength fingerprint; --baseline catches weakening")
    st.add_argument("spec")
    st.add_argument("--baseline", help="JSON fingerprint to compare against (exit 1 if weaker)")
    st.add_argument("--write", help="write the fingerprint JSON to this path (a new baseline)")
    _add_json(st)
    st.set_defaults(func=_cmd_strength)

    o = sub.add_parser("ontology", help="event-ontology conformance / compatibility")
    osub = o.add_subparsers(dest="onto_cmd", required=True)
    oc = osub.add_parser("check", help="validate events against an ontology")
    oc.add_argument("ontology")
    oc.add_argument("--events", required=True)
    oc.add_argument("--event-type")
    oc.add_argument("--closed-world", action="store_true")
    _add_json(oc)
    ok = osub.add_parser("compat", help="is old->new a safe ontology evolution?")
    ok.add_argument("old")
    ok.add_argument("new")
    ok.add_argument("--mode", choices=["backward", "forward", "full"], default="backward")
    _add_json(ok)
    o.set_defaults(func=_cmd_ontology)

    b = sub.add_parser("backends", help="list backends / probe the configured store")
    bsub = b.add_subparsers(dest="be_cmd", required=True)
    bl = bsub.add_parser("list", help="list built-in + entry-point backends")
    _add_json(bl)
    bd = bsub.add_parser("doctor", help="construct the backend and probe reachability")
    bd.add_argument("--backend")
    _add_json(bd)
    b.set_defaults(func=_cmd_backends)

    mon = sub.add_parser("monitor", help="show the per-check streaming verdict/settle of a gate")
    mon.add_argument("spec")
    mon.add_argument("--backend")
    _add_json(mon)
    mon.set_defaults(func=_cmd_monitor)

    vc = sub.add_parser("verify-chain", help="audit a tamper-evident receipt chain (HMAC)")
    vc.add_argument("--records", required=True, help="JSON file: the record list")
    vc.add_argument(
        "--key-env", default=DEFAULT_ENV_KEYS.signing_key, help="env var holding the key"
    )
    vc.add_argument("--evolve", action="store_true", help="key-evolving chain (forward secrecy)")
    vc.add_argument("--single", action="store_true", help="per-record signature_status instead")
    _add_json(vc)
    vc.set_defaults(func=_cmd_verify_chain)

    sc = sub.add_parser("schema", help="print the gate/ontology cheat-sheet or the envelope schema")
    sc.add_argument("kind", nargs="?", choices=["gate", "ontology", "envelope"], default="gate")
    _add_json(sc)
    sc.set_defaults(func=_cmd_schema)

    sub.add_parser("version", help="print version").set_defaults(
        func=lambda _a: print(__version__) or 0
    )

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as exc:
        # A user/config error — a spec with no cid (`OOPTDD_CID` unset and no `cid:`), or a
        # missing spec file — is a clean one-line message on the INFRA/usage rung (exit 2), not
        # an uncaught traceback. Conclusive contract verdicts keep their normal exit codes.
        print(f"ERROR - {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
