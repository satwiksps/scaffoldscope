"""Installed-distribution fixture for the plugin runtime integration test."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from scaffoldscope.context import NonePolicy
from scaffoldscope.models import ModelResponse, Usage
from scaffoldscope.plugins import (
    ContextPolicyRequest,
    ModelProviderRequest,
    context_policy_plugin,
    model_provider_plugin,
)

policy_options_seen: list[dict[str, object]] = []
model_options_seen: list[dict[str, object]] = []


class RuntimePolicy(NonePolicy):
    """Identity policy used to prove third-party factory selection."""


class RuntimeModel:
    """Offline provider used to prove the model-provider extension path."""

    def __init__(self, request: ModelProviderRequest) -> None:
        self.request = request

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        seed: int,
        max_output_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        del seed, max_output_tokens, temperature
        started = time.perf_counter()
        content = json.dumps(
            {"final": str(self.request.options.get("reply", "runtime plugin complete"))},
            separators=(",", ":"),
        )
        return ModelResponse(
            content=content,
            usage=Usage(
                input_tokens=self.request.counter.messages(messages),
                output_tokens=self.request.counter.text(content),
                source="estimated_char4",
            ),
            request_id="runtime-plugin-request",
            provider_model=self.request.config.name,
            finish_reason="stop",
            latency_seconds=time.perf_counter() - started,
            raw_metadata={"fixture": True},
        )


def create_policy(request: ContextPolicyRequest) -> RuntimePolicy:
    policy_options_seen.append(dict(request.options))
    return RuntimePolicy(request.config, request.counter)


def create_model(request: ModelProviderRequest) -> RuntimeModel:
    model_options_seen.append(dict(request.options))
    return RuntimeModel(request)


policy_registration = context_policy_plugin(
    create_policy,
    plugin_version="1.0.0",
    description="Runtime integration-test context policy.",
    minimum_core_version="0.1.0",
    maximum_core_version_exclusive="2.0.0",
)

provider_registration = model_provider_plugin(
    create_model,
    plugin_version="1.0.0",
    description="Runtime integration-test model provider.",
    minimum_core_version="0.1.0",
    maximum_core_version_exclusive="2.0.0",
)

__all__ = [
    "RuntimeModel",
    "RuntimePolicy",
    "create_model",
    "create_policy",
    "model_options_seen",
    "policy_options_seen",
    "policy_registration",
    "provider_registration",
]
