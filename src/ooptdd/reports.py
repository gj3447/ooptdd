"""Gate-result report renderers — CI-native artifacts from an evaluate() result.

JUnit XML is the lingua franca of CI test summaries (GitHub/GitLab/Jenkins all
render it natively), so `ooptdd gate --report junit` makes a gate's verdict a
first-class CI citizen with zero integration code. Markdown is the human/PR form.

Honesty rules carried into the formats:

- INFRA (unreachable / truncated read) renders as **skipped**, never failure —
  the LTL3 ``inconclusive`` must not be demoted to "falsified" by a CI badge.
- Every report names the cid and the backend identity, and the markdown form
  includes the re-verify command — so a reviewer can independently re-check
  (generator ≠ verifier extends to the human reading the report).
"""
from __future__ import annotations

import json
from xml.sax.saxutils import escape, quoteattr

from .engine.gate import _label


def _infra(result: dict) -> str | None:
    if not result.get("reachable", True):
        return "store unreachable"
    if not result.get("complete", True):
        return "readback truncated (incomplete evidence)"
    return None


def _check_rows(result: dict):
    for chk in result.get("checks", []):
        label = _label(chk)
        detail = {k: v for k, v in chk.items()
                  if k != "passed" and (not isinstance(v, (dict, list))
                                        or k in ("missing", "offenders", "reasons"))}
        yield label, chk, detail


#: Suite-level reasons `ok` can be false with every per-check row green — each must
#: surface as a synthetic failure, or the exported artifact is a fake green (the exact
#: sin this library exists against).
_SUITE_RED_FLAGS = ("vacuous", "uncorroborated", "unauthenticated", "dependent_store")


def _suite_level_red(result: dict) -> str | None:
    if result.get("ok") or _infra(result) is not None:
        return None
    for flag in _SUITE_RED_FLAGS:
        if result.get(flag):
            return flag
    if not any(not c.get("passed") and not c.get("optional") and not c.get("pending")
               for c in result.get("checks", [])):
        return "gate red with no failing gating check (empty or threshold-mode miss)"
    return None


def to_junit_xml(result: dict, *, suite: str = "ooptdd.gate") -> str:
    """One <testcase> per check; gating failures are <failure>, INFRA is <skipped>,
    optional AND pending misses are <skipped> (surfaced, never red — pending checks
    are designed never to gate, so they must not fail the build via the report).
    A suite-level RED (vacuous/uncorroborated/…) gets a synthetic failing testcase
    so the artifact can never read green while the verdict was red."""
    infra = _infra(result)
    cases, failures, skipped = [], 0, 0
    for label, chk, detail in _check_rows(result):
        name = quoteattr(label)
        body = ""
        if infra is not None:
            skipped += 1
            body = f"<skipped message={quoteattr('INCONCLUSIVE: ' + infra)}/>"
        elif not chk.get("passed"):
            payload = escape(json.dumps(detail, ensure_ascii=False, default=str))
            if chk.get("optional") or chk.get("pending"):
                kind = "optional" if chk.get("optional") else "pending"
                skipped += 1
                body = (f"<skipped message={quoteattr(kind + ' check missed (non-gating)')}>"
                        f"{payload}</skipped>")
            elif result.get("ok"):
                # threshold/quorum mode: the gate as a whole is GREEN, this miss was
                # absorbed by the weighted score — a <failure> here would flip CI red
                # on a green verdict. Surface, don't gate.
                skipped += 1
                msg = quoteattr("miss absorbed by threshold mode (gate GREEN)")
                body = f"<skipped message={msg}>{payload}</skipped>"
            else:
                failures += 1
                body = f"<failure message={quoteattr('gate check failed')}>{payload}</failure>"
        cases.append(f"  <testcase classname={quoteattr(str(result.get('cid')))} "
                     f"name={name}>{body}</testcase>")
    suite_red = _suite_level_red(result)
    if suite_red is not None:
        failures += 1
        cases.append(f"  <testcase classname={quoteattr(str(result.get('cid')))} name=\"(gate)\">"
                     f"<failure message={quoteattr('gate RED: ' + suite_red)}/></testcase>")
    props = (f'  <properties>\n'
             f'    <property name="cid" value={quoteattr(str(result.get("cid")))}/>\n'
             f'    <property name="backend" '
             f'value={quoteattr(str(result.get("oracle", {}).get("emit_identity", "")))}/>\n'
             f'  </properties>')
    return ("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            f"<testsuite name={quoteattr(suite)} tests=\"{len(cases)}\" "
            f"failures=\"{failures}\" errors=\"0\" skipped=\"{skipped}\">\n"
            + props + "\n" + "\n".join(cases) + "\n</testsuite>\n")


def to_markdown(result: dict) -> str:
    infra = _infra(result)
    verdict = ("🟡 INCONCLUSIVE" if infra
               else ("🟢 GREEN" if result.get("ok") else "🔴 RED"))
    cid = result.get("cid")
    lines = [
        f"## ooptdd gate — {verdict}",
        "",
        f"- **cid**: `{cid}`",
        f"- **backend**: `{result.get('oracle', {}).get('emit_identity', '?')}`",
    ]
    if infra:
        lines.append(f"- **why inconclusive**: {infra}")
    lines += ["", "| check | result | detail |", "|---|---|---|"]
    suite_red = _suite_level_red(result)
    if suite_red is not None:
        lines.insert(4, f"- **why red**: {suite_red}")
    for label, chk, detail in _check_rows(result):
        if infra is not None:
            state = "⏭ inconclusive"
        elif chk.get("passed"):
            state = "✅ pass"
        elif chk.get("optional") or chk.get("pending"):
            state = "⏭ optional-miss" if chk.get("optional") else "⏭ pending-miss"
        elif result.get("ok"):
            state = "⏭ absorbed (threshold)"
        else:
            state = "❌ fail"
        brief = {k: v for k, v in detail.items()
                 if k in ("got", "want", "score", "target", "missing", "offenders",
                          "value", "violations", "reason", "verdict")}
        cell = escape(json.dumps(brief, ensure_ascii=False, default=str)) if brief else ""
        lines.append(f"| `{label}` | {state} | {cell} |")
    lines += ["",
              f"Re-verify independently: `ooptdd verify {cid} --backend <your-backend>`",
              ""]
    return "\n".join(lines)


RENDERERS = {"junit": to_junit_xml, "md": to_markdown}
