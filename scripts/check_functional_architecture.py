#!/usr/bin/env python3
"""Deterministic L_IDE fitness gate for OOPTDD's functional/SOLID boundary.

The checker is deliberately stdlib-only. It falsifies violations of declared import direction,
configured direct-effect boundaries, syntactically frozen dataclass bindings, shared mutable
module state, and explicit size ratchets. It does not prove semantic purity or SOLID.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESULT_SCHEMA_VERSION = "ooptdd-functional-solid-result/v1"
MUTATING_METHODS = {
    "__delitem__",
    "__setitem__",
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
}


class ContractError(ValueError):
    """The machine-readable contract is malformed or internally inconsistent."""


@dataclass(frozen=True, order=True)
class Violation:
    rule_id: str
    path: str
    line: int
    symbol: str
    message: str
    remediation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "line": self.line,
            "message": self.message,
            "path": self.path,
            "remediation": self.remediation,
            "rule_id": self.rule_id,
            "symbol": self.symbol,
        }


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    path: Path
    relative_path: str
    tree: ast.Module


def load_contract(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read contract {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractError("contract root must be an object")
    required = {
        "schema_version",
        "harness_tier",
        "source_root",
        "package",
        "claim",
        "purity_scope",
        "layers",
        "pure_modules",
        "pure_boundary",
        "package_root_import_allowlist",
        "responsibility_budgets",
        "require_budget_for_each_pure_module",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ContractError(f"contract missing fields: {missing}")
    if value["schema_version"] != "ooptdd-functional-solid-contract/v1":
        raise ContractError("unsupported contract schema_version")
    if value["harness_tier"] != "L_IDE":
        raise ContractError("functional architecture contract must declare harness_tier L_IDE")
    if not isinstance(value["claim"], str) or not value["claim"].strip():
        raise ContractError("claim must be non-empty text")
    if value["purity_scope"] != "direct-module-syntax-only":
        raise ContractError(
            "purity_scope must be 'direct-module-syntax-only'; transitive purity is not checked"
        )
    for field in ("source_root", "package"):
        if not isinstance(value[field], str) or not value[field]:
            raise ContractError(f"{field} must be a non-empty string")
    if not isinstance(value["layers"], list) or not value["layers"]:
        raise ContractError("layers must be a non-empty array")
    if not all(isinstance(layer, dict) for layer in value["layers"]):
        raise ContractError("every layer must be an object")
    defaults = [layer for layer in value["layers"] if layer.get("default") is True]
    if len(defaults) != 1:
        raise ContractError("exactly one layer must declare default=true")
    names = [layer.get("name") for layer in value["layers"]]
    if any(not isinstance(name, str) or not name for name in names) or len(names) != len(
        set(names)
    ):
        raise ContractError("layer names must be unique non-empty strings")
    known = set(names)
    for layer in value["layers"]:
        for field in ("exact_modules", "module_prefixes", "may_depend_on"):
            if not isinstance(layer.get(field), list) or not all(
                isinstance(item, str) and item for item in layer[field]
            ):
                raise ContractError(f"layer {layer['name']!r} has invalid {field}")
        unknown = set(layer["may_depend_on"]) - known
        if unknown:
            raise ContractError(
                f"layer {layer['name']!r} names unknown dependencies {sorted(unknown)}"
            )
    exact_owners: dict[str, str] = {}
    for layer in value["layers"]:
        for module in layer["exact_modules"]:
            previous = exact_owners.setdefault(module, layer["name"])
            if previous != layer["name"]:
                raise ContractError(
                    f"exact module {module!r} belongs to both {previous!r} and {layer['name']!r}"
                )
    if not isinstance(value["pure_modules"], list) or not all(
        isinstance(item, str) and item for item in value["pure_modules"]
    ):
        raise ContractError("pure_modules must contain non-empty strings")
    if len(value["pure_modules"]) != len(set(value["pure_modules"])):
        raise ContractError("pure_modules must be unique")
    package_prefix = value["package"] + "."
    if any(
        module != value["package"] and not module.startswith(package_prefix)
        for module in value["pure_modules"]
    ):
        raise ContractError("every pure module must belong to the configured package")
    unlayered_pure = set(value["pure_modules"]) - set(exact_owners)
    if unlayered_pure:
        raise ContractError(
            f"pure modules must be assigned by exact_modules: {sorted(unlayered_pure)}"
        )
    if not isinstance(value["package_root_import_allowlist"], list) or not all(
        isinstance(item, str) and item for item in value["package_root_import_allowlist"]
    ):
        raise ContractError("package_root_import_allowlist must contain non-empty strings")
    boundary = value["pure_boundary"]
    if not isinstance(boundary, dict):
        raise ContractError("pure_boundary must be an object")
    for field in ("forbidden_import_roots", "forbidden_calls"):
        if not isinstance(boundary.get(field), list) or not all(
            isinstance(item, str) and item for item in boundary[field]
        ):
            raise ContractError(f"pure_boundary.{field} must contain strings")
    allowed_imports = boundary.get("allowed_imports_by_module")
    if not isinstance(allowed_imports, dict) or not all(
        isinstance(module, str)
        and module in value["pure_modules"]
        and isinstance(imports, list)
        and all(isinstance(item, str) and item for item in imports)
        and len(imports) == len(set(imports))
        for module, imports in allowed_imports.items()
    ):
        raise ContractError(
            "pure_boundary.allowed_imports_by_module must map pure modules to unique imports"
        )
    for field in (
        "require_frozen_dataclasses",
        "forbid_global_nonlocal",
        "forbid_module_global_mutation",
    ):
        if not isinstance(boundary.get(field), bool):
            raise ContractError(f"pure_boundary.{field} must be a boolean")
    budgets = value["responsibility_budgets"]
    if not isinstance(budgets, list):
        raise ContractError("responsibility_budgets must be an array")
    budget_paths: list[str] = []
    for budget in budgets:
        if not isinstance(budget, dict) or not isinstance(budget.get("path"), str):
            raise ContractError("every responsibility budget needs a path")
        if budget.get("classification") not in {"pure_module", "non_pure_debt"}:
            raise ContractError(
                f"budget {budget['path']!r} needs classification pure_module or non_pure_debt"
            )
        budget_paths.append(budget["path"])
        for field in ("max_physical_lines", "max_function_lines", "max_function_parameters"):
            if not isinstance(budget.get(field), int) or budget[field] < 0:
                raise ContractError(f"budget {budget['path']!r} has invalid {field}")
    if len(budget_paths) != len(set(budget_paths)):
        raise ContractError("responsibility budget paths must be unique")
    expected_pure_paths = {
        f"{value['source_root']}/{module.replace('.', '/')}.py" for module in value["pure_modules"]
    }
    labelled_pure_paths = {
        budget["path"] for budget in budgets if budget["classification"] == "pure_module"
    }
    if labelled_pure_paths != expected_pure_paths:
        raise ContractError(
            "pure_module budget classifications must exactly match pure_modules; "
            f"missing={sorted(expected_pure_paths - labelled_pure_paths)}, "
            f"extra={sorted(labelled_pure_paths - expected_pure_paths)}"
        )
    if value["require_budget_for_each_pure_module"] is not True:
        raise ContractError("require_budget_for_each_pure_module must be true")
    return value


def _discover(repo_root: Path, contract: dict[str, Any]) -> dict[str, ModuleInfo]:
    source_root = repo_root / contract["source_root"]
    package = contract["package"]
    package_root = source_root / package.replace(".", "/")
    if not package_root.is_dir():
        raise ContractError(f"package root does not exist: {package_root}")
    modules: dict[str, ModuleInfo] = {}
    for path in sorted(package_root.rglob("*.py")):
        parts = list(path.relative_to(source_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(parts)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as error:
            raise ContractError(f"cannot parse {path}: {error}") from error
        modules[name] = ModuleInfo(name, path, path.relative_to(repo_root).as_posix(), tree)
    return modules


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    anchor = package.split(".") if package else []
    strip = level - 1
    if strip > 0:
        anchor = anchor[: max(0, len(anchor) - strip)]
    base = ".".join(anchor)
    if module:
        return f"{base}.{module}" if base else module
    return base


def _module_imports(info: ModuleInfo, known: set[str]) -> set[str]:
    is_init = info.path.name == "__init__.py"
    package = info.name if is_init else info.name.rsplit(".", 1)[0] if "." in info.name else ""
    targets: set[str] = set()

    def record(name: str) -> None:
        parts = name.split(".")
        for index in range(len(parts), 0, -1):
            candidate = ".".join(parts[:index])
            if candidate in known and candidate != info.name:
                targets.add(candidate)
                return

    for node in ast.walk(info.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(package, node.level, node.module)
                if node.module:
                    record(base)
                for alias in node.names:
                    record(f"{base}.{alias.name}" if base else alias.name)
            elif node.module:
                record(node.module)
                for alias in node.names:
                    record(f"{node.module}.{alias.name}")
    return targets


def _import_cycles(adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Return sorted strongly-connected components of size greater than one."""

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    components: list[list[str]] = []

    def visit(module: str) -> None:
        nonlocal counter
        index[module] = low[module] = counter
        counter += 1
        stack.append(module)
        on_stack.add(module)
        for dependency in sorted(adjacency[module]):
            if dependency not in index:
                visit(dependency)
                low[module] = min(low[module], low[dependency])
            elif dependency in on_stack:
                low[module] = min(low[module], index[dependency])
        if low[module] == index[module]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == module:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    for module in sorted(adjacency):
        if module not in index:
            visit(module)
    return sorted(components)


