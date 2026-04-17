from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.runner import run_experiment
from scaffoldscope.schema import RunConfig
from scaffoldscope.starter import StarterError, create_starter_project


class StarterProjectTests(unittest.TestCase):
    def test_creates_runnable_valid_starter_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "study"
            first = create_starter_project(destination, name="context-study")

            self.assertTrue(first.initialized)
            self.assertEqual(first.root, destination.absolute())
            self.assertEqual(first.config_path, destination.absolute() / "experiment.json")
            self.assertTrue((destination / ".gitignore").is_file())
            self.assertTrue((destination / "README.md").is_file())
            self.assertTrue((destination / "tasks.jsonl").is_file())
            self.assertTrue(
                (destination / "workspaces" / "text-cleaner" / "test_text_cleaner.py").is_file()
            )

            config = RunConfig.load(first.config_path)
            self.assertEqual(config.experiment.name, "context-study")
            self.assertEqual(len(config.tasks), 1)
            self.assertEqual(
                [variant.id for variant in config.variants],
                [
                    "none",
                    "reactive-80",
                    "selective",
                ],
            )
            run = run_experiment(config)
            self.assertEqual(run.scheduled, 3)
            self.assertEqual(run.completed, 3)
            self.assertEqual(run.failed, 0)

            second = create_starter_project(destination, name="context-study")
            self.assertFalse(second.initialized)
            self.assertFalse(second.created_files)
            self.assertEqual(set(second.preserved_files), set(first.created_files))

    def test_rerun_preserves_operator_edits_and_restores_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "study"
            create_starter_project(destination)
            readme = destination / "README.md"
            readme.write_text("my research notes\n", encoding="utf-8")
            missing = destination / "workspaces" / "text-cleaner" / "text_cleaner.py"
            missing.unlink()

            result = create_starter_project(destination)

            self.assertEqual(readme.read_text(encoding="utf-8"), "my research notes\n")
            self.assertEqual(result.created_files, (missing,))
            self.assertIn(readme, result.preserved_files)
            self.assertIn("def collapse_spaces", missing.read_text(encoding="utf-8"))

    def test_refuses_nonempty_unowned_or_conflicting_destinations_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "existing"
            destination.mkdir()
            sentinel = destination / "important.txt"
            sentinel.write_text("do not touch", encoding="utf-8")

            with self.assertRaisesRegex(StarterError, "not a ScaffoldScope starter"):
                create_starter_project(destination)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not touch")
            self.assertEqual(list(destination.iterdir()), [sentinel])

            partial = Path(temporary) / "partial"
            partial.mkdir()
            (partial / "README.md").write_text("someone else's file\n", encoding="utf-8")
            with self.assertRaisesRegex(StarterError, "conflicting unowned file"):
                create_starter_project(partial)
            self.assertFalse((partial / "experiment.json").exists())

    def test_recovers_exact_interrupted_scaffold_and_rejects_name_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "study"
            destination.mkdir()
            expected_readme = (
                Path(__file__).resolve().parents[1]
                / "src"
                / "scaffoldscope"
                / "starter_assets"
                / "README.md"
            ).read_bytes()
            (destination / "README.md").write_bytes(expected_readme)

            result = create_starter_project(destination, name="first-study")

            self.assertTrue(result.initialized)
            marker = json.loads(
                (destination / ".scaffoldscope-project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["experiment_name"], "first-study")
            with self.assertRaisesRegex(StarterError, "not 'second-study'"):
                create_starter_project(destination, name="second-study")

    def test_rejects_invalid_name_and_file_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaisesRegex(StarterError, "experiment name"):
                create_starter_project(base / "bad", name="has spaces")
            destination = base / "file"
            destination.write_text("content", encoding="utf-8")
            with self.assertRaisesRegex(StarterError, "not a directory"):
                create_starter_project(destination)

    def test_preserves_caller_path_identity_through_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            actual_parent = base / "actual"
            actual_parent.mkdir()
            alias_parent = base / "alias"
            try:
                alias_parent.symlink_to(actual_parent, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            destination = alias_parent / "study"
            result = create_starter_project(destination)

            self.assertEqual(result.root, destination.absolute())
            self.assertEqual(result.config_path, destination.absolute() / "experiment.json")
            self.assertTrue(result.config_path.is_file())
            self.assertTrue(all(path.is_relative_to(result.root) for path in result.created_files))
            self.assertNotEqual(result.root, destination.resolve())


if __name__ == "__main__":
    unittest.main()
