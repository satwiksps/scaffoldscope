from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scaffoldscope.agent import AgentOutcome, UsageLedger
from scaffoldscope.docker_sandbox import DockerSandboxConfig
from scaffoldscope.errors import ConfigError
from scaffoldscope.integrity import result_semantic_issues
from scaffoldscope.jsonutil import (
    atomic_write_json,
    content_hash,
    file_hash,
    load_json,
    load_jsonl,
    write_jsonl,
)
from scaffoldscope.locking import experiment_lock
from scaffoldscope.report import check_experiment, write_report
from scaffoldscope.runner import clean_workspaces, run_experiment
from scaffoldscope.sandbox import EvaluationResult, LocalSandbox
from scaffoldscope.schema import RunConfig, SandboxConfig, TaskSpec

DEMO = Path(__file__).resolve().parents[1] / "src" / "scaffoldscope" / "demo"


class RunnerIntegrationTests(unittest.TestCase):
    def test_secret_shaped_content_is_redacted_without_breaking_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            fixture_marker = "sk-" + "abcdefghijklmnop"
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            tasks_path = project / "tasks.jsonl"
            task = load_jsonl(tasks_path)[0]
            task["id"] = "messy-task-01"
            task["problem"] += f" Treat {fixture_marker} as inert fixture text."
            task["constraints"][0]["text"] = f"Never reveal fixture credential {fixture_marker}."
            task["script"][-1]["final"] = f"Completed without using {fixture_marker}."
            write_jsonl(tasks_path, [task])
            background = project / "fixtures" / "calculator" / "background.txt"
            background.write_text(
                background.read_text(encoding="utf-8") + f"\nfixture={fixture_marker}\n",
                encoding="utf-8",
            )

            config = RunConfig.load(config_path)
            first = run_experiment(config)
            self.assertEqual(first.completed, 1)
            valid, issues = check_experiment(first.experiment_dir)
            self.assertTrue(valid, issues)

            manifest = load_json(first.experiment_dir / "manifest.json")
            constraint = manifest["task_constraints"][task["id"]][0]
            self.assertTrue(constraint["redaction_applied"])
            self.assertNotIn(fixture_marker, constraint["text"])
            row = load_jsonl(first.experiment_dir / "episodes.jsonl")[0]
            trace_path = first.experiment_dir / row["artifacts"]["trace"]
            result_path = first.experiment_dir / row["artifacts"]["result"]
            for evidence_path in (
                first.experiment_dir / "manifest.json",
                first.experiment_dir / "config.resolved.json",
                first.experiment_dir / "episodes.jsonl",
                trace_path,
                result_path,
            ):
                self.assertNotIn(fixture_marker, evidence_path.read_text(encoding="utf-8"))
            requests = [
                event for event in load_jsonl(trace_path) if event["type"] == "model_request"
            ]
            self.assertTrue(any(event["payload"]["redaction_applied"] for event in requests))

            resumed = run_experiment(config)
            self.assertEqual(resumed.completed, 0)
            self.assertEqual(resumed.skipped, 1)

    def test_infrastructure_error_is_checkable_and_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)

            with patch(
                "scaffoldscope.runner.prepare_workspace",
                side_effect=OSError("fixture unavailable"),
            ) as prepare:
                first = run_experiment(config)
                self.assertEqual(first.completed, 1)
                row = load_jsonl(first.experiment_dir / "episodes.jsonl")[0]
                self.assertEqual(row["status"], "infrastructure_error")
                self.assertEqual(row["provider_seed_supported"], config.model.supports_seed)
                valid, issues = check_experiment(first.experiment_dir)
                self.assertTrue(valid, issues)

                resumed = run_experiment(config)
                self.assertEqual(resumed.completed, 0)
                self.assertEqual(resumed.skipped, 1)
                self.assertEqual(prepare.call_count, 1)

    def test_harness_error_is_excluded_from_evaluator_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            with patch(
                "scaffoldscope.runner.CodingAgent.run",
                side_effect=RuntimeError("harness fixture failed"),
            ):
                summary = run_experiment(RunConfig.load(config_path))

            row = load_jsonl(summary.experiment_dir / "episodes.jsonl")[0]
            self.assertEqual(summary.failed, 1)
            self.assertEqual(row["status"], "harness_error")
            self.assertFalse(row["infrastructure_valid"])
            self.assertFalse(row["evaluation_valid"])
            self.assertIsNone(row["solved"])
            self.assertIsNone(row["governed_solved"])
            valid, issues = check_experiment(summary.experiment_dir)
            self.assertTrue(valid, issues)

    def test_model_error_cannot_become_a_solved_trial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            outcome = AgentOutcome(
                "model_error",
                None,
                1,
                0,
                0,
                0,
                UsageLedger(),
                0,
                0,
                error="provider failed",
                model_trajectory_sha256=hashlib.sha256(b"").hexdigest(),
            )
            evaluation = EvaluationResult(True, 0, "passing fixture", 0.0, {}, {})

            with (
                patch("scaffoldscope.runner.CodingAgent.run", return_value=outcome),
                patch("scaffoldscope.runner.LocalSandbox.evaluate", return_value=evaluation),
            ):
                summary = run_experiment(RunConfig.load(config_path))

            row = load_jsonl(summary.experiment_dir / "episodes.jsonl")[0]
            self.assertEqual(row["status"], "model_error")
            self.assertFalse(row["solved"])
            self.assertEqual(result_semantic_issues(row), [])

    def test_resume_reexecutes_results_with_tampered_identity_or_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)
            first = run_experiment(config)
            self.assertEqual(first.completed, 1)
            result_path = next(first.experiment_dir.glob("trials/*/result.json"))

            mutations: tuple[tuple[str, object], ...] = (
                ("status", "tampered-status"),
                ("variant_tools", ["read_file"]),
                ("block_index", 999),
                ("order_position", 999),
            )
            for field, replacement in mutations:
                with self.subTest(field=field):
                    result = load_json(result_path)
                    result[field] = replacement
                    atomic_write_json(result_path, result)

                    resumed = run_experiment(config)
                    self.assertEqual(resumed.completed, 1)
                    self.assertEqual(resumed.skipped, 0)
                    repaired = load_json(result_path)
                    self.assertNotEqual(repaired[field], replacement)

            result = load_json(result_path)
            recorded_input_tokens = result["agent"]["usage"]["input_tokens"]
            result["agent"]["usage"]["input_tokens"] = recorded_input_tokens + 1
            atomic_write_json(result_path, result)
            resumed = run_experiment(config)
            self.assertEqual(resumed.completed, 1)
            self.assertEqual(resumed.skipped, 0)
            repaired = load_json(result_path)
            self.assertEqual(
                repaired["agent"]["usage"]["input_tokens"],
                recorded_input_tokens,
            )

            result = load_json(result_path)
            trace_path = first.experiment_dir / result["artifacts"]["trace"]
            events = load_jsonl(trace_path)
            events[-1]["payload"]["status"] = "tampered-terminal-status"
            write_jsonl(trace_path, events)
            result["artifact_hashes"]["trace_sha256"] = hashlib.sha256(
                trace_path.read_bytes()
            ).hexdigest()
            atomic_write_json(result_path, result)

            resumed = run_experiment(config)
            self.assertEqual(resumed.completed, 1)
            self.assertEqual(resumed.skipped, 0)
            repaired = load_json(result_path)
            repaired_events = load_jsonl(first.experiment_dir / repaired["artifacts"]["trace"])
            self.assertEqual(repaired_events[-1]["payload"]["status"], repaired["status"])

            result = load_json(result_path)
            trace_path = first.experiment_dir / result["artifacts"]["trace"]
            events = load_jsonl(trace_path)
            result["status"] = "fabricated"
            events[-1]["payload"]["status"] = "fabricated"
            write_jsonl(trace_path, events)
            result["artifact_hashes"]["trace_sha256"] = file_hash(trace_path)
            atomic_write_json(result_path, result)

            resumed = run_experiment(config)
            self.assertEqual(resumed.completed, 1)
            self.assertEqual(resumed.skipped, 0)
            self.assertNotEqual(load_json(result_path)["status"], "fabricated")

            for tamper in (
                "evaluation payload",
                "patch payload",
                "duplicate evaluation",
                "duplicate patch",
            ):
                with self.subTest(trace_lifecycle=tamper):
                    result = load_json(result_path)
                    trace_path = first.experiment_dir / result["artifacts"]["trace"]
                    events = load_jsonl(trace_path)
                    event_type = (
                        "evaluation_finished" if "evaluation" in tamper else "patch_captured"
                    )
                    lifecycle_event = next(
                        event for event in events if event.get("type") == event_type
                    )
                    if "payload" in tamper:
                        lifecycle_event["payload"] = {
                            **lifecycle_event["payload"],
                            "tampered": True,
                        }
                    else:
                        events.insert(
                            len(events) - 1,
                            json.loads(json.dumps(lifecycle_event)),
                        )
                        for sequence, event in enumerate(events, start=1):
                            event["sequence"] = sequence
                    write_jsonl(trace_path, events)
                    result["artifact_hashes"]["trace_sha256"] = hashlib.sha256(
                        trace_path.read_bytes()
                    ).hexdigest()
                    atomic_write_json(result_path, result)

                    resumed = run_experiment(config)
                    self.assertEqual(resumed.completed, 1)
                    self.assertEqual(resumed.skipped, 0)

            episodes_path = first.experiment_dir / "episodes.jsonl"
            episodes_path.unlink()
            resumed_without_aggregate = run_experiment(config)
            self.assertEqual(resumed_without_aggregate.completed, 0)
            self.assertEqual(resumed_without_aggregate.skipped, 1)

            write_jsonl(episodes_path, [])
            resumed_without_trial_row = run_experiment(config)
            self.assertEqual(resumed_without_trial_row.completed, 0)
            self.assertEqual(resumed_without_trial_row.skipped, 1)

    def test_resume_surfaces_unexpected_cache_loader_failures(self) -> None:
        cases = (
            ("scaffoldscope.runner.load_json", load_json, "result.json"),
            ("scaffoldscope.runner.load_jsonl", load_jsonl, "events.jsonl"),
        )
        for patch_target, real_loader, failing_name in cases:
            with self.subTest(loader=patch_target), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / "demo"
                shutil.copytree(DEMO, project)
                config_path = project / "experiment.json"
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
                raw["tasks"]["limit"] = 1
                raw["variants"] = [raw["variants"][0]]
                config_path.write_text(json.dumps(raw), encoding="utf-8")
                config = RunConfig.load(config_path)
                run_experiment(config)
                raised = False

                def fail_once(
                    path: Path,
                    target_name: str = failing_name,
                    loader: Callable[[Path], object] = real_loader,
                ) -> object:
                    nonlocal raised
                    if path.name == target_name and not raised:
                        raised = True
                        raise RuntimeError("cache loader fixture failed")
                    return loader(path)

                with (
                    patch(patch_target, side_effect=fail_once),
                    self.assertRaisesRegex(RuntimeError, "cache loader fixture failed"),
                ):
                    run_experiment(config)

    def test_integrity_profile_requires_complete_manifest_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            experiment = run_experiment(RunConfig.load(config_path)).experiment_dir
            manifest_path = experiment / "manifest.json"
            manifest = load_json(manifest_path)

            for field in (
                "experiment",
                "scaffoldscope_version",
                "created_at",
                "code_commit",
                "python",
                "platform",
                "token_counter",
                "model_provider",
                "model_name",
                "provider_seed_supported",
                "sandbox_backend",
                "docker",
                "plugins",
                "implementation_hash",
                "task_source_hashes",
                "task_provenance",
                "task_constraints",
                "task_toolsets",
            ):
                with self.subTest(field=field):
                    tampered = dict(manifest)
                    tampered.pop(field)
                    atomic_write_json(manifest_path, tampered)
                    valid, issues = check_experiment(experiment)
                    self.assertFalse(valid)
                    self.assertTrue(
                        any("missing v1-profile fields" in issue for issue in issues),
                        issues,
                    )
            tampered = dict(manifest)
            tampered["docker"] = {"image": "tampered"}
            atomic_write_json(manifest_path, tampered)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertIn("resolved docker_config does not match manifest", issues)
            tampered = dict(manifest)
            tampered["token_counter"] = str(manifest["token_counter"]) + "-tampered"
            atomic_write_json(manifest_path, tampered)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertIn("manifest token_counter does not match runtime identity", issues)
            atomic_write_json(manifest_path, manifest)

    def test_plan_defers_runtime_pin_until_first_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)

            def runtime(system: str) -> dict[str, str]:
                value = {
                    "python_implementation": "CPython",
                    "python_version": "3.14.0",
                    "operating_system": system,
                    "machine": "x86_64",
                    "token_counter": "char4-v1",
                }
                return {**value, "hash": content_hash(value)}

            with patch("scaffoldscope.runner._runtime_identity", return_value=runtime("Planner")):
                planned = run_experiment(config, dry_run=True)
            manifest = load_json(planned.experiment_dir / "manifest.json")
            self.assertEqual(manifest["integrity_version"], 1)
            self.assertEqual(manifest["variant_order_algorithm"], "sha256-rank-v1")
            self.assertEqual(manifest["task_toolsets"], config.task_toolsets)
            self.assertEqual(manifest["task_constraints"], config.task_constraints)
            self.assertIsNone(manifest["runtime_identity"])

            manifest_path = planned.experiment_dir / "manifest.json"
            tampered_manifest = dict(manifest)
            tampered_manifest["model_provider"] = "tampered-provider"
            atomic_write_json(manifest_path, tampered_manifest)
            with (
                patch("scaffoldscope.runner._runtime_identity", return_value=runtime("Worker")),
                self.assertRaisesRegex(ConfigError, "Existing experiment manifest differs"),
            ):
                run_experiment(config)
            atomic_write_json(manifest_path, manifest)

            with patch("scaffoldscope.runner._runtime_identity", return_value=runtime("Worker")):
                completed = run_experiment(config)
            self.assertEqual(completed.completed, 1)
            manifest = load_json(completed.experiment_dir / "manifest.json")
            self.assertEqual(manifest["runtime_identity"]["operating_system"], "Worker")
            valid, issues = check_experiment(completed.experiment_dir)
            self.assertTrue(valid, issues)

    def test_resume_rejects_host_runtime_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)

            def runtime(system: str) -> dict[str, str]:
                value = {
                    "python_implementation": "CPython",
                    "python_version": "3.14.0",
                    "operating_system": system,
                    "machine": "x86_64",
                    "token_counter": "char4-v1",
                }
                return {**value, "hash": content_hash(value)}

            with patch("scaffoldscope.runner._runtime_identity", return_value=runtime("SystemA")):
                completed = run_experiment(config)
            self.assertEqual(completed.completed, 1)
            result = load_jsonl(completed.experiment_dir / "episodes.jsonl")[0]
            self.assertEqual(result["runtime_identity"]["operating_system"], "SystemA")

            with (
                patch(
                    "scaffoldscope.runner._runtime_identity",
                    return_value=runtime("SystemB"),
                ),
                self.assertRaisesRegex(ConfigError, "Runtime identity differs"),
            ):
                run_experiment(config)

    def test_v02_manifest_without_integrity_profile_remains_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            completed = run_experiment(RunConfig.load(config_path))
            experiment = completed.experiment_dir

            manifest = load_json(experiment / "manifest.json")
            manifest["scaffoldscope_version"] = "0.2.0"
            manifest.pop("integrity_version")
            manifest.pop("resolved_config_hash")
            manifest.pop("runtime_identity")
            manifest.pop("variant_order_algorithm")
            manifest.pop("task_toolsets")
            manifest.pop("task_provenance")
            manifest.pop("task_constraints")
            manifest.pop("provider_seed_supported")
            atomic_write_json(experiment / "manifest.json", manifest)
            rows = load_jsonl(experiment / "episodes.jsonl")
            for row in rows:
                row["scaffoldscope_version"] = "0.2.0"
                row.pop("runtime_identity")
                row.pop("provider_seed_supported")
                atomic_write_json(experiment / row["artifacts"]["result"], row)
            write_jsonl(experiment / "episodes.jsonl", rows)

            valid, issues = check_experiment(experiment)
            self.assertTrue(valid, issues)

    def test_integrity_marker_keeps_exact_plan_and_result_checks_forward_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = raw["variants"][:2]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            completed = run_experiment(RunConfig.load(config_path))
            experiment = completed.experiment_dir

            resolved_path = experiment / "config.resolved.json"
            resolved = load_json(resolved_path)
            tampered_resolved = dict(resolved)
            tampered_resolved["schema_version"] = 999
            atomic_write_json(resolved_path, tampered_resolved)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertIn("resolved config content hash mismatch", issues)
            atomic_write_json(resolved_path, resolved)

            manifest = load_json(experiment / "manifest.json")
            original_treatments = manifest["variant_treatments"]
            treatment_mutations = {
                "policy": {"context_policy": "tampered-policy"},
                "tools": {"tools": ["read_file"]},
                "instructions": {"instructions_sha256": "tampered-instructions"},
                "plugin options": {"plugin_options": {"tampered": True}},
            }
            for label, mutation in treatment_mutations.items():
                with self.subTest(treatment_field=label):
                    manifest["variant_treatments"] = json.loads(json.dumps(original_treatments))
                    first_variant = next(iter(manifest["variant_treatments"].values()))
                    first_variant.update(mutation)
                    atomic_write_json(experiment / "manifest.json", manifest)
                    valid, issues = check_experiment(experiment)
                    self.assertFalse(valid)
                    self.assertIn(
                        "manifest variant_treatments do not match resolved config",
                        issues,
                    )
            manifest["variant_treatments"] = original_treatments
            manifest.pop("integrity_version")
            atomic_write_json(experiment / "manifest.json", manifest)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertIn(
                "manifest is missing integrity_version for v1-profile evidence",
                issues,
            )
            manifest["integrity_version"] = 1
            manifest["scaffoldscope_version"] = "999.0.0"
            atomic_write_json(experiment / "manifest.json", manifest)
            rows = load_jsonl(experiment / "episodes.jsonl")
            for row in rows:
                row["scaffoldscope_version"] = "999.0.0"
                atomic_write_json(experiment / row["artifacts"]["result"], row)
            write_jsonl(experiment / "episodes.jsonl", rows)
            valid, issues = check_experiment(experiment)
            self.assertTrue(valid, issues)

            pinned_runtime = manifest["runtime_identity"]
            manifest["runtime_identity"] = None
            atomic_write_json(experiment / "manifest.json", manifest)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertIn("manifest is missing runtime identity", issues)
            manifest["runtime_identity"] = pinned_runtime
            atomic_write_json(experiment / "manifest.json", manifest)

            plan_path = experiment / "plan.jsonl"
            plan = load_jsonl(plan_path)
            write_jsonl(plan_path, list(reversed(plan)))
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertIn(
                "plan.jsonl does not match the deterministic manifest grid/order",
                issues,
            )

            write_jsonl(plan_path, plan)
            rows[0]["order_position"] = 99
            atomic_write_json(experiment / rows[0]["artifacts"]["result"], rows[0])
            write_jsonl(experiment / "episodes.jsonl", rows)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("result order_position does not match plan" in issue for issue in issues),
                issues,
            )

    def test_integrity_profile_binds_pricing_tools_and_lifecycle_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            raw["model"]["input_price_per_million"] = 0
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            experiment = run_experiment(RunConfig.load(config_path)).experiment_dir

            original_row = load_jsonl(experiment / "episodes.jsonl")[0]
            trace_path = experiment / original_row["artifacts"]["trace"]
            result_path = experiment / original_row["artifacts"]["result"]
            patch_path = experiment / original_row["artifacts"]["patch"]
            original_trace = load_jsonl(trace_path)

            def clone(value: object) -> object:
                return json.loads(json.dumps(value))

            def install_evidence(row_value: object, trace_value: object) -> None:
                assert isinstance(row_value, dict)
                assert isinstance(trace_value, list)
                write_jsonl(trace_path, trace_value)
                row_value["artifact_hashes"]["trace_sha256"] = file_hash(trace_path)
                atomic_write_json(result_path, row_value)
                write_jsonl(experiment / "episodes.jsonl", [row_value])

            def synchronize_context_evidence(
                row_value: dict[str, object], trace_value: list[dict[str, object]]
            ) -> None:
                decisions = [
                    {key: value for key, value in event["payload"].items() if key != "turn"}
                    for event in trace_value
                    if event.get("type") == "context_prepared"
                ]
                agent = row_value["agent"]
                assert isinstance(agent, dict)
                agent["context_checks"] = decisions
                agent["compactions"] = [
                    decision for decision in decisions if decision.get("compaction_event") is True
                ]
                agent["compaction_count"] = len(agent["compactions"])
                availability = [
                    retained
                    for decision in decisions
                    if decision.get("history_compacted") is True
                    for retained in decision["lexical_constraint_availability"].values()
                ]
                agent["lexical_constraint_availability_rate"] = (
                    sum(availability) / len(availability) if availability else None
                )
                agent_event = next(
                    event for event in trace_value if event.get("type") == "agent_finished"
                )
                agent_event["payload"] = clone(agent)

            duplicated_trace = clone(original_trace)
            assert isinstance(duplicated_trace, list)
            for event_type in ("evaluation_finished", "patch_captured"):
                original_event = next(
                    event for event in duplicated_trace if event.get("type") == event_type
                )
                terminal_index = next(
                    index
                    for index, event in enumerate(duplicated_trace)
                    if event.get("type") == "trial_finished"
                )
                duplicated_trace.insert(terminal_index, clone(original_event))
            for sequence, event in enumerate(duplicated_trace, start=1):
                event["sequence"] = sequence
            duplicated_row = clone(original_row)
            install_evidence(duplicated_row, duplicated_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("exactly one evaluation_finished" in issue for issue in issues), issues
            )
            self.assertTrue(any("exactly one patch_captured" in issue for issue in issues), issues)

            mismatched_trace = clone(original_trace)
            assert isinstance(mismatched_trace, list)
            evaluation_event = next(
                event for event in mismatched_trace if event.get("type") == "evaluation_finished"
            )
            evaluation_event["payload"] = {**evaluation_event["payload"], "passed": "tampered"}
            patch_event = next(
                event for event in mismatched_trace if event.get("type") == "patch_captured"
            )
            patch_event["payload"] = {**patch_event["payload"], "patch_bytes": -1}
            mismatched_row = clone(original_row)
            install_evidence(mismatched_row, mismatched_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("evaluation_finished payload does not match" in issue for issue in issues)
            )
            self.assertTrue(
                any("patch_captured payload does not match" in issue for issue in issues)
            )

            tampered_tools_row = clone(original_row)
            assert isinstance(tampered_tools_row, dict)
            tampered_tools_row["variant_tools"] = ["read_file"]
            install_evidence(tampered_tools_row, original_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("variant tools mismatch" in issue for issue in issues), issues)

            missing_patch_row = clone(original_row)
            assert isinstance(missing_patch_row, dict)
            missing_patch_row["artifacts"].pop("patch")
            missing_patch_row["artifact_hashes"].pop("patch_sha256")
            install_evidence(missing_patch_row, original_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("exact artifact map" in issue for issue in issues), issues)

            fabricated_row = clone(original_row)
            fabricated_trace = clone(original_trace)
            assert isinstance(fabricated_row, dict)
            assert isinstance(fabricated_trace, list)
            fabricated_row["status"] = "fabricated"
            fabricated_trace[-1]["payload"]["status"] = "fabricated"
            install_evidence(fabricated_row, fabricated_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("supported persisted terminal status" in issue for issue in issues))

            contradictory_row = clone(original_row)
            contradictory_trace = clone(original_trace)
            assert isinstance(contradictory_row, dict)
            assert isinstance(contradictory_trace, list)
            contradictory_evaluation = contradictory_row["evaluation"]
            contradictory_agent = contradictory_row["agent"]
            contradictory_row["solved"] = not bool(
                contradictory_evaluation["passed"] and contradictory_agent["status"] == "completed"
            )
            install_evidence(contradictory_row, contradictory_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any(
                    "solved must agree with evaluation.passed and agent completion" in issue
                    for issue in issues
                ),
                issues,
            )

            agent_tampered_row = clone(original_row)
            assert isinstance(agent_tampered_row, dict)
            agent_tampered_row["agent"]["usage"]["total_tokens"] += 1
            install_evidence(agent_tampered_row, original_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("agent_finished payload does not match" in issue for issue in issues)
            )

            stripped_agent_row = clone(original_row)
            stripped_agent_trace = clone(original_trace)
            assert isinstance(stripped_agent_row, dict)
            assert isinstance(stripped_agent_trace, list)
            stripped_agent_row.pop("agent")
            stripped_agent_trace = [
                event for event in stripped_agent_trace if event.get("type") != "agent_finished"
            ]
            for sequence, event in enumerate(stripped_agent_trace, start=1):
                event["sequence"] = sequence
            install_evidence(stripped_agent_row, stripped_agent_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("agent must be an object" in issue for issue in issues), issues)

            minimal_solved_row = clone(original_row)
            minimal_solved_trace = clone(original_trace)
            assert isinstance(minimal_solved_row, dict)
            assert isinstance(minimal_solved_trace, list)
            minimal_solved_row.update(
                {
                    "status": "resolved",
                    "infrastructure_valid": True,
                    "evaluation_valid": True,
                    "solved": True,
                    "governed_solved": True,
                }
            )
            for field in ("agent", "evaluation", "patch_sha256", "patch_bytes"):
                minimal_solved_row.pop(field, None)
            minimal_solved_row["artifacts"] = {
                "trace": original_row["artifacts"]["trace"],
                "result": original_row["artifacts"]["result"],
            }
            minimal_solved_row["artifact_hashes"] = {
                "trace_sha256": original_row["artifact_hashes"]["trace_sha256"]
            }
            minimal_solved_trace = [minimal_solved_trace[0], minimal_solved_trace[-1]]
            minimal_solved_trace[-1]["sequence"] = 2
            minimal_solved_trace[-1]["payload"].update(
                {
                    "status": "resolved",
                    "solved": True,
                    "wall_seconds": minimal_solved_row["wall_seconds"],
                }
            )
            install_evidence(minimal_solved_row, minimal_solved_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("normal trial results require" in issue for issue in issues), issues
            )

            stripped_usage_row = clone(original_row)
            stripped_usage_trace = clone(original_trace)
            assert isinstance(stripped_usage_row, dict)
            assert isinstance(stripped_usage_trace, list)
            stripped_usage_row["agent"].pop("usage")
            agent_event = next(
                event for event in stripped_usage_trace if event.get("type") == "agent_finished"
            )
            agent_event["payload"].pop("usage")
            install_evidence(stripped_usage_row, stripped_usage_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("agent.usage" in issue for issue in issues), issues)

            stripped_evaluation_row = clone(original_row)
            stripped_evaluation_trace = clone(original_trace)
            assert isinstance(stripped_evaluation_row, dict)
            assert isinstance(stripped_evaluation_trace, list)
            stripped_evaluation_row["evaluation"] = {
                "passed": original_row["evaluation"]["passed"],
                "behavioral_adherence": original_row["evaluation"]["behavioral_adherence"],
            }
            evaluation_event = next(
                event
                for event in stripped_evaluation_trace
                if event.get("type") == "evaluation_finished"
            )
            evaluation_event["payload"] = clone(stripped_evaluation_row["evaluation"])
            install_evidence(stripped_evaluation_row, stripped_evaluation_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("evaluation has missing" in issue for issue in issues), issues)

            missing_response_row = clone(original_row)
            missing_response_trace = clone(original_trace)
            assert isinstance(missing_response_row, dict)
            assert isinstance(missing_response_trace, list)
            missing_response_trace = [
                event for event in missing_response_trace if event.get("type") != "model_response"
            ]
            for sequence, event in enumerate(missing_response_trace, start=1):
                event["sequence"] = sequence
            install_evidence(missing_response_row, missing_response_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("model_calls" in issue for issue in issues), issues)

            mismatched_context_row = clone(original_row)
            mismatched_context_trace = clone(original_trace)
            assert isinstance(mismatched_context_row, dict)
            assert isinstance(mismatched_context_trace, list)
            first_request = next(
                event for event in mismatched_context_trace if event.get("type") == "model_request"
            )
            first_request["payload"]["messages"] = []
            install_evidence(mismatched_context_row, mismatched_context_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("messages do not match" in issue for issue in issues), issues)

            fabricated_availability_row = clone(original_row)
            fabricated_availability_trace = clone(original_trace)
            assert isinstance(fabricated_availability_row, dict)
            assert isinstance(fabricated_availability_trace, list)
            availability_changed = False
            for event in fabricated_availability_trace:
                if event.get("type") not in {"context_prepared", "model_request"}:
                    continue
                availability = event["payload"]["lexical_constraint_availability"]
                self.assertTrue(availability)
                event["payload"]["lexical_constraint_availability"] = {
                    constraint_id: not retained for constraint_id, retained in availability.items()
                }
                availability_changed = True
            self.assertTrue(availability_changed)
            synchronize_context_evidence(fabricated_availability_row, fabricated_availability_trace)
            install_evidence(fabricated_availability_row, fabricated_availability_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any(
                    "lexical constraint availability does not match model_request" in issue
                    for issue in issues
                ),
                issues,
            )

            nonexistent_id_row = clone(original_row)
            nonexistent_id_trace = clone(original_trace)
            assert isinstance(nonexistent_id_row, dict)
            assert isinstance(nonexistent_id_trace, list)
            first_context = next(
                event for event in nonexistent_id_trace if event.get("type") == "context_prepared"
            )
            first_context["payload"]["kept_message_ids"].append("m99999")
            synchronize_context_evidence(nonexistent_id_row, nonexistent_id_trace)
            install_evidence(nonexistent_id_row, nonexistent_id_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any(
                    "kept_message_ids do not match the reconstructed trajectory" in issue
                    for issue in issues
                ),
                issues,
            )

            split_bundle_row = clone(original_row)
            split_bundle_trace = clone(original_trace)
            assert isinstance(split_bundle_row, dict)
            assert isinstance(split_bundle_trace, list)
            bundled_context = next(
                event
                for event in split_bundle_trace
                if event.get("type") == "context_prepared"
                and {"m00003", "m00004"}.issubset(event["payload"]["kept_message_ids"])
            )
            bundled_context["payload"]["kept_message_ids"].remove("m00003")
            bundled_context["payload"]["dropped_message_ids"] = sorted(
                [*bundled_context["payload"]["dropped_message_ids"], "m00003"]
            )
            bundled_context["payload"]["history_compacted"] = True
            synchronize_context_evidence(split_bundle_row, split_bundle_trace)
            install_evidence(split_bundle_row, split_bundle_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("splits an atomic assistant/tool message bundle" in issue for issue in issues),
                issues,
            )

            split_summary_row = clone(original_row)
            split_summary_trace = clone(original_trace)
            assert isinstance(split_summary_row, dict)
            assert isinstance(split_summary_trace, list)
            summary_context = next(
                event
                for event in split_summary_trace
                if event.get("type") == "context_prepared"
                and {"m00003", "m00004"}.issubset(event["payload"]["kept_message_ids"])
            )
            for message_id in ("m00003", "m00004"):
                summary_context["payload"]["kept_message_ids"].remove(message_id)
            summary_context["payload"]["dropped_message_ids"] = sorted(
                [
                    *summary_context["payload"]["dropped_message_ids"],
                    "m00003",
                    "m00004",
                ]
            )
            summary_context["payload"]["summary_source_ids"] = ["m00003"]
            summary_context["payload"]["history_compacted"] = True
            synchronize_context_evidence(split_summary_row, split_summary_trace)
            install_evidence(split_summary_row, split_summary_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("splits an atomic summary-source message bundle" in issue for issue in issues),
                issues,
            )

            self.assertEqual(original_row["agent"]["status"], "context_overflow")
            missing_overflow_row = clone(original_row)
            missing_overflow_trace = clone(original_trace)
            assert isinstance(missing_overflow_row, dict)
            assert isinstance(missing_overflow_trace, list)
            missing_overflow_trace = [
                event for event in missing_overflow_trace if event.get("type") != "context_overflow"
            ]
            for sequence, event in enumerate(missing_overflow_trace, start=1):
                event["sequence"] = sequence
            install_evidence(missing_overflow_row, missing_overflow_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("context_overflow event" in issue for issue in issues), issues)

            stripped_context_row = clone(original_row)
            stripped_context_trace = clone(original_trace)
            assert isinstance(stripped_context_row, dict)
            assert isinstance(stripped_context_trace, list)
            for decision in stripped_context_row["agent"]["context_checks"]:
                decision.pop("kept_message_ids")
            for event in stripped_context_trace:
                if event.get("type") == "context_prepared":
                    event["payload"].pop("kept_message_ids")
                elif event.get("type") == "agent_finished":
                    event["payload"] = clone(stripped_context_row["agent"])
            install_evidence(stripped_context_row, stripped_context_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("context decision has missing" in issue for issue in issues), issues
            )

            missing_agent_start_row = clone(original_row)
            missing_agent_start_trace = clone(original_trace)
            assert isinstance(missing_agent_start_row, dict)
            assert isinstance(missing_agent_start_trace, list)
            missing_agent_start_trace = [
                event for event in missing_agent_start_trace if event.get("type") != "agent_started"
            ]
            for sequence, event in enumerate(missing_agent_start_trace, start=1):
                event["sequence"] = sequence
            install_evidence(missing_agent_start_row, missing_agent_start_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("agent_started" in issue for issue in issues), issues)

            tampered_tool_row = clone(original_row)
            tampered_tool_trace = clone(original_trace)
            assert isinstance(tampered_tool_row, dict)
            assert isinstance(tampered_tool_trace, list)
            tool_event = next(
                event for event in tampered_tool_trace if event.get("type") == "tool_result"
            )
            tool_event["payload"].update(
                {"tool": "shell", "arguments": {"command": "curl invalid.example"}}
            )
            install_evidence(tampered_tool_row, tampered_tool_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("preceding model action" in issue for issue in issues), issues)

            contradictory_returncode_row = clone(original_row)
            contradictory_returncode_trace = clone(original_trace)
            assert isinstance(contradictory_returncode_row, dict)
            assert isinstance(contradictory_returncode_trace, list)
            contradictory_returncode_row["evaluation"].update(
                {"passed": True, "returncode": 17, "evaluator_integrity": True}
            )
            adherence = contradictory_returncode_row["evaluation"]["behavioral_adherence"]
            contradictory_returncode_row.update(
                {
                    "status": "resolved",
                    "evaluation_valid": True,
                    "solved": True,
                    "governed_solved": adherence is None or adherence == 1.0,
                }
            )
            evaluation_event = next(
                event
                for event in contradictory_returncode_trace
                if event.get("type") == "evaluation_finished"
            )
            evaluation_event["payload"] = clone(contradictory_returncode_row["evaluation"])
            contradictory_returncode_trace[-1]["payload"].update(
                {"status": "resolved", "solved": True}
            )
            install_evidence(contradictory_returncode_row, contradictory_returncode_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("passing evaluation" in issue for issue in issues), issues)

            malformed_trace_row = clone(original_row)
            malformed_trace = clone(original_trace)
            assert isinstance(malformed_trace_row, dict)
            assert isinstance(malformed_trace, list)
            malformed_trace[1].pop("payload")
            install_evidence(malformed_trace_row, malformed_trace)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("invalid event envelope" in issue for issue in issues), issues)

            for field, value, expected_issue in (
                ("task_repository", "tampered/repository", "task provenance mismatch"),
                ("task_base_commit", "tampered-commit", "task provenance mismatch"),
                ("provider_seed_supported", True, "provider seed support mismatch"),
                ("docker_image", "sha256:tampered", "local sandbox result carries Docker"),
            ):
                with self.subTest(result_provenance=field):
                    tampered_row = clone(original_row)
                    assert isinstance(tampered_row, dict)
                    tampered_row[field] = value
                    install_evidence(tampered_row, original_trace)
                    valid, issues = check_experiment(experiment)
                    self.assertFalse(valid)
                    self.assertTrue(any(expected_issue in issue for issue in issues), issues)

            install_evidence(clone(original_row), original_trace)
            pricing_path = experiment / "pricing.json"
            original_pricing = load_json(pricing_path)
            tampered_pricing = dict(original_pricing)
            tampered_pricing["input_price_per_million"] = 0.125
            pricing_identity = dict(tampered_pricing)
            pricing_identity.pop("hash")
            tampered_pricing["hash"] = content_hash(pricing_identity)
            atomic_write_json(pricing_path, tampered_pricing)
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertIn(
                "pricing snapshot does not match resolved model configuration",
                issues,
            )
            atomic_write_json(pricing_path, original_pricing)

            overlays = experiment / "external-evaluations"
            overlays.mkdir()
            overlay_path = overlays / "tampered.json"

            def write_overlay(strategy: str, replicate: int, task_ids: list[str]) -> None:
                overlay_identity = {
                    "schema_version": 1,
                    "kind": "swebench-evaluation-overlay",
                    "config_hash": original_row["config_hash"],
                    "strategy": strategy,
                    "replicate": replicate,
                    "evaluator_version": "test-evaluator-v1",
                    "evaluator_run_id": "test-run",
                    "image_set_digest": "sha256:" + "a" * 64,
                    "outcomes": {
                        task_id: {"completed": True, "resolved": False} for task_id in task_ids
                    },
                }
                atomic_write_json(
                    overlay_path,
                    {**overlay_identity, "overlay_hash": content_hash(overlay_identity)},
                )

            task_id = str(original_row["task_id"])
            variant_id = str(original_row["variant_id"])
            replicate = int(original_row["replicate"])
            write_overlay(variant_id, replicate, [task_id, "not-a-generated-task"])
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(
                any("does not match its generated cell" in issue for issue in issues), issues
            )
            write_overlay("not-a-treatment", replicate, [task_id])
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("undeclared strategy" in issue for issue in issues), issues)
            self.assertTrue(any("no declared treatment" in issue for issue in issues), issues)
            write_overlay(variant_id, replicate + 1, [task_id])
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertTrue(any("undeclared replicate" in issue for issue in issues), issues)
            overlay_path.unlink()
            overlays.rmdir()

            stray_directory = experiment / "trials" / "not-in-plan"
            stray_directory.mkdir()
            stray_file = experiment / "trials" / "also-not-in-plan"
            stray_file.write_text("stray\n", encoding="utf-8")
            valid, issues = check_experiment(experiment)
            self.assertFalse(valid)
            self.assertIn("unplanned active trial artifact: not-in-plan", issues)
            self.assertIn("unplanned active trial artifact: also-not-in-plan", issues)
            stray_directory.rmdir()
            stray_file.unlink()

            # A symlink with identical bytes used to pass because both the observed and
            # expected paths were resolved before their file types were checked.
            patch_target = patch_path.with_name("patch.real")
            patch_path.replace(patch_target)
            try:
                patch_path.symlink_to(patch_target.name)
            except OSError:
                patch_target.replace(patch_path)
            else:
                valid, issues = check_experiment(experiment)
                self.assertFalse(valid)
                self.assertTrue(any("artifact is a symlink" in issue for issue in issues), issues)
                patch_path.unlink()
                patch_target.replace(patch_path)

    def test_parallel_failure_waits_for_running_workers_before_unlocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"replicates": [11], "max_workers": 2})
            raw["tasks"]["limit"] = 2
            raw["variants"] = [raw["variants"][0]]
            raw["experiment"]["primary_comparison"] = None
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)
            running = threading.Event()
            release_worker = threading.Event()
            finished = threading.Event()
            shutdown_started = threading.Event()
            run_finished = threading.Event()
            run_errors: list[BaseException] = []

            def fail_or_finish(*args: object, **kwargs: object) -> list[object]:
                del kwargs
                trials = args[1]
                assert isinstance(trials, list)
                if trials[0].block_index == 0:
                    running.wait()
                    raise RuntimeError("injected block failure")
                running.set()
                release_worker.wait()
                finished.set()
                return []

            real_shutdown = ThreadPoolExecutor.shutdown

            def observed_shutdown(
                executor: ThreadPoolExecutor,
                wait: bool = True,
                *,
                cancel_futures: bool = False,
            ) -> None:
                if wait:
                    shutdown_started.set()
                real_shutdown(executor, wait=wait, cancel_futures=cancel_futures)

            def run_in_thread() -> None:
                try:
                    run_experiment(config)
                except BaseException as exc:
                    run_errors.append(exc)
                finally:
                    run_finished.set()

            controller = threading.Thread(target=run_in_thread)
            with (
                patch("scaffoldscope.runner._run_block", side_effect=fail_or_finish),
                patch(
                    "scaffoldscope.runner.ThreadPoolExecutor.shutdown",
                    new=observed_shutdown,
                ),
            ):
                controller.start()
                try:
                    self.assertTrue(running.wait(timeout=5), "parallel worker did not start")
                    self.assertTrue(
                        shutdown_started.wait(timeout=5),
                        "runner did not begin blocking executor shutdown",
                    )
                    self.assertFalse(finished.is_set())
                    self.assertFalse(run_finished.is_set())
                    with (
                        self.assertRaisesRegex(ConfigError, "already active"),
                        experiment_lock(config.experiment_dir),
                    ):
                        pass
                finally:
                    running.set()
                    release_worker.set()
                    run_finished.wait(timeout=5)
                    controller.join(timeout=5)

            self.assertFalse(controller.is_alive())
            self.assertEqual(len(run_errors), 1)
            self.assertIsInstance(run_errors[0], RuntimeError)
            self.assertEqual(str(run_errors[0]), "injected block failure")
            self.assertTrue(finished.is_set())
            with experiment_lock(config.experiment_dir):
                pass

    def test_offline_context_accounting_is_stable_across_fresh_runs(self) -> None:
        observed: list[dict[str, dict[str, object]]] = []
        with tempfile.TemporaryDirectory() as temporary:
            for run_number in range(2):
                project = Path(temporary) / f"demo-{run_number}"
                shutil.copytree(DEMO, project)
                config_path = project / "experiment.json"
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                raw["experiment"].update(
                    {
                        "replicates": [11],
                        "max_workers": 1,
                        "bootstrap_samples": 200,
                    }
                )
                raw["tasks"]["limit"] = 1
                config_path.write_text(json.dumps(raw), encoding="utf-8")

                completed = run_experiment(RunConfig.load(config_path))
                rows = load_jsonl(completed.experiment_dir / "episodes.jsonl")
                observed.append(
                    {
                        str(row["variant_id"]): {
                            "status": row["status"],
                            "solved": row["solved"],
                            "patch_sha256": row["patch_sha256"],
                            "usage": row["agent"]["usage"],
                            "peak_active_context_tokens": row["agent"][
                                "peak_active_context_tokens"
                            ],
                            "peak_canonical_context_tokens": row["agent"][
                                "peak_canonical_context_tokens"
                            ],
                            "compactions": row["agent"]["compactions"],
                            "context_checks": row["agent"]["context_checks"],
                            "model_trajectory_sha256": row["agent"]["model_trajectory_sha256"],
                        }
                        for row in rows
                    }
                )

        self.assertEqual(observed[0], observed[1])

    def test_openai_compatible_preflight_allows_explicit_no_auth_localhost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["model"].update(
                {
                    "provider": "openai_compatible",
                    "name": "local-test-model",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "requires_api_key": False,
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)

            with (
                patch.dict("scaffoldscope.runner.os.environ", {}, clear=True),
                patch("scaffoldscope.runner.build_plan", return_value=[]),
                patch("scaffoldscope.runner.prepare_experiment"),
            ):
                summary = run_experiment(config)
            self.assertEqual(summary.scheduled, 0)

            raw["model"].update(
                {
                    "base_url": "https://provider.invalid/v1",
                    "requires_api_key": True,
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            authenticated = RunConfig.load(config_path)
            with (
                patch.dict("scaffoldscope.runner.os.environ", {}, clear=True),
                self.assertRaisesRegex(ConfigError, "required to run"),
            ):
                run_experiment(authenticated)

    def test_clean_removes_active_and_archived_workspaces_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = Path(temporary) / "experiment"
            active = experiment / "trials" / "trial-a"
            archived = experiment / "aborted-attempts" / "trial-a--old"
            for attempt in (active, archived):
                (attempt / "workspace").mkdir(parents=True)
                (attempt / "workspace" / "source.py").write_text("x = 1\n", encoding="utf-8")
                (attempt / "test-home").mkdir()
                (attempt / "test-home" / "state").write_text("generated\n", encoding="utf-8")
                (attempt / "test-temp").mkdir()
                (attempt / "test-temp" / "state").write_text("generated\n", encoding="utf-8")
                (attempt / "events.jsonl").write_text("{}\n", encoding="utf-8")

            self.assertEqual(clean_workspaces(experiment), 2)
            self.assertFalse((active / "workspace").exists())
            self.assertFalse((archived / "workspace").exists())
            self.assertFalse((active / "test-home").exists())
            self.assertFalse((active / "test-temp").exists())
            self.assertFalse((archived / "test-home").exists())
            self.assertFalse((archived / "test-temp").exists())
            self.assertTrue((active / "events.jsonl").is_file())
            self.assertTrue((archived / "events.jsonl").is_file())

    def test_clean_ignores_symlinked_attempts_and_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = root / "experiment"
            trials = experiment / "trials"
            trials.mkdir(parents=True)

            external_attempt = root / "external-attempt"
            (external_attempt / "workspace").mkdir(parents=True)
            external_attempt_sentinel = external_attempt / "workspace" / "keep.txt"
            external_attempt_sentinel.write_text("keep\n", encoding="utf-8")
            linked_attempt = trials / "linked-attempt"

            regular_attempt = trials / "regular-attempt"
            regular_attempt.mkdir()
            external_workspace = root / "external-workspace"
            external_workspace.mkdir()
            external_workspace_sentinel = external_workspace / "keep.txt"
            external_workspace_sentinel.write_text("keep\n", encoding="utf-8")
            linked_workspace = regular_attempt / "workspace"
            try:
                linked_attempt.symlink_to(external_attempt, target_is_directory=True)
                linked_workspace.symlink_to(external_workspace, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            self.assertEqual(clean_workspaces(experiment), 0)
            self.assertTrue(external_attempt_sentinel.is_file())
            self.assertTrue(external_workspace_sentinel.is_file())
            self.assertTrue(linked_attempt.is_symlink())
            self.assertTrue(linked_workspace.is_symlink())

    def test_run_rejects_a_symlinked_trials_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update({"max_workers": 1, "primary_comparison": None})
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)
            planned = run_experiment(config, dry_run=True)

            external = Path(temporary) / "external-trials"
            external.mkdir()
            sentinel = external / "keep.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            trials_root = planned.experiment_dir / "trials"
            try:
                trials_root.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(ConfigError, "Trials root is not a regular directory"):
                run_experiment(config)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_offline_matrix_runs_resumes_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"]["replicates"] = [11]
            raw["experiment"]["bootstrap_samples"] = 200
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)
            first = run_experiment(config)
            self.assertEqual(first.scheduled, 12)
            self.assertEqual(first.completed, 12)
            second = run_experiment(config)
            self.assertEqual(second.skipped, 12)
            summary = write_report(first.experiment_dir)
            self.assertEqual(summary["recorded_trials"], 12)
            self.assertEqual(summary["pair_coverage"], 1.0)
            self.assertEqual(summary["strategies"]["none"]["solve_rate"], 0.0)
            self.assertEqual(summary["strategies"]["selective"]["solve_rate"], 1.0)
            self.assertEqual(
                summary["strategies"]["selective"]["solve_rate_interval"],
                [None, None],
            )
            for comparison in summary["comparisons"].values():
                self.assertFalse(comparison["inference_ready"])
                self.assertEqual(comparison["classification"], "descriptive_only")
                self.assertIsNone(comparison["empirical_mde"])
            self.assertIn("SCRIPTED_PROVIDER", {warning["code"] for warning in summary["warnings"]})
            valid, issues = check_experiment(first.experiment_dir)
            self.assertTrue(valid, issues)

            selected = next(
                row
                for row in load_jsonl(first.experiment_dir / "episodes.jsonl")
                if row["variant_id"] == "selective"
            )
            trace_events = load_jsonl(first.experiment_dir / selected["artifacts"]["trace"])
            self.assertIn("evaluation_finished", {event["type"] for event in trace_events})
            self.assertIn("patch_captured", {event["type"] for event in trace_events})
            patch_path = first.experiment_dir / selected["artifacts"]["patch"]
            patch_path.write_text(
                patch_path.read_text(encoding="utf-8") + "\ncorrupt\n",
                encoding="utf-8",
            )
            valid, issues = check_experiment(first.experiment_dir)
            self.assertFalse(valid)
            self.assertTrue(any("patch" in issue for issue in issues))

            repaired = run_experiment(config)
            self.assertEqual(repaired.completed, 1)
            self.assertEqual(repaired.skipped, 11)
            self.assertTrue((first.experiment_dir / "aborted-attempts").is_dir())
            valid, issues = check_experiment(first.experiment_dir)
            self.assertTrue(valid, issues)

    def test_docker_factory_uses_preflight_identity_and_records_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update(
                {
                    "max_workers": 1,
                    "primary_comparison": None,
                    "randomize_variant_order": False,
                }
            )
            raw["tasks"]["limit"] = 1
            raw["variants"] = [raw["variants"][0]]
            declared_image = "python@sha256:" + "a" * 64
            raw["sandbox"].update(
                {
                    "backend": "docker",
                    "docker": {"image": declared_image},
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = RunConfig.load(config_path)
            image_id = "sha256:" + "b" * 64
            observed = {
                "declared_image": declared_image,
                "image_id": image_id,
                "configured_platform": "linux/amd64",
                "image_platform": "linux/amd64",
            }

            def local_delegate(
                root: Path,
                task: TaskSpec,
                sandbox_config: SandboxConfig,
                docker_config: DockerSandboxConfig,
                *,
                resolved_image: str,
            ) -> LocalSandbox:
                del docker_config
                self.assertEqual(resolved_image, image_id)
                return LocalSandbox(root, task, sandbox_config)

            with (
                patch("scaffoldscope.runner.docker_preflight", return_value=observed) as preflight,
                patch("scaffoldscope.runner.DockerSandbox", side_effect=local_delegate) as factory,
            ):
                planned = run_experiment(config, dry_run=True)
                preflight.assert_not_called()
                manifest = load_json(planned.experiment_dir / "manifest.json")
                self.assertIsNone(manifest["docker_runtime"])

                completed = run_experiment(config)
                self.assertEqual(completed.completed, 1)
                self.assertEqual(preflight.call_count, 1)
                self.assertEqual(factory.call_count, 1)

                manifest = load_json(completed.experiment_dir / "manifest.json")
                self.assertEqual(manifest["docker_runtime"]["image_id"], image_id)
                self.assertEqual(len(manifest["docker_runtime"]["hash"]), 64)
                result = load_jsonl(completed.experiment_dir / "episodes.jsonl")[0]
                self.assertEqual(result["sandbox_backend"], "docker")
                self.assertEqual(result["docker_image"], declared_image)
                self.assertEqual(result["docker_image_id"], image_id)
                self.assertEqual(result["docker_image_platform"], "linux/amd64")
                valid, issues = check_experiment(completed.experiment_dir)
                self.assertTrue(valid, issues)

                resumed = run_experiment(config)
                self.assertEqual(resumed.skipped, 1)
                self.assertEqual(factory.call_count, 1)

                drifted = {**observed, "image_id": "sha256:" + "c" * 64}
                preflight.return_value = drifted
                with self.assertRaisesRegex(ConfigError, "runtime provenance differs"):
                    run_experiment(config)


if __name__ == "__main__":
    unittest.main()
