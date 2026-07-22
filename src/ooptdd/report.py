"""Report writers — pure projections of the canonical verdict document.

Adapter layer (like the CLI): writers never re-judge and never reach into the
engine. They serialize the same canonical result dict the CLI already prints to
stdout into CI-portable artifacts (JUnit XML, markdown, JSON). One dispatcher
(:func:`write_report`) is the single seam — CLI commands route every report
through it, so a new format is one :func:`register_writer` call away and can
never fork the verdict logic.

The invariant every writer must uphold: **the three-valued verdict survives the
projection**. ``inconclusive`` (unreachable store, truncated read, unreachable
probe, unsettled ``pend`` check) is never silently green:

- JUnit: ``<error type="ooptdd.inconclusive">`` by default (a fail-closed
  artifact — most CI parsers treat errors as red). ``<skipped>`` only via the
  explicit ``inconclusive: skipped`` option (``--junit-inconclusive=skipped``).
- markdown: renders as ``INCONCLUSIVE``.
- JSON: carries the verdict verbatim.

Registry discipline mirrors ``@check`` / ontology presets: duplicate
registration and unknown formats raise loudly.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable

from . import __version__

# writer signature: (doc, path, options) -> None
Writer = Callable[[dict, str, dict], None]

WRITERS: dict[str, Writer] = {}


def register_writer(fmt: str, writer: Writer) -> None:
    """Register a report writer. Duplicate keys raise — silent override is how
    two plugins end up fighting over one format."""
    if fmt in WRITERS:
        raise ValueError(f"report writer already registered: {fmt!r}")
    WRITERS[fmt] = writer


def write_report(fmt: str, path: str, doc: dict, *, options: dict | None = None) -> None:
    writer = WRITERS.get(fmt)
    if writer is None:
        raise ValueError(
            f"unknown report format {fmt!r}; known: {', '.join(sorted(WRITERS))}")
    writer(doc, path, options or {})


# ── classification: one shared LTL3 projection ─────────────────────────────────
def classify(result: dict) -> str:
    """Collapse a canonical result dict onto the exit ladder: ``green`` / ``red`` /
    ``inconclusive``. Mirrors ``cli._exit`` — a not-clean read is never green and
    never red."""
    verdict = result.get("verdict")
    if verdict in ("present", "absent", "inconclusive"):
        return {"present": "green", "absent": "red", "inconclusive": "inconclusive"}[verdict]
    if result.get("ok"):
        return "green"
    if (not result.get("reachable", True) or not result.get("complete", True)
            or not result.get("probe_reachable", True)):
        return "inconclusive"
    return "red"


def _check_status(check: dict, overall: str) -> str:
    """Per-check rung. An unsettled ``pend`` check did not fail — it never settled;
    and under an inconclusive read no check individually failed either."""
    if check.get("passed"):
        return "green"
    if overall == "inconclusive" or check.get("verdict") == "pend":
        return "inconclusive"
    return "red"


def _check_label(check: dict) -> str:
    if check.get("label"):
        return str(check["label"])
    if check.get("event"):
        return str(check["event"])
    for key in ("present", "absent", "forbid", "must_order", "trajectory", "conforms",
                "heartbeat", "ratioMetric", "ratio", "invariant", "metamorphic",
                "external", "indicatorRef"):
        if key in check:
            return key
    return "check"


def report_document(command: str, result: dict, *, meta: dict | None = None) -> dict:
    """The canonical, self-describing report document — writers consume ONLY this."""
    doc = {
        "tool": "ooptdd",
        "version": __version__,
        "command": command,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "verdict": classify(result),
        "results": [result],
    }
    if meta:
        doc["meta"] = meta
    return doc


# ── JUnit XML ──────────────────────────────────────────────────────────────────
def _xml_safe(text: str) -> str:
    """Replace characters invalid in XML 1.0 with ``#xNN`` escapes (the pytest
    ``bin_xml_escape`` discipline) so any reason string stays parseable."""
    out = []
    for ch in str(text):
        code = ord(ch)
        if code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD:
            out.append(ch)
        else:
            out.append(f"#x{code:02X}")
    return "".join(out)


def _write_junit(doc: dict, path: str, options: dict) -> None:
    inconclusive_mode = options.get("inconclusive", "error")
    if inconclusive_mode not in ("error", "skipped"):
        raise ValueError(f"junit inconclusive policy must be error|skipped, "
                         f"got {inconclusive_mode!r}")
    root = ET.Element("testsuites", name="ooptdd")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for result in doc["results"]:
        overall = classify(result)
        suite = ET.SubElement(root, "testsuite",
                              name=f"ooptdd.{doc['command']}", hostname="",
                              timestamp=doc["generated_at"], time="0")
        props = ET.SubElement(suite, "properties")
        prop_pairs = {
            "ooptdd.version": doc["version"],
            "ooptdd.cid": result.get("cid", ""),
            "ooptdd.verdict": overall,
        }
        for extra in ("service", "backend"):
            if result.get(extra):
                prop_pairs[f"ooptdd.{extra}"] = result[extra]
        for name, value in prop_pairs.items():
            ET.SubElement(props, "property", name=name, value=_xml_safe(str(value)))
        checks = result.get("checks") or [{"event": doc["command"],
                                           "passed": overall == "green",
                                           "verdict": None, "settled_at": None}]
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        reasons = "; ".join(str(r) for r in result.get("reasons", [])) or None
        for check in checks:
            counts["tests"] += 1
            case = ET.SubElement(suite, "testcase", classname="ooptdd.gates",
                                 name=_xml_safe(_check_label(check)), time="0")
            status = _check_status(check, overall)
            message = _xml_safe(reasons or f"check verdict={check.get('verdict')}")
            if status == "green":
                continue
            if status == "red":
                counts["failures"] += 1
                ET.SubElement(case, "failure", type="ooptdd.absent", message=message)
            elif inconclusive_mode == "skipped":
                counts["skipped"] += 1
                ET.SubElement(case, "skipped", message=f"inconclusive: {message}")
            else:
                counts["errors"] += 1
                ET.SubElement(case, "error", type="ooptdd.inconclusive", message=message)
        for key, value in counts.items():
            suite.set(key, str(value))
            totals[key] += value
    for key, value in totals.items():
        root.set(key, str(value))
    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(path, encoding="utf-8", xml_declaration=True)


# ── markdown ───────────────────────────────────────────────────────────────────
_MD_WORD = {"green": "GREEN ✅", "red": "RED ❌", "inconclusive": "INCONCLUSIVE ⚠️"}


def _write_markdown(doc: dict, path: str, options: dict) -> None:
    lines = []
    for result in doc["results"]:
        overall = classify(result)
        lines.append(f"## ooptdd {doc['command']} — {_MD_WORD[overall]}")
        lines.append("")
        lines.append(f"- cid: `{result.get('cid', '')}`  ·  ooptdd {doc['version']}  ·  "
                     f"{doc['generated_at']}")
        if result.get("reasons"):
            lines.append(f"- reasons: {'; '.join(str(r) for r in result['reasons'])}")
        lines.append("")
        checks = result.get("checks") or []
        if checks:
            lines.append("| check | verdict | settled_at |")
            lines.append("|---|---|---|")
            for check in checks:
                status = _check_status(check, overall)
                word = _MD_WORD[status].split()[0]
                cell = str(_check_label(check)).replace("|", "\\|")
                lines.append(f"| {cell} | {word} | {check.get('settled_at')} |")
            lines.append("")
        if overall == "inconclusive":
            lines.append("> ⚠️ INCONCLUSIVE is not a pass: the store was unreachable or the "
                         "read was incomplete. Nothing was proven either way.")
            lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ── JSON ───────────────────────────────────────────────────────────────────────
def _write_json(doc: dict, path: str, options: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


register_writer("junit", _write_junit)
register_writer("markdown", _write_markdown)
register_writer("json", _write_json)


# ── CLI-facing dispatch helper ─────────────────────────────────────────────────
def parse_report_specs(raw: list[str] | None) -> list[tuple[str, str]]:
    """Parse repeated ``--report FMT=PATH`` values. Malformed input raises
    ``ValueError`` (the CLI's clean usage-error rung, exit 2)."""
    specs: list[tuple[str, str]] = []
    for item in raw or []:
        fmt, sep, path = item.partition("=")
        if not sep or not fmt or not path:
            raise ValueError(f"--report expects FMT=PATH, got {item!r}")
        if fmt not in WRITERS:
            raise ValueError(
                f"unknown report format {fmt!r}; known: {', '.join(sorted(WRITERS))}")
        specs.append((fmt, path))
    return specs


def dispatch_reports(args: Any, command: str, result: dict) -> None:
    """The single seam CLI commands call after computing a result. Reads
    ``args.report`` / ``args.junit_inconclusive``; absent attrs mean no-op, so
    commands without the flags are unaffected ("off is truly off")."""
    specs = parse_report_specs(getattr(args, "report", None))
    if not specs:
        return
    doc = report_document(command, result)
    options = {"inconclusive": getattr(args, "junit_inconclusive", None) or "error"}
    for fmt, path in specs:
        write_report(fmt, path, doc, options=options)
