"""Shared composition boundary for application adapters.

The application adapters capture ambient inputs once, then pass an immutable
runtime snapshot inward. The engine receives resolved values without depending on
their configuration source.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from .backends import BackendRegistry, get_backend
from .config import load_pyproject, resolve_settings, resolve_signing_settings
from .domain.settings import DEFAULT_ENV_KEYS, EnvironmentKeys, Settings, SigningSettings
from .engine.gate_values import CheckFn


class ExtensionProvider(Protocol):
    """Pure provider for one explicitly named gate-vocabulary extension."""

    def __call__(self) -> Mapping[str, CheckFn]: ...


DEFAULT_EXTENSION_PROVIDERS: Mapping[str, ExtensionProvider] = MappingProxyType({})


def _freeze_registry(registry: Mapping[str, CheckFn]) -> Mapping[str, CheckFn]:
    return MappingProxyType(dict(registry))


def _extension_checks(extension_name: str, provider: ExtensionProvider) -> Mapping[str, CheckFn]:
    if not callable(provider):
        raise TypeError(f"extension provider {extension_name!r} must be callable")
    supplied = provider()
    if not isinstance(supplied, Mapping):
        raise TypeError(f"extension provider {extension_name!r} must return a mapping")
    checked: dict[str, CheckFn] = {}
    for key, handler in supplied.items():
        if not isinstance(key, str) or not key:
            raise TypeError(f"extension {extension_name!r} supplied an invalid predicate key")
        if not callable(handler):
            raise TypeError(f"extension {extension_name!r} predicate {key!r} must be callable")
        if key in checked:
            raise ValueError(f"extension {extension_name!r} duplicated predicate {key!r}")
        checked[key] = handler
    return MappingProxyType(checked)


@dataclass(frozen=True)
class Runtime:
    """Fully resolved adapter dependencies for one process invocation."""

    settings: Settings
    signing: SigningSettings
    environment: Mapping[str, str] = field(repr=False)
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS
    check_registry: Mapping[str, CheckFn] = field(default_factory=dict, repr=False)
    _base_check_registry: Mapping[str, CheckFn] = field(default_factory=dict, repr=False)
    extension_providers: Mapping[str, ExtensionProvider] = field(default_factory=dict, repr=False)
    backend_registry: BackendRegistry = field(default_factory=BackendRegistry, repr=False)

    def activate_extensions(self) -> Runtime:
        """Return a new runtime with configured predicate providers composed.

        Providers are pure ``ooptdd_checks()`` functions.  Composition always starts
        from this runtime's captured base registry, so imports, module caching, and
        unrelated changes to the compatibility registry cannot change the result.
        """

        combined = dict(self._base_check_registry)
        owners = {key: "ooptdd core" for key in combined}
        for extension_name in self.settings.extensions:
            if extension_name not in self.extension_providers:
                raise ValueError(
                    f"unknown extension {extension_name!r}; allowed names: "
                    f"{sorted(self.extension_providers)}"
                )
            provider = self.extension_providers[extension_name]
            for key, handler in _extension_checks(extension_name, provider).items():
                if key in combined:
                    raise ValueError(
                        f"duplicate check predicate {key!r} from {extension_name!r}; "
                        f"already supplied by {owners[key]}"
                    )
                combined[key] = handler
                owners[key] = repr(extension_name)
        return Runtime(
            settings=self.settings,
            signing=self.signing,
            environment=self.environment,
            env_keys=self.env_keys,
            check_registry=_freeze_registry(combined),
            _base_check_registry=self._base_check_registry,
            extension_providers=self.extension_providers,
            backend_registry=self.backend_registry,
        )

    def evaluate(self, backend: Any, spec: dict[str, Any], **kwargs: Any) -> dict:
        """Evaluate through this runtime's immutable predicate registry."""

        from .engine.gate import evaluate

        return evaluate(backend, spec, registry=self.check_registry, **kwargs)

    def verify(self, backend: Any, cid: str, spec: dict[str, Any], **kwargs: Any) -> dict:
        """Poll through this runtime's immutable predicate registry."""

        from .engine.verify import verify_gate

        return verify_gate(
            backend,
            cid,
            spec,
            registry=self.check_registry,
            **kwargs,
        )

    def lint_spec(self, spec: dict[str, Any]) -> list[dict]:
        """Audit a spec using this runtime's exact predicate vocabulary."""

        from .engine.gate import lint_spec

        return lint_spec(spec, registry=self.check_registry)

    def strength_fingerprint(self, spec: dict[str, Any]) -> dict:
        """Fingerprint a spec using this runtime's exact predicate vocabulary."""

        from .engine.gate import strength_fingerprint

        return strength_fingerprint(spec, registry=self.check_registry)

    @property
    def cid(self) -> str | None:
        return self.environment.get(self.env_keys.cid) or None

    def backend(self):
        """Build the selected backend from the same captured environment snapshot."""

        return get_backend(
            self.settings.backend,
            registry=self.backend_registry,
            service=self.settings.service,
            environment=self.environment,
            env_keys=self.env_keys,
            **self.settings.backend_options,
        )


def compose_runtime(
    *,
    project_path: str | Path = "pyproject.toml",
    project: Mapping[str, object] | None = None,
    environment: Mapping[str, str] | None = None,
    overrides: Mapping[str, object] | None = None,
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
    extension_providers: Mapping[str, ExtensionProvider] = DEFAULT_EXTENSION_PROVIDERS,
    backend_registry: BackendRegistry | None = None,
) -> Runtime:
    """Resolve the one precedence law and return an immutable runtime snapshot.

    ``project`` and ``environment`` are injectable for callers and embedding. If
    omitted, each ambient source is captured exactly once at this boundary.
    """

    project_values = load_pyproject(str(project_path)) if project is None else project
    captured_environment = MappingProxyType(
        dict(os.environ) if environment is None else dict(environment)
    )
    settings = resolve_settings(
        project_values,
        captured_environment,
        overrides,
        env_keys=env_keys,
    )
    signing = resolve_signing_settings(captured_environment, env_keys=env_keys)
    from .engine.gate import core_check_registry

    base_registry = _freeze_registry(core_check_registry())
    captured_providers = MappingProxyType(dict(extension_providers))
    return Runtime(
        settings=settings,
        signing=signing,
        environment=captured_environment,
        env_keys=env_keys,
        check_registry=base_registry,
        _base_check_registry=base_registry,
        extension_providers=captured_providers,
        backend_registry=BackendRegistry() if backend_registry is None else backend_registry,
    )


__all__ = [
    "DEFAULT_EXTENSION_PROVIDERS",
    "ExtensionProvider",
    "Runtime",
    "compose_runtime",
]
