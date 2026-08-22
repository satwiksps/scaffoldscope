"""Versioned configuration and result-domain types."""

from __future__ import annotations

import hashlib
import math
import re
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import content_hash, load_json, load_jsonl
from scaffoldscope.redact import redact_text

if TYPE_CHECKING:
    from scaffoldscope.docker_sandbox import DockerSandboxConfig
    from scaffoldscope.plugins import PluginRegistry

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
BUILTIN_TOOL_NAMES = (
    "list_files",
    "read_file",
    "search",
    "search_symbols",
    "replace",
    "write_file",
    "run_tests",
)
_BUILTIN_POLICY_FIELDS = {
    "none": frozenset(),
    "reactive": frozenset({"trigger_ratio", "target_ratio", "keep_recent_bundles"}),
    "periodic": frozenset({"target_ratio", "every_turns", "keep_recent_bundles"}),
    "selective": frozenset({"trigger_ratio", "target_ratio", "keep_recent_bundles", "weights"}),
}
_POLICY_CONFIGURATION_FIELDS = frozenset(
    {
        "trigger_ratio",
        "target_ratio",
        "every_turns",
        "keep_recent_bundles",
        "weights",
        "plugin_options",
    }
)
_SCRIPTED_UNSUPPORTED_FIELDS = frozenset(
    {"base_url", "api_key_env", "requires_api_key", "timeout_seconds", "retries", "json_mode"}
)
_IGNORED_FINGERPRINT_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def _directory_fingerprint(path: Path) -> str:
    records: list[dict[str, Any]] = []
    children = sorted(
        path.rglob("*"),
        key=lambda child: child.relative_to(path).as_posix().encode("utf-8"),
    )
    for child in children:
        relative = child.relative_to(path)
        if any(part in _IGNORED_FINGERPRINT_PARTS for part in relative.parts):
            continue
        relative_name = relative.as_posix()
        if child.is_symlink():
            records.append(
                {
                    "path": relative_name,
                    "kind": "symlink",
                    "target": str(child.readlink()),
                }
            )
        elif child.is_dir():
            mode = stat.S_IMODE(child.stat(follow_symlinks=False).st_mode)
            records.append(
                {
                    "path": relative_name,
                    "kind": "directory",
                    "mode": f"{mode:04o}",
                }
            )
        elif child.is_file():
            mode = stat.S_IMODE(child.stat(follow_symlinks=False).st_mode)
            digest = hashlib.sha256()
            byte_count = 0
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    byte_count += len(chunk)
            records.append(
                {
                    "path": relative_name,
                    "kind": "file",
                    "mode": f"{mode:04o}",
                    "bytes": byte_count,
                    "sha256": digest.hexdigest(),
                }
            )
        else:
            raise ConfigError(f"Unsupported special file in task workspace: {child}")
    return content_hash({"algorithm": "task-directory-v2", "entries": records})


