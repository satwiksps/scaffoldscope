from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scaffoldscope.cli import main
from scaffoldscope.jsonutil import load_jsonl
from scaffoldscope.locking import experiment_lock
from scaffoldscope.operations import trial_inventory
from scaffoldscope.report import write_report
from scaffoldscope.runner import run_experiment
from scaffoldscope.schema import RunConfig
from scaffoldscope.starter import create_starter_project

DEMO_CONFIG = (
    Path(__file__).resolve().parents[1] / "src" / "scaffoldscope" / "demo" / "experiment.json"
)


class CliTests(unittest.TestCase):
    def _completed_starter(self, root: Path) -> tuple[Path, Path]:
        project = create_starter_project(root / "starter", name="cli-modes")
        summary = run_experiment(RunConfig.load(project.config_path))
        write_report(summary.experiment_dir)
        return project.config_path, summary.experiment_dir

    def test_module_entry_point_reports_version_and_command_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            version = subprocess.run(
                [sys.executable, "-m", "scaffoldscope", "--version"],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            missing = subprocess.run(  # noqa: S603 - executable and arguments are test-owned
                [
                    sys.executable,
                    "-m",
                    "scaffoldscope",
                    "validate",
                    str(Path(temporary) / "missing.json"),
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn("scaffoldscope 1.0.0", version.stdout)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error:", missing.stderr)

    def test_module_entry_point_handles_paths_outside_stdout_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "study-\u03a9"
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "cp1252:strict"
            initialized = subprocess.run(  # noqa: S603 - executable and arguments are test-owned
                [sys.executable, "-m", "scaffoldscope", "init", str(project)],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            project_created = (project / "experiment.json").is_file()

        self.assertTrue(project_created)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.assertIn(r"study-\u03a9", initialized.stdout)

    def test_plan_is_dry_run_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_starter_project(Path(temporary) / "starter", name="plan-only")
            output = io.StringIO()
            with (
                patch("scaffoldscope.runner.CodingAgent.run") as agent_run,
                redirect_stdout(output),
            ):
                exit_code = main(["plan", str(project.config_path)])

            experiment = next((project.root / "runs").glob("plan-only-*"))
            self.assertEqual(exit_code, 0)
            self.assertIn("Planned 3 trials", output.getvalue())
            self.assertEqual(len(load_jsonl(experiment / "plan.jsonl")), 3)
            self.assertFalse((experiment / "trials").exists())
            agent_run.assert_not_called()

    def test_report_wires_sensitivity_flags_and_opens_generated_html(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _config, experiment = self._completed_starter(Path(temporary))
            output = io.StringIO()
            with (
                patch("scaffoldscope.cli.write_report", wraps=write_report) as report,
                patch("scaffoldscope.cli.webbrowser.open") as opened,
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "report",
                        str(experiment),
                        "--bootstrap-samples",
                        "100",
                        "--analysis-seed",
                        "23",
                        "--sesoi",
                        "0.2",
                        "--open",
                    ]
                )

            self.assertEqual(exit_code, 0)
            report.assert_called_once_with(
                experiment,
                bootstrap_samples=100,
                analysis_seed=23,
                sesoi=0.2,
            )
            opened.assert_called_once_with((experiment / "report.html").as_uri())
            self.assertIn("Wrote report", output.getvalue())

    def test_json_output_modes_and_schema_file_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, experiment = self._completed_starter(root)
            first_trial = load_jsonl(experiment / "episodes.jsonl")[0]["trial_id"]

            commands = (
                (["status", str(experiment), "--json"], "completed_trials"),
                (["budget", str(config), "--json"], "scheduled_trials"),
                (["replay", str(experiment), first_trial, "--json"], "timeline"),
                (["plugins", "--json"], None),
            )
            for command, expected_key in commands:
                with self.subTest(command=command[0]):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(command), 0)
                    payload = json.loads(output.getvalue())
                    if expected_key is None:
                        self.assertIsInstance(payload, list)
                        self.assertTrue(payload)
                    else:
                        self.assertIn(expected_key, payload)

            schema_path = root / "schema.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["schema", "--out", str(schema_path)]), 0)
            self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8"))["type"], "object")

    def test_trial_text_filters_and_strict_check_have_observable_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _config, experiment = self._completed_starter(Path(temporary))
            row = load_jsonl(experiment / "episodes.jsonl")[0]
            output = io.StringIO()
            with (
                patch("scaffoldscope.cli.trial_inventory", wraps=trial_inventory) as inventory,
                redirect_stdout(output),
            ):
                self.assertEqual(
                    main(
                        [
                            "trials",
                            str(experiment),
                            "--status",
                            row["status"],
                            "--variant",
                            row["variant_id"],
                            "--task",
                            row["task_id"],
                        ]
                    ),
                    0,
                )
            inventory.assert_called_once_with(
                experiment,
                status=row["status"],
                variant=row["variant_id"],
                task=row["task_id"],
            )
            self.assertIn(row["trial_id"], output.getvalue())
            self.assertIn("1 trial(s)", output.getvalue())

            strict = io.StringIO()
            with redirect_stdout(strict):
                self.assertEqual(main(["check", str(experiment), "--strict"]), 2)
            self.assertIn("report warning", strict.getvalue())

    def test_import_swebench_cli_wires_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, cache, output = root / "source.json", root / "repos", root / "tasks.jsonl"
            with (
                patch("scaffoldscope.cli.import_swebench_manifest", return_value=2) as imported,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "import-swebench",
                            str(source),
                            "--repo-cache",
                            str(cache),
                            "--out",
                            str(output),
                        ]
                    ),
                    0,
                )
            imported.assert_called_once_with(source, cache, output)

    def test_export_swebench_cli_wires_cell_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment, output = root / "experiment", root / "predictions.jsonl"
            with (
                patch("scaffoldscope.cli.export_swebench_predictions", return_value=3) as exported,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "export-swebench",
                            str(experiment),
                            "--out",
                            str(output),
                            "--strategy",
                            "selective",
                            "--replicate",
                            "17",
                        ]
                    ),
                    0,
                )
            exported.assert_called_once_with(experiment, output, strategy="selective", replicate=17)

    def test_export_swebench_matrix_cli_wires_dataset_and_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment, output = root / "experiment", root / "matrix"
            matrix = root / "matrix.json"
            matrix.write_text(json.dumps({"cells": [{}, {}]}), encoding="utf-8")
            with (
                patch("scaffoldscope.cli.export_swebench_matrix", return_value=matrix) as exported,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "export-swebench-matrix",
                            str(experiment),
                            "--out-dir",
                            str(output),
                            "--dataset-name",
                            "org/dataset",
                            "--split",
                            "test",
                        ]
                    ),
                    0,
                )
            exported.assert_called_once_with(
                experiment, output, dataset_name="org/dataset", split="test"
            )

    def test_ingest_swebench_cli_wires_evaluator_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment, results = root / "experiment", root / "results.json"
            experiment.mkdir()
            overlay = experiment / "external-evaluations" / "overlay.json"
            with (
                patch("scaffoldscope.cli.ingest_swebench_results", return_value=overlay) as ingest,
                patch("scaffoldscope.cli.write_report", return_value={"pair_coverage": 0.5}),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "ingest-swebench",
                            str(experiment),
                            str(results),
                            "--strategy",
                            "none",
                            "--replicate",
                            "17",
                            "--evaluator-version",
                            "commit",
                            "--evaluator-run-id",
                            "run-1",
                            "--image-set-digest",
                            "a" * 64,
                        ]
                    ),
                    0,
                )
            ingest.assert_called_once_with(
                experiment,
                results,
                strategy="none",
                replicate=17,
                evaluator_version="commit",
                evaluator_run_id="run-1",
                image_set_digest="a" * 64,
            )

    def test_run_refuses_to_publish_a_report_when_final_integrity_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = Path(temporary) / "experiment"
            summary = SimpleNamespace(
                experiment_dir=experiment,
                scheduled=1,
                completed=1,
                skipped=0,
                failed=0,
            )
            stderr = io.StringIO()
            with (
                patch("scaffoldscope.cli.run_experiment", return_value=summary),
                patch(
                    "scaffoldscope.cli.check_experiment",
                    return_value=(False, ["tampered result fixture"]),
                ) as check,
                patch(
                    "scaffoldscope.cli.write_report", return_value={"pair_coverage": 1.0}
                ) as write,
                redirect_stderr(stderr),
            ):
                exit_code = main(["run", str(DEMO_CONFIG)])

            self.assertEqual(exit_code, 2)
            check.assert_called_once_with(experiment)
            write.assert_not_called()
            self.assertIn("tampered result fixture", stderr.getvalue())

    def test_doctor_reports_literal_credential_status_without_config_values(self) -> None:
        environment_variable = "SCAFFOLDSCOPE_DOCTOR_TEST_KEY"
        sentinel_value = "doctor-secret-sentinel-must-not-appear"
        with tempfile.TemporaryDirectory() as temporary:
            raw = json.loads(DEMO_CONFIG.read_text(encoding="utf-8"))
            raw["tasks"]["manifest"] = str(DEMO_CONFIG.with_name("tasks.jsonl"))
            raw["experiment"]["output_dir"] = str(Path(temporary) / "runs")
            raw["model"].update(
                {
                    "provider": "openai_compatible",
                    "name": "doctor-test-model",
                    "base_url": "https://provider.invalid/v1",
                    "api_key_env": environment_variable,
                    "requires_api_key": True,
                }
            )
            config = Path(temporary) / "experiment.json"
            config.write_text(json.dumps(raw), encoding="utf-8")

            output = io.StringIO()
            with (
                patch.dict(os.environ, {environment_variable: sentinel_value}, clear=True),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["doctor", "--config", str(config)]), 0)

            missing_output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), redirect_stdout(missing_output):
                self.assertEqual(main(["doctor", "--config", str(config)]), 2)

            empty_output = io.StringIO()
            with (
                patch.dict(os.environ, {environment_variable: ""}, clear=True),
                redirect_stdout(empty_output),
            ):
                self.assertEqual(main(["doctor", "--config", str(config)]), 2)

        rendered = output.getvalue()
        payload = json.loads(rendered)
        self.assertEqual(payload["experiment"]["credential_status"], "configured")
        self.assertEqual(payload["experiment"]["provider_connectivity"], "not-checked")
        self.assertIs(payload["preflight_passed"], True)
        self.assertNotIn("ready", payload)
        self.assertNotIn(sentinel_value, rendered)
        self.assertNotIn(environment_variable, rendered)
        self.assertNotIn("api_key", rendered)

        missing_payload = json.loads(missing_output.getvalue())
        self.assertEqual(missing_payload["experiment"]["credential_status"], "missing")
        self.assertIs(missing_payload["preflight_passed"], False)
        self.assertNotIn(environment_variable, missing_output.getvalue())

        empty_payload = json.loads(empty_output.getvalue())
        self.assertEqual(empty_payload["experiment"]["credential_status"], "missing")
        self.assertIs(empty_payload["preflight_passed"], False)

    def test_version_doctor_validate_and_error_exit_codes(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            main(["--version"])
        self.assertEqual(exited.exception.code, 0)
        self.assertIn("scaffoldscope 1.0.0", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["doctor"]), 0)
        self.assertIn('"runtime_dependencies": "none"', output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["doctor", "--config", str(DEMO_CONFIG)]), 0)
        self.assertIn('"preflight_passed": true', output.getvalue())
        self.assertIn('"provider_connectivity": "not-applicable"', output.getvalue())
        self.assertIn('"credential_status": "not-required"', output.getvalue())
        self.assertIn('"sandbox_backend": "local"', output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["plugins", "--check"]), 0)
        self.assertIn("context_policy", output.getvalue())
        self.assertIn("openai_compatible", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["schema"]), 0)
        self.assertIn(
            '"$schema": "https://json-schema.org/draft/2020-12/schema"', output.getvalue()
        )

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["validate", str(DEMO_CONFIG)]), 0)
        self.assertIn("3 tasks x 1 replicates x 4 variants = 12 trials", output.getvalue())

        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(main(["validate", "missing-config.json"]), 2)
        self.assertIn("error:", errors.getvalue())

    def test_demo_report_check_and_clean_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            demo = Path(temporary) / "demo"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["demo", "--directory", str(demo)]), 0)
            experiment = next((demo / "runs").glob("offline-quickstart-*"))
            self.assertTrue((experiment / "report.html").is_file())

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["check", str(experiment)]), 0)

            commands = (
                ["report", str(experiment)],
                ["check", str(experiment)],
                ["clean", str(experiment), "--workspaces"],
                ["bundle", str(experiment), "--out", str(Path(temporary) / "locked.zip")],
                [
                    "ingest-swebench",
                    str(experiment),
                    str(Path(temporary) / "not-read-while-locked.json"),
                    "--strategy",
                    "none",
                    "--replicate",
                    "1729",
                    "--evaluator-version",
                    "test",
                    "--evaluator-run-id",
                    "test",
                    "--image-set-digest",
                    "sha256:" + "a" * 64,
                ],
            )
            with experiment_lock(experiment):
                for command in commands:
                    with self.subTest(command=command[0]), redirect_stderr(io.StringIO()):
                        self.assertEqual(main(command), 2)

    def test_clean_rejects_missing_experiment_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-experiment"
            errors = io.StringIO()
            with redirect_stderr(errors):
                exit_code = main(["clean", str(missing), "--workspaces"])

            self.assertEqual(exit_code, 2)
            self.assertIn("Not a ScaffoldScope experiment", errors.getvalue())
            self.assertFalse(missing.exists())

    def test_locked_operations_do_not_create_missing_experiment_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = (
                ("report", []),
                ("check", []),
                ("bundle", ["--out", str(root / "evidence.zip")]),
                (
                    "ingest-swebench",
                    [
                        str(root / "results.json"),
                        "--strategy",
                        "none",
                        "--replicate",
                        "1",
                        "--evaluator-version",
                        "v",
                        "--evaluator-run-id",
                        "r",
                        "--image-set-digest",
                        "0" * 64,
                    ],
                ),
            )
            for command, arguments in commands:
                missing = root / f"missing-{command}"
                with self.subTest(command=command), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([command, str(missing), *arguments]), 2)
                    self.assertFalse(missing.exists())

    def test_init_budget_status_and_evidence_bundle_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            starter = root / "starter"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["init", str(starter), "--name", "operator-study"]),
                    0,
                )
            self.assertIn("Initialized", output.getvalue())

            config = starter / "experiment.json"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["budget", str(config)]), 0)
            self.assertIn("3 trials", output.getvalue())

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["run", str(config)]), 0)
            experiment = next((starter / "runs").glob("operator-study-*"))

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["status", str(experiment)]), 0)
            self.assertIn("Progress: 3 / 3", output.getvalue())

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["trials", str(experiment), "--jsonl"]), 0)
            trial_rows = [line for line in output.getvalue().splitlines() if line]
            self.assertEqual(len(trial_rows), 3)
            first_trial = __import__("json").loads(trial_rows[0])["trial_id"]

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["replay", str(experiment), first_trial]), 0)
            self.assertIn("Offline replay", output.getvalue())
            self.assertIn("Trace SHA-256", output.getvalue())

            archive = root / "evidence.zip"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["bundle", str(experiment), "--out", str(archive)]),
                    0,
                )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["verify-bundle", str(archive)]), 0)
            self.assertIn("PASS", output.getvalue())

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["clean", str(experiment), "--workspaces"]),
                    0,
                )
            self.assertIn("generated workspace", output.getvalue())

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["check", str(experiment)]), 0)


if __name__ == "__main__":
    unittest.main()