def _layer_of(module: str, layers: list[dict[str, Any]]) -> str:
    for layer in layers:
        if module in layer["exact_modules"]:
            return layer["name"]
    matches: list[tuple[int, str]] = []
    for layer in layers:
        for prefix in layer["module_prefixes"]:
            if module == prefix or module.startswith(prefix + "."):
                matches.append((len(prefix), layer["name"]))
    if matches:
        return max(matches)[1]
    return next(layer["name"] for layer in layers if layer.get("default") is True)


def _is_pure(module: str, pure_modules: set[str]) -> bool:
    return module in pure_modules


def _dataclass_imports(tree: ast.Module) -> tuple[set[str], set[str]]:
    decorators: set[str] = set()
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "dataclasses":
            decorators.update(
                alias.asname or alias.name for alias in node.names if alias.name == "dataclass"
            )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name for alias in node.names if alias.name == "dataclasses"
            )
    return decorators, modules


def _dataclass_is_frozen(
    decorator: ast.expr, decorator_names: set[str], module_names: set[str]
) -> bool | None:
    if isinstance(decorator, ast.Name) and decorator.id in decorator_names:
        return False
    if (
        isinstance(decorator, ast.Attribute)
        and decorator.attr == "dataclass"
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id in module_names
    ):
        return False
    if not isinstance(decorator, ast.Call):
        return None
    target = decorator.func
    is_dataclass = isinstance(target, ast.Name) and target.id in decorator_names
    is_dataclass = is_dataclass or (
        isinstance(target, ast.Attribute)
        and target.attr == "dataclass"
        and isinstance(target.value, ast.Name)
        and target.value.id in module_names
    )
    if not is_dataclass:
        return None
    frozen = next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "frozen"), None
    )
    return isinstance(frozen, ast.Constant) and frozen.value is True


