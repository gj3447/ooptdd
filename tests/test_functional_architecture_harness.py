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
        "fail_on_unclassified_modules": False,
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
        "configuration_boundary": {
            "environment_key_owner": "p.core",
            "environment_key_patterns": ["^P_[A-Z0-9_]+$"],
            "ambient_environment_modules": ["p.adapters"],
        },
        "edge_vocabulary_boundary": {
            "forbidden_layers": ["api", "core", "engine"],
            "patterns": [
                {"id": "test-framework", "regex": "(?i)\\bpytest\\b"},
                {"id": "project-profile", "regex": "(?i)\\bsymposium\\b"},
            ],
            "scan_comments_and_docstrings": True,
        },
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
        "ooptdd.ouroboros.receipt",
        "ooptdd.ouroboros.reducer",
    }
    assert set(contract["pure_modules"]) == expected_pure
    assert contract["purity_scope"] == "direct-module-syntax-only"
    assert contract["fail_on_unclassified_modules"] is True
    assert contract["configuration_boundary"] == {
        "environment_key_owner": "ooptdd.domain.settings",
        "environment_key_patterns": ["^OOPTDD_[A-Z0-9_]+$", "^OTEL_EXPORTER_[A-Z0-9_]+$"],
        "ambient_environment_modules": ["ooptdd.bootstrap", "ooptdd.config"],
    }
    layers = {layer["name"]: layer for layer in contract["layers"]}
    assert layers["protocol_api"]["exact_modules"] == ["ooptdd.ouroboros"]
    assert {"ooptdd", "ooptdd.identity", "ooptdd.sdk"} <= set(layers["api"]["exact_modules"])
    assert all(not layer["module_prefixes"] for layer in layers.values())
    assert not any("profile" in name for name in layers)
    assert not any("profiles/" in item["path"] for item in contract["responsibility_budgets"])
    assert not any(".profiles." in module for module in contract["pure_modules"])
    assert CHECKER.check_repository(ROOT, contract) == []


def test_fail_closed_contract_rejects_an_unclassified_module(tmp_path):
    contract = _contract()
    contract["fail_on_unclassified_modules"] = True
    violations = _tree(
        tmp_path,
        {
            "src/p/core.py": "VALUE = 1\n",
            "src/p/unreviewed.py": "VALUE = 2\n",
        },
        contract,
    )
    assert "FA000" in _rule_ids(violations)
    finding = next(item for item in violations if item.rule_id == "FA000")
    assert finding.symbol == "p.unreviewed"


def test_fail_closed_contract_rejects_a_new_module_under_a_known_prefix(tmp_path):
    contract = _contract()
    contract["fail_on_unclassified_modules"] = True
    violations = _tree(
        tmp_path,
        {
            "src/p/core.py": "VALUE = 1\n",
            "src/p/engine/leaky.py": "VALUE = 2\n",
        },
        contract,
    )

    finding = next(item for item in violations if item.rule_id == "FA000")
    assert finding.symbol == "p.engine.leaky"


def test_fail_closed_contract_rejects_a_stale_exact_module(tmp_path):
    contract = _contract()
    contract["fail_on_unclassified_modules"] = True
    contract["layers"][0]["exact_modules"] = ["p.core", "p.removed"]
    violations = _tree(
        tmp_path,
        {"src/p/core.py": "VALUE = 1\n"},
        contract,
    )

    finding = next(
        item for item in violations if item.rule_id == "FA000" and item.symbol == "p.removed"
    )
    assert "not shipped" in finding.message


def test_incomplete_contract_fails_closed(tmp_path):
    contract = _contract()
    del contract["pure_boundary"]["require_frozen_dataclasses"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="require_frozen_dataclasses"):
        CHECKER.load_contract(path)


def test_contract_requires_explicit_module_coverage_policy(tmp_path):
    contract = _contract()
    del contract["fail_on_unclassified_modules"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="fail_on_unclassified_modules"):
        CHECKER.load_contract(path)


def test_contract_rejects_unknown_top_level_fields(tmp_path):
    contract = _contract()
    contract["silent_future_option"] = True
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match=r"unknown=.*silent_future_option"):
        CHECKER.load_contract(path)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda contract: contract["layers"][0].__setitem__("typo", True),
            r"layers\[0\].*unknown=.*typo",
        ),
        (
            lambda contract: contract["pure_boundary"].__setitem__("typo", True),
            r"pure_boundary.*unknown=.*typo",
        ),
        (
            lambda contract: contract["configuration_boundary"].__setitem__("typo", True),
            r"configuration_boundary.*unknown=.*typo",
        ),
        (
            lambda contract: contract["responsibility_budgets"][0].__setitem__("typo", True),
            r"responsibility budget.*unknown=.*typo",
        ),
    ],
)
def test_contract_rejects_unknown_nested_fields(tmp_path, mutate, match):
    contract = _contract()
    mutate(contract)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match=match):
        CHECKER.load_contract(path)


