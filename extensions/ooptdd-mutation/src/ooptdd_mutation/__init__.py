"""Opt-in mutation tooling for the general OOPTDD framework."""

from .analysis import (
    LOCK_SCHEMA,
    derive_mutations,
    mutation_report,
    ndcg,
    ooptdd_checks,
    ranked_kills,
    verify_audit_ranking,
    verify_mutation_lock,
)

__version__ = "0.1.0"

__all__ = (
    "LOCK_SCHEMA",
    "derive_mutations",
    "mutation_report",
    "ndcg",
    "ooptdd_checks",
    "ranked_kills",
    "verify_audit_ranking",
    "verify_mutation_lock",
)