def _target_base_name(target: ast.expr) -> str | None:
    current = target
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _assigned_names(nodes: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        targets: list[ast.expr] = []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, (ast.Tuple, ast.List)):
                names.update(item.id for item in target.elts if isinstance(item, ast.Name))
    return names


@dataclass(frozen=True)
class ScopeBindings:
    locals: frozenset[str]
    globals: frozenset[str]
    nonlocals: frozenset[str]


class _BindingCollector(ast.NodeVisitor):
    """Collect compile-time bindings for one lexical scope without entering child scopes."""

    def __init__(self) -> None:
        self.locals: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.locals.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.locals.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.locals.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.locals.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.locals.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.locals.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _argument_names(arguments: ast.arguments) -> set[str]:
    names = {
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }
    if arguments.vararg is not None:
        names.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.add(arguments.kwarg.arg)
    return names


def _scope_bindings(
    scope: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef,
    cache: dict[ast.AST, ScopeBindings],
) -> ScopeBindings:
    cached = cache.get(scope)
    if cached is not None:
        return cached
    collector = _BindingCollector()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        collector.locals.update(_argument_names(scope.args))
    body = [scope.body] if isinstance(scope, ast.Lambda) else scope.body
    for node in body:
        collector.visit(node)
    collector.locals.difference_update(collector.globals)
    collector.locals.difference_update(collector.nonlocals)
    result = ScopeBindings(
        frozenset(collector.locals),
        frozenset(collector.globals),
        frozenset(collector.nonlocals),
    )
    cache[scope] = result
    return result


