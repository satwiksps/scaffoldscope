"""Shared semantic validation for persisted trial results and event traces."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from typing import Any, TypeGuard, cast

from scaffoldscope.agent import parse_response
from scaffoldscope.errors import ProtocolError
from scaffoldscope.jsonutil import canonical_json, content_hash
from scaffoldscope.tokenization import Char4TokenCounter

PERSISTED_RESULT_STATUSES = frozenset(
    {
        "resolved",
        "unresolved",
        "context_overflow",
        "turn_limit",
        "token_limit",
        "cost_limit",
        "cost_unobservable",
        "model_error",
        "infrastructure_error",
        "harness_error",
        "awaiting_external_evaluation",
    }
)
_AGENT_STATUSES = frozenset(
    {
        "completed",
        "context_overflow",
        "turn_limit",
        "token_limit",
        "cost_limit",
        "cost_unobservable",
        "model_error",
    }
)
_EXCEPTION_RESULT_STATUSES = frozenset({"infrastructure_error", "harness_error"})
_AGENT_FIELDS = {
    "status",
    "final",
    "turns",
    "model_calls",
    "tool_calls",
    "protocol_errors",
    "usage",
    "peak_active_context_tokens",
    "peak_canonical_context_tokens",
    "compaction_count",
    "compactions",
    "context_checks",
    "lexical_constraint_availability_rate",
    "model_latency_seconds",
    "tool_latency_seconds",
    "error",
    "provider_models",
    "provider_fingerprints",
    "model_trajectory_sha256",
}
_USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "cost_usd",
    "usage_sources",
    "complete",
}
_EVALUATION_FIELDS = {
    "passed",
    "returncode",
    "output",
    "duration_seconds",
    "constraint_checks",
    "constraint_details",
    "behavioral_adherence",
    "evaluator_integrity",
    "evaluator_integrity_details",
}
_CONTEXT_DECISION_FIELDS = {
    "policy",
    "reason",
    "compaction_event",
    "history_compacted",
    "tokens_before",
    "tokens_after",
    "canonical_tokens",
    "input_limit",
    "compression_ratio",
    "kept_message_ids",
    "dropped_message_ids",
    "summary_source_ids",
    "summary_tokens",
    "selected_scores",
    "lexical_constraint_availability",
}
_EVENT_TYPES = {
    "trial_started",
    "agent_started",
    "context_overflow",
    "context_prepared",
    "token_preflight_rejected",
    "cost_preflight_rejected",
    "model_request",
    "model_attempt_failed",
    "model_response",
    "model_error",
    "cost_observability_lost",
    "protocol_error",
    "agent_final",
    "tool_result",
    "agent_finished",
    "evaluation_finished",
    "patch_captured",
    "trial_error",
    "harness_error",
    "trial_finished",
}
_COMMON_RESULT_FIELDS = {
    "schema_version",
    "trial_id",
    "trial_hash",
    "task_id",
    "variant_id",
    "replicate",
    "block_index",
    "order_position",
    "scaffoldscope_version",
    "experiment",
    "config_hash",
    "implementation_hash",
    "task_source_hash",
    "task_repository",
    "task_base_commit",
    "variant_policy",
    "model_provider",
    "model_name",
    "sandbox_backend",
    "docker_image",
    "docker_image_id",
    "docker_image_platform",
    "runtime_identity",
    "plugins",
    "variant_tools",
    "variant_instructions_sha256",
    "provider_seed_supported",
    "status",
    "infrastructure_valid",
    "evaluation_valid",
    "solved",
    "governed_solved",
    "started_at",
    "finished_at",
    "wall_seconds",
    "artifacts",
    "artifact_hashes",
}
_NORMAL_RESULT_FIELDS = _COMMON_RESULT_FIELDS | {
    "agent",
    "evaluation",
    "patch_sha256",
    "patch_bytes",
}
_EXCEPTION_RESULT_FIELDS = _COMMON_RESULT_FIELDS | {"error"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MESSAGE_ID = re.compile(r"^m[0-9]{5,}$")
_RFC3339_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)$"
)


def _same_json(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def _nonnegative_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite_nonnegative(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or _RFC3339_UTC.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() == timedelta(0) else None


def _lexical_constraint_availability(
    messages: list[dict[str, str]], constraints: list[dict[str, str]]
) -> dict[str, bool]:
    text = " ".join(message["content"] for message in messages)
    normalized = " ".join(text.lower().split())
    availability: dict[str, bool] = {}
    for constraint in constraints:
        identifier = constraint["id"]
        exact = " ".join(constraint["text"].lower().split())
        availability[identifier] = exact in normalized or f"[{identifier.lower()}]" in normalized
    return availability


def _tool_result_model_content(payload: dict[str, Any]) -> str:
    model_payload = {
        "ok": payload.get("ok"),
        "content": payload.get("content"),
        "metadata": payload.get("metadata"),
    }
    return (
        f'<tool_result name="{payload.get("tool")}">\n'
        + json.dumps(model_payload, ensure_ascii=False)
        + "\n</tool_result>"
    )


def _tool_model_content_matches(left: str, right: str) -> bool:
    """Compare tool observations without depending on JSON object key order."""

    pattern = re.compile(r'^<tool_result name="([^"]+)">\n(.*)\n</tool_result>$', re.DOTALL)
    left_match = pattern.fullmatch(left)
    right_match = pattern.fullmatch(right)
    if left_match is None or right_match is None or left_match.group(1) != right_match.group(1):
        return False
    try:
        return _same_json(json.loads(left_match.group(2)), json.loads(right_match.group(2)))
    except (TypeError, ValueError):
        return False


def _context_decision_issues(decision: Any) -> list[str]:
    if not isinstance(decision, dict):
        return ["context decision must be an object"]
    issues: list[str] = []
    if set(decision) != _CONTEXT_DECISION_FIELDS:
        issues.append("context decision has missing or unknown fields")
    for field in ("policy", "reason"):
        if not isinstance(decision.get(field), str) or not decision.get(field):
            issues.append(f"context decision {field} must be a non-empty string")
    for field in ("compaction_event", "history_compacted"):
        if not isinstance(decision.get(field), bool):
            issues.append(f"context decision {field} must be boolean")
    for field in (
        "tokens_before",
        "tokens_after",
        "canonical_tokens",
        "input_limit",
        "summary_tokens",
    ):
        if not _nonnegative_int(decision.get(field)):
            issues.append(f"context decision {field} must be a non-negative integer")
    if (
        _nonnegative_int(decision.get("tokens_after"))
        and _nonnegative_int(decision.get("input_limit"))
        and decision["tokens_after"] > decision["input_limit"]
    ):
        issues.append("context decision tokens_after exceeds input_limit")
    before = decision.get("tokens_before")
    after = decision.get("tokens_after")
    ratio = decision.get("compression_ratio")
    if _nonnegative_int(before) and _nonnegative_int(after):
        expected_ratio = after / before if before else 1.0
        if ratio != expected_ratio:
            issues.append("context decision compression_ratio is inconsistent")
    elif not _finite_nonnegative(ratio):
        issues.append("context decision compression_ratio must be finite and non-negative")

    id_lists: dict[str, list[str]] = {}
    for field in ("kept_message_ids", "dropped_message_ids", "summary_source_ids"):
        values = decision.get(field)
        if (
            not isinstance(values, list)
            or not all(
                isinstance(value, str) and _MESSAGE_ID.fullmatch(value) is not None
                for value in values
            )
            or len(values) != len(set(values))
        ):
            issues.append(f"context decision {field} must contain unique message IDs")
        else:
            id_lists[field] = values
    kept = set(id_lists.get("kept_message_ids", []))
    dropped = set(id_lists.get("dropped_message_ids", []))
    summary_sources = set(id_lists.get("summary_source_ids", []))
    if kept & dropped:
        issues.append("context decision kept and dropped message IDs overlap")
    if not summary_sources <= dropped:
        issues.append("context decision summary sources must be dropped messages")
    history_compacted = decision.get("history_compacted")
    summary_tokens = decision.get("summary_tokens")
    if (
        isinstance(history_compacted, bool)
        and _nonnegative_int(summary_tokens)
        and history_compacted != bool(dropped or summary_tokens)
    ):
        issues.append("context decision history_compacted is inconsistent")

    scores = decision.get("selected_scores")
    if not isinstance(scores, dict) or not all(
        isinstance(key, str)
        and key
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for key, value in scores.items()
    ):
        issues.append("context decision selected_scores must map strings to finite numbers")
    availability = decision.get("lexical_constraint_availability")
    if not isinstance(availability, dict) or not all(
        isinstance(key, str) and key and isinstance(value, bool)
        for key, value in availability.items()
    ):
        issues.append(
            "context decision lexical_constraint_availability must map strings to booleans"
        )
    return issues


def _agent_issues(agent: Any) -> list[str]:
    if not isinstance(agent, dict):
        return ["agent must be an object for a completed agent lifecycle"]
    issues: list[str] = []
    if set(agent) != _AGENT_FIELDS:
        issues.append("agent has missing or unknown fields")
    if agent.get("status") not in _AGENT_STATUSES:
        issues.append("agent.status is unsupported")
    if agent.get("final") is not None and not isinstance(agent.get("final"), str):
        issues.append("agent.final must be string or null")
    if agent.get("error") is not None and not isinstance(agent.get("error"), str):
        issues.append("agent.error must be string or null")
    if agent.get("status") == "completed":
        if not isinstance(agent.get("final"), str) or not agent["final"]:
            issues.append("a completed agent must have a non-empty final response")
        if agent.get("error") is not None:
            issues.append("a completed agent cannot have an error")
    elif agent.get("status") in _AGENT_STATUSES:
        if agent.get("final") is not None:
            issues.append("a terminated agent cannot have a final response")
        if not isinstance(agent.get("error"), str) or not agent["error"]:
            issues.append("a terminated agent must have a non-empty error")
    for field in (
        "turns",
        "model_calls",
        "tool_calls",
        "protocol_errors",
        "peak_active_context_tokens",
        "peak_canonical_context_tokens",
        "compaction_count",
    ):
        if not _nonnegative_int(agent.get(field)):
            issues.append(f"agent.{field} must be a non-negative integer")
    for field in ("model_latency_seconds", "tool_latency_seconds"):
        if not _finite_nonnegative(agent.get(field)):
            issues.append(f"agent.{field} must be finite and non-negative")
    compactions = agent.get("compactions")
    context_checks = agent.get("context_checks")
    if not isinstance(compactions, list) or not all(isinstance(item, dict) for item in compactions):
        issues.append("agent.compactions must be a list of objects")
    elif agent.get("compaction_count") != len(compactions):
        issues.append("agent.compaction_count must equal len(agent.compactions)")
    else:
        for decision in compactions:
            issues.extend(_context_decision_issues(decision))
    if not isinstance(context_checks, list) or not all(
        isinstance(item, dict) for item in context_checks
    ):
        issues.append("agent.context_checks must be a list of objects")
    else:
        availability: list[bool] = []
        malformed_availability = False
        for item in context_checks:
            issues.extend(_context_decision_issues(item))
            if item.get("history_compacted") is not True:
                continue
            values = item.get("lexical_constraint_availability", {})
            if not isinstance(values, dict) or not all(
                isinstance(key, str) and isinstance(value, bool) for key, value in values.items()
            ):
                malformed_availability = True
                break
            availability.extend(values.values())
        if malformed_availability:
            issues.append("agent context lexical availability must map strings to booleans")
        else:
            expected_rate = sum(availability) / len(availability) if availability else None
            if agent.get("lexical_constraint_availability_rate") != expected_rate:
                issues.append(
                    "agent lexical_constraint_availability_rate does not match context checks"
                )
        if all(_nonnegative_int(item.get("tokens_after")) for item in context_checks):
            expected_active_peak = max((item["tokens_after"] for item in context_checks), default=0)
            if agent.get("peak_active_context_tokens") != expected_active_peak:
                issues.append("agent peak_active_context_tokens does not match context checks")
        if all(_nonnegative_int(item.get("canonical_tokens")) for item in context_checks):
            expected_canonical_peak = max(
                (item["canonical_tokens"] for item in context_checks), default=0
            )
            if agent.get("peak_canonical_context_tokens") != expected_canonical_peak:
                issues.append("agent peak_canonical_context_tokens does not match context checks")
    for field in ("provider_models", "provider_fingerprints"):
        values = agent.get(field)
        if (
            not isinstance(values, list)
            or not all(isinstance(value, str) and value for value in values)
            or values != sorted(set(values))
        ):
            issues.append(f"agent.{field} must be sorted unique non-empty strings")
    trajectory = agent.get("model_trajectory_sha256")
    if not isinstance(trajectory, str) or _SHA256.fullmatch(trajectory) is None:
        issues.append("agent.model_trajectory_sha256 must be a SHA-256 digest")

    usage = agent.get("usage")
    if not isinstance(usage, dict):
        issues.append("agent.usage must be an object")
        return issues
    if set(usage) != _USAGE_FIELDS:
        issues.append("agent.usage has missing or unknown fields")
    for field in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    ):
        if not _nonnegative_int(usage.get(field)):
            issues.append(f"agent.usage.{field} must be a non-negative integer")
    if (
        all(_nonnegative_int(usage.get(field)) for field in ("input_tokens", "output_tokens"))
        and usage.get("total_tokens") != usage["input_tokens"] + usage["output_tokens"]
    ):
        issues.append("agent.usage.total_tokens must equal input_tokens + output_tokens")
    if (
        all(
            _nonnegative_int(usage.get(field))
            for field in ("input_tokens", "cache_read_tokens", "cache_write_tokens")
        )
        and usage["cache_read_tokens"] + usage["cache_write_tokens"] > usage["input_tokens"]
    ):
        issues.append("agent cache tokens cannot exceed input tokens")
    cost = usage.get("cost_usd")
    if cost is not None and not _finite_nonnegative(cost):
        issues.append("agent.usage.cost_usd must be finite, non-negative, or null")
    complete = usage.get("complete")
    if not isinstance(complete, bool):
        issues.append("agent.usage.complete must be boolean")
    elif not complete and cost is not None:
        issues.append("an incomplete usage ledger must have null cost_usd")
    sources = usage.get("usage_sources")
    if (
        not isinstance(sources, list)
        or not all(isinstance(source, str) and source for source in sources)
        or sources != sorted(set(sources))
    ):
        issues.append("agent.usage.usage_sources must be sorted unique non-empty strings")
    return issues


def _evaluation_issues(evaluation: Any) -> list[str]:
    if not isinstance(evaluation, dict):
        return ["evaluation must be an object when present"]
    issues: list[str] = []
    if set(evaluation) != _EVALUATION_FIELDS:
        issues.append("evaluation has missing or unknown fields")
    passed = evaluation.get("passed")
    returncode = evaluation.get("returncode")
    if passed is not None and not isinstance(passed, bool):
        issues.append("evaluation.passed must be boolean or null")
    if returncode is not None and (not isinstance(returncode, int) or isinstance(returncode, bool)):
        issues.append("evaluation.returncode must be integer or null")
    if not isinstance(evaluation.get("output"), str):
        issues.append("evaluation.output must be a string")
    if not _finite_nonnegative(evaluation.get("duration_seconds")):
        issues.append("evaluation.duration_seconds must be finite and non-negative")
    checks = evaluation.get("constraint_checks")
    details = evaluation.get("constraint_details")
    if not isinstance(checks, dict) or not all(
        isinstance(key, str) and key and isinstance(value, bool) for key, value in checks.items()
    ):
        issues.append("evaluation.constraint_checks must map non-empty strings to booleans")
        checks = None
    if not isinstance(details, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) for key, value in details.items()
    ):
        issues.append("evaluation.constraint_details must map non-empty strings to strings")
        details = None
    if isinstance(checks, dict) and isinstance(details, dict) and set(checks) != set(details):
        issues.append("evaluation constraint detail keys must match constraint checks")
    adherence = evaluation.get("behavioral_adherence")
    expected_adherence = sum(checks.values()) / len(checks) if checks else None
    if adherence != expected_adherence:
        issues.append("evaluation.behavioral_adherence must be derived from constraint checks")
    evaluator_integrity = evaluation.get("evaluator_integrity")
    if not isinstance(evaluator_integrity, bool):
        issues.append("evaluation.evaluator_integrity must be boolean")
    elif passed is True and not evaluator_integrity:
        issues.append("evaluation cannot pass when evaluator integrity failed")
    if passed is None and returncode is not None:
        issues.append("a pending evaluation must have a null return code")
    elif passed is True and returncode != 0:
        issues.append("a passing evaluation must have return code zero")
    elif (
        isinstance(returncode, int)
        and not isinstance(returncode, bool)
        and returncode != 0
        and passed is not False
    ):
        issues.append("a nonzero evaluator return code requires a failed evaluation")
    elif returncode == 0 and passed is False and evaluator_integrity is not False:
        issues.append("return code zero can fail only when evaluator integrity failed")
    integrity_details = evaluation.get("evaluator_integrity_details")
    if not isinstance(integrity_details, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str)
        for key, value in integrity_details.items()
    ):
        issues.append(
            "evaluation.evaluator_integrity_details must map non-empty strings to strings"
        )
    return issues


def result_semantic_issues(result: dict[str, Any]) -> list[str]:
    """Return contradictions in a raw persisted trial result."""

    issues: list[str] = []
    status = result.get("status")
    infrastructure_valid = result.get("infrastructure_valid")
    evaluation_valid = result.get("evaluation_valid")
    solved = result.get("solved")
    governed_solved = result.get("governed_solved")
    wall_seconds = result.get("wall_seconds")
    started_at = parse_utc_timestamp(result.get("started_at"))
    finished_at = parse_utc_timestamp(result.get("finished_at"))

    if status not in PERSISTED_RESULT_STATUSES:
        issues.append("status is not a supported persisted terminal status")
    if not isinstance(infrastructure_valid, bool):
        issues.append("infrastructure_valid must be boolean")
    if not isinstance(evaluation_valid, bool):
        issues.append("evaluation_valid must be boolean")
    if solved is not None and not isinstance(solved, bool):
        issues.append("solved must be boolean or null")
    if governed_solved is not None and not isinstance(governed_solved, bool):
        issues.append("governed_solved must be boolean or null")
    if (
        isinstance(wall_seconds, bool)
        or not isinstance(wall_seconds, (int, float))
        or not math.isfinite(wall_seconds)
        or wall_seconds < 0
    ):
        issues.append("wall_seconds must be a finite non-negative number")
    if started_at is None:
        issues.append("started_at must be an RFC-3339 UTC timestamp")
    if finished_at is None:
        issues.append("finished_at must be an RFC-3339 UTC timestamp")
    if started_at is not None and finished_at is not None and finished_at < started_at:
        issues.append("finished_at cannot precede started_at")

    is_normal_lifecycle = status in PERSISTED_RESULT_STATUSES - _EXCEPTION_RESULT_STATUSES
    error = result.get("error")
    artifacts = result.get("artifacts")
    artifact_hashes = result.get("artifact_hashes")
    if is_normal_lifecycle:
        if set(result) != _NORMAL_RESULT_FIELDS:
            issues.append("normal trial result has missing or unknown fields")
        issues.extend(_agent_issues(result.get("agent")))
        if not isinstance(result.get("evaluation"), dict):
            issues.append("normal trial results require an evaluation object")
        patch_sha256 = result.get("patch_sha256")
        patch_bytes = result.get("patch_bytes")
        if not isinstance(patch_sha256, str) or _SHA256.fullmatch(patch_sha256) is None:
            issues.append("normal trial results require a patch SHA-256 digest")
        if not _nonnegative_int(patch_bytes):
            issues.append("normal trial results require a non-negative patch byte count")
        if error is not None:
            issues.append("normal trial results cannot carry a top-level error")
        if not isinstance(artifacts, dict) or set(artifacts) != {
            "trace",
            "patch",
            "result",
            "workspace",
        }:
            issues.append("normal trial results require the exact artifact map")
        if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != {
            "trace_sha256",
            "patch_sha256",
        }:
            issues.append("normal trial results require exact trace and patch artifact hashes")
    elif status in _EXCEPTION_RESULT_STATUSES:
        if set(result) != _EXCEPTION_RESULT_FIELDS:
            issues.append("exception trial result has missing or unknown fields")
        if not isinstance(error, dict) or set(error) != {"type", "message"}:
            issues.append("exception trial results require an exact error object")
        elif not all(isinstance(error.get(field), str) for field in ("type", "message")):
            issues.append("exception trial error fields must be strings")
        for field in ("agent", "evaluation", "patch_sha256", "patch_bytes"):
            if field in result:
                issues.append(f"exception trial results cannot carry {field}")
        if not isinstance(artifacts, dict) or set(artifacts) != {"trace", "result"}:
            issues.append("exception trial results require the exact artifact map")
        if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != {"trace_sha256"}:
            issues.append("exception trial results require exactly one trace artifact hash")

    if isinstance(infrastructure_valid, bool) and isinstance(evaluation_valid, bool):
        if not infrastructure_valid:
            if status != "infrastructure_error":
                issues.append("infrastructure-invalid results must use infrastructure_error")
            if evaluation_valid or solved is not None or governed_solved is not None:
                issues.append("infrastructure-invalid results cannot carry an evaluator outcome")
        elif not evaluation_valid:
            if status != "awaiting_external_evaluation":
                issues.append("evaluator-pending results must use awaiting_external_evaluation")
            if solved is not None or governed_solved is not None:
                issues.append("evaluator-pending results must keep solve fields null")
        else:
            if not isinstance(solved, bool) or not isinstance(governed_solved, bool):
                issues.append("evaluator-valid results require boolean solve fields")
            if status in {"infrastructure_error", "awaiting_external_evaluation"}:
                issues.append("evaluator-valid results use an incompatible terminal status")
            if isinstance(solved, bool) and ((status == "resolved") != solved):
                issues.append("resolved status must agree exactly with solved")
            if governed_solved is True and solved is not True:
                issues.append("governed_solved cannot be true when solved is not true")

    evaluation = result.get("evaluation")
    if evaluation is not None:
        issues.extend(_evaluation_issues(evaluation))
        if isinstance(evaluation, dict):
            passed = evaluation.get("passed")
            adherence = evaluation.get("behavioral_adherence")
            if isinstance(evaluation_valid, bool) and evaluation_valid != (passed is not None):
                issues.append("evaluation_valid must agree with evaluation.passed availability")
            if isinstance(passed, bool) and solved is not passed:
                issues.append("solved must agree with evaluation.passed")
            expected_governed = (
                bool(passed and (adherence is None or adherence == 1.0))
                if isinstance(passed, bool)
                else None
            )
            if governed_solved != expected_governed:
                issues.append("governed_solved must agree with solved and behavioral adherence")
    return issues


def _context_trace_issues(
    events: list[dict[str, Any]], constraints: list[dict[str, Any]] | None
) -> list[str]:
    """Reconstruct canonical message identity and verify every model-facing view."""

    issues: list[str] = []
    if constraints is None:
        return ["task constraint provenance is required for context evidence"]
    if not isinstance(constraints, list) or not all(
        isinstance(constraint, dict)
        and set(constraint) == {"id", "text", "text_sha256", "redaction_applied"}
        and isinstance(constraint.get("id"), str)
        and bool(constraint.get("id"))
        and isinstance(constraint.get("text"), str)
        and bool(constraint.get("text", "").strip())
        and isinstance(constraint.get("text_sha256"), str)
        and _SHA256.fullmatch(constraint.get("text_sha256", "")) is not None
        and isinstance(constraint.get("redaction_applied"), bool)
        and (
            constraint.get("redaction_applied") is True
            or content_hash(constraint.get("text")) == constraint.get("text_sha256")
        )
        for constraint in constraints
    ):
        return ["task constraint provenance must contain exact non-empty ID/text objects"]
    constraint_ids = [constraint["id"] for constraint in constraints]
    if len(constraint_ids) != len(set(constraint_ids)):
        return ["task constraint provenance contains duplicate IDs"]
    lexical_constraints = [
        {"id": str(constraint["id"]), "text": str(constraint["text"])}
        for constraint in constraints
        if constraint["redaction_applied"] is False
    ]

    # Every coding-agent trajectory starts with these two bundles. Their content
    # is learned from the first view that retains each message; all later content
    # is reconstructed from response and tool events.
    canonical: list[dict[str, Any]] = [
        {
            "id": "m00001",
            "role": "system",
            "content": None,
            "bundle_id": "system",
            "exact": False,
        },
        {
            "id": "m00002",
            "role": "user",
            "content": None,
            "bundle_id": "task",
            "exact": False,
        },
    ]
    counter = Char4TokenCounter()

    def append_message(*, role: str, content: str, bundle_id: str) -> None:
        canonical.append(
            {
                "id": f"m{len(canonical) + 1:05d}",
                "role": role,
                "content": content,
                "bundle_id": bundle_id,
                "exact": False,
            }
        )

    for event_index, event in enumerate(events):
        event_type = event.get("type")
        payload = event.get("payload", {})
        next_event = events[event_index + 1] if event_index + 1 < len(events) else None

        if event_type == "context_prepared":
            turn = payload.get("turn")
            if (
                next_event is None
                or next_event.get("type") != "model_request"
                or next_event.get("payload", {}).get("turn") != turn
            ):
                issues.append("context_prepared is not followed by its matching model_request")

            kept = payload.get("kept_message_ids")
            dropped = payload.get("dropped_message_ids")
            summary_sources = payload.get("summary_source_ids")
            if not all(
                isinstance(value, list) and all(isinstance(message_id, str) for message_id in value)
                for value in (kept, dropped, summary_sources)
            ):
                continue
            canonical_ids = [message["id"] for message in canonical]
            kept_set = set(kept)
            dropped_set = set(dropped)
            source_set = set(summary_sources)
            expected_kept = [message_id for message_id in canonical_ids if message_id in kept_set]
            expected_dropped = [
                message_id for message_id in canonical_ids if message_id not in kept_set
            ]
            expected_sources = [
                message_id for message_id in canonical_ids if message_id in source_set
            ]
            if kept != expected_kept:
                issues.append(
                    "context decision kept_message_ids do not match the reconstructed trajectory"
                )
            if dropped != expected_dropped or dropped_set != set(expected_dropped):
                issues.append(
                    "context decision dropped_message_ids do not match the reconstructed trajectory"
                )
            if summary_sources != expected_sources:
                issues.append(
                    "context decision summary_source_ids do not match the reconstructed trajectory"
                )
            if "m00001" not in kept_set:
                issues.append("context decision dropped the pinned system message")

            bundles: dict[str, list[str]] = {}
            for message in canonical:
                bundles.setdefault(str(message["bundle_id"]), []).append(str(message["id"]))
            for bundle_ids in bundles.values():
                kept_count = sum(message_id in kept_set for message_id in bundle_ids)
                if 0 < kept_count < len(bundle_ids):
                    issues.append("context decision splits an atomic assistant/tool message bundle")
                    break
            for bundle_ids in bundles.values():
                source_count = sum(message_id in source_set for message_id in bundle_ids)
                if 0 < source_count < len(bundle_ids):
                    issues.append("context decision splits an atomic summary-source message bundle")
                    break

        elif event_type == "model_request":
            previous_event = events[event_index - 1] if event_index > 0 else None
            if previous_event is None or previous_event.get("type") != "context_prepared":
                continue
            decision = previous_event.get("payload", {})
            messages = payload.get("messages")
            kept = decision.get("kept_message_ids")
            if not isinstance(messages, list) or not isinstance(kept, list):
                continue
            if not all(
                isinstance(message, dict)
                and isinstance(message.get("role"), str)
                and isinstance(message.get("content"), str)
                for message in messages
            ):
                continue
            request_messages = cast(list[dict[str, str]], messages)
            redaction_applied = payload.get("redaction_applied")
            messages_sha256 = payload.get("messages_sha256")
            observed_tokens = payload.get("observed_tokens")
            observed_availability = payload.get("lexical_constraint_availability")
            if not isinstance(observed_availability, dict) or set(observed_availability) != set(
                constraint_ids
            ):
                issues.append("model_request lexical constraint IDs do not match task provenance")
            if observed_tokens != decision.get("tokens_after"):
                issues.append("model_request observed_tokens do not match its context decision")
            if observed_availability != decision.get("lexical_constraint_availability"):
                issues.append(
                    "model_request lexical constraint availability does not match its "
                    "context decision"
                )
            persisted_messages_hash = content_hash(request_messages)
            if redaction_applied is False:
                if messages_sha256 != persisted_messages_hash:
                    issues.append("model_request messages_sha256 does not match its messages")
                if counter.messages(request_messages) != observed_tokens:
                    issues.append("model_request observed_tokens do not match its messages")
            if isinstance(observed_availability, dict) and lexical_constraints:
                expected_availability = _lexical_constraint_availability(
                    request_messages, lexical_constraints
                )
                if any(
                    observed_availability.get(constraint_id) != retained
                    for constraint_id, retained in expected_availability.items()
                ):
                    issues.append(
                        "context lexical constraint availability does not match "
                        "model_request messages"
                    )
            if redaction_applied is True and messages_sha256 == persisted_messages_hash:
                issues.append(
                    "redacted model_request commitment unexpectedly matches persisted messages"
                )

            by_id = {message["id"]: message for message in canonical}
            if not all(message_id in by_id for message_id in kept):
                continue
            visible = [by_id[message_id] for message_id in kept]
            summary_tokens = decision.get("summary_tokens")
            has_summary = _nonnegative_int(summary_tokens) and summary_tokens > 0
            summary_index = 0
            while summary_index < len(visible) and visible[summary_index]["role"] == "system":
                summary_index += 1
            expected_length = len(visible) + int(has_summary)
            if len(request_messages) != expected_length:
                issues.append(
                    "model_request messages do not match the reconstructed context selection"
                )
                continue

            request_index = 0
            projection_matches = True
            for visible_index, canonical_message in enumerate(visible):
                if has_summary and visible_index == summary_index:
                    summary_message = request_messages[request_index]
                    summary_sources = decision.get("summary_source_ids", [])
                    expected_header = (
                        "[Compacted context; source_count="
                        f"{len(summary_sources) if isinstance(summary_sources, list) else 0}]"
                    )
                    if (
                        summary_message["role"] != "user"
                        or not summary_message["content"].startswith(expected_header)
                        or (
                            redaction_applied is False
                            and counter.message("user", summary_message["content"])
                            != summary_tokens
                        )
                        or not summary_sources
                    ):
                        issues.append("model_request summary does not match its context decision")
                    request_index += 1
                request_message = request_messages[request_index]
                expected_role = (
                    "user" if canonical_message["role"] == "tool" else canonical_message["role"]
                )
                if request_message["role"] != expected_role:
                    projection_matches = False
                canonical_content = canonical_message["content"]
                if canonical_content is None:
                    canonical_message["content"] = request_message["content"]
                    canonical_message["exact"] = redaction_applied is False
                elif canonical_message["role"] == "tool":
                    if not _tool_model_content_matches(
                        str(canonical_content), request_message["content"]
                    ):
                        projection_matches = False
                    elif redaction_applied is False:
                        canonical_message["content"] = request_message["content"]
                        canonical_message["exact"] = True
                elif request_message["content"] != canonical_content:
                    projection_matches = False
                elif redaction_applied is False:
                    canonical_message["exact"] = True
                request_index += 1
            if has_summary and summary_index == len(visible):
                summary_message = request_messages[request_index]
                summary_sources = decision.get("summary_source_ids", [])
                expected_header = (
                    "[Compacted context; source_count="
                    f"{len(summary_sources) if isinstance(summary_sources, list) else 0}]"
                )
                if (
                    summary_message["role"] != "user"
                    or not summary_message["content"].startswith(expected_header)
                    or (
                        redaction_applied is False
                        and counter.message("user", summary_message["content"]) != summary_tokens
                    )
                    or not summary_sources
                ):
                    issues.append("model_request summary does not match its context decision")
            if not projection_matches:
                issues.append(
                    "model_request messages do not match the reconstructed context selection"
                )

            if redaction_applied is False and all(
                isinstance(message["content"], str) and message["exact"] is True
                for message in canonical
            ):
                canonical_messages = [
                    {
                        "role": ("user" if message["role"] == "tool" else str(message["role"])),
                        "content": str(message["content"]),
                    }
                    for message in canonical
                ]
                if counter.messages(canonical_messages) != decision.get("canonical_tokens"):
                    issues.append(
                        "context canonical_tokens do not match the reconstructed trajectory"
                    )

        elif event_type == "model_response":
            # A response that immediately loses cost observability is deliberately
            # not appended by CodingAgent; execution stops before response parsing.
            if next_event is not None and next_event.get("type") == "cost_observability_lost":
                continue
            content = payload.get("content")
            turn = payload.get("turn")
            if not isinstance(content, str) or not isinstance(turn, int) or isinstance(turn, bool):
                continue
            bundle_id = f"turn-{turn:04d}"
            append_message(role="assistant", content=content, bundle_id=bundle_id)
            if next_event is None:
                continue
            next_type = next_event.get("type")
            next_payload = next_event.get("payload", {})
            if next_type == "tool_result" and isinstance(next_payload, dict):
                append_message(
                    role="tool",
                    content=_tool_result_model_content(next_payload),
                    bundle_id=bundle_id,
                )
            elif next_type == "protocol_error":
                try:
                    parse_response(content)
                except ProtocolError as exc:
                    feedback = (
                        "<protocol_error>"
                        + str(exc)
                        + ". Return exactly one valid action or final JSON object.</protocol_error>"
                    )
                    append_message(role="tool", content=feedback, bundle_id=bundle_id)

    return issues


def trace_lifecycle_issues(
    events: list[dict[str, Any]],
    *,
    expected_trial: dict[str, Any],
    result: dict[str, Any],
    require_artifact_events: bool,
    constraints: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Validate the exact persisted event envelope and terminal lifecycle."""

    issues: list[str] = []
    event_fields = {"schema_version", "sequence", "timestamp", "type", "payload"}
    for sequence, event in enumerate(events, start=1):
        if (
            set(event) != event_fields
            or event.get("schema_version") != 1
            or not isinstance(event.get("sequence"), int)
            or isinstance(event.get("sequence"), bool)
            or event.get("sequence") != sequence
            or parse_utc_timestamp(event.get("timestamp")) is None
            or not isinstance(event.get("type"), str)
            or not event.get("type")
            or event.get("type") not in _EVENT_TYPES
            or not isinstance(event.get("payload"), dict)
        ):
            issues.append("trace contains an invalid event envelope or sequence")
            break
    if issues:
        return issues

    strict_retry_events = result.get("model_provider") == "openai_compatible"
    for event in events:
        event_type = event["type"]
        payload = event["payload"]
        if event_type == "model_request":
            messages = payload.get("messages")
            if (
                set(payload)
                != {
                    "turn",
                    "messages",
                    "messages_sha256",
                    "redaction_applied",
                    "observed_tokens",
                    "lexical_constraint_availability",
                }
                or not _nonnegative_int(payload.get("turn"))
                or payload.get("turn", 0) < 1
            ):
                issues.append("model_request payload has missing or unknown fields")
            if not isinstance(messages, list) or not all(
                isinstance(message, dict)
                and set(message) == {"role", "content"}
                and isinstance(message.get("role"), str)
                and isinstance(message.get("content"), str)
                for message in messages
            ):
                issues.append("model_request messages must contain exact role/content objects")
            if (
                not isinstance(payload.get("messages_sha256"), str)
                or _SHA256.fullmatch(payload["messages_sha256"]) is None
            ):
                issues.append("model_request messages_sha256 must be a SHA-256 digest")
            if not isinstance(payload.get("redaction_applied"), bool):
                issues.append("model_request redaction_applied must be boolean")
            if not _nonnegative_int(payload.get("observed_tokens")):
                issues.append("model_request observed_tokens must be non-negative")
            availability = payload.get("lexical_constraint_availability")
            if not isinstance(availability, dict) or not all(
                isinstance(key, str) and key and isinstance(value, bool)
                for key, value in availability.items()
            ):
                issues.append(
                    "model_request lexical_constraint_availability must map strings to booleans"
                )
        elif event_type == "model_response":
            expected_fields = {
                "turn",
                "content",
                "usage",
                "cost_usd",
                "request_id",
                "provider_model",
                "finish_reason",
                "latency_seconds",
                "metadata",
            }
            if set(payload) != expected_fields:
                issues.append("model_response payload has missing or unknown fields")
            if not _nonnegative_int(payload.get("turn")) or payload.get("turn", 0) < 1:
                issues.append("model_response turn must be a positive integer")
            if payload.get("request_id") is not None and not isinstance(
                payload.get("request_id"), str
            ):
                issues.append("model_response request_id must be string or null")
            if payload.get("finish_reason") is not None and not isinstance(
                payload.get("finish_reason"), str
            ):
                issues.append("model_response finish_reason must be string or null")
            if not _finite_nonnegative(payload.get("latency_seconds")):
                issues.append("model_response latency_seconds must be finite and non-negative")
        elif event_type == "tool_result":
            expected_fields = {
                "turn",
                "arguments",
                "tool",
                "ok",
                "content",
                "metadata",
                "duration_seconds",
            }
            if set(payload) != expected_fields:
                issues.append("tool_result payload has missing or unknown fields")
            if not _nonnegative_int(payload.get("turn")) or payload.get("turn", 0) < 1:
                issues.append("tool_result turn must be a positive integer")
            if not isinstance(payload.get("arguments"), dict):
                issues.append("tool_result arguments must be an object")
            if not isinstance(payload.get("tool"), str) or not payload.get("tool"):
                issues.append("tool_result tool must be a non-empty string")
            if not isinstance(payload.get("ok"), bool):
                issues.append("tool_result ok must be boolean")
            if not isinstance(payload.get("content"), str):
                issues.append("tool_result content must be a string")
            if not isinstance(payload.get("metadata"), dict):
                issues.append("tool_result metadata must be an object")
            if not _finite_nonnegative(payload.get("duration_seconds")):
                issues.append("tool_result duration_seconds must be finite and non-negative")
        elif event_type == "context_prepared":
            if set(payload) != {"turn", *_CONTEXT_DECISION_FIELDS}:
                issues.append("context_prepared payload has missing or unknown fields")
            if not _nonnegative_int(payload.get("turn")) or payload.get("turn", 0) < 1:
                issues.append("context_prepared turn must be positive")
            issues.extend(
                _context_decision_issues(
                    {key: value for key, value in payload.items() if key != "turn"}
                )
            )
        elif event_type == "model_attempt_failed":
            expected_fields = {
                "attempt",
                "maximum_attempts",
                "retrying",
                "latency_seconds",
                "error_type",
                "error",
            }
            if strict_retry_events and set(payload) != expected_fields:
                issues.append("model_attempt_failed payload has missing or unknown fields")
            if strict_retry_events and (
                not _nonnegative_int(payload.get("attempt")) or payload.get("attempt", 0) < 1
            ):
                issues.append("model_attempt_failed attempt must be positive")
            if strict_retry_events and (
                not _nonnegative_int(payload.get("maximum_attempts"))
                or payload.get("maximum_attempts", 0) < 1
            ):
                issues.append("model_attempt_failed maximum_attempts must be positive")
            if strict_retry_events and not isinstance(payload.get("retrying"), bool):
                issues.append("model_attempt_failed retrying must be boolean")
            if strict_retry_events and not _finite_nonnegative(payload.get("latency_seconds")):
                issues.append("model_attempt_failed latency_seconds must be finite")
            if strict_retry_events:
                for field in ("error_type", "error"):
                    if not isinstance(payload.get(field), str) or not payload.get(field):
                        issues.append(f"model_attempt_failed {field} must be non-empty")
        elif event_type == "protocol_error":
            if (
                set(payload) != {"turn", "error"}
                or not _nonnegative_int(payload.get("turn"))
                or payload.get("turn", 0) < 1
                or not isinstance(payload.get("error"), str)
            ):
                issues.append("protocol_error payload is invalid")
        elif event_type == "model_error":
            if (
                set(payload) != {"turn", "error"}
                or not _nonnegative_int(payload.get("turn"))
                or payload.get("turn", 0) < 1
                or not isinstance(payload.get("error"), str)
            ):
                issues.append("model_error payload is invalid")
        elif event_type == "agent_final":
            if (
                set(payload) != {"turn", "final"}
                or not _nonnegative_int(payload.get("turn"))
                or payload.get("turn", 0) < 1
                or not isinstance(payload.get("final"), str)
                or not payload.get("final")
            ):
                issues.append("agent_final payload is invalid")
        elif event_type == "cost_observability_lost":
            if (
                set(payload) != {"turn"}
                or not _nonnegative_int(payload.get("turn"))
                or payload.get("turn", 0) < 1
            ):
                issues.append("cost_observability_lost payload is invalid")
        elif event_type == "context_overflow":
            if (
                set(payload) != {"turn", "error"}
                or not _nonnegative_int(payload.get("turn"))
                or payload.get("turn", 0) < 1
                or not isinstance(payload.get("error"), str)
                or not payload.get("error")
            ):
                issues.append("context_overflow payload is invalid")
        elif event_type == "token_preflight_rejected":
            if (
                set(payload) != {"turn", "projected_tokens"}
                or not _nonnegative_int(payload.get("turn"))
                or payload.get("turn", 0) < 1
                or not _nonnegative_int(payload.get("projected_tokens"))
            ):
                issues.append("token_preflight_rejected payload is invalid")
        elif event_type == "cost_preflight_rejected":
            if (
                set(payload) != {"turn", "projected_cost_usd"}
                or not _nonnegative_int(payload.get("turn"))
                or payload.get("turn", 0) < 1
                or not _finite_nonnegative(payload.get("projected_cost_usd"))
            ):
                issues.append("cost_preflight_rejected payload is invalid")
    if issues:
        return issues

    issues.extend(_context_trace_issues(events, constraints))

    started = [event for event in events if event.get("type") == "trial_started"]
    if len(started) != 1 or not events or events[0].get("type") != "trial_started":
        issues.append("trace must begin with exactly one trial_started event")
    elif not _same_json(started[0].get("payload"), expected_trial):
        issues.append("trial_started payload does not match the plan")

    finished = [event for event in events if event.get("type") == "trial_finished"]
    if len(finished) != 1 or not events or events[-1].get("type") != "trial_finished":
        issues.append("trace must end with exactly one trial_finished event")
    else:
        expected_terminal = {
            "status": result.get("status"),
            "solved": result.get("solved"),
            "wall_seconds": result.get("wall_seconds"),
        }
        if set(finished[0].get("payload", {})) != set(expected_terminal) or not _same_json(
            finished[0].get("payload"), expected_terminal
        ):
            issues.append("trial_finished payload does not match the result")

    status = result.get("status")
    normal_lifecycle = status in PERSISTED_RESULT_STATUSES - _EXCEPTION_RESULT_STATUSES
    if normal_lifecycle != require_artifact_events:
        issues.append("result artifact shape does not match its terminal status")

    agent = result.get("agent")
    if normal_lifecycle:
        issues.extend(_agent_issues(agent))
        agent_started = [event for event in events if event.get("type") == "agent_started"]
        if len(agent_started) != 1 or len(events) < 2 or events[1].get("type") != "agent_started":
            issues.append("trace must contain exactly one agent_started event")
        else:
            payload = agent_started[0].get("payload", {})
            expected_fields = {
                "task_id",
                "seed",
                "policy",
                "input_limit",
                "token_counter",
                "available_tools",
                "instructions_sha256",
            }
            runtime_identity = result.get("runtime_identity")
            expected_counter = (
                runtime_identity.get("token_counter")
                if isinstance(runtime_identity, dict)
                else None
            )
            if set(payload) != expected_fields:
                issues.append("agent_started payload has missing or unknown fields")
            if (
                payload.get("task_id") != result.get("task_id")
                or payload.get("seed") != result.get("replicate")
                or payload.get("policy") != result.get("variant_id")
                or payload.get("available_tools") != result.get("variant_tools")
                or payload.get("instructions_sha256") != result.get("variant_instructions_sha256")
                or (
                    expected_counter is not None
                    and payload.get("token_counter") != expected_counter
                )
                or not _nonnegative_int(payload.get("input_limit"))
                or payload.get("input_limit", 0) < 1
            ):
                issues.append("agent_started payload does not match the result treatment")
        evaluation_events = [
            event for event in events if event.get("type") == "evaluation_finished"
        ]
        patch_events = [event for event in events if event.get("type") == "patch_captured"]
        if len(evaluation_events) != 1:
            issues.append("trace must contain exactly one evaluation_finished event")
        elif not _same_json(evaluation_events[0].get("payload"), result.get("evaluation")):
            issues.append("evaluation_finished payload does not match the result")
        expected_patch = {
            "patch_sha256": result.get("patch_sha256"),
            "patch_bytes": result.get("patch_bytes"),
        }
        if len(patch_events) != 1:
            issues.append("trace must contain exactly one patch_captured event")
        elif not _same_json(patch_events[0].get("payload"), expected_patch):
            issues.append("patch_captured payload does not match the result")
        terminal_phase = [
            next(
                (index for index, event in enumerate(events) if event.get("type") == event_type),
                None,
            )
            for event_type in (
                "agent_finished",
                "evaluation_finished",
                "patch_captured",
                "trial_finished",
            )
        ]
        if None not in terminal_phase:
            terminal_indices = [index for index in terminal_phase if index is not None]
            if terminal_indices != sorted(terminal_indices):
                issues.append("normal terminal lifecycle events are out of order")
    elif status in _EXCEPTION_RESULT_STATUSES:
        error_type = "trial_error" if status == "infrastructure_error" else "harness_error"
        error_events = [event for event in events if event.get("type") == error_type]
        error = result.get("error")
        if len(error_events) != 1:
            issues.append(f"trace must contain exactly one {error_type} event")
        elif not isinstance(error, dict):
            issues.append("exception trace cannot be bound without a result error")
        else:
            payload = error_events[0].get("payload", {})
            if (
                set(payload) != {"error_type", "message", "traceback"}
                or payload.get("error_type") != error.get("type")
                or payload.get("message") != error.get("message")
                or not isinstance(payload.get("traceback"), str)
            ):
                issues.append(f"{error_type} payload does not match the result error")
            elif events.index(error_events[0]) >= len(events) - 1:
                issues.append(f"{error_type} must precede trial_finished")

    if isinstance(agent, dict):
        agent_events = [event for event in events if event.get("type") == "agent_finished"]
        if len(agent_events) != 1:
            issues.append("trace must contain exactly one agent_finished event")
        elif not _same_json(agent_events[0].get("payload"), result.get("agent")):
            issues.append("agent_finished payload does not match the result")

        responses = [event for event in events if event.get("type") == "model_response"]
        tool_results = [event for event in events if event.get("type") == "tool_result"]
        protocol_errors = [event for event in events if event.get("type") == "protocol_error"]
        agent_finals = [event for event in events if event.get("type") == "agent_final"]
        prepared = [event for event in events if event.get("type") == "context_prepared"]
        if agent.get("model_calls") != len(responses):
            issues.append("agent.model_calls does not match model_response events")
        if agent.get("tool_calls") != len(tool_results):
            issues.append("agent.tool_calls does not match tool_result events")
        if agent.get("protocol_errors") != len(protocol_errors):
            issues.append("agent.protocol_errors does not match protocol_error events")
        if agent.get("status") == "completed":
            if len(agent_finals) != 1 or agent_finals[0]["payload"].get("final") != agent.get(
                "final"
            ):
                issues.append("completed agent does not match exactly one agent_final event")
        elif agent_finals:
            issues.append("terminated agent cannot carry an agent_final event")
        prepared_decisions = [
            {key: value for key, value in event["payload"].items() if key != "turn"}
            for event in prepared
        ]
        if not _same_json(agent.get("context_checks"), prepared_decisions):
            issues.append("agent.context_checks do not match context_prepared events")
        started_events = [event for event in events if event.get("type") == "agent_started"]
        started_input_limit = (
            started_events[0]["payload"].get("input_limit") if len(started_events) == 1 else None
        )
        for decision in prepared_decisions:
            if decision.get("policy") != result.get("variant_policy"):
                issues.append("context decision policy does not match the result treatment")
            if (
                started_input_limit is not None
                and decision.get("input_limit") != started_input_limit
            ):
                issues.append("context decision input_limit does not match agent_started")
        expected_compactions = [
            decision for decision in prepared_decisions if decision.get("compaction_event") is True
        ]
        if not _same_json(agent.get("compactions"), expected_compactions):
            issues.append("agent.compactions do not match effective context events")
        tool_durations = [event["payload"].get("duration_seconds") for event in tool_results]
        if all(_finite_nonnegative(value) for value in tool_durations) and agent.get(
            "tool_latency_seconds"
        ) != sum(tool_durations):
            issues.append("agent.tool_latency_seconds does not match tool events")

        response_contents: list[str] = []
        response_models: set[str] = set()
        response_fingerprints: set[str] = set()
        usage_totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }
        usage_sources: set[str] = set()
        response_cost: float | None = 0.0
        retry_observed = False
        for response in responses:
            payload = response["payload"]
            content = payload.get("content")
            provider_model = payload.get("provider_model")
            metadata = payload.get("metadata")
            usage = payload.get("usage")
            if not isinstance(content, str):
                issues.append("model_response content must be a string")
            else:
                response_contents.append(content)
            if not isinstance(provider_model, str) or not provider_model:
                issues.append("model_response provider_model must be a non-empty string")
            else:
                response_models.add(provider_model)
            if not isinstance(metadata, dict):
                issues.append("model_response metadata must be an object")
                metadata = {}
            fingerprint = metadata.get("system_fingerprint")
            if fingerprint:
                response_fingerprints.add(str(fingerprint))
            attempt_count = metadata.get("attempt_count", 1)
            if not _nonnegative_int(attempt_count) or attempt_count < 1:
                issues.append("model_response attempt_count must be a positive integer")
            elif attempt_count > 1:
                retry_observed = True
            expected_usage_fields = {
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
                "total_tokens",
                "source",
            }
            if not isinstance(usage, dict) or set(usage) != expected_usage_fields:
                issues.append("model_response usage has missing or unknown fields")
            else:
                for field in usage_totals:
                    value = usage.get(field)
                    if not _nonnegative_int(value):
                        issues.append(f"model_response usage {field} must be non-negative")
                    else:
                        usage_totals[field] += value
                response_input = usage.get("input_tokens")
                response_output = usage.get("output_tokens")
                if (
                    _nonnegative_int(response_input)
                    and _nonnegative_int(response_output)
                    and usage.get("total_tokens") != response_input + response_output
                ):
                    issues.append("model_response total_tokens is inconsistent")
                source = usage.get("source")
                if not isinstance(source, str) or not source:
                    issues.append("model_response usage source must be non-empty")
                else:
                    usage_sources.add(source)
            cost = payload.get("cost_usd")
            if cost is None:
                response_cost = None
            elif not _finite_nonnegative(cost):
                issues.append("model_response cost_usd must be finite, non-negative, or null")
            elif response_cost is not None:
                response_cost += float(cost)

        expected_trajectory = content_hash(response_contents)
        if agent.get("model_trajectory_sha256") != expected_trajectory:
            issues.append("agent model trajectory hash does not match model responses")
        if agent.get("provider_models") != sorted(response_models):
            issues.append("agent provider models do not match model responses")
        if agent.get("provider_fingerprints") != sorted(response_fingerprints):
            issues.append("agent provider fingerprints do not match model responses")

        available_tools = result.get("variant_tools")
        if not isinstance(available_tools, list) or not all(
            isinstance(tool, str) and tool for tool in available_tools
        ):
            issues.append("result variant_tools must be a list of non-empty strings")
            available_tool_set: set[str] = set()
        else:
            available_tool_set = set(available_tools)
        for tool_event in tool_results:
            tool_payload = tool_event["payload"]
            if tool_payload.get("tool") not in available_tool_set and not (
                tool_payload.get("ok") is False
                and isinstance(tool_payload.get("metadata"), dict)
                and tool_payload["metadata"].get("error_type") == "ToolUnavailable"
            ):
                issues.append("tool_result used a tool outside the declared treatment")

        for event_index, event in enumerate(events):
            if event.get("type") == "model_request":
                previous_event = events[event_index - 1] if event_index > 0 else None
                if (
                    previous_event is None
                    or previous_event.get("type") != "context_prepared"
                    or previous_event["payload"].get("turn") != event["payload"].get("turn")
                ):
                    issues.append(
                        "model_request is not preceded by its matching context_prepared event"
                    )
                else:
                    messages = event["payload"].get("messages")
                    if (
                        event["payload"].get("redaction_applied") is False
                        and isinstance(messages, list)
                        and Char4TokenCounter().messages(cast(list[dict[str, str]], messages))
                        != previous_event["payload"].get("tokens_after")
                    ):
                        issues.append(
                            "model_request messages do not match context_prepared tokens_after"
                        )
            if event.get("type") != "model_response":
                continue
            payload = event["payload"]
            turn = payload.get("turn")
            next_event = events[event_index + 1] if event_index + 1 < len(events) else None
            next_type = next_event.get("type") if isinstance(next_event, dict) else None
            if next_type == "cost_observability_lost":
                continue
            content = payload.get("content")
            if not isinstance(content, str):
                continue
            try:
                parsed = parse_response(content)
            except ProtocolError as exc:
                if (
                    next_type != "protocol_error"
                    or next_event is None
                    or next_event["payload"].get("turn") != turn
                    or next_event["payload"].get("error") != str(exc)
                ):
                    issues.append("invalid model response is not followed by its protocol error")
                continue
            if parsed.action is not None:
                if next_type != "tool_result" or next_event is None:
                    issues.append("model action is not followed by its tool result")
                else:
                    tool_payload = next_event["payload"]
                    if (
                        tool_payload.get("turn") != turn
                        or tool_payload.get("tool") != parsed.action.tool
                        or not _same_json(tool_payload.get("arguments"), parsed.action.arguments)
                    ):
                        issues.append("tool_result does not match the preceding model action")
            elif parsed.final is not None:
                expected_final = {"turn": turn, "final": parsed.final}
                if (
                    next_type != "agent_final"
                    or next_event is None
                    or not _same_json(next_event.get("payload"), expected_final)
                ):
                    issues.append("model final is not followed by its agent_final event")

        request_open_turn: int | None = None
        failed_attempts: list[dict[str, Any]] = []
        completed_requests = 0
        model_errors = 0
        for event in events:
            event_type = event.get("type")
            if event_type == "model_request":
                if request_open_turn is not None:
                    issues.append("model requests overlap without a terminal response")
                request_turn = event["payload"].get("turn")
                request_open_turn = request_turn if isinstance(request_turn, int) else None
                failed_attempts = []
            elif event_type == "model_attempt_failed":
                if request_open_turn is None:
                    issues.append("model_attempt_failed appears outside a model request")
                else:
                    failed_attempts.append(event["payload"])
            elif event_type in {"model_response", "model_error"}:
                if request_open_turn is None:
                    issues.append(f"{event_type} appears without a model request")
                    continue
                if event["payload"].get("turn") != request_open_turn:
                    issues.append(f"{event_type} turn does not match its model_request")
                if event_type == "model_response":
                    metadata = event["payload"].get("metadata")
                    attempts = metadata.get("attempt_count", 1) if isinstance(metadata, dict) else 1
                    if (
                        strict_retry_events
                        and isinstance(attempts, int)
                        and not isinstance(attempts, bool)
                        and len(failed_attempts) != attempts - 1
                    ):
                        issues.append("model retry events do not match response attempt_count")
                    if failed_attempts:
                        retry_observed = True
                else:
                    model_errors += 1
                    if strict_retry_events and (
                        not failed_attempts or failed_attempts[-1].get("retrying") is not False
                    ):
                        issues.append("terminal model_error lacks a final failed-attempt event")
                if strict_retry_events:
                    for expected_attempt, failure in enumerate(failed_attempts, start=1):
                        if failure.get("attempt") != expected_attempt:
                            issues.append("model failed-attempt sequence is not contiguous")
                            break
                        if (
                            expected_attempt < len(failed_attempts)
                            and failure.get("retrying") is not True
                        ):
                            issues.append(
                                "non-terminal model failed attempts must declare retrying"
                            )
                            break
                completed_requests += 1
                request_open_turn = None
                failed_attempts = []
        if request_open_turn is not None:
            issues.append("trace ends with an unterminated model request")
        if completed_requests != len(responses) + model_errors:
            issues.append("model request lifecycle count is inconsistent")
        expected_model_errors = 1 if agent.get("status") == "model_error" else 0
        if model_errors != expected_model_errors:
            issues.append("agent status does not match model_error events")
        context_overflows = [event for event in events if event.get("type") == "context_overflow"]
        cost_observability_events = [
            event for event in events if event.get("type") == "cost_observability_lost"
        ]
        token_preflight_events = [
            event for event in events if event.get("type") == "token_preflight_rejected"
        ]
        cost_preflight_events = [
            event for event in events if event.get("type") == "cost_preflight_rejected"
        ]
        if agent.get("status") == "context_overflow":
            expected_overflow = {"turn": agent.get("turns"), "error": agent.get("error")}
            if len(context_overflows) != 1 or not _same_json(
                context_overflows[0].get("payload") if context_overflows else None,
                expected_overflow,
            ):
                issues.append("context_overflow event does not match agent termination")
        elif context_overflows:
            issues.append("context_overflow event is incompatible with agent status")
        if agent.get("status") == "model_error" and model_errors == 1:
            model_error_event = next(
                event for event in events if event.get("type") == "model_error"
            )
            if model_error_event["payload"].get("error") != agent.get("error"):
                issues.append("model_error event does not match agent termination")
        if agent.get("status") == "cost_unobservable":
            if len(cost_observability_events) != 1 or cost_observability_events[0]["payload"].get(
                "turn"
            ) != agent.get("turns"):
                issues.append("cost_observability_lost event does not match agent termination")
        elif cost_observability_events:
            issues.append("cost_observability_lost event is incompatible with agent status")
        if token_preflight_events and (
            agent.get("status") != "token_limit"
            or len(token_preflight_events) != 1
            or token_preflight_events[0]["payload"].get("turn") != agent.get("turns")
        ):
            issues.append("token_preflight_rejected event is incompatible with agent status")
        if cost_preflight_events and (
            agent.get("status") != "cost_limit"
            or len(cost_preflight_events) != 1
            or cost_preflight_events[0]["payload"].get("turn") != agent.get("turns")
        ):
            issues.append("cost_preflight_rejected event is incompatible with agent status")

        ledger = agent.get("usage")
        if isinstance(ledger, dict):
            expected_complete = not retry_observed and model_errors == 0
            expected_sources = set(usage_sources)
            if retry_observed:
                expected_sources.add("provider_retry_unreported")
            if model_errors:
                expected_sources.add("provider_error_unreported")
            expected_cost = response_cost if expected_complete else None
            for field, expected_value in usage_totals.items():
                if ledger.get(field) != expected_value:
                    issues.append(f"agent usage {field} does not match model responses")
            if ledger.get("total_tokens") != (
                usage_totals["input_tokens"] + usage_totals["output_tokens"]
            ):
                issues.append("agent usage total_tokens does not match model responses")
            if ledger.get("usage_sources") != sorted(expected_sources):
                issues.append("agent usage sources do not match model lifecycle")
            if ledger.get("complete") is not expected_complete:
                issues.append("agent usage completeness does not match model lifecycle")
            if ledger.get("cost_usd") != expected_cost:
                issues.append("agent usage cost does not match model lifecycle")

        result_status = result.get("status")
        agent_status = agent.get("status")
        if result_status == "unresolved" and agent_status != "completed":
            issues.append("unresolved result must come from a completed agent")
        if result_status in _AGENT_STATUSES - {"completed"} and agent_status != result_status:
            issues.append("result termination status does not match agent status")
    return issues
