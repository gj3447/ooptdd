"""Canaries for the L_IDE functional-core/imperative-shell architecture gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_functional_architecture.py"
CONTRACT = ROOT / "docs" / "architecture" / "functional-solid-contract.json"


def _load_checker():
    spec = importlib.util.spec_from_file_location("ooptdd_functional_architecture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


def _contract(*, budget_lines: int = 40, function_lines: int = 20) -> dict:
    return {
        "schema_version": "ooptdd-functional-solid-contract/v1",
        "harness_tier": "L_IDE",
        "source_root": "src",
        "package": "p",
        "claim": "test fixture",
        "purity_scope": "direct-module-syntax-only",
        "layers": [
            {
                "name": "core",
                "exact_modules": ["p.core"],
                "module_prefixes": [],
                "may_depend_on": ["core"],
            },
            {
                "name": "engine",
                "exact_modules": [],
                "module_prefixes": ["p.engine"],
                "may_depend_on": ["core", "engine"],
            },
            {
                "name": "adapters",
                "exact_modules": [],
                "module_prefixes": [],
                "may_depend_on": ["adapters", "core", "engine"],
                "default": True,
            },
            {
                "name": "api",
                "exact_modules": ["p"],
                "module_prefixes": [],
                "may_depend_on": ["adapters", "api", "core", "engine"],
            },
        ],
        "pure_modules": ["p.core"],
        "package_root_import_allowlist": ["__version__"],
        "pure_boundary": {
            "forbidden_import_roots": ["os", "random", "time"],
            "forbidden_calls": ["open", "print"],
            "allowed_imports_by_module": {},
            "require_frozen_dataclasses": True,
            "forbid_global_nonlocal": True,
            "forbid_module_global_mutation": True,
        },
        "responsibility_budgets": [
            {
                "path": "src/p/core.py",
                "classification": "pure_module",
                "max_physical_lines": budget_lines,
                "max_function_lines": function_lines,
                "max_function_parameters": 3,
            }
        ],
        "require_budget_for_each_pure_module": True,
    }


def _tree(tmp_path: Path, files: dict[str, str], contract: dict | None = None):
    for relative, text in {"src/p/__init__.py": "", **files}.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    value = contract or _contract()
    return CHECKER.check_repository(tmp_path, value)


def _rule_ids(violations) -> set[str]:
    return {violation.rule_id for violation in violations}


def test_repository_satisfies_declared_functional_solid_contract():
    contract = CHECKER.load_contract(CONTRACT)
    expected_pure = {
        "ooptdd.engine.gate_freeze",
        "ooptdd.engine.gate_kernel",
        "ooptdd.engine.gate_ontology",
        "ooptdd.engine.gate_primitives",
        "ooptdd.engine.gate_rules",
        "ooptdd.engine.gate_values",
        "ooptdd.ouroboros.identity",
        "ooptdd.ouroboros.model",
        "ooptdd.ouroboros.ports",
        "ooptdd.ouroboros.reducer",
    }
    assert set(contract["pure_modules"]) == expected_pure
    assert contract["purity_scope"] == "direct-module-syntax-only"
    layers = {layer["name"]: layer for layer in contract["layers"]}
    assert set(layers["functional_domain"]["exact_modules"]) == {
        "ooptdd.ouroboros.gate_adapter",
        "ooptdd.ouroboros.schema",
    }
    assert layers["application_core"]["exact_modules"] == ["ooptdd.ouroboros.completion"]
    assert "ooptdd.ouroboros.completion" not in expected_pure
    assert "ooptdd.ouroboros.completion_io" not in expected_pure
    budgets = {item["path"]: item for item in contract["responsibility_budgets"]}
    assert budgets["src/ooptdd/ouroboros/completion.py"]["classification"] == "non_pure_debt"
    assert CHECKER.check_repository(ROOT, contract) == []


def test_incomplete_contract_fails_closed(tmp_path):
    contract = _contract()
    del contract["pure_boundary"]["require_frozen_dataclasses"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="require_frozen_dataclasses"):
        CHECKER.load_contract(path)


def test_contract_cannot_overclaim_transitive_purity(tmp_path):
    contract = _contract()
    contract["purity_scope"] = "transitive-semantic-proof"
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="direct-module-syntax-only"):
        CHECKER.load_contract(path)


def test_fa001_cycle_canary_is_detected(tmp_path):
    contract = _contract()
    contract["responsibility_budgets"].extend(
        [
            {
                "path": "src/p/core_a.py",
                "classification": "pure_module",
                "max_physical_lines": 10,
                "max_function_lines": 5,
                "max_function_parameters": 1,
            },
            {
                "path": "src/p/core_b.py",
                "classification": "pure_module",
                "max_physical_lines": 10,
                "max_function_lines": 5,
                "max_function_parameters": 1,
            },
        ]
    )
    contract["responsibility_budgets"] = contract["responsibility_budgets"][1:]
    contract["pure_modules"] = ["p.core_a", "p.core_b"]
    contract["layers"][0]["exact_modules"] = ["p.core_a", "p.core_b"]
    violations = _tree(
        tmp_path,
        {
            "src/p/core_a.py": "from .core_b import b\na = 1\n",
            "src/p/core_b.py": "from .core_a import a\nb = 2\n",
        },
        contract,
    )
    assert "FA001" in _rule_ids(violations)


def test_fa002_outward_dependency_canary_is_detected(tmp_path):
    violations = _tree(
        tmp_path,
        {
            "src/p/core.py": "VALUE = 1\n",
            "src/p/engine.py": "from .adapters import Adapter\n",
            "src/p/adapters.py": "class Adapter:\n    pass\n",
        },
    )
    assert "FA002" in _rule_ids(violations)


def test_fa003_ambient_effect_canary_is_detected(tmp_path):
    violations = _tree(tmp_path, {"src/p/core.py": "import time\nNOW = time.time()\n"})
    assert "FA003" in _rule_ids(violations)


def test_fa003_allows_only_an_exact_module_scoped_safe_import(tmp_path):
    contract = _contract()
    contract["pure_boundary"]["forbidden_import_roots"].append("datetime")
    contract["pure_boundary"]["allowed_imports_by_module"] = {
        "p.core": ["datetime.date"]
    }

    assert _tree(
        tmp_path,
        {"src/p/core.py": "from datetime import date\nVALUE = date(2026, 8, 7)\n"},
        contract,
    ) == []
    violations = _tree(
        tmp_path,
        {"src/p/core.py": "from datetime import datetime\nVALUE = datetime.now()\n"},
        contract,
    )
    assert "FA003" in _rule_ids(violations)


def test_fa004_mutability_canaries_are_detected(tmp_path):
    source = """\
