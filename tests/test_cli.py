from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scaffoldscope.cli import main
from scaffoldscope.locking import experiment_lock

DEMO_CONFIG = (
    Path(__file__).resolve().parents[1] / "src" / "scaffoldscope" / "demo" / "experiment.json"
)


class CliTests(unittest.TestCase):
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
        self.assertNotIn(sentinel_value, rendered)
        self.assertNotIn(environment_variable, rendered)
        self.assertNotIn("api_key", rendered)

        missing_payload = json.loads(missing_output.getvalue())
        self.assertEqual(missing_payload["experiment"]["credential_status"], "missing")
        self.assertIs(missing_payload["ready"], False)
        self.assertNotIn(environment_variable, missing_output.getvalue())

        empty_payload = json.loads(empty_output.getvalue())
        self.assertEqual(empty_payload["experiment"]["credential_status"], "missing")
        self.assertIs(empty_payload["ready"], False)

    def test_version_doctor_validate_and_error_exit_codes(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            main(["--version"])
        self.assertEqual(exited.exception.code, 0)
        self.assertIn("scaffoldscope 0.3.1", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["doctor"]), 0)
        self.assertIn('"runtime_dependencies": "none"', output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["doctor", "--config", str(DEMO_CONFIG)]), 0)
        self.assertIn('"ready": true', output.getvalue())
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
