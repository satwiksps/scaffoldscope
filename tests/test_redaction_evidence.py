from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scaffoldscope.jsonutil import load_json, load_jsonl, write_jsonl
from scaffoldscope.report import check_experiment
from scaffoldscope.runner import run_experiment
from scaffoldscope.schema import RunConfig
from scaffoldscope.starter import create_starter_project


class RedactedEvidenceTests(unittest.TestCase):
    def test_secret_shaped_task_response_tool_and_evaluator_text_stays_verifiable(self) -> None:
        bearer = "Bearer abcdefghijklmnop"
        model_fixture = "sk-" + "abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temp:
            project = create_starter_project(Path(temp) / "study", name="redaction-check")
            task_path = project.root / "tasks.jsonl"
            task = load_jsonl(task_path)[0]
            task["problem"] += f" Keep this credential-shaped fixture private: {bearer}."
            task["constraints"][0]["id"] = "api-key"
            task["constraints"][0]["text"] = f"Preserve this fixture marker: {bearer}."
            task["script"][-1] = {"final": f"Preserved source marker {model_fixture}."}
            write_jsonl(task_path, [task])

            evaluator_path = project.root / "workspaces" / "text-cleaner" / "test_text_cleaner.py"
            evaluator_path.write_text(
                f'print("{bearer}")\n' + evaluator_path.read_text(encoding="utf-8"),
                encoding="utf-8",
                newline="\n",
            )

            summary = run_experiment(RunConfig.load(project.config_path))
            valid, issues = check_experiment(summary.experiment_dir)

            self.assertTrue(valid, issues)
            persisted = b"\n".join(
                path.read_bytes()
                for path in summary.experiment_dir.rglob("*")
                if path.is_file() and "workspace" not in path.parts
            )
            self.assertNotIn(bearer.encode(), persisted)
            self.assertNotIn(model_fixture.encode(), persisted)
            self.assertIn(b"[REDACTED]", persisted)

            manifest = load_json(summary.experiment_dir / "manifest.json")
            constraint = manifest["task_constraints"]["collapse-spaces"][0]
            self.assertTrue(constraint["redaction_applied"])
            events = load_jsonl(next((summary.experiment_dir / "trials").glob("*/events.jsonl")))
            requests = [event["payload"] for event in events if event["type"] == "model_request"]
            self.assertTrue(any(payload["redaction_applied"] for payload in requests))


if __name__ == "__main__":
    unittest.main()
