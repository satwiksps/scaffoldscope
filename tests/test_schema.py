from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.schema import RunConfig, _directory_fingerprint

DEMO_CONFIG = (
    Path(__file__).resolve().parents[1] / "src" / "scaffoldscope" / "demo" / "experiment.json"
)


class ConfigTests(unittest.TestCase):
    def test_directory_fingerprint_records_are_structurally_unambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            one_file = root / "one-file"
            two_files = root / "two-files"
            one_file.mkdir()
            two_files.mkdir()
            ambiguous_suffix = b"\0b\0file\0" + b"000" + b"\0Y"
            (one_file / "a").write_bytes(b"X" + ambiguous_suffix)
            (two_files / "a").write_bytes(b"X")
            (two_files / "b").write_bytes(b"Y")

            self.assertNotEqual(
                _directory_fingerprint(one_file),
                _directory_fingerprint(two_files),
            )

    def test_non_git_task_identity_covers_empty_directories_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            initial = RunConfig.load(config_path)

            empty_directory = project / "fixtures" / "calculator" / "empty-marker"
            empty_directory.mkdir()
            with_empty_directory = RunConfig.load(config_path)
            self.assertNotEqual(initial.config_hash, with_empty_directory.config_hash)

            if os.name == "posix":
                source = project / "fixtures" / "calculator" / "calculator.py"
                source.chmod(source.stat().st_mode | stat.S_IXUSR)
                with_executable = RunConfig.load(config_path)
                self.assertNotEqual(
                    with_empty_directory.config_hash,
                    with_executable.config_hash,
                )

                source.chmod(source.stat().st_mode & ~stat.S_IWUSR)
                with_read_only_source = RunConfig.load(config_path)
                self.assertNotEqual(
                    with_executable.config_hash,
                    with_read_only_source.config_hash,
                )

                empty_directory.chmod(empty_directory.stat().st_mode & ~stat.S_IWUSR)
                with_read_only_directory = RunConfig.load(config_path)
                self.assertNotEqual(
                    with_read_only_source.config_hash,
                    with_read_only_directory.config_hash,
                )

    def test_explicit_null_optional_prompt_and_local_docker_match_schema_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["agent"].update({"system_prompt": None, "prompt_file": None})
            raw["sandbox"]["docker"] = None
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            config = RunConfig.load(config_path)

            self.assertIsNone(config.agent.system_prompt)
            self.assertIsNone(config.docker)

    def test_demo_config_is_valid_and_stable(self) -> None:
        first = RunConfig.load(DEMO_CONFIG)
        second = RunConfig.load(DEMO_CONFIG)
        self.assertEqual(len(first.tasks), 3)
        self.assertEqual(len(first.variants), 4)
        self.assertEqual(first.config_hash, second.config_hash)
        self.assertEqual(first.experiment.baseline, "none")

    def test_task_prompt_contains_machine_readable_constraint_ids(self) -> None:
        config = RunConfig.load(DEMO_CONFIG)
        prompt = config.tasks[0].prompt()
        self.assertIn("[preserve-api]", prompt)
        self.assertIn("[keep-canary]", prompt)

    def test_material_config_values_are_strict(self) -> None:
        cases = {
            "string boolean": lambda raw: raw["model"].__setitem__("supports_seed", "false"),
            "oversized output": lambda raw: raw["model"].update(
                {"context_window_tokens": 128, "max_output_tokens": 128}
            ),
            "string analysis seed": lambda raw: raw["experiment"].__setitem__(
                "analysis_seed", "123"
            ),
            "unknown weight": lambda raw: raw["variants"][-1]["weights"].__setitem__("mystery", 1),
            "unknown root field": lambda raw: raw.__setitem__("max_tokens", 42),
            "misspelled model field": lambda raw: raw["model"].__setitem__("max_ouput_tokens", 42),
            "missing schema version": lambda raw: raw.pop("schema_version"),
            "negative replicate": lambda raw: raw["experiment"].__setitem__("replicates", [-1]),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / "demo"
                shutil.copytree(DEMO_CONFIG.parent, project)
                config_path = project / "experiment.json"
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                mutate(raw)
                config_path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    RunConfig.load(config_path)

    def test_docker_backend_requires_a_digest_pinned_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["sandbox"].update(
                {
                    "backend": "docker",
                    "docker": {
                        "image": "python@sha256:" + "a" * 64,
                        "cpus": 1.5,
                        "memory_bytes": 256 * 1024 * 1024,
                    },
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            config = RunConfig.load(config_path)

            self.assertEqual(config.sandbox.backend, "docker")
            self.assertIsNotNone(config.docker)
            assert config.docker is not None
            self.assertEqual(config.docker.cpus, 1.5)
            self.assertTrue(config.docker.require_image_digest)
            first_hash = config.config_hash

            raw["sandbox"]["docker"]["cpus"] = 1.75
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertNotEqual(RunConfig.load(config_path).config_hash, first_hash)

            raw["sandbox"]["docker"]["image"] = "python:latest"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "sha256"):
                RunConfig.load(config_path)

    def test_local_backend_rejects_unused_docker_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["sandbox"]["docker"] = {"image": "python@sha256:" + "a" * 64}
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "only valid"):
                RunConfig.load(config_path)

    def test_agent_rejects_inline_and_file_prompts_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["agent"].update(
                {
                    "system_prompt": "Keep the task constraints intact.",
                    "prompt_file": "system-prompt.txt",
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "only one"):
                RunConfig.load(config_path)

    def test_task_selection_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["tasks"]["ids"] = ["calculator-add", "calculator-add"]
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "unique task IDs"):
                RunConfig.load(config_path)

    def test_variants_can_declare_a_reproducible_tool_and_instruction_treatment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["variants"][0]["tools"] = ["read_file", "search_symbols", "replace"]
            raw["variants"][0]["instructions"] = "Use symbol search before editing."
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            config = RunConfig.load(config_path)

            self.assertEqual(
                config.variants[0].tools,
                ("read_file", "search_symbols", "replace"),
            )
            self.assertEqual(
                config.variants[0].instructions,
                "Use symbol search before editing.",
            )

            raw["variants"][0]["tools"] = ["read_file", "arbitrary_shell"]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, r"variants\[0\]\.tools"):
                RunConfig.load(config_path)

    def test_explicit_null_variant_treatments_match_schema_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["variants"][0]["tools"] = None
            raw["variants"][0]["instructions"] = None
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            config = RunConfig.load(config_path)

            self.assertIsNone(config.variants[0].tools)
            self.assertIsNone(config.variants[0].instructions)

    def test_remote_plain_http_cannot_receive_an_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["model"].update(
                {
                    "provider": "openai_compatible",
                    "base_url": "http://models.example.invalid/v1",
                    "requires_api_key": True,
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "must use HTTPS"):
                RunConfig.load(config_path)

            raw["model"].update(
                {
                    "base_url": "http://127.0.0.1:11434/v1",
                    "requires_api_key": False,
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertFalse(RunConfig.load(config_path).model.requires_api_key)

    def test_output_directory_cannot_contaminate_a_task_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_CONFIG.parent, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"]["output_dir"] = "fixtures/calculator/generated-runs"
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "contaminate"):
                RunConfig.load(config_path)


if __name__ == "__main__":
    unittest.main()
