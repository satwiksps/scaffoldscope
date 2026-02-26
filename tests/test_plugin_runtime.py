from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

from scaffoldscope.context import ContextPolicy, make_policy
from scaffoldscope.models import make_model
from scaffoldscope.schema import RunConfig
from scaffoldscope.tokenization import Char4TokenCounter

DEMO = Path(__file__).resolve().parents[1] / "src" / "scaffoldscope" / "demo"
PLUGIN_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "runtime_plugin"
PLUGIN_MODULE = "scaffoldscope_runtime_test_plugin"


def _install_fixture_distribution(site: Path) -> tuple[metadata.EntryPoint, ...]:
    shutil.copy2(PLUGIN_FIXTURE / f"{PLUGIN_MODULE}.py", site / f"{PLUGIN_MODULE}.py")
    dist_info = site / "scaffoldscope_runtime_test_plugin-1.0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: scaffoldscope-runtime-test-plugin\nVersion: 1.0.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[scaffoldscope.context_policies]\n"
        f"runtime.policy = {PLUGIN_MODULE}:policy_registration\n"
        "[scaffoldscope.model_providers]\n"
        f"runtime.model = {PLUGIN_MODULE}:provider_registration\n",
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text(
        f"{PLUGIN_MODULE}.py,,\n"
        f"{dist_info.name}/METADATA,,\n"
        f"{dist_info.name}/entry_points.txt,,\n"
        f"{dist_info.name}/RECORD,,\n",
        encoding="utf-8",
    )
    distributions = tuple(metadata.distributions(path=[str(site)]))
    if len(distributions) != 1:
        raise AssertionError(f"Expected one fixture distribution, found {len(distributions)}")
    return tuple(distributions[0].entry_points)


class PluginRuntimeIntegrationTests(unittest.TestCase):
    def test_run_config_and_factories_select_real_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            entry_points = _install_fixture_distribution(site)
            project = root / "experiment"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["tasks"]["limit"] = 1
            raw["experiment"].update(
                {
                    "baseline": "runtime-policy",
                    "primary_comparison": None,
                    "replicates": [7],
                }
            )
            policy_options = {"keep": "atomic", "nested": {"enabled": True}}
            model_options = {"reply": "third-party runtime complete", "mode": "offline"}
            raw["variants"] = [
                {
                    "id": "runtime-policy",
                    "policy": "runtime.policy",
                    "plugin_options": policy_options,
                }
            ]
            raw["model"].update(
                {
                    "provider": "runtime.model",
                    "name": "runtime-model-1",
                    "plugin_options": model_options,
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            selectable = metadata.EntryPoints(entry_points)
            sys.modules.pop(PLUGIN_MODULE, None)
            self.addCleanup(sys.modules.pop, PLUGIN_MODULE, None)
            with (
                patch.object(sys, "path", [str(site), *sys.path]),
                patch("scaffoldscope.plugins.metadata.entry_points", return_value=selectable),
            ):
                importlib.invalidate_caches()
                config = RunConfig.load(config_path)
                counter = Char4TokenCounter()
                policy = make_policy(config.variants[0], counter, config.plugin_registry)
                model = make_model(
                    config.model,
                    config.tasks[0],
                    counter,
                    registry=config.plugin_registry,
                )
                response = model.complete(
                    [{"role": "user", "content": "Finish offline."}],
                    seed=7,
                    max_output_tokens=32,
                    temperature=0.0,
                )
                plugin_module = importlib.import_module(PLUGIN_MODULE)

                self.assertIsInstance(policy, ContextPolicy)
                self.assertEqual(type(policy).__name__, "RuntimePolicy")
                self.assertEqual(type(model).__name__, "RuntimeModel")
                self.assertEqual(json.loads(response.content)["final"], model_options["reply"])
                self.assertEqual(plugin_module.policy_options_seen, [policy_options])
                self.assertEqual(plugin_module.model_options_seen, [model_options])

                policy_key = "context_policy:runtime-policy"
                provider_key = "model_provider:runtime-model"
                self.assertEqual(set(config.plugin_provenance), {policy_key, provider_key})
                for provenance in config.plugin_provenance.values():
                    self.assertEqual(
                        provenance["distribution"],
                        "scaffoldscope-runtime-test-plugin",
                    )
                    self.assertEqual(
                        provenance["implementation_hash_source"],
                        "distribution_python_files",
                    )
                    self.assertRegex(
                        str(provenance["implementation_sha256"]),
                        r"^[0-9a-f]{64}$",
                    )

                original_hash = config.config_hash
                original_implementation = config.plugin_provenance[policy_key][
                    "implementation_sha256"
                ]
                raw["variants"][0]["plugin_options"]["keep"] = "task-and-atomic"
                config_path.write_text(json.dumps(raw), encoding="utf-8")
                options_changed = RunConfig.load(config_path)
                self.assertNotEqual(original_hash, options_changed.config_hash)
                self.assertEqual(
                    original_implementation,
                    options_changed.plugin_provenance[policy_key]["implementation_sha256"],
                )

                raw["variants"][0]["plugin_options"]["keep"] = "atomic"
                config_path.write_text(json.dumps(raw), encoding="utf-8")
                installed_source = site / f"{PLUGIN_MODULE}.py"
                installed_source.write_text(
                    installed_source.read_text(encoding="utf-8") + "\n# implementation drift\n",
                    encoding="utf-8",
                )
                implementation_changed = RunConfig.load(config_path)
                self.assertNotEqual(original_hash, implementation_changed.config_hash)
                self.assertNotEqual(
                    original_implementation,
                    implementation_changed.plugin_provenance[policy_key]["implementation_sha256"],
                )


if __name__ == "__main__":
    unittest.main()
