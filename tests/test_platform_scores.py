"""Platform score sinks — LangSmith / Langfuse / Phoenix payload builders.

The one rule under test everywhere: **the 3-valued verdict never collapses.**
- LangSmith: categorical feedback; ``score=None`` for inconclusive (never 0 / 0.5).
- Langfuse: ``dataType=CATEGORICAL`` with a STRING value — the verdict word itself.
- Phoenix: CODE annotation; inconclusive carries a label but NO score key.

Builders are pure/offline; the posters use an injectable opener like the backends.
"""
from __future__ import annotations

import base64
import json

from ooptdd.integrations.platform_scores import (
    FEEDBACK_KEY,
    langfuse_score_body,
    langsmith_feedback_kwargs,
    phoenix_annotation_payload,
    post_langfuse_score,
    post_phoenix_annotations,
)


def _res(*, ok=True, reachable=True, complete=True, checks=None):
    return {"cid": "trace-1", "ok": ok, "reachable": reachable, "complete": complete,
            "checks": checks or [{"event": "a", "passed": ok}],
            "oracle": {"emit_identity": "memory:demo"}}


GREEN, RED = _res(ok=True), _res(ok=False)
INCONCLUSIVE = _res(ok=False, reachable=False)


# ── LangSmith ──────────────────────────────────────────────────────────────────
def test_langsmith_kwargs_green_red():
    g = langsmith_feedback_kwargs(GREEN, run_id="r1")
    r = langsmith_feedback_kwargs(RED, run_id="r1")
    assert g["key"] == FEEDBACK_KEY and g["score"] == 1.0 and g["value"] == "present"
    assert r["score"] == 0.0 and r["value"] == "absent"
    # FeedbackConfig categories: list[{value: float, label: str}] (schemas.py contract)
    cats = g["feedback_config"]["categories"]
    assert g["feedback_config"]["type"] == "categorical"
    assert all(isinstance(c["value"], float) and isinstance(c["label"], str) for c in cats)
    assert {c["label"] for c in cats} == {"present", "absent", "inconclusive"}


def test_langsmith_inconclusive_score_is_none_not_a_number():
    i = langsmith_feedback_kwargs(INCONCLUSIVE, run_id="r1")
    assert i["score"] is None and i["value"] == "inconclusive"


# ── Langfuse ───────────────────────────────────────────────────────────────────
def test_langfuse_body_is_categorical_string():
    b = langfuse_score_body(GREEN)
    assert b["dataType"] == "CATEGORICAL" and b["value"] == "present"
    assert b["traceId"] == "trace-1" and b["name"] == FEEDBACK_KEY
    assert isinstance(b["value"], str)  # CATEGORICAL value is z.string() upstream


def test_langfuse_inconclusive_stays_categorical():
    b = langfuse_score_body(INCONCLUSIVE, trace_id="t9", observation_id="o1")
    assert b["value"] == "inconclusive" and b["traceId"] == "t9"
    assert b["observationId"] == "o1"
    assert "score" not in b  # no numeric side-channel that could read as 0


def test_langfuse_poster_hits_scores_endpoint_with_basic_auth():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data)

        class R:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    status = post_langfuse_score("https://cloud.langfuse.com", "pk", "sk",
                                 langfuse_score_body(GREEN), opener=opener)
    assert status == 200
    assert seen["url"].endswith("/api/public/scores")
    assert seen["auth"] == "Basic " + base64.b64encode(b"pk:sk").decode()
    assert seen["body"]["value"] == "present"


# ── Phoenix ────────────────────────────────────────────────────────────────────
def test_phoenix_annotation_shape():
    p = phoenix_annotation_payload(GREEN)
    ann = p["data"][0]
    assert ann["trace_id"] == "trace-1" and ann["annotator_kind"] == "CODE"
    assert ann["name"] == FEEDBACK_KEY
    assert ann["identifier"] == FEEDBACK_KEY  # retry-safe upsert, not duplicate rows
    assert ann["result"]["label"] == "present" and ann["result"]["score"] == 1.0


def test_phoenix_annotation_custom_identifier_and_metadata():
    p = phoenix_annotation_payload(
        GREEN, identifier="gate:deploy-v2", metadata={"spec": "gates/deploy.yaml"})
    ann = p["data"][0]
    assert ann["identifier"] == "gate:deploy-v2"
    assert ann["metadata"] == {"spec": "gates/deploy.yaml"}


def test_phoenix_inconclusive_has_label_but_no_score():
    p = phoenix_annotation_payload(INCONCLUSIVE)
    result = p["data"][0]["result"]
    assert result["label"] == "inconclusive" and "score" not in result


def test_phoenix_poster_hits_trace_annotations():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        seen["api_key"] = req.get_header("Api_key")

        class R:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    status = post_phoenix_annotations("http://phx:6006", phoenix_annotation_payload(RED),
                                      api_key="k1", opener=opener)
    assert status == 200
    assert seen["url"].endswith("/v1/trace_annotations")
    assert seen["api_key"] == "k1"
    assert seen["body"]["data"][0]["result"]["label"] == "absent"


def test_phoenix_poster_sync_mode_is_explicit():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url

        class R:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    status = post_phoenix_annotations(
        "http://phx:6006", phoenix_annotation_payload(GREEN), sync=True, opener=opener)
    assert status == 200 and seen["url"].endswith("/v1/trace_annotations?sync=true")
