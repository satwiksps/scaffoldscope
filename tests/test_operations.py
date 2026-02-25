from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.operations import budget_estimate, experiment_status
from scaffoldscope.schema import RunConfig


class OperationsTests(unittest.TestCase):
    def test_status_reads_durable_trials_instead_of_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = root / "run"
            (experiment / "trials" / "trial-a").mkdir(parents=True)
            (experiment / "trials" / "trial-b").mkdir(parents=True)
            (experiment / "manifest.json").write_text(
                json.dumps(
                    {
                        "experiment": "status-test",
                        "config_hash": "abc",
                        "variants": ["none", "selective"],
                        "trial_count": 2,
                    }
                ),
                encoding="utf-8",
            )
            plan = [
                {
                    "trial_id": "trial-a",
                    "trial_hash": "hash-a",
                    "task_id": "task",
                    "variant_id": "none",
                    "replicate": 7,
                },
                {
                    "trial_id": "trial-b",
                    "trial_hash": "hash-b",
                    "task_id": "task",
                    "variant_id": "selective",
                    "replicate": 7,
                },
            ]
            (experiment / "plan.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in plan), encoding="utf-8"
            )
            result = {
                **plan[0],
                "config_hash": "abc",
                "status": "resolved",
                "agent": {"usage": {"total_tokens": 42, "cost_usd": 0.02, "complete": True}},
            }
            (experiment / "trials" / "trial-a" / "result.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            (experiment / "trials" / "trial-b" / "events.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )

            status = experiment_status(experiment)

            self.assertEqual(status["completed_trials"], 1)
            self.assertEqual(status["started_without_result"], 1)
            self.assertEqual(status["remaining_trials"], 1)
            self.assertEqual(status["reported_tokens"], 42)
            self.assertEqual(status["reported_cost_usd"], 0.02)
            self.assertEqual(status["pair_coverage"], 0.0)

    def test_budget_estimate_exposes_grid_and_power_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "repo"
            workspace.mkdir()
            (workspace / "code.py").write_text("x = 1\n", encoding="utf-8")
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                json.dumps(
                    {
                        "id": "task-a",
                        "workspace": "repo",
                        "problem": "Change x",
                        "test_command": [],
                        "script": [{"final": "done"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config_path = root / "experiment.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "experiment": {
                            "name": "budget-test",
                            "replicates": [1, 2],
                            "output_dir": "runs",
                            "baseline": "none",
                        },
                        "tasks": {"manifest": "tasks.jsonl"},
                        "model": {
                            "provider": "scripted",
                            "name": "scripted",
                            "context_window_tokens": 1000,
                            "max_output_tokens": 100,
                            "supports_seed": False,
                            "input_price_per_million": 1.0,
                            "output_price_per_million": 2.0,
                        },
                        "agent": {"max_turns": 5, "max_total_tokens": 1000},
                        "variants": [
                            {"id": "none", "policy": "none"},
                            {"id": "selective", "policy": "selective"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            estimate = budget_estimate(RunConfig.load(config_path))

            self.assertEqual(estimate["scheduled_trials"], 4)
            self.assertEqual(estimate["maximum_model_calls"], 20)
            self.assertEqual(estimate["maximum_total_tokens"], 4000)
            self.assertEqual(estimate["maximum_configured_cost_usd"], 0.008)
            self.assertEqual(
                {warning["code"] for warning in estimate["warnings"]},
                {"LOW_TASK_COUNT", "SEED_UNCONFIRMED"},
            )


if __name__ == "__main__":
    unittest.main()
