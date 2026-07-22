"""ooptdd verdicts as platform scores — LangSmith / Langfuse / Phoenix sinks.

The remaining leg of "competitors become display surfaces" (see
``verdict_export``): after a gate runs, push its verdict where the team already
looks — LangSmith feedback, Langfuse scores, Phoenix annotations.

The one rule, everywhere: **the three-valued verdict never collapses.** Each
payload carries the verdict as a CATEGORICAL value (``present`` / ``absent`` /
``inconclusive``); a numeric score rides along only where the platform expects
one, and ``inconclusive`` maps to *no number* (``None`` / omitted) — never 0.0
or 0.5, which a dashboard would read as a graded failure.

Zero hard dependencies: the builders are pure functions over the gate result
(exact upstream shapes pinned in tests), and the posters speak plain HTTP with
an injectable ``opener`` — the same offline-testable pattern as the backends.
LangSmith is SDK-first upstream, so its builder returns kwargs for
``langsmith.Client.create_feedback(**kwargs)`` instead of a poster.
"""
from __future__ import annotations

import base64
import json
import urllib.request

from .verdict_export import _failed_labels, _verdict_word

#: One shared score/feedback/annotation name across all three platforms.
FEEDBACK_KEY = "ooptdd.arrival"

_SCORE = {"present": 1.0, "absent": 0.0, "inconclusive": None}


def _comment(result: dict) -> str:
    failed = _failed_labels(result)
    parts = [f"cid={result.get('cid')}",
             f"backend={result.get('oracle', {}).get('emit_identity', '')}"]
    if failed:
        parts.append("failed=" + ",".join(failed))
    if not result.get("reachable", True):
        parts.append("store unreachable")
    elif not result.get("complete", True):
        parts.append("readback truncated")
    return "; ".join(parts)


# ── LangSmith ──────────────────────────────────────────────────────────────────
def langsmith_feedback_kwargs(result: dict, *, run_id, key: str = FEEDBACK_KEY) -> dict:
    """kwargs for ``langsmith.Client.create_feedback(**kwargs)``.

    Upstream contract (langsmith ``schemas.py``): a categorical ``FeedbackConfig``
    takes ``categories: list[{value: float, label: str}]`` — labels are the LTL3
    words; ``score`` is ``None`` for inconclusive (the config's 0.5 slot is a
    display encoding for the label, not a score we ever send)."""
    verdict = _verdict_word(result)
    return {
        "run_id": run_id,
        "key": key,
        "score": _SCORE[verdict],
        "value": verdict,
        "comment": _comment(result),
        "feedback_config": {
            "type": "categorical",
            "categories": [
                {"value": 1.0, "label": "present"},
                {"value": 0.5, "label": "inconclusive"},
                {"value": 0.0, "label": "absent"},
            ],
        },
    }


# ── Langfuse ───────────────────────────────────────────────────────────────────
def langfuse_score_body(result: dict, *, trace_id: str | None = None,
                        observation_id: str | None = None,
                        name: str = FEEDBACK_KEY) -> dict:
    """Body for ``POST /api/public/scores``.

    Upstream contract (langfuse ``shared.ts`` PostScoresBody): with
    ``dataType: CATEGORICAL`` the ``value`` is a STRING — so the verdict word
    goes through verbatim and no numeric shadow exists to misread."""
    body = {
        "name": name,
        "traceId": trace_id or str(result.get("cid")),
        "dataType": "CATEGORICAL",
        "value": _verdict_word(result),
        "comment": _comment(result),
    }
    if observation_id:
        body["observationId"] = observation_id
    return body


def post_langfuse_score(base_url: str, public_key: str, secret_key: str, body: dict,
                        *, opener=None, timeout: float = 10.0) -> int:
    """POST the score with Langfuse's Basic auth (public:secret). Returns the
    HTTP status; raises on transport errors — the caller decides whether a sink
    failure matters (it never changes the verdict itself)."""
    opener = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/public/scores",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
    )
    with opener(req, timeout) as resp:
        return getattr(resp, "status", 200)


# ── Phoenix ────────────────────────────────────────────────────────────────────
def phoenix_annotation_payload(result: dict, *, trace_id: str | None = None,
                               name: str = FEEDBACK_KEY) -> dict:
    """Payload for ``POST /v1/trace_annotations`` (``{"data": [TraceAnnotationData]}``).

    ``annotator_kind=CODE`` — deterministic, not an LLM/HUMAN judgement.
    Inconclusive carries the label WITHOUT a score key: Phoenix renders the
    label; a numeric would be a fabricated confidence."""
    verdict = _verdict_word(result)
    ann_result: dict = {"label": verdict, "explanation": _comment(result)}
    if _SCORE[verdict] is not None:
        ann_result["score"] = _SCORE[verdict]
    return {"data": [{
        "trace_id": trace_id or str(result.get("cid")),
        "name": name,
        "annotator_kind": "CODE",
        "result": ann_result,
    }]}


def post_phoenix_annotations(base_url: str, payload: dict, *, api_key: str | None = None,
                             opener=None, timeout: float = 10.0) -> int:
    """POST trace annotations to a Phoenix server (``api_key`` header optional —
    self-hosted Phoenix often runs authless)."""
    opener = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api_key"] = api_key
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/trace_annotations",
        data=json.dumps(payload).encode(),
        method="POST",
        headers=headers,
    )
    with opener(req, timeout) as resp:
        return getattr(resp, "status", 200)