def _git_object(path: Path, revision: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", f"{revision}^{{commit}}"],
            cwd=path,
            capture_output=True,
            text=True,
            shell=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _task_source_fingerprint(task: TaskSpec) -> str:
    if (task.workspace / ".git").exists():
        revision = task.base_commit or "HEAD"
        commit = _git_object(task.workspace, revision)
        if commit is None:
            raise ConfigError(
                f"Could not resolve Git revision {revision!r} in task workspace {task.workspace}"
            )
        return content_hash({"kind": "git-commit", "commit": commit})
    return content_hash(
        {"kind": "directory", "content_sha256": _directory_fingerprint(task.workspace)}
    )


def _implementation_fingerprint() -> str:
    package = Path(__file__).resolve().parent
    records: list[dict[str, Any]] = []
    children = sorted(
        package.rglob("*.py"),
        key=lambda child: child.relative_to(package).as_posix().encode("utf-8"),
    )
    for child in children:
        relative_path = child.relative_to(package)
        if "demo" in relative_path.parts:
            continue
        payload = child.read_bytes()
        records.append(
            {
                "path": relative_path.as_posix(),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return content_hash({"algorithm": "implementation-python-v2", "files": records})


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a JSON object")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"Unknown {label} field(s): {unknown}")


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ConfigError(f"{label} must be a non-empty string")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{label} must be finite")
    return result


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be a boolean")
    return value


def _nonnegative_number(value: Any, label: str) -> float:
    result = _number(value, label)
    if result < 0:
        raise ConfigError(f"{label} must be non-negative")
    return result


def _positive_number(value: Any, label: str) -> float:
    result = _number(value, label)
    if result <= 0:
        raise ConfigError(f"{label} must be positive")
    return result


def _positive_int(value: Any, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{label} must be an integer >= {minimum}")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{label} must be an integer")
    return value


def _ratio(value: Any, label: str) -> float:
    result = _number(value, label)
    if result <= 0 or result > 1:
        raise ConfigError(f"{label} must be in (0, 1]")
    return result


def _identifier(value: Any, label: str) -> str:
    result = _string(value, label)
    if not _ID_PATTERN.fullmatch(result):
        raise ConfigError(f"{label} must match {_ID_PATTERN.pattern}")
    return result


def _relative_path_string(value: Any, label: str) -> str:
    result = _string(value, label)
    path = Path(result)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(part.lower() == ".git" for part in path.parts)
    ):
        raise ConfigError(f"{label} must be a relative workspace path outside .git")
    return result


@dataclass(frozen=True)
class ConstraintSpec:
    id: str
    text: str
    check: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, value: Any, label: str) -> ConstraintSpec:
        data = _mapping(value, label)
        _reject_unknown(data, {"id", "text", "check"}, label)
        check = data.get("check")
        if check is not None:
            check = _mapping(check, f"{label}.check")
            _reject_unknown(check, {"type", "path", "text"}, f"{label}.check")
            check_type = _string(check.get("type"), f"{label}.check.type")
            if check_type not in {"file_unchanged", "file_exists", "text_present", "text_absent"}:
                raise ConfigError(f"Unsupported constraint check type: {check_type}")
            _relative_path_string(check.get("path"), f"{label}.check.path")
            if check_type in {"text_present", "text_absent"}:
                _string(check.get("text"), f"{label}.check.text")
        return cls(
            id=_identifier(data.get("id"), f"{label}.id"),
            text=_string(data.get("text"), f"{label}.text"),
            check=check,
        )


@dataclass(frozen=True)
class TaskSpec:
    id: str
    workspace: Path
    problem: str
    constraints: tuple[ConstraintSpec, ...]
    test_command: tuple[str, ...]
    protected_paths: tuple[str, ...] = ()
    script: tuple[dict[str, Any], ...] = ()
    base_commit: str | None = None
    repository: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, manifest_dir: Path) -> TaskSpec:
        # Accept SWE-bench's field names as aliases so converted manifests stay transparent.
        _reject_unknown(
            value,
            {
                "id",
                "instance_id",
                "workspace",
                "problem",
                "problem_statement",
                "constraints",
                "test_command",
                "protected_paths",
                "script",
                "base_commit",
                "repository",
                "repo",
                "metadata",
                "FAIL_TO_PASS",
                "PASS_TO_PASS",
                "version",
            },
            "task",
        )
        for canonical, alias in (
            ("id", "instance_id"),
            ("problem", "problem_statement"),
            ("repository", "repo"),
        ):
            if canonical in value and alias in value:
                raise ConfigError(f"Task cannot set both {canonical!r} and its alias {alias!r}")
        task_id = value.get("id", value.get("instance_id"))
        problem = value.get("problem", value.get("problem_statement"))
        workspace_value = value.get("workspace")
        if workspace_value is None:
            raise ConfigError(
                f"Task {task_id!r} has no workspace. Point it at a trusted local checkout."
            )
        workspace = Path(_string(workspace_value, f"task {task_id}.workspace"))
        if not workspace.is_absolute():
            workspace = (manifest_dir / workspace).resolve()
        constraints_value = value.get("constraints", [])
        if not isinstance(constraints_value, list):
            raise ConfigError(f"task {task_id}.constraints must be a list")
        constraints = tuple(
            ConstraintSpec.from_dict(item, f"task {task_id}.constraints[{index}]")
            for index, item in enumerate(constraints_value)
        )
        if len({item.id for item in constraints}) != len(constraints):
            raise ConfigError(f"task {task_id} has duplicate constraint IDs")
        command_value = value.get("test_command", [])
        if not isinstance(command_value, list) or not all(
            isinstance(item, str) and item for item in command_value
        ):
            raise ConfigError(f"task {task_id}.test_command must be a list of strings")
        script_value = value.get("script", [])
        if not isinstance(script_value, list) or not all(
            isinstance(item, dict) for item in script_value
        ):
            raise ConfigError(f"task {task_id}.script must be a list of JSON objects")
        protected_value = value.get("protected_paths", [])
        if not isinstance(protected_value, list):
            raise ConfigError(f"task {task_id}.protected_paths must be relative path strings")
        protected_paths = tuple(
            _relative_path_string(item, f"task {task_id}.protected_paths[{index}]")
            for index, item in enumerate(protected_value)
        )
        if len(set(protected_paths)) != len(protected_paths):
            raise ConfigError(f"task {task_id}.protected_paths must be unique")
        metadata = dict(_mapping(value.get("metadata", {}), f"task {task_id}.metadata"))
        for key in ("FAIL_TO_PASS", "PASS_TO_PASS", "version"):
            if key in value:
                metadata[key] = value[key]
        return cls(
            id=_identifier(task_id, "task.id"),
            workspace=workspace,
            problem=_string(problem, f"task {task_id}.problem"),
            constraints=constraints,
            test_command=tuple(command_value),
            protected_paths=protected_paths,
            script=tuple(script_value),
            base_commit=(
                _string(value["base_commit"], f"task {task_id}.base_commit")
                if value.get("base_commit") is not None
                else None
            ),
            repository=(
                _string(
                    value.get("repository", value.get("repo")),
                    f"task {task_id}.repository",
                )
                if value.get("repository", value.get("repo")) is not None
                else None
            ),
            metadata=metadata,
        )

    def prompt(self) -> str:
        lines = [self.problem.strip()]
        if self.constraints:
            lines.extend(["", "Standing constraints (keep these active for the entire task):"])
            lines.extend(f"- [{item.id}] {item.text}" for item in self.constraints)
        return "\n".join(lines)


@dataclass(frozen=True)
class VariantConfig:
    id: str
    policy: str
    trigger_ratio: float = 0.95
    target_ratio: float = 0.65
    every_turns: int = 4
    keep_recent_bundles: int = 2
    weights: dict[str, float] = field(default_factory=dict)
    plugin_options: dict[str, Any] = field(default_factory=dict)
    tools: tuple[str, ...] | None = None
    instructions: str | None = None

    @classmethod
    def from_dict(cls, value: Any, index: int) -> VariantConfig:
        data = _mapping(value, f"variants[{index}]")
        _reject_unknown(
            data,
            {
                "id",
                "policy",
                "trigger_ratio",
                "target_ratio",
                "every_turns",
                "keep_recent_bundles",
                "weights",
                "plugin_options",
                "tools",
                "instructions",
            },
            f"variants[{index}]",
        )
        policy = _identifier(data.get("policy"), f"variants[{index}].policy")
        if policy in _BUILTIN_POLICY_FIELDS:
            unused = sorted(
                (data.keys() & _POLICY_CONFIGURATION_FIELDS) - _BUILTIN_POLICY_FIELDS[policy]
            )
            if unused:
                raise ConfigError(
                    f"Built-in context policy {policy!r} does not use: {', '.join(unused)}"
                )
        trigger = _ratio(data.get("trigger_ratio", 0.95), f"variants[{index}].trigger_ratio")
        target = _ratio(data.get("target_ratio", 0.65), f"variants[{index}].target_ratio")
        if target > trigger and policy != "periodic":
            raise ConfigError(f"variants[{index}].target_ratio cannot exceed trigger_ratio")
        raw_weights = _mapping(data.get("weights", {}), f"variants[{index}].weights")
        weights: dict[str, float] = {}
        allowed_weights = {"recency", "referenced", "subgoal", "constraint", "task", "error"}
        for key, raw_value in raw_weights.items():
            if key not in allowed_weights:
                raise ConfigError(f"Unknown selective weight: {key}")
            weights[str(key)] = _nonnegative_number(raw_value, f"variants[{index}].weights.{key}")
        raw_tools = data.get("tools")
        tools: tuple[str, ...] | None = None
        if raw_tools is not None:
            if not isinstance(raw_tools, list) or not all(
                isinstance(item, str) and item in BUILTIN_TOOL_NAMES for item in raw_tools
            ):
                raise ConfigError(
                    f"variants[{index}].tools must be a list containing only "
                    f"{list(BUILTIN_TOOL_NAMES)}"
                )
            if len(set(raw_tools)) != len(raw_tools):
                raise ConfigError(f"variants[{index}].tools must not contain duplicates")
            tools = tuple(name for name in BUILTIN_TOOL_NAMES if name in raw_tools)
        instructions_value = data.get("instructions")
        return cls(
            id=_identifier(data.get("id"), f"variants[{index}].id"),
            policy=policy,
            trigger_ratio=trigger,
            target_ratio=target,
            every_turns=_positive_int(data.get("every_turns", 4), f"variants[{index}].every_turns"),
            keep_recent_bundles=_positive_int(
                data.get("keep_recent_bundles", 2),
                f"variants[{index}].keep_recent_bundles",
                minimum=0,
            ),
            weights=weights,
            plugin_options=dict(
                _mapping(
                    data.get("plugin_options", {}),
                    f"variants[{index}].plugin_options",
                )
            ),
            tools=tools,
            instructions=(
                _string(instructions_value, f"variants[{index}].instructions")
                if instructions_value is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    name: str
    context_window_tokens: int
    max_output_tokens: int
    temperature: float
    base_url: str | None
    api_key_env: str
    timeout_seconds: float
    retries: int
    supports_seed: bool
    json_mode: bool
    input_price_per_million: float | None
    output_price_per_million: float | None
    cache_read_price_per_million: float | None
    cache_write_price_per_million: float | None
    requires_api_key: bool = True
    plugin_options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Any) -> ModelConfig:
        data = _mapping(value, "model")
        _reject_unknown(
            data,
            {
                "provider",
                "name",
                "context_window_tokens",
                "max_output_tokens",
                "temperature",
                "base_url",
                "api_key_env",
                "requires_api_key",
                "timeout_seconds",
                "retries",
                "supports_seed",
                "json_mode",
                "input_price_per_million",
                "output_price_per_million",
                "cache_read_price_per_million",
                "cache_write_price_per_million",
                "plugin_options",
            },
            "model",
        )
        provider = _identifier(data.get("provider"), "model.provider")
        if provider == "scripted":
            unused = sorted(data.keys() & _SCRIPTED_UNSUPPORTED_FIELDS)
            if unused:
                raise ConfigError(
                    f"Built-in model provider 'scripted' does not use: {', '.join(unused)}"
                )
        base_url = data.get("base_url")
        if base_url is not None:
            base_url = _string(base_url, "model.base_url").rstrip("/")
            parsed_url = urlsplit(base_url)
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.hostname
                or parsed_url.username is not None
                or parsed_url.password is not None
                or parsed_url.query
                or parsed_url.fragment
            ):
                raise ConfigError(
                    "model.base_url must be an HTTP(S) URL without credentials, query, or fragment"
                )
        api_key_env = _string(data.get("api_key_env", "OPENAI_API_KEY"), "model.api_key_env")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ConfigError("model.api_key_env must be an environment-variable name")
        requires_api_key = _boolean(data.get("requires_api_key", True), "model.requires_api_key")
        if base_url is not None:
            parsed_url = urlsplit(base_url)
            if (
                provider == "openai_compatible"
                and requires_api_key
                and parsed_url.scheme == "http"
                and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ConfigError(
                    "model.base_url must use HTTPS when model.requires_api_key is true; "
                    "plain HTTP is allowed only for loopback endpoints"
                )
        input_price = data.get("input_price_per_million")
        output_price = data.get("output_price_per_million")
        cache_read_price = data.get("cache_read_price_per_million")
        cache_write_price = data.get("cache_write_price_per_million")
        temperature = _nonnegative_number(data.get("temperature", 0.0), "model.temperature")
        supports_seed = _boolean(data.get("supports_seed", False), "model.supports_seed")
        if provider == "scripted" and temperature != 0.0:
            raise ConfigError("Built-in model provider 'scripted' does not use temperature")
        if provider == "scripted" and supports_seed:
            raise ConfigError("Built-in model provider 'scripted' does not use provider seeds")
        return cls(
            provider=provider,
            name=_string(data.get("name"), "model.name"),
            context_window_tokens=_positive_int(
                data.get("context_window_tokens"), "model.context_window_tokens", minimum=128
            ),
            max_output_tokens=_positive_int(
                data.get("max_output_tokens", 512), "model.max_output_tokens"
            ),
            temperature=temperature,
            base_url=base_url,
            api_key_env=api_key_env,
            timeout_seconds=_positive_number(
                data.get("timeout_seconds", 120), "model.timeout_seconds"
            ),
            retries=_positive_int(data.get("retries", 2), "model.retries", minimum=0),
            supports_seed=supports_seed,
            json_mode=_boolean(data.get("json_mode", False), "model.json_mode"),
            input_price_per_million=(
                _nonnegative_number(input_price, "model.input_price_per_million")
                if input_price is not None
                else None
            ),
            output_price_per_million=(
                _nonnegative_number(output_price, "model.output_price_per_million")
                if output_price is not None
                else None
            ),
            cache_read_price_per_million=(
                _nonnegative_number(cache_read_price, "model.cache_read_price_per_million")
                if cache_read_price is not None
                else None
            ),
            cache_write_price_per_million=(
                _nonnegative_number(cache_write_price, "model.cache_write_price_per_million")
                if cache_write_price is not None
                else None
            ),
            requires_api_key=requires_api_key,
            plugin_options=dict(_mapping(data.get("plugin_options", {}), "model.plugin_options")),
        )


@dataclass(frozen=True)
class AgentConfig:
    max_turns: int = 20
    max_total_tokens: int = 100_000
    max_cost_usd: float | None = None
    system_prompt: str | None = None

    @classmethod
    def from_dict(cls, value: Any, *, config_dir: Path) -> AgentConfig:
        data = _mapping(value, "agent")
        _reject_unknown(
            data,
            {"max_turns", "max_total_tokens", "max_cost_usd", "system_prompt", "prompt_file"},
            "agent",
        )
        system_prompt = data.get("system_prompt")
        prompt_file = data.get("prompt_file")
        if system_prompt is not None and prompt_file is not None:
            raise ConfigError("Set only one of agent.system_prompt and agent.prompt_file")
        if prompt_file is not None:
            path = Path(_string(prompt_file, "agent.prompt_file"))
            if not path.is_absolute():
                path = config_dir / path
            try:
                system_prompt = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ConfigError(f"Could not read agent.prompt_file {path}: {exc}") from exc
        if system_prompt is not None:
            system_prompt = _string(system_prompt, "agent.system_prompt")
        cost = data.get("max_cost_usd")
        return cls(
            max_turns=_positive_int(data.get("max_turns", 20), "agent.max_turns"),
            max_total_tokens=_positive_int(
                data.get("max_total_tokens", 100_000), "agent.max_total_tokens"
            ),
            max_cost_usd=(
                _nonnegative_number(cost, "agent.max_cost_usd") if cost is not None else None
            ),
            system_prompt=system_prompt,
        )


@dataclass(frozen=True)
class SandboxConfig:
    backend: str = "local"
    max_file_bytes: int = 1_000_000
    max_observation_chars: int = 20_000
    max_process_output_chars: int = 20_000
    test_timeout_seconds: float = 120.0

    @classmethod
    def from_dict(cls, value: Any) -> SandboxConfig:
        data = _mapping(value, "sandbox")
        _reject_unknown(
            data,
            {
                "backend",
                "max_file_bytes",
                "max_observation_chars",
                "max_process_output_chars",
                "test_timeout_seconds",
                "docker",
            },
            "sandbox",
        )
        backend = _string(data.get("backend", "local"), "sandbox.backend")
        if backend not in {"local", "docker"}:
            raise ConfigError("sandbox.backend must be local or docker")
        return cls(
            backend=backend,
            max_file_bytes=_positive_int(
                data.get("max_file_bytes", 1_000_000), "sandbox.max_file_bytes"
            ),
            max_observation_chars=_positive_int(
                data.get("max_observation_chars", 20_000), "sandbox.max_observation_chars"
            ),
            max_process_output_chars=_positive_int(
                data.get("max_process_output_chars", 20_000),
                "sandbox.max_process_output_chars",
            ),
            test_timeout_seconds=_positive_number(
                data.get("test_timeout_seconds", 120), "sandbox.test_timeout_seconds"
            ),
        )


def _docker_settings(value: Any, *, enabled: bool) -> DockerSandboxConfig | None:
    if value is None:
        if enabled:
            raise ConfigError("sandbox.docker is required when sandbox.backend is docker")
        return None
    if not enabled:
        raise ConfigError("sandbox.docker is only valid when sandbox.backend is docker")
    data = _mapping(value, "sandbox.docker")
    allowed = {
        "image",
        "binary",
        "user",
        "cpus",
        "memory_bytes",
        "pids_limit",
        "tmpfs_bytes",
        "nofile_limit",
        "cleanup_timeout_seconds",
        "python_executable",
        "platform",
        "require_image_digest",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"Unknown sandbox.docker field(s): {sorted(unknown)}")
    from scaffoldscope.docker_sandbox import DockerSandboxConfig

    platform_value = data.get("platform", "linux/amd64")
    platform_name = (
        _string(platform_value, "sandbox.docker.platform") if platform_value is not None else None
    )
    return DockerSandboxConfig(
        image=_string(data.get("image"), "sandbox.docker.image"),
        docker_binary=_string(data.get("binary", "docker"), "sandbox.docker.binary"),
        user=_string(data.get("user", "65532:65532"), "sandbox.docker.user"),
        cpus=_positive_number(data.get("cpus", 2.0), "sandbox.docker.cpus"),
        memory_bytes=_positive_int(
            data.get("memory_bytes", 2 * 1024 * 1024 * 1024),
            "sandbox.docker.memory_bytes",
        ),
        pids_limit=_positive_int(data.get("pids_limit", 256), "sandbox.docker.pids_limit"),
        tmpfs_bytes=_positive_int(
            data.get("tmpfs_bytes", 512 * 1024 * 1024),
            "sandbox.docker.tmpfs_bytes",
        ),
        nofile_limit=_positive_int(data.get("nofile_limit", 1024), "sandbox.docker.nofile_limit"),
        cleanup_timeout_seconds=_positive_number(
            data.get("cleanup_timeout_seconds", 10),
            "sandbox.docker.cleanup_timeout_seconds",
        ),
        python_executable=_string(
            data.get("python_executable", "python"),
            "sandbox.docker.python_executable",
        ),
        platform=platform_name,
        require_image_digest=_boolean(
            data.get("require_image_digest", True),
            "sandbox.docker.require_image_digest",
        ),
    )


@dataclass(frozen=True)
class ExperimentSettings:
    name: str
    replicates: tuple[int, ...]
    max_workers: int
    output_dir: Path
    baseline: str
    randomize_variant_order: bool
    bootstrap_samples: int
    analysis_seed: int
    sesoi: float
    primary_comparison: str | None

    @classmethod
    def from_dict(cls, value: Any, *, config_dir: Path) -> ExperimentSettings:
        data = _mapping(value, "experiment")
        _reject_unknown(
            data,
            {
                "name",
                "replicates",
                "seeds",
                "max_workers",
                "output_dir",
                "baseline",
                "primary_comparison",
                "randomize_variant_order",
                "bootstrap_samples",
                "analysis_seed",
                "sesoi",
            },
            "experiment",
        )
        if "replicates" in data and "seeds" in data:
            raise ConfigError("experiment cannot set both replicates and its legacy alias seeds")
        replicates = data.get("replicates", data.get("seeds", [1729, 2718, 31415]))
        if (
            not isinstance(replicates, list)
            or not replicates
            or not all(isinstance(item, int) and not isinstance(item, bool) for item in replicates)
        ):
            raise ConfigError("experiment.replicates must be a non-empty list of integers")
        if len(set(replicates)) != len(replicates):
            raise ConfigError("experiment.replicates must be unique")
        if any(item < 0 or item > 2**63 - 1 for item in replicates):
            raise ConfigError("experiment.replicates must be integers from 0 through 2^63-1")
        output = Path(_string(data.get("output_dir", "runs"), "experiment.output_dir"))
        if not output.is_absolute():
            output = config_dir / output
        output = output.resolve()
        sesoi = _number(data.get("sesoi", 0.05), "experiment.sesoi")
        if sesoi <= 0 or sesoi >= 1:
            raise ConfigError("experiment.sesoi must be in (0, 1)")
        primary = data.get("primary_comparison")
        if primary is not None:
            primary = _identifier(primary, "experiment.primary_comparison")
        return cls(
            name=_identifier(data.get("name"), "experiment.name"),
            replicates=tuple(replicates),
            max_workers=_positive_int(data.get("max_workers", 1), "experiment.max_workers"),
            output_dir=output,
            baseline=_identifier(data.get("baseline", "none"), "experiment.baseline"),
            randomize_variant_order=_boolean(
                data.get("randomize_variant_order", True),
                "experiment.randomize_variant_order",
            ),
            bootstrap_samples=_positive_int(
                data.get("bootstrap_samples", 5000),
                "experiment.bootstrap_samples",
                minimum=100,
            ),
            analysis_seed=_integer(data.get("analysis_seed", 20260815), "experiment.analysis_seed"),
            sesoi=sesoi,
            primary_comparison=primary,
        )


@dataclass(frozen=True)
class RunConfig:
    schema_version: int
    path: Path
    raw: dict[str, Any]
    experiment: ExperimentSettings
    model: ModelConfig
    agent: AgentConfig
    sandbox: SandboxConfig
    docker: DockerSandboxConfig | None
    plugin_registry: PluginRegistry
    plugin_provenance: dict[str, dict[str, Any]]
    variants: tuple[VariantConfig, ...]
    tasks: tuple[TaskSpec, ...]
    task_manifest: Path
    implementation_hash: str
    task_source_hashes: dict[str, str]
    config_hash: str

    @classmethod
    def load(cls, path: Path) -> RunConfig:
        path = path.resolve()
        raw = _mapping(load_json(path), "root")
        _reject_unknown(
            raw,
            {"schema_version", "experiment", "tasks", "model", "agent", "sandbox", "variants"},
            "root",
        )
        if "schema_version" not in raw:
            raise ConfigError("schema_version is required")
        version = _positive_int(raw.get("schema_version"), "schema_version")
        if version != 1:
            raise ConfigError(
                f"Unsupported schema_version {version}; this release supports version 1"
            )
        config_dir = path.parent
        tasks_config = _mapping(raw.get("tasks"), "tasks")
        _reject_unknown(tasks_config, {"manifest", "ids", "limit"}, "tasks")
        manifest = Path(_string(tasks_config.get("manifest"), "tasks.manifest"))
        if not manifest.is_absolute():
            manifest = (config_dir / manifest).resolve()
        task_rows = load_jsonl(manifest)
        selected = tasks_config.get("ids")
        if selected is not None:
            if not isinstance(selected, list) or not all(
                isinstance(item, str) for item in selected
            ):
                raise ConfigError("tasks.ids must be a list of task IDs")
            if len(set(selected)) != len(selected):
                raise ConfigError("tasks.ids must contain unique task IDs")
            selected_set = set(selected)
            task_rows = [
                row for row in task_rows if row.get("id", row.get("instance_id")) in selected_set
            ]
            missing = selected_set - {row.get("id", row.get("instance_id")) for row in task_rows}
            if missing:
                raise ConfigError(f"tasks.ids not found in manifest: {sorted(missing)}")
        limit = tasks_config.get("limit")
        if limit is not None:
            task_rows = task_rows[: _positive_int(limit, "tasks.limit")]
        tasks = tuple(TaskSpec.from_dict(row, manifest_dir=manifest.parent) for row in task_rows)
        if not tasks:
            raise ConfigError("The selected task panel is empty")
        if len({task.id for task in tasks}) != len(tasks):
            raise ConfigError("Task IDs must be unique")
        variants_value = raw.get("variants")
        if not isinstance(variants_value, list) or not variants_value:
            raise ConfigError("variants must be a non-empty list")
        variants = tuple(
            VariantConfig.from_dict(item, index) for index, item in enumerate(variants_value)
        )
        if len({variant.id for variant in variants}) != len(variants):
            raise ConfigError("Variant IDs must be unique")
        experiment = ExperimentSettings.from_dict(raw.get("experiment"), config_dir=config_dir)
        if experiment.baseline not in {variant.id for variant in variants}:
            raise ConfigError("experiment.baseline must match one of the variant IDs")
        if experiment.primary_comparison is not None and experiment.primary_comparison not in {
            variant.id for variant in variants
        }:
            raise ConfigError("experiment.primary_comparison must match one of the variant IDs")
        if experiment.primary_comparison == experiment.baseline:
            raise ConfigError("experiment.primary_comparison cannot equal the baseline")
        missing_workspaces = [str(task.workspace) for task in tasks if not task.workspace.is_dir()]
        if missing_workspaces:
            raise ConfigError(f"Task workspaces do not exist: {missing_workspaces}")
        for task in tasks:
            try:
                experiment.output_dir.relative_to(task.workspace.resolve())
            except ValueError:
                continue
            raise ConfigError(
                "experiment.output_dir must not be inside a task workspace; generated evidence "
                f"would contaminate task source {task.id!r}"
            )
        implementation_hash = _implementation_fingerprint()
        task_source_hashes = {task.id: _task_source_fingerprint(task) for task in tasks}
        model_config = ModelConfig.from_dict(raw.get("model"))
        agent_config = AgentConfig.from_dict(raw.get("agent", {}), config_dir=config_dir)
        from scaffoldscope.plugins import BUILTIN_PLUGIN_NAMES, PluginKind, PluginRegistry

        registry = PluginRegistry.discover()
        plugin_provenance: dict[str, dict[str, Any]] = {}
        builtin_policies = BUILTIN_PLUGIN_NAMES[PluginKind.CONTEXT_POLICY]
        builtin_providers = BUILTIN_PLUGIN_NAMES[PluginKind.MODEL_PROVIDER]
        for variant in variants:
            if variant.policy in builtin_policies:
                continue
            loaded = registry.load_context_policy(variant.policy)
            plugin_provenance[f"context_policy:{loaded.info.normalized_name}"] = loaded.provenance()
        if model_config.provider in builtin_providers:
            if model_config.plugin_options:
                raise ConfigError(
                    f"Built-in model provider {model_config.provider!r} does not accept plugin_options"
                )
        else:
            loaded_provider = registry.load_model_provider(model_config.provider)
            plugin_provenance[f"model_provider:{loaded_provider.info.normalized_name}"] = (
                loaded_provider.provenance()
            )
        if model_config.provider == "openai_compatible" and model_config.base_url is None:
            raise ConfigError("model.base_url is required for openai_compatible")
        if model_config.provider == "scripted":
            missing_scripts = [task.id for task in tasks if not task.script]
            if missing_scripts:
                raise ConfigError(
                    f"Scripted-provider tasks need non-empty scripts: {missing_scripts}"
                )
        if model_config.max_output_tokens >= model_config.context_window_tokens:
            raise ConfigError("model.max_output_tokens must be smaller than context_window_tokens")
        if agent_config.max_cost_usd is not None and (
            model_config.input_price_per_million is None
            or model_config.output_price_per_million is None
        ):
            raise ConfigError("agent.max_cost_usd requires configured input and output prices")
        sandbox_config = SandboxConfig.from_dict(raw.get("sandbox", {}))
        sandbox_data = _mapping(raw.get("sandbox", {}), "sandbox")
        docker_config = _docker_settings(
            sandbox_data.get("docker"), enabled=sandbox_config.backend == "docker"
        )
        digest_input = {
            "config": raw,
            "tasks": task_rows,
            "prompt_file_contents": agent_config.system_prompt,
            "implementation_hash": implementation_hash,
            "task_source_hashes": task_source_hashes,
            "plugin_provenance": plugin_provenance,
        }
        return cls(
            schema_version=version,
            path=path,
            raw=raw,
            experiment=experiment,
            model=model_config,
            agent=agent_config,
            sandbox=sandbox_config,
            docker=docker_config,
            plugin_registry=registry,
            plugin_provenance=plugin_provenance,
            variants=variants,
            tasks=tasks,
            task_manifest=manifest,
            implementation_hash=implementation_hash,
            task_source_hashes=task_source_hashes,
            config_hash=content_hash(digest_input),
        )

    @property
    def experiment_dir(self) -> Path:
        return self.experiment.output_dir / f"{self.experiment.name}-{self.config_hash[:8]}"

    @property
    def task_toolsets(self) -> dict[str, list[str]]:
        return {
            task.id: [
                name
                for name in BUILTIN_TOOL_NAMES
                if name != "run_tests" or bool(task.test_command)
            ]
            for task in self.tasks
        }

    @property
    def task_provenance(self) -> dict[str, dict[str, str | None]]:
        return {
            task.id: {
                "repository": task.repository,
                "base_commit": task.base_commit,
                "source_hash": self.task_source_hashes[task.id],
            }
            for task in self.tasks
        }

    @property
    def task_constraints(self) -> dict[str, list[dict[str, Any]]]:
        """Return redacted constraint text plus an exact content commitment.

        Constraint checks can contain local paths or evaluator commands and are not
        needed to verify model-facing lexical availability. Persist only the stable
        ID/text provenance that the context policy actually receives without
        writing common credential shapes into portable experiment metadata.
        """

        result: dict[str, list[dict[str, Any]]] = {}
        for task in self.tasks:
            rows: list[dict[str, Any]] = []
            for constraint in task.constraints:
                redacted_text = redact_text(constraint.text)
                rows.append(
                    {
                        "id": constraint.id,
                        "text": redacted_text,
                        "text_sha256": content_hash(constraint.text),
                        "redaction_applied": redacted_text != constraint.text,
                    }
                )
            result[task.id] = rows
        return result

    def public_dict(self) -> dict[str, Any]:
        result = dict(self.raw)
        result["resolved"] = {
            "config_path": str(self.path),
            "task_manifest": str(self.task_manifest),
            "output_directory": str(self.experiment_dir),
            "config_hash": self.config_hash,
            "implementation_hash": self.implementation_hash,
            "task_source_hashes": self.task_source_hashes,
            "task_provenance": self.task_provenance,
            "task_constraints": self.task_constraints,
            "plugin_provenance": self.plugin_provenance,
            "docker_config": self.docker.to_dict() if self.docker is not None else None,
            "task_ids": [task.id for task in self.tasks],
            "task_toolsets": self.task_toolsets,
        }
        return result
