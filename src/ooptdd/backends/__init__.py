"""Backend registry — resolve a driver name to a Backend instance.

Resolution order: built-ins, then the ``ooptdd.backends`` entry-point group
(so ``pip install ooptdd-loki`` adds a ``loki`` backend with no core change).
"""
from __future__ import annotations

from .base import Backend, QueryResult
from .memory import MemoryBackend
from .memory import reset as memory_reset

_BUILTINS = {
    "memory": "ooptdd.backends.memory:MemoryBackend",
    "openobserve": "ooptdd.backends.openobserve:OpenObserveBackend",
    "otel": "ooptdd.backends.otel:OtelBackend",
    "clickhouse": "ooptdd.backends.clickhouse:ClickHouseBackend",
    "signoz": "ooptdd.backends.clickhouse:ClickHouseBackend",  # SigNoz = ClickHouse tables
    "victorialogs": "ooptdd.backends.victorialogs:VictoriaLogsBackend",
}


def _load(target: str):
    mod_name, _, attr = target.partition(":")
    import importlib

    return getattr(importlib.import_module(mod_name), attr)


def get_backend(name: str, **options) -> Backend:
    """Return a configured backend instance.

    ``name`` is a built-in ("memory" | "openobserve" | "otel") or an entry point
    registered under ``ooptdd.backends``. ``options`` are passed to the driver.
    """
    if name in _BUILTINS:
        return _load(_BUILTINS[name])(**options)

    from importlib.metadata import entry_points

    for ep in entry_points(group="ooptdd.backends"):
        if ep.name == name:
            return ep.load()(**options)

    raise ValueError(
        f"unknown ooptdd backend {name!r}. built-ins: {sorted(_BUILTINS)}; "
        "or install a driver exposing the 'ooptdd.backends' entry point."
    )


__all__ = ["Backend", "QueryResult", "MemoryBackend", "get_backend", "memory_reset"]
