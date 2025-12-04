from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import patch

from scaffoldscope.errors import ModelError
from scaffoldscope.models import OpenAICompatibleModel, Usage, estimate_cost
from scaffoldscope.schema import ModelConfig
from scaffoldscope.tokenization import Char4TokenCounter


def _config(**overrides: Any) -> ModelConfig:
    values: dict[str, Any] = {
        "provider": "openai_compatible",
        "name": "model-revision-2026-08-15",
        "context_window_tokens": 4096,
        "max_output_tokens": 128,
        "temperature": 0.0,
        "base_url": "https://provider.invalid/v1",
        "api_key_env": "SCAFFOLDSCOPE_TEST_KEY",
        "timeout_seconds": 5.0,
        "retries": 0,
        "supports_seed": True,
        "json_mode": True,
        "input_price_per_million": 1.0,
        "output_price_per_million": 2.0,
        "cache_read_price_per_million": 0.1,
        "cache_write_price_per_million": 0.5,
    }
    values.update(overrides)
    return ModelConfig(**values)


class _Response:
    def __init__(self, payload: dict[str, Any], *, request_id: str = "req-123") -> None:
        self.payload = payload
        self.headers = {"x-request-id": request_id}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ModelAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {"SCAFFOLDSCOPE_TEST_KEY": "unit-test-secret-token"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.messages = [{"role": "user", "content": "Return JSON."}]

    def test_payload_provider_usage_and_cache_pricing(self) -> None:
        captured: list[urllib.request.Request] = []
        payload = {
            "id": "response-id",
            "model": "effective-model-revision",
            "system_fingerprint": "fp-abc",
            "choices": [
                {
                    "message": {"content": '{"final":"done"}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {
                    "cached_tokens": 40,
                    "cache_write_tokens": 10,
                },
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        }

        def open_request(request: urllib.request.Request, *, timeout: float) -> _Response:
            self.assertEqual(timeout, 5.0)
            captured.append(request)
            return _Response(payload)

        model = OpenAICompatibleModel(_config(), Char4TokenCounter())
        with patch("urllib.request.urlopen", side_effect=open_request):
            response = model.complete(
                self.messages,
                seed=1729,
                max_output_tokens=64,
                temperature=0.2,
            )

        request_payload = json.loads(captured[0].data or b"{}")
        self.assertEqual(request_payload["seed"], 1729)
        self.assertEqual(request_payload["response_format"], {"type": "json_object"})
        self.assertEqual(request_payload["max_tokens"], 64)
        self.assertEqual(captured[0].get_header("Authorization"), "Bearer unit-test-secret-token")
        self.assertEqual(response.request_id, "req-123")
        self.assertEqual(response.provider_model, "effective-model-revision")
        self.assertEqual(response.usage.source, "provider")
        self.assertEqual(response.usage.cache_read_tokens, 40)
        self.assertEqual(response.usage.cache_write_tokens, 10)
        self.assertEqual(response.raw_metadata["system_fingerprint"], "fp-abc")
        self.assertAlmostEqual(estimate_cost(_config(), response.usage) or 0.0, 0.000099)

    def test_missing_usage_is_labeled_as_an_estimate(self) -> None:
        model = OpenAICompatibleModel(
            _config(supports_seed=False, json_mode=False),
            Char4TokenCounter(),
        )
        payload = {
            "choices": [{"message": {"content": '{"final":"done"}'}}],
        }
        captured: list[urllib.request.Request] = []

        def open_request(request: urllib.request.Request, *, timeout: float) -> _Response:
            del timeout
            captured.append(request)
            return _Response(payload)

        with patch("urllib.request.urlopen", side_effect=open_request):
            response = model.complete(
                self.messages,
                seed=1,
                max_output_tokens=32,
                temperature=0.0,
            )

        request_payload = json.loads(captured[0].data or b"{}")
        self.assertNotIn("seed", request_payload)
        self.assertNotIn("response_format", request_payload)
        self.assertEqual(response.usage.source, "estimated_char4")
        self.assertGreater(response.usage.total_tokens, 0)

    def test_local_endpoint_can_explicitly_disable_authentication(self) -> None:
        captured: list[urllib.request.Request] = []

        def open_request(request: urllib.request.Request, *, timeout: float) -> _Response:
            del timeout
            captured.append(request)
            return _Response(
                {
                    "choices": [{"message": {"content": '{"final":"done"}'}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                }
            )

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("urllib.request.urlopen", side_effect=open_request),
        ):
            model = OpenAICompatibleModel(
                _config(
                    base_url="http://127.0.0.1:11434/v1",
                    requires_api_key=False,
                    supports_seed=False,
                    json_mode=False,
                ),
                Char4TokenCounter(),
            )
            model.complete(self.messages, seed=1, max_output_tokens=32, temperature=0.0)

        self.assertIsNone(captured[0].get_header("Authorization"))

    def test_retry_is_observable(self) -> None:
        events: list[dict[str, Any]] = []
        error = urllib.error.HTTPError(
            "https://provider.invalid/v1/chat/completions",
            429,
            "rate limited",
            {},
            io.BytesIO(b"rate limited"),
        )
        success = _Response(
            {
                "choices": [{"message": {"content": '{"final":"done"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )
        model = OpenAICompatibleModel(
            _config(retries=1),
            Char4TokenCounter(),
            events.append,
        )

        with (
            patch("urllib.request.urlopen", side_effect=[error, success]) as opened,
            patch("scaffoldscope.models.time.sleep"),
        ):
            response = model.complete(
                self.messages,
                seed=1,
                max_output_tokens=32,
                temperature=0.0,
            )

        self.assertEqual(opened.call_count, 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["attempt"], 1)
        self.assertTrue(events[0]["retrying"])
        self.assertEqual(response.raw_metadata["attempt_count"], 2)

    def test_non_retryable_error_redacts_the_key(self) -> None:
        error = urllib.error.HTTPError(
            "https://provider.invalid/v1/chat/completions",
            400,
            "bad request",
            {},
            io.BytesIO(b"api_key=unit-test-secret-token"),
        )
        events: list[dict[str, Any]] = []
        model = OpenAICompatibleModel(_config(retries=3), Char4TokenCounter(), events.append)
        with (
            patch("urllib.request.urlopen", side_effect=error) as opened,
            self.assertRaises(ModelError) as raised,
        ):
            model.complete(
                self.messages,
                seed=1,
                max_output_tokens=32,
                temperature=0.0,
            )

        self.assertEqual(opened.call_count, 1)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["retrying"])
        self.assertNotIn("unit-test-secret-token", str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))
        self.assertIn("after 1 attempt", str(raised.exception))

    def test_invalid_provider_usage_is_rejected(self) -> None:
        model = OpenAICompatibleModel(_config(), Char4TokenCounter())
        payload = {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": -1, "completion_tokens": 1},
        }
        with (
            patch("urllib.request.urlopen", return_value=_Response(payload)),
            self.assertRaises(ModelError),
        ):
            model.complete(
                self.messages,
                seed=1,
                max_output_tokens=32,
                temperature=0.0,
            )

    def test_cost_is_unavailable_without_prices(self) -> None:
        config = _config(input_price_per_million=None, output_price_per_million=None)
        self.assertIsNone(estimate_cost(config, Usage(input_tokens=10, output_tokens=2)))


if __name__ == "__main__":
    unittest.main()
