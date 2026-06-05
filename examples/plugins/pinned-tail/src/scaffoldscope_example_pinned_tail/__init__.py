"""Example bundle-atomic context policy distributed through a Python entry point."""

from __future__ import annotations

import math
from collections.abc import Sequence

from scaffoldscope.context import (
    ContextBudget,
    ContextPolicy,
    ContextView,
    MessageBundle,
    Trajectory,
)
from scaffoldscope.errors import ContextOverflowError
from scaffoldscope.plugins import ContextPolicyRequest, context_policy_plugin
from scaffoldscope.schema import ConstraintSpec


class PinnedTailPolicy(ContextPolicy):
    """Keep mandatory bundles and a recent, bundle-atomic tail under pressure."""

    def prepare(
        self,
        trajectory: Trajectory,
        budget: ContextBudget,
        constraints: Sequence[ConstraintSpec],
        *,
        turn: int,
    ) -> ContextView:
        del turn
        bundles = trajectory.bundles()
        messages = tuple(message for bundle in bundles for message in bundle.messages)
        tokens = self.counter.messages(message.model_dict() for message in messages)
        trigger = math.floor(budget.input_limit * self.config.trigger_ratio)
        if tokens < trigger:
            return self._view(
                all_messages=messages,
                visible_messages=messages,
                summary=None,
                budget=budget,
                constraints=constraints,
                reason="below_trigger",
            )

        recent = bundles[-self.config.keep_recent_bundles :]
        if self.config.keep_recent_bundles == 0:
            recent = ()
        keep_ids = {
            bundle.id
            for bundle in bundles
            if bundle.pinned or _contains_task(bundle) or bundle in recent
        }
        visible = tuple(
            message for bundle in bundles if bundle.id in keep_ids for message in bundle.messages
        )
        try:
            return self._view(
                all_messages=messages,
                visible_messages=visible,
                summary=None,
                budget=budget,
                constraints=constraints,
                reason="pinned_tail",
                compaction_event=True,
            )
        except ContextOverflowError as exc:
            raise ContextOverflowError(
                "Pinned, task, and recent bundles exceed the context input budget"
            ) from exc


def _contains_task(bundle: MessageBundle) -> bool:
    return any(message.kind == "task" for message in bundle.messages)


def create_policy(request: ContextPolicyRequest) -> PinnedTailPolicy:
    if request.options:
        unknown = ", ".join(sorted(request.options))
        raise ValueError(f"example.pinned-tail does not accept plugin options: {unknown}")
    return PinnedTailPolicy(request.config, request.counter)


registration = context_policy_plugin(
    create_policy,
    plugin_version="0.1.0",
    description="Keeps system/task bundles and a deterministic recent tail.",
    minimum_core_version="0.2.0",
    maximum_core_version_exclusive="1.0.0",
)

__all__ = ["PinnedTailPolicy", "create_policy", "registration"]
