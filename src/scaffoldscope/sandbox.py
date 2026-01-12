"""A bounded local workspace for trusted fixtures and pre-cloned repositories.

Path checks and fixed commands reduce accidents; they are not an OS security
boundary. Run untrusted benchmark repositories inside a container or VM.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from scaffoldscope.errors import SandboxError
from scaffoldscope.schema import BUILTIN_TOOL_NAMES, ConstraintSpec, SandboxConfig, TaskSpec

_IGNORED_WORKSPACE_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


@dataclass(frozen=True)
class ToolResult:
    tool: str
    ok: bool
    content: str
    metadata: dict[str, Any]
    duration_seconds: float

    def model_content(self) -> str:
        payload = {
            "ok": self.ok,
            "content": self.content,
            "metadata": self.metadata,
        }
        return (
            f'<tool_result name="{self.tool}">\n'
            + json.dumps(payload, ensure_ascii=False)
            + "\n</tool_result>"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "content": self.content,
            "metadata": self.metadata,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool | None
    returncode: int | None
    output: str
    duration_seconds: float
    constraint_checks: dict[str, bool]
    constraint_details: dict[str, str]
    evaluator_integrity: bool = True
    evaluator_integrity_details: dict[str, str] | None = None

    @property
    def behavioral_adherence(self) -> float | None:
        if not self.constraint_checks:
            return None
        return sum(self.constraint_checks.values()) / len(self.constraint_checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "returncode": self.returncode,
            "output": self.output,
            "duration_seconds": self.duration_seconds,
            "constraint_checks": self.constraint_checks,
            "constraint_details": self.constraint_details,
            "behavioral_adherence": self.behavioral_adherence,
            "evaluator_integrity": self.evaluator_integrity,
            "evaluator_integrity_details": self.evaluator_integrity_details or {},
        }


class WorkspaceSandbox(Protocol):
    """The minimal execution contract consumed by the agent and runner."""

    available_tools: tuple[str, ...]

    def invoke(self, tool: str, arguments: Any) -> ToolResult: ...

    def evaluate(self) -> EvaluationResult: ...

    def patch(self) -> str: ...


class RestrictedSandbox:
    """Expose an explicit treatment-specific subset of a sandbox's tool surface."""

    def __init__(self, delegate: WorkspaceSandbox, allowed_tools: tuple[str, ...]) -> None:
        self.delegate = delegate
        allowed = set(allowed_tools)
        self.available_tools = tuple(name for name in delegate.available_tools if name in allowed)

    def invoke(self, tool: str, arguments: Any) -> ToolResult:
        if tool not in self.available_tools:
            return ToolResult(
                tool=tool,
                ok=False,
                content=f"tool is not available in this treatment: {tool}",
                metadata={"error_type": "ToolUnavailable", "truncated": False},
                duration_seconds=0.0,
            )
        return self.delegate.invoke(tool, arguments)

    def evaluate(self) -> EvaluationResult:
        return self.delegate.evaluate()

    def patch(self) -> str:
        return self.delegate.patch()


def _safe_delete_workspace(path: Path, expected_parent: Path) -> None:
    resolved = path.resolve()
    parent = expected_parent.resolve()
    if resolved.parent != parent or resolved == parent:
        raise SandboxError(f"Refusing to delete unexpected workspace path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def prepare_workspace(task: TaskSpec, destination: Path, *, recreate: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not recreate:
            return
        _safe_delete_workspace(destination, destination.parent)
    source = task.workspace.resolve()
    resolved_destination = destination.resolve(strict=False)
    if source == resolved_destination or source in resolved_destination.parents:
        raise SandboxError("experiment output cannot be located inside a non-Git task workspace")
    if (source / ".git").exists():
        result = subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", "--local", str(source), str(destination)],
            capture_output=True,
            text=True,
            shell=False,
            timeout=180,
        )
        if result.returncode != 0:
            raise SandboxError(f"Could not clone task workspace: {result.stderr.strip()}")
        if task.base_commit:
            checkout = subprocess.run(
                ["git", "checkout", "--quiet", "--detach", task.base_commit],
                cwd=destination,
                capture_output=True,
                text=True,
                shell=False,
                timeout=120,
            )
            if checkout.returncode != 0:
                raise SandboxError(
                    f"Could not checkout {task.base_commit} for {task.id}: {checkout.stderr.strip()}"
                )
    else:
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"
            ),
        )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _patch_text(value: bytes, maximum: int) -> str | None:
    if len(value) > maximum or b"\x00" in value:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _unified_patch(
    old: str,
    new: str,
    *,
    fromfile: str,
    tofile: str,
) -> list[str]:
    """Render a unified text diff without losing missing-final-newline semantics."""

    rendered: list[str] = []
    for line in difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=fromfile,
        tofile=tofile,
    ):
        rendered.append(line)
        if line[:1] in {" ", "+", "-"} and not line.endswith(("\n", "\r")):
            rendered.append("\n\\ No newline at end of file\n")
    return rendered


