from __future__ import annotations

import unittest

from scaffoldscope.context import ContextBudget, MessageBundle, Trajectory, make_policy
from scaffoldscope.errors import ContextOverflowError
from scaffoldscope.schema import ConstraintSpec, VariantConfig
from scaffoldscope.tokenization import Char4TokenCounter


def trajectory_with_pressure() -> Trajectory:
    trajectory = Trajectory()
    trajectory.append(
        role="system",
        content="system rules " * 20,
        kind="system",
        turn=0,
        bundle_id="system",
        pinned=True,
    )
    trajectory.append(
        role="user",
        content="Fix it. [keep-canary] Do not modify canary.txt.",
        kind="task",
        turn=0,
        bundle_id="task",
    )
    trajectory.append(
        role="assistant",
        content='{"action":{"tool":"read_file","arguments":{"path":"notes.txt"}}}',
        kind="assistant_action",
        turn=1,
        bundle_id="turn-1",
    )
    trajectory.append(
        role="tool",
        content="large observation " * 80,
        kind="tool_result",
        turn=1,
        bundle_id="turn-1",
    )
    return trajectory


class ContextPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counter = Char4TokenCounter()
        self.constraint = ConstraintSpec("keep-canary", "Do not modify canary.txt.")

    def test_message_bundle_rejects_empty_messages(self) -> None:
        with self.assertRaisesRegex(ValueError, "messages must not be empty"):
            MessageBundle("empty", ())

    def test_none_reports_overflow(self) -> None:
        policy = make_policy(VariantConfig("none", "none"), self.counter)
        with self.assertRaises(ContextOverflowError):
            policy.prepare(
                trajectory_with_pressure(),
                ContextBudget(500, 100),
                [self.constraint],
                turn=2,
            )

    def test_reactive_compacts_without_splitting_tool_bundle(self) -> None:
        config = VariantConfig(
            "reactive", "reactive", trigger_ratio=0.7, target_ratio=0.65, keep_recent_bundles=0
        )
        policy = make_policy(config, self.counter)
        view = policy.prepare(
            trajectory_with_pressure(),
            ContextBudget(650, 100),
            [self.constraint],
            turn=2,
        )
        self.assertTrue(view.decision.compaction_event)
        dropped = set(view.decision.dropped_message_ids)
        self.assertEqual("m00003" in dropped, "m00004" in dropped)
        self.assertLessEqual(view.decision.tokens_after, view.decision.input_limit)

    def test_periodic_boundary_without_eligible_history_is_not_a_compaction(self) -> None:
        trajectory = Trajectory()
        trajectory.append(
            role="system",
            content="system",
            kind="system",
            turn=0,
            bundle_id="system",
            pinned=True,
        )
        trajectory.append(
            role="user",
            content="task",
            kind="task",
            turn=0,
            bundle_id="task",
        )
        policy = make_policy(
            VariantConfig("periodic", "periodic", every_turns=2, keep_recent_bundles=1),
            self.counter,
        )

        view = policy.prepare(
            trajectory,
            ContextBudget(1000, 100),
            [self.constraint],
            turn=2,
        )

        self.assertEqual(view.decision.reason, "periodic_boundary")
        self.assertFalse(view.decision.compaction_event)
        self.assertFalse(view.decision.history_compacted)

    def test_selective_emits_scores_and_keeps_atomic_bundles(self) -> None:
        config = VariantConfig(
            "selective", "selective", trigger_ratio=0.5, target_ratio=0.5, keep_recent_bundles=0
        )
        policy = make_policy(config, self.counter)
        view = policy.prepare(
            trajectory_with_pressure(),
            ContextBudget(800, 100),
            [self.constraint],
            turn=2,
        )
        kept = set(view.decision.kept_message_ids)
        self.assertEqual("m00003" in kept, "m00004" in kept)
        self.assertTrue(view.decision.selected_scores)

    def test_selective_trigger_without_a_history_change_is_not_a_compaction(self) -> None:
        trajectory = Trajectory()
        trajectory.append(
            role="system",
            content="s" * 100,
            kind="system",
            turn=0,
            bundle_id="system",
            pinned=True,
        )
        trajectory.append(
            role="user",
            content="t" * 100,
            kind="task",
            turn=0,
            bundle_id="task",
        )
        policy = make_policy(
            VariantConfig(
                "selective",
                "selective",
                trigger_ratio=0.95,
                target_ratio=0.9,
                keep_recent_bundles=1,
            ),
            self.counter,
        )

        canonical_tokens = self.counter.messages(
            message.model_dict() for message in trajectory.messages
        )
        view = policy.prepare(
            trajectory,
            ContextBudget(canonical_tokens + 10, 10),
            [self.constraint],
            turn=1,
        )

        self.assertEqual(view.decision.reason, "selective_budget")
        self.assertFalse(view.decision.compaction_event)
        self.assertFalse(view.decision.history_compacted)
        self.assertEqual(view.decision.tokens_before, view.decision.tokens_after)


if __name__ == "__main__":
    unittest.main()
