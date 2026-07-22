"""Export a gate verdict back into the trace world.

The bridge in the Phoenix/LangSmith direction: after a gate runs, ship one
``ooptdd.verdict`` structured event (same cid, so it lands in the same trace
space) and/or stamp the verdict onto the current OTel span as attributes. A
trace UI then shows the arrival verdict inline with the spans it judged —
competitors become display surfaces.
"""
from __future__ import annotations

from ..domain.model import build_event
from ..engine.gate import _label

#: Attribute namespace. ooptdd.* is ours; the values follow the LTL3 vocabulary.
ATTR_PREFIX = "ooptdd."


def _failed_labels(result: dict) -> list[str]:
    return [_label(c) for c in result.get("checks", [])
            if not c.get("passed") and not c.get("optional") and not c.get("pending")]


def _verdict_word(result: dict) -> str:
    if not result.get("reachable", True) or not result.get("complete", True):
        return "inconclusive"
    return "present" if result.get("ok") else "absent"


def verdict_span_attributes(result: dict) -> dict:
    """A gate result as flat OTel span attributes (strings/ints/bools only — no
    otel import needed; hand the dict to ``span.set_attributes(...)``)."""
    failed = _failed_labels(result)
    return {
        ATTR_PREFIX + "verdict": _verdict_word(result),
        ATTR_PREFIX + "ok": bool(result.get("ok")),
        ATTR_PREFIX + "cid": str(result.get("cid")),
        ATTR_PREFIX + "checks.total": len(result.get("checks", [])),
        ATTR_PREFIX + "checks.failed": len(failed),
        ATTR_PREFIX + "checks.failed_labels": ",".join(failed),
        ATTR_PREFIX + "backend": str(result.get("oracle", {}).get("emit_identity", "")),
    }


def emit_verdict_event(backend, result: dict) -> dict:
    """Ship one ``ooptdd.verdict`` event for ``result``'s cid and return it.

    The event goes through ``backend.ship`` like any other — meaning the verdict
    itself becomes arrival-assertable (a gate can `present:` it) and visible to
    anything tailing the stream. Phoenix's annotation vocabulary would call this
    an ``annotator_kind=CODE`` annotation bound to the trace."""
    cid = str(result.get("cid"))
    failed = _failed_labels(result)
    # build_event, not a hand-rolled dict: the verdict lands in the SAME cid it annotates,
    # so it must honor the envelope contract (spec_version/service/level) — a bare dict
    # here poisons pin_service / closed-world `conforms:` gates over that cid (grill
    # finding). level stays INFO even for an `absent` verdict: the exported verdict is
    # information ABOUT a failure, not an error event, and must not trip forbid_errors.
    event = build_event(
        cid, "ooptdd.verdict", service="ooptdd.gate",
        level="INFO",
        verdict=_verdict_word(result),
        ok=bool(result.get("ok")),
        checks_total=len(result.get("checks", [])),
        checks_failed=len(failed),
        failed_labels=failed,
        annotator_kind="CODE",  # Phoenix vocabulary: deterministic, not LLM/HUMAN
    )
    backend.ship([event])
    return event
