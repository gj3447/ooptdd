"""Domain layer — pure data and the ports the engine depends on.

No I/O, no test-framework dependency, no backend driver, no network. This layer owns:

  - :mod:`ooptdd.domain.ports`     the ``Backend`` port + ``QueryResult`` (DIP boundary)
  - :mod:`ooptdd.domain.model`     generic event envelopes, signing, and chains
  - :mod:`ooptdd.domain.ontology`  EventType vocabulary + conformance + schema compatibility
Domain vocabularies live in opt-in distributions; importing
this package never registers application-specific presets.

Dependency rule: ``domain`` imports only from ``domain`` and the stdlib. The engine and the
adapters depend on it; it depends on neither. The architecture fitness test enforces this.
"""

from .ontology import EventType, Ontology, check_conformance
from .ports import Backend, QueryResult

__all__ = [
    "Backend",
    "QueryResult",
    "EventType",
    "Ontology",
    "check_conformance",
]
