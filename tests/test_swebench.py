from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import load_json, load_jsonl
from scaffoldscope.swebench import (
    export_swebench_matrix,
    export_swebench_predictions,
    import_swebench_manifest,
    ingest_swebench_results,
)


class SwebenchTests(unittest.TestCase):
    @staticmethod
    def _external_experiment(root: Path, *task_ids: str) -> Path:
        experiment = root / "experiment"
        experiment.mkdir()
        (experiment / "manifest.json").write_text(
            json.dumps(
                {
                    "config_hash": "test-config-hash",
                    "tasks": list(task_ids),
                    "variants": ["selective"],
                    "replicates": [17],
                }
            ),
            encoding="utf-8",
        )
        (experiment / "episodes.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "task_id": task_id,
                        "variant_id": "selective",
                        "replicate": 17,
                    }
                )
                + "\n"
                for task_id in task_ids
            ),
            encoding="utf-8",
        )
        return experiment

    def test_ingest_rejects_non_object_experiment_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = root / "experiment"
            experiment.mkdir()
            (experiment / "manifest.json").write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "manifest must be a JSON object"):
                ingest_swebench_results(
                    experiment,
                    root / "results.json",
                    strategy="selective",
                    replicate=17,
                    evaluator_version="commit",
                    evaluator_run_id="run",
                    image_set_digest="0" * 64,
                )

    def test_import_maps_official_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "swe.json"
            source.write_text(
                json.dumps(
                    [
                        {
                            "instance_id": "owner__repo-1",
                            "repo": "owner/repo",
                            "base_commit": "abc123",
                            "problem_statement": "Fix it",
                            "FAIL_TO_PASS": ["test_x"],
                            "PASS_TO_PASS": [],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            cache = root / "repos"
            (cache / "owner__repo").mkdir(parents=True)
            output = root / "tasks.jsonl"
            count = import_swebench_manifest(source, cache, output)
            self.assertEqual(count, 1)
            row = load_jsonl(output)[0]
            self.assertEqual(
                row,
                {
                    "id": "owner__repo-1",
                    "repository": "owner/repo",
                    "workspace": str((cache / "owner__repo").resolve()),
                    "base_commit": "abc123",
                    "problem": "Fix it",
                    "constraints": [],
                    "test_command": [],
                    "metadata": {
                        "FAIL_TO_PASS": ["test_x"],
                        "PASS_TO_PASS": [],
                        "source": "swe-bench",
                    },
                },
            )

    def test_import_accepts_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "swe.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "instance_id": "owner__repo-2",
                        "repo": "owner/repo",
                        "base_commit": "def456",
                        "problem_statement": "Fix the JSONL case",
                        "FAIL_TO_PASS": ["test_jsonl"],
                        "PASS_TO_PASS": ["test_existing"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            cache = root / "repos"
            (cache / "owner" / "repo").mkdir(parents=True)
            output = root / "tasks.jsonl"

            count = import_swebench_manifest(source, cache, output)

            self.assertEqual(
                (count, load_jsonl(output)),
                (
                    1,
                    [
                        {
                            "id": "owner__repo-2",
                            "repository": "owner/repo",
                            "workspace": str((cache / "owner" / "repo").resolve()),
                            "base_commit": "def456",
                            "problem": "Fix the JSONL case",
                            "constraints": [],
                            "test_command": [],
                            "metadata": {
                                "FAIL_TO_PASS": ["test_jsonl"],
                                "PASS_TO_PASS": ["test_existing"],
                                "source": "swe-bench",
                            },
                        }
                    ],
                ),
            )

    def test_import_rejects_a_missing_repository_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "swe.json"
            source.write_text(
                json.dumps(
                    [
                        {
                            "instance_id": "owner__repo-1",
                            "repo": "owner/repo",
                            "problem_statement": "Fix it",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            output = root / "tasks.jsonl"

            with self.assertRaisesRegex(ConfigError, "no checkout for 'owner/repo'"):
                import_swebench_manifest(source, root / "repos", output)

            self.assertFalse(output.exists())

    def test_export_requires_a_complete_cell_and_preserves_patches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = Path(temporary) / "experiment"
            experiment.mkdir()
            (experiment / "manifest.json").write_text(
                json.dumps({"tasks": ["task-a", "task-b"]}),
                encoding="utf-8",
            )
            rows = []
            for task_id in ("task-a", "task-b"):
                trial = experiment / "trials" / task_id
                trial.mkdir(parents=True)
                (trial / "patch.diff").write_text(
                    f"diff --git a/{task_id} b/{task_id}\n",
                    encoding="utf-8",
                )
                rows.append(
                    {
                        "task_id": task_id,
                        "variant_id": "selective",
                        "replicate": 17,
                        "model_name": "pinned-model",
                        "artifacts": {"patch": f"trials/{task_id}/patch.diff"},
                    }
                )
            (experiment / "episodes.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            output = Path(temporary) / "predictions.jsonl"

            with self.assertRaisesRegex(ConfigError, "outside the experiment directory"):
                export_swebench_predictions(
                    experiment,
                    experiment / "predictions.jsonl",
                    strategy="selective",
                    replicate=17,
                )

            count = export_swebench_predictions(
                experiment,
                output,
                strategy="selective",
                replicate=17,
            )

            self.assertEqual(count, 2)
            predictions = load_jsonl(output)
            self.assertEqual({row["instance_id"] for row in predictions}, {"task-a", "task-b"})
            self.assertTrue(all("diff --git" in row["model_patch"] for row in predictions))

            (experiment / "episodes.jsonl").write_text(
                json.dumps(rows[0]) + "\n",
                encoding="utf-8",
            )
            original_output = output.read_bytes()
            with self.assertRaises(ConfigError):
                export_swebench_predictions(
                    experiment,
                    output,
                    strategy="selective",
                    replicate=17,
                )
            self.assertEqual(output.read_bytes(), original_output)

            escaped = dict(rows[0])
            escaped["artifacts"] = {"patch": "../outside.diff"}
            (experiment / "manifest.json").write_text(
                json.dumps({"tasks": ["task-a"]}), encoding="utf-8"
            )
            (experiment / "episodes.jsonl").write_text(json.dumps(escaped) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "escapes"):
                export_swebench_predictions(
                    experiment,
                    Path(temporary) / "unsafe.jsonl",
                    strategy="selective",
                    replicate=17,
                )

    def test_ingest_accepts_per_instance_jsonl_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._external_experiment(root, "task-a", "task-b")
            results = root / "instance-results.jsonl"
            results.write_text(
                json.dumps({"instance_id": "task-a", "resolved": True})
                + "\n"
                + json.dumps({"instance_id": "task-b", "completed": False, "resolved": False})
                + "\n",
                encoding="utf-8",
            )

            overlay = ingest_swebench_results(
                experiment,
                results,
                strategy="selective",
                replicate=17,
                evaluator_version="commit",
                evaluator_run_id="run",
                image_set_digest="0" * 64,
            )

            self.assertEqual(
                load_json(overlay)["outcomes"],
                {
                    "task-a": {"completed": True, "resolved": True},
                    "task-b": {"completed": False, "resolved": False},
                },
            )

    def test_ingest_rejects_malformed_outcome_formats(self) -> None:
        cases = (
            (
                "empty JSONL instance id",
                "results.jsonl",
                json.dumps({"instance_id": "", "completed": True, "resolved": True}) + "\n",
                "non-empty instance_id",
            ),
            (
                "duplicate JSONL instance id",
                "results.jsonl",
                json.dumps({"instance_id": "task-a", "completed": True, "resolved": True})
                + "\n"
                + json.dumps({"instance_id": "task-a", "completed": True, "resolved": False})
                + "\n",
                "Duplicate official evaluator outcome",
            ),
            (
                "non-boolean JSONL outcome",
                "results.jsonl",
                json.dumps({"instance_id": "task-a", "completed": "yes", "resolved": False}) + "\n",
                "boolean completed/resolved fields",
            ),
            (
                "resolved incomplete JSONL outcome",
                "results.jsonl",
                json.dumps({"instance_id": "task-a", "completed": False, "resolved": True}) + "\n",
                "cannot resolve an incomplete run",
            ),
            (
                "non-object JSON document",
                "results.json",
                "[]",
                "must be a JSON object or instance JSONL",
            ),
            (
                "non-list aggregate bucket",
                "results.json",
                json.dumps({"resolved_ids": "task-a", "unresolved_ids": [], "incomplete_ids": []}),
                "field resolved_ids must be a list",
            ),
            (
                "duplicate aggregate outcome",
                "results.json",
                json.dumps(
                    {
                        "resolved_ids": ["task-a"],
                        "unresolved_ids": ["task-a"],
                        "incomplete_ids": [],
                    }
                ),
                "Duplicate official evaluator outcome",
            ),
            (
                "non-object mapped outcome",
                "results.json",
                json.dumps({"task-a": True}),
                "Could not recognize SWE-bench results",
            ),
        )
        for label, filename, content, message in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                experiment = self._external_experiment(root, "task-a")
                results = root / filename
                results.write_text(content, encoding="utf-8")

                with self.assertRaisesRegex(ConfigError, message):
                    ingest_swebench_results(
                        experiment,
                        results,
                        strategy="selective",
                        replicate=17,
                        evaluator_version="commit",
                        evaluator_run_id="run",
                        image_set_digest="0" * 64,
                    )

    def test_matrix_export_writes_every_cell_and_unique_run_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = root / "experiment"
            experiment.mkdir()
            (experiment / "manifest.json").write_text(
                json.dumps(
                    {
                        "experiment": "matrix-study",
                        "config_hash": "abcdef123456" + "0" * 52,
                        "tasks": ["task-a"],
                        "variants": ["none", "selective"],
                        "replicates": [17, 23],
                    }
                ),
                encoding="utf-8",
            )
            rows = []
            for strategy in ("none", "selective"):
                for replicate in (17, 23):
                    trial = experiment / "trials" / f"{strategy}-{replicate}"
                    trial.mkdir(parents=True)
                    (trial / "patch.diff").write_text(
                        f"diff --git a/{strategy} b/{strategy}\n", encoding="utf-8"
                    )
                    rows.append(
                        {
                            "task_id": "task-a",
                            "variant_id": strategy,
                            "replicate": replicate,
                            "model_name": "model-revision",
                            "artifacts": {"patch": f"trials/{strategy}-{replicate}/patch.diff"},
                        }
                    )
            (experiment / "episodes.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )

            matrix_path = export_swebench_matrix(
                experiment,
                root / "matrix",
                dataset_name="SWE-bench/SWE-bench_Lite",
            )

            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            self.assertEqual(len(matrix["cells"]), 4)
            self.assertEqual(len({cell["evaluator_run_id"] for cell in matrix["cells"]}), 4)
            self.assertTrue(all(cell["predictions_sha256"] for cell in matrix["cells"]))
            runbook = (root / "matrix" / "evaluate.sh").read_text(encoding="utf-8")
            self.assertEqual(runbook.count("swebench.harness.run_evaluation"), 4)

            rows.pop()
            (experiment / "episodes.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            incomplete = root / "incomplete-matrix"
            with self.assertRaises(ConfigError):
                export_swebench_matrix(
                    experiment,
                    incomplete,
                    dataset_name="SWE-bench/SWE-bench_Lite",
                )
            self.assertFalse(incomplete.exists())


if __name__ == "__main__":
    unittest.main()
