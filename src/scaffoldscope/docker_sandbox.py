"""Docker-backed evaluator execution without a Docker SDK dependency.

The structured file tools remain those of :class:`LocalSandbox`; only the
task-owned evaluator command runs in the container.  This makes the class a
drop-in sandbox for ``CodingAgent`` while preserving identical patch and
constraint semantics.

The defaults deliberately assume untrusted repository code: no network,
non-root execution, a read-only container root, no Linux capabilities, and
bounded CPU, memory, process, output, and wall-clock resources.  Docker is a
useful isolation layer, but its strength still depends on the host daemon and
runtime configuration.  Prefer a rootless daemon or a disposable worker VM.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast

from scaffoldscope.errors import ConfigError, SandboxError
from scaffoldscope.sandbox import EvaluationResult, LocalSandbox
from scaffoldscope.schema import SandboxConfig, TaskSpec

_DIGEST_IMAGE = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:|sha256:)[0-9a-fA-F]{64}\Z")
_CONTAINER_USER = re.compile(r"[1-9][0-9]*:[1-9][0-9]*\Z")
_SAFE_CONTAINER_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+\Z")
_MIN_MEMORY_BYTES = 16 * 1024 * 1024
_MIN_TMPFS_BYTES = 1024 * 1024


def _plain_argument(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or any(character in "\r\n" for character in value)
    ):
        raise ConfigError(f"{label} must be a non-empty, single-line string")
    return value


@dataclass(frozen=True)
class DockerSandboxConfig:
    """Settings that affect Docker isolation and experimental identity.

    Images are digest-pinned by default. Set ``require_image_digest=False``
    only for local exploration, never for a published experiment.
    """

    image: str
    docker_binary: str = "docker"
    user: str = "65532:65532"
    cpus: float = 2.0
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    pids_limit: int = 256
    tmpfs_bytes: int = 512 * 1024 * 1024
    nofile_limit: int = 1024
    cleanup_timeout_seconds: float = 10.0
    python_executable: str = "python"
    platform: str | None = "linux/amd64"
    require_image_digest: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.require_image_digest, bool):
            raise ConfigError("docker.require_image_digest must be a boolean")
        image = _plain_argument(self.image, "docker.image")
        if image.startswith("-") or any(character.isspace() for character in image):
            raise ConfigError("docker.image must be a single OCI image reference")
        if self.require_image_digest and _DIGEST_IMAGE.fullmatch(image) is None:
            raise ConfigError(
                "docker.image must be pinned by sha256 digest for reproducible execution"
            )
        _plain_argument(self.docker_binary, "docker.binary")
        user = _plain_argument(self.user, "docker.user")
        if _CONTAINER_USER.fullmatch(user) is None:
            raise ConfigError("docker.user must be a non-root numeric UID:GID")
        if isinstance(self.cpus, bool) or not isinstance(self.cpus, (int, float)):
            raise ConfigError("docker.cpus must be a finite positive number")
        if not math.isfinite(float(self.cpus)) or self.cpus <= 0:
            raise ConfigError("docker.cpus must be a finite positive number")
        for value, minimum, label in (
            (self.memory_bytes, _MIN_MEMORY_BYTES, "docker.memory_bytes"),
            (self.pids_limit, 1, "docker.pids_limit"),
            (self.tmpfs_bytes, _MIN_TMPFS_BYTES, "docker.tmpfs_bytes"),
            (self.nofile_limit, 16, "docker.nofile_limit"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ConfigError(f"{label} must be an integer >= {minimum}")
        if (
            isinstance(self.cleanup_timeout_seconds, bool)
            or not isinstance(self.cleanup_timeout_seconds, (int, float))
            or not math.isfinite(float(self.cleanup_timeout_seconds))
            or self.cleanup_timeout_seconds <= 0
        ):
            raise ConfigError("docker.cleanup_timeout_seconds must be a finite positive number")
        executable = _plain_argument(self.python_executable, "docker.python_executable")
        if executable.startswith("-") or any(character.isspace() for character in executable):
            raise ConfigError("docker.python_executable must be one executable name or path")
        if self.platform is not None:
            platform = _plain_argument(self.platform, "docker.platform")
            if platform.startswith("-") or any(character.isspace() for character in platform):
                raise ConfigError("docker.platform must be one Docker platform value")

    def to_dict(self) -> dict[str, Any]:
        """Return all result-affecting settings for config hashing and manifests."""

        return {
            "image": self.image,
            "docker_binary": self.docker_binary,
            "user": self.user,
            "cpus": float(self.cpus),
            "memory_bytes": self.memory_bytes,
            "pids_limit": self.pids_limit,
            "tmpfs_bytes": self.tmpfs_bytes,
            "nofile_limit": self.nofile_limit,
            "cleanup_timeout_seconds": float(self.cleanup_timeout_seconds),
            "python_executable": self.python_executable,
            "platform": self.platform,
            "require_image_digest": self.require_image_digest,
        }


class DockerSandbox(LocalSandbox):
    """Run task evaluation in one short-lived, hardened Docker container."""

    workspace_target = PurePosixPath("/workspace")

    def __init__(
        self,
        root: Path,
        task: TaskSpec,
        config: SandboxConfig,
        docker: DockerSandboxConfig,
        *,
        resolved_image: str | None = None,
    ) -> None:
        super().__init__(root, task, config)
        if not self.root.is_dir():
            raise SandboxError(f"Docker workspace is not a directory: {self.root}")
        if "," in str(self.root):
            raise SandboxError("Docker --mount cannot represent a workspace path containing ','")
        self.docker = docker
        if resolved_image is not None and _DIGEST_IMAGE.fullmatch(resolved_image) is None:
            raise SandboxError("resolved Docker image must be an immutable sha256 identity")
        self.resolved_image = resolved_image

    @staticmethod
    def _container_name() -> str:
        return f"scaffoldscope-{uuid.uuid4().hex}"

    @staticmethod
    def _docker_environment() -> dict[str, str]:
        """Pass only variables needed by the Docker CLI, never provider secrets."""

        allowed = (
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "HOME",
            "USERPROFILE",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "DOCKER_CERT_PATH",
            "DOCKER_TLS_VERIFY",
            "DOCKER_API_VERSION",
            "TEMP",
            "TMP",
        )
        return {key: os.environ[key] for key in allowed if key in os.environ}

    def _protected_mounts(self) -> list[str]:
        mounts: list[str] = []
        git_metadata = self.root / ".git"
        if git_metadata.exists():
            resolved_metadata = git_metadata.resolve()
            try:
                resolved_metadata.relative_to(self.root)
            except ValueError as exc:
                raise SandboxError("workspace Git metadata resolves outside the workspace") from exc
            mounts.extend(
                [
                    "--mount",
                    (
                        f"type=bind,source={resolved_metadata},"
                        f"target={self.workspace_target / '.git'},readonly"
                    ),
                ]
            )
        for raw in self.task.protected_paths:
            source = self._path(raw, must_exist=False)
            if not source.exists():
                continue
            relative = source.relative_to(self.root)
            target = self.workspace_target.joinpath(*relative.parts)
            source_text = str(source)
            if "," in source_text or "," in str(target):
                raise SandboxError("Docker --mount cannot represent protected paths containing ','")
            mounts.extend(
                [
                    "--mount",
                    f"type=bind,source={source_text},target={target},readonly",
                ]
            )
        return mounts

    def docker_run_argv(self, container_name: str) -> list[str]:
        """Build the exact Docker CLI argv used for evaluation.

        This method has no side effects and exists to make the execution contract
        inspectable in traces, tests, and future runner integration.
        """

        if _SAFE_CONTAINER_NAME.fullmatch(container_name) is None:
            raise SandboxError("generated Docker container name is invalid")
        command = [
            self.docker.python_executable if part == "{python}" else part
            for part in self.task.test_command
        ]
        if any("\x00" in part for part in command):
            raise SandboxError("test command arguments cannot contain NUL bytes")
        argv = [
            self.docker.docker_binary,
            "run",
            "--rm",
            "--name",
            container_name,
            "--pull=never",
            "--network=none",
            "--hostname",
            "scaffoldscope-evaluator",
            "--label",
            "org.scaffoldscope.role=evaluator",
            "--log-driver=none",
            "--user",
            self.docker.user,
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--read-only",
            "--init",
            "--cpus",
            str(float(self.docker.cpus)),
            "--memory",
            str(self.docker.memory_bytes),
            "--memory-swap",
            str(self.docker.memory_bytes),
            "--pids-limit",
            str(self.docker.pids_limit),
            "--ulimit",
            f"nofile={self.docker.nofile_limit}:{self.docker.nofile_limit}",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={self.docker.tmpfs_bytes}",  # noqa: S108
            "--workdir",
            str(self.workspace_target),
            "--env",
            "HOME=/tmp/scaffoldscope-home",
            "--env",
            "TMPDIR=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUTF8=1",
            "--env",
            "PYTHONHASHSEED=0",
            "--env",
            "PYTEST_ADDOPTS=-p no:cacheprovider",
            "--env",
            "CI=1",
            "--env",
            "NO_COLOR=1",
            "--env",
            "TZ=UTC",
            "--env",
            "LANG=C.UTF-8",
            "--env",
            "LC_ALL=C.UTF-8",
            "--mount",
            f"type=bind,source={self.root},target={self.workspace_target}",
        ]
        argv.extend(self._protected_mounts())
        if self.docker.platform is not None:
            argv.extend(["--platform", self.docker.platform])
        # Clearing the image entrypoint makes the task manifest's argv authoritative.
        argv.extend(["--entrypoint", "", self.resolved_image or self.docker.image, *command])
        return argv

    def _docker_control(self, *arguments: str) -> None:
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(  # noqa: S603
                [self.docker.docker_binary, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                env=self._docker_environment(),
                timeout=self.docker.cleanup_timeout_seconds,
                check=False,
            )

    def _stop_container(self, container_name: str) -> None:
        self._docker_control("kill", "--signal=KILL", container_name)
        self._docker_control("rm", "--force", "--volumes", container_name)

    def _finish_reader(
        self,
        process: subprocess.Popen[bytes],
        reader: threading.Thread,
        source: BinaryIO,
    ) -> None:
        try:
            process.wait(timeout=self.docker.cleanup_timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            try:
                process.wait(timeout=self.docker.cleanup_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                raise SandboxError("Docker CLI did not exit after container cleanup") from exc
        reader.join(timeout=self.docker.cleanup_timeout_seconds)
        if reader.is_alive():
            source.close()
            reader.join(timeout=2)
        if reader.is_alive():
            raise SandboxError("could not drain Docker evaluator output after cleanup")
        source.close()

    def _run_test_command(self) -> EvaluationResult:
        if not self.task.test_command:
            return EvaluationResult(None, None, "No test command configured.", 0.0, {}, {})

        container_name = self._container_name()
        argv = self.docker_run_argv(container_name)
        started = time.perf_counter()
        popen_options: dict[str, Any] = {}
        if sys.platform == "win32":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True

        byte_limit = self.config.max_process_output_chars * 4
        stream_truncated = [False]
        timed_out = False
        with tempfile.TemporaryFile(mode="w+b") as process_output:
            try:
                process = subprocess.Popen(  # noqa: S603
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    env=self._docker_environment(),
                    **popen_options,
                )
            except OSError as exc:
                raise SandboxError(
                    f"Could not start Docker CLI {self.docker.docker_binary!r}: {exc}"
                ) from exc
            source = cast(BinaryIO, process.stdout)
            reader = threading.Thread(
                target=self._drain_process_output,
                args=(source, process_output),
                kwargs={"byte_limit": byte_limit, "truncated": stream_truncated},
                name=f"scaffoldscope-docker-output-{process.pid}",
                daemon=True,
            )
            reader.start()
            try:
                process.wait(timeout=self.config.test_timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._stop_container(container_name)
                self._terminate_process_tree(process)
            except BaseException:
                self._stop_container(container_name)
                self._terminate_process_tree(process)
                raise
            finally:
                try:
                    self._finish_reader(process, reader, source)
                finally:
                    # ``--rm`` normally removes the container; this also covers a
                    # daemon/client race or an interrupted ``docker run``.
                    self._docker_control("rm", "--force", "--volumes", container_name)
            process_output.seek(0)
            raw_output = process_output.read(byte_limit)

        captured = self._bounded_process_output(
            raw_output,
            stream_truncated=stream_truncated[0],
        )
        duration = time.perf_counter() - started
        if timed_out:
            prefix = f"Test command timed out after {self.config.test_timeout_seconds}s.\n"
            bounded, _ = self._bounded(prefix + captured, self.config.max_process_output_chars)
            return EvaluationResult(False, None, bounded, duration, {}, {})
        if process.returncode in {125, 126, 127}:
            detail = captured.strip() or "Docker returned no diagnostic output."
            raise SandboxError(
                f"Docker could not start the evaluator (exit {process.returncode}): {detail}"
            )
        return EvaluationResult(
            process.returncode == 0,
            process.returncode,
            captured,
            duration,
            {},
            {},
        )


def docker_preflight(config: DockerSandboxConfig) -> dict[str, str]:
    """Resolve and validate the local image before any paid model call is made."""

    try:
        completed = subprocess.run(  # noqa: S603
            [
                config.docker_binary,
                "image",
                "inspect",
                config.image,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            shell=False,
            env=DockerSandbox._docker_environment(),
            timeout=config.cleanup_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError(f"Docker preflight could not inspect {config.image!r}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ConfigError(
            "Docker evaluator image is unavailable locally; ScaffoldScope never pulls "
            f"during a run ({config.image!r}): {detail or 'docker image inspect failed'}"
        )
    try:
        records = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ConfigError("Docker image inspect returned invalid JSON") from exc
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ConfigError("Docker image inspect returned an invalid provenance record")
    record = records[0]
    image_id = record.get("Id")
    image_os = record.get("Os")
    architecture = record.get("Architecture")
    variant = record.get("Variant", "")
    if (
        not isinstance(image_id, str)
        or not isinstance(image_os, str)
        or not isinstance(architecture, str)
        or not isinstance(variant, str)
    ):
        raise ConfigError("Docker image inspect returned an invalid provenance record")
    if _DIGEST_IMAGE.fullmatch(image_id) is None:
        raise ConfigError("Docker image inspect returned no immutable sha256 image ID")
    if not image_os or not architecture:
        raise ConfigError("Docker image inspect returned no OS/architecture provenance")
    image_platform = f"{image_os.lower()}/{architecture.lower()}"
    if variant and variant != "<no value>":
        image_platform += f"/{variant.lower()}"
    if config.platform is not None and image_platform != config.platform.lower():
        raise ConfigError(
            f"Docker image platform is {image_platform}, but sandbox.docker.platform is "
            f"{config.platform}"
        )
    return {
        "declared_image": config.image,
        "image_id": image_id,
        "configured_platform": config.platform or "default",
        "image_platform": image_platform,
    }
