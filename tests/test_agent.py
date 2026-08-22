from __future__ import annotations

import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scaffoldscope.agent import AgentOutcome, CodingAgent, parse_response
from scaffoldscope.context import (
    ContextBudget,
    ContextDecision,
    ContextPolicy,
    ContextView,
    Trajectory,
)
from scaffoldscope.errors import ModelError, ProtocolError
from scaffoldscope.events import EventLog
from scaffoldscope.jsonutil import load_jsonl
from scaffoldscope.models import ModelResponse, Usage
from scaffoldscope.sandbox import EvaluationResult, ToolResult
from scaffoldscope.schema import AgentConfig, ConstraintSpec, ModelConfig, TaskSpec, VariantConfig
from scaffoldscope.tokenization import Char4TokenCounter


class _FixedPolicy(ContextPolicy):
    def __init__(self, counter: Char4TokenCounter, *, tokens_after: int) -> None:
        super().__init__(VariantConfig(id="fixed", policy="none"), counter)
        self.tokens_after = tokens_after

    def prepare(
        self,
        trajectory: Trajectory,
        budget: ContextBudget,
        constraints: Sequence[ConstraintSpec],
        *,
        turn: int,
    ) -> ContextView:
        del constraints, turn
        messages = trajectory.messages
        return ContextView(
            messages=tuple(message.model_dict() for message in messages),
            decision=ContextDecision(
                policy=self.config.policy,
                reason="fixed_test_view",
                compaction_event=False,
                history_compacted=False,
                tokens_before=self.tokens_after,
                tokens_after=self.tokens_after,
                canonical_tokens=self.tokens_after,
                input_limit=budget.input_limit,
                kept_message_ids=tuple(message.id for message in messages),
                dropped_message_ids=(),
            ),
        )


class _SequenceModel:
    def __init__(
        self,
        responses: Sequence[ModelResponse | ModelError],
        *,
        failed_attempts_per_call: Sequence[int] = (),
    ) -> None:
        self.responses = list(responses)
        self.failed_attempts_per_call = tuple(failed_attempts_per_call)
        self.failed_attempts = 0
        self.calls: list[tuple[dict[str, str], ...]] = []

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        seed: int,
        max_output_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        del seed, max_output_tokens, temperature
        call_index = len(self.calls)
        self.calls.append(tuple(dict(message) for message in messages))
        if call_index < len(self.failed_attempts_per_call):
            self.failed_attempts += self.failed_attempts_per_call[call_index]
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, ModelError):
            raise response
        return response


class _StubSandbox:
    available_tools = ("list_files",)

    def __init__(self) -> None:
        self.invocations: list[tuple[str, Any]] = []

    def invoke(self, tool: str, arguments: Any) -> ToolResult:
        self.invocations.append((tool, arguments))
        return ToolResult(tool, True, "ok", {}, 0.0)

    def evaluate(self) -> EvaluationResult:
        return EvaluationResult(True, 0, "", 0.0, {}, {})

    def patch(self) -> str:
        return ""