def _name_refers_to_module_global(
    node: ast.AST,
    name: str,
    module_globals: set[str],
    cache: dict[ast.AST, ScopeBindings],
) -> bool:
    if name not in module_globals:
        return False
    current = getattr(node, "parent", None)
    crossed_function = False
    while current is not None and not isinstance(current, ast.Module):
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            crossed_function = True
            bindings = _scope_bindings(current, cache)
            if name in bindings.globals:
                return True
            if name in bindings.locals:
                return False
            if name in bindings.nonlocals:
                current = getattr(current, "parent", None)
                continue
        elif isinstance(current, ast.ClassDef) and not crossed_function:
            bindings = _scope_bindings(current, cache)
            if name in bindings.locals:
                return False
        current = getattr(current, "parent", None)
    return True


def _nearest_function_scope(
    node: ast.AST,
) -> ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | None:
    current = getattr(node, "parent", None)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return current
        current = getattr(current, "parent", None)
    return None


def _scope_aliases_to_globals(
    scope: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    module_globals: set[str],
    binding_cache: dict[ast.AST, ScopeBindings],
    alias_cache: dict[ast.AST, dict[str, str]],
) -> dict[str, str]:
    cached = alias_cache.get(scope)
    if cached is not None:
        return cached
    pairs: list[tuple[str, str, ast.AST]] = []

    class AliasCollector(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            if isinstance(node.value, ast.Name):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        pairs.append((target.id, node.value.id, node))
            self.generic_visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Name):
                pairs.append((node.target.id, node.value.id, node))
            if node.value is not None:
                self.visit(node.value)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    collector = AliasCollector()
    body = [scope.body] if isinstance(scope, ast.Lambda) else scope.body
    for child in body:
        collector.visit(child)
    aliases: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for target, source, assignment in pairs:
            origin = aliases.get(source)
            if origin is None and _name_refers_to_module_global(
                assignment, source, module_globals, binding_cache
            ):
                origin = source
            if origin is not None and aliases.get(target) != origin:
                aliases[target] = origin
                changed = True
    alias_cache[scope] = aliases
    return aliases


def _module_global_origin(
    node: ast.AST,
    name: str | None,
    module_globals: set[str],
    binding_cache: dict[ast.AST, ScopeBindings],
    alias_cache: dict[ast.AST, dict[str, str]],
) -> str | None:
    if name is None:
        return None
    if _name_refers_to_module_global(node, name, module_globals, binding_cache):
        return name
    scope = _nearest_function_scope(node)
    if scope is None:
        return None
    return _scope_aliases_to_globals(scope, module_globals, binding_cache, alias_cache).get(name)


def _contains_mutable_initializer(value: ast.expr) -> bool:
    if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(value, ast.Lambda):
        return False
    if isinstance(value, ast.Call):
        if isinstance(value.func, ast.Name) and value.func.id in {"list", "dict", "set"}:
            return True
        if isinstance(value.func, ast.Name) and value.func.id in {"MappingProxyType", "frozenset"}:
            return False
        return False
    if isinstance(value, ast.Tuple):
        return any(_contains_mutable_initializer(item) for item in value.elts)
    if isinstance(value, ast.BoolOp):
        return any(_contains_mutable_initializer(item) for item in value.values)
    if isinstance(value, ast.BinOp):
        return _contains_mutable_initializer(value.left) or _contains_mutable_initializer(
            value.right
        )
    if isinstance(value, ast.IfExp):
        return _contains_mutable_initializer(value.body) or _contains_mutable_initializer(
            value.orelse
        )
    return False


