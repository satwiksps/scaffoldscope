from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from scaffoldscope.plugins import (
    CONTEXT_POLICY_ENTRY_POINT,
    MODEL_PROVIDER_ENTRY_POINT,
    PLUGIN_API_VERSION,
    ContextPolicyRequest,
    ModelProviderRequest,
    PluginCollisionError,
    PluginCompatibilityError,
    PluginKind,
    PluginLoadError,
    PluginRegistry,
    context_policy_plugin,
    model_provider_plugin,
)


@dataclass
class _Distribution:
    name: str
    version: str
    root: Path | None = None
    files: tuple[str, ...] | None = None
    editable: bool = False

    @property
    def metadata(self) -> dict[str, str]:
        return {"Name": self.name}

    def locate_file(self, item: object) -> Path:
        if self.root is None:
            raise FileNotFoundError("test distribution has no installed-file root")
        return self.root / str(item)

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json" and self.editable:
            return '{"dir_info":{"editable":true}}'
        return None


class _EntryPoint:
    def __init__(
        self,
        *,
        name: str,
        group: str,
        exported: object,
        distribution: str = "acme-scaffoldscope",
        version: str = "2.3.4",
        value: str = "acme_plugin:registration",
        root: Path | None = None,
        files: tuple[str, ...] | None = None,
        editable: bool = False,
    ) -> None:
        self.name = name
        self.group = group
        self.value = value
        self.dist = _Distribution(distribution, version, root, files, editable)
        self.exported = exported
        self.load_count = 0

    def load(self) -> object:
        self.load_count += 1
        if isinstance(self.exported, BaseException):
            raise self.exported
        return self.exported


def _context_factory(request: ContextPolicyRequest) -> Any:
    return request


def _model_factory(request: ModelProviderRequest) -> Any:
    return request


def _context_registration(**overrides: Any) -> object:
    values: dict[str, Any] = {
        "factory": _context_factory,
        "plugin_version": "2.3.4",
        "description": "A deterministic test policy.",
        "minimum_core_version": "0.1.0",
        "maximum_core_version_exclusive": "1.0.0",
    }
    values.update(overrides)
    return context_policy_plugin(**values)


