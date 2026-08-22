from __future__ import annotations

import csv
import html
import io
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from scaffoldscope.errors import ConfigError
from scaffoldscope.integrity import (
    parse_utc_timestamp,
    result_semantic_issues,
    trace_lifecycle_issues,
)
from scaffoldscope.jsonutil import (
    atomic_write_json,
    atomic_write_text,
    content_hash,
    file_hash,
    load_json,
    load_jsonl,
)
from scaffoldscope.redact import redact_text
from scaffoldscope.schema import BUILTIN_TOOL_NAMES
from scaffoldscope.stats import (
    RESAMPLING_ALGORITHM,
    bootstrap_mean_interval,
    empirical_mde,
    finite,
    mean,
    paired_sign_flip_pvalue,
    percentile,
    prospective_paired_mde,
)
from scaffoldscope.swebench import apply_external_evaluations

_VARIANT_ORDER_ALGORITHM = "sha256-rank-v1"


def _nested(row: dict[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _task_means(
    rows: Iterable[dict[str, Any]], value: Callable[[dict[str, Any]], float | None]
) -> list[float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        item = value(row)
        if item is not None:
            grouped[str(row["task_id"])].append(float(item))
    return [sum(items) / len(items) for items in grouped.values() if items]


def _metric_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": mean(values),
        "median": percentile(values, 0.5),
        "p90": percentile(values, 0.9),
        "total": sum(values) if values else None,
    }


def _uncached_input_tokens(row: dict[str, Any]) -> float | None:
    usage = _nested(row, "agent", "usage")
    if not isinstance(usage, dict):
        return None
    values = [
        usage.get("input_tokens"),
        usage.get("cache_read_tokens", 0),
        usage.get("cache_write_tokens", 0),
    ]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return None
    return max(0.0, float(values[0]) - float(values[1]) - float(values[2]))


def analyze_experiment(
    experiment_dir: Path,
    *,
    bootstrap_samples: int | None = None,
    analysis_seed: int | None = None,
    sesoi: float | None = None,
) -> dict[str, Any]:
    experiment_dir = experiment_dir.resolve()
    manifest = load_json(experiment_dir / "manifest.json")
    config = load_json(experiment_dir / "config.resolved.json")
    rows = apply_external_evaluations(experiment_dir, load_jsonl(experiment_dir / "episodes.jsonl"))
    if not isinstance(manifest, dict) or not isinstance(config, dict):
        raise ConfigError("Experiment manifest/config is invalid")
    experiment_config = config.get("experiment", {})
    if not isinstance(experiment_config, dict):
        raise ConfigError("Resolved experiment configuration is invalid")
    baseline = str(experiment_config.get("baseline", "none"))
    try:
        samples = int(
            bootstrap_samples
            if bootstrap_samples is not None
            else experiment_config.get("bootstrap_samples", 5000)
        )
        seed = int(
            analysis_seed
            if analysis_seed is not None
            else experiment_config.get("analysis_seed", 20260815)
        )
        practical = float(sesoi if sesoi is not None else experiment_config.get("sesoi", 0.05))
    except (TypeError, ValueError) as exc:
        raise ConfigError("Report bootstrap_samples, analysis_seed, and sesoi are invalid") from exc
    if samples < 100:
        raise ConfigError("report bootstrap_samples must be >= 100")
    if not 0 < practical < 1:
        raise ConfigError("report sesoi must be in (0, 1)")
    primary_comparison = experiment_config.get("primary_comparison")
    is_scripted = manifest.get("model_provider") == "scripted"
    manifest_tasks = manifest.get("tasks")
    manifest_variants = manifest.get("variants")
    manifest_replicates = manifest.get("replicates")
    if (
        not isinstance(manifest_tasks, list)
        or not isinstance(manifest_variants, list)
        or not manifest_variants
        or not isinstance(manifest_replicates, list)
        or not manifest_replicates
    ):
        raise ConfigError("Experiment manifest task/variant/replicate fields are invalid")
    variants = [str(value) for value in manifest_variants]
    if baseline not in variants:
        raise ConfigError("Report baseline is not present in the experiment variants")
    expected_blocks = len(manifest_tasks) * len(manifest_replicates)
    infrastructure_rows = [row for row in rows if row.get("infrastructure_valid") is True]
    outcome_rows = [row for row in infrastructure_rows if row.get("evaluation_valid", True) is True]
    outcome_by_block: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in outcome_rows:
        outcome_by_block[(str(row["task_id"]), int(row["replicate"]))][str(row["variant_id"])] = row
    complete_blocks = {
        key: value
        for key, value in outcome_by_block.items()
        if all(variant in value for variant in variants)
    }
    pair_coverage = len(complete_blocks) / expected_blocks if expected_blocks else 0.0
    infrastructure_by_block: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in infrastructure_rows:
        infrastructure_by_block[(str(row["task_id"]), int(row["replicate"]))][
            str(row["variant_id"])
        ] = row
    strategy_outcome_rows: dict[str, list[dict[str, Any]]] = {
        variant: [row for row in outcome_rows if row.get("variant_id") == variant]
        for variant in variants
    }
    strategy_infrastructure_rows: dict[str, list[dict[str, Any]]] = {
        variant: [row for row in infrastructure_rows if row.get("variant_id") == variant]
        for variant in variants
    }
    strategies: dict[str, Any] = {}
    for index, variant in enumerate(variants):
        outcome_selected = strategy_outcome_rows[variant]
        infrastructure_selected = strategy_infrastructure_rows[variant]
        recorded_for_variant = [row for row in rows if row.get("variant_id") == variant]
        solve_task_values = _task_means(
            outcome_selected, lambda row: 1.0 if row.get("solved") is True else 0.0
        )
        solve_rate = mean(solve_task_values)
        if is_scripted or len(solve_task_values) < 10:
            low, high = None, None
        else:
            low, high = bootstrap_mean_interval(
                solve_task_values, samples=samples, seed=seed + index * 101
            )
        tokens = finite(
            _nested(row, "agent", "usage", "total_tokens") for row in infrastructure_selected
        )
        token_ledger = {
            "uncached_input_tokens": _metric_summary(
                finite(_uncached_input_tokens(row) for row in infrastructure_selected)
            ),
            **{
                name: _metric_summary(
                    finite(_nested(row, "agent", "usage", name) for row in infrastructure_selected)
                )
                for name in (
                    "input_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                )
            },
        }
        costs = finite(
            _nested(row, "agent", "usage", "cost_usd") for row in infrastructure_selected
        )
        latencies = finite(row.get("wall_seconds") for row in infrastructure_selected)
        lexical_availability = finite(
            _nested(row, "agent", "lexical_constraint_availability_rate")
            for row in infrastructure_selected
        )
        adherence = finite(
            _nested(row, "evaluation", "behavioral_adherence") for row in outcome_selected
        )
        compacted_count = sum(
            int((_nested(row, "agent", "compaction_count") or 0) > 0)
            for row in infrastructure_selected
        )
        status_counts = Counter(str(row.get("status")) for row in outcome_selected)
        all_status_counts = Counter(str(row.get("status")) for row in recorded_for_variant)
        governed_task_values = _task_means(
            outcome_selected,
            lambda row: 1.0 if row.get("governed_solved") is True else 0.0,
        )
        usage_sources = sorted(
            {
                str(source)
                for row in infrastructure_selected
                for source in (_nested(row, "agent", "usage", "usage_sources") or [])
            }
        )
        incomplete_usage_attempts = sum(
            int(_nested(row, "agent", "usage", "complete") is False)
            for row in infrastructure_selected
        )
        provider_models = sorted(
            {
                str(model)
                for row in infrastructure_selected
                for model in (_nested(row, "agent", "provider_models") or [])
            }
        )
        provider_fingerprints = sorted(
            {
                str(fingerprint)
                for row in infrastructure_selected
                for fingerprint in (_nested(row, "agent", "provider_fingerprints") or [])
            }
        )
        trajectory_groups: dict[str, list[str]] = defaultdict(list)
        for row in infrastructure_selected:
            signature = _nested(row, "agent", "model_trajectory_sha256")
            if isinstance(signature, str):
                trajectory_groups[str(row["task_id"])].append(signature)
        repeated_groups = [items for items in trajectory_groups.values() if len(items) > 1]
        trajectory_observations = sum(len(items) for items in repeated_groups)
        unique_trajectories = sum(len(set(items)) for items in repeated_groups)
        strategies[variant] = {
            "scheduled_attempts": expected_blocks,
            "recorded_attempts": len(recorded_for_variant),
            "infrastructure_valid_attempts": len(infrastructure_selected),
            "valid_attempts": len(outcome_selected),
            "invalid_or_pending_attempts": len(recorded_for_variant) - len(outcome_selected),
            "tasks": len(solve_task_values),
            "solve_rate": solve_rate,
            "solve_rate_interval": [low, high],
            "tokens": _metric_summary(tokens),
            "token_ledger": token_ledger,
            "cost_usd": _metric_summary(costs),
            "latency_seconds": _metric_summary(latencies),
            "lexical_constraint_availability": _metric_summary(lexical_availability),
            "behavioral_adherence": _metric_summary(adherence),
            "governed_solve_rate": mean(governed_task_values),
            "compaction_exposure_rate": (
                compacted_count / len(infrastructure_selected) if infrastructure_selected else None
            ),
            "status_counts": dict(sorted(status_counts.items())),
            "all_status_counts": dict(sorted(all_status_counts.items())),
            "usage_sources": usage_sources,
            "incomplete_usage_attempts": incomplete_usage_attempts,
            "provider_models": provider_models,
            "provider_fingerprints": provider_fingerprints,
            "repeated_trajectory_observations": trajectory_observations,
            "effective_unique_trajectories": (
                unique_trajectories if trajectory_observations else None
            ),
            "duplicate_trajectory_rate": (
                (trajectory_observations - unique_trajectories) / trajectory_observations
                if trajectory_observations
                else None
            ),
        }
    comparisons: dict[str, Any] = {}
    for index, variant in enumerate(variants):
        if variant == baseline:
            continue
        baseline_models = set(strategies[baseline]["provider_models"])
        variant_models = set(strategies[variant]["provider_models"])
        baseline_fingerprints = set(strategies[baseline]["provider_fingerprints"])
        variant_fingerprints = set(strategies[variant]["provider_fingerprints"])
        provider_confounded = bool(
            len(baseline_models) > 1
            or len(variant_models) > 1
            or len(baseline_fingerprints) > 1
            or len(variant_fingerprints) > 1
            or (baseline_models and variant_models and baseline_models != variant_models)
            or (
                baseline_fingerprints
                and variant_fingerprints
                and baseline_fingerprints != variant_fingerprints
            )
        )
        outcome_pair_blocks = {
            key: block
            for key, block in outcome_by_block.items()
            if baseline in block and variant in block
        }
        infrastructure_pair_blocks = {
            key: block
            for key, block in infrastructure_by_block.items()
            if baseline in block and variant in block
        }
        for block in infrastructure_pair_blocks.values():
            baseline_agent_models = set(_nested(block[baseline], "agent", "provider_models") or [])
            variant_agent_models = set(_nested(block[variant], "agent", "provider_models") or [])
            baseline_agent_fingerprints = set(
                _nested(block[baseline], "agent", "provider_fingerprints") or []
            )
            variant_agent_fingerprints = set(
                _nested(block[variant], "agent", "provider_fingerprints") or []
            )
            if (
                baseline_agent_models
                and variant_agent_models
                and baseline_agent_models != variant_agent_models
            ) or (
                baseline_agent_fingerprints
                and variant_agent_fingerprints
                and baseline_agent_fingerprints != variant_agent_fingerprints
            ):
                provider_confounded = True
                break
        comparison_pair_coverage = (
            len(outcome_pair_blocks) / expected_blocks if expected_blocks else 0.0
        )
        task_effects: dict[str, list[float]] = defaultdict(list)
        wins = losses = ties = 0
        for (task_id, _replicate), block in outcome_pair_blocks.items():
            current = 1.0 if block[variant].get("solved") is True else 0.0
            control = 1.0 if block[baseline].get("solved") is True else 0.0
            task_effects[task_id].append(current - control)
            wins += int(current > control)
            losses += int(current < control)
            ties += int(current == control)
        effects = [sum(items) / len(items) for items in task_effects.values() if items]
        estimate = mean(effects)
        if is_scripted or len(effects) < 10 or provider_confounded:
            low, high = None, None
        else:
            low, high = bootstrap_mean_interval(
                effects, samples=samples, seed=seed + 10_000 + index * 101
            )
        mde = None if is_scripted else empirical_mde(effects)
        prospective_mde = prospective_paired_mde(len(effects), anticipated_discordance=0.2)
        sign_flip = (
            None
            if is_scripted or provider_confounded
            else paired_sign_flip_pvalue(effects, seed=seed + index)
        )
        inference_ready = (
            manifest.get("model_provider") != "scripted"
            and len(effects) >= 20
            and comparison_pair_coverage >= 0.98
            and primary_comparison == variant
            and not provider_confounded
        )
        if provider_confounded:
            classification = "provider_confounded"
        elif not inference_ready:
            classification = (
                "descriptive_only"
                if manifest.get("model_provider") == "scripted" or len(effects) < 20
                else "exploratory"
            )
        elif (
            low is not None
            and high is not None
            and low >= -practical
            and high <= practical
            and prospective_mde is not None
            and prospective_mde <= practical
        ):
            classification = "practical_equivalence"
        elif sign_flip is None or sign_flip > 0.05:
            classification = "inconclusive"
        elif low is not None and low >= practical:
            classification = "meaningful_gain"
        elif high is not None and high <= -practical:
            classification = "meaningful_loss"
        else:
            classification = "inconclusive"
        resource_deltas: dict[str, Any] = {}
        resource_extractors: dict[str, Callable[[dict[str, Any]], float | None]] = {
            "tokens": lambda row: _nested(row, "agent", "usage", "total_tokens"),
            "cost_usd": lambda row: _nested(row, "agent", "usage", "cost_usd"),
            "wall_seconds": lambda row: row.get("wall_seconds"),
        }
        for metric_index, (metric_name, extractor) in enumerate(resource_extractors.items()):
            per_task: dict[str, list[float]] = defaultdict(list)
            for (task_id, _replicate), block in infrastructure_pair_blocks.items():
                current_value = extractor(block[variant])
                control_value = extractor(block[baseline])
                if current_value is not None and control_value is not None:
                    per_task[task_id].append(float(current_value) - float(control_value))
            metric_effects = [sum(items) / len(items) for items in per_task.values() if items]
            if is_scripted or len(metric_effects) < 10 or provider_confounded:
                metric_low, metric_high = None, None
            else:
                metric_low, metric_high = bootstrap_mean_interval(
                    metric_effects,
                    samples=samples,
                    seed=seed + 20_000 + index * 101 + metric_index,
                )
            resource_deltas[metric_name] = {
                "mean_delta": mean(metric_effects),
                "interval": [metric_low, metric_high],
                "paired_task_count": len(metric_effects),
            }
        comparisons[variant] = {
            "baseline": baseline,
            "paired_task_count": len(effects),
            "paired_block_count": wins + losses + ties,
            "pair_coverage": comparison_pair_coverage,
            "delta_solve_rate": estimate,
            "delta_interval": [low, high],
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "empirical_mde": mde,
            "prospective_mde_at_20pct_discordance": prospective_mde,
            "sign_flip_pvalue": sign_flip,
            "sesoi": practical,
            "classification": classification,
            "primary_comparison": primary_comparison == variant,
            "inference_ready": inference_ready,
            "provider_confounded": provider_confounded,
            "resource_deltas": resource_deltas,
        }
    infrastructure_invalid = sum(int(row.get("infrastructure_valid") is not True) for row in rows)
    warnings: list[dict[str, str]] = []
    if is_scripted:
        warnings.append(
            {
                "code": "SCRIPTED_PROVIDER",
                "message": (
                    "This run validates the engine; it is not model-performance evidence. "
                    "Inferential intervals are withheld and comparisons are labeled descriptive."
                ),
            }
        )
    if len(manifest_tasks) < 20:
        warnings.append(
            {
                "code": "LOW_TASK_COUNT",
                "message": "The task panel is too small for narrow solve-rate claims.",
            }
        )
    pending_evaluations = sum(
        int(
            row.get("infrastructure_valid") is True
            and row.get("evaluation_valid", True) is not True
        )
        for row in rows
    )
    if pending_evaluations:
        warnings.append(
            {
                "code": "PENDING_EXTERNAL_EVALUATION",
                "message": f"{pending_evaluations} episode(s) do not yet have evaluator outcomes.",
            }
        )
    if pair_coverage < 0.98:
        warnings.append(
            {
                "code": "LOW_PAIR_COVERAGE",
                "message": f"Only {pair_coverage:.1%} of scheduled paired blocks are complete.",
            }
        )
    invalid_rate = infrastructure_invalid / len(rows) if rows else 0.0
    if invalid_rate > 0.02:
        warnings.append(
            {
                "code": "HIGH_INFRA_FAILURE",
                "message": f"Infrastructure-invalid episodes are {invalid_rate:.1%} of recorded runs.",
            }
        )
    for variant, value in strategies.items():
        exposure = value["compaction_exposure_rate"]
        treatment_map = manifest.get("variant_treatments")
        treatment = treatment_map.get(variant) if isinstance(treatment_map, dict) else None
        context_policy = treatment.get("context_policy") if isinstance(treatment, dict) else None
        expects_builtin_compaction = context_policy in {"reactive", "periodic", "selective"}
        if (
            variant != baseline
            and expects_builtin_compaction
            and exposure is not None
            and exposure < 0.5
        ):
            warnings.append(
                {
                    "code": "LOW_COMPACTION_EXPOSURE",
                    "message": f"Only {exposure:.1%} of {variant} episodes exercised compaction.",
                }
            )
        metric_counts = {
            metric: int(value[metric]["count"])
            for metric in ("tokens", "cost_usd", "latency_seconds")
        }
        missing_metrics = [
            metric
            for metric, count in metric_counts.items()
            if count < value["infrastructure_valid_attempts"]
        ]
        if missing_metrics:
            warnings.append(
                {
                    "code": "INCOMPLETE_RESOURCE_LEDGER",
                    "message": (
                        f"{variant} is missing {', '.join(missing_metrics)} for one or more "
                        "infrastructure-valid attempts."
                    ),
                }
            )
        if len(value["provider_models"]) > 1 or len(value["provider_fingerprints"]) > 1:
            warnings.append(
                {
                    "code": "PROVIDER_DRIFT",
                    "message": (
                        f"{variant} observed multiple effective provider models or fingerprints: "
                        f"models={value['provider_models']}, "
                        f"fingerprints={value['provider_fingerprints']}."
                    ),
                }
            )
        duplicate_rate = value["duplicate_trajectory_rate"]
        if duplicate_rate is not None and duplicate_rate > 0.5:
            warnings.append(
                {
                    "code": "DUPLICATE_TRAJECTORIES",
                    "message": (
                        f"{duplicate_rate:.1%} of repeated {variant} observations duplicate "
                        "another replicate's model-response trajectory."
                    ),
                }
            )
        if not is_scripted and "estimated_char4" in value["usage_sources"]:
            warnings.append(
                {
                    "code": "ESTIMATED_USAGE",
                    "message": f"{variant} includes locally estimated rather than provider usage.",
                }
            )
        if value["incomplete_usage_attempts"]:
            warnings.append(
                {
                    "code": "INCOMPLETE_USAGE_LEDGER",
                    "message": (
                        f"{variant} has {value['incomplete_usage_attempts']} attempt(s) with "
                        "failed/retried provider calls whose billed usage was not reported."
                    ),
                }
            )
    effective_models = {
        variant: tuple(value["provider_models"])
        for variant, value in strategies.items()
        if value["provider_models"]
    }
    effective_fingerprints = {
        variant: tuple(value["provider_fingerprints"])
        for variant, value in strategies.items()
        if value["provider_fingerprints"]
    }
    if len(set(effective_models.values())) > 1 or len(set(effective_fingerprints.values())) > 1:
        warnings.append(
            {
                "code": "TREATMENT_PROVIDER_CONFOUND",
                "message": (
                    "Effective provider model or fingerprint sets differ across treatments; "
                    f"models={effective_models}, fingerprints={effective_fingerprints}. "
                    "Do not attribute the contrast to the harness alone."
                ),
            }
        )
    for variant, value in comparisons.items():
        prospective = value["prospective_mde_at_20pct_discordance"]
        if prospective is not None and prospective > practical:
            warnings.append(
                {
                    "code": "LOW_POWER",
                    "message": (
                        f"{variant} vs {baseline}: prospective MDE {prospective:.1%} at "
                        "20% anticipated discordance exceeds "
                        f"the {practical:.1%} practical-effect threshold."
                    ),
                }
            )
    return {
        "schema_version": 2,
        "experiment": manifest.get("experiment"),
        "config_hash": manifest.get("config_hash"),
        "model_provider": manifest.get("model_provider"),
        "model_name": manifest.get("model_name"),
        "baseline": baseline,
        "primary_comparison": primary_comparison,
        "task_count": len(manifest_tasks),
        "replicate_count": len(manifest_replicates),
        "scheduled_trials": manifest.get("trial_count"),
        "recorded_trials": len(rows),
        "valid_trials": len(outcome_rows),
        "infrastructure_invalid_trials": infrastructure_invalid,
        "evaluation_pending_trials": sum(
            int(
                row.get("infrastructure_valid") is True
                and row.get("evaluation_valid", True) is not True
            )
            for row in rows
        ),
        "expected_paired_blocks": expected_blocks,
        "complete_paired_blocks": len(complete_blocks),
        "pair_coverage": pair_coverage,
        "bootstrap_samples": samples,
        "analysis_seed": seed,
        "resampling_algorithm": RESAMPLING_ALGORITHM,
        "sesoi": practical,
        "interval_interpretation": (
            "task-panel cluster bootstrap; replicates stay within tasks; intervals are withheld "
            "for scripted runs and panels below 10 tasks"
        ),
        "strategies": strategies,
        "comparisons": comparisons,
        "warnings": warnings,
    }


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"


def _number(value: Any, digits: int = 1) -> str:
    return "n/a" if value is None else f"{float(value):,.{digits}f}"


def _percent_estimate(value: Any, interval: list[Any]) -> str:
    if value is None:
        return _percent(None)
    if interval[0] is None or interval[1] is None:
        return f"{_percent(value)} (descriptive)"
    return f"{_percent(value)} [{_percent(interval[0])}, {_percent(interval[1])}]"


def _number_estimate(value: Any, interval: list[Any], digits: int) -> str:
    rendered = _number(value, digits)
    if value is None:
        return rendered
    if interval[0] is None or interval[1] is None:
        return f"{rendered} (descriptive)"
    return f"{rendered} [{_number(interval[0], digits)}, {_number(interval[1], digits)}]"


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['experiment']}: ScaffoldScope report",
        "",
        f"**Model:** `{summary['model_name']}` | **Baseline:** `{summary['baseline']}` | "
        f"**Panel:** {summary['task_count']} tasks x {summary['replicate_count']} paired replicates",
        f"**Predeclared primary contrast:** `{summary['primary_comparison'] or 'none'}`",
        "",
        "> Intervals are task-panel cluster-bootstrap intervals. Replicates do not count as independent tasks.",
        "",
        "## Experiment integrity",
        "",
        f"- Recorded trials: {summary['recorded_trials']} / {summary['scheduled_trials']}",
        f"- Analysis-valid trials: {summary['valid_trials']}",
        f"- Pending evaluator outcomes: {summary['evaluation_pending_trials']}",
        f"- Complete paired blocks: {summary['complete_paired_blocks']} / "
        f"{summary['expected_paired_blocks']} ({_percent(summary['pair_coverage'])})",
        f"- Config hash: `{summary['config_hash']}`",
        "",
        "## Solve outcomes",
        "",
        "| Strategy | Solve rate / interval | Delta vs baseline / interval | W/L/T | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for variant, value in summary["strategies"].items():
        interval = value["solve_rate_interval"]
        comparison = summary["comparisons"].get(variant)
        if comparison is None:
            delta = "n/a"
            wlt = "n/a"
            status = "baseline"
        else:
            delta_interval = comparison["delta_interval"]
            delta = _percent_estimate(comparison["delta_solve_rate"], delta_interval)
            wlt = f"{comparison['wins']}/{comparison['losses']}/{comparison['ties']}"
            status = comparison["classification"].replace("_", " ")
        solve = _percent_estimate(value["solve_rate"], interval)
        lines.append(f"| `{variant}` | {solve} | {delta} | {wlt} | {status} |")
    lines.extend(
        [
            "",
            "## Resources and governance",
            "",
            "| Strategy | Generated / scheduled | Tokens / attempt (mean) | Token p50 / p90 | Total estimated cost | "
            "Latency p50 / p90 | Compaction exposure | Lexical constraint availability | "
            "Behavioral adherence | Governed solve |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for variant, value in summary["strategies"].items():
        tokens = value["tokens"]
        cost = value["cost_usd"]
        latency = value["latency_seconds"]
        availability = value["lexical_constraint_availability"]
        adherence = value["behavioral_adherence"]
        total_cost = "n/a" if cost["total"] is None else f"${cost['total']:.4f}"
        lines.append(
            f"| `{variant}` | {value['infrastructure_valid_attempts']} / "
            f"{value['scheduled_attempts']} | "
            f"{_number(tokens['mean'], 0)} | "
            f"{_number(tokens['median'], 0)} / {_number(tokens['p90'], 0)} | "
            f"{total_cost} | "
            f"{_number(latency['median'], 2)}s / {_number(latency['p90'], 2)}s | "
            f"{_percent(value['compaction_exposure_rate'])} | {_percent(availability['mean'])} | "
            f"{_percent(adherence['mean'])} | {_percent(value['governed_solve_rate'])} |"
        )
    lines.extend(
        [
            "",
            "### Token ledger totals",
            "",
            "Input includes cached tokens when the provider reports them; uncached input subtracts "
            "cache reads and writes. Reasoning tokens are provider-reported completion details and "
            "can overlap output tokens.",
            "",
            "| Strategy | Uncached input | Cache read | Cache write | Output | Reasoning | Total |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for variant, value in summary["strategies"].items():
        ledger = value["token_ledger"]
        lines.append(
            f"| `{variant}` | {_number(ledger['uncached_input_tokens']['total'], 0)} | "
            f"{_number(ledger['cache_read_tokens']['total'], 0)} | "
            f"{_number(ledger['cache_write_tokens']['total'], 0)} | "
            f"{_number(ledger['output_tokens']['total'], 0)} | "
            f"{_number(ledger['reasoning_tokens']['total'], 0)} | "
            f"{_number(ledger['total_tokens']['total'], 0)} |"
        )
    lines.extend(
        [
            "",
            "## Paired resource deltas",
            "",
            "| Strategy | Delta tokens / task / interval | Delta estimated cost / task / interval | "
            "Delta wall seconds / task / interval |",
            "|---|---:|---:|---:|",
        ]
    )
    for variant, comparison in summary["comparisons"].items():
        resources = comparison["resource_deltas"]
        token_delta = resources["tokens"]
        cost_delta = resources["cost_usd"]
        wall_delta = resources["wall_seconds"]
        lines.append(
            f"| `{variant}` | "
            f"{_number_estimate(token_delta['mean_delta'], token_delta['interval'], 0)} | "
            f"{_number_estimate(cost_delta['mean_delta'], cost_delta['interval'], 4)} | "
            f"{_number_estimate(wall_delta['mean_delta'], wall_delta['interval'], 2)} |"
        )
    lines.extend(["", "## Warnings", ""])
    if not summary["warnings"]:
        lines.append("No automated integrity warnings.")
    else:
        lines.extend(
            f"- **{warning['code']}** - {warning['message']}" for warning in summary["warnings"]
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Protocol-defined failures and budget terminations remain in the solve denominator. "
            "Resource summaries use every reported value from infrastructure-valid attempts and warn when a "
            "ledger is missing or incomplete. Do not treat a small curated panel or scripted-provider "
            "demo as an estimate of SWE-bench performance.",
            "",
        ]
    )
    return "\n".join(lines)


def _csv_text(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def render_html(summary: dict[str, Any]) -> str:
    max_rate = max(
        (value["solve_rate"] or 0.0 for value in summary["strategies"].values()), default=1.0
    )
    bars = []
    for variant, value in summary["strategies"].items():
        rate = value["solve_rate"]
        width = 0 if max_rate == 0 or rate is None else (rate / max_rate) * 100
        bars.append(
            '<div class="bar-row"><code>'
            + html.escape(variant)
            + '</code><div class="track"><div class="bar" style="width:'
            + f"{width:.2f}%"
            + '"></div></div><strong>'
            + _percent(rate)
            + "</strong></div>"
        )
    warning_html = (
        "".join(
            f"<li><strong>{html.escape(item['code'])}</strong> - {html.escape(item['message'])}</li>"
            for item in summary["warnings"]
        )
        or "<li>No automated integrity warnings.</li>"
    )
    report_markdown = render_markdown(summary)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(summary["experiment"]))} - ScaffoldScope</title>
<style>
:root{{--ink:#17202a;--muted:#667085;--paper:#fbfaf7;--panel:#fff;--accent:#6d5dfc;--line:#e7e2d8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,sans-serif}}
main{{max-width:980px;margin:auto;padding:64px 24px}}.eyebrow{{color:var(--accent);font-weight:800;letter-spacing:.08em;text-transform:uppercase}}
h1{{font-size:clamp(2.4rem,7vw,5rem);line-height:.95;margin:.25em 0}}.lede{{font-size:1.2rem;color:var(--muted)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:24px;margin:28px 0;box-shadow:0 12px 40px #4b3f2a0d}}
.bar-row{{display:grid;grid-template-columns:150px 1fr 64px;gap:14px;align-items:center;margin:14px 0}}.track{{height:14px;background:#eeeafc;border-radius:99px;overflow:hidden}}.bar{{height:100%;background:linear-gradient(90deg,#6d5dfc,#b06cff)}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#171821;color:#f5f2ff;padding:24px;border-radius:14px;font-size:13px}}code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}ul{{padding-left:22px}}footer{{color:var(--muted);margin-top:40px}}@media(max-width:600px){{.bar-row{{grid-template-columns:100px 1fr 54px}}}}
</style></head><body><main>
<div class="eyebrow">ScaffoldScope - controlled ablation report</div>
<h1>{html.escape(str(summary["experiment"]))}</h1>
<p class="lede">Same model. Same tasks. Same budgets. One declared scaffold treatment changed.</p>
<section class="card"><h2>Solve rate</h2>{"".join(bars)}</section>
<section class="card"><h2>Integrity</h2><p>{summary["complete_paired_blocks"]} / {summary["expected_paired_blocks"]} paired blocks complete ({_percent(summary["pair_coverage"])}). Model: <code>{html.escape(str(summary["model_name"]))}</code>.</p></section>
<section class="card"><h2>Warnings</h2><ul>{warning_html}</ul></section>
<section><h2>Full report</h2><pre>{html.escape(report_markdown)}</pre></section>
<footer>Generated from auditable ScaffoldScope episode records. Config <code>{html.escape(str(summary["config_hash"]))}</code>.</footer>
</main></body></html>"""


def write_report(
    experiment_dir: Path,
    *,
    bootstrap_samples: int | None = None,
    analysis_seed: int | None = None,
    sesoi: float | None = None,
) -> dict[str, Any]:
    summary = analyze_experiment(
        experiment_dir,
        bootstrap_samples=bootstrap_samples,
        analysis_seed=analysis_seed,
        sesoi=sesoi,
    )
    atomic_write_json(experiment_dir / "summary.json", summary)
    atomic_write_text(experiment_dir / "report.md", render_markdown(summary))
    atomic_write_text(experiment_dir / "report.html", render_html(summary))
    strategy_rows: list[dict[str, Any]] = []
    for variant, value in summary["strategies"].items():
        strategy_rows.append(
            {
                "strategy": variant,
                "recorded_attempts": value["recorded_attempts"],
                "infrastructure_valid_attempts": value["infrastructure_valid_attempts"],
                "valid_attempts": value["valid_attempts"],
                "scheduled_attempts": value["scheduled_attempts"],
                "tasks": value["tasks"],
                "solve_rate": value["solve_rate"],
                "solve_ci_low": value["solve_rate_interval"][0],
                "solve_ci_high": value["solve_rate_interval"][1],
                "tokens_mean": value["tokens"]["mean"],
                "tokens_p50": value["tokens"]["median"],
                "tokens_p90": value["tokens"]["p90"],
                "uncached_input_tokens_total": value["token_ledger"]["uncached_input_tokens"][
                    "total"
                ],
                "input_tokens_total": value["token_ledger"]["input_tokens"]["total"],
                "cache_read_tokens_total": value["token_ledger"]["cache_read_tokens"]["total"],
                "cache_write_tokens_total": value["token_ledger"]["cache_write_tokens"]["total"],
                "output_tokens_total": value["token_ledger"]["output_tokens"]["total"],
                "reasoning_tokens_total": value["token_ledger"]["reasoning_tokens"]["total"],
                "total_tokens": value["token_ledger"]["total_tokens"]["total"],
                "cost_total_usd": value["cost_usd"]["total"],
                "latency_p50_seconds": value["latency_seconds"]["median"],
                "latency_p90_seconds": value["latency_seconds"]["p90"],
                "compaction_exposure_rate": value["compaction_exposure_rate"],
                "lexical_constraint_availability": value["lexical_constraint_availability"]["mean"],
                "behavioral_adherence": value["behavioral_adherence"]["mean"],
                "governed_solve_rate": value["governed_solve_rate"],
                "duplicate_trajectory_rate": value["duplicate_trajectory_rate"],
                "effective_unique_trajectories": value["effective_unique_trajectories"],
                "provider_models": ";".join(value["provider_models"]),
                "provider_fingerprints": ";".join(value["provider_fingerprints"]),
                "usage_sources": ";".join(value["usage_sources"]),
                "incomplete_usage_attempts": value["incomplete_usage_attempts"],
            }
        )
    fields = list(strategy_rows[0]) if strategy_rows else ["strategy"]
    atomic_write_text(experiment_dir / "summary.csv", _csv_text(strategy_rows, fields))
    comparison_rows: list[dict[str, Any]] = []
    for variant, value in summary["comparisons"].items():
        resources = value["resource_deltas"]
        flattened = {key: item for key, item in value.items() if key != "resource_deltas"}
        for metric, metric_value in resources.items():
            flattened[f"{metric}_mean_delta"] = metric_value["mean_delta"]
            flattened[f"{metric}_ci_low"] = metric_value["interval"][0]
            flattened[f"{metric}_ci_high"] = metric_value["interval"][1]
            flattened[f"{metric}_paired_tasks"] = metric_value["paired_task_count"]
        comparison_rows.append({"strategy": variant, **flattened})
    comparison_fields = list(comparison_rows[0]) if comparison_rows else ["strategy"]
    atomic_write_text(
        experiment_dir / "paired-comparisons.csv",
        _csv_text(comparison_rows, comparison_fields),
    )
    return summary


def check_experiment(experiment_dir: Path) -> tuple[bool, list[str]]:
    experiment_dir = experiment_dir.resolve()
    issues: list[str] = []
    required = [
        "manifest.json",
        "config.resolved.json",
        "plan.jsonl",
        "episodes.jsonl",
        "pricing.json",
    ]
    for name in required:
        required_path = experiment_dir / name
        if required_path.is_symlink():
            issues.append(f"required evidence file is a symlink: {name}")
        elif not required_path.is_file():
            issues.append(f"missing {name}")
    if issues:
        return False, issues
    manifest = load_json(experiment_dir / "manifest.json")
    resolved_config = load_json(experiment_dir / "config.resolved.json")
    pricing = load_json(experiment_dir / "pricing.json")
    plan = load_jsonl(experiment_dir / "plan.jsonl")
    rows = load_jsonl(experiment_dir / "episodes.jsonl")
    if (
        not isinstance(manifest, dict)
        or not isinstance(resolved_config, dict)
        or not isinstance(pricing, dict)
    ):
        return False, ["manifest/config/pricing must contain JSON objects"]
    integrity_version = manifest.get("integrity_version")

    def requires_v1_profile(version: Any) -> bool:
        if not isinstance(version, str):
            return False
        version_parts = version.split(".")
        try:
            major = int(version_parts[0])
            minor = int(version_parts[1]) if len(version_parts) > 1 else 0
        except ValueError:
            return False
        return (major, minor) >= (0, 3)

    version_requires_integrity = requires_v1_profile(manifest.get("scaffoldscope_version"))
    profile_fields_present = any(
        key in manifest
        for key in (
            "resolved_config_hash",
            "runtime_identity",
            "variant_order_algorithm",
            "task_toolsets",
            "task_provenance",
            "task_constraints",
            "provider_seed_supported",
        )
    )
    episode_profile_present = any(
        "runtime_identity" in row or requires_v1_profile(row.get("scaffoldscope_version"))
        for row in rows
    )
    profile_requires_integrity = (
        version_requires_integrity or profile_fields_present or episode_profile_present
    )
    if integrity_version is None and profile_requires_integrity:
        issues.append("manifest is missing integrity_version for v1-profile evidence")
    if integrity_version is not None and (
        not isinstance(integrity_version, int)
        or isinstance(integrity_version, bool)
        or integrity_version != 1
    ):
        issues.append("manifest integrity_version is unsupported")
    requires_integrity_v1 = integrity_version == 1 or profile_requires_integrity
    if requires_integrity_v1:
        required_manifest_fields = {
            "schema_version",
            "integrity_version",
            "variant_order_algorithm",
            "scaffoldscope_version",
            "created_at",
            "experiment",
            "config_hash",
            "resolved_config_hash",
            "implementation_hash",
            "task_source_hashes",
            "task_provenance",
            "task_constraints",
            "code_commit",
            "python",
            "platform",
            "token_counter",
            "runtime_identity",
            "model_provider",
            "model_name",
            "provider_seed_supported",
            "sandbox_backend",
            "docker",
            "docker_runtime",
            "plugins",
            "tasks",
            "task_toolsets",
            "variants",
            "variant_treatments",
            "replicates",
            "trial_count",
            "pairing_unit",
            "warning",
        }
        missing_manifest_fields = sorted(required_manifest_fields - set(manifest))
        if missing_manifest_fields:
            issues.append(
                "manifest is missing v1-profile fields: " + ", ".join(missing_manifest_fields)
            )
        if manifest.get("schema_version") != 1:
            issues.append("manifest schema_version must be 1 for v1-profile evidence")
        if manifest.get("pairing_unit") != "task_id + replicate":
            issues.append("manifest pairing_unit is invalid for v1-profile evidence")
        if parse_utc_timestamp(manifest.get("created_at")) is None:
            issues.append("manifest created_at must be an RFC-3339 UTC timestamp")
        for field in ("python", "platform", "token_counter"):
            if not isinstance(manifest.get(field), str) or not manifest.get(field):
                issues.append(f"manifest {field} must be a non-empty string")
        code_commit = manifest.get("code_commit")
        if code_commit is not None and (
            not isinstance(code_commit, str)
            or re.fullmatch(r"[0-9a-f]{40,64}", code_commit) is None
        ):
            issues.append("manifest code_commit must be a Git object ID or null")
    variant_order_algorithm = manifest.get("variant_order_algorithm")
    if requires_integrity_v1 and variant_order_algorithm != _VARIANT_ORDER_ALGORITHM:
        issues.append(
            "manifest variant_order_algorithm must be "
            f"{_VARIANT_ORDER_ALGORITHM!r} for v1-profile evidence"
        )
    pricing_identity = dict(pricing)
    pricing_hash = pricing_identity.pop("hash", None)
    if not isinstance(pricing_hash, str) or content_hash(pricing_identity) != pricing_hash:
        issues.append("pricing snapshot hash mismatch")
    if "model_name" in manifest and pricing.get("model") != manifest.get("model_name"):
        issues.append("pricing model does not match manifest")
    declared_resolved_hash = manifest.get("resolved_config_hash")
    if declared_resolved_hash is None:
        if requires_integrity_v1:
            issues.append("manifest is missing resolved config hash")
    elif (
        not isinstance(declared_resolved_hash, str)
        or content_hash(resolved_config) != declared_resolved_hash
    ):
        issues.append("resolved config content hash mismatch")
    resolved_identity = resolved_config.get("resolved", {})
    if not isinstance(resolved_identity, dict) or resolved_identity.get(
        "config_hash"
    ) != manifest.get("config_hash"):
        issues.append("resolved config hash does not match manifest")
    if isinstance(resolved_identity, dict):
        for manifest_key, resolved_key in (
            ("implementation_hash", "implementation_hash"),
            ("task_source_hashes", "task_source_hashes"),
            ("task_provenance", "task_provenance"),
            ("task_constraints", "task_constraints"),
            ("task_toolsets", "task_toolsets"),
            ("plugins", "plugin_provenance"),
        ):
            if manifest_key in manifest and manifest.get(manifest_key) != resolved_identity.get(
                resolved_key
            ):
                issues.append(f"resolved {resolved_key} does not match manifest")
        if requires_integrity_v1 and manifest.get("docker") != resolved_identity.get(
            "docker_config"
        ):
            issues.append("resolved docker_config does not match manifest")
    if requires_integrity_v1:
        resolved_experiment = resolved_config.get("experiment")
        resolved_model = resolved_config.get("model")
        resolved_sandbox = resolved_config.get("sandbox")
        if not isinstance(resolved_experiment, dict) or manifest.get(
            "experiment"
        ) != resolved_experiment.get("name"):
            issues.append("manifest experiment does not match resolved config")
        if not isinstance(resolved_model, dict) or (
            manifest.get("model_provider") != resolved_model.get("provider")
            or manifest.get("model_name") != resolved_model.get("name")
            or manifest.get("provider_seed_supported")
            is not resolved_model.get("supports_seed", False)
        ):
            issues.append("manifest model identity does not match resolved config")
        if isinstance(resolved_model, dict):

            def normalized_price(field: str) -> Any:
                value = resolved_model.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
                return value

            expected_pricing_identity = {
                "model": resolved_model.get("name"),
                "input_price_per_million": normalized_price("input_price_per_million"),
                "output_price_per_million": normalized_price("output_price_per_million"),
                "cache_read_price_per_million": normalized_price("cache_read_price_per_million"),
                "cache_write_price_per_million": normalized_price("cache_write_price_per_million"),
                "currency": "USD",
                "source": "experiment configuration; user-supplied snapshot",
            }
            expected_pricing = {
                **expected_pricing_identity,
                "hash": content_hash(expected_pricing_identity),
            }
            if pricing != expected_pricing:
                issues.append("pricing snapshot does not match resolved model configuration")
        expected_sandbox_backend = (
            resolved_sandbox.get("backend", "local") if isinstance(resolved_sandbox, dict) else None
        )
        if manifest.get("sandbox_backend") != expected_sandbox_backend:
            issues.append("manifest sandbox backend does not match resolved config")
    resolved_variants = resolved_config.get("variants")
    expected_treatments: dict[str, dict[str, Any]] | None = None
    if isinstance(resolved_variants, list) and all(
        isinstance(row, dict) for row in resolved_variants
    ):
        expected_treatments = {}
        for variant in resolved_variants:
            variant_id = variant.get("id")
            policy = variant.get("policy")
            raw_tools = variant.get("tools")
            instructions = variant.get("instructions")
            plugin_options = variant.get("plugin_options", {})
            if (
                not isinstance(variant_id, str)
                or not variant_id
                or not isinstance(policy, str)
                or not policy
                or (
                    raw_tools is not None
                    and (
                        not isinstance(raw_tools, list)
                        or not all(
                            isinstance(tool, str) and tool in BUILTIN_TOOL_NAMES
                            for tool in raw_tools
                        )
                        or len(set(raw_tools)) != len(raw_tools)
                    )
                )
                or (instructions is not None and not isinstance(instructions, str))
                or not isinstance(plugin_options, dict)
                or variant_id in expected_treatments
            ):
                expected_treatments = None
                break
            tools: str | list[str]
            if raw_tools is None:
                tools = "default"
            else:
                tools = [name for name in BUILTIN_TOOL_NAMES if name in raw_tools]
            expected_treatments[variant_id] = {
                "context_policy": policy,
                "tools": tools,
                "instructions_sha256": (
                    content_hash(instructions) if instructions is not None else None
                ),
                "plugin_options": plugin_options,
            }
    declared_treatments = manifest.get("variant_treatments")
    if declared_treatments is not None or requires_integrity_v1:
        if expected_treatments is None:
            issues.append("resolved variant treatments are invalid")
        elif declared_treatments != expected_treatments:
            issues.append("manifest variant_treatments do not match resolved config")
    docker_runtime = manifest.get("docker_runtime")
    if docker_runtime is not None:
        if not isinstance(docker_runtime, dict):
            issues.append("manifest docker_runtime must be a JSON object")
            docker_runtime = None
        else:
            docker_identity = dict(docker_runtime)
            declared_runtime_hash = docker_identity.pop("hash", None)
            if (
                not isinstance(declared_runtime_hash, str)
                or content_hash(docker_identity) != declared_runtime_hash
            ):
                issues.append("manifest Docker runtime provenance hash mismatch")
    runtime_identity = manifest.get("runtime_identity")
    if runtime_identity is None:
        if requires_integrity_v1 and rows:
            issues.append("manifest is missing runtime identity")
    elif not isinstance(runtime_identity, dict):
        issues.append("manifest runtime_identity must be a JSON object")
        runtime_identity = None
    else:
        required_runtime_fields = {
            "python_implementation",
            "python_version",
            "operating_system",
            "machine",
            "token_counter",
            "hash",
        }
        if set(runtime_identity) != required_runtime_fields or not all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in runtime_identity.items()
        ):
            issues.append("manifest runtime identity has missing, empty, or unknown fields")
            runtime_identity = None
        else:
            unhashed_runtime = {
                key: value for key, value in runtime_identity.items() if key != "hash"
            }
            if runtime_identity.get("hash") != content_hash(unhashed_runtime):
                issues.append("manifest runtime identity hash mismatch")
    expected_token_counter = (
        runtime_identity.get("token_counter") if isinstance(runtime_identity, dict) else "char4-v1"
    )
    if requires_integrity_v1 and manifest.get("token_counter") != expected_token_counter:
        issues.append("manifest token_counter does not match runtime identity")

    strict_plan_identity = requires_integrity_v1
    manifest_tasks = manifest.get("tasks")
    manifest_variants = manifest.get("variants")
    manifest_replicates = manifest.get("replicates")
    manifest_task_constraints = manifest.get("task_constraints")
    if strict_plan_identity:
        if (
            not isinstance(manifest_tasks, list)
            or not manifest_tasks
            or not all(isinstance(item, str) and item for item in manifest_tasks)
            or len(set(manifest_tasks)) != len(manifest_tasks)
            or not isinstance(manifest_variants, list)
            or not manifest_variants
            or not all(isinstance(item, str) and item for item in manifest_variants)
            or len(set(manifest_variants)) != len(manifest_variants)
            or not isinstance(manifest_replicates, list)
            or not manifest_replicates
            or not all(
                isinstance(item, int) and not isinstance(item, bool) and item >= 0
                for item in manifest_replicates
            )
            or len(set(manifest_replicates)) != len(manifest_replicates)
        ):
            issues.append("manifest task/variant/replicate grid is invalid")
        else:
            constraint_map_valid = True
            if not isinstance(manifest_task_constraints, dict) or set(
                manifest_task_constraints
            ) != set(manifest_tasks):
                constraint_map_valid = False
            else:
                for task_id in manifest_tasks:
                    constraint_rows = manifest_task_constraints.get(task_id)
                    if not isinstance(constraint_rows, list):
                        constraint_map_valid = False
                        break
                    constraint_ids: list[str] = []
                    for constraint in constraint_rows:
                        if (
                            not isinstance(constraint, dict)
                            or set(constraint) != {"id", "text", "text_sha256", "redaction_applied"}
                            or not isinstance(constraint.get("id"), str)
                            or not constraint["id"]
                            or not isinstance(constraint.get("text"), str)
                            or not constraint["text"].strip()
                            or redact_text(constraint["text"]) != constraint["text"]
                            or not isinstance(constraint.get("text_sha256"), str)
                            or re.fullmatch(r"[0-9a-f]{64}", constraint["text_sha256"]) is None
                            or not isinstance(constraint.get("redaction_applied"), bool)
                            or (
                                constraint["redaction_applied"] is False
                                and content_hash(constraint["text"]) != constraint["text_sha256"]
                            )
                        ):
                            constraint_map_valid = False
                            break
                        constraint_ids.append(constraint["id"])
                    if len(constraint_ids) != len(set(constraint_ids)):
                        constraint_map_valid = False
                    if not constraint_map_valid:
                        break
            if not constraint_map_valid:
                issues.append("manifest task_constraints map is invalid")
            experiment_settings = resolved_config.get("experiment")
            resolved_task_ids = resolved_identity.get("task_ids")
            if resolved_task_ids != manifest_tasks:
                issues.append("resolved task_ids do not match manifest tasks")
            resolved_variant_ids = (
                [row.get("id") for row in resolved_variants]
                if isinstance(resolved_variants, list)
                and all(isinstance(row, dict) for row in resolved_variants)
                else None
            )
            if resolved_variant_ids != manifest_variants:
                issues.append("resolved variant IDs do not match manifest variants")
            resolved_replicates = (
                experiment_settings.get("replicates")
                if isinstance(experiment_settings, dict)
                else None
            )
            if resolved_replicates != manifest_replicates:
                issues.append("resolved replicates do not match manifest replicates")
            randomize = (
                experiment_settings.get("randomize_variant_order")
                if isinstance(experiment_settings, dict)
                else None
            )
            if not isinstance(randomize, bool):
                issues.append("resolved config has no valid randomize_variant_order setting")
            else:
                expected_plan: list[dict[str, Any]] = []
                block_index = 0
                for task_id in manifest_tasks:
                    for replicate in manifest_replicates:
                        ordered = list(manifest_variants)
                        if randomize:
                            ordered.sort(
                                key=lambda variant_id: (
                                    content_hash(
                                        {
                                            "algorithm": _VARIANT_ORDER_ALGORITHM,
                                            "config_hash": manifest.get("config_hash"),
                                            "task_id": task_id,
                                            "replicate": replicate,
                                            "variant_id": variant_id,
                                        }
                                    ),
                                    variant_id,
                                )
                            )
                        for order_position, variant_id in enumerate(ordered):
                            trial_hash = content_hash(
                                {
                                    "config_hash": manifest.get("config_hash"),
                                    "task_id": task_id,
                                    "variant_id": variant_id,
                                    "replicate": replicate,
                                }
                            )
                            expected_plan.append(
                                {
                                    "schema_version": 1,
                                    "trial_id": (
                                        f"{task_id}--{variant_id}--r{replicate}--{trial_hash[:8]}"
                                    ),
                                    "trial_hash": trial_hash,
                                    "task_id": task_id,
                                    "variant_id": variant_id,
                                    "replicate": replicate,
                                    "block_index": block_index,
                                    "order_position": order_position,
                                }
                            )
                        block_index += 1
                if plan != expected_plan:
                    issues.append("plan.jsonl does not match the deterministic manifest grid/order")
    plan_counts = Counter(str(row.get("trial_id")) for row in plan)
    episode_counts = Counter(str(row.get("trial_id")) for row in rows)
    if any(count != 1 for count in plan_counts.values()):
        issues.append("plan.jsonl contains duplicate trial IDs")
    duplicate_episodes = sorted(
        trial_id for trial_id, count in episode_counts.items() if count != 1
    )
    if duplicate_episodes:
        issues.append(
            "episodes.jsonl contains duplicate trial IDs: " + ", ".join(duplicate_episodes)
        )
    if manifest.get("trial_count") != len(plan):
        issues.append("manifest trial_count does not match plan.jsonl")
    planned = {str(row.get("trial_id")): row for row in plan}
    active_trials_dir = experiment_dir / "trials"
    active_trials_expected = experiment_dir / "trials"
    if active_trials_dir.is_symlink() or (
        active_trials_dir.exists()
        and active_trials_dir.resolve(strict=False) != active_trials_expected
    ):
        issues.append("active trials directory resolves outside the experiment")
    elif active_trials_dir.is_dir():
        for active_trial in sorted(active_trials_dir.iterdir(), key=lambda path: path.name):
            if active_trial.name not in planned:
                issues.append(f"unplanned active trial artifact: {active_trial.name}")
    for trial_id, planned_row in planned.items():
        task_id = planned_row.get("task_id")
        variant_id = planned_row.get("variant_id")
        replicate = planned_row.get("replicate")
        if (
            not isinstance(task_id, str)
            or not isinstance(variant_id, str)
            or not isinstance(replicate, int)
            or isinstance(replicate, bool)
        ):
            issues.append(f"invalid planned trial identity: {trial_id}")
            continue
        expected_trial_hash = content_hash(
            {
                "config_hash": manifest.get("config_hash"),
                "task_id": task_id,
                "variant_id": variant_id,
                "replicate": replicate,
            }
        )
        if planned_row.get("trial_hash") != expected_trial_hash:
            issues.append(f"planned trial hash mismatch: {trial_id}")
    recorded: set[str] = set()
    for row in rows:
        trial_id = str(row.get("trial_id"))
        if trial_id not in planned:
            issues.append(f"unplanned trial in episodes.jsonl: {trial_id}")
            continue
        recorded.add(trial_id)
        if requires_integrity_v1:
            for semantic_issue in result_semantic_issues(row):
                issues.append(f"invalid result semantics for {trial_id}: {semantic_issue}")
        if row.get("trial_hash") != planned[trial_id].get("trial_hash"):
            issues.append(f"trial hash mismatch: {trial_id}")
        for identity_key in (
            "schema_version",
            "task_id",
            "variant_id",
            "replicate",
            "block_index",
            "order_position",
        ):
            if row.get(identity_key) != planned[trial_id].get(identity_key):
                issues.append(f"result {identity_key} does not match plan: {trial_id}")
        if row.get("config_hash") != manifest.get("config_hash"):
            issues.append(f"config hash mismatch: {trial_id}")
        for result_key, manifest_key in (
            ("scaffoldscope_version", "scaffoldscope_version"),
            ("experiment", "experiment"),
            ("model_provider", "model_provider"),
            ("model_name", "model_name"),
        ):
            if manifest_key in manifest and row.get(result_key) != manifest.get(manifest_key):
                issues.append(f"{result_key} mismatch: {trial_id}")
        if "implementation_hash" in manifest and row.get("implementation_hash") != manifest.get(
            "implementation_hash"
        ):
            issues.append(f"implementation hash mismatch: {trial_id}")
        task_sources = manifest.get("task_source_hashes")
        if isinstance(task_sources, dict) and row.get("task_source_hash") != task_sources.get(
            row.get("task_id")
        ):
            issues.append(f"task source hash mismatch: {trial_id}")
        task_provenance = manifest.get("task_provenance")
        provenance = (
            task_provenance.get(row.get("task_id")) if isinstance(task_provenance, dict) else None
        )
        if requires_integrity_v1:
            expected_provenance = {
                "repository": row.get("task_repository"),
                "base_commit": row.get("task_base_commit"),
                "source_hash": row.get("task_source_hash"),
            }
            if provenance != expected_provenance:
                issues.append(f"task provenance mismatch: {trial_id}")
            if row.get("provider_seed_supported") is not manifest.get("provider_seed_supported"):
                issues.append(f"provider seed support mismatch: {trial_id}")
        if "sandbox_backend" in manifest and row.get("sandbox_backend") != manifest.get(
            "sandbox_backend"
        ):
            issues.append(f"sandbox backend mismatch: {trial_id}")
        if (
            requires_integrity_v1
            and manifest.get("sandbox_backend") == "local"
            and any(
                row.get(field) is not None
                for field in ("docker_image", "docker_image_id", "docker_image_platform")
            )
        ):
            issues.append(f"local sandbox result carries Docker provenance: {trial_id}")
        if "plugins" in manifest and row.get("plugins") != manifest.get("plugins"):
            issues.append(f"plugin provenance mismatch: {trial_id}")
        if runtime_identity is not None and row.get("runtime_identity") != runtime_identity:
            issues.append(f"runtime identity mismatch: {trial_id}")
        treatments = manifest.get("variant_treatments")
        treatment = treatments.get(row.get("variant_id")) if isinstance(treatments, dict) else None
        if isinstance(treatment, dict):
            if row.get("variant_policy") != treatment.get("context_policy"):
                issues.append(f"variant policy mismatch: {trial_id}")
            if row.get("variant_instructions_sha256") != treatment.get("instructions_sha256"):
                issues.append(f"variant instruction hash mismatch: {trial_id}")
            if requires_integrity_v1:
                treatment_tools = treatment.get("tools")
                if treatment_tools == "default":
                    treatment_toolset = list(BUILTIN_TOOL_NAMES)
                elif isinstance(treatment_tools, list):
                    treatment_toolset = [
                        name for name in BUILTIN_TOOL_NAMES if name in treatment_tools
                    ]
                else:
                    treatment_toolset = []
                task_toolsets = manifest.get("task_toolsets")
                task_tools = (
                    task_toolsets.get(row.get("task_id"))
                    if isinstance(task_toolsets, dict)
                    else None
                )
                expected_tools = (
                    [name for name in treatment_toolset if name in task_tools]
                    if isinstance(task_tools, list)
                    else None
                )
                if row.get("variant_tools") != expected_tools:
                    issues.append(f"variant tools mismatch: {trial_id}")
        if docker_runtime is not None:
            expected_docker = {
                "sandbox_backend": "docker",
                "docker_image": docker_runtime.get("declared_image"),
                "docker_image_id": docker_runtime.get("image_id"),
                "docker_image_platform": docker_runtime.get("image_platform"),
            }
            for key, expected_value in expected_docker.items():
                if row.get(key) != expected_value:
                    issues.append(f"Docker runtime provenance mismatch for {trial_id}: {key}")
        artifacts = row.get("artifacts", {})
        if not isinstance(artifacts, dict):
            issues.append(f"invalid artifacts object: {trial_id}")
            continue
        unknown_artifacts = sorted(set(artifacts) - {"trace", "patch", "result", "workspace"})
        if unknown_artifacts:
            issues.append(f"unknown artifact field(s) for {trial_id}: {unknown_artifacts}")
        safe_artifacts: dict[str, Path] = {}
        for artifact_type, relative in artifacts.items():
            if not isinstance(relative, str):
                issues.append(f"invalid {artifact_type} artifact path for {trial_id}")
                continue
            unresolved_artifact_path = experiment_dir / relative
            artifact_path = unresolved_artifact_path.resolve()
            try:
                artifact_path.relative_to(experiment_dir)
            except ValueError:
                issues.append(f"artifact escapes experiment directory for {trial_id}: {relative}")
                continue
            expected_artifact_unresolved = (
                experiment_dir
                / "trials"
                / trial_id
                / {
                    "trace": "events.jsonl",
                    "patch": "patch.diff",
                    "result": "result.json",
                    "workspace": "workspace",
                }.get(str(artifact_type), "__invalid_artifact__")
            )
            expected_artifact = expected_artifact_unresolved.resolve()
            if artifact_path != expected_artifact:
                issues.append(
                    f"unexpected {artifact_type} artifact path for {trial_id}: {relative}"
                )
                continue
            artifact_components = [unresolved_artifact_path]
            artifact_components.extend(
                parent
                for parent in unresolved_artifact_path.parents
                if parent != experiment_dir and experiment_dir in parent.parents
            )
            if any(component.is_symlink() for component in artifact_components):
                issues.append(f"{artifact_type} artifact is a symlink for {trial_id}")
                continue
            safe_artifacts[str(artifact_type)] = artifact_path
        for artifact_type in ("trace", "result"):
            required_artifact = safe_artifacts.get(artifact_type)
            if required_artifact is None or not required_artifact.is_file():
                issues.append(f"missing {artifact_type} artifact for {trial_id}")
        result_relative = artifacts.get("result")
        result_path = safe_artifacts.get("result")
        if isinstance(result_relative, str) and result_path is not None and result_path.is_file():
            per_trial = load_json(result_path)
            if per_trial != row:
                issues.append(f"aggregate/per-trial result mismatch: {trial_id}")
        hashes = row.get("artifact_hashes", {})
        if not isinstance(hashes, dict):
            issues.append(f"invalid artifact_hashes object: {trial_id}")
            hashes = {}
        trace_path = safe_artifacts.get("trace")
        if trace_path is not None and trace_path.is_file():
            if hashes.get("trace_sha256") != file_hash(trace_path):
                issues.append(f"trace hash mismatch: {trial_id}")
            try:
                trace_rows = load_jsonl(trace_path)
            except ConfigError as exc:
                issues.append(f"invalid trace JSONL for {trial_id}: {exc}")
                trace_rows = []
            has_normal_patch_evidence = (
                "patch" in artifacts
                or "patch_sha256" in row
                or "patch_bytes" in row
                or "evaluation" in row
            )
            if requires_integrity_v1:
                task_constraints = (
                    manifest_task_constraints.get(row.get("task_id"))
                    if isinstance(manifest_task_constraints, dict)
                    else None
                )
                for lifecycle_issue in trace_lifecycle_issues(
                    trace_rows,
                    expected_trial=planned[trial_id],
                    result=row,
                    require_artifact_events=has_normal_patch_evidence,
                    constraints=task_constraints,
                ):
                    issues.append(f"invalid trace lifecycle for {trial_id}: {lifecycle_issue}")
        patch_path = safe_artifacts.get("patch")
        if artifacts.get("patch") is not None and (patch_path is None or not patch_path.is_file()):
            issues.append(f"missing patch artifact for {trial_id}")
        elif patch_path is not None and patch_path.is_file():
            actual_patch_hash = file_hash(patch_path)
            actual_patch_bytes = patch_path.stat().st_size
            if hashes.get("patch_sha256") != actual_patch_hash:
                issues.append(f"patch artifact hash mismatch: {trial_id}")
            if row.get("patch_sha256") != actual_patch_hash:
                issues.append(f"patch_sha256 field mismatch: {trial_id}")
            if "patch_bytes" in row and row.get("patch_bytes") != actual_patch_bytes:
                issues.append(f"patch_bytes field mismatch: {trial_id}")
    missing = set(planned) - recorded
    if missing:
        issues.append(f"{len(missing)} planned trial(s) have no recorded result")
    overlays_are_well_shaped = True
    external_evaluations = experiment_dir / "external-evaluations"
    external_evaluations_expected = experiment_dir / "external-evaluations"
    if external_evaluations.is_symlink() or (
        external_evaluations.exists()
        and external_evaluations.resolve(strict=False) != external_evaluations_expected
    ):
        issues.append("external-evaluations directory resolves outside the experiment")
        overlays_are_well_shaped = False
    elif external_evaluations.is_dir():
        recorded_cells: dict[tuple[str, int], set[str]] = defaultdict(set)
        for row in rows:
            row_strategy = row.get("variant_id")
            row_replicate = row.get("replicate")
            row_task = row.get("task_id")
            if (
                isinstance(row_strategy, str)
                and isinstance(row_replicate, int)
                and not isinstance(row_replicate, bool)
                and isinstance(row_task, str)
            ):
                recorded_cells[(row_strategy, row_replicate)].add(row_task)
        for overlay_path in sorted(external_evaluations.glob("*.json")):
            if overlay_path.is_symlink():
                issues.append(f"external evaluation overlay is a symlink: {overlay_path.name}")
                overlays_are_well_shaped = False
                continue
            if not overlay_path.is_file():
                issues.append(
                    f"external evaluation overlay is not a regular file: {overlay_path.name}"
                )
                overlays_are_well_shaped = False
                continue
            overlay = load_json(overlay_path)
            if not isinstance(overlay, dict):
                issues.append(f"external evaluation overlay is not an object: {overlay_path.name}")
                overlays_are_well_shaped = False
                continue
            strategy = overlay.get("strategy")
            replicate = overlay.get("replicate")
            outcomes = overlay.get("outcomes")
            if (
                not isinstance(strategy, str)
                or not isinstance(manifest_variants, list)
                or (strategy not in manifest_variants)
            ):
                issues.append(
                    f"external evaluation overlay has undeclared strategy: {overlay_path.name}"
                )
                overlays_are_well_shaped = False
            if (
                not isinstance(replicate, int)
                or isinstance(replicate, bool)
                or not isinstance(manifest_replicates, list)
                or replicate not in manifest_replicates
            ):
                issues.append(
                    f"external evaluation overlay has undeclared replicate: {overlay_path.name}"
                )
                overlays_are_well_shaped = False
            if requires_integrity_v1 and (
                not isinstance(declared_treatments, dict)
                or not isinstance(strategy, str)
                or strategy not in declared_treatments
            ):
                issues.append(
                    f"external evaluation overlay has no declared treatment: {overlay_path.name}"
                )
                overlays_are_well_shaped = False
            if not isinstance(outcomes, dict):
                issues.append(
                    f"external evaluation overlay has invalid outcomes: {overlay_path.name}"
                )
                overlays_are_well_shaped = False
            elif (
                isinstance(strategy, str)
                and isinstance(replicate, int)
                and not isinstance(replicate, bool)
            ):
                expected_tasks = recorded_cells.get((strategy, replicate))
                observed_tasks = set(outcomes)
                if expected_tasks is None or observed_tasks != expected_tasks:
                    issues.append(
                        "external evaluation overlay does not match its generated cell: "
                        f"{overlay_path.name}; expected={sorted(expected_tasks or set())}, "
                        f"observed={sorted(observed_tasks)}"
                    )
            for field in ("evaluator_version", "evaluator_run_id", "image_set_digest"):
                if not isinstance(overlay.get(field), str) or not overlay.get(field):
                    issues.append(
                        f"external evaluation overlay has invalid {field}: {overlay_path.name}"
                    )
                    overlays_are_well_shaped = False
    if overlays_are_well_shaped:
        try:
            apply_external_evaluations(experiment_dir, rows)
        except ConfigError as exc:
            issues.append(str(exc))
    return not issues, issues