def _direct_package_root_imports(info: ModuleInfo, package_root: str) -> list[tuple[int, str]]:
    is_init = info.path.name == "__init__.py"
    package = info.name if is_init else info.name.rsplit(".", 1)[0] if "." in info.name else ""
    imports: list[tuple[int, str]] = []
    for node in ast.walk(info.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package_root:
                    imports.append((node.lineno, "*"))
                elif alias.name.startswith(package_root + "."):
                    imports.append((node.lineno, alias.name[len(package_root) + 1 :]))
        elif isinstance(node, ast.ImportFrom):
            resolved = (
                _resolve_relative(package, node.level, node.module)
                if node.level
                else node.module or ""
            )
            if resolved == package_root:
                imports.extend((node.lineno, alias.name) for alias in node.names)
    return imports


def _function_parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = node.args
    return (
        len(args.posonlyargs)
        + len(args.args)
        + len(args.kwonlyargs)
        + (args.vararg is not None)
        + (args.kwarg is not None)
    )


def check_repository(repo_root: Path, contract: dict[str, Any]) -> list[Violation]:
    repo_root = repo_root.resolve()
    modules = _discover(repo_root, contract)
    for info in modules.values():
        _attach_parents(info.tree)
    known = set(modules)
    adjacency = {name: _module_imports(info, known) for name, info in modules.items()}
    violations: list[Violation] = []

    # FA001 — no module import cycles.
    for cycle in _import_cycles(adjacency):
        first = modules[cycle[0]]
        violations.append(
            Violation(
                "FA001",
                first.relative_path,
                1,
                " -> ".join(cycle),
                f"import cycle detected: {' -> '.join(cycle)}",
                "Invert the dependency through a domain-owned port or move shared values inward.",
            )
        )

    # FA002 — declared dependency arrows only.
    layers = contract["layers"]
    package_root = contract["package"]
    root_allowlist = set(contract["package_root_import_allowlist"])
    allowed = {layer["name"]: set(layer["may_depend_on"]) for layer in layers}
    for source, dependencies in adjacency.items():
        source_layer = _layer_of(source, layers)
        for dependency in sorted(dependencies):
            if dependency == package_root:
                info = modules[source]
                for line, imported_name in _direct_package_root_imports(info, package_root):
                    if imported_name not in root_allowlist:
                        violations.append(
                            Violation(
                                "FA002",
                                info.relative_path,
                                line,
                                source,
                                f"package-root import {imported_name!r} is not allowlisted",
                                "Import the inward module directly or add a narrowly reviewed "
                                "metadata symbol to package_root_import_allowlist.",
                            )
                        )
                continue
            dependency_layer = _layer_of(dependency, layers)
            if dependency_layer not in allowed[source_layer]:
                info = modules[source]
                violations.append(
                    Violation(
                        "FA002",
                        info.relative_path,
                        1,
                        source,
                        f"layer {source_layer!r} imports forbidden layer "
                        f"{dependency_layer!r} via {dependency}",
                        "Depend on an inward port/value or move wiring to the "
                        "API/composition shell.",
                    )
                )

    boundary = contract["pure_boundary"]
    forbidden_imports = set(boundary["forbidden_import_roots"])
    forbidden_calls = set(boundary["forbidden_calls"])
    declared_pure_modules = set(contract["pure_modules"])
    pure_modules = {name for name in modules if _is_pure(name, declared_pure_modules)}
    for name in sorted(pure_modules):
        info = modules[name]
        tree = info.tree

        # FA003 — pure modules may not acquire ambient effects.
        allowed_module_imports = set(
            boundary["allowed_imports_by_module"].get(name, [])
        )
        for node in ast.walk(tree):
            imported_names: list[tuple[str, str]] = []
            if isinstance(node, ast.Import):
                imported_names = [
                    (alias.name.split(".", 1)[0], alias.name) for alias in node.names
                ]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_names = [
                    (
                        node.module.split(".", 1)[0],
                        f"{node.module}.{alias.name}",
                    )
                    for alias in node.names
                ]
            for imported_root, imported_name in imported_names:
                if (
                    imported_root in forbidden_imports
                    and imported_name not in allowed_module_imports
                ):
                    violations.append(
                        Violation(
                            "FA003",
                            info.relative_path,
                            node.lineno,
                            imported_name,
                            f"pure module imports ambient-effect capability {imported_name!r}",
                            "Pass the capability through a caller-owned port and keep its "
                            "adapter in the shell.",
                        )
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in forbidden_calls:
                    violations.append(
                        Violation(
                            "FA003",
                            info.relative_path,
                            node.lineno,
                            node.func.id,
                            f"pure module calls forbidden effect primitive {node.func.id!r}",
                            "Return data/effect intent; execute the effect in an "
                            "imperative adapter.",
                        )
                    )

        # FA004 — immutable protocol values and no shared-state mutation after import.
        module_globals = _assigned_names(tree.body)
        dataclass_names, dataclass_modules = _dataclass_imports(tree)
        binding_cache: dict[ast.AST, ScopeBindings] = {}
        alias_cache: dict[ast.AST, dict[str, str]] = {}
        for node in ast.walk(tree):
            if boundary.get("forbid_global_nonlocal") and isinstance(
                node, (ast.Global, ast.Nonlocal)
            ):
                violations.append(
                    Violation(
                        "FA004",
                        info.relative_path,
                        node.lineno,
                        ",".join(node.names),
                        "pure module declares global/nonlocal mutation",
                        "Thread state through immutable input/output values instead.",
                    )
                )
            if boundary.get("require_frozen_dataclasses") and isinstance(node, ast.ClassDef):
                states = [
                    _dataclass_is_frozen(decorator, dataclass_names, dataclass_modules)
                    for decorator in node.decorator_list
                ]
                states = [state for state in states if state is not None]
                if states and not all(states):
                    violations.append(
                        Violation(
                            "FA004",
                            info.relative_path,
                            node.lineno,
                            node.name,
                            "dataclass in pure module is not frozen",
                            "Use @dataclass(frozen=True) and immutable collection fields.",
                        )
                    )
            if not boundary.get("forbid_module_global_mutation"):
                continue
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            at_module_scope = isinstance(getattr(node, "parent", None), ast.Module)
            if at_module_scope and isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if value is not None and _contains_mutable_initializer(value):
                    declared = sorted(
                        name
                        for target in targets
                        for name in [_target_base_name(target)]
                        if name is not None
                    )
                    violations.append(
                        Violation(
                            "FA004",
                            info.relative_path,
                            node.lineno,
                            ",".join(declared),
                            "pure module declares mutable module-global state",
                            "Use an immutable tuple/frozenset/frozen value or construct a "
                            "fresh value inside a function.",
                        )
                    )
            for target in targets:
                base = _target_base_name(target)
                origin = _module_global_origin(
                    node, base, module_globals, binding_cache, alias_cache
                )
                is_item_or_attribute = isinstance(target, (ast.Attribute, ast.Subscript))
                is_module_augmented = at_module_scope and isinstance(node, ast.AugAssign)
                if origin is not None and (is_item_or_attribute or is_module_augmented):
                    violations.append(
                        Violation(
                            "FA004",
                            info.relative_path,
                            node.lineno,
                            origin,
                            f"pure module mutates module global {origin!r}",
                            "Build and return a new value rather than mutating shared state.",
                        )
                    )
            if isinstance(node, ast.Delete):
                for target in node.targets:
                    base = _target_base_name(target)
                    origin = _module_global_origin(
                        node, base, module_globals, binding_cache, alias_cache
                    )
                    if origin is not None and isinstance(target, (ast.Attribute, ast.Subscript)):
                        violations.append(
                            Violation(
                                "FA004",
                                info.relative_path,
                                node.lineno,
                                origin,
                                f"pure module deletes content from module global {origin!r}",
                                "Build and return a new value rather than mutating shared state.",
                            )
                        )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = _target_base_name(node.func.value)
                origin = _module_global_origin(
                    node, base, module_globals, binding_cache, alias_cache
                )
                if origin is not None and node.func.attr in MUTATING_METHODS:
                    violations.append(
                        Violation(
                            "FA004",
                            info.relative_path,
                            node.lineno,
                            origin,
                            f"pure module calls mutator {node.func.attr!r} on module "
                            f"global {origin!r}",
                            "Use an immutable constant or return an updated value to the caller.",
                        )
                    )

    # FA005 — exact per-path responsibility ratchets; unbudgeted new pure files fail closed.
    budgets = {item["path"]: item for item in contract["responsibility_budgets"]}
    if contract.get("require_budget_for_each_pure_module", False):
        for name in sorted(declared_pure_modules):
            if name not in modules:
                relative_path = f"{contract['source_root']}/{name.replace('.', '/')}.py"
                violations.append(
                    Violation(
                        "FA005",
                        relative_path,
                        1,
                        name,
                        "declared pure module does not exist",
                        "Add the reviewed module or remove the stale pure-module declaration.",
                    )
                )
                continue
            info = modules[name]
            if info.relative_path not in budgets:
                violations.append(
                    Violation(
                        "FA005",
                        info.relative_path,
                        1,
                        name,
                        "pure module has no explicit responsibility budget",
                        "Declare a reviewed per-path budget in the architecture contract.",
                    )
                )
            elif budgets[info.relative_path]["classification"] != "pure_module":
                violations.append(
                    Violation(
                        "FA005",
                        info.relative_path,
                        1,
                        name,
                        "pure module budget is not classified pure_module",
                        "Mark the exact path budget as pure_module.",
                    )
                )
    for relative_path, budget in sorted(budgets.items()):
        path = repo_root / relative_path
        if not path.is_file():
            violations.append(
                Violation(
                    "FA005",
                    relative_path,
                    1,
                    relative_path,
                    "budgeted path does not exist",
                    "Remove the stale budget or point it at the owned replacement module.",
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        physical_lines = len(text.splitlines())
        if physical_lines > budget["max_physical_lines"]:
            violations.append(
                Violation(
                    "FA005",
                    relative_path,
                    1,
                    relative_path,
                    f"physical lines {physical_lines} exceed budget {budget['max_physical_lines']}",
                    "Split the responsibility or deliberately review and update this "
                    "exact path budget.",
                )
            )
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            function_lines = (node.end_lineno or node.lineno) - node.lineno + 1
            if function_lines > budget["max_function_lines"]:
                violations.append(
                    Violation(
                        "FA005",
                        relative_path,
                        node.lineno,
                        node.name,
                        f"function lines {function_lines} exceed budget "
                        f"{budget['max_function_lines']}",
                        "Extract named pure transformations with explicit input/output contracts.",
                    )
                )
            parameters = _function_parameter_count(node)
            if parameters > budget["max_function_parameters"]:
                violations.append(
                    Violation(
                        "FA005",
                        relative_path,
                        node.lineno,
                        node.name,
                        f"function parameters {parameters} exceed budget "
                        f"{budget['max_function_parameters']}",
                        "Introduce a frozen request/context value or split the responsibility.",
                    )
                )
    return sorted(set(violations))


def _attach_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]


def format_result(violations: list[Violation], output_format: str) -> str:
    ordered = sorted(set(violations))
    if output_format == "json":
        payload = {
            "ok": not ordered,
            "schema_version": RESULT_SCHEMA_VERSION,
            "violations": [violation.to_dict() for violation in ordered],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if not ordered:
        return "functional-solid architecture: PASS"
    lines = [f"functional-solid architecture: FAIL ({len(ordered)} violation(s))"]
    lines.extend(
        f"{item.rule_id} {item.path}:{item.line} {item.symbol}: {item.message} "
        f"[fix: {item.remediation}]"
        for item in ordered
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("docs/architecture/functional-solid-contract.json"),
    )
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        contract = load_contract(args.contract)
        violations = check_repository(args.root, contract)
    except ContractError as error:
        print(f"functional-solid contract error: {error}", file=sys.stderr)
        return 2
    print(format_result(violations, args.format))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
