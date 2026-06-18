"""Backend abstraction — the portability seam (adapter side).

The driver *contract* — the :class:`Backend` Protocol and :class:`QueryResult` — is a
domain **port**, defined in :mod:`ooptdd.domain.ports` so the engine can depend on it
without depending on any concrete adapter. This module re-exports it for the adapter
layer: concrete drivers (memory, openobserve, otel, …) import ``Backend``/``QueryResult``
from here, and third-party drivers register under the ``ooptdd.backends`` entry-point group.

Drivers are discovered three ways, in order:
  1. built-ins (memory, openobserve, otel)
  2. the ``ooptdd.backends`` entry-point group (``pip install`` a 3rd-party driver)
  3. an explicit instance passed in code
"""
from __future__ import annotations

from ..domain.ports import Backend, QueryResult

__all__ = ["Backend", "QueryResult"]
