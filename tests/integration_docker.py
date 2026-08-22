from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.runner import run_experiment
from scaffoldscope.schema import RunConfig

DEMO_DIRECTORY = Path(__file__).resolve().parents[1] / "src" / "scaffoldscope" / "demo"


class DockerIntegrationTests(unittest.TestCase):
    def test_real_docker_backend_completes_one_trial(self) -> None:
        image = os.environ.get("SCAFFOLDSCOPE_DOCKER_IMAGE")
        user = os.environ.get("SCAFFOLDSCOPE_DOCKER_USER")
        if not image or not user:
            self.fail("SCAFFOLDSCOPE_DOCKER_IMAGE and SCAFFOLDSCOPE_DOCKER_USER are required")

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "demo"
            shutil.copytree(DEMO_DIRECTORY, project)
            config_path = project / "experiment.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["experiment"].update(
                {
                    "max_workers": 1,
                    "output_dir": "runs",
                    "primary_comparison": None,
                }
            )
            raw["tasks"]["ids"] = ["calculator-add"]
            raw["sandbox"].update(
                {
                    "backend": "docker",
                    "docker": {
                        "image": image,
                        "user": user,
                        "platform": "linux/amd64",
                        "cpus": 1,
                        "memory_bytes": 268435456,
                        "pids_limit": 128,
                        "tmpfs_bytes": 67108864,
                        "nofile_limit": 512,
                    },
                }
            )
            raw["variants"] = [raw["variants"][0]]
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            summary = run_experiment(RunConfig.load(config_path))

            self.assertEqual(summary.scheduled, 1)
            self.assertEqual(summary.completed, 1)
            self.assertEqual(summary.failed, 0)
            result_paths = list(summary.experiment_dir.glob("trials/*/result.json"))
            self.assertEqual(len(result_paths), 1)
            result = json.loads(result_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(result["sandbox_backend"], "docker")
            self.assertEqual(result["docker_image_id"], image)


if __name__ == "__main__":
    unittest.main()
