"""Strictly offline trace inspection for one completed trial."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import file_hash, load_json, load_jsonl


def _safe_trial_dir(experiment_dir: Path, trial_id: str) -> Path:
    if not trial_id:
        raise ConfigError("trial_id must be non-empty")
    root = (experiment_dir.resolve() / "trials").resolve()
    path = (root / trial_id).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ConfigError(f"Unsafe trial id: {trial_id!r}") from exc
    return path


def _event_summary(event: dict[str, Any]) -> str:
    event_type = str(event.get("type", "unknown"))
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return event_type
    turn = payload.get("turn")
    prefix = f"turn {turn}: " if isinstance(turn, int) and not isinstance(turn, bool) else ""
    if event_type == "context_prepared":
        return (
            f"{prefix}context {payload.get('reason', 'prepared')} "
            f"{payload.get('tokens_before', '?')} -> {payload.get('tokens_after', '?')} tokens"
        )
    if event_type == "model_request":
        messages = payload.get("messages")
        count = len(messages) if isinstance(messages, list) else "?"
        return f"{prefix}model request ({count} messages)"
    if event_type == "model_response":
        usage = payload.get("usage")
        total = usage.get("total_tokens") if isinstance(usage, dict) else "?"
        return f"{prefix}model response ({total} tokens, finish={payload.get('finish_reason')})"
    if event_type == "tool_result":
        duration = payload.get("duration_seconds")
        rendered_duration = (
            f"{float(duration):.3f}s"
            if isinstance(duration, (int, float)) and not isinstance(duration, bool)
            else "unknown duration"
        )
        return (
            f"{prefix}tool {payload.get('tool')} "
            f"({'ok' if payload.get('ok') else 'error'}, "
            f"{rendered_duration})"
        )
    if event_type in {"model_error", "protocol_error", "trial_error", "harness_error"}:
        return f"{prefix}{event_type}: {payload.get('message', payload.get('error', ''))}"
    if event_type == "trial_finished":
        return f"trial finished: {payload.get('status')} solved={payload.get('solved')}"
    return prefix + event_type.replace("_", " ")


def replay_trial(experiment_dir: Path, trial_id: str) -> dict[str, Any]:
    """Validate and summarize a trace; never invoke a provider or workspace tool."""

    root = experiment_dir.resolve()
    trial_dir = _safe_trial_dir(root, trial_id)
    result_path = trial_dir / "result.json"
    trace_path = trial_dir / "events.jsonl"
    result = load_json(result_path)
    if not isinstance(result, dict):
        raise ConfigError(f"Trial result must be a JSON object: {result_path}")
    if result.get("trial_id") != trial_id:
        raise ConfigError("Trial result identity does not match the requested trial")
    hashes = result.get("artifact_hashes")
    if not isinstance(hashes, dict) or hashes.get("trace_sha256") != file_hash(trace_path):
        raise ConfigError("Trial trace hash does not match result.json")
    events = load_jsonl(trace_path)
    sequences = [event.get("sequence") for event in events]
    if not all(
        isinstance(item, int) and not isinstance(item, bool) for item in sequences
    ) or sequences != list(range(1, len(events) + 1)):
        raise ConfigError("Trial trace sequence is incomplete or out of order")
    if not events or events[-1].get("type") != "trial_finished":
        raise ConfigError("Trial trace has no terminal trial_finished event")
    return {
        "schema_version": 1,
        "kind": "offline-trial-replay",
        "experiment_dir": str(root),
        "trial_id": trial_id,
        "task_id": result.get("task_id"),
        "variant_id": result.get("variant_id"),
        "replicate": result.get("replicate"),
        "status": result.get("status"),
        "solved": result.get("solved"),
        "trace_sha256": hashes["trace_sha256"],
        "event_count": len(events),
        "timeline": [
            {
                "sequence": event.get("sequence"),
                "timestamp": event.get("timestamp"),
                "type": event.get("type"),
                "summary": _event_summary(event),
                "payload": event.get("payload"),
            }
            for event in events
        ],
    }
