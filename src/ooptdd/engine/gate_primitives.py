"""Small immutable primitives shared by the gate kernel and monitor adapters."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

_OP_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "gte": ">=",
        "ge": ">=",
        "gt": ">",
        "eq": "==",
        "ne": "!=",
        "lte": "<=",
        "le": "<=",
        "lt": "<",
    }
)


def _norm_op(op: object) -> str:
    """Map an OpenSLO word operator to its symbolic form; pass symbols through."""

    value = str(op)
    return _OP_ALIASES.get(value, value)


def stream_key(event: Mapping[str, Any]) -> tuple[bool, Any, Any]:
    """Return the stable historical ordering key for one already captured event."""

    timestamp = event.get("_timestamp")
    sequence = event.get("_seq")
    return (
        timestamp is None,
        timestamp if timestamp is not None else 0,
        sequence if sequence is not None else 0,
    )


__all__ = ("_OP_ALIASES", "_norm_op", "stream_key")