from dataclasses import dataclass

REGISTRY = {}

@dataclass
class Value:
    item: int

def mutate():
    REGISTRY.update({"x": 1})
"""
    violations = _tree(tmp_path, {"src/p/core.py": source})
    assert "FA004" in _rule_ids(violations)
    messages = "\n".join(item.message for item in violations if item.rule_id == "FA004")
    assert "not frozen" in messages
    assert "module global" in messages


def test_fa004_dataclass_alias_without_frozen_is_detected(tmp_path):
    source = """\
from dataclasses import dataclass as immutable_record

@immutable_record
class Value:
    item: int
"""
    violations = _tree(tmp_path, {"src/p/core.py": source})
    assert any(item.rule_id == "FA004" and "not frozen" in item.message for item in violations)


def test_fa004_mutable_declaration_alone_is_detected(tmp_path):
    violations = _tree(tmp_path, {"src/p/core.py": "REGISTRY = {}\n"})
    assert "FA004" in _rule_ids(violations)
    assert any("declares mutable" in item.message for item in violations)


def test_fa004_module_item_and_augmented_mutation_are_detected(tmp_path):
    source = "REGISTRY = tuple()\nREGISTRY[0] = 1\nCOUNT = 0\nCOUNT += 1\n"
    violations = _tree(tmp_path, {"src/p/core.py": source})
    messages = "\n".join(item.message for item in violations if item.rule_id == "FA004")
    assert "REGISTRY" in messages
    assert "COUNT" in messages


def test_fa004_alias_of_module_global_is_detected(tmp_path):
    source = """\
class Registry:
    pass

REGISTRY = Registry()

def mutate():
    alias = REGISTRY
    alias.update({"x": 1})
"""
    violations = _tree(tmp_path, {"src/p/core.py": source})
    assert any(item.rule_id == "FA004" and item.symbol == "REGISTRY" for item in violations)


def test_fa004_benign_local_shadow_does_not_false_positive(tmp_path):
    source = """\
REGISTRY = frozenset()

def local_work():
    REGISTRY = set()
    REGISTRY.add("local")
    return REGISTRY
"""
    assert _tree(tmp_path, {"src/p/core.py": source}) == []


def test_fa002_non_metadata_package_root_import_is_detected(tmp_path):
    violations = _tree(
        tmp_path,
        {
            "src/p/core.py": "VALUE = 1\n",
            "src/p/engine.py": "from p import concrete_backend\n",
        },
    )
    assert any(item.rule_id == "FA002" and "not allowlisted" in item.message for item in violations)


def test_fa002_allowlisted_package_metadata_import_is_accepted(tmp_path):
    assert (
        _tree(
            tmp_path,
            {
                "src/p/core.py": "VALUE = 1\n",
                "src/p/engine.py": "from p import __version__\n",
            },
        )
        == []
    )


def test_fa005_per_path_budget_canary_is_detected(tmp_path):
    source = "def too_large():\n" + "    x = 1\n" * 5 + "    return x\n"
    violations = _tree(
        tmp_path,
        {"src/p/core.py": source},
        _contract(budget_lines=6, function_lines=4),
    )
    assert "FA005" in _rule_ids(violations)


def test_benign_comment_does_not_trip_the_gate(tmp_path):
    source = """\
from dataclasses import dataclass

@dataclass(frozen=True)
class Value:
    item: int

def identity(value):
    # benign comment: no behavior or boundary changed
    return value
"""
    assert _tree(tmp_path, {"src/p/core.py": source}) == []


def test_json_result_is_stable_and_machine_readable(tmp_path):
    violations = _tree(tmp_path, {"src/p/core.py": "import os\n"})
    first = CHECKER.format_result(violations, "json")
    second = CHECKER.format_result(list(reversed(violations)), "json")
    assert first == second
    payload = json.loads(first)
    assert payload["schema_version"] == "ooptdd-functional-solid-result/v1"
    assert payload["ok"] is False
    assert set(payload["violations"][0]) == {
        "line",
        "message",
        "path",
        "remediation",
        "rule_id",
        "symbol",
    }
