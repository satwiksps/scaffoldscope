"""Append-only trajectories and pluggable context policies.

Policies never mutate the canonical trajectory. They derive an auditable view for
one model request, preserving assistant-action/tool-result bundles atomically.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from scaffoldscope.errors import ContextOverflowError
from scaffoldscope.schema import ConstraintSpec, VariantConfig
from scaffoldscope.tokenization import Char4TokenCounter

if TYPE_CHECKING:
    from scaffoldscope.plugins import PluginRegistry


@dataclass(frozen=True)
class Message:
    id: str
    role: str
    content: str
    kind: str
    turn: int
    bundle_id: str
    pinned: bool = False

    def model_dict(self) -> dict[str, str]:
        # The action protocol is text JSON, not native provider tool calls. Returning
        # observations as user messages therefore works across OpenAI-compatible APIs.
        role = "user" if self.role == "tool" else self.role
        return {"role": role, "content": self.content}


@dataclass(frozen=True)
class MessageBundle:
    id: str
    messages: tuple[Message, ...]

    @property
    def pinned(self) -> bool:
        return any(message.pinned for message in self.messages)

    @property
    def turn(self) -> int:
        return max(message.turn for message in self.messages)

    @property
    def content(self) -> str:
        return "\n".join(message.content for message in self.messages)


class Trajectory:
    """An append-only, in-memory canonical transcript."""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    def append(
        self,
        *,
        role: str,
        content: str,
        kind: str,
        turn: int,
        bundle_id: str,
        pinned: bool = False,
    ) -> Message:
        message = Message(
            id=f"m{len(self._messages) + 1:05d}",
            role=role,
            content=content,
            kind=kind,
            turn=turn,
            bundle_id=bundle_id,
            pinned=pinned,
        )
        self._messages.append(message)
        return message

    def bundles(self) -> tuple[MessageBundle, ...]:
        order: list[str] = []
        grouped: dict[str, list[Message]] = {}
        for message in self._messages:
            if message.bundle_id not in grouped:
                grouped[message.bundle_id] = []
                order.append(message.bundle_id)
            grouped[message.bundle_id].append(message)
        return tuple(MessageBundle(item, tuple(grouped[item])) for item in order)


@dataclass(frozen=True)
class ContextBudget:
    context_window_tokens: int
    reserve_output_tokens: int

    @property
    def input_limit(self) -> int:
        return self.context_window_tokens - self.reserve_output_tokens


@dataclass(frozen=True)
class ContextDecision:
    policy: str
    reason: str
    compaction_event: bool
    history_compacted: bool
    tokens_before: int
    tokens_after: int
    canonical_tokens: int
    input_limit: int
    kept_message_ids: tuple[str, ...]
    dropped_message_ids: tuple[str, ...]
    summary_source_ids: tuple[str, ...] = ()
    summary_tokens: int = 0
    selected_scores: dict[str, float] = field(default_factory=dict)
    lexical_constraint_availability: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "reason": self.reason,
            "compaction_event": self.compaction_event,
            "history_compacted": self.history_compacted,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "canonical_tokens": self.canonical_tokens,
            "input_limit": self.input_limit,
            "compression_ratio": (
                self.tokens_after / self.tokens_before if self.tokens_before else 1.0
            ),
            "kept_message_ids": list(self.kept_message_ids),
            "dropped_message_ids": list(self.dropped_message_ids),
            "summary_source_ids": list(self.summary_source_ids),
            "summary_tokens": self.summary_tokens,
            "selected_scores": self.selected_scores,
            "lexical_constraint_availability": self.lexical_constraint_availability,
        }


@dataclass(frozen=True)
class ContextView:
    messages: tuple[dict[str, str], ...]
    decision: ContextDecision


def _flatten(bundles: Iterable[MessageBundle]) -> list[Message]:
    return [message for bundle in bundles for message in bundle.messages]


def _lexical_availability(
    model_messages: Sequence[dict[str, str]], constraints: Sequence[ConstraintSpec]
) -> dict[str, bool]:
    text = " ".join(message["content"] for message in model_messages)
    normalized = " ".join(text.lower().split())
    result: dict[str, bool] = {}
    for constraint in constraints:
        exact = " ".join(constraint.text.lower().split())
        identifier = constraint.id.lower()
        result[constraint.id] = exact in normalized or f"[{identifier}]" in normalized
    return result


class ContextPolicy:
    def __init__(self, config: VariantConfig, counter: Char4TokenCounter) -> None:
        self.config = config
        self.counter = counter

    def prepare(
        self,
        trajectory: Trajectory,
        budget: ContextBudget,
        constraints: Sequence[ConstraintSpec],
        *,
        turn: int,
    ) -> ContextView:
        raise NotImplementedError

    def _view(
        self,
        *,
        all_messages: Sequence[Message],
        visible_messages: Sequence[Message],
        summary: Message | None,
        budget: ContextBudget,
        constraints: Sequence[ConstraintSpec],
        reason: str,
        scores: dict[str, float] | None = None,
        compaction_event: bool = False,
        summary_source_ids: Sequence[str] = (),
        tokens_before_override: int | None = None,
    ) -> ContextView:
        visible_ids = {message.id for message in visible_messages}
        ordered: list[Message] = []
        inserted_summary = False
        for message in visible_messages:
            if summary is not None and not inserted_summary and message.role != "system":
                ordered.append(summary)
                inserted_summary = True
            ordered.append(message)
        if summary is not None and not inserted_summary:
            ordered.append(summary)
        model_messages = tuple(message.model_dict() for message in ordered)
        canonical = self.counter.messages(message.model_dict() for message in all_messages)
        before = tokens_before_override if tokens_before_override is not None else canonical
        after = self.counter.messages(model_messages)
        if after > budget.input_limit:
            raise ContextOverflowError(
                f"{self.config.id} produced {after} input tokens for a {budget.input_limit}-token budget"
            )
        dropped = tuple(message.id for message in all_messages if message.id not in visible_ids)
        summary_tokens = 0
        if summary is not None:
            summary_tokens = self.counter.message(summary.role, summary.content)
        decision = ContextDecision(
            policy=self.config.policy,
            reason=reason,
            compaction_event=compaction_event,
            history_compacted=bool(dropped or summary),
            tokens_before=before,
            tokens_after=after,
            canonical_tokens=canonical,
            input_limit=budget.input_limit,
            kept_message_ids=tuple(message.id for message in visible_messages),
            dropped_message_ids=dropped,
            summary_source_ids=tuple(summary_source_ids),
            summary_tokens=summary_tokens,
            selected_scores=scores or {},
            lexical_constraint_availability=_lexical_availability(model_messages, constraints),
        )
        return ContextView(messages=model_messages, decision=decision)


class NonePolicy(ContextPolicy):
    def prepare(
        self,
        trajectory: Trajectory,
        budget: ContextBudget,
        constraints: Sequence[ConstraintSpec],
        *,
        turn: int,
    ) -> ContextView:
        del turn
        messages = list(trajectory.messages)
        tokens = self.counter.messages(message.model_dict() for message in messages)
        if tokens > budget.input_limit:
            raise ContextOverflowError(
                f"uncompacted history reached {tokens} input tokens; limit is {budget.input_limit}"
            )
        return self._view(
            all_messages=messages,
            visible_messages=messages,
            summary=None,
            budget=budget,
            constraints=constraints,
            reason="below_limit",
        )


_SALIENT = re.compile(
    r"(?i)(must|never|do not|don't|constraint|requirement|error|failed|traceback|todo|next|"
    r"[A-Za-z0-9_.-]+\.(?:py|js|ts|go|rs|java|md|toml|yaml|yml|json))"
)


@dataclass(frozen=True)
class SummaryArtifact:
    message: Message | None
    source_ids: tuple[str, ...]


def _summarize(bundles: Sequence[MessageBundle], max_tokens: int) -> SummaryArtifact:
    source_ids = tuple(message.id for bundle in bundles for message in bundle.messages)
    if not bundles or max_tokens < 12:
        return SummaryArtifact(None, source_ids)
    salient: list[str] = []
    ordinary: list[str] = []
    for bundle in bundles:
        for message in bundle.messages:
            for raw_line in message.content.splitlines():
                line = " ".join(raw_line.strip().split())
                if not line:
                    continue
                rendered = f"- {message.kind}: {line}"
                (salient if _SALIENT.search(line) else ordinary).append(rendered)
    lines = salient + ordinary
    header = f"[Compacted context; source_count={len(source_ids)}]"
    limit_bytes = max_tokens * 4
    selected: list[str] = []
    used = len(header.encode("utf-8")) + 1
    for line in lines:
        line_bytes = len(line.encode("utf-8"))
        if used + line_bytes + 1 > limit_bytes:
            continue
        selected.append(line)
        used += line_bytes + 1
    if not selected:
        return SummaryArtifact(None, source_ids)
    return SummaryArtifact(
        Message(
            id="summary",
            role="user",
            content=header + "\n" + "\n".join(selected),
            kind="summary",
            turn=0,
            bundle_id="summary",
            pinned=False,
        ),
        source_ids,
    )


class SummarizingPolicy(ContextPolicy):
    def __init__(self, config: VariantConfig, counter: Char4TokenCounter) -> None:
        super().__init__(config, counter)
        self._summary: Message | None = None
        self._summary_source_ids: tuple[str, ...] = ()
        self._covered_bundle_ids: set[str] = set()

    def _should_trigger(self, tokens: int, budget: ContextBudget, turn: int) -> tuple[bool, str]:
        if self.config.policy == "reactive":
            triggered = tokens >= math.floor(budget.input_limit * self.config.trigger_ratio)
            return triggered, "reactive_threshold" if triggered else "below_trigger"
        periodic = turn > 0 and turn % self.config.every_turns == 0
        emergency = tokens >= math.floor(budget.input_limit * 0.95)
        if periodic:
            return True, "periodic_boundary"
        if emergency:
            return True, "emergency_threshold"
        return False, "between_boundaries"

    def prepare(
        self,
        trajectory: Trajectory,
        budget: ContextBudget,
        constraints: Sequence[ConstraintSpec],
        *,
        turn: int,
    ) -> ContextView:
        bundles = list(trajectory.bundles())
        messages = _flatten(bundles)
        active_bundles = [
            bundle
            for bundle in bundles
            if bundle.pinned or bundle.id not in self._covered_bundle_ids
        ]
        active_messages = _flatten(active_bundles)
        ordered_active: list[Message] = []
        inserted_summary = False
        for message in active_messages:
            if self._summary is not None and not inserted_summary and message.role != "system":
                ordered_active.append(self._summary)
                inserted_summary = True
            ordered_active.append(message)
        if self._summary is not None and not inserted_summary:
            ordered_active.append(self._summary)
        active_tokens = self.counter.messages(message.model_dict() for message in ordered_active)
        trigger, reason = self._should_trigger(active_tokens, budget, turn)
        if not trigger:
            if active_tokens > budget.input_limit:
                raise ContextOverflowError(
                    f"active history reached {active_tokens} input tokens; limit is {budget.input_limit}"
                )
            return self._view(
                all_messages=messages,
                visible_messages=active_messages,
                summary=self._summary,
                budget=budget,
                constraints=constraints,
                reason=reason,
                summary_source_ids=self._summary_source_ids,
                tokens_before_override=active_tokens,
            )
        pinned = [bundle for bundle in bundles if bundle.pinned]
        recent_candidates = [bundle for bundle in bundles if not bundle.pinned]
        recent = recent_candidates[-self.config.keep_recent_bundles :]
        if self.config.keep_recent_bundles == 0:
            recent = []
        mandatory_ids = {bundle.id for bundle in [*pinned, *recent]}
        mandatory = [bundle for bundle in bundles if bundle.id in mandatory_ids]
        eligible = [bundle for bundle in bundles if bundle.id not in mandatory_ids]
        covered_bundle_ids = {bundle.id for bundle in eligible}
        mandatory_messages = _flatten(mandatory)
        mandatory_tokens = self.counter.messages(
            message.model_dict() for message in mandatory_messages
        )
        if mandatory_tokens > budget.input_limit:
            raise ContextOverflowError(
                "Pinned and recent atomic bundles alone exceed the context input budget; "
                "lower observation limits or keep fewer recent bundles"
            )
        target = max(mandatory_tokens, math.floor(budget.input_limit * self.config.target_ratio))
        summary_allowance = max(0, min(target, budget.input_limit) - mandatory_tokens - 4)
        visible = _flatten(mandatory)
        view: ContextView | None = None
        artifact = _summarize(eligible, summary_allowance)
        for allowance in range(summary_allowance, -1, -8):
            artifact = _summarize(eligible, allowance)
            effective_compaction = (
                artifact.message != self._summary
                or artifact.source_ids != self._summary_source_ids
                or covered_bundle_ids != self._covered_bundle_ids
            )
            try:
                view = self._view(
                    all_messages=messages,
                    visible_messages=visible,
                    summary=artifact.message,
                    budget=budget,
                    constraints=constraints,
                    reason=reason,
                    compaction_event=effective_compaction,
                    summary_source_ids=artifact.source_ids,
                    tokens_before_override=active_tokens,
                )
                break
            except ContextOverflowError:
                continue
        if view is None:
            raise ContextOverflowError(
                "Pinned and recent bundles fit alone, but summary message overhead exceeded the budget"
            )
        self._summary = artifact.message
        self._summary_source_ids = artifact.source_ids
        self._covered_bundle_ids = covered_bundle_ids
        return view


_PATH_TERM = re.compile(r"\b[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8}\b")


class SelectivePolicy(ContextPolicy):
    """Budgeted bundle selection using a deterministic 0/1 knapsack."""

    def _score(
        self,
        bundle: MessageBundle,
        *,
        index: int,
        bundle_count: int,
        later_text: str,
        constraints: Sequence[ConstraintSpec],
    ) -> float:
        weights = {
            "recency": 2.0,
            "referenced": 2.5,
            "subgoal": 1.5,
            "constraint": 8.0,
            "task": 5.0,
            "error": 2.0,
        }
        weights.update(self.config.weights)
        recency = (index + 1) / max(1, bundle_count)
        score = weights["recency"] * recency
        content_lower = bundle.content.lower()
        if any(message.kind == "task" for message in bundle.messages):
            score += weights["task"]
        if any(
            constraint.id.lower() in content_lower or constraint.text.lower() in content_lower
            for constraint in constraints
        ):
            score += weights["constraint"]
        terms = set(_PATH_TERM.findall(bundle.content))
        if any(term in later_text for term in terms):
            score += weights["referenced"]
        if re.search(r"(?i)\b(plan|todo|next|remaining|hypothesis)\b", bundle.content):
            score += weights["subgoal"]
        if re.search(r"(?i)\b(error|failed|traceback|assertion)\b", bundle.content):
            score += weights["error"]
        return score

    def prepare(
        self,
        trajectory: Trajectory,
        budget: ContextBudget,
        constraints: Sequence[ConstraintSpec],
        *,
        turn: int,
    ) -> ContextView:
        del turn
        bundles = list(trajectory.bundles())
        messages = _flatten(bundles)
        tokens = self.counter.messages(message.model_dict() for message in messages)
        trigger_at = math.floor(budget.input_limit * self.config.trigger_ratio)
        if tokens < trigger_at:
            return self._view(
                all_messages=messages,
                visible_messages=messages,
                summary=None,
                budget=budget,
                constraints=constraints,
                reason="below_trigger",
            )
        pinned = [bundle for bundle in bundles if bundle.pinned]
        candidates = [bundle for bundle in bundles if not bundle.pinned]
        recent = candidates[-self.config.keep_recent_bundles :]
        if self.config.keep_recent_bundles == 0:
            recent = []
        mandatory_ids = {bundle.id for bundle in [*pinned, *recent]}
        mandatory = [bundle for bundle in bundles if bundle.id in mandatory_ids]
        optional = [bundle for bundle in bundles if bundle.id not in mandatory_ids]
        mandatory_messages = _flatten(mandatory)
        mandatory_tokens = self.counter.messages(
            message.model_dict() for message in mandatory_messages
        )
        if mandatory_tokens > budget.input_limit:
            raise ContextOverflowError(
                "Pinned and recent atomic bundles alone exceed the context input budget; "
                "lower observation limits or keep fewer recent bundles"
            )
        target = max(mandatory_tokens, math.floor(budget.input_limit * self.config.target_ratio))
        capacity_tokens = max(0, min(target, budget.input_limit) - mandatory_tokens)
        unit = 8
        capacity = capacity_tokens // unit
        scores: dict[str, float] = {}
        items: list[tuple[int, float, MessageBundle]] = []
        positions = {bundle.id: index for index, bundle in enumerate(bundles)}
        for bundle in optional:
            original_position = positions[bundle.id]
            later_text = "\n".join(item.content for item in bundles[original_position + 1 :])
            score = self._score(
                bundle,
                index=original_position,
                bundle_count=len(bundles),
                later_text=later_text,
                constraints=constraints,
            )
            weight = max(
                1,
                math.ceil(
                    self.counter.messages(message.model_dict() for message in bundle.messages)
                    / unit
                ),
            )
            scores[bundle.id] = round(score, 6)
            if weight <= capacity:
                items.append((weight, score, bundle))
        values = [float("-inf")] * (capacity + 1)
        selections = [0] * (capacity + 1)
        values[0] = 0.0
        for item_index, (weight, score, _bundle) in enumerate(items):
            for used in range(capacity, weight - 1, -1):
                previous = values[used - weight]
                candidate = previous + score
                if previous != float("-inf") and candidate > values[used]:
                    values[used] = candidate
                    selections[used] = selections[used - weight] | (1 << item_index)
        best = max(range(capacity + 1), key=lambda position: values[position])
        selected_bits = selections[best]
        selected_ids = {
            bundle.id
            for index, (_weight, _score, bundle) in enumerate(items)
            if selected_bits & (1 << index)
        }
        keep_ids = mandatory_ids | selected_ids
        visible_bundles = [bundle for bundle in bundles if bundle.id in keep_ids]
        visible = _flatten(visible_bundles)
        # If message overhead nudges the exact view above budget, evict the least
        # valuable optional bundle deterministically.
        while True:
            try:
                return self._view(
                    all_messages=messages,
                    visible_messages=visible,
                    summary=None,
                    budget=budget,
                    constraints=constraints,
                    reason="selective_budget",
                    scores=scores,
                    compaction_event=len(visible) != len(messages),
                )
            except ContextOverflowError:
                removable = [bundle for bundle in visible_bundles if bundle.id in selected_ids]
                if not removable:
                    raise
                victim = min(removable, key=lambda bundle: (scores[bundle.id], bundle.id))
                selected_ids.remove(victim.id)
                visible_bundles = [bundle for bundle in visible_bundles if bundle.id != victim.id]
                visible = _flatten(visible_bundles)


def make_policy(
    config: VariantConfig,
    counter: Char4TokenCounter,
    registry: PluginRegistry | None = None,
) -> ContextPolicy:
    if config.policy == "none":
        return NonePolicy(config, counter)
    if config.policy in {"reactive", "periodic"}:
        return SummarizingPolicy(config, counter)
    if config.policy == "selective":
        return SelectivePolicy(config, counter)
    from scaffoldscope.plugins import ContextPolicyRequest, PluginLoadError, PluginRegistry

    selected_registry = registry or PluginRegistry.discover()
    loaded = selected_registry.load_context_policy(config.policy)
    policy = loaded.factory(
        ContextPolicyRequest(config=config, counter=counter, options=config.plugin_options)
    )
    if not isinstance(policy, ContextPolicy):
        raise PluginLoadError(
            f"Context-policy plugin {loaded.info.name!r} returned {type(policy).__name__}, "
            "not ContextPolicy",
            hint="Return a ContextPolicy subclass from the registered factory.",
        )
    return policy
