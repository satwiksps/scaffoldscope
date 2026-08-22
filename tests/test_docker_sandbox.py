from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scaffoldscope.docker_sandbox import DockerSandbox, DockerSandboxConfig, docker_preflight
from scaffoldscope.errors import ConfigError, SandboxError
from scaffoldscope.schema import SandboxConfig, TaskSpec

_IMAGE = "python@sha256:" + "a" * 64


class _Process:
    def __init__(self, output: bytes = b"ok\n", returncode: int = 0, *, timeout: bool = False):
        self.stdout = io.BytesIO(output)
        self.returncode: int | None = returncode
        self.pid = 12345
        self._timeout = timeout
        self._waits = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._waits += 1
        if self._timeout and self._waits == 1:
            raise subprocess.TimeoutExpired("docker", 1)
        return 0 if self.returncode is None else self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def terminate(self) -> None:
        self.returncode = -15


class DockerSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        protected = self.root / "tests"
        protected.mkdir()
        (protected / "test_hidden.py").write_text("assert True\n", encoding="utf-8")
        self.task = TaskSpec(
            id="docker-task",
            workspace=self.root,
            problem="Change VALUE.",
            constraints=(),
            test_command=("{python}", "-c", "import module; assert module.VALUE == 2"),
            protected_paths=("tests",),
        )
        self.config = DockerSandboxConfig(image=_IMAGE)
        self.sandbox = DockerSandbox(self.root, self.task, SandboxConfig(), self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_run_argv_has_hardened_defaults_and_literal_task_argv(self) -> None:
        (self.root / ".git").mkdir()
        argv = self.sandbox.docker_run_argv("scaffoldscope-test")
        image_index = argv.index(_IMAGE)

        self.assertEqual(argv[:2], ["docker", "run"])
        self.assertIn("--network=none", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--security-opt=no-new-privileges:true", argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--pull=never", argv)
        self.assertIn("--log-driver=none", argv)
        self.assertEqual(argv[argv.index("--user") + 1], "65532:65532")
        self.assertEqual(argv[argv.index("--memory") + 1], str(2 * 1024 * 1024 * 1024))
        self.assertEqual(argv[argv.index("--memory-swap") + 1], str(2 * 1024 * 1024 * 1024))
        self.assertEqual(argv[image_index + 1 :], ["python", "-c", self.task.test_command[2]])
        mount_values = [argv[index + 1] for index, item in enumerate(argv) if item == "--mount"]
        self.assertTrue(any("target=/workspace" in value for value in mount_values))
        self.assertTrue(any("target=/workspace/tests,readonly" in value for value in mount_values))
        self.assertTrue(any("target=/workspace/.git,readonly" in value for value in mount_values))

        resolved = DockerSandbox(
            self.root,
            self.task,
            SandboxConfig(),
            self.config,
            resolved_image="sha256:" + "b" * 64,
        ).docker_run_argv("scaffoldscope-resolved")
        self.assertIn("sha256:" + "b" * 64, resolved)
        self.assertNotIn(_IMAGE, resolved)

    @patch("scaffoldscope.docker_sandbox.subprocess.run")
    @patch("scaffoldscope.docker_sandbox.subprocess.Popen")
    def test_evaluate_uses_argv_without_shell_and_scrubs_provider_keys(
        self, popen: Mock, run: Mock
    ) -> None:
        popen.return_value = _Process()
        run.return_value = subprocess.CompletedProcess([], 0)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "DOCKER_HOST": "tcp://daemon"}):
            evaluation = self.sandbox.evaluate()

        self.assertTrue(evaluation.passed)
        kwargs = popen.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
        self.assertEqual(kwargs["env"]["DOCKER_HOST"], "tcp://daemon")

    @patch.object(DockerSandbox, "_terminate_process_tree")
    @patch("scaffoldscope.docker_sandbox.subprocess.run")
    @patch("scaffoldscope.docker_sandbox.subprocess.Popen")
    def test_timeout_kills_and_removes_container(
        self, popen: Mock, run: Mock, terminate: Mock
    ) -> None:
        process = _Process(output=b"still running\n", timeout=True)
        popen.return_value = process
        run.return_value = subprocess.CompletedProcess([], 0)
        sandbox = DockerSandbox(
            self.root,
            self.task,
            SandboxConfig(test_timeout_seconds=0.01),
            self.config,
        )

        evaluation = sandbox.evaluate()

        self.assertFalse(evaluation.passed)
        self.assertIsNone(evaluation.returncode)
        self.assertIn("timed out", evaluation.output)
        terminate.assert_called_once_with(process)
        control_commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(
            any(command[1:3] == ["kill", "--signal=KILL"] for command in control_commands)
        )
        self.assertTrue(any(command[1:3] == ["rm", "--force"] for command in control_commands))

    @patch("scaffoldscope.docker_sandbox.subprocess.run")
    @patch("scaffoldscope.docker_sandbox.subprocess.Popen")
    def test_output_is_bounded_and_docker_start_failures_are_infrastructure_errors(
        self, popen: Mock, run: Mock
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 0)
        popen.return_value = _Process(output=b"x" * 10_000)
        sandbox = DockerSandbox(
            self.root,
            self.task,
            SandboxConfig(max_process_output_chars=80),
            self.config,
        )
        evaluation = sandbox.evaluate()
        self.assertLessEqual(len(evaluation.output), 80)
        self.assertIn("truncated", evaluation.output)

        popen.return_value = _Process(output=b"image missing\n", returncode=125)
        with self.assertRaisesRegex(SandboxError, "exit 125"):
            sandbox.evaluate()

    def test_configuration_rejects_mutable_images_and_root_users(self) -> None:
        with self.assertRaisesRegex(ConfigError, "sha256"):
            DockerSandboxConfig(image="python:latest")
        with self.assertRaisesRegex(ConfigError, "non-root"):
            DockerSandboxConfig(image=_IMAGE, user="0:0")
        exploratory = DockerSandboxConfig(image="python:3.12-slim", require_image_digest=False)
        self.assertEqual(exploratory.image, "python:3.12-slim")
        self.assertEqual(self.config.platform, "linux/amd64")

    def test_invalid_name_and_unrepresentable_mount_are_rejected(self) -> None:
        with self.assertRaisesRegex(SandboxError, "container name"):
            self.sandbox.docker_run_argv("--dangerous")

        comma_root = self.root / "workspace,comma"
        comma_root.mkdir()
        with self.assertRaisesRegex(SandboxError, "containing ','"):
            DockerSandbox(comma_root, self.task, SandboxConfig(), self.config)

        nul_task = TaskSpec(
            id="nul-command",
            workspace=self.root,
            problem="Reject an invalid process argument.",
            constraints=(),
            test_command=("python", "bad\x00argument"),
        )
        nul_sandbox = DockerSandbox(self.root, nul_task, SandboxConfig(), self.config)
        with self.assertRaisesRegex(SandboxError, "NUL"):
            nul_sandbox.docker_run_argv("scaffoldscope-test")

    @patch("scaffoldscope.docker_sandbox.subprocess.run")
    def test_preflight_requires_the_local_image_before_model_work(self, run: Mock) -> None:
        image_id = "sha256:" + "b" * 64
        inspected = json.dumps([{"Id": image_id, "Os": "linux", "Architecture": "amd64"}])
        run.return_value = subprocess.CompletedProcess([], 0, inspected, "")
        result = docker_preflight(self.config)
        self.assertEqual(result["image_id"], image_id)
        self.assertEqual(result["image_platform"], "linux/amd64")
        self.assertIn("image", run.call_args.args[0])
        self.assertIn("inspect", run.call_args.args[0])

        run.return_value = subprocess.CompletedProcess([], 1, "", "No such image")
        with self.assertRaisesRegex(ConfigError, "never pulls"):
            docker_preflight(self.config)

        wrong_platform = json.dumps(
            [{"Id": image_id, "Os": "linux", "Architecture": "arm64", "Variant": "v8"}]
        )
        run.return_value = subprocess.CompletedProcess([], 0, wrong_platform, "")
        with self.assertRaisesRegex(ConfigError, "linux/arm64/v8"):
            docker_preflight(self.config)


if __name__ == "__main__":
    unittest.main()
