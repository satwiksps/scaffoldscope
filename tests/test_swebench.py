from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import load_jsonl
from scaffoldscope.swebench import (
    export_swebench_matrix,
    export_swebench_predictions,
    import_swebench_manifest,
)


class SwebenchTests(unittest.TestCase):
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
            self.assertEqual(row["id"], "owner__repo-1")
            self.assertEqual(row["repository"], "owner/repo")

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
