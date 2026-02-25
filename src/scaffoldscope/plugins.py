"""Lazy, deterministic discovery for third-party ScaffoldScope extensions.

Plugins are ordinary Python distributions that publish entry points. Discovery
reads package metadata only; importing plugin code is deferred until an explicit
``load_*`` call.  This keeps commands that do not use plugins predictable and
makes the loaded implementation provenance available to experiment manifests.

Configuration loading uses the registry to validate selected extensions before
planning, then records loaded provenance in experiment identity. See
``docs/extensions.md`` for the full contract.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, cast

from scaffoldscope import __version__
from scaffoldscope.errors import ScaffoldScopeError
from scaffoldscope.redact import redact_text

if TYPE_CHECKING:
    from scaffoldscope.context import ContextPolicy
    from scaffoldscope.models import ChatModel
    from scaffoldscope.schema import ModelConfig, TaskSpec, VariantConfig
    from scaffoldscope.tokenization import Char4TokenCounter


PLUGIN_API_VERSION = 1
CONTEXT_POLICY_ENTRY_POINT = "scaffoldscope.context_policies"
MODEL_PROVIDER_ENTRY_POINT = "scaffoldscope.model_providers"


class PluginKind(str, Enum):
    """Extension points supported by the public plugin API."""

    CONTEXT_POLICY = "context_policy"
    MODEL_PROVIDER = "model_provider"

    @property
    def entry_point_group(self) -> str:
        if self is PluginKind.CONTEXT_POLICY:
            return CONTEXT_POLICY_ENTRY_POINT
        return MODEL_PROVIDER_ENTRY_POINT


BUILTIN_PLUGIN_NAMES: Mapping[PluginKind, frozenset[str]] = {
    PluginKind.CONTEXT_POLICY: frozenset({"none", "reactive", "periodic", "selective"}),
    PluginKind.MODEL_PROVIDER: frozenset({"scripted", "openai_compatible"}),
}


class PluginError(ScaffoldScopeError):
    """Base class for actionable extension failures."""

    code = "plugin_error"

    def __init__(self, message: str, *, hint: str) -> None:
        self.message = message
        self.hint = hint
        super().__init__(f"{message}\nHint: {hint}")


class PluginDiscoveryError(PluginError):
    """Installed entry-point metadata is invalid or ambiguous."""

    code = "plugin_discovery_error"


class PluginCollisionError(PluginDiscoveryError):
    """Two extensions claim the same normalized name."""

    code = "plugin_name_collision"


class PluginLoadError(PluginError):
    """A selected extension could not be imported or has an invalid contract."""

    code = "plugin_load_error"


class PluginCompatibilityError(PluginLoadError):
    """An extension targets an incompatible core or plugin API version."""

    code = "plugin_incompatible"


@dataclass(frozen=True)
class ContextPolicyRequest:
    """Inputs supplied to a context-policy factory.

    ``options`` contains plugin-owned, JSON-compatible configuration. Plugins
    must treat the mapping and nested values as read-only.
    """

    config: VariantConfig
    counter: Char4TokenCounter
    options: Mapping[str, object]


@dataclass(frozen=True)
class ModelProviderRequest:
    """Inputs supplied to a model-provider factory."""

    config: ModelConfig
    task: TaskSpec
    counter: Char4TokenCounter
    event_callback: Callable[[dict[str, Any]], None] | None
    options: Mapping[str, object]


class ContextPolicyFactory(Protocol):
    """Stable callable contract for a context-policy plugin."""

    def __call__(self, request: ContextPolicyRequest) -> ContextPolicy: ...


class ModelProviderFactory(Protocol):
    """Stable callable contract for a model-provider plugin."""

    def __call__(self, request: ModelProviderRequest) -> ChatModel: ...


@dataclass(frozen=True, kw_only=True)
class PluginRegistration:
    """Object exported by a plugin entry point.

    Core bounds use numeric dotted releases (for example ``0.1.0``). The upper
    bound is exclusive. Plugin package version and distribution version are kept
    separately because they are distinct pieces of provenance.
    """

    kind: PluginKind
    factory: object
    plugin_version: str
    description: str
    api_version: int = PLUGIN_API_VERSION
    minimum_core_version: str | None = None
    maximum_core_version_exclusive: str | None = None


def context_policy_plugin(
    factory: ContextPolicyFactory,
    *,
    plugin_version: str,
    description: str,
    api_version: int = PLUGIN_API_VERSION,
    minimum_core_version: str | None = None,
    maximum_core_version_exclusive: str | None = None,
) -> PluginRegistration:
    """Build a validated-on-load context-policy registration object."""

    return PluginRegistration(
        kind=PluginKind.CONTEXT_POLICY,
        factory=factory,
        plugin_version=plugin_version,
        description=description,
        api_version=api_version,
        minimum_core_version=minimum_core_version,
        maximum_core_version_exclusive=maximum_core_version_exclusive,
    )


def model_provider_plugin(
    factory: ModelProviderFactory,
    *,
    plugin_version: str,
    description: str,
    api_version: int = PLUGIN_API_VERSION,
    minimum_core_version: str | None = None,
    maximum_core_version_exclusive: str | None = None,
) -> PluginRegistration:
    """Build a validated-on-load model-provider registration object."""

    return PluginRegistration(
        kind=PluginKind.MODEL_PROVIDER,
        factory=factory,
        plugin_version=plugin_version,
        description=description,
        api_version=api_version,
        minimum_core_version=minimum_core_version,
        maximum_core_version_exclusive=maximum_core_version_exclusive,
    )


_NORMALIZE_SEPARATORS = re.compile(r"[-_.]+")
_ENTRY_POINT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NUMERIC_RELEASE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")
_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
_MAX_IMPLEMENTATION_FILES = 10_000
_MAX_DISTRIBUTION_RECORDS = 100_000
_MAX_IMPLEMENTATION_FILE_BYTES = 16 * 1024 * 1024
_MAX_IMPLEMENTATION_TOTAL_BYTES = 128 * 1024 * 1024


def normalize_plugin_name(name: str) -> str:
    """Return the comparison key used for lookup and collision detection."""

    return _NORMALIZE_SEPARATORS.sub("-", name).lower()


@dataclass(frozen=True)
class PluginInfo:
    """Import-free metadata for one discovered entry point."""

    name: str
    normalized_name: str
    kind: PluginKind
    group: str
    object_reference: str
    distribution: str
    distribution_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "normalized_name": self.normalized_name,
            "kind": self.kind.value,
            "group": self.group,
            "object_reference": self.object_reference,
            "distribution": self.distribution,
            "distribution_version": self.distribution_version,
        }


FactoryT = TypeVar("FactoryT")


@dataclass(frozen=True)
class LoadedPlugin(Generic[FactoryT]):
    """A loaded factory plus the provenance required for result identity."""

    info: PluginInfo
    registration: PluginRegistration
    factory: FactoryT
    implementation_sha256: str
    implementation_hash_source: str

    def provenance(self) -> dict[str, str | int | None]:
        return {
            **self.info.to_dict(),
            "plugin_api_version": self.registration.api_version,
            "plugin_version": self.registration.plugin_version,
            "description": self.registration.description,
            "minimum_core_version": self.registration.minimum_core_version,
            "maximum_core_version_exclusive": (self.registration.maximum_core_version_exclusive),
            "implementation_sha256": self.implementation_sha256,
            "implementation_hash_source": self.implementation_hash_source,
        }


class _EntryPoint(Protocol):
    name: str
    group: str
    value: str

    def load(self) -> object: ...


class _PluginHandle:
    def __init__(self, info: PluginInfo, entry_point: _EntryPoint, core_version: str) -> None:
        self.info = info
        self._entry_point = entry_point
        self._core_version = core_version
        self._loaded: LoadedPlugin[object] | None = None
        self._error: PluginError | None = None
        self._lock = threading.Lock()

    def load(self) -> LoadedPlugin[object]:
        with self._lock:
            if self._loaded is not None:
                return self._loaded
            if self._error is not None:
                raise self._error
            try:
                exported = self._entry_point.load()
            except Exception as exc:
                self._error = PluginLoadError(
                    f"Could not load {self.info.kind.value} plugin {self.info.name!r} from "
                    f"{self.info.distribution} {self.info.distribution_version}: "
                    f"{redact_text(str(exc))}",
                    hint=(
                        f"Check that {self.info.object_reference!r} is importable, then reinstall "
                        f"or upgrade distribution {self.info.distribution!r}."
                    ),
                )
                raise self._error from exc
            try:
                registration = _validate_registration(exported, self.info, self._core_version)
                implementation_sha256, implementation_hash_source = _implementation_hash(
                    self._entry_point,
                    registration.factory,
                    self.info,
                )
            except PluginError as exc:
                self._error = exc
                raise
            self._loaded = LoadedPlugin(
                info=self.info,
                registration=registration,
                factory=registration.factory,
                implementation_sha256=implementation_sha256,
                implementation_hash_source=implementation_hash_source,
            )
            return self._loaded


def _hash_bytes(domain: bytes, records: Sequence[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    digest.update(domain)
    digest.update(b"\0")
    for label, content in records:
        encoded_label = label.encode("utf-8")
        digest.update(len(encoded_label).to_bytes(8, "big"))
        digest.update(encoded_label)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _read_bounded(path: Path, limit: int) -> bytes | None:
    with path.open("rb") as handle:
        content = handle.read(limit + 1)
    return content if len(content) <= limit else None


def _distribution_implementation_hash(entry_point: _EntryPoint) -> str | None:
    """Hash safe installed Python files, or return ``None`` for source fallback.

    Any unsafe, unreadable, or incomplete metadata abandons the distribution-file
    route entirely. A partial set would look authoritative while omitting code.
    """

    distribution = getattr(entry_point, "dist", None)
    if distribution is None:
        return None
    try:
        read_metadata = getattr(distribution, "read_text", None)
        if callable(read_metadata):
            direct_url = read_metadata("direct_url.json")
            if isinstance(direct_url, str) and direct_url:
                if len(direct_url) > 64 * 1024:
                    return None
                direct_url_data = json.loads(direct_url)
                if not isinstance(direct_url_data, dict):
                    return None
                directory_info = direct_url_data.get("dir_info", {})
                if not isinstance(directory_info, dict):
                    return None
                if directory_info.get("editable") is True:
                    # Editable metadata often lists only an import-hook shim, not
                    # the source tree that actually implements the factory.
                    return None
        raw_files = getattr(distribution, "files", None)
        if raw_files is None:
            return None
        python_files: list[object] = []
        for index, item in enumerate(raw_files):
            if index >= _MAX_DISTRIBUTION_RECORDS:
                return None
            if Path(str(item)).suffix.lower() in _PYTHON_SUFFIXES:
                python_files.append(item)
                if len(python_files) > _MAX_IMPLEMENTATION_FILES:
                    return None
        if not python_files:
            return None
        base = Path(distribution.locate_file("")).resolve(strict=True)
        located: dict[str, Path] = {}
        total_size = 0
        for item in python_files:
            relative = Path(str(item))
            if relative.is_absolute() or ".." in relative.parts:
                return None
            label = relative.as_posix()
            resolved = Path(distribution.locate_file(item)).resolve(strict=True)
            resolved.relative_to(base)
            if not resolved.is_file():
                return None
            size = resolved.stat().st_size
            if size > _MAX_IMPLEMENTATION_FILE_BYTES:
                return None
            total_size += size
            if total_size > _MAX_IMPLEMENTATION_TOTAL_BYTES:
                return None
            previous = located.get(label)
            if previous is not None and previous != resolved:
                return None
            located[label] = resolved
        records: list[tuple[str, bytes]] = []
        content_size = 0
        for label, path in sorted(located.items()):
            content = _read_bounded(path, _MAX_IMPLEMENTATION_FILE_BYTES)
            if content is None:
                return None
            content_size += len(content)
            if content_size > _MAX_IMPLEMENTATION_TOTAL_BYTES:
                return None
            records.append((label, content))
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    return _hash_bytes(b"scaffoldscope-plugin-distribution-python-v1", records)


def _factory_source_implementation_hash(factory: object) -> str | None:
    """Hash the bounded source file containing a factory without invoking it."""

    candidates: tuple[Any, ...] = (factory, type(factory))
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            source_name = inspect.getsourcefile(candidate)
            if source_name is None:
                continue
            source_path = Path(source_name)
            if source_path.suffix.lower() not in _PYTHON_SUFFIXES or source_path.is_symlink():
                continue
            source_path = source_path.resolve(strict=True)
            if (
                not source_path.is_file()
                or source_path.stat().st_size > _MAX_IMPLEMENTATION_FILE_BYTES
            ):
                continue
            content = _read_bounded(source_path, _MAX_IMPLEMENTATION_FILE_BYTES)
            if content is None:
                continue
            module = getattr(candidate, "__module__", type(factory).__module__)
            qualname = getattr(candidate, "__qualname__", type(factory).__qualname__)
            label = f"{module}:{qualname}:{source_path.name}"
            return _hash_bytes(
                b"scaffoldscope-plugin-factory-source-v1",
                [(label, content)],
            )
        except (OSError, TypeError, ValueError):
            continue
    return None


def _implementation_hash(
    entry_point: _EntryPoint,
    factory: object,
    info: PluginInfo,
) -> tuple[str, str]:
    distribution_hash = _distribution_implementation_hash(entry_point)
    if distribution_hash is not None:
        return distribution_hash, "distribution_python_files"
    factory_hash = _factory_source_implementation_hash(factory)
    if factory_hash is not None:
        return factory_hash, "factory_source"
    raise PluginLoadError(
        f"Could not establish an implementation SHA-256 for plugin {info.name!r}",
        hint=(
            "Install the plugin from a wheel containing Python sources, or export a factory "
            "whose bounded .py source file is inspectable."
        ),
    )


def _numeric_release(value: str, *, field: str, info: PluginInfo) -> tuple[int, ...]:
    if not _NUMERIC_RELEASE.fullmatch(value):
        raise PluginLoadError(
            f"Plugin {info.name!r} declares invalid {field} {value!r}; expected a numeric dotted "
            "release such as '0.1.0'",
            hint=f"Correct the PluginRegistration exported by {info.object_reference!r}.",
        )
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def _validate_registration(
    exported: object, info: PluginInfo, core_version: str
) -> PluginRegistration:
    if not isinstance(exported, PluginRegistration):
        raise PluginLoadError(
            f"Entry point {info.group}:{info.name} exported {type(exported).__name__}, not "
            "PluginRegistration",
            hint=(
                "Export the object returned by context_policy_plugin(...) or "
                "model_provider_plugin(...), not the factory function directly."
            ),
        )
    registration = exported
    if registration.kind is not info.kind:
        raise PluginLoadError(
            f"Plugin {info.name!r} is registered in {info.group!r} but declares kind "
            f"{registration.kind!r}",
            hint=f"Use the {info.kind.entry_point_group!r} group and matching registration helper.",
        )
    if isinstance(registration.api_version, bool) or not isinstance(registration.api_version, int):
        raise PluginLoadError(
            f"Plugin {info.name!r} has a non-integer plugin API version",
            hint=f"Set api_version=PLUGIN_API_VERSION ({PLUGIN_API_VERSION}).",
        )
    if registration.api_version != PLUGIN_API_VERSION:
        raise PluginCompatibilityError(
            f"Plugin {info.name!r} requires plugin API {registration.api_version}, but "
            f"ScaffoldScope {core_version} provides API {PLUGIN_API_VERSION}",
            hint="Install a compatible plugin release or a compatible ScaffoldScope release.",
        )
    if not isinstance(registration.plugin_version, str) or not registration.plugin_version.strip():
        raise PluginLoadError(
            f"Plugin {info.name!r} does not declare a non-empty plugin_version",
            hint="Publish an immutable plugin version in PluginRegistration.",
        )
    if not isinstance(registration.description, str) or not registration.description.strip():
        raise PluginLoadError(
            f"Plugin {info.name!r} does not declare a non-empty description",
            hint="Add a concise mechanism description to PluginRegistration.",
        )
    if not callable(registration.factory):
        raise PluginLoadError(
            f"Plugin {info.name!r} declares a non-callable factory",
            hint="Pass a callable accepting the appropriate plugin request object.",
        )

    current = _numeric_release(core_version, field="ScaffoldScope version", info=info)
    minimum: tuple[int, ...] | None = None
    maximum: tuple[int, ...] | None = None
    if registration.minimum_core_version is not None:
        minimum = _numeric_release(
            registration.minimum_core_version,
            field="minimum_core_version",
            info=info,
        )
    if registration.maximum_core_version_exclusive is not None:
        maximum = _numeric_release(
            registration.maximum_core_version_exclusive,
            field="maximum_core_version_exclusive",
            info=info,
        )
    if minimum is not None and maximum is not None and minimum >= maximum:
        raise PluginLoadError(
            f"Plugin {info.name!r} has an empty core compatibility interval",
            hint="Set minimum_core_version below maximum_core_version_exclusive.",
        )
    if (minimum is not None and current < minimum) or (maximum is not None and current >= maximum):
        lower = registration.minimum_core_version or "any"
        upper = registration.maximum_core_version_exclusive or "any"
        raise PluginCompatibilityError(
            f"Plugin {info.name!r} supports ScaffoldScope versions in [{lower}, {upper}), "
            f"not installed version {core_version}",
            hint="Install a compatible plugin release or adjust the ScaffoldScope version.",
        )
    return registration


def _distribution_details(entry_point: _EntryPoint) -> tuple[str, str]:
    distribution = getattr(entry_point, "dist", None)
    if distribution is None:
        return "<unknown>", "<unknown>"
    name = "<unknown>"
    try:
        raw_name = distribution.metadata.get("Name")
        if isinstance(raw_name, str) and raw_name:
            name = raw_name
    except (AttributeError, KeyError, TypeError):
        pass
    raw_version = getattr(distribution, "version", None)
    version = raw_version if isinstance(raw_version, str) and raw_version else "<unknown>"
    return name, version


def _installed_entry_points() -> tuple[_EntryPoint, ...]:
    available: Any = metadata.entry_points()
    selected: list[_EntryPoint] = []
    if hasattr(available, "select"):
        for kind in PluginKind:
            selected.extend(
                cast(Iterable[_EntryPoint], available.select(group=kind.entry_point_group))
            )
    else:  # pragma: no cover - compatibility with old importlib.metadata implementations
        for kind in PluginKind:
            selected.extend(cast(Iterable[_EntryPoint], available.get(kind.entry_point_group, ())))
    return tuple(selected)


class PluginRegistry:
    """An immutable index of installed extension entry points.

    Constructing a registry performs no plugin imports. Names use a PEP 503-like
    normalization so ``acme_fast``, ``acme-fast``, and ``acme.fast`` cannot be
    installed as ambiguous alternatives.
    """

    def __init__(
        self,
        handles: Mapping[PluginKind, Mapping[str, _PluginHandle]],
        *,
        core_version: str,
    ) -> None:
        self._handles = {kind: dict(values) for kind, values in handles.items()}
        self.core_version = core_version

    @classmethod
    def discover(
        cls,
        *,
        entry_points: Iterable[_EntryPoint] | None = None,
        reserved_names: Mapping[PluginKind, Iterable[str]] | None = None,
        core_version: str = __version__,
    ) -> PluginRegistry:
        """Discover plugins and reject invalid or colliding names deterministically."""

        points = tuple(_installed_entry_points() if entry_points is None else entry_points)
        reserved = {
            kind: {
                normalize_plugin_name(name)
                for name in (
                    *BUILTIN_PLUGIN_NAMES[kind],
                    *(tuple(reserved_names.get(kind, ())) if reserved_names else ()),
                )
            }
            for kind in PluginKind
        }
        candidates: list[tuple[PluginKind, _EntryPoint, PluginInfo]] = []
        valid_groups = {kind.entry_point_group: kind for kind in PluginKind}
        for point in points:
            kind = valid_groups.get(point.group)
            if kind is None:
                continue
            if not isinstance(point.name, str) or not _ENTRY_POINT_NAME.fullmatch(point.name):
                raise PluginDiscoveryError(
                    f"Invalid plugin entry-point name {point.name!r} in group {point.group!r}",
                    hint="Use 1-128 ASCII letters, digits, dots, underscores, or hyphens.",
                )
            normalized = normalize_plugin_name(point.name)
            distribution, distribution_version = _distribution_details(point)
            info = PluginInfo(
                name=point.name,
                normalized_name=normalized,
                kind=kind,
                group=point.group,
                object_reference=point.value,
                distribution=distribution,
                distribution_version=distribution_version,
            )
            candidates.append((kind, point, info))

        candidates.sort(
            key=lambda item: (
                item[0].value,
                item[2].normalized_name,
                item[2].name,
                item[2].distribution,
                item[2].distribution_version,
                item[2].object_reference,
            )
        )
        handles: dict[PluginKind, dict[str, _PluginHandle]] = {kind: {} for kind in PluginKind}
        for kind, point, info in candidates:
            if info.normalized_name in reserved[kind]:
                raise PluginCollisionError(
                    f"Plugin {info.name!r} from {info.distribution} collides with a built-in "
                    f"{kind.value} name after normalization ({info.normalized_name!r})",
                    hint=f"Rename the entry point in group {kind.entry_point_group!r}.",
                )
            previous = handles[kind].get(info.normalized_name)
            if previous is not None:
                raise PluginCollisionError(
                    f"Plugins {previous.info.name!r} from {previous.info.distribution} and "
                    f"{info.name!r} from {info.distribution} claim the same normalized "
                    f"{kind.value} name {info.normalized_name!r}",
                    hint=(
                        "Uninstall one distribution or ask a publisher to use a unique, "
                        "organization-prefixed entry-point name."
                    ),
                )
            handles[kind][info.normalized_name] = _PluginHandle(info, point, core_version)
        return cls(handles, core_version=core_version)

    def plugins(self, kind: PluginKind | None = None) -> tuple[PluginInfo, ...]:
        """List discovered metadata without importing plugin modules."""

        kinds: Sequence[PluginKind] = tuple(PluginKind) if kind is None else (kind,)
        return tuple(
            handle.info
            for selected_kind in kinds
            for _name, handle in sorted(self._handles[selected_kind].items())
        )

    def has(self, kind: PluginKind, name: str) -> bool:
        """Return whether a third-party extension name was discovered."""

        return normalize_plugin_name(name) in self._handles[kind]

    def _load(self, kind: PluginKind, name: str) -> LoadedPlugin[object]:
        normalized = normalize_plugin_name(name)
        handle = self._handles[kind].get(normalized)
        if handle is None:
            available = ", ".join(info.name for info in self.plugins(kind)) or "none"
            raise PluginLoadError(
                f"No {kind.value} plugin named {name!r} is installed; discovered: {available}",
                hint=f"Install a distribution exposing group {kind.entry_point_group!r}.",
            )
        return handle.load()

    def load_context_policy(self, name: str) -> LoadedPlugin[ContextPolicyFactory]:
        """Import and validate one context-policy registration on first use."""

        return cast(LoadedPlugin[ContextPolicyFactory], self._load(PluginKind.CONTEXT_POLICY, name))

    def load_model_provider(self, name: str) -> LoadedPlugin[ModelProviderFactory]:
        """Import and validate one model-provider registration on first use."""

        return cast(LoadedPlugin[ModelProviderFactory], self._load(PluginKind.MODEL_PROVIDER, name))


__all__ = [
    "BUILTIN_PLUGIN_NAMES",
    "CONTEXT_POLICY_ENTRY_POINT",
    "MODEL_PROVIDER_ENTRY_POINT",
    "PLUGIN_API_VERSION",
    "ContextPolicyFactory",
    "ContextPolicyRequest",
    "LoadedPlugin",
    "ModelProviderFactory",
    "ModelProviderRequest",
    "PluginCollisionError",
    "PluginCompatibilityError",
    "PluginDiscoveryError",
    "PluginError",
    "PluginInfo",
    "PluginKind",
    "PluginLoadError",
    "PluginRegistration",
    "PluginRegistry",
    "context_policy_plugin",
    "model_provider_plugin",
    "normalize_plugin_name",
]