def test_contract_rejects_paths_that_escape_the_repository(tmp_path):
    contract = _contract()
    contract["source_root"] = "../outside"
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="repository-relative"):
        CHECKER.load_contract(path)

    contract = _contract()
    contract["responsibility_budgets"][0]["path"] = "../outside.py"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="repository-relative"):
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
    contract["pure_boundary"]["allowed_imports_by_module"] = {"p.core": ["datetime.date"]}

    assert (
        _tree(
            tmp_path,
            {"src/p/core.py": "from datetime import date\nVALUE = date(2026, 8, 7)\n"},
            contract,
        )
        == []
    )
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


def test_fa006_rejects_environment_key_literals_outside_settings_owner(tmp_path):
    violations = _tree(
        tmp_path,
        {
            "src/p/core.py": "SETTING = 'P_PRIMARY_KEY'\n",
            "src/p/feature.py": "DUPLICATE = 'P_PRIMARY_KEY'\n",
        },
    )

    assert "FA006" in _rule_ids(violations)
    finding = next(item for item in violations if item.rule_id == "FA006")
    assert finding.path == "src/p/feature.py"
    assert finding.symbol == "P_PRIMARY_KEY"


def test_fa006_rejects_ambient_environment_reads_outside_composition_shell(tmp_path):
    violations = _tree(
        tmp_path,
        {
            "src/p/core.py": "SETTING = 'P_PRIMARY_KEY'\n",
            "src/p/engine/feature.py": "import os\nVALUE = os.environ.get('ANY')\n",
        },
    )

    assert "FA006" in _rule_ids(violations)
    assert any(item.symbol == "os.environ" for item in violations)


def test_fa006_allows_one_declared_composition_shell_to_capture_environment(tmp_path):
    violations = _tree(
        tmp_path,
        {
            "src/p/core.py": "SETTING = 'P_PRIMARY_KEY'\n",
            "src/p/adapters.py": "import os\nSNAPSHOT = dict(os.environ)\n",
        },
    )

    assert "FA006" not in _rule_ids(violations)


def test_fa007_rejects_edge_vocabulary_in_a_generic_layer(tmp_path):
    violations = _tree(
        tmp_path,
        {"src/p/core.py": 'PROFILE = "pytest"\n'},
    )

    assert "FA007" in _rule_ids(violations)
    finding = next(item for item in violations if item.rule_id == "FA007")
    assert finding.symbol == "test-framework:pytest"


def test_fa007_allows_edge_vocabulary_in_an_adapter_layer(tmp_path):
    assert (
        _tree(
            tmp_path,
            {
                "src/p/core.py": "VALUE = 1\n",
                "src/p/adapters.py": 'PLUGIN = "pytest"\n',
            },
        )
        == []
    )


def test_fa007_pattern_can_forbid_project_vocabulary_in_every_layer(tmp_path):
    contract = _contract()
    contract["edge_vocabulary_boundary"]["patterns"][1]["forbidden_layers"] = [
        "adapters",
        "api",
        "core",
        "engine",
    ]
    violations = _tree(
        tmp_path,
        {
            "src/p/core.py": "VALUE = 1\n",
            "src/p/adapters.py": 'LEGACY = "symposium"\n',
        },
        contract,
    )

    finding = next(item for item in violations if item.rule_id == "FA007")
    assert finding.path == "src/p/adapters.py"
    assert finding.symbol == "project-profile:symposium"


def test_fa007_uses_precise_patterns_and_scans_comments_by_contract(tmp_path):
    near_word = _tree(tmp_path, {"src/p/core.py": "CREDIT = 'greenhouse'\n"})
    assert "FA007" not in _rule_ids(near_word)

    comment = _tree(tmp_path, {"src/p/core.py": "# pytest belongs in an adapter\nVALUE = 1\n"})
    assert "FA007" in _rule_ids(comment)


def test_edge_vocabulary_contract_rejects_unknown_layers_and_invalid_regex(tmp_path):
    contract = _contract()
    contract["edge_vocabulary_boundary"]["forbidden_layers"] = ["missing"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="unknown layers"):
        CHECKER.load_contract(path)

    contract = _contract()
    contract["edge_vocabulary_boundary"]["patterns"][0]["regex"] = "["
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="invalid edge-vocabulary pattern"):
        CHECKER.load_contract(path)

    contract = _contract()
    contract["edge_vocabulary_boundary"]["patterns"][0]["forbidden_layers"] = ["missing"]
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(CHECKER.ContractError, match="names unknown layers"):
        CHECKER.load_contract(path)


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
