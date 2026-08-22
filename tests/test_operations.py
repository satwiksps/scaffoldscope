from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.operations import budget_estimate, experiment_status, trial_inventory
from scaffoldscope.schema import RunConfig

DEMO_CONFIG = (
    Path(__file__).resolve().parents[1] / "src" / "scaffoldscope" / "demo" / "experiment.json"
)


class OperationsTests(unittest.TestCase):
    def test_trial_inventory_rejects_unknown_filters_instead_of_silent_empty_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = Path(temporary) / "run"
            experiment.mkdir()
            (experiment / "manifest.json").write_text(
                json.dumps({"config_hash": "abc"}), encoding="utf-8"
            )
            (experiment / "plan.jsonl").write_text(
                json.dumps(
                    {
                        "trial_id": "trial-a",
                        "trial_hash": "hash-a",
                        "task_id": "task-a",
                        "variant_id": "none",
                        "replicate": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(len(trial_inventory(experiment)), 1)
            for filters in (
                {"status": "typo"},
                {"variant": "typo"},
                {"task": "typo"},
            ):
                with (
                    self.subTest(filters=filters),
                    self.assertRaisesRegex(ConfigError, "Unknown .* filter"),
                ):
                    trial_inventory(experiment, **filters)

    def test_trial_inventory_applies_combined_status_variant_and_task_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = Path(temporary) / "run"
            (experiment / "trials" / "trial-a").mkdir(parents=True)
            (experiment / "trials" / "trial-b").mkdir(parents=True)
            (experiment / "manifest.json").write_text(
                json.dumps({"config_hash": "abc"}), encoding="utf-8"
            )
            plan = [
                {
                    "trial_id": "trial-a",
                    "trial_hash": "hash-a",
                    "task_id": "task-a",
                    "variant_id": "none",
                    "replicate": 1,
                },
                {
                    "trial_id": "trial-b",
                    "trial_hash": "hash-b",
                    "task_id": "task-b",
                    "variant_id": "selective",
                    "replicate": 1,
                },
            ]
            (experiment / "plan.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in plan), encoding="utf-8"
            )
            for row, status in zip(plan, ("resolved", "unresolved"), strict=True):
                (experiment / "trials" / row["trial_id"] / "result.json").write_text(
                    json.dumps({**row, "config_hash": "abc", "status": status}),
                    encoding="utf-8",
                )

            rows = trial_inventory(
                experiment,
                status="resolved",
                variant="none",
                task="task-a",
            )

            self.assertEqual([row["trial_id"] for row in rows], ["trial-a"])
            self.assertEqual(
                trial_inventory(
                    experiment,
                    status="resolved",
                    variant="selective",
                    task="task-a",
                ),
                [],
            )

    def test_status_and_inventory_reject_malformed_identity_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            malformed = root / "malformed"
            malformed.mkdir()
            (malformed / "manifest.json").write_text("[]", encoding="utf-8")
            (malformed / "plan.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "manifest must be a JSON object"):
                trial_inventory(malformed)

            unsafe = root / "unsafe"
            unsafe.mkdir()
            (unsafe / "manifest.json").write_text(
                json.dumps({"config_hash": "abc", "variants": ["none"]}),
                encoding="utf-8",
            )
            (unsafe / "plan.jsonl").write_text(
                json.dumps(
                    {
                        "trial_id": "../escape",
                        "trial_hash": "hash",
                        "task_id": "task-a",
                        "variant_id": "none",
                        "replicate": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "unsafe trial id"):
                trial_inventory(unsafe)

            invalid = root / "invalid-result"
            (invalid / "trials" / "trial-a").mkdir(parents=True)
            (invalid / "manifest.json").write_text(
                json.dumps({"config_hash": "abc", "variants": ["none"]}),
                encoding="utf-8",
            )
            plan = {
                "trial_id": "trial-a",
                "trial_hash": "hash-a",
                "task_id": "task-a",
                "variant_id": "none",
                "replicate": 1,
            }
            (invalid / "plan.jsonl").write_text(json.dumps(plan) + "\n", encoding="utf-8")
            (invalid / "trials" / "trial-a" / "result.json").write_text(
                json.dumps({**plan, "config_hash": "wrong", "status": "resolved"}),
                encoding="utf-8",
            )

            self.assertEqual(trial_inventory(invalid)[0]["status"], "invalid_result")
            status = experiment_status(invalid)
            self.assertEqual(status["completed_trials"], 0)
            self.assertEqual(status["malformed_result_trials"], ["trial-a"])

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

    def test_budget_estimate_distinguishes_caps_cache_prices_and_unbounded_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = json.loads(DEMO_CONFIG.read_text(encoding="utf-8"))
            base["tasks"]["manifest"] = str(DEMO_CONFIG.with_name("tasks.jsonl"))
            base["experiment"]["output_dir"] = str(root / "runs")

            capped = json.loads(json.dumps(base))
            capped["agent"]["max_cost_usd"] = 1.25
            capped_path = root / "capped.json"
            capped_path.write_text(json.dumps(capped), encoding="utf-8")
            capped_estimate = budget_estimate(RunConfig.load(capped_path))
            self.assertEqual(capped_estimate["maximum_configured_cost_usd"], 15.0)
            self.assertEqual(
                capped_estimate["cost_bound_basis"],
                "sum of configured hard per-trial cost caps",
            )

            cached = json.loads(json.dumps(base))
            cached["model"].update(
                {
                    "input_price_per_million": 1.0,
                    "output_price_per_million": 2.0,
                    "cache_read_price_per_million": 3.0,
                    "cache_write_price_per_million": 4.0,
                }
            )
            cached_path = root / "cached.json"
            cached_path.write_text(json.dumps(cached), encoding="utf-8")
            cached_estimate = budget_estimate(RunConfig.load(cached_path))
            self.assertEqual(cached_estimate["maximum_configured_cost_usd"], 0.96)

            unbounded = json.loads(json.dumps(base))
            unbounded["model"]["input_price_per_million"] = None
            unbounded["model"]["output_price_per_million"] = None
            unbounded_path = root / "unbounded.json"
            unbounded_path.write_text(json.dumps(unbounded), encoding="utf-8")
            unbounded_estimate = budget_estimate(RunConfig.load(unbounded_path))
            self.assertIsNone(unbounded_estimate["maximum_configured_cost_usd"])
            self.assertIn(
                "COST_UNBOUNDED",
                {warning["code"] for warning in unbounded_estimate["warnings"]},
            )


if __name__ == "__main__":
    unittest.main()
