"""Engine layer — the evaluation logic, written against domain ports only.

This is the kernel and the verdict machinery:

  - :mod:`ooptdd.engine.monitor`  streaming LTL₃/MTL monitor automata (the kernel)
  - :mod:`ooptdd.engine.gate`     compile a YAML trace spec into monitors and run it
  - :mod:`ooptdd.engine.verify`   poll a backend port, produce the three-valued verdict

Dependency rule: ``engine`` imports only from ``engine``, ``domain`` (the ``Backend`` port,
the event model/ontology), and the stdlib — never from a concrete adapter (a backend
driver, the CLI, or framework plugins). The architecture fitness test enforces this, so the
evaluation logic stays runnable against any store and trivially unit-testable.
"""

from .gate import (
    check,
    checks_from,
    combine_results,
    compose_check_registry,
    evaluate,
    load_gate,
)
from .monitor import Monitor, run_monitor
from .verify import poll_until_present, verify_gate

__all__ = [
    "evaluate",
    "combine_results",
    "check",
    "checks_from",
    "compose_check_registry",
    "load_gate",
    "Monitor",
    "run_monitor",
    "poll_until_present",
    "verify_gate",
]
