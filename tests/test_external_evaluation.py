from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import content_hash, load_json, load_jsonl
from scaffoldscope.report import check_experiment, write_report
from scaffoldscope.swebench import apply_external_evaluations, ingest_swebench_results


class ExternalEvaluationTests(unittest.TestCase):
    def _experiment(self, root: Path) -> Path:
        experiment = root / "external-study"
        experiment.mkdir()
        manifest = {
            "schema_version": 1,
            "experiment": "external-study",
            "config_hash": "test-config-hash",
            "model_provider": "openai_compatible",
            "model_name": "pinned-test-model",
            "tasks": ["task-a", "task-b"],
            "variants": ["none", "selective"],
            "replicates": [17],
            "trial_count": 4,
        }
        config = {
            "schema_version": 1,
            "experiment": {
                "baseline": "none",
                "primary_comparison": "selective",
                "bootstrap_samples": 200,
                "analysis_seed": 1234,
                "sesoi": 0.1,
            },
        }
        episodes: list[dict[str, Any]] = []
        plan: list[dict[str, Any]] = []
        for block_index, task_id in enumerate(("task-a", "task-b")):
            for order_position, variant in enumerate(("none", "selective")):
                row = self._pending_episode(task_id, variant)
                trial_hash = f"hash-{task_id}-{variant}-r17"
                row.update(
                    {
                        "trial_hash": trial_hash,
                        "block_index": block_index,
                        "order_position": order_position,
                    }
                )
                episodes.append(row)
                plan.append(
                    {
                        "schema_version": 1,
                        "trial_id": row["trial_id"],
                        "trial_hash": trial_hash,
                        "task_id": task_id,
                        "variant_id": variant,
                        "replicate": 17,
                        "block_index": block_index,
                        "order_position": order_position,
                    }
                )
        pricing = {
            "model": "pinned-test-model",
            "input_price_per_million": None,
            "output_price_per_million": None,
            "cache_read_price_per_million": None,
            "cache_write_price_per_million": None,
            "currency": "USD",
            "source": "test fixture",
        }
        (experiment / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (experiment / "config.resolved.json").write_text(json.dumps(config), encoding="utf-8")
        (experiment / "episodes.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in episodes), encoding="utf-8"
        )
        (experiment / "plan.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in plan), encoding="utf-8"
        )
        (experiment / "pricing.json").write_text(
            json.dumps({**pricing, "hash": content_hash(pricing)}), encoding="utf-8"
        )
        return experiment

    @staticmethod
    def _pending_episode(task_id: str, variant: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "trial_id": f"{task_id}--{variant}--r17",
            "task_id": task_id,
            "variant_id": variant,
            "replicate": 17,
            "model_name": "pinned-test-model",
            "infrastructure_valid": True,
            "evaluation_valid": False,
            "solved": None,
            "governed_solved": None,
            "status": "awaiting_external_evaluation",
            "wall_seconds": 1.0,
            "agent": {
                "compaction_count": int(variant == "selective"),
                "lexical_constraint_availability_rate": 0.75,
                "provider_models": ["pinned-test-model"],
                "provider_fingerprints": ["fp-test"],
                "usage": {
                    "input_tokens": 80 if variant == "none" else 65,
                    "output_tokens": 20 if variant == "none" else 15,
                    "total_tokens": 100 if variant == "none" else 80,
                    "cache_read_tokens": 10 if variant == "none" else 5,
                    "cache_write_tokens": 5,
                    "reasoning_tokens": 2 if variant == "none" else 1,
                    "cost_usd": 0.01,
                    "usage_sources": ["provider"],
                    "complete": True,
                },
            },
            "evaluation": {"behavioral_adherence": None},
        }

    def _ingest(
        self,
        root: Path,
        experiment: Path,
        *,
        strategy: str,
        resolved: list[str],
        unresolved: list[str],
        incomplete: list[str] | None = None,
    ) -> Path:
        results = root / f"{strategy}-results.json"
        results.write_text(
            json.dumps(
                {
                    "resolved_ids": resolved,
                    "unresolved_ids": unresolved,
                    "incomplete_ids": incomplete or [],
                }
            ),
            encoding="utf-8",
        )
        return ingest_swebench_results(
            experiment,
            results,
            strategy=strategy,
            replicate=17,
            evaluator_version="swebench-test-v1",
            evaluator_run_id=f"run-{strategy}-17",
            image_set_digest="sha256:" + "a" * 64,
        )

    def test_pending_external_labels_are_excluded_from_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            for strategy in ("none", "selective"):
                self._ingest(
                    root,
                    experiment,
                    strategy=strategy,
                    resolved=[],
                    unresolved=[],
                    incomplete=["task-a", "task-b"],
                )

            summary = write_report(experiment)

            with self.assertRaisesRegex(ConfigError, "sesoi"):
                write_report(experiment, sesoi=float("nan"))

            self.assertEqual(summary["schema_version"], 2)
            self.assertEqual(summary["recorded_trials"], 4)
            self.assertEqual(summary["valid_trials"], 0)
            self.assertEqual(summary["evaluation_pending_trials"], 4)
            self.assertEqual(summary["complete_paired_blocks"], 0)
            self.assertIsNone(summary["strategies"]["none"]["solve_rate"])
            self.assertIsNone(summary["strategies"]["selective"]["solve_rate"])
            self.assertEqual(summary["strategies"]["none"]["tokens"]["count"], 2)
            self.assertEqual(summary["strategies"]["none"]["tokens"]["total"], 200)
            self.assertEqual(summary["strategies"]["selective"]["tokens"]["total"], 160)
            none_ledger = summary["strategies"]["none"]["token_ledger"]
            selective_ledger = summary["strategies"]["selective"]["token_ledger"]
            self.assertEqual(none_ledger["input_tokens"]["total"], 160)
            self.assertEqual(none_ledger["uncached_input_tokens"]["total"], 130)
            self.assertEqual(none_ledger["output_tokens"]["total"], 40)
            self.assertEqual(none_ledger["reasoning_tokens"]["total"], 4)
            self.assertEqual(selective_ledger["uncached_input_tokens"]["total"], 110)
            self.assertEqual(summary["strategies"]["none"]["infrastructure_valid_attempts"], 2)
            self.assertEqual(summary["strategies"]["none"]["cost_usd"]["total"], 0.02)
            self.assertEqual(summary["strategies"]["selective"]["cost_usd"]["total"], 0.02)
            self.assertEqual(summary["strategies"]["none"]["compaction_exposure_rate"], 0.0)
            self.assertEqual(summary["strategies"]["selective"]["compaction_exposure_rate"], 1.0)
            self.assertEqual(
                summary["strategies"]["none"]["lexical_constraint_availability"]["count"],
                2,
            )
            self.assertEqual(
                summary["strategies"]["none"]["provider_models"], ["pinned-test-model"]
            )
            self.assertEqual(summary["strategies"]["none"]["provider_fingerprints"], ["fp-test"])
            self.assertEqual(summary["strategies"]["none"]["usage_sources"], ["provider"])
            resource_delta = summary["comparisons"]["selective"]["resource_deltas"]["tokens"]
            self.assertEqual(resource_delta["paired_task_count"], 2)
            self.assertEqual(resource_delta["mean_delta"], -20.0)
            self.assertEqual(
                summary["comparisons"]["selective"]["classification"],
                "descriptive_only",
            )
            self.assertIn(
                "PENDING_EXTERNAL_EVALUATION",
                {warning["code"] for warning in summary["warnings"]},
            )

    def test_comparison_does_not_require_an_unrelated_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            manifest = load_json(experiment / "manifest.json")
            manifest["variants"].append("periodic")
            manifest["trial_count"] = 6
            (experiment / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            periodic = self._pending_episode("task-a", "periodic")
            periodic.update(
                {
                    "evaluation_valid": True,
                    "solved": False,
                    "governed_solved": False,
                    "status": "unresolved",
                }
            )
            with (experiment / "episodes.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(periodic) + "\n")

            self._ingest(
                root,
                experiment,
                strategy="none",
                resolved=["task-b"],
                unresolved=["task-a"],
            )
            self._ingest(
                root,
                experiment,
                strategy="selective",
                resolved=["task-a", "task-b"],
                unresolved=[],
            )

            summary = write_report(experiment)

            self.assertEqual(summary["pair_coverage"], 0.5)
            self.assertEqual(summary["complete_paired_blocks"], 1)
            comparison = summary["comparisons"]["selective"]
            self.assertEqual(comparison["paired_block_count"], 2)
            self.assertEqual(comparison["paired_task_count"], 2)
            self.assertEqual(comparison["pair_coverage"], 1.0)
            self.assertEqual(comparison["delta_solve_rate"], 0.5)
            self.assertEqual(comparison["resource_deltas"]["tokens"]["paired_task_count"], 2)

    def test_effective_provider_identity_cannot_be_confounded_with_treatment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            rows = load_jsonl(experiment / "episodes.jsonl")
            for row in rows:
                if row["variant_id"] == "selective":
                    row["agent"]["provider_models"] = ["routed-model-b"]
                    row["agent"]["provider_fingerprints"] = ["fp-b"]
            (experiment / "episodes.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            summary = write_report(experiment)

            warnings = {warning["code"]: warning["message"] for warning in summary["warnings"]}
            self.assertIn("TREATMENT_PROVIDER_CONFOUND", warnings)
            self.assertIn(
                "Do not attribute the contrast to the harness alone",
                warnings["TREATMENT_PROVIDER_CONFOUND"],
            )
            comparison = summary["comparisons"]["selective"]
            self.assertEqual(comparison["classification"], "provider_confounded")
            self.assertFalse(comparison["inference_ready"])
            self.assertEqual(comparison["delta_interval"], [None, None])
            self.assertIsNone(comparison["sign_flip_pvalue"])

    def test_ingested_official_results_overlay_feed_the_paired_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            none_overlay = self._ingest(
                root,
                experiment,
                strategy="none",
                resolved=["task-b"],
                unresolved=["task-a"],
            )
            selective_overlay = self._ingest(
                root,
                experiment,
                strategy="selective",
                resolved=["task-a", "task-b"],
                unresolved=[],
            )

            self.assertTrue(none_overlay.is_file())
            self.assertTrue(selective_overlay.is_file())
            self.assertEqual(load_json(none_overlay)["evaluator_run_id"], "run-none-17")

            raw_rows = load_jsonl(experiment / "episodes.jsonl")
            self.assertTrue(all(row["solved"] is None for row in raw_rows))
            joined = apply_external_evaluations(experiment, raw_rows)
            self.assertTrue(all(row["evaluation_valid"] for row in joined))
            self.assertTrue(all("external_evaluation" in row for row in joined))

            summary = write_report(experiment)
            comparison = summary["comparisons"]["selective"]
            self.assertEqual(summary["valid_trials"], 4)
            self.assertEqual(summary["evaluation_pending_trials"], 0)
            self.assertEqual(summary["pair_coverage"], 1.0)
            self.assertEqual(summary["strategies"]["none"]["solve_rate"], 0.5)
            self.assertEqual(summary["strategies"]["selective"]["solve_rate"], 1.0)
            self.assertEqual(comparison["delta_solve_rate"], 0.5)
            self.assertEqual(
                (comparison["wins"], comparison["losses"], comparison["ties"]),
                (1, 0, 1),
            )
            self.assertFalse(comparison["inference_ready"])
            self.assertEqual(comparison["classification"], "descriptive_only")
            self.assertTrue((experiment / "summary.json").is_file())
            self.assertTrue((experiment / "report.md").is_file())

    def test_tampered_overlay_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            overlay_path = self._ingest(
                root,
                experiment,
                strategy="none",
                resolved=["task-a", "task-b"],
                unresolved=[],
            )
            overlay = load_json(overlay_path)
            overlay["outcomes"]["task-a"]["resolved"] = False
            overlay_path.write_text(json.dumps(overlay), encoding="utf-8")

            with self.assertRaises(ConfigError):
                apply_external_evaluations(experiment, load_jsonl(experiment / "episodes.jsonl"))

    def test_overlay_directory_cannot_escape_the_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            external = root / "external-overlays"
            external.mkdir()
            sentinel = external / "keep.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            overlay_directory = experiment / "external-evaluations"
            try:
                overlay_directory.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(ConfigError, "not a regular directory|escapes experiment"):
                apply_external_evaluations(
                    experiment,
                    load_jsonl(experiment / "episodes.jsonl"),
                )
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("external-evaluations directory resolves outside" in issue for issue in issues),
                issues,
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_official_empty_patch_bucket_counts_as_an_observed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            results = root / "official-report.json"
            results.write_text(
                json.dumps(
                    {
                        "resolved_ids": ["task-a"],
                        "unresolved_ids": [],
                        "empty_patch_ids": ["task-b"],
                        "error_ids": [],
                        "incomplete_ids": [],
                    }
                ),
                encoding="utf-8",
            )

            ingest_swebench_results(
                experiment,
                results,
                strategy="none",
                replicate=17,
                evaluator_version="swebench-test-v1",
                evaluator_run_id="run-none-17",
                image_set_digest="sha256:" + "a" * 64,
            )

            joined = apply_external_evaluations(
                experiment, load_jsonl(experiment / "episodes.jsonl")
            )
            none_rows = [row for row in joined if row["variant_id"] == "none"]
            self.assertEqual(
                {row["task_id"]: row["solved"] for row in none_rows},
                {"task-a": True, "task-b": False},
            )
            self.assertTrue(all(row["evaluation_valid"] for row in none_rows))


if __name__ == "__main__":
    unittest.main()
