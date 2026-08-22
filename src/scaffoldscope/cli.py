"""Command-line interface for planning, running, checking, and reporting studies."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scaffoldscope import __version__
from scaffoldscope.bundle import create_evidence_bundle, verify_evidence_bundle
from scaffoldscope.docker_sandbox import docker_preflight
from scaffoldscope.errors import ConfigError, ScaffoldScopeError
from scaffoldscope.locking import experiment_lock
from scaffoldscope.operations import budget_estimate, experiment_status, trial_inventory
from scaffoldscope.plugins import BUILTIN_PLUGIN_NAMES, PluginKind, PluginRegistry
from scaffoldscope.replay import replay_trial
from scaffoldscope.report import check_experiment, write_report
from scaffoldscope.runner import clean_workspaces, run_experiment
from scaffoldscope.schema import RunConfig
from scaffoldscope.schema_export import config_schema_text, export_config_schema
from scaffoldscope.starter import create_starter_project
from scaffoldscope.swebench import (
    export_swebench_matrix,
    export_swebench_predictions,
    import_swebench_manifest,
    ingest_swebench_results,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scaffoldscope",
        description="Hold the model still. Measure the scaffold.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    initializer = subparsers.add_parser("init", help="create a safe, runnable starter experiment")
    initializer.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path("scaffoldscope-study"),
        help="starter destination (default: ./scaffoldscope-study)",
    )
    initializer.add_argument(
        "--name",
        default="my-context-ablation",
        help="experiment identifier written to the starter config",
    )

    validate = subparsers.add_parser("validate", help="validate an experiment config")
    validate.add_argument("config", type=Path)

    plan = subparsers.add_parser(
        "plan", help="expand and persist a trial matrix without running it"
    )
    _add_run_arguments(plan)

    run = subparsers.add_parser("run", help="run or resume an experiment")
    _add_run_arguments(run)

    report = subparsers.add_parser(
        "report", help="regenerate Markdown, HTML, JSON, and CSV reports"
    )
    report.add_argument("experiment_dir", type=Path)
    report.add_argument("--bootstrap-samples", type=int)
    report.add_argument("--analysis-seed", type=int)
    report.add_argument("--sesoi", type=float)
    report.add_argument("--open", action="store_true", dest="open_report")

    status = subparsers.add_parser(
        "status", help="show durable progress without rewriting experiment files"
    )
    status.add_argument("experiment_dir", type=Path)
    status.add_argument("--json", action="store_true", dest="as_json")

    trials = subparsers.add_parser(
        "trials", help="list every planned trial and its durable outcome state"
    )
    trials.add_argument("experiment_dir", type=Path)
    trials.add_argument("--status", dest="filter_status")
    trials.add_argument("--variant")
    trials.add_argument("--task")
    trials.add_argument("--jsonl", action="store_true")

    replay = subparsers.add_parser(
        "replay", help="inspect one trace strictly offline without running tools or models"
    )
    replay.add_argument("experiment_dir", type=Path)
    replay.add_argument("trial_id")
    replay.add_argument("--json", action="store_true", dest="as_json")

    budget = subparsers.add_parser(
        "budget", help="estimate grid size, power, and configured spending bounds"
    )
    budget.add_argument("config", type=Path)
    budget.add_argument("--json", action="store_true", dest="as_json")

    check = subparsers.add_parser(
        "check", help="verify result-bundle completeness and identity hashes"
    )
    check.add_argument("experiment_dir", type=Path)
    check.add_argument("--strict", action="store_true", help="also fail on report warnings")

    demo = subparsers.add_parser("demo", help="run the bundled offline engine demonstration")
    demo.add_argument(
        "--directory",
        type=Path,
        default=Path("scaffoldscope-demo"),
        help="copy the demo project here (default: ./scaffoldscope-demo)",
    )
    demo.add_argument("--open", action="store_true", dest="open_report")

    clean = subparsers.add_parser("clean", help="remove generated workspaces but retain evidence")
    clean.add_argument("experiment_dir", type=Path)
    clean.add_argument(
        "--workspaces",
        action="store_true",
        required=True,
        help="confirm removal of generated trial workspaces",
    )

    bundle = subparsers.add_parser(
        "bundle", help="create a deterministic, workspace-free evidence archive"
    )
    bundle.add_argument("experiment_dir", type=Path)
    bundle.add_argument("--out", type=Path, required=True)

    verify_bundle = subparsers.add_parser(
        "verify-bundle", help="verify an evidence archive without extracting it"
    )
    verify_bundle.add_argument("archive", type=Path)

    plugins = subparsers.add_parser(
        "plugins", help="list extension points or import-check installed plugins"
    )
    plugins.add_argument(
        "--check",
        action="store_true",
        help="explicitly import and validate every discovered third-party plugin",
    )
    plugins.add_argument("--json", action="store_true", dest="as_json")

    schema = subparsers.add_parser(
        "schema", help="print or export the packaged experiment JSON Schema"
    )
    schema.add_argument("--out", type=Path)

    importer = subparsers.add_parser(
        "import-swebench", help="convert downloaded SWE-bench rows to a local task manifest"
    )
    importer.add_argument("source", type=Path)
    importer.add_argument("--repo-cache", type=Path, required=True)
    importer.add_argument("--out", type=Path, required=True)

    exporter = subparsers.add_parser(
        "export-swebench", help="export one paired cell for the official evaluator"
    )
    exporter.add_argument("experiment_dir", type=Path)
    exporter.add_argument("--strategy", required=True)
    exporter.add_argument("--replicate", type=int, required=True)
    exporter.add_argument("--out", type=Path, required=True)

    matrix_exporter = subparsers.add_parser(
        "export-swebench-matrix",
        help="export every treatment cell with unique official-evaluator run IDs",
    )
    matrix_exporter.add_argument("experiment_dir", type=Path)
    matrix_exporter.add_argument("--out-dir", type=Path, required=True)
    matrix_exporter.add_argument("--dataset-name", default="SWE-bench/SWE-bench_Lite")
    matrix_exporter.add_argument("--split", default="test")

    ingest = subparsers.add_parser(
        "ingest-swebench", help="attach an immutable official-evaluator overlay to one cell"
    )
    ingest.add_argument("experiment_dir", type=Path)
    ingest.add_argument("results", type=Path)
    ingest.add_argument("--strategy", required=True)
    ingest.add_argument("--replicate", type=int, required=True)
    ingest.add_argument("--evaluator-version", required=True)
    ingest.add_argument("--evaluator-run-id", required=True)
    ingest.add_argument("--image-set-digest", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="check local or experiment-specific prerequisites"
    )
    doctor.add_argument("--config", type=Path, help="preflight one experiment without running it")
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("config", type=Path)


def _run(args: argparse.Namespace, *, dry_run: bool) -> int:
    config = RunConfig.load(args.config)
    summary = run_experiment(
        config,
        dry_run=dry_run,
    )
    if dry_run:
        print(f"Planned {summary.scheduled} trials in {summary.experiment_dir}")
        print(f"Review {summary.experiment_dir / 'plan.jsonl'} before spending model budget.")
        return 0
    with experiment_lock(summary.experiment_dir):
        valid, issues = check_experiment(summary.experiment_dir)
        if not valid:
            raise ScaffoldScopeError(
                "Final experiment integrity check failed: " + "; ".join(issues)
            )
        report = write_report(summary.experiment_dir)
    print(
        f"Finished {summary.scheduled} trials: {summary.completed} executed, "
        f"{summary.skipped} resumed, {summary.failed} infrastructure failure(s)."
    )
    print(f"Report: {summary.experiment_dir / 'report.md'}")
    print(f"Pair coverage: {report['pair_coverage']:.1%}")
    return 2 if summary.failed else 0


def _copy_demo(destination: Path) -> Path:
    source = Path(__file__).resolve().parent / "demo"
    destination = destination.resolve()
    config_path = destination / "experiment.json"
    if destination.exists():
        if config_path.is_file():
            return config_path
        raise ScaffoldScopeError(
            f"Demo destination exists but is not a ScaffoldScope demo: {destination}"
        )
    shutil.copytree(source, destination)
    return config_path


def _demo(args: argparse.Namespace) -> int:
    config_path = _copy_demo(args.directory)
    config = RunConfig.load(config_path)
    summary = run_experiment(config)
    with experiment_lock(summary.experiment_dir):
        report = write_report(summary.experiment_dir)
    report_path = summary.experiment_dir / "report.html"
    print(
        "Offline demo complete. This validates context pressure, compaction, patching, "
        "tests, traces, and reporting; it does not benchmark model intelligence."
    )
    print(f"Report: {report_path}")
    warning_codes = list(dict.fromkeys(item["code"] for item in report["warnings"]))
    print(f"Warnings: {', '.join(warning_codes) or 'none'}")
    if args.open_report:
        webbrowser.open(report_path.as_uri())
    return 2 if summary.failed else 0


def _doctor(config_path: Path | None = None) -> int:
    capabilities = {
        "scaffoldscope": __version__,
        "python": sys.version.split()[0],
        "python_supported": (3, 10) <= sys.version_info < (3, 15),
        "git": shutil.which("git"),
        "docker": shutil.which("docker"),
        "runtime_dependencies": "none",
    }
    preflight_passed = bool(capabilities["python_supported"])
    if config_path is not None:
        config = RunConfig.load(config_path)
        credential_status = "not-required"
        if config.model.provider == "openai_compatible" and config.model.requires_api_key:
            if os.environ.get(config.model.api_key_env):
                credential_status = "configured"
            else:
                credential_status = "missing"
                preflight_passed = False
        experiment: dict[str, Any] = {
            "name": config.experiment.name,
            "config_hash": config.config_hash,
            "trials": (
                len(config.tasks) * len(config.experiment.replicates) * len(config.variants)
            ),
            "provider": config.model.provider,
            "credential_status": credential_status,
            "provider_connectivity": (
                "not-applicable" if config.model.provider == "scripted" else "not-checked"
            ),
            "sandbox_backend": config.sandbox.backend,
            "plugins": config.plugin_provenance,
        }
        if config.sandbox.backend == "docker":
            if config.docker is None:
                raise ScaffoldScopeError("Docker sandbox configuration is missing")
            experiment["docker_runtime"] = docker_preflight(config.docker)
        capabilities["experiment"] = experiment
    capabilities["preflight_passed"] = preflight_passed
    # Only literal status labels are rendered; regression coverage proves that neither the
    # credential nor its environment-variable name can reach this output sink.
    # codeql[py/clear-text-logging-sensitive-data]
    print(json.dumps(capabilities, indent=2))
    return 0 if preflight_passed else 2


def _print_status(value: dict[str, Any]) -> None:
    print(f"Experiment: {value['experiment']} ({str(value['config_hash'])[:8]})")
    print(
        f"Progress: {value['completed_trials']} / {value['scheduled_trials']} "
        f"({float(value['progress']):.1%}); remaining: {value['remaining_trials']}"
    )
    print(
        f"Paired blocks: {value['complete_paired_blocks']} / "
        f"{value['expected_paired_blocks']} ({float(value['pair_coverage']):.1%})"
    )
    if value.get("started_without_result") or value.get("archived_attempts"):
        print(
            f"Partial attempts: active={value.get('started_without_result', 0)}, "
            f"archived={value.get('archived_attempts', 0)}"
        )
    statuses = value.get("status_counts")
    if isinstance(statuses, dict) and statuses:
        print("Outcomes: " + ", ".join(f"{key}={statuses[key]}" for key in sorted(statuses)))
    print(f"Reported tokens: {value['reported_tokens']}")
    malformed = value.get("malformed_result_trials")
    if isinstance(malformed, list) and malformed:
        print(f"Integrity warning: {len(malformed)} result(s) have invalid identity; run check.")
    cost = value.get("reported_cost_usd")
    print(f"Reported configured-price cost: {'n/a' if cost is None else f'${float(cost):.4f}'}")


def _print_budget(value: dict[str, Any]) -> None:
    print(
        f"Grid: {value['tasks']} tasks x {value['replicates']} replicates x "
        f"{value['variants']} variants = {value['scheduled_trials']} trials"
    )
    print(f"Maximum model calls: {value['maximum_model_calls']}")
    print(f"Maximum token budget: {value['maximum_total_tokens']}")
    cost = value.get("maximum_configured_cost_usd")
    print(f"Configured-price upper bound: {'n/a' if cost is None else f'${float(cost):.2f}'}")
    mde = value.get("prospective_paired_mde")
    print(f"Prospective paired MDE: {'n/a' if mde is None else f'{float(mde):.1%}'}")
    warnings = value.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, dict):
                print(f"Warning [{warning.get('code')}]: {warning.get('message')}")


def _plugin_inventory(*, check: bool) -> list[dict[str, Any]]:
    registry = PluginRegistry.discover()
    inventory: list[dict[str, Any]] = [
        {
            "name": name,
            "normalized_name": name.replace("_", "-"),
            "kind": kind.value,
            "built_in": True,
            "status": "ready",
        }
        for kind in PluginKind
        for name in sorted(BUILTIN_PLUGIN_NAMES[kind])
    ]
    for info in registry.plugins():
        item: dict[str, Any] = {**info.to_dict(), "built_in": False, "status": "discovered"}
        if check:
            loaded = (
                registry.load_context_policy(info.name)
                if info.kind is PluginKind.CONTEXT_POLICY
                else registry.load_model_provider(info.name)
            )
            item.update(loaded.provenance())
            item["status"] = "ready"
        inventory.append(item)
    return inventory


def _require_experiment_directory(path: Path) -> None:
    if not path.is_dir():
        raise ConfigError(f"Experiment directory does not exist: {path}")


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            project = create_starter_project(args.directory, name=args.name)
            verb = "Initialized" if project.initialized else "Already initialized"
            print(f"{verb}: {project.root}")
            print(f"Config: {project.config_path}")
            print(f'Next: scaffoldscope validate "{project.config_path}"')
            print(f'Then: scaffoldscope run "{project.config_path}"')
            return 0
        if args.command == "validate":
            config = RunConfig.load(args.config)
            print(
                f"Valid: {len(config.tasks)} tasks x {len(config.experiment.replicates)} replicates "
                f"x {len(config.variants)} variants = "
                f"{len(config.tasks) * len(config.experiment.replicates) * len(config.variants)} trials"
            )
            print(f"Config hash: {config.config_hash}")
            return 0
        if args.command == "plan":
            return _run(args, dry_run=True)
        if args.command == "run":
            return _run(args, dry_run=False)
        if args.command == "report":
            _require_experiment_directory(args.experiment_dir)
            with experiment_lock(args.experiment_dir):
                summary = write_report(
                    args.experiment_dir,
                    bootstrap_samples=args.bootstrap_samples,
                    analysis_seed=args.analysis_seed,
                    sesoi=args.sesoi,
                )
            report_path = args.experiment_dir.resolve() / "report.html"
            print(f"Wrote report for {summary['recorded_trials']} trials to {report_path}")
            if args.open_report:
                webbrowser.open(report_path.as_uri())
            return 0
        if args.command == "status":
            value = experiment_status(args.experiment_dir)
            if args.as_json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                _print_status(value)
            return 0
        if args.command == "trials":
            rows = trial_inventory(
                args.experiment_dir,
                status=args.filter_status,
                variant=args.variant,
                task=args.task,
            )
            if args.jsonl:
                for row in rows:
                    print(json.dumps(row, sort_keys=True, separators=(",", ":")))
            else:
                for row in rows:
                    solved = "-" if row["solved"] is None else str(row["solved"]).lower()
                    print(
                        f"{row['trial_id']}  status={row['status']}  solved={solved}  "
                        f"tokens={row['total_tokens'] if row['total_tokens'] is not None else '-'}"
                    )
                print(f"{len(rows)} trial(s)")
            return 0
        if args.command == "replay":
            replay = replay_trial(args.experiment_dir, args.trial_id)
            if args.as_json:
                print(json.dumps(replay, indent=2, sort_keys=True))
            else:
                print(
                    f"Offline replay: {replay['trial_id']} "
                    f"({replay['variant_id']}, status={replay['status']}, solved={replay['solved']})"
                )
                for event in replay["timeline"]:
                    print(f"{int(event['sequence']):04d}  {event['summary']}")
                print(f"Trace SHA-256: {replay['trace_sha256']}")
            return 0
        if args.command == "budget":
            value = budget_estimate(RunConfig.load(args.config))
            if args.as_json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                _print_budget(value)
            return 0
        if args.command == "check":
            _require_experiment_directory(args.experiment_dir)
            with experiment_lock(args.experiment_dir):
                valid, issues = check_experiment(args.experiment_dir)
                if valid and args.strict:
                    summary = write_report(args.experiment_dir)
                    issues.extend(
                        f"report warning {item['code']}: {item['message']}"
                        for item in summary["warnings"]
                    )
                    valid = not issues
            if valid:
                print("PASS: result bundle is complete and internally consistent.")
                return 0
            print("FAIL:")
            for issue in issues:
                print(f"- {issue}")
            return 2
        if args.command == "demo":
            return _demo(args)
        if args.command == "clean":
            if not (args.experiment_dir / "manifest.json").is_file():
                raise ConfigError(f"Not a ScaffoldScope experiment: {args.experiment_dir}")
            with experiment_lock(args.experiment_dir):
                removed = clean_workspaces(args.experiment_dir)
            print(f"Removed {removed} generated workspace(s). Traces, patches, and results remain.")
            return 0
        if args.command == "bundle":
            _require_experiment_directory(args.experiment_dir)
            with experiment_lock(args.experiment_dir):
                manifest = create_evidence_bundle(args.experiment_dir, args.out)
            print(f"Wrote evidence bundle: {args.out.resolve()}")
            print(f"Bundle hash: {manifest['bundle_hash']}")
            print(f"Evidence files: {len(manifest['files'])}")
            return 0
        if args.command == "verify-bundle":
            manifest = verify_evidence_bundle(args.archive)
            print(
                "PASS: evidence archive is structurally and semantically consistent "
                f"({len(manifest['files'])} files, {manifest['bundle_hash']})."
            )
            return 0
        if args.command == "plugins":
            inventory = _plugin_inventory(check=args.check)
            if args.as_json:
                print(json.dumps(inventory, indent=2, sort_keys=True))
            else:
                for item in inventory:
                    source = "built-in" if item["built_in"] else item.get("distribution")
                    print(f"{item['kind']:<15} {item['name']:<28} {item['status']:<10} {source}")
            return 0
        if args.command == "schema":
            if args.out is None:
                print(config_schema_text(), end="")
            else:
                destination = export_config_schema(args.out)
                print(f"Wrote experiment JSON Schema: {destination}")
            return 0
        if args.command == "import-swebench":
            count = import_swebench_manifest(args.source, args.repo_cache, args.out)
            print(f"Wrote {count} task rows to {args.out.resolve()}")
            return 0
        if args.command == "export-swebench":
            count = export_swebench_predictions(
                args.experiment_dir,
                args.out,
                strategy=args.strategy,
                replicate=args.replicate,
            )
            print(f"Wrote {count} predictions to {args.out.resolve()}")
            return 0
        if args.command == "export-swebench-matrix":
            matrix = export_swebench_matrix(
                args.experiment_dir,
                args.out_dir,
                dataset_name=args.dataset_name,
                split=args.split,
            )
            value = json.loads(matrix.read_text(encoding="utf-8"))
            print(f"Wrote {len(value['cells'])} evaluator cells to {args.out_dir.resolve()}")
            print(f"Matrix manifest: {matrix}")
            print(f"Runbook: {args.out_dir.resolve() / 'evaluate.sh'}")
            return 0
        if args.command == "ingest-swebench":
            _require_experiment_directory(args.experiment_dir)
            with experiment_lock(args.experiment_dir):
                overlay = ingest_swebench_results(
                    args.experiment_dir,
                    args.results,
                    strategy=args.strategy,
                    replicate=args.replicate,
                    evaluator_version=args.evaluator_version,
                    evaluator_run_id=args.evaluator_run_id,
                    image_set_digest=args.image_set_digest,
                )
                summary = write_report(args.experiment_dir)
            print(f"Wrote immutable evaluator overlay: {overlay}")
            print(f"Updated report pair coverage: {summary['pair_coverage']:.1%}")
            return 0
        if args.command == "doctor":
            return _doctor(args.config)
    except ScaffoldScopeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: operating-system failure: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    parser.error(f"Unhandled command: {args.command}")