class LocalSandbox:
    def __init__(self, root: Path, task: TaskSpec, config: SandboxConfig) -> None:
        self.root = root.resolve()
        self.task = task
        self.config = config
        self.available_tools = tuple(
            name for name in BUILTIN_TOOL_NAMES if name != "run_tests" or task.test_command
        )
        self._protected = tuple(Path(item) for item in task.protected_paths)
        self._initial: dict[str, tuple[str, str | None]] = {}
        for path in self._iter_files():
            relative = path.relative_to(self.root).as_posix()
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            text = _patch_text(raw, config.max_file_bytes)
            self._initial[relative] = (_sha256(raw), text)
        self._initial_links = self._symlink_snapshot()
        self._protected_snapshots = {
            item.as_posix(): self._protected_snapshot(item) for item in self._protected
        }

    def _protected_snapshot(self, relative_path: Path) -> tuple[str, dict[str, str]]:
        lexical_path = self.root / relative_path
        if lexical_path.is_symlink():
            relative = lexical_path.relative_to(self.root).as_posix()
            return "symlink", {relative: os.readlink(lexical_path)}
        path = self._path(relative_path.as_posix(), must_exist=False)
        if path.is_file():
            relative = path.relative_to(self.root).as_posix()
            return "file", {relative: _sha256(path.read_bytes())}
        if path.is_dir():
            files = {
                child.relative_to(self.root).as_posix(): _sha256(child.read_bytes())
                for child in self._iter_files()
                if path == child.parent or path in child.parents
            }
            files.update(
                {
                    f"@link:{relative}": target
                    for relative, target in self._symlink_snapshot().items()
                    if Path(relative).parent == relative_path
                    or relative_path in Path(relative).parents
                }
            )
            return "directory", files
        return "missing", {}

    def _symlink_snapshot(self) -> dict[str, str]:
        links: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_symlink():
                continue
            relative = path.relative_to(self.root)
            if any(part.lower() in _IGNORED_WORKSPACE_PARTS for part in relative.parts):
                continue
            links[relative.as_posix()] = os.readlink(path)
        return links

    def _iter_files(self) -> Iterable[Path]:
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative_parts = path.relative_to(self.root).parts
            if any(part.lower() in _IGNORED_WORKSPACE_PARTS for part in relative_parts):
                continue
            yield path

    def _path(self, raw: Any, *, must_exist: bool = True) -> Path:
        if not isinstance(raw, str) or not raw:
            raise SandboxError("path must be a non-empty string")
        supplied = Path(raw)
        if supplied.is_absolute():
            raise SandboxError("absolute paths are not allowed")
        if any(part.lower() == ".git" for part in supplied.parts):
            raise SandboxError("the Git metadata directory is not accessible to tools")
        candidate = (self.root / supplied).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise SandboxError("path escapes the workspace") from exc
        if must_exist and not candidate.exists():
            raise SandboxError(f"path does not exist: {raw}")
        return candidate

    def _bounded(self, value: str, limit: int | None = None) -> tuple[str, bool]:
        maximum = limit or self.config.max_observation_chars
        if len(value) <= maximum:
            return value, False
        marker = f"\n...[observation truncated at {maximum} characters]"
        return value[: max(0, maximum - len(marker))] + marker, True

    def _assert_writable(self, path: Path) -> None:
        relative = path.relative_to(self.root)
        for protected in self._protected:
            if relative == protected or protected in relative.parents:
                raise SandboxError(f"path is evaluator-protected: {relative.as_posix()}")

    def invoke(self, tool: str, arguments: Any) -> ToolResult:
        started = time.perf_counter()
        try:
            if not isinstance(arguments, dict):
                raise SandboxError("tool arguments must be a JSON object")
            handler = getattr(self, f"_tool_{tool}", None)
            if handler is None or tool.startswith("_"):
                raise SandboxError(f"unknown tool: {tool}")
            content, metadata = handler(arguments)
            bounded, truncated = self._bounded(content)
            metadata = {**metadata, "truncated": truncated}
            return ToolResult(tool, True, bounded, metadata, time.perf_counter() - started)
        except (SandboxError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
            return ToolResult(
                tool,
                False,
                str(exc),
                {"error_type": type(exc).__name__, "truncated": False},
                time.perf_counter() - started,
            )

    def _tool_list_files(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        directory = self._path(arguments.get("path", "."))
        if not directory.is_dir():
            raise SandboxError("list_files path must be a directory")
        raw_limit = arguments.get("limit", 200)
        if (
            isinstance(raw_limit, bool)
            or not isinstance(raw_limit, int)
            or raw_limit < 1
            or raw_limit > 1000
        ):
            raise SandboxError("limit must be an integer from 1 to 1000")
        files: list[str] = []
        for path in sorted(directory.rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and all(part.lower() != ".git" for part in path.relative_to(self.root).parts)
            ):
                files.append(path.relative_to(self.root).as_posix())
                if len(files) >= raw_limit:
                    break
        return "\n".join(files), {"count": len(files), "limit": raw_limit}

    def _tool_read_file(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path = self._path(arguments.get("path"))
        if not path.is_file():
            raise SandboxError("read_file path must be a file")
        if path.stat().st_size > self.config.max_file_bytes:
            raise SandboxError(
                f"file exceeds sandbox.max_file_bytes ({self.config.max_file_bytes})"
            )
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = arguments.get("start_line", 1)
        end = arguments.get("end_line", len(lines))
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 1
            or end < start
        ):
            raise SandboxError("start_line/end_line must be valid 1-based integers")
        selected = lines[start - 1 : end]
        numbered = "\n".join(
            f"{line_number:>5} | {line}" for line_number, line in enumerate(selected, start=start)
        )
        return numbered, {
            "path": path.relative_to(self.root).as_posix(),
            "start_line": start,
            "end_line": min(end, len(lines)),
            "total_lines": len(lines),
        }

    def _tool_search(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise SandboxError("query must be a non-empty string")
        directory = self._path(arguments.get("path", "."))
        if not directory.is_dir():
            raise SandboxError("search path must be a directory")
        case_sensitive = arguments.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise SandboxError("case_sensitive must be a boolean")
        raw_limit = arguments.get("max_results", 100)
        if (
            isinstance(raw_limit, bool)
            or not isinstance(raw_limit, int)
            or raw_limit < 1
            or raw_limit > 1000
        ):
            raise SandboxError("max_results must be an integer from 1 to 1000")
        needle = query if case_sensitive else query.lower()
        matches: list[str] = []
        for path in sorted(directory.rglob("*")):
            if (
                path.is_symlink()
                or not path.is_file()
                or any(part.lower() == ".git" for part in path.relative_to(self.root).parts)
            ):
                continue
            try:
                if path.stat().st_size > self.config.max_file_bytes:
                    continue
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    matches.append(f"{path.relative_to(self.root).as_posix()}:{line_number}:{line}")
                    if len(matches) >= raw_limit:
                        return "\n".join(matches), {"count": len(matches), "limit": raw_limit}
        return "\n".join(matches), {"count": len(matches), "limit": raw_limit}

    def _tool_search_symbols(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        symbol = arguments.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise SandboxError("symbol must be a non-empty string")
        directory = self._path(arguments.get("path", "."))
        if not directory.is_dir():
            raise SandboxError("search_symbols path must be a directory")
        case_sensitive = arguments.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise SandboxError("case_sensitive must be a boolean")
        raw_limit = arguments.get("max_results", 100)
        if (
            isinstance(raw_limit, bool)
            or not isinstance(raw_limit, int)
            or raw_limit < 1
            or raw_limit > 1000
        ):
            raise SandboxError("max_results must be an integer from 1 to 1000")
        needle = symbol if case_sensitive else symbol.lower()
        matches: list[str] = []
        scanned = 0
        for path in sorted(directory.rglob("*.py")):
            if (
                path.is_symlink()
                or not path.is_file()
                or any(part.lower() == ".git" for part in path.relative_to(self.root).parts)
            ):
                continue
            try:
                if path.stat().st_size > self.config.max_file_bytes:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError, ValueError):
                continue
            scanned += 1
            for node in ast.walk(tree):
                if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                candidate = node.name if case_sensitive else node.name.lower()
                if needle not in candidate:
                    continue
                kind = (
                    "class"
                    if isinstance(node, ast.ClassDef)
                    else "async-function"
                    if isinstance(node, ast.AsyncFunctionDef)
                    else "function"
                )
                relative = path.relative_to(self.root).as_posix()
                matches.append(f"{relative}:{node.lineno}:{kind}:{node.name}")
                if len(matches) >= raw_limit:
                    return "\n".join(matches), {
                        "count": len(matches),
                        "limit": raw_limit,
                        "python_files_scanned": scanned,
                    }
        return "\n".join(matches), {
            "count": len(matches),
            "limit": raw_limit,
            "python_files_scanned": scanned,
        }

    def _tool_replace(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path = self._path(arguments.get("path"))
        self._assert_writable(path)
        if not path.is_file():
            raise SandboxError("replace path must be a file")
        if path.stat().st_size > self.config.max_file_bytes:
            raise SandboxError(
                f"file exceeds sandbox.max_file_bytes ({self.config.max_file_bytes})"
            )
        old = arguments.get("old_text")
        new = arguments.get("new_text")
        expected = arguments.get("expected_replacements", 1)
        if not isinstance(old, str) or not old:
            raise SandboxError("old_text must be a non-empty string")
        if not isinstance(new, str):
            raise SandboxError("new_text must be a string")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise SandboxError("expected_replacements must be a positive integer")
        text = path.read_text(encoding="utf-8")
        actual = text.count(old)
        if actual != expected:
            raise SandboxError(f"expected {expected} matching region(s), found {actual}")
        replaced = text.replace(old, new)
        if len(replaced.encode("utf-8")) > self.config.max_file_bytes:
            raise SandboxError("replacement would exceed sandbox.max_file_bytes")
        self._atomic_workspace_write(path, replaced)
        return f"Replaced {actual} region(s) in {path.relative_to(self.root).as_posix()}.", {
            "path": path.relative_to(self.root).as_posix(),
            "replacements": actual,
        }

    def _tool_write_file(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path = self._path(arguments.get("path"), must_exist=False)
        self._assert_writable(path)
        content = arguments.get("content")
        if not isinstance(content, str):
            raise SandboxError("content must be a string")
        if len(content.encode("utf-8")) > self.config.max_file_bytes:
            raise SandboxError("new file exceeds sandbox.max_file_bytes")
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_workspace_write(path, content)
        return f"Wrote {len(content)} characters to {path.relative_to(self.root).as_posix()}.", {
            "path": path.relative_to(self.root).as_posix(),
            "overwrote": existed,
        }

    def _tool_run_tests(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if arguments:
            # No model-controlled command strings: task manifests own the command.
            unknown = set(arguments) - {"reason"}
            if unknown:
                raise SandboxError(f"run_tests does not accept: {sorted(unknown)}")
        evaluation = self._run_test_command()
        if evaluation.passed is None:
            raise SandboxError("this task does not define a test_command")
        # Process output uses platform-native newlines. Normalize only the
        # model-facing observation so identical trials make identical context
        # decisions across supported hosts; the evaluator retains raw output.
        model_output = evaluation.output.replace("\r\n", "\n").replace("\r", "\n")
        return model_output, {
            "returncode": evaluation.returncode,
            "passed": evaluation.passed,
        }

    def _atomic_workspace_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_mode = (
            stat.S_IMODE(path.stat().st_mode) if path.is_file() and not path.is_symlink() else None
        )
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # mkstemp deliberately starts at 0600. Existing files keep their
            # executable/read bits; new workspace files use the same regular-file
            # mode represented by exported patches.
            os.chmod(temporary, existing_mode if existing_mode is not None else 0o644)
            os.replace(temporary, path)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary)
            raise

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
        """Terminate the test process and descendants created in its process group."""

        if sys.platform == "win32":
            if process.poll() is not None:
                return
            # CREATE_NEW_PROCESS_GROUP gives the evaluator a distinct root PID. taskkill
            # scopes recursive termination to exactly that PID's process tree.
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    timeout=10,
                    check=False,
                )
            if process.poll() is None:
                process.kill()
            return

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            if process.poll() is None:
                process.terminate()
        # Give well-behaved descendants a brief opportunity to exit, then ensure
        # the whole session is gone even if its direct parent exited first.
        time.sleep(0.2)
        try:
            os.killpg(
                process.pid,
                signal.SIGKILL,
            )
        except ProcessLookupError:
            pass
        except OSError:
            if process.poll() is None:
                process.kill()

    @staticmethod
    def _drain_process_output(
        source: BinaryIO,
        destination: BinaryIO,
        *,
        byte_limit: int,
        truncated: list[bool],
    ) -> None:
        """Drain a child pipe while retaining only a bounded prefix on disk."""

        retained = 0
        try:
            while True:
                chunk = source.read(64 * 1024)
                if not chunk:
                    return
                remaining = max(0, byte_limit - retained)
                if remaining:
                    selected = chunk[:remaining]
                    destination.write(selected)
                    retained += len(selected)
                if len(chunk) > remaining:
                    truncated[0] = True
        except (OSError, ValueError):
            # Closing the read side is the final escape hatch for a descendant that
            # deliberately detaches while retaining the inherited output handle.
            return

    def _bounded_process_output(self, raw: bytes, *, stream_truncated: bool) -> str:
        maximum = self.config.max_process_output_chars
        output = raw.decode("utf-8", errors="replace")
        bounded, text_truncated = self._bounded(output, maximum)
        if not stream_truncated or text_truncated:
            return bounded
        marker = f"\n...[process output truncated at {maximum} characters]"
        return output[: max(0, maximum - len(marker))] + marker

    def _run_test_command(self) -> EvaluationResult:
        if not self.task.test_command:
            return EvaluationResult(None, None, "No test command configured.", 0.0, {}, {})
        command = [
            sys.executable if part == "{python}" else part for part in self.task.test_command
        ]
        started = time.perf_counter()
        test_home = self.root.parent / "test-home"
        test_temp = self.root.parent / "test-temp"
        test_home.mkdir(parents=True, exist_ok=True)
        test_temp.mkdir(parents=True, exist_ok=True)
        # Do not pass provider keys or the host's ambient Python configuration to
        # model-edited test code. A deliberately small environment also reduces
        # cross-machine confounds.
        environment = {
            key: os.environ[key]
            for key in ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL")
            if key in os.environ
        }
        environment.update(
            {
                "HOME": str(test_home),
                "USERPROFILE": str(test_home),
                "TMP": str(test_temp),
                "TEMP": str(test_temp),
                "TMPDIR": str(test_temp),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
                "PYTEST_ADDOPTS": "-p no:cacheprovider",
                "CI": "1",
                "NO_COLOR": "1",
            }
        )
        popen_options: dict[str, Any] = {}
        if sys.platform == "win32":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True

        # Retain at most four UTF-8 bytes per configured output character. A reader
        # thread continues draining beyond this prefix so a noisy child cannot block
        # on a full pipe, while the backing temporary file remains strictly bounded.
        byte_limit = self.config.max_process_output_chars * 4
        stream_truncated = [False]
        timed_out = False
        with tempfile.TemporaryFile(mode="w+b") as process_output:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                env=environment,
                **popen_options,
            )
            assert process.stdout is not None
            reader = threading.Thread(
                target=self._drain_process_output,
                args=(process.stdout, process_output),
                kwargs={"byte_limit": byte_limit, "truncated": stream_truncated},
                name=f"scaffoldscope-output-{process.pid}",
                daemon=True,
            )
            reader.start()
            try:
                process.wait(timeout=self.config.test_timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_process_tree(process)
            finally:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                reader.join(timeout=10)
                if reader.is_alive():
                    process.stdout.close()
                    reader.join(timeout=2)
                if reader.is_alive():
                    raise SandboxError("could not drain evaluator output after process termination")
                process.stdout.close()
            process_output.seek(0)
            raw_output = process_output.read(byte_limit)

        captured = self._bounded_process_output(
            raw_output,
            stream_truncated=stream_truncated[0],
        )
        if timed_out:
            prefix = f"Test command timed out after {self.config.test_timeout_seconds}s.\n"
            bounded, _ = self._bounded(prefix + captured, self.config.max_process_output_chars)
            return EvaluationResult(
                False,
                None,
                bounded,
                time.perf_counter() - started,
                {},
                {},
            )
        return EvaluationResult(
            process.returncode == 0,
            process.returncode,
            captured,
            time.perf_counter() - started,
            {},
            {},
        )

    def evaluate(self) -> EvaluationResult:
        test = self._run_test_command()
        checks, details = self._check_constraints(self.task.constraints)
        integrity_checks: dict[str, str] = {}
        evaluator_integrity = True
        for protected in self.task.protected_paths:
            relative = Path(protected).as_posix()
            initial = self._protected_snapshots[relative]
            current = self._protected_snapshot(Path(protected))
            unchanged = current == initial
            evaluator_integrity = evaluator_integrity and unchanged
            integrity_checks[relative] = "unchanged" if unchanged else "changed or missing"
        return EvaluationResult(
            passed=(test.passed and evaluator_integrity) if test.passed is not None else None,
            returncode=test.returncode,
            output=test.output,
            duration_seconds=test.duration_seconds,
            constraint_checks=checks,
            constraint_details=details,
            evaluator_integrity=evaluator_integrity,
            evaluator_integrity_details=integrity_checks,
        )

    def _check_constraints(
        self, constraints: Iterable[ConstraintSpec]
    ) -> tuple[dict[str, bool], dict[str, str]]:
        checks: dict[str, bool] = {}
        details: dict[str, str] = {}
        for constraint in constraints:
            if constraint.check is None:
                continue
            check = constraint.check
            path = self._path(check["path"], must_exist=False)
            relative = path.relative_to(self.root).as_posix()
            check_type = check["type"]
            if check_type == "file_exists":
                passed = path.is_file()
                detail = f"{relative} {'exists' if passed else 'is missing'}"
            elif check_type == "file_unchanged":
                initial = self._initial.get(relative)
                if not path.is_file() or initial is None:
                    passed = False
                else:
                    passed = _sha256(path.read_bytes()) == initial[0]
                detail = f"{relative} {'was unchanged' if passed else 'changed or disappeared'}"
            else:
                text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
                needle = str(check["text"])
                present = needle in text
                passed = present if check_type == "text_present" else not present
                detail = f"{needle!r} was {'present' if present else 'absent'} in {relative}"
            checks[constraint.id] = passed
            details[constraint.id] = detail
        return checks, details

    def patch(self) -> str:
        if (self.root / ".git").exists():
            add = subprocess.run(
                ["git", "add", "-N", "--", "."],
                cwd=self.root,
                capture_output=True,
                shell=False,
                timeout=30,
            )
            if add.returncode != 0:
                raise SandboxError(
                    f"git add -N failed while capturing the patch: {add.stderr.decode(errors='replace') if isinstance(add.stderr, bytes) else add.stderr}"
                )
            completed = subprocess.run(
                ["git", "diff", "--binary", "--no-ext-diff", "--", "."],
                cwd=self.root,
                capture_output=True,
                text=True,
                errors="replace",
                shell=False,
                timeout=30,
            )
            if completed.returncode != 0:
                raise SandboxError(f"git diff failed while capturing the patch: {completed.stderr}")
            return completed.stdout
        current_links = self._symlink_snapshot()
        if current_links != self._initial_links:
            raise SandboxError(
                "workspace symlink changes cannot be represented safely in a non-Git patch"
            )
        current: dict[str, tuple[str, str | None]] = {}
        for path in self._iter_files():
            relative = path.relative_to(self.root).as_posix()
            raw = path.read_bytes()
            current[relative] = (
                _sha256(raw),
                _patch_text(raw, self.config.max_file_bytes),
            )
        chunks: list[str] = []
        for relative in sorted(set(self._initial) | set(current)):
            old_hash, old = self._initial.get(relative, ("", ""))
            new_hash, new = current.get(relative, ("", ""))
            if old_hash == new_hash:
                continue
            if old is None or new is None:
                raise SandboxError(
                    f"changed binary or oversized file cannot be exported safely: {relative}"
                )
            created = relative not in self._initial
            deleted = relative not in current
            chunks.append(f"diff --git a/{relative} b/{relative}\n")
            if created:
                chunks.append("new file mode 100644\n")
            elif deleted:
                chunks.append("deleted file mode 100644\n")
            if not old and not new:
                if created:
                    chunks.extend(
                        [
                            "index 0000000..e69de29\n",
                            "--- /dev/null\n",
                            f"+++ b/{relative}\n",
                        ]
                    )
                elif deleted:
                    chunks.extend(
                        [
                            "index e69de29..0000000\n",
                            f"--- a/{relative}\n",
                            "+++ /dev/null\n",
                        ]
                    )
                continue
            chunks.extend(
                _unified_patch(
                    old,
                    new,
                    fromfile="/dev/null" if created else f"a/{relative}",
                    tofile="/dev/null" if deleted else f"b/{relative}",
                )
            )
        return "".join(chunks)
