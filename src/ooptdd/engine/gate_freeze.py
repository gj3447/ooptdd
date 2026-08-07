"""Strict recursive capture/thaw operations for functional gate values."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import PurePath
from types import MappingProxyType
from typing import Any
from uuid import UUID

_EXACT_IMMUTABLE_LEAF_TYPES = (
    str,
    bytes,
    bool,
    int,
    float,
    Decimal,
    Fraction,
    UUID,
    type(None),
)


class _FrozenList(tuple[Any, ...]):
    """Tuple storage tagged so thawing restores the caller's list semantics."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _FrozenList):
            return tuple.__eq__(self, other)
        if isinstance(other, list):
            return list(self) == other
        return False

    __hash__ = None  # type: ignore[assignment]


class _FrozenSet(frozenset[Any]):
    """Frozenset storage tagged so thawing restores the caller's set semantics."""


def _is_immutable_leaf(value: object) -> bool:
    return type(value) in _EXACT_IMMUTABLE_LEAF_TYPES or (
        isinstance(value, PurePath) and type(value).__module__ == "pathlib"
    )


def _enter(value: object, active: set[int], label: str) -> int:
    identity = id(value)
    if identity in active:
        raise ValueError(f"cannot capture a cyclic {label}")
    active.add(identity)
    return identity


def _freeze_mapping(value: Mapping[object, Any], active: set[int]) -> Mapping[str, Any]:
    identity = _enter(value, active, "mapping")
    try:
        frozen: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("captured mapping keys must be strings")
            frozen[key] = _freeze_value(child, active)
        return MappingProxyType(frozen)
    finally:
        active.remove(identity)


def _freeze_sequence(value: list[Any] | tuple[Any, ...], active: set[int]) -> tuple[Any, ...]:
    identity = _enter(value, active, "sequence")
    try:
        items = tuple(_freeze_value(child, active) for child in value)
        return _FrozenList(items) if isinstance(value, list) else items
    finally:
        active.remove(identity)


def _freeze_set(value: set[Any] | frozenset[Any], active: set[int]) -> frozenset[Any]:
    identity = _enter(value, active, "set")
    try:
        items = frozenset(_freeze_value(child, active) for child in value)
        return _FrozenSet(items) if isinstance(value, set) else items
    finally:
        active.remove(identity)


def _freeze_value(value: Any, active: set[int]) -> Any:
    if type(value) is date or _is_immutable_leaf(value):
        return value
    if isinstance(value, _FrozenList):
        identity = _enter(value, active, "frozen list")
        try:
            return _FrozenList(_freeze_value(child, active) for child in value)
        finally:
            active.remove(identity)
    if isinstance(value, _FrozenSet):
        identity = _enter(value, active, "frozen set")
        try:
            return _FrozenSet(_freeze_value(child, active) for child in value)
        finally:
            active.remove(identity)
    if isinstance(value, Mapping):
        return _freeze_mapping(value, active)
    if isinstance(value, list | tuple):
        return _freeze_sequence(value, active)
    if isinstance(value, set | frozenset):
        return _freeze_set(value, active)
    raise TypeError(f"unsupported captured value: {type(value).__name__}")


def freeze_value(value: Any) -> Any:
    """Capture supported values without retaining caller-owned mutable aliases."""

    return _freeze_value(value, set())


def thaw_value(value: Any) -> Any:
    """Return a fresh mutable compatibility value from a captured value."""

    if type(value) is date or _is_immutable_leaf(value):
        return value
    if isinstance(value, Mapping):
        return {key: thaw_value(child) for key, child in value.items()}
    if isinstance(value, _FrozenList):
        return [thaw_value(child) for child in value]
    if isinstance(value, tuple):
        return tuple(thaw_value(child) for child in value)
    if isinstance(value, _FrozenSet):
        return {thaw_value(child) for child in value}
    if isinstance(value, frozenset):
        return frozenset(thaw_value(child) for child in value)
    raise TypeError(f"unsupported thawed value: {type(value).__name__}")


__all__ = ("freeze_value", "thaw_value")
