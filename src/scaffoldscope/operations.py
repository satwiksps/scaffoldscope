"""Read-only operator views for planned and running experiments."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scaffoldscope.errors import ConfigError
from scaffoldscope.integrity import PERSISTED_RESULT_STATUSES
from scaffoldscope.jsonutil import load_json, load_jsonl
from scaffoldscope.schema import RunConfig
from scaffoldscope.stats import prospective_paired_mde


def _trial_result_path(experiment_dir: Path, trial_id: Any) -> Path:
    if not isinstance(trial_id, str) or not trial_id:
        raise ConfigError("Experiment plan contains a trial without a valid id")
    path = (experiment_dir / "trials" / trial_id / "result.json").resolve()
    trials_root = (experiment_dir / "trials").resolve()
    try:
        path.relative_to(trials_root)
    except ValueError as exc:
        raise ConfigError(f"Experiment plan contains an unsafe trial id: {trial_id!r}") from exc
    return path


def _result_matches_plan(
    value: dict[str, Any],
    trial: dict[str, Any],
    *,
    config_hash: Any,
) -> bool:
    return all(
        value.get(key) == expected
        for key, expected in (
            ("trial_id", trial.get("trial_id")),
            ("trial_hash", trial.get("trial_hash")),
            ("task_id", trial.get("task_id")),
            ("variant_id", trial.get("variant_id")),
            ("replicate", trial.get("replicate")),
            ("config_hash", config_hash),
        )
    )


def experiment_status(experiment_dir: Path) -> dict[str, Any]:
    """Summarize durable per-trial state without trusting a possibly stale aggregate."""

    root = experiment_dir.resolve()
    manifest = load_json(root / "manifest.json")
    plan = load_jsonl(root / "plan.jsonl")
    if not isinstance(manifest, dict):
        raise ConfigError("Experiment manifest must be a JSON object")
    variants = manifest.get("variants")
    if (
        not isinstance(variants, list)
        or not variants
        or not all(isinstance(item, str) and item for item in variants)
    ):
        raise ConfigError("Experiment manifest has no valid variant list")

    statuses: Counter[str] = Counter()
    completed = 0
    started = 0
    token_total = 0
    known_cost = 0.0
    cost_rows = 0
    incomplete_usage = 0
    blocks: dict[tuple[str, int], set[str]] = defaultdict(set)
    malformed_results: list[str] = []
    expected_variants = set(variants)
    planned_blocks: set[tuple[str, int]] = set()

    for trial in plan:
        task_id = trial.get("task_id")
        replicate = trial.get("replicate")
        variant_id = trial.get("variant_id")
        if (
            not isinstance(task_id, str)
            or not task_id
            or not isinstance(replicate, int)
            or isinstance(replicate, bool)
            or variant_id not in expected_variants
        ):
            raise ConfigError("Experiment plan contains an invalid paired-block identity")
        planned_blocks.add((task_id, replicate))

    for trial in plan:
        trial_id = trial.get("trial_id")
        result_path = _trial_result_path(root, trial_id)
        if not result_path.is_file():
            trace_path = result_path.parent / "events.jsonl"
            if trace_path.is_file():
                started += 1
            continue
        value = load_json(result_path)
        if not isinstance(value, dict):
            malformed_results.append(str(trial_id))
            continue
        if not _result_matches_plan(value, trial, config_hash=manifest.get("config_hash")):
            malformed_results.append(str(trial_id))
            continue
        completed += 1
        status = str(value.get("status", "unknown"))
        statuses[status] += 1
        task_id = value.get("task_id")
        replicate = value.get("replicate")
        variant_id = value.get("variant_id")
        if (
            isinstance(task_id, str)
            and isinstance(replicate, int)
            and not isinstance(replicate, bool)
            and isinstance(variant_id, str)
        ):
            blocks[(task_id, replicate)].add(variant_id)
        agent = value.get("agent")
        usage = agent.get("usage") if isinstance(agent, dict) else None
        if isinstance(usage, dict):
            tokens = usage.get("total_tokens")
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                token_total += tokens
            cost = usage.get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                known_cost += float(cost)
                cost_rows += 1
            if usage.get("complete") is False:
                incomplete_usage += 1

    scheduled = len(plan)
    complete_blocks = sum(observed == expected_variants for observed in blocks.values())
    expected_blocks = len(planned_blocks)
    aborted_root = root / "aborted-attempts"
    archived_attempts = (
        sum(1 for path in aborted_root.iterdir() if path.is_dir()) if aborted_root.is_dir() else 0
    )
    return {
        "schema_version": 1,
        "experiment": manifest.get("experiment"),
        "config_hash": manifest.get("config_hash"),
        "experiment_dir": str(root),
        "scheduled_trials": scheduled,
        "completed_trials": completed,
        "started_without_result": started,
        "archived_attempts": archived_attempts,
        "remaining_trials": max(0, scheduled - completed),
        "progress": completed / scheduled if scheduled else 0.0,
        "status_counts": dict(sorted(statuses.items())),
        "complete_paired_blocks": complete_blocks,
        "expected_paired_blocks": expected_blocks,
        "pair_coverage": complete_blocks / expected_blocks if expected_blocks else 0.0,
        "reported_tokens": token_total,
        "reported_cost_usd": known_cost if cost_rows else None,
        "cost_reported_trials": cost_rows,
        "incomplete_usage_trials": incomplete_usage,
        "malformed_result_trials": sorted(malformed_results),
    }


def trial_inventory(
    experiment_dir: Path,
    *,
    status: str | None = None,
    variant: str | None = None,
    task: str | None = None,
) -> list[dict[str, Any]]:
    """Return one stable row per planned trial, including not-yet-run trials."""

    root = experiment_dir.resolve()
    manifest = load_json(root / "manifest.json")
    if not isinstance(manifest, dict):
        raise ConfigError("Experiment manifest must be a JSON object")
    plan = load_jsonl(root / "plan.jsonl")
    allowed_filters = {
        "status": set(PERSISTED_RESULT_STATUSES) | {"planned", "invalid_result"},
        "variant": {value for row in plan if isinstance((value := row.get("variant_id")), str)},
        "task": {value for row in plan if isinstance((value := row.get("task_id")), str)},
    }
    for name, selected in (("status", status), ("variant", variant), ("task", task)):
        if selected is not None and selected not in allowed_filters[name]:
            choices = ", ".join(sorted(allowed_filters[name])) or "none"
            raise ConfigError(f"Unknown {name} filter {selected!r}; available values: {choices}")
    rows: list[dict[str, Any]] = []
    for trial in plan:
        trial_id = trial.get("trial_id")
        result_path = _trial_result_path(root, trial_id)
        result: dict[str, Any] | None = None
        invalid_result = False
        if result_path.is_file():
            loaded = load_json(result_path)
            if not isinstance(loaded, dict):
                raise ConfigError(f"Trial result must be a JSON object: {result_path}")
            if _result_matches_plan(loaded, trial, config_hash=manifest.get("config_hash")):
                result = loaded
            else:
                invalid_result = True
        observed_status = (
            "invalid_result"
            if invalid_result
            else str(result.get("status", "planned"))
            if result
            else "planned"
        )
        agent = result.get("agent") if result else None
        usage = agent.get("usage") if isinstance(agent, dict) else None
        item = {
            "trial_id": trial_id,
            "task_id": trial.get("task_id"),
            "variant_id": trial.get("variant_id"),
            "replicate": trial.get("replicate"),
            "block_index": trial.get("block_index"),
            "order_position": trial.get("order_position"),
            "status": observed_status,
            "solved": result.get("solved") if result else None,
            "infrastructure_valid": result.get("infrastructure_valid") if result else None,
            "evaluation_valid": result.get("evaluation_valid") if result else None,
            "total_tokens": usage.get("total_tokens") if isinstance(usage, dict) else None,
            "cost_usd": usage.get("cost_usd") if isinstance(usage, dict) else None,
            "wall_seconds": result.get("wall_seconds") if result else None,
        }
        if status is not None and observed_status != status:
            continue
        if variant is not None and item["variant_id"] != variant:
            continue
        if task is not None and item["task_id"] != task:
            continue
        rows.append(item)
    return rows


def budget_estimate(config: RunConfig) -> dict[str, Any]:
    """Return conservative grid size, power, and configured-price upper bounds."""

    task_count = len(config.tasks)
    replicate_count = len(config.experiment.replicates)
    variant_count = len(config.variants)
    scheduled = task_count * replicate_count * variant_count
    max_calls = scheduled * config.agent.max_turns
    maximum_tokens = scheduled * config.agent.max_total_tokens

    maximum_cost: float | None = None
    cost_basis: str | None = None
    if config.agent.max_cost_usd is not None:
        maximum_cost = scheduled * config.agent.max_cost_usd
        cost_basis = "sum of configured hard per-trial cost caps"
    elif (
        config.model.input_price_per_million is not None
        and config.model.output_price_per_million is not None
    ):
        token_prices = [
            config.model.input_price_per_million,
            config.model.output_price_per_million,
        ]
        if config.model.cache_read_price_per_million is not None:
            token_prices.append(config.model.cache_read_price_per_million)
        if config.model.cache_write_price_per_million is not None:
            token_prices.append(config.model.cache_write_price_per_million)
        maximum_cost = maximum_tokens * max(token_prices) / 1_000_000
        cost_basis = "conservative configured-price bound at the per-trial token cap"

    warnings: list[dict[str, str]] = []
    if task_count < 20:
        warnings.append(
            {
                "code": "LOW_TASK_COUNT",
                "message": "Fewer than 20 independent tasks; reports remain descriptive.",
            }
        )
    if replicate_count > 1 and not config.model.supports_seed:
        warnings.append(
            {
                "code": "SEED_UNCONFIRMED",
                "message": "The provider is not declared seed-capable; repeated trajectories may duplicate.",
            }
        )
    if maximum_cost is None:
        warnings.append(
            {
                "code": "COST_UNBOUNDED",
                "message": "No hard cost cap or complete configured pricing is available.",
            }
        )

    prospective_mde = prospective_paired_mde(task_count)
    return {
        "schema_version": 1,
        "experiment": config.experiment.name,
        "config_hash": config.config_hash,
        "tasks": task_count,
        "replicates": replicate_count,
        "variants": variant_count,
        "scheduled_trials": scheduled,
        "maximum_model_calls": max_calls,
        "maximum_total_tokens": maximum_tokens,
        "maximum_configured_cost_usd": maximum_cost,
        "cost_bound_basis": cost_basis,
        "prospective_paired_mde": prospective_mde,
        "sesoi": config.experiment.sesoi,
        "mde_within_sesoi": (
            prospective_mde <= config.experiment.sesoi if prospective_mde is not None else None
        ),
        "warnings": warnings,
    }
