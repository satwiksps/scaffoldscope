"""Blocked experiment planning, resumable execution, and result persistence."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import threading
import time
import traceback
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from scaffoldscope import __version__
from scaffoldscope.agent import CodingAgent
from scaffoldscope.context import make_policy
from scaffoldscope.docker_sandbox import DockerSandbox, docker_preflight
from scaffoldscope.errors import ConfigError, SandboxError
from scaffoldscope.events import EventLog
from scaffoldscope.integrity import result_semantic_issues, trace_lifecycle_issues
from scaffoldscope.jsonutil import (
    atomic_write_json,
    atomic_write_text,
    canonical_json,
    content_hash,
    load_json,
    load_jsonl,
    write_jsonl,
)
from scaffoldscope.locking import experiment_lock
from scaffoldscope.models import make_model
from scaffoldscope.redact import redact
from scaffoldscope.sandbox import (
    LocalSandbox,
    RestrictedSandbox,
    WorkspaceSandbox,
    prepare_workspace,
)
from scaffoldscope.schema import BUILTIN_TOOL_NAMES, RunConfig, TaskSpec, VariantConfig
from scaffoldscope.tokenization import Char4TokenCounter

_VARIANT_ORDER_ALGORITHM = "sha256-rank-v1"


def _ensure_regular_directory(
    path: Path,
    *,
    label: str,
    expected_parent: Path | None = None,
) -> Path:
    """Create a directory only when its resolved identity is the declared path."""

    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ConfigError(f"{label} is not a regular directory: {path}")
    if expected_parent is None:
        expected = path
    else:
        expected_parent_resolved = expected_parent.resolve()
        expected = expected_parent_resolved / path.name
    resolved = path.resolve(strict=False)
    if resolved != expected:
        raise ConfigError(f"{label} resolves outside its declared location: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@dataclass(frozen=True)
class TrialSpec:
    id: str
    hash: str
    task_id: str
    variant_id: str
    replicate: int
    block_index: int
    order_position: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "trial_id": self.id,
            "trial_hash": self.hash,
            "task_id": self.task_id,
            "variant_id": self.variant_id,
            "replicate": self.replicate,
            "block_index": self.block_index,
            "order_position": self.order_position,
        }


@dataclass(frozen=True)
class RunSummary:
    experiment_dir: Path
    scheduled: int
    completed: int
    skipped: int
    failed: int


def _trial_spec(
    config: RunConfig,
    *,
    task: TaskSpec,
    variant: VariantConfig,
    replicate: int,
    block_index: int,
    order_position: int,
) -> TrialSpec:
    identity = {
        "config_hash": config.config_hash,
        "task_id": task.id,
        "variant_id": variant.id,
        "replicate": replicate,
    }
    digest = content_hash(identity)
    return TrialSpec(
        id=f"{task.id}--{variant.id}--r{replicate}--{digest[:8]}",
        hash=digest,
        task_id=task.id,
        variant_id=variant.id,
        replicate=replicate,
        block_index=block_index,
        order_position=order_position,
    )


def build_plan(
    config: RunConfig,
) -> list[TrialSpec]:
    plan: list[TrialSpec] = []
    block_index = 0
    for task in config.tasks:
        for replicate in config.experiment.replicates:
            ordered = list(config.variants)
            if config.experiment.randomize_variant_order:
                ordered.sort(
                    key=lambda variant: (
                        content_hash(
                            {
                                "algorithm": _VARIANT_ORDER_ALGORITHM,
                                "config_hash": config.config_hash,
                                "task_id": task.id,
                                "replicate": replicate,
                                "variant_id": variant.id,
                            }
                        ),
                        variant.id,
                    )
                )
            for position, variant in enumerate(ordered):
                plan.append(
                    _trial_spec(
                        config,
                        task=task,
                        variant=variant,
                        replicate=replicate,
                        block_index=block_index,
                        order_position=position,
                    )
                )
            block_index += 1
    return plan


def _git_commit(path: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            shell=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _runtime_identity() -> dict[str, str]:
    identity = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "operating_system": platform.system(),
        "machine": platform.machine(),
        "token_counter": "char4-v1",
    }
    return {**identity, "hash": content_hash(identity)}


def _validated_runtime_identity(value: Any) -> dict[str, str]:
    required = {
        "python_implementation",
        "python_version",
        "operating_system",
        "machine",
        "token_counter",
        "hash",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or not all(
            isinstance(key, str) and isinstance(item, str) and item for key, item in value.items()
        )
    ):
        raise ConfigError("Runtime identity has missing, empty, or unknown fields")
    normalized = {str(key): str(item) for key, item in value.items()}
    unhashed = {key: item for key, item in normalized.items() if key != "hash"}
    if normalized["hash"] != content_hash(unhashed):
        raise ConfigError("Runtime identity hash is invalid")
    return normalized


def _validated_docker_runtime(
    config: RunConfig,
    value: Any,
) -> dict[str, str] | None:
    if value is None:
        return None
    if config.sandbox.backend != "docker" or config.docker is None:
        raise ConfigError("Docker runtime provenance is invalid for the local sandbox")
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ConfigError("Docker runtime provenance must contain string fields")
    required = {
        "declared_image",
        "image_id",
        "configured_platform",
        "image_platform",
        "hash",
    }
    if set(value) != required:
        raise ConfigError("Docker runtime provenance has missing or unknown fields")
    normalized = {str(key): str(item) for key, item in value.items()}
    unhashed = {key: item for key, item in normalized.items() if key != "hash"}
    if normalized["hash"] != content_hash(unhashed):
        raise ConfigError("Docker runtime provenance hash is invalid")
    if normalized["declared_image"] != config.docker.image:
        raise ConfigError("Docker runtime provenance does not match the configured image")
    configured_platform = config.docker.platform or "default"
    if normalized["configured_platform"] != configured_platform:
        raise ConfigError("Docker runtime provenance does not match the configured platform")
    return normalized


def prepare_experiment(
    config: RunConfig,
    plan: list[TrialSpec],
    *,
    docker_runtime: dict[str, str] | None = None,
    runtime_identity: dict[str, str] | None = None,
) -> None:
    docker_runtime = _validated_docker_runtime(config, docker_runtime)
    if runtime_identity is not None:
        runtime_identity = _validated_runtime_identity(runtime_identity)
    directory = config.experiment_dir
    output_root = config.experiment.output_dir
    _ensure_regular_directory(output_root, label="Experiment output root")
    _ensure_regular_directory(
        directory,
        label="Experiment directory",
        expected_parent=output_root,
    )
    resolved_path = directory / "config.resolved.json"
    plan_path = directory / "plan.jsonl"
    pricing_path = directory / "pricing.json"
    manifest_path = directory / "manifest.json"
    for evidence_path in (resolved_path, plan_path, pricing_path, manifest_path):
        if evidence_path.is_symlink():
            raise ConfigError(f"Experiment identity file must not be a symlink: {evidence_path}")
    resolved_value = config.public_dict()
    resolved_config_hash = content_hash(resolved_value)
    if resolved_path.exists() and load_json(resolved_path) != resolved_value:
        raise ConfigError(f"Existing resolved config differs in {directory}")
    if not resolved_path.exists():
        atomic_write_json(resolved_path, resolved_value)
    plan_rows = [trial.to_dict() for trial in plan]
    if plan_path.exists() and load_jsonl(plan_path) != plan_rows:
        raise ConfigError(f"Existing trial plan differs in {directory}")
    if not plan_path.exists():
        write_jsonl(plan_path, plan_rows)
    pricing = {
        "model": config.model.name,
        "input_price_per_million": config.model.input_price_per_million,
        "output_price_per_million": config.model.output_price_per_million,
        "cache_read_price_per_million": config.model.cache_read_price_per_million,
        "cache_write_price_per_million": config.model.cache_write_price_per_million,
        "currency": "USD",
        "source": "experiment configuration; user-supplied snapshot",
    }
    pricing_value = {**pricing, "hash": content_hash(pricing)}
    if pricing_path.exists() and load_json(pricing_path) != pricing_value:
        raise ConfigError(f"Existing pricing snapshot differs in {directory}")
    if not pricing_path.exists():
        atomic_write_json(pricing_path, pricing_value)
    manifest = {
        "schema_version": 1,
        "integrity_version": 1,
        "variant_order_algorithm": _VARIANT_ORDER_ALGORITHM,
        "scaffoldscope_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment": config.experiment.name,
        "config_hash": config.config_hash,
        "resolved_config_hash": resolved_config_hash,
        "implementation_hash": config.implementation_hash,
        "task_source_hashes": config.task_source_hashes,
        "task_provenance": config.task_provenance,
        "task_constraints": config.task_constraints,
        "code_commit": _git_commit(config.path.parent),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "token_counter": "char4-v1",
        "runtime_identity": runtime_identity,
        "model_provider": config.model.provider,
        "model_name": config.model.name,
        "provider_seed_supported": config.model.supports_seed,
        "sandbox_backend": config.sandbox.backend,
        "docker": config.docker.to_dict() if config.docker is not None else None,
        "docker_runtime": docker_runtime,
        "plugins": config.plugin_provenance,
        "tasks": [task.id for task in config.tasks],
        "task_toolsets": config.task_toolsets,
        "variants": [variant.id for variant in config.variants],
        "variant_treatments": {
            variant.id: {
                "context_policy": variant.policy,
                "tools": list(variant.tools) if variant.tools is not None else "default",
                "instructions_sha256": (
                    content_hash(variant.instructions) if variant.instructions is not None else None
                ),
                "plugin_options": variant.plugin_options,
            }
            for variant in config.variants
        },
        "replicates": list(config.experiment.replicates),
        "trial_count": len(plan),
        "pairing_unit": "task_id + replicate",
        "warning": (
            "Scripted-provider runs validate the engine only and are not evidence of model capability."
            if config.model.provider == "scripted"
            else None
        ),
    }
    if manifest_path.exists():
        existing_manifest = load_json(manifest_path)
        if not isinstance(existing_manifest, dict):
            raise ConfigError(f"Existing experiment manifest is invalid in {directory}")
        identity_fields = (
            "schema_version",
            "integrity_version",
            "variant_order_algorithm",
            "scaffoldscope_version",
            "experiment",
            "config_hash",
            "resolved_config_hash",
            "implementation_hash",
            "task_source_hashes",
            "task_provenance",
            "task_constraints",
            "token_counter",
            "model_provider",
            "model_name",
            "provider_seed_supported",
            "sandbox_backend",
            "docker",
            "plugins",
            "tasks",
            "task_toolsets",
            "variants",
            "variant_treatments",
            "replicates",
            "trial_count",
            "pairing_unit",
            "warning",
        )
        if any(existing_manifest.get(key) != manifest.get(key) for key in identity_fields):
            raise ConfigError(f"Existing experiment manifest differs in {directory}")
        existing_runtime_identity = existing_manifest.get("runtime_identity")
        if existing_runtime_identity is not None:
            existing_runtime_identity = _validated_runtime_identity(existing_runtime_identity)
        if runtime_identity is not None:
            if (
                existing_runtime_identity is not None
                and existing_runtime_identity != runtime_identity
            ):
                raise ConfigError("Runtime identity differs from the existing experiment manifest")
            if existing_runtime_identity is None:
                result_paths = directory.glob("trials/*/result.json")
                if any(path.is_file() for path in result_paths):
                    raise ConfigError(
                        "Cannot backfill runtime identity after trials have produced results"
                    )
                existing_manifest["runtime_identity"] = runtime_identity
                atomic_write_json(manifest_path, existing_manifest)
        existing_docker_runtime = _validated_docker_runtime(
            config,
            existing_manifest.get("docker_runtime"),
        )
        if docker_runtime is not None:
            if existing_docker_runtime is not None and existing_docker_runtime != docker_runtime:
                raise ConfigError(
                    "Docker runtime provenance differs from the existing experiment manifest"
                )
            if existing_docker_runtime is None:
                result_paths = directory.glob("trials/*/result.json")
                if any(path.is_file() for path in result_paths):
                    raise ConfigError(
                        "Cannot backfill Docker provenance after trials have produced results"
                    )
                existing_manifest["docker_runtime"] = docker_runtime
                atomic_write_json(manifest_path, existing_manifest)
    else:
        atomic_write_json(manifest_path, manifest)


def _existing_result(
    path: Path,
    config: RunConfig,
    trial: TrialSpec,
    task: TaskSpec,
    variant: VariantConfig,
    docker_runtime: dict[str, str] | None,
    runtime_identity: dict[str, str],
    aggregate_results: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = load_json(path)
    except ConfigError:
        return None
    if not isinstance(value, dict):
        return None
    declared_tools = [
        name
        for name in BUILTIN_TOOL_NAMES
        if (name != "run_tests" or task.test_command)
        and (variant.tools is None or name in variant.tools)
    ]
    expected = {
        "schema_version": 1,
        "trial_id": trial.id,
        "trial_hash": trial.hash,
        "task_id": trial.task_id,
        "variant_id": trial.variant_id,
        "replicate": trial.replicate,
        "block_index": trial.block_index,
        "order_position": trial.order_position,
        "scaffoldscope_version": __version__,
        "experiment": config.experiment.name,
        "config_hash": config.config_hash,
        "implementation_hash": config.implementation_hash,
        "task_source_hash": config.task_source_hashes[task.id],
        "task_repository": task.repository,
        "task_base_commit": task.base_commit,
        "variant_policy": variant.policy,
        "model_provider": config.model.provider,
        "model_name": config.model.name,
        "sandbox_backend": config.sandbox.backend,
        "docker_image": config.docker.image if config.docker is not None else None,
        "docker_image_id": (docker_runtime.get("image_id") if docker_runtime is not None else None),
        "docker_image_platform": (
            docker_runtime.get("image_platform") if docker_runtime is not None else None
        ),
        "runtime_identity": runtime_identity,
        "plugins": config.plugin_provenance,
        "variant_tools": declared_tools,
        "variant_instructions_sha256": (
            content_hash(variant.instructions) if variant.instructions is not None else None
        ),
        "provider_seed_supported": config.model.supports_seed,
    }
    if not set(expected).issubset(value) or any(
        content_hash(value[key]) != content_hash(expected_value)
        for key, expected_value in expected.items()
    ):
        return None
    trial_dir = path.parent
    if trial_dir.is_symlink() or not trial_dir.is_dir():
        return None
    artifacts = value.get("artifacts")
    hashes = value.get("artifact_hashes")
    if not isinstance(artifacts, dict) or not isinstance(hashes, dict):
        return None
    expected_artifact_paths = {
        "trace": f"trials/{trial.id}/events.jsonl",
        "patch": f"trials/{trial.id}/patch.diff",
        "result": f"trials/{trial.id}/result.json",
        "workspace": f"trials/{trial.id}/workspace",
    }
    if not {"trace", "result"}.issubset(artifacts) or any(
        key not in expected_artifact_paths or relative != expected_artifact_paths[key]
        for key, relative in artifacts.items()
    ):
        return None
    trace_path = trial_dir / "events.jsonl"
    if trace_path.is_symlink() or not trace_path.is_file():
        return None
    patch_path = trial_dir / "patch.diff"
    if patch_path.is_symlink():
        return None
    expected_hashes = {
        "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
    }
    if patch_path.is_file():
        expected_hashes["patch_sha256"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    if hashes != expected_hashes:
        return None
    if artifacts.get("patch") is not None:
        if not patch_path.is_file():
            return None
        patch_hash = expected_hashes["patch_sha256"]
        if (
            value.get("patch_sha256") != patch_hash
            or value.get("patch_bytes") != patch_path.stat().st_size
        ):
            return None
    try:
        trace_rows = load_jsonl(trace_path)
    except ConfigError:
        return None
    has_normal_patch_evidence = (
        "patch" in artifacts
        or "patch_sha256" in value
        or "patch_bytes" in value
        or "evaluation" in value
    )
    if result_semantic_issues(value) or trace_lifecycle_issues(
        trace_rows,
        expected_trial=trial.to_dict(),
        result=value,
        require_artifact_events=has_normal_patch_evidence,
        constraints=config.task_constraints[task.id],
    ):
        return None
    aggregate_matches = aggregate_results.get(trial.id, []) if aggregate_results is not None else []
    if aggregate_matches and (
        len(aggregate_matches) != 1 or canonical_json(aggregate_matches[0]) != canonical_json(value)
    ):
        return None
    return value


def _make_workspace_sandbox(
    config: RunConfig,
    workspace: Path,
    task: TaskSpec,
    docker_runtime: dict[str, str] | None,
) -> WorkspaceSandbox:
    """Construct the configured backend from preflight-verified provenance."""

    if config.sandbox.backend == "local":
        if docker_runtime is not None:
            raise ConfigError("Local sandbox cannot receive Docker runtime provenance")
        return LocalSandbox(workspace, task, config.sandbox)
    if config.docker is None:
        raise ConfigError("Docker sandbox configuration is missing")
    if docker_runtime is None:
        raise ConfigError("Docker sandbox requires successful runtime preflight")
    image_id = docker_runtime.get("image_id")
    if not isinstance(image_id, str) or not image_id:
        raise ConfigError("Docker preflight returned no resolved image ID")
    return DockerSandbox(
        workspace,
        task,
        config.sandbox,
        config.docker,
        resolved_image=image_id,
    )


def _run_trial(
    config: RunConfig,
    trial: TrialSpec,
    task: TaskSpec,
    variant: VariantConfig,
    docker_runtime: dict[str, str] | None,
    runtime_identity: dict[str, str],
    aggregate_results: dict[str, list[dict[str, Any]]] | None,
) -> tuple[dict[str, Any], bool]:
    trials_root = config.experiment_dir / "trials"
    _ensure_regular_directory(
        trials_root,
        label="Trials root",
        expected_parent=config.experiment_dir,
    )
    trial_dir = trials_root / trial.id
    _ensure_regular_directory(
        trial_dir,
        label="Trial directory",
        expected_parent=trials_root,
    )
    result_path = trial_dir / "result.json"
    existing = _existing_result(
        result_path,
        config,
        trial,
        task,
        variant,
        docker_runtime,
        runtime_identity,
        aggregate_results,
    )
    if existing is not None:
        return existing, True
    if trial_dir.exists() and any(trial_dir.iterdir()):
        aborted_root = config.experiment_dir / "aborted-attempts"
        _ensure_regular_directory(
            aborted_root,
            label="Aborted-attempt root",
            expected_parent=config.experiment_dir,
        )
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        trial_dir.rename(aborted_root / f"{trial.id}--{suffix}")
    trial_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trial_dir / "events.jsonl"
    events = EventLog(trace_path)
    started_wall = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    events.emit("trial_started", trial.to_dict())
    workspace = trial_dir / "workspace"
    counter = Char4TokenCounter()
    declared_tools = tuple(
        name
        for name in BUILTIN_TOOL_NAMES
        if (name != "run_tests" or task.test_command)
        and (variant.tools is None or name in variant.tools)
    )
    docker_image_id = docker_runtime.get("image_id") if docker_runtime is not None else None
    docker_image_platform = (
        docker_runtime.get("image_platform") if docker_runtime is not None else None
    )
    result_base = {
        **trial.to_dict(),
        "scaffoldscope_version": __version__,
        "experiment": config.experiment.name,
        "config_hash": config.config_hash,
        "implementation_hash": config.implementation_hash,
        "task_source_hash": config.task_source_hashes[task.id],
        "task_repository": task.repository,
        "task_base_commit": task.base_commit,
        "variant_policy": variant.policy,
        "model_provider": config.model.provider,
        "model_name": config.model.name,
        "sandbox_backend": config.sandbox.backend,
        "docker_image": config.docker.image if config.docker is not None else None,
        "docker_image_id": docker_image_id,
        "docker_image_platform": docker_image_platform,
        "runtime_identity": runtime_identity,
        "plugins": config.plugin_provenance,
        "variant_instructions_sha256": (
            content_hash(variant.instructions) if variant.instructions is not None else None
        ),
        "provider_seed_supported": config.model.supports_seed,
        "started_at": started_at,
    }
    try:
        prepare_workspace(task, workspace, recreate=True)
        sandbox = _make_workspace_sandbox(config, workspace, task, docker_runtime)
        if variant.tools is not None:
            sandbox = RestrictedSandbox(sandbox, variant.tools)
        failed_attempt_count = 0

        def record_failed_attempt(payload: dict[str, Any]) -> None:
            nonlocal failed_attempt_count
            failed_attempt_count += 1
            events.emit("model_attempt_failed", payload)

        model = make_model(
            config.model,
            task,
            counter,
            event_callback=record_failed_attempt,
            registry=config.plugin_registry,
        )
        policy = make_policy(variant, counter, config.plugin_registry)
        agent = CodingAgent(
            task=task,
            seed=trial.replicate,
            model=model,
            model_config=config.model,
            agent_config=config.agent,
            policy=policy,
            sandbox=sandbox,
            counter=counter,
            events=events,
            failed_attempt_count=lambda: failed_attempt_count,
        )
        outcome = agent.run()
        evaluation = sandbox.evaluate()
        outcome_payload = cast(dict[str, Any], redact(outcome.to_dict()))
        evaluation_payload = cast(dict[str, Any], redact(evaluation.to_dict()))
        events.emit("evaluation_finished", evaluation_payload)
        patch = sandbox.patch()
        patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        events.emit(
            "patch_captured",
            {
                "patch_sha256": patch_hash,
                "patch_bytes": len(patch.encode("utf-8")),
            },
        )
        atomic_write_text(trial_dir / "patch.diff", patch)
        evaluation_valid = evaluation.passed is not None
        solved: bool | None = (
            bool(evaluation.passed and outcome.status == "completed") if evaluation_valid else None
        )
        adherence = evaluation.behavioral_adherence
        governed_solved: bool | None = (
            bool(solved and (adherence is None or adherence == 1.0)) if evaluation_valid else None
        )
        if not evaluation_valid:
            status = "awaiting_external_evaluation"
        elif outcome.status != "completed":
            status = outcome.status
        elif solved:
            status = "resolved"
        else:
            status = "unresolved"
        result = {
            **result_base,
            "variant_tools": list(sandbox.available_tools),
            "status": status,
            "infrastructure_valid": True,
            "evaluation_valid": evaluation_valid,
            "solved": solved,
            "governed_solved": governed_solved,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": time.perf_counter() - started_wall,
            "agent": outcome_payload,
            "evaluation": evaluation_payload,
            "patch_sha256": patch_hash,
            "patch_bytes": len(patch.encode("utf-8")),
            "artifacts": {
                "trace": f"trials/{trial.id}/events.jsonl",
                "patch": f"trials/{trial.id}/patch.diff",
                "result": f"trials/{trial.id}/result.json",
                "workspace": f"trials/{trial.id}/workspace",
            },
        }
    except (ConfigError, SandboxError, OSError, subprocess.SubprocessError) as exc:
        error_payload = cast(
            dict[str, Any],
            redact({"type": type(exc).__name__, "message": str(exc)}),
        )
        result = {
            **result_base,
            "variant_tools": list(declared_tools),
            "status": "infrastructure_error",
            "infrastructure_valid": False,
            "evaluation_valid": False,
            "solved": None,
            "governed_solved": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": time.perf_counter() - started_wall,
            "error": error_payload,
            "artifacts": {
                "trace": f"trials/{trial.id}/events.jsonl",
                "result": f"trials/{trial.id}/result.json",
            },
        }
        events.emit(
            "trial_error",
            {
                "error_type": type(exc).__name__,
                "message": error_payload["message"],
                "traceback": traceback.format_exc(),
            },
        )
    except Exception as exc:
        error_payload = cast(
            dict[str, Any],
            redact({"type": type(exc).__name__, "message": str(exc)}),
        )
        result = {
            **result_base,
            "variant_tools": list(declared_tools),
            "status": "harness_error",
            "infrastructure_valid": False,
            "evaluation_valid": False,
            "solved": None,
            "governed_solved": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": time.perf_counter() - started_wall,
            "error": error_payload,
            "artifacts": {
                "trace": f"trials/{trial.id}/events.jsonl",
                "result": f"trials/{trial.id}/result.json",
            },
        }
        events.emit(
            "harness_error",
            {
                "error_type": type(exc).__name__,
                "message": error_payload["message"],
                "traceback": traceback.format_exc(),
            },
        )
    events.emit(
        "trial_finished",
        {
            "status": result["status"],
            "solved": result["solved"],
            "wall_seconds": result["wall_seconds"],
        },
    )
    artifact_hashes = {"trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest()}
    patch_path = trial_dir / "patch.diff"
    if patch_path.is_file():
        artifact_hashes["patch_sha256"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    result["artifact_hashes"] = artifact_hashes
    atomic_write_json(result_path, result)
    return result, False


def _aggregate(config: RunConfig, plan: Iterable[TrialSpec]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trial in plan:
        result_path = config.experiment_dir / "trials" / trial.id / "result.json"
        if result_path.is_file():
            value = load_json(result_path)
            if isinstance(value, dict):
                rows.append(value)
    write_jsonl(config.experiment_dir / "episodes.jsonl", rows)
    return rows


def _run_block(
    config: RunConfig,
    trials: list[TrialSpec],
    task_map: dict[str, TaskSpec],
    variant_map: dict[str, VariantConfig],
    docker_runtime: dict[str, str] | None,
    runtime_identity: dict[str, str],
    aggregate_results: dict[str, list[dict[str, Any]]] | None,
    cancel_event: threading.Event,
) -> list[tuple[dict[str, Any], bool]]:
    """Run variants sequentially inside a paired block to avoid self-contention."""

    results: list[tuple[dict[str, Any], bool]] = []
    for trial in sorted(trials, key=lambda item: item.order_position):
        if cancel_event.is_set():
            break
        results.append(
            _run_trial(
                config,
                trial,
                task_map[trial.task_id],
                variant_map[trial.variant_id],
                docker_runtime,
                runtime_identity,
                aggregate_results,
            )
        )
    return results


def _run_experiment_unlocked(
    config: RunConfig,
    *,
    dry_run: bool = False,
) -> RunSummary:
    if (
        not dry_run
        and config.model.provider == "openai_compatible"
        and config.model.requires_api_key
        and not os.environ.get(config.model.api_key_env)
    ):
        raise ConfigError(
            f"Environment variable {config.model.api_key_env} is required to run this experiment"
        )
    docker_runtime: dict[str, str] | None = None
    runtime_identity = None if dry_run else _runtime_identity()
    if not dry_run and config.sandbox.backend == "docker":
        if config.docker is None:
            raise ConfigError("Docker sandbox configuration is missing")
        observed = docker_preflight(config.docker)
        docker_runtime = {**observed, "hash": content_hash(observed)}
    plan = build_plan(config)
    prepare_experiment(
        config,
        plan,
        docker_runtime=docker_runtime,
        runtime_identity=runtime_identity,
    )
    if dry_run:
        return RunSummary(config.experiment_dir, len(plan), 0, 0, 0)
    if runtime_identity is None:  # pragma: no cover - narrowed by dry_run above
        raise AssertionError("runtime identity must be pinned before trial execution")
    aggregate_results: dict[str, list[dict[str, Any]]] | None = None
    episodes_path = config.experiment_dir / "episodes.jsonl"
    if not episodes_path.is_symlink() and episodes_path.is_file():
        try:
            aggregate_rows: list[dict[str, Any]] | None = load_jsonl(episodes_path)
        except ConfigError:
            aggregate_rows = None
        if aggregate_rows is not None:
            aggregate_results = {}
            for row in aggregate_rows:
                trial_id = row.get("trial_id")
                if isinstance(trial_id, str):
                    aggregate_results.setdefault(trial_id, []).append(row)
    task_map = {task.id: task for task in config.tasks}
    variant_map = {variant.id: variant for variant in config.variants}
    completed = 0
    skipped = 0
    failed = 0
    if config.experiment.max_workers == 1:
        for trial in plan:
            result, was_skipped = _run_trial(
                config,
                trial,
                task_map[trial.task_id],
                variant_map[trial.variant_id],
                docker_runtime,
                runtime_identity,
                aggregate_results,
            )
            skipped += int(was_skipped)
            completed += int(not was_skipped)
            failed += int(not result.get("infrastructure_valid", False))
    else:
        blocks: dict[int, list[TrialSpec]] = {}
        for trial in plan:
            blocks.setdefault(trial.block_index, []).append(trial)
        futures: dict[Future[list[tuple[dict[str, Any], bool]]], int] = {}
        cancel_event = threading.Event()
        executor = ThreadPoolExecutor(max_workers=config.experiment.max_workers)
        try:
            for block_index, block_trials in blocks.items():
                futures[
                    executor.submit(
                        _run_block,
                        config,
                        block_trials,
                        task_map,
                        variant_map,
                        docker_runtime,
                        runtime_identity,
                        aggregate_results,
                        cancel_event,
                    )
                ] = block_index
            for future in as_completed(futures):
                for result, was_skipped in future.result():
                    skipped += int(was_skipped)
                    completed += int(not was_skipped)
                    failed += int(not result.get("infrastructure_valid", False))
        except BaseException:
            cancel_event.set()
            for future in futures:
                future.cancel()
            # Keep the experiment lock until every already-running block has
            # stopped writing.  Releasing it early lets a second process enter
            # while interrupted workers are still producing trial evidence.
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
    _aggregate(config, plan)
    return RunSummary(config.experiment_dir, len(plan), completed, skipped, failed)


def run_experiment(
    config: RunConfig,
    *,
    dry_run: bool = False,
) -> RunSummary:
    """Plan or run one experiment while excluding concurrent writers."""

    with experiment_lock(config.experiment_dir):
        return _run_experiment_unlocked(config, dry_run=dry_run)


def clean_workspaces(experiment_dir: Path) -> int:
    """Remove generated active/archived workspaces, retaining evidence artifacts."""

    experiment_dir = experiment_dir.resolve()
    removed = 0
    for directory_name in ("trials", "aborted-attempts"):
        attempts_root = experiment_dir / directory_name
        if attempts_root.is_symlink() or not attempts_root.is_dir():
            continue
        resolved_attempts_root = attempts_root.resolve()
        if resolved_attempts_root.parent != experiment_dir:
            continue
        for attempt_dir in attempts_root.iterdir():
            if attempt_dir.is_symlink() or not attempt_dir.is_dir():
                continue
            resolved_attempt = attempt_dir.resolve()
            if resolved_attempt.parent != resolved_attempts_root:
                continue
            removed_attempt = False
            for generated_name in ("workspace", "test-home", "test-temp"):
                generated = attempt_dir / generated_name
                if generated.is_symlink() or not generated.is_dir():
                    continue
                if generated.resolve().parent != resolved_attempt:
                    continue
                shutil.rmtree(generated)
                removed_attempt = True
            removed += int(removed_attempt)
    return removed
