"""Adversarial checks for immutable public port values and adapter bounds."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from ooptdd.backends.clickhouse import ClickHouseBackend
from ooptdd.backends.jsonl import JsonlBackend
from ooptdd.backends.memory import MemoryBackend
from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.backends.otel import OtelBackend
from ooptdd.backends.settings import BackendSettings
from ooptdd.backends.victorialogs import VictoriaLogsBackend
from ooptdd.domain.ports import ProbeResult, QueryResult, QuerySpec, TimeWindow


class _Response:
    status = 200

    def __init__(self, body: bytes):
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_query_result_deeply_snapshots_caller_owned_events():
    source = [{"event": "alpha", "nested": {"labels": ["one"]}}]
    result = QueryResult(reachable=True, events=source)

    source[0]["event"] = "changed"
    source[0]["nested"]["labels"].append("two")
    source.append({"event": "later"})

    assert isinstance(result.events, tuple)
    assert result.events[0]["event"] == "alpha"
    nested = result.events[0]["nested"]
    assert isinstance(nested, Mapping)
    assert nested["labels"] == ("one",)
    with pytest.raises(TypeError):
        result.events[0]["event"] = "mutated"


def test_query_spec_and_probe_result_snapshot_nested_values():
    where = {"meta": {"tags": ["a"]}}
    probe_value = {"rows": [{"id": 1}]}
    spec = QuerySpec("cid", TimeWindow(1, 2), where=where)
    result = ProbeResult(reachable=True, value=probe_value)

    where["meta"]["tags"].append("b")
    probe_value["rows"][0]["id"] = 2

    assert spec.where["meta"]["tags"] == ("a",)
    assert result.value["rows"][0]["id"] == 1
    with pytest.raises(TypeError):
        spec.where["new"] = True


def test_public_values_reject_cycles_and_invalid_bounds():
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="cyclic"):
        QueryResult(reachable=True, events=[cycle])
    with pytest.raises(ValueError, match="since_us"):
        TimeWindow(2, 1)
    with pytest.raises(ValueError, match="positive"):
        QuerySpec("cid", TimeWindow(1, 2), limit=0)


def test_backends_accept_one_immutable_operational_settings_value(tmp_path):
    settings = BackendSettings(
        lookback_s=12,
        future_buffer_s=3,
        timeout_s=4.5,
        page_size=7,
        max_rows=21,
    )
    environment = {
        "OOPTDD_CH_URL": "http://clickhouse",
        "OOPTDD_OO_URL": "http://openobserve",
        "OOPTDD_OO_PASSWORD": "secret",
        "OOPTDD_VL_URL": "http://victorialogs",
    }
    adapters = (
        ClickHouseBackend(settings=settings, environment=environment),
        OpenObserveBackend(settings=settings, environment=environment),
        VictoriaLogsBackend(settings=settings, environment=environment),
        OtelBackend(settings=settings, environment=environment),
        MemoryBackend(settings=settings),
        JsonlBackend(path=str(tmp_path / "events.jsonl"), settings=settings),
    )

    assert all(adapter.default_lookback_s == 12 for adapter in adapters)
    assert all(adapter.default_future_buffer_s == 3 for adapter in adapters)
    assert adapters[0].timeout == adapters[1].timeout == adapters[2].timeout == 4.5
    assert adapters[0].max_rows == adapters[1].max_rows == adapters[2].max_rows == 21
    assert adapters[1].page_size == 7


def test_network_backend_overrides_are_validated_by_shared_settings():
    with pytest.raises(ValueError, match="finite and positive"):
        ClickHouseBackend(base_url="http://clickhouse", timeout=float("nan"))
    with pytest.raises(ValueError, match="positive integer"):
        VictoriaLogsBackend(base_url="http://victorialogs", max_rows=True)
    with pytest.raises(ValueError, match="must not exceed"):
        OpenObserveBackend(
            base_url="http://openobserve",
            environment={"OOPTDD_OO_PASSWORD": "secret"},
            page_size=11,
            max_rows=10,
        )


@pytest.mark.parametrize(
    "backend, body",
    [
        (
            OpenObserveBackend(
                base_url="http://openobserve",
                environment={"OOPTDD_OO_PASSWORD": "secret"},
                opener=lambda request, timeout: _Response(json.dumps({"hits": {}}).encode()),
            ),
            None,
        ),
        (
            ClickHouseBackend(
                base_url="http://clickhouse",
                opener=lambda request, timeout: _Response(json.dumps({"data": [1]}).encode()),
            ),
            None,
        ),
        (
            VictoriaLogsBackend(
                base_url="http://victorialogs",
                opener=lambda request, timeout: _Response(b"not-json\n"),
            ),
            None,
        ),
    ],
)
def test_malformed_backend_rows_are_never_complete_success(backend, body):
    del body
    result = backend.query("cid", since_us=0, until_us=1)
    assert result.complete is False or result.reachable is False
    assert result.error is not None