class PluginRegistryTests(unittest.TestCase):
    def test_installed_discovery_selects_both_public_entry_point_groups(self) -> None:
        policy = _EntryPoint(
            name="acme.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(),
        )
        provider = _EntryPoint(
            name="acme.provider",
            group=MODEL_PROVIDER_ENTRY_POINT,
            exported=model_provider_plugin(
                _model_factory,
                plugin_version="1.0.0",
                description="A provider.",
            ),
        )

        class _Selectable:
            def __init__(self) -> None:
                self.selected_groups: list[str] = []

            def select(self, *, group: str) -> list[_EntryPoint]:
                self.selected_groups.append(group)
                return [point for point in (policy, provider) if point.group == group]

        available = _Selectable()
        with patch("scaffoldscope.plugins.metadata.entry_points", return_value=available):
            registry = PluginRegistry.discover()

        self.assertEqual(
            available.selected_groups,
            [CONTEXT_POLICY_ENTRY_POINT, MODEL_PROVIDER_ENTRY_POINT],
        )
        self.assertEqual(len(registry.plugins()), 2)
        self.assertEqual(policy.load_count, 0)
        self.assertEqual(provider.load_count, 0)

    def test_discovery_is_sorted_and_does_not_import_plugins(self) -> None:
        second = _EntryPoint(
            name="zeta.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(),
            distribution="zeta-package",
        )
        first = _EntryPoint(
            name="Acme_Policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(),
            distribution="acme-package",
        )
        ignored = _EntryPoint(
            name="ignored",
            group="unrelated.plugins",
            exported=RuntimeError("must not load"),
        )

        registry = PluginRegistry.discover(entry_points=[second, ignored, first])

        self.assertEqual([item.name for item in registry.plugins()], ["Acme_Policy", "zeta.policy"])
        self.assertEqual(first.load_count, 0)
        self.assertEqual(second.load_count, 0)
        self.assertEqual(ignored.load_count, 0)
        self.assertTrue(registry.has(PluginKind.CONTEXT_POLICY, "acme-policy"))

    def test_load_is_lazy_cached_and_returns_reproducibility_metadata(self) -> None:
        point = _EntryPoint(
            name="acme.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(),
        )
        registry = PluginRegistry.discover(entry_points=[point])

        first = registry.load_context_policy("acme_policy")
        second = registry.load_context_policy("ACME-POLICY")

        self.assertIs(first, second)
        self.assertEqual(point.load_count, 1)
        self.assertIs(first.factory, _context_factory)
        self.assertEqual(first.provenance()["plugin_api_version"], PLUGIN_API_VERSION)
        self.assertEqual(first.provenance()["plugin_version"], "2.3.4")
        self.assertEqual(first.provenance()["distribution"], "acme-scaffoldscope")
        self.assertEqual(first.provenance()["distribution_version"], "2.3.4")
        self.assertEqual(first.implementation_hash_source, "factory_source")
        self.assertRegex(first.implementation_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            first.provenance()["implementation_sha256"],
            first.implementation_sha256,
        )

    def test_installed_python_file_hash_is_order_independent_and_content_sensitive(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "acme_plugin"
            package.mkdir()
            first_source = package / "alpha.py"
            second_source = package / "beta.py"
            ignored = package / "notes.txt"
            first_source.write_text("VALUE = 1\n", encoding="utf-8")
            second_source.write_text("VALUE = 2\n", encoding="utf-8")
            ignored.write_text("not implementation\n", encoding="utf-8")
            files = ("acme_plugin/alpha.py", "acme_plugin/beta.py", "acme_plugin/notes.txt")

            first = PluginRegistry.discover(
                entry_points=[
                    _EntryPoint(
                        name="acme.policy",
                        group=CONTEXT_POLICY_ENTRY_POINT,
                        exported=_context_registration(),
                        root=root,
                        files=files,
                    )
                ]
            ).load_context_policy("acme.policy")
            ignored.write_text("changed metadata only\n", encoding="utf-8")
            reordered = PluginRegistry.discover(
                entry_points=[
                    _EntryPoint(
                        name="acme.policy",
                        group=CONTEXT_POLICY_ENTRY_POINT,
                        exported=_context_registration(),
                        root=root,
                        files=tuple(reversed(files)),
                    )
                ]
            ).load_context_policy("acme.policy")

            self.assertEqual(first.implementation_hash_source, "distribution_python_files")
            self.assertEqual(first.implementation_sha256, reordered.implementation_sha256)

            second_source.write_text("VALUE = 3\n", encoding="utf-8")
            changed = PluginRegistry.discover(
                entry_points=[
                    _EntryPoint(
                        name="acme.policy",
                        group=CONTEXT_POLICY_ENTRY_POINT,
                        exported=_context_registration(),
                        root=root,
                        files=files,
                    )
                ]
            ).load_context_policy("acme.policy")
            self.assertNotEqual(first.implementation_sha256, changed.implementation_sha256)

    def test_load_fails_when_no_safe_implementation_source_exists(self) -> None:
        point = _EntryPoint(
            name="opaque.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=context_policy_plugin(
                len,  # type: ignore[arg-type]
                plugin_version="1.0.0",
                description="An intentionally opaque factory.",
            ),
        )

        with self.assertRaises(PluginLoadError) as raised:
            PluginRegistry.discover(entry_points=[point]).load_context_policy("opaque.policy")

        self.assertIn("implementation SHA-256", str(raised.exception))
        self.assertIn("inspectable", str(raised.exception))

    def test_unsafe_distribution_path_uses_factory_source_fallback(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "installed"
            root.mkdir()
            (root.parent / "outside.py").write_text("SECRET = 'not read as package code'\n")
            point = _EntryPoint(
                name="acme.policy",
                group=CONTEXT_POLICY_ENTRY_POINT,
                exported=_context_registration(),
                root=root,
                files=("../outside.py",),
            )

            loaded = PluginRegistry.discover(entry_points=[point]).load_context_policy(
                "acme.policy"
            )

        self.assertEqual(loaded.implementation_hash_source, "factory_source")

    def test_editable_distribution_hashes_factory_source_not_import_hook(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "editable_finder.py"
            shim.write_text("MAPPING = {}\n", encoding="utf-8")
            point = _EntryPoint(
                name="acme.policy",
                group=CONTEXT_POLICY_ENTRY_POINT,
                exported=_context_registration(),
                root=root,
                files=("editable_finder.py",),
                editable=True,
            )

            loaded = PluginRegistry.discover(entry_points=[point]).load_context_policy(
                "acme.policy"
            )

        self.assertEqual(loaded.implementation_hash_source, "factory_source")

    def test_model_provider_group_has_a_typed_load_path(self) -> None:
        registration = model_provider_plugin(
            _model_factory,
            plugin_version="1.0.0",
            description="A deterministic test provider.",
        )
        point = _EntryPoint(
            name="acme.provider",
            group=MODEL_PROVIDER_ENTRY_POINT,
            exported=registration,
        )

        loaded = PluginRegistry.discover(entry_points=[point]).load_model_provider("acme.provider")

        self.assertIs(loaded.factory, _model_factory)
        self.assertEqual(loaded.registration.kind, PluginKind.MODEL_PROVIDER)

    def test_normalized_third_party_collision_is_an_error(self) -> None:
        one = _EntryPoint(
            name="acme_fast",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(),
            distribution="first-package",
        )
        two = _EntryPoint(
            name="acme-fast",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(),
            distribution="second-package",
        )

        with self.assertRaises(PluginCollisionError) as raised:
            PluginRegistry.discover(entry_points=[two, one])

        message = str(raised.exception)
        self.assertIn("first-package", message)
        self.assertIn("second-package", message)
        self.assertIn("organization-prefixed", message)

    def test_builtin_names_cannot_be_shadowed(self) -> None:
        point = _EntryPoint(
            name="openai-compatible",
            group=MODEL_PROVIDER_ENTRY_POINT,
            exported=model_provider_plugin(
                _model_factory,
                plugin_version="1.0.0",
                description="Should never shadow the built-in.",
            ),
        )

        with self.assertRaises(PluginCollisionError) as raised:
            PluginRegistry.discover(entry_points=[point])

        self.assertIn("collides with a built-in", str(raised.exception))

    def test_incompatible_api_and_core_versions_are_actionable(self) -> None:
        wrong_api = _EntryPoint(
            name="future.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(api_version=PLUGIN_API_VERSION + 1),
        )
        with self.assertRaises(PluginCompatibilityError) as api_error:
            PluginRegistry.discover(entry_points=[wrong_api]).load_context_policy("future.policy")
        self.assertIn("provides API", str(api_error.exception))
        self.assertIn("Hint:", str(api_error.exception))

        wrong_core = _EntryPoint(
            name="old.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(
                minimum_core_version="0.4.0",
                maximum_core_version_exclusive="1.0.0",
            ),
        )
        with self.assertRaises(PluginCompatibilityError) as core_error:
            PluginRegistry.discover(entry_points=[wrong_core]).load_context_policy("old.policy")
        self.assertIn("not installed version 0.3.0", str(core_error.exception))

    def test_wrong_export_kind_and_missing_plugin_are_actionable(self) -> None:
        wrong_kind = _EntryPoint(
            name="acme.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=model_provider_plugin(
                _model_factory,
                plugin_version="1.0.0",
                description="Wrong group on purpose.",
            ),
        )
        registry = PluginRegistry.discover(entry_points=[wrong_kind])
        with self.assertRaises(PluginLoadError) as kind_error:
            registry.load_context_policy("acme.policy")
        self.assertIn("matching registration helper", str(kind_error.exception))

        with self.assertRaises(PluginLoadError) as missing_error:
            registry.load_model_provider("not-installed")
        self.assertIn(MODEL_PROVIDER_ENTRY_POINT, str(missing_error.exception))

    def test_import_failure_is_wrapped_once_with_distribution_context(self) -> None:
        point = _EntryPoint(
            name="broken.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=ImportError("missing optional accelerator"),
        )
        registry = PluginRegistry.discover(entry_points=[point])

        for _attempt in range(2):
            with self.assertRaises(PluginLoadError) as raised:
                registry.load_context_policy("broken.policy")
            self.assertIn("acme-scaffoldscope 2.3.4", str(raised.exception))
            self.assertIn("missing optional accelerator", str(raised.exception))
        self.assertEqual(point.load_count, 1)

    def test_invalid_registration_metadata_is_rejected(self) -> None:
        invalid_version = _EntryPoint(
            name="bad.policy",
            group=CONTEXT_POLICY_ENTRY_POINT,
            exported=_context_registration(minimum_core_version=">=0.1"),
        )
        with self.assertRaises(PluginLoadError) as raised:
            PluginRegistry.discover(entry_points=[invalid_version]).load_context_policy(
                "bad.policy"
            )
        self.assertIn("numeric dotted release", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