def _response(
    content: str,
    *,
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> ModelResponse:
    return ModelResponse(
        content=content,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        request_id="request-id",
        provider_model="stub-model",
        finish_reason="stop",
        latency_seconds=0.0,
        raw_metadata={"attempt_count": 1},
    )


class ProtocolTests(unittest.TestCase):
    def test_parses_action(self) -> None:
        parsed = parse_response('{"action":{"tool":"read_file","arguments":{"path":"hello.py"}}}')
        self.assertIsNotNone(parsed.action)
        assert parsed.action is not None
        self.assertEqual(parsed.action.tool, "read_file")
        self.assertEqual(parsed.action.arguments["path"], "hello.py")

    def test_parses_fenced_json_for_provider_tolerance(self) -> None:
        parsed = parse_response('```json\n{"final":"done"}\n```')
        self.assertEqual(parsed.final, "done")

    def test_rejects_ambiguous_response(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_response('{"action":{},"final":"done"}')

    def test_rejects_prose_wrappers_and_unknown_fields(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_response('Here you go: {"final":"done"}')
        with self.assertRaises(ProtocolError):
            parse_response('{"final":"done","confidence":1}')


class CodingAgentBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run_index = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run_agent(
        self,
        responses: Sequence[ModelResponse | ModelError],
        *,
        tokens_after: int = 1,
        max_turns: int = 3,
        max_total_tokens: int = 100,
        max_cost_usd: float | None = None,
        max_output_tokens: int = 1,
        price_per_million: float = 0.0,
        failed_attempts_per_call: Sequence[int] = (),
    ) -> tuple[AgentOutcome, _SequenceModel, _StubSandbox, list[dict[str, Any]]]:
        self.run_index += 1
        counter = Char4TokenCounter()
        model = _SequenceModel(
            responses,
            failed_attempts_per_call=failed_attempts_per_call,
        )
        sandbox = _StubSandbox()
        events_path = self.root / f"events-{self.run_index}.jsonl"
        agent = CodingAgent(
            task=TaskSpec(
                id="agent-test",
                workspace=self.root,
                problem="Exercise one agent branch.",
                constraints=(),
                test_command=(),
            ),
            seed=7,
            model=model,
            model_config=ModelConfig(
                provider="stub",
                name="stub-model",
                context_window_tokens=1_000,
                max_output_tokens=max_output_tokens,
                temperature=0.0,
                base_url=None,
                api_key_env="IGNORED",
                timeout_seconds=1.0,
                retries=0,
                supports_seed=True,
                json_mode=True,
                input_price_per_million=price_per_million,
                output_price_per_million=price_per_million,
                cache_read_price_per_million=price_per_million,
                cache_write_price_per_million=price_per_million,
                requires_api_key=False,
            ),
            agent_config=AgentConfig(
                max_turns=max_turns,
                max_total_tokens=max_total_tokens,
                max_cost_usd=max_cost_usd,
                system_prompt="Test system prompt.",
            ),
            policy=_FixedPolicy(counter, tokens_after=tokens_after),
            sandbox=sandbox,
            counter=counter,
            events=EventLog(events_path),
            failed_attempt_count=lambda: model.failed_attempts,
        )

        outcome = agent.run()
        return outcome, model, sandbox, load_jsonl(events_path)

    def test_projected_token_cap_rejects_before_model_call(self) -> None:
        outcome, model, _sandbox, events = self._run_agent(
            [],
            tokens_after=3,
            max_output_tokens=2,
            max_total_tokens=4,
        )

        self.assertEqual(outcome.status, "token_limit")
        self.assertEqual(outcome.turns, 1)
        self.assertEqual(outcome.model_calls, 0)
        self.assertEqual(model.calls, [])
        self.assertEqual(outcome.context_checks, [])
        self.assertEqual(outcome.usage.total_tokens, 0)
        self.assertIn("0 used + 5 projected > 4", outcome.error or "")
        rejection = next(row for row in events if row["type"] == "token_preflight_rejected")
        self.assertEqual(rejection["payload"]["projected_tokens"], 5)

    def test_consumed_token_cap_stops_before_second_model_call(self) -> None:
        outcome, model, sandbox, _events = self._run_agent(
            [
                _response(
                    '{"action":{"tool":"list_files","arguments":{}}}',
                    input_tokens=2,
                    output_tokens=2,
                )
            ],
            max_turns=3,
            max_total_tokens=4,
        )

        self.assertEqual(outcome.status, "token_limit")
        self.assertEqual(outcome.turns, 2)
        self.assertEqual(outcome.model_calls, 1)
        self.assertEqual(outcome.tool_calls, 1)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(sandbox.invocations, [("list_files", {})])
        self.assertEqual(outcome.usage.total_tokens, 4)
        self.assertEqual(outcome.error, "maximum total token budget reached")

    def test_projected_cost_cap_rejects_before_model_call(self) -> None:
        outcome, model, _sandbox, events = self._run_agent(
            [],
            tokens_after=2,
            max_output_tokens=2,
            max_cost_usd=3.5,
            price_per_million=1_000_000.0,
        )

        self.assertEqual(outcome.status, "cost_limit")
        self.assertEqual(outcome.turns, 1)
        self.assertEqual(outcome.model_calls, 0)
        self.assertEqual(model.calls, [])
        self.assertEqual(outcome.context_checks, [])
        self.assertEqual(outcome.usage.cost_usd, 0.0)
        rejection = next(row for row in events if row["type"] == "cost_preflight_rejected")
        self.assertEqual(rejection["payload"]["projected_cost_usd"], 4.0)

    def test_consumed_cost_cap_stops_before_second_model_call(self) -> None:
        outcome, model, sandbox, _events = self._run_agent(
            [_response('{"action":{"tool":"list_files","arguments":{}}}')],
            max_turns=3,
            max_cost_usd=2.0,
            price_per_million=1_000_000.0,
        )

        self.assertEqual(outcome.status, "cost_limit")
        self.assertEqual(outcome.turns, 2)
        self.assertEqual(outcome.model_calls, 1)
        self.assertEqual(outcome.tool_calls, 1)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(sandbox.invocations, [("list_files", {})])
        self.assertEqual(outcome.usage.cost_usd, 2.0)
        self.assertTrue(outcome.usage.complete)
        self.assertEqual(outcome.error, "maximum per-trial cost reached")

    def test_model_error_marks_unreported_usage_incomplete_without_counting_a_call(self) -> None:
        outcome, model, _sandbox, events = self._run_agent([ModelError("provider unavailable")])

        self.assertEqual(outcome.status, "model_error")
        self.assertEqual(outcome.error, "provider unavailable")
        self.assertEqual(outcome.turns, 1)
        self.assertEqual(outcome.model_calls, 0)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(outcome.usage.total_tokens, 0)
        self.assertFalse(outcome.usage.complete)
        self.assertIsNone(outcome.usage.cost_usd)
        self.assertEqual(outcome.usage.sources, {"provider_error_unreported"})
        self.assertEqual(len(outcome.context_checks), 1)
        self.assertIn("model_error", [row["type"] for row in events])

    def test_retry_loses_cost_observability_after_recording_reported_usage(self) -> None:
        outcome, model, _sandbox, events = self._run_agent(
            [_response('{"final":"would otherwise finish"}')],
            max_cost_usd=10.0,
            price_per_million=1_000_000.0,
            failed_attempts_per_call=(1,),
        )

        self.assertEqual(outcome.status, "cost_unobservable")
        self.assertIsNone(outcome.final)
        self.assertEqual(outcome.model_calls, 1)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(outcome.usage.total_tokens, 2)
        self.assertFalse(outcome.usage.complete)
        self.assertIsNone(outcome.usage.cost_usd)
        self.assertEqual(
            outcome.usage.sources,
            {"provider", "provider_retry_unreported"},
        )
        self.assertIn("can no longer be enforced", outcome.error or "")
        self.assertIn("cost_observability_lost", [row["type"] for row in events])

    def test_protocol_error_feedback_allows_next_turn_recovery(self) -> None:
        outcome, model, sandbox, _events = self._run_agent(
            [
                _response("not valid protocol", input_tokens=2, output_tokens=1),
                _response('{"final":"recovered"}', input_tokens=3, output_tokens=1),
            ]
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.final, "recovered")
        self.assertEqual(outcome.turns, 2)
        self.assertEqual(outcome.model_calls, 2)
        self.assertEqual(outcome.protocol_errors, 1)
        self.assertEqual(outcome.tool_calls, 0)
        self.assertEqual(sandbox.invocations, [])
        self.assertEqual(outcome.usage.total_tokens, 7)
        self.assertEqual(len(model.calls), 2)
        feedback = model.calls[1][-1]
        self.assertEqual(feedback["role"], "user")
        self.assertIn("<protocol_error>", feedback["content"])
        self.assertIn("Return exactly one valid action", feedback["content"])

    def test_turn_limit_counts_every_completed_action_turn(self) -> None:
        action = _response('{"action":{"tool":"list_files","arguments":{}}}')
        outcome, model, sandbox, _events = self._run_agent(
            [action, action],
            max_turns=2,
        )

        self.assertEqual(outcome.status, "turn_limit")
        self.assertEqual(outcome.error, "maximum turn budget reached")
        self.assertEqual(outcome.turns, 2)
        self.assertEqual(outcome.model_calls, 2)
        self.assertEqual(outcome.tool_calls, 2)
        self.assertEqual(outcome.protocol_errors, 0)
        self.assertIsNone(outcome.final)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(
            sandbox.invocations,
            [("list_files", {}), ("list_files", {})],
        )
        self.assertEqual(outcome.usage.total_tokens, 4)


if __name__ == "__main__":
    unittest.main()
