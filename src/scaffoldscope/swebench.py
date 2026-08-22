"""Thin interoperability helpers for the official SWE-bench evaluator."""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import (
    atomic_write_json,
    atomic_write_text,
    canonical_json,
    content_hash,
    file_hash,
    load_json,
    load_jsonl,
)

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA256_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _write_jsonl_idempotent(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "".join(canonical_json(row) + "\n" for row in rows)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != content:
            raise ConfigError(f"Refusing to overwrite a different file: {path}")
        return
    atomic_write_text(path, content)


def _strict_outcome(instance_id: str, value: dict[str, Any]) -> dict[str, bool]:
    resolved = value.get("resolved")
    completed = value.get("completed", True)
    if not isinstance(resolved, bool) or not isinstance(completed, bool):
        raise ConfigError(
            f"SWE-bench outcome {instance_id!r} needs boolean completed/resolved fields"
        )
    if resolved and not completed:
        raise ConfigError(f"SWE-bench outcome {instance_id!r} cannot resolve an incomplete run")
    return {"completed": completed, "resolved": resolved}


def import_swebench_manifest(source: Path, repo_cache: Path, output: Path) -> int:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ConfigError("SWE-bench output must differ from the source manifest")
    if source.suffix.lower() == ".jsonl":
        records = load_jsonl(source)
    else:
        value = load_json(source)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ConfigError("SWE-bench input JSON must contain a list of objects")
        records = value
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    cache_root = repo_cache.resolve()
    for record in records:
        instance_id = record.get("instance_id")
        repository = record.get("repo")
        problem = record.get("problem_statement")
        if (
            not isinstance(instance_id, str)
            or not instance_id
            or not isinstance(repository, str)
            or not _REPOSITORY.fullmatch(repository)
            or not isinstance(problem, str)
            or not problem.strip()
        ):
            raise ConfigError(
                "Every SWE-bench row needs a non-empty instance_id, owner/repo, and problem_statement"
            )
        if instance_id in seen:
            raise ConfigError(f"Duplicate SWE-bench instance_id: {instance_id}")
        seen.add(instance_id)
        nested = (cache_root / Path(repository)).resolve()
        flattened = (cache_root / repository.replace("/", "__")).resolve()
        for candidate in (nested, flattened):
            try:
                candidate.relative_to(cache_root)
            except ValueError as exc:
                raise ConfigError(f"Repository path escapes repo cache: {repository}") from exc
        if nested.is_dir():
            workspace = nested
        elif flattened.is_dir():
            workspace = flattened
        else:
            raise ConfigError(
                f"Repository cache has no checkout for {repository!r}; "
                f"expected {nested} or {flattened}"
            )
        rows.append(
            {
                "id": instance_id,
                "repository": repository,
                "workspace": str(workspace.resolve()),
                "base_commit": record.get("base_commit"),
                "problem": problem,
                "constraints": [],
                "test_command": [],
                "metadata": {
                    "FAIL_TO_PASS": record.get("FAIL_TO_PASS", []),
                    "PASS_TO_PASS": record.get("PASS_TO_PASS", []),
                    "source": "swe-bench",
                },
            }
        )
    _write_jsonl_idempotent(output, rows)
    return len(rows)


def export_swebench_predictions(
    experiment_dir: Path,
    output: Path,
    *,
    strategy: str,
    replicate: int,
) -> int:
    experiment_dir = experiment_dir.resolve()
    output = output.resolve()
    try:
        output.relative_to(experiment_dir)
    except ValueError:
        pass
    else:
        raise ConfigError("Prediction output must be outside the experiment directory")
    predictions = _prediction_rows(experiment_dir, strategy=strategy, replicate=replicate)
    _write_jsonl_idempotent(output, predictions)
    return len(predictions)


def _prediction_rows(
    experiment_dir: Path,
    *,
    strategy: str,
    replicate: int,
) -> list[dict[str, Any]]:
    """Validate and render one complete evaluator cell without writing it."""

    rows = load_jsonl(experiment_dir / "episodes.jsonl")
    manifest = load_json(experiment_dir / "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("tasks"), list):
        raise ConfigError("Experiment manifest has no valid task panel")
    selected = [
        row
        for row in rows
        if row.get("variant_id") == strategy
        and isinstance(row.get("replicate"), int)
        and not isinstance(row.get("replicate"), bool)
        and row.get("replicate") == replicate
    ]
    if not selected:
        raise ConfigError(f"No episodes found for strategy={strategy!r}, replicate={replicate}")
    selected_ids = [str(row.get("task_id")) for row in selected]
    expected_ids = [str(task_id) for task_id in manifest["tasks"]]
    if len(set(selected_ids)) != len(selected_ids) or set(selected_ids) != set(expected_ids):
        raise ConfigError(
            "Cannot export an incomplete or duplicated evaluation cell: "
            f"expected={sorted(expected_ids)}, observed={sorted(selected_ids)}"
        )
    predictions: list[dict[str, Any]] = []
    for row in selected:
        artifacts = row.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ConfigError(f"Episode {row.get('task_id')!r} has invalid artifact metadata")
        relative = artifacts.get("patch")
        patch = ""
        if relative is not None:
            if not isinstance(relative, str) or not relative:
                raise ConfigError(f"Episode {row.get('task_id')!r} has an invalid patch path")
            path = (experiment_dir / relative).resolve()
            try:
                path.relative_to(experiment_dir)
            except ValueError as exc:
                raise ConfigError(f"Episode patch escapes the experiment: {relative!r}") from exc
            if not path.is_file():
                raise ConfigError(f"Episode patch is missing: {path}")
            patch = path.read_text(encoding="utf-8", errors="replace")
        predictions.append(
            {
                "instance_id": row["task_id"],
                "model_name_or_path": f"{row.get('model_name')}+scaffoldscope:{strategy}",
                "model_patch": patch,
            }
        )
    return predictions


def export_swebench_matrix(
    experiment_dir: Path,
    output_dir: Path,
    *,
    dataset_name: str,
    split: str = "test",
) -> Path:
    """Export every strategy/replicate cell plus pinned evaluator run identities."""

    experiment_dir = experiment_dir.resolve()
    destination = output_dir.resolve()
    if not dataset_name.strip() or any(character in dataset_name for character in "\r\n\x00"):
        raise ConfigError("dataset_name must be a non-empty single-line value")
    if not split.strip() or any(character in split for character in "\r\n\x00"):
        raise ConfigError("split must be a non-empty single-line value")
    try:
        destination.relative_to(experiment_dir)
    except ValueError:
        pass
    else:
        raise ConfigError("SWE-bench matrix output must be outside the experiment directory")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ConfigError(f"SWE-bench matrix destination must be absent or empty: {destination}")
    manifest = load_json(experiment_dir / "manifest.json")
    if not isinstance(manifest, dict):
        raise ConfigError("Experiment manifest must be a JSON object")
    variants = manifest.get("variants")
    replicates = manifest.get("replicates")
    if (
        not isinstance(variants, list)
        or not variants
        or not all(isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in variants)
    ):
        raise ConfigError("Experiment manifest has no valid variants")
    if not isinstance(replicates, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in replicates
    ):
        raise ConfigError("Experiment manifest has no valid replicates")
    experiment_name = manifest.get("experiment")
    config_hash = manifest.get("config_hash")
    if not isinstance(experiment_name, str) or _IDENTIFIER.fullmatch(experiment_name) is None:
        raise ConfigError("Experiment manifest has no valid experiment name")
    if not isinstance(config_hash, str) or re.fullmatch(r"[0-9a-f]{64}", config_hash) is None:
        raise ConfigError("Experiment manifest has no valid config SHA-256")
    rendered_cells: list[tuple[str, int, str, list[dict[str, Any]], str]] = []
    for strategy in variants:
        for replicate in replicates:
            filename = f"{strategy}--r{replicate}.predictions.jsonl"
            predictions = _prediction_rows(
                experiment_dir,
                strategy=strategy,
                replicate=replicate,
            )
            cell_hash = content_hash(
                {
                    "config_hash": config_hash,
                    "strategy": strategy,
                    "replicate": replicate,
                },
                length=8,
            )
            run_id = (
                f"scaffoldscope-{experiment_name[:24]}-{strategy[:24]}-"
                f"{config_hash[:8]}-{cell_hash}"
            )
            rendered_cells.append((strategy, replicate, filename, predictions, run_id))

    destination.mkdir(parents=True, exist_ok=True)
    cells: list[dict[str, Any]] = []
    commands = [
        "#!/usr/bin/env sh",
        "set -eu",
        'cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"',
        "",
    ]
    for strategy, replicate, filename, predictions, run_id in rendered_cells:
        path = destination / filename
        _write_jsonl_idempotent(path, predictions)
        cells.append(
            {
                "strategy": strategy,
                "replicate": replicate,
                "predictions": filename,
                "prediction_count": len(predictions),
                "predictions_sha256": file_hash(path),
                "evaluator_run_id": run_id,
            }
        )
        command = [
            "python",
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            dataset_name,
            "--split",
            split,
            "--predictions_path",
            filename,
            "--run_id",
            run_id,
        ]
        commands.append(" ".join(shlex.quote(part) for part in command))
    matrix = {
        "schema_version": 1,
        "kind": "scaffoldscope-swebench-matrix",
        "experiment": experiment_name,
        "config_hash": manifest.get("config_hash"),
        "dataset_name": dataset_name,
        "split": split,
        "cells": cells,
    }
    matrix["matrix_hash"] = content_hash(matrix)
    atomic_write_json(destination / "matrix.json", matrix)
    atomic_write_text(destination / "evaluate.sh", "\n".join(commands) + "\n")
    return destination / "matrix.json"


def _official_outcomes(path: Path) -> dict[str, dict[str, bool]]:
    if path.suffix.lower() == ".jsonl":
        rows = load_jsonl(path)
        outcomes: dict[str, dict[str, bool]] = {}
        for row in rows:
            instance_id = row.get("instance_id")
            if not isinstance(instance_id, str) or not instance_id:
                raise ConfigError("Official instance JSONL rows need a non-empty instance_id")
            if instance_id in outcomes:
                raise ConfigError(f"Duplicate official evaluator outcome: {instance_id}")
            outcomes[instance_id] = _strict_outcome(instance_id, row)
        return outcomes
    value = load_json(path)
    if not isinstance(value, dict):
        raise ConfigError("Official SWE-bench results must be a JSON object or instance JSONL")
    if any(key in value for key in ("resolved_ids", "unresolved_ids", "incomplete_ids")):
        outcomes = {}
        for key in (
            "resolved_ids",
            "unresolved_ids",
            "empty_patch_ids",
            "incomplete_ids",
            "error_ids",
        ):
            if not isinstance(value.get(key, []), list):
                raise ConfigError(f"Official SWE-bench results field {key} must be a list")
        for instance_id in value.get("resolved_ids", []):
            normalized = str(instance_id)
            if normalized in outcomes:
                raise ConfigError(f"Duplicate official evaluator outcome: {normalized}")
            outcomes[normalized] = {"completed": True, "resolved": True}
        for instance_id in value.get("unresolved_ids", []):
            normalized = str(instance_id)
            if normalized in outcomes:
                raise ConfigError(f"Duplicate official evaluator outcome: {normalized}")
            outcomes[normalized] = {"completed": True, "resolved": False}
        # The official harness does not execute empty patches, but their outcome is
        # known: they did not resolve the task. Keep them in the intention-to-treat
        # denominator instead of treating them as missing evaluator data.
        for instance_id in value.get("empty_patch_ids", []):
            normalized = str(instance_id)
            if normalized in outcomes:
                raise ConfigError(f"Duplicate official evaluator outcome: {normalized}")
            outcomes[normalized] = {"completed": True, "resolved": False}
        for key in ("incomplete_ids", "error_ids"):
            for instance_id in value.get(key, []):
                normalized = str(instance_id)
                if normalized in outcomes:
                    raise ConfigError(f"Duplicate official evaluator outcome: {normalized}")
                outcomes[normalized] = {"completed": False, "resolved": False}
        return outcomes
    outcomes = {}
    for instance_id, result in value.items():
        if not isinstance(result, dict):
            raise ConfigError(
                "Could not recognize SWE-bench results. Use results.json or instance_results.jsonl."
            )
        outcomes[str(instance_id)] = _strict_outcome(str(instance_id), result)
    return outcomes


def ingest_swebench_results(
    experiment_dir: Path,
    results_path: Path,
    *,
    strategy: str,
    replicate: int,
    evaluator_version: str,
    evaluator_run_id: str,
    image_set_digest: str,
) -> Path:
    """Write an immutable evaluation overlay; raw generation episodes stay unchanged."""

    experiment_dir = experiment_dir.resolve()
    manifest = load_json(experiment_dir / "manifest.json")
    if not isinstance(manifest, dict):
        raise ConfigError("Experiment manifest must be a JSON object")
    if strategy not in manifest.get("variants", []):
        raise ConfigError(f"Strategy {strategy!r} is not in the experiment manifest")
    if replicate not in manifest.get("replicates", []):
        raise ConfigError(f"Replicate {replicate!r} is not in the experiment manifest")
    for label, value in (
        ("evaluator_version", evaluator_version),
        ("evaluator_run_id", evaluator_run_id),
        ("image_set_digest", image_set_digest),
    ):
        if not value.strip():
            raise ConfigError(f"{label} must be non-empty")
        if any(character in value for character in "\r\n\x00"):
            raise ConfigError(f"{label} must be a single-line value")
    if _SHA256_DIGEST.fullmatch(image_set_digest) is None:
        raise ConfigError("image_set_digest must be a SHA-256 digest")
    episodes = load_jsonl(experiment_dir / "episodes.jsonl")
    selected_ids = {
        str(row["task_id"])
        for row in episodes
        if row.get("variant_id") == strategy and int(row.get("replicate", -1)) == replicate
    }
    if not selected_ids:
        raise ConfigError(
            f"No generated episodes found for strategy={strategy!r}, replicate={replicate}"
        )
    outcomes = _official_outcomes(results_path.resolve())
    missing = selected_ids - set(outcomes)
    unexpected = set(outcomes) - selected_ids
    if missing or unexpected:
        raise ConfigError(
            "Evaluator cell does not match the generated cell: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    overlay = {
        "schema_version": 1,
        "kind": "swebench-evaluation-overlay",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_hash": manifest.get("config_hash"),
        "strategy": strategy,
        "replicate": replicate,
        "evaluator_version": evaluator_version,
        "evaluator_run_id": evaluator_run_id,
        "image_set_digest": image_set_digest,
        "source_path": str(results_path.resolve()),
        "source_sha256": file_hash(results_path.resolve()),
        "outcomes": {key: outcomes[key] for key in sorted(outcomes)},
    }
    identity = dict(overlay)
    identity.pop("created_at")
    digest = content_hash(identity)
    overlay["overlay_hash"] = digest
    directory = _external_evaluations_directory(experiment_dir, create=True)
    destination = directory / f"{strategy}--r{replicate}--{digest[:12]}.json"
    if destination.exists():
        existing = load_json(destination)
        if existing.get("overlay_hash") != digest:
            raise ConfigError(f"Evaluation overlay hash collision at {destination}")
        return destination
    atomic_write_json(destination, overlay)
    return destination


def apply_external_evaluations(
    experiment_dir: Path, episodes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    directory = _external_evaluations_directory(experiment_dir, create=False)
    if not directory.exists():
        return episodes
    overlays: dict[tuple[str, int, str], dict[str, Any]] = {}
    manifest = load_json(experiment_dir / "manifest.json")
    for path in sorted(directory.glob("*.json")):
        overlay = load_json(path)
        if not isinstance(overlay, dict):
            raise ConfigError(f"External evaluation overlay is not an object: {path}")
        declared_hash = overlay.get("overlay_hash")
        identity = dict(overlay)
        identity.pop("overlay_hash", None)
        identity.pop("created_at", None)
        if not isinstance(declared_hash, str) or content_hash(identity) != declared_hash:
            raise ConfigError(f"External evaluation overlay hash mismatch: {path}")
        if overlay.get("config_hash") != manifest.get("config_hash"):
            raise ConfigError(f"External evaluation config hash mismatch: {path}")
        strategy = str(overlay["strategy"])
        replicate = int(overlay["replicate"])
        outcomes = overlay.get("outcomes")
        if not isinstance(outcomes, dict):
            raise ConfigError(f"External evaluation overlay has invalid outcomes: {path}")
        for task_id, outcome in outcomes.items():
            if not isinstance(outcome, dict):
                raise ConfigError(f"External evaluation overlay has invalid outcome: {path}")
            strict = _strict_outcome(str(task_id), outcome)
            key = (strategy, replicate, str(task_id))
            normalized = {
                **strict,
                "evaluator_version": overlay["evaluator_version"],
                "evaluator_run_id": overlay["evaluator_run_id"],
                "image_set_digest": overlay["image_set_digest"],
                "overlay_hash": overlay["overlay_hash"],
            }
            if key in overlays and overlays[key] != normalized:
                raise ConfigError(f"Conflicting external evaluations for {key}")
            overlays[key] = normalized
    joined: list[dict[str, Any]] = []
    for original in episodes:
        row = dict(original)
        key = (str(row.get("variant_id")), int(row.get("replicate", -1)), str(row.get("task_id")))
        outcome = overlays.get(key)
        if outcome is not None:
            completed = bool(outcome["completed"])
            resolved = bool(outcome["resolved"])
            row["external_evaluation"] = outcome
            row["evaluation_valid"] = completed
            row["solved"] = resolved if completed else None
            adherence = row.get("evaluation", {}).get("behavioral_adherence")
            row["governed_solved"] = (
                bool(resolved and (adherence is None or adherence == 1.0)) if completed else None
            )
            if not completed:
                row["status"] = "external_evaluation_incomplete"
            elif resolved:
                row["status"] = "resolved"
            elif row.get("status") == "awaiting_external_evaluation":
                row["status"] = "unresolved"
        joined.append(row)
    return joined


def _external_evaluations_directory(experiment_dir: Path, *, create: bool) -> Path:
    """Return the canonical overlay directory without following reparse escapes."""

    root = experiment_dir.resolve()
    directory = experiment_dir / "external-evaluations"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ConfigError(f"External evaluation path is not a regular directory: {directory}")
    expected = root / "external-evaluations"
    if directory.resolve(strict=False) != expected:
        raise ConfigError(f"External evaluation directory escapes experiment: {directory}")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory
